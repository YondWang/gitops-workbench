# Feature 包与正式 SimOS CI 复用及迁移设计

## 状态

已确认，待实施计划审阅后落地。

## 目标

Feature 测试包必须复用当前 SimOS 正式 CI 的构建调度和构建脚本，不能维护功能相近但细节不同的 Workbench 编译实现。当前 Feature 包使用 SimOS 固定的 Config 矩阵；未来 OS/config 若按 release、fix 或 bugfix 分支冻结，也能在不重写构建和发布链路的前提下迁移。

该设计还为正式 SimOS 构建日后迁入 Workbench 受保护 CI 仓库留下单一的共享调度与发布边界。迁移前后都不得破坏现有 `OS/simos` Tag Pipeline、Runner、分支、Tag、提交、MR 或 OTA 流程。

## 当前构建合同

当前正式 SimOS CI 定义在 `OS/simos/.gitlab-ci.yml`，有四个并行构建 Job：

| Job 系列 | Config ref | 标签 |
| --- | --- | --- |
| `resident_package` | `SIMBOT_R6_A` | `360` |
| `resident_package` | `SIMBOT_R6_B` | `360s` |
| `deb_package` | `SIMBOT_R6_A` | `360` |
| `deb_package` | `SIMBOT_R6_B` | `360s` |

唯一构建入口为：

```text
ci/resident/ci-build-resident.sh
ci/deb/ci-build-debs.sh
```

这些入口已经处理 Config 选择和检出、子模块准备、cross-rootfs、并行控制、resident/deb 包整理、摘要计算和正式 Package Registry manifest 生成。Deb 入口还会规范化 `*/debian/install` 的执行权限；遗漏这一步会使 `dh_install` 将配置文件当作可执行程序并以 exit 126 失败。

Workbench Feature CI 当前只运行 `build_all_debs.sh` 并平铺扫描输出，不能匹配正式调度，也会遗漏正式行为。因此应替换而不是继续补丁式扩展。

## 约束

- Feature 包不创建远端构建分支、提交、Tag、MR 或 release note。
- Feature 包不执行 OTA 上传、OTA 注册或其他 OTA 云端接口。
- Feature build 是无发布凭据、无 Docker socket 的非特权容器 Job。
- 只有 `software_hmi_app/gitops-control@ci/feature-package` 的可信脚本可以启动或发布 Feature 包；不执行冻结 SimOS Checkout 中的 `.gitlab-ci.yml`、发布脚本或 OTA 脚本。
- 现有 SimOS 正式 Tag Pipeline 及其 Runner 配置不在本次变更范围。
- Feature Pipeline 不包含 `button_*` 手工操作 Job；只保留下列五个逻辑阶段。

## 流水线设计

```text
feature_context_validate
  -> feature_prepare
      -> feature_build:resident [SIMBOT_R6_A, 360]
      -> feature_build:resident [SIMBOT_R6_B, 360s]
      -> feature_build:deb      [SIMBOT_R6_A, 360]
      -> feature_build:deb      [SIMBOT_R6_B, 360s]
          -> feature_publish_registry
              -> feature_publish_nextcloud
```

`feature_build` 在 YAML 中是 resident/deb 两个定义、每个定义一个双项 matrix；GitLab UI 会显示四个构建实例。这是与正式 SimOS 调度等价的表现。“五个 Job”指五个逻辑阶段，不是 UI 实例数量。

| 阶段 | Runner 与权限 | 责任 |
| --- | --- | --- |
| `feature_context_validate` | `simos-feature-build`，无发布凭据 | 验证 API 触发条件、HMAC、过期时间、schema、Feature ref/SHA、云盘分类和 Config 策略。 |
| `feature_prepare` | `simos-feature-build`，无发布凭据 | 检出冻结 SimOS/组件 SHA，固定 gitlink，并只在临时工作区注入 Feature 元数据。 |
| `feature_build` | `simos-feature-build`，无发布凭据、无 Docker socket | 直接运行冻结 SimOS 的正式构建入口。 |
| `feature_publish_registry` | `gitops-feature-publisher`，受保护环境 | 校验正式 manifest 和本地文件摘要，并上传 resident/deb 两类 Registry 内容。 |
| `feature_publish_nextcloud` | `gitops-feature-publisher`，受保护环境 | 基于经过验证的发布清单，发布完整 Feature 包布局至选定 Nextcloud 分类。 |

所有 Job 都必须同时满足 API Pipeline、`ci/feature-package` ref 和 `GITOPS_FEATURE_PACKAGE=1` 才会运行。

## 签名上下文

