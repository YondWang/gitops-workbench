# 发布 helper 随包版本

这些脚本来自 simos 的 `ci/resident/server/` 和 `ci/sim/server/`，随 Workbench 部署到已有 Nextcloud/Runner 主机。部署不读取旁边的 simos checkout，不在运行时下载未固定版本。

当前能力：

- `simos-ci-publish-resident`：`config-matrix-runtime-switchable-isolated-layout-v2`，兼容 `feature-package-v1`。
- `simos-ci-publish-sim`：`sim-package-v1`，包含仿真包和测试报告发布。

更新方式：将验证过的源脚本同步到本目录，运行部署测试和能力检查，再使用 `deploy/deploy-to-server.sh`。部署器按内容校验和、权限与所有权判断是否更新，不依据时间戳决定版本。

本目录只存脚本，不存密钥、账号或生产环境变量。宿主机安装由 `deploy/setup-host.sh` 和 `deploy/host-dependencies.py` 管理。
