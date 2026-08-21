# GitLab Branch Workbench

轻量 Web 分支管理工具。后端使用 Python 标准库，核心 Git 操作统一通过 GitLab REST API v4 完成，不依赖本地 clone。工具支持维护多个 GitLab 仓库，并可选择对当前仓库或全部启用仓库执行创建操作。

## 功能

- 页面中添加、编辑、启用、停用多个 GitLab 仓库。
- 读取并分组展示当前仓库的远端分支和 Tag。
- `admin` 可从指定来源分支或 Tag 创建唯一 `release` 分支。
- `user` 和 `admin` 可从 `release`、`bugfix/<版本号>` 或迁移期 `fix` 创建 `feature/{来源}_{功能描述}`，任务号可选。
- `admin` 可从指定来源分支或 Tag 创建版本级长期分支 `bugfix/<版本号>`。
- Web 工具创建的 `release`、`bugfix/<版本号>` 默认设置为 GitLab 保护分支。
- `admin` 可基于分支创建 Tag，默认命名为 `<来源>-<yyyyMMddHHmmss>`；来源分支中的 `/` 会替换为 `-`。
- `user` 和 `admin` 可从任意 `feature/*` 创建隔离的 Feature 测试包；测试包固定使用 `T` 前缀，并通过临时构建分支触发 GitLab CI。
- 写操作可选择“当前仓库”或“全部启用仓库”；全部仓库会先做预检查，预检查失败时不会写任何仓库。

Web 工具只做分支创建和 Tag 创建，不做 Feature 合入、Bugfix 同步或自动 MR。Feature 合回来源分支、Bugfix 发版后同步回 `release`，统一在 GitLab MR 中完成。

默认保护策略：

- `release` 和 `bugfix/<版本号>`：禁止直接 Push，仅允许 Maintainer 通过 MR 合入。
- `feature/*`：不设置为保护分支，方便开发人员进行日常开发提交。

## 分支规则

```text
release
feature/{来源}_{功能描述}
feature/{来源}_{任务号}_{功能描述}
bugfix/<版本号>
```

规则摘要：

- `release` 只有一个，是下一版本功能集成和提测分支。
- `feature/*` 可以从 `release`、`bugfix/<版本号>` 或迁移期 `fix` 拉出。
- `feature/*` 分支名称需要简洁体现来源分支；任务号可选，功能描述必填。
- `feature/*` 必须遵循“从哪拉出，就合入到哪”的原则。
- `bugfix/<版本号>` 是版本级长期维护分支，不是个人问题级短分支。
- `bugfix/<版本号>` 稳定节点在自身分支打 Tag 进行提测或发版。
- Bugfix 发版后，修复内容必须通过 GitLab MR 同步回 `release`。

## 完整发版来源解析

完整发版以 SimOS 的来源分支为主线，SimOS 必须具有请求的来源分支。非 SimOS 业务仓库优先使用同名来源 ref；缺失时使用任务中配置的“Feature 缺失时回退分支”（默认 `release`）的最新 commit。该规则同样适用于 `fix_otaEnvVi` 一类非 `feature/*` 的来源 ref。

WebApp 会在发版计划生成时记录每个仓库实际使用的来源分支和 commit。版本 MR、重试与最终 Tag 都复用这份快照，因此等待版本 MR 合并期间其他分支推进不会改变本次包的组件组合。SimOS Tag 会固化这些子模块 commit，现有 CI 继续按该 Tag 构建，无需修改 CI 文件。

Feature 测试包规则：来源必须为 `feature/*`；服务端从该分支的 `version.info` 读取四段版本，默认沿用当前版本计算逻辑递增第四位。勾选“切换周版本（第三位 +1）”只增加计算结果的第三位，第四位不会被重置为 `001`。Tag 由服务端生成（例如 `feature-release_login_T3.1.24.021_202608071530`），user 不能手填或删除。SimOS 使用 `automation/feature-package/...` 临时构建分支写入 T 版本与组件 commit 快照后打 Tag；原始 Feature 分支、正式版本线和 Release 任务都不会被更新。Tag 创建会触发真实 GitLab CI，所有仓库预检通过前不会发生写操作。