浏览器请求只允许 `ref`、可选 `baseline_ref`、`repository_ids` 和 `cloud_category`。后端预检后，在锁内分配唯一 `TyyyyMMddHHmmss_描述` build ID，读取远端 SimOS Feature 分支的 `version.info`，冻结 SimOS 与选中业务组件 SHA，并将 canonical JSON Base64 编码后 HMAC 签名。

上下文 schema 升为 `2`，新增稳定的 Config 边界：

```json
{
  "schema": 2,
  "run_id": "feature-20260831153045123456",
  "build_id": "T20260831153045_login",
  "source": {
    "repository_id": "simos",
    "project": "OS/simos",
    "ref": "feature/release_login",
    "sha": "<40-or-64-hex-sha>"
  },
  "config_source": {
    "mode": "formal_matrix",
    "project": "OS/config",
    "variants": [
      {"ref": "SIMBOT_R6_A", "label": "360"},
      {"ref": "SIMBOT_R6_B", "label": "360s"}
    ]
  }
}
```

当前模式不携带可由浏览器控制的 Config ref 或 SHA。验证器只接受完整、固定顺序的正式矩阵，拒绝未知 mode、重复变体、错误 label 或变体缺失。

## 准备与构建

`feature_prepare` 用 GitLab Job Token 与 `CI_SERVER_URL` 检出 `source.sha`，初始化 SimOS gitlink 固定的子模块，将选中的业务组件检出为签名中的 SHA，再以本地 git index `160000` 固定 gitlink。它只修改临时 Checkout 的 `version.info` 与 `software.yaml`，不向任何远端仓库写回。

Feature build wrapper 只负责隔离检查、环境适配与正式脚本调度，不复制 SimOS 构建细节。每个矩阵实例必须：

1. 验证处于隔离容器，且不存在 Docker socket 和发布凭据。
2. 从已验证的 `feature-context.json` 确认 Job 的 Config ref/label 属于签名 `formal_matrix`。
3. 以 `CI_PROJECT_DIR=feature-source` 运行冻结源码的正式入口。若不覆盖，正式脚本会错误地把 Workbench Checkout 当作项目根目录。
4. 使用与正式 CI 相同的构建变量：`SIMOS_BUILD_IMAGE`、`SIMOS_DEB_BUILD_IMAGE`、`SIMOS_DEB_BUILD_MODE=all`、`SIMOS_DEB_BUILD_JOBS=16`。这些均由受保护 CI 配置维护。
5. 仅对正式脚本子进程显式清空 `CI_COMMIT_TAG`，以无 Tag 模式生成 formal manifest，其 `tag` 字段必须为空。冻结的 resident 入口会调用 `check-release-version.sh`；Feature `TyyyyMMddHHmmss_description` 不是其接受的正式发布 Tag。Feature `build_id` 仍保留在签名上下文、运行记录、可信 Registry 路径/版本和最终结果中，不绑定为正式构建子进程的 Tag。
6. 设置 resident/deb 的 `*_PACKAGE_REGISTRY_UPLOAD_ENABLED=false` 与 `*_PACKAGE_REGISTRY_UPLOAD_REQUIRED=false`。这样正式脚本完整生成“已收集、未上传”的 manifest，但构建 Job 不持有写入凭据。
7. 将输出分别保存到确定路径，例如 `feature-output/resident/360/` 与 `feature-output/deb/360/`。不得平铺文件，避免 `360` 和 `360s` 的同名 sidecar 相互覆盖。

resident 构建直接调用 `feature-source/ci/resident/ci-build-resident.sh`；deb 构建直接调用 `feature-source/ci/deb/ci-build-debs.sh`。不得调用 `build_all_debs.sh`，也不得复制正式脚本中的 `fix_debhelper_install_permissions`、Config checkout、子模块或交叉编译实现。

## 发布设计

Feature 使用 `T...` 作为 Generic Package version，但保持正式构建的两类 package name：

```text
OS/simos / simos-resident / TyyyyMMddHHmmss_description
OS/simos / simos-debs     / TyyyyMMddHHmmss_description
```

可信 Registry 发布在任何外部写入前校验四个 build artifacts：每个签名 Config variant 都必须有 resident 与 deb 输出；正式 manifest 的 tag 必须为空，Config ref/label 必须与签名一致；每个 manifest 文件必须存在并满足大小、MD5、SHA-256；拒绝未列入 manifest 的包文件，不从目录扫描推断上传内容。可信发布器单独使用签名上下文中的 Feature `build_id` 作为 Generic Package version。

