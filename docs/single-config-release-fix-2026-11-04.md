# 单配置发版修复与验收

本次保留现有默认值。关闭双配置矩阵并填写 OS/config 分支时，resident、deb 各生成一个编译 job；开启矩阵时保留原有 A/B 两组并行 job。

## 问题原因

1. simos 的 `.gitlab-ci.yml` 固定创建 A/360、B/360s 两组 job，再由脚本跳过未选配置。填写 `feature/v3.2_x86ci` 时没有对应 job，两组都会跳过。
2. CI 对环境变量与 Tag message 的优先级按字段交错处理，旧 Tag 的矩阵可能覆盖新的单个 ref。
3. x86 构建读取 Tag 单个 ref 优先于矩阵，且没有统一使用 Pipeline 覆盖参数。
4. CI 文件未给出 `SIMOS_X86_BUILD_IMAGE`，本次运行落到 Runner 默认 `ubuntu:24.04` 并在拉取阶段失败。

## 修复契约

Workbench 根据实际选中的配置数量发送：

```text
SIMOS_CONFIG_MODE=single
SIMOS_CONFIG_MATRIX=feature/v3.2_x86ci:feature-v3.2_x86ci
SIMOS_CONFIG_REFS=feature/v3.2_x86ci
SIMOS_CONFIG_REF=feature/v3.2_x86ci
```

多配置发送 `SIMOS_CONFIG_MODE=matrix`。simos 仅在 mode 不为 single 时加载 `ci/release-config-matrix.yml`。mode 未提供时保留旧双矩阵行为，以兼容旧客户端。已有 Tag 重跑由 Workbench 读取 Tag message 后补齐 mode 和配置变量。

CI 配置选择顺序为 Pipeline 环境变量、Tag message、旧默认矩阵；每个来源内部按 MATRIX、REFS、REF 排序。single 模式要求恰好一个配置，数量不正确直接失败。固定矩阵 job 必须同时匹配 ref 和 label。

x86 使用用户确认的镜像：

```text
chancee-robotics-registry-vpc.cn-shanghai.cr.aliyuncs.com/cloud-platform/ubuntu-2004-x86-sim-chancee@sha256:9a69432df539fe8d94ae993cab69d1fa74f677ee6be85afd5442d110114c54ac
```

项目或组级 `SIMOS_X86_BUILD_IMAGE` 若存在，仍优先于 YAML 默认值；需核对其值及保护分支、环境作用域。

## 上线与真实验收

1. 将 simos 的 CI 修复（包含新增 include 文件）纳入 `feature/v3.2_x86ci` 来源分支，并部署对应 Workbench 修改。单配置新 job 布局依赖两端同时更新。
2. 创建新的发版 Tag：simos 来源与 OS/config 分支都选择 `feature/v3.2_x86ci`，关闭矩阵，按需勾选 x86 仿真包。旧 Tag 固定了旧 CI 文件，在同一 Tag 上重跑无法加载本次 CI 修复。
3. Pipeline 中 resident、deb 各只有一个编译 job。构建日志应出现 `selected single config: feature/v3.2_x86ci:feature-v3.2_x86ci`，config checkout 的实际分支和产物元数据应一致。
4. `sim_package_x86` 的 Preparing executor 阶段应使用上述 digest 镜像，随后进入构建脚本。镜像拉取、凭证与网络访问需在云端 Runner 验证。
5. resident/deb 的 Registry 文件名前缀与云盘配置目录使用 `feature-v3.2_x86ci`，发布端应读取相同 label；不应查找 360/360s 两组元数据。
6. 开启双配置进行回归，应恢复 A/360、B/360s 两组 job；默认勾选状态保持原样。

本地 YAML 合并与 job 展开测试覆盖项目的 include 契约，不替代 GitLab CI Lint。真实全量 C++ 编译、镜像拉取、Registry/云盘发布和测试平台联调需完成上述云端验收。

## 后续编译失败：config 分支名污染车型宏

2026-11-04 后续 x86 日志出现 `<command-line>: warning: ISO C++11 requires whitespace after the macro name`、`expected ... before '/' token` 和 `v3 was not declared`。根因为 `checkout-config-ref.sh` 无条件将 config 分支名写入 `vehicle.info` 第一行，随后 `simos.sh -s/-m` 将其拼成 `-Dfeature/v3.2_x86ci`。GCC 将 `feature` 定义为替换内容 `/v3.2_x86ci` 的宏，破坏 OpenCV 等头文件中同名参数。

用户确认规则：普通 config 分支保留 simos 来源提交中的车型，只有车型分支才同步。因此 checkout 脚本仅在 ref 匹配 `SIMBOT_[A-Z0-9]+(_[A-Z0-9]+)*` 时同步车型，其余分支不改写 `vehicle.info`。架构行不改动。config 分支和 commit 元信息继续记录实际检出的 ref/commit。

本次镜像配置保持原样：resident/deb 使用原 ARM 镜像，sim_package_x86 使用独立 x86 digest 镜像。上述错误已用真实 GCC 最小程序复现，修复不需要重建镜像。云端复验时应同时看到 config checkout 为 `feature/v3.2_x86ci`、`veh_type` 为 simos 原车型（当前 `SIMBOT_R6_A`），且不再出现 `-Dfeature/v3.2_x86ci`。