版本号按精确 SimOS 来源分支独立维护：`fix`、`release`、`feature/ABC` 与 `feature/XYZ` 的版本文件和版本兜底值互不共享。

## 迁移说明

当前项目如果仍存在 `fix` 和 `dev`：

- 将现有 `fix` 视为上一版本的 Bugfix 维护线，暂时不强制重命名。
- 所有上一版本或历史版本发现的问题继续在 `fix` 修复。
- 从 `fix` 拉出新的 `release`。
- 新版本常规功能从 `release` 拉出 `feature/*`。
- 新版本固定版本修复从 `release` 拉出新的 `bugfix/<版本号>`。
- 如果上一版本的小需求必须进入 `fix`，可以从 `fix` 拉出 Feature，但必须合回 `fix`。
- `dev` 不再作为新规则下的功能开发来源。

## 配置

复制示例文件并填写 GitLab Token：

```bash
cp .env.example .env.local
```

关键变量：

```text
GITLAB_TOKEN=replace-with-a-gitlab-token
```

Token 需要具备 `api` scope，并至少拥有目标项目 Maintainer 权限。新增仓库时可以复用 `GITLAB_TOKEN`，也可以在 `.env.local` 中添加新的 Token 变量，例如 `GITLAB_TOKEN_OTHER`，然后在页面的 `token_env` 填写该变量名。

发版任务会为版本号更新创建 MR，并立即通过 GitLab API 请求合入；只有 GitLab 实际报告该 MR 已合入后，系统才会继续创建 Tag。请确保 SimOS 仓库所使用的 Token 同时具备 `api` scope 和该项目的 MR 合入权限。GitLab 的保护分支、审批和可合并性限制仍然有效；若 GitLab 拒绝合入，页面会显示原因并允许在自动重试次数耗尽后执行“一键重试自动合并”。

```text
GITOPS_RELEASE_RUN_POLL_SECONDS=10
```

该变量控制服务端检查未完成发版运行的秒数，默认值为 `10`；它负责在浏览器关闭或服务恢复后继续检查版本 MR 的实际合入状态。resident 包状态不修改 GitLab CI，而是读取 Tag Pipeline 和 Job 的真实状态；页面仅在发版任务页可见且构建活跃时每 5 秒刷新。

仓库列表保存到：

```text
data/repositories.json
```

该文件只保存 GitLab 地址、项目路径和 Token 环境变量名，不保存 Token 明文。

默认登录账号：

```text
admin / admin123
user  / user123
```

上线或绑定非本机地址前，请通过 `.env.local` 覆盖 `GITOPS_ADMIN_PASSWORD`、`GITOPS_USER_PASSWORD` 和 `GITOPS_SESSION_SECRET`。

## 启动

```bash
python3 server.py --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

服务器迁移时，只需要 Python 3、项目文件和可访问 GitLab 的网络环境。

## 本地模拟 GitLab/CI

不要用生产 `.env.local` 验证 Feature 打包。模拟模式使用本地 JSON 保存分支、Tag、文件和 Pipeline 状态，不访问 GitLab，也不会触发服务器 CI。

从项目根目录启动：

```bash
docker compose -f docker-compose.simulation.yml up --build
```

浏览器打开 `http://127.0.0.1:8765`，使用 `user / user123` 登录。模拟数据预置 `feature/release_login`、`V3.1.24.020` 和一个历史 T Tag，可验证默认第四位递增和第三位递增。

模拟模式由 `GITOPS_MODE=simulation` 控制：它只构造本地 `SimulatedGitLabClient`，数据只写入 `webapp/data-simulation`，不会使用生产 Token、`/data/simos-ci`、TLS 证书或自动发版调度。模拟 Tag 会生成成功状态的本地 Pipeline 记录。

停止后恢复初始模拟数据：

```bash
docker compose -f docker-compose.simulation.yml down
git checkout -- webapp/data-simulation/simulation-state.json
```

验证真实 `.gitlab-ci.yml`、Runner 和构建产物时，应使用独立 GitLab sandbox 项目及 Token；不要将生产项目配置放进模拟 Compose。