发布集合遵循正式 manifest：resident 的 `resident.tar.gz`、`resident.md5`、`simos.config`、`deploy.sh`、`remote_run.sh`、`checksum.md5`、`checksums.txt`、`build-info.json` 和 manifest，以及 deb 的 `.deb`、`.ddeb`、`.changes`、`.buildinfo`、`resident_*.tar.gz`、`simos_*.zip`、`config.yaml` 和 metadata。命名保持正式 `360-`、`360s-` 前缀。

Nextcloud Job 只下载并发布经过验证的发布清单，保留 `cloud_category/build_id` 下的变体结构和所有附属文件。它不调用 SimOS 生产 Nextcloud 脚本，因为后者依赖发布器宿主机和生产约定；这一差异只在发布环境，不改变编译或打包行为。最终结果记录 resident/deb Registry URL、变体、Nextcloud 目录和 Pipeline/Job 链接。

## Config 演进预留

当前唯一生效的模式为 `formal_matrix`，严格镜像 SimOS CI。冻结源码内的 `ci/common/checkout-config-ref.sh` 继续负责当前 Config checkout；本次不增加 Config UI、Config 分支创建或后端 Config 写接口。

未来改为共享发布分支时新增模式，而不改变现有模式：

```json
{
  "mode": "shared_branch_snapshot",
  "project": "OS/config",
  "ref": "release",
  "sha": "<40-or-64-hex-sha>",
  "variants": [{"ref": "release", "label": "360"}]
}
```

迁移时由后端把 Config 加入预检和 SHA 冻结，`feature_prepare` 检出该 SHA，SimOS Config resolver 增加“已冻结 Config checkout”输入并优先验证使用它。`feature_build`、manifest 验证、Registry 和 Nextcloud 接口不改变，只按 `config_source.mode` 选择准备方式。应先在 Feature Pipeline 验证，再给正式 Tag 包启用，两种模式并行到正式验收完成。

## 正式构建迁入 Workbench 的预留

本次不迁移正式构建。受保护 Workbench Pipeline 按以下分层实现：

```text
受保护 Workbench Pipeline
  -> 可信 context 与冻结 source preparation
      -> 冻结 SimOS 正式 build entrypoint
          -> 受保护 manifest publisher
```

正式迁移时新增独立 `GITOPS_BUILD_KIND=formal_tag` context，包含经 GitLab Tag 验证的 Tag、Tag SHA、版本与 Config 策略；不能将 Feature sentinel 放宽为接受任意 Tag/SHA。迁移顺序是：先让 Feature 完成四路调度、两类 Registry 与 Nextcloud 回归验证；抽取受保护 CI 的通用模板但保持 Feature/formal rules 和发布环境隔离；创建不发布、无 OTA 的 formal dry-run，与 SimOS 正式 manifest、文件名、摘要和 Config commit 对比；连续一致后才接管正式 Registry/Nextcloud；保留 SimOS CI 作为回退直到明确退役。

OTA、release note、Tag 创建和版本提交始终属于正式发布编排，未来也不能进入 Feature flow。

## 错误处理与验收

- 验证器拒绝 HMAC 缺失/篡改、过期 context、未知 schema/mode、错误 Config 矩阵、非法 Feature ref/SHA 和不在受保护白名单的云盘分类。
- 准备阶段验证实际 SimOS/组件 `HEAD` 等于签名 SHA；不匹配即失败。
- build Job 即使失败也上传正式失败 manifest/build-info，便于区分编译、Config checkout 与输入问题。
- Registry 发布在预上传验证失败时不得调用上传，错误需指出 build kind、label、manifest 或摘要差异。
- 构建记录保留 `config_source`、四个 build Job 链接、两类 Registry 结果、Nextcloud 结果和最终错误。
- 测试覆盖 schema 2、Config matrix 完整性、两组双项 matrix、正式入口调用、`CI_PROJECT_DIR` 覆盖、禁止 `build_all_debs.sh`、隔离检查、未平铺 artifact、manifest 摘要校验、两类 package name 和无 OTA/release note/button Job。
- 运行完整 Workbench 单测、Shell 语法检查、YAML 解析检查与 `git diff --check`。

## 实施范围

预计修改：

```text
.gitlab/ci/feature-package.yml
.gitlab/scripts/feature-package-validate.py
.gitlab/scripts/feature-package-build.sh
.gitlab/scripts/feature-package-publish-registry.sh
.gitlab/scripts/feature-package-publish-nextcloud.sh
webapp/server.py
webapp/test_feature_package.py
webapp/simulated_gitlab.py
webapp/README.md
```

本文件同时维护 Config 演进与正式构建迁移路线。`OS/simos` 本次不改动；它只作为构建 contract 与回归对照来源。
