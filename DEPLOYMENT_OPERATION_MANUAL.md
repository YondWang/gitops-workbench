# GitOps Workbench 部署与操作手册

本文档记录 `gitops-workbench` 的服务器部署、更新发布、重启验证和常见排障步骤。

## 1. 当前部署信息

| 项目 | 值 |
| --- | --- |
| 应用名称 | GitOps Workbench |
| 服务器 | `192.168.110.17` |
| SSH 用户 | `admin1` |
| 服务器部署目录 | `/opt/gitops-workbench` |
| 服务器数据目录 | `/data/gitops-workbench/data` |
| 容器名 | `gitops-workbench` |
| 镜像名 | `gitops-workbench:latest` |
| 监听端口 | `9910` |
| 访问地址 | `https://www.chancee-shanghai.cn:9910` |
| 本机验证地址 | `https://127.0.0.1:9910` |

当前部署方式是单容器部署：

```text
Browser
  -> https://www.chancee-shanghai.cn:9910
  -> gitops-workbench container
  -> Python HTTPS server
```

容器直接监听 HTTPS `9910`，不再额外启动 Nginx 容器。

## 2. 证书说明

应用复用服务器上 GitLab 已有证书：

```text
/etc/gitlab/ssl/chancee-shanghai.cn-crt.pem
/etc/gitlab/ssl/chancee-shanghai.cn-key.pem
```

项目不会修改原始证书文件。`docker-compose.yml` 中将证书目录只读挂载到容器：

```yaml
volumes:
  - /etc/gitlab/ssl:/etc/gitlab/ssl:ro
```

证书相关环境变量位于服务器：

```text
/opt/gitops-workbench/.env
```

推荐配置：

```env
GITOPS_TLS_CERT=/etc/gitlab/ssl/chancee-shanghai.cn-crt.pem
GITOPS_TLS_KEY=/etc/gitlab/ssl/chancee-shanghai.cn-key.pem
```

## 3. 生产环境变量

生产环境变量文件在服务器：

```bash
/opt/gitops-workbench/.env
```

至少需要包含：

```env
GITLAB_TOKEN=replace-with-real-token
GITOPS_ADMIN_PASSWORD=replace-with-strong-admin-password
GITOPS_USER_PASSWORD=replace-with-strong-user-password
GITOPS_SESSION_SECRET=replace-with-long-random-secret
GITLAB_HOST_IP=192.168.110.17
GITOPS_TLS_CERT=/etc/gitlab/ssl/chancee-shanghai.cn-crt.pem
GITOPS_TLS_KEY=/etc/gitlab/ssl/chancee-shanghai.cn-key.pem
PYTHON_BASE_IMAGE=docker.m.daocloud.io/python:3.12-slim
```

生成 `GITOPS_SESSION_SECRET`：

```bash
openssl rand -base64 48
```

不要把 `.env` 提交到 Git，也不要在聊天、工单或截图里泄露其中内容。

## 4. 服务器前置条件

确认 SSH 可用：

```bash
ssh admin1@192.168.110.17 "whoami && hostname"
```

确认 Docker 权限可用：

```bash
ssh admin1@192.168.110.17 "docker ps"
```

确认部署目录存在：

```bash
ssh admin1@192.168.110.17 "ls -ld /opt/gitops-workbench /data/gitops-workbench/data"
```

确认生产环境变量存在：

```bash
ssh admin1@192.168.110.17 "test -f /opt/gitops-workbench/.env && echo ENV_OK"
```

确认证书存在：

```bash
ssh admin1@192.168.110.17 "test -f /etc/gitlab/ssl/chancee-shanghai.cn-crt.pem && echo CERT_OK"
ssh admin1@192.168.110.17 "test -f /etc/gitlab/ssl/chancee-shanghai.cn-key.pem && echo KEY_OK"
```

## 5. 常规发布流程

本地修改代码后，在本地仓库根目录执行：

```bash
cd /home/simpleai/CodeManage/gitops-workbench
```

先运行测试：

```bash
python3 -m unittest discover webapp
```

部署到服务器：

```bash
./deploy/deploy-to-server.sh
```

如果在 Windows PowerShell 中执行，可以用：

```powershell
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/simpleai/CodeManage/gitops-workbench && ./deploy/deploy-to-server.sh"
```

部署脚本会执行以下操作：

```text
1. 检查服务器目录、.env、证书、Docker Compose 和正在运行的 Nextcloud
2. 使用 rsync 增量同步应用和随附发布 helper
3. 检查发布 helper、sudo 规则和 incoming/artifacts/sim-test-reports 目录
4. 仅对缺失或变化的依赖执行安装更新；需要时统一提示 sudo 密码
5. 检查应用构建输入与镜像 ID；一致则跳过 build，否则使用 Docker 缓存构建
6. 执行 docker compose up -d --wait --remove-orphans
7. 查看容器状态并验证 HTTPS 和仓库配置
```

部署脚本不会上传本地 `.env`，不会修改服务器证书，也不会删除服务器上的生产环境变量。

### 5.1 依赖安装与增量更新

同一个 `./deploy/deploy-to-server.sh` 命令同时负责现有服务器的应用和发布依赖。首次运行或依赖有变化时，脚本会提示输入服务器 sudo 密码；依赖一致时只检查并跳过安装，不需要 sudo 提示。

| 对象 | 每次检查 | 安装或更新条件 |
| --- | --- | --- |
| resident/deb、仿真发布 helper | 内容 SHA-256、权限和 root 所有权 | 缺失、内容变化或权限漂移 |
| 发布 sudo 规则 | 内容或已验证的 root 安装记录、权限 | 缺失或变化 |
| incoming、artifacts、报告目录 | 是否存在；incoming 的 runner 写权限 | 缺失或权限异常；不递归扫描历史产物 |
| Python、curl、sudo | 命令是否存在 | 仅缺失时安装软件包；已有软件包不自动升级 |
| Docker、Compose、Runner 用户、Nextcloud | 已有环境可用性 | 不重装现有基础设施，异常时明确终止 |
| 应用镜像 | Dockerfile、Compose、构建参数所在 .env、webapp 内容及镜像 ID | 输入变化、镜像丢失或强制构建 |

发布 helper 随仓库存放在 `deploy/publishers/`，部署时不依赖本机其他 simos checkout。升级 helper 时更新该目录中的受审核版本；安装到 `/usr/local/bin/` 后只允许 root 修改。独立 sudo 文件为 `/etc/sudoers.d/gitops-workbench-publishers`，保留服务器原有 sudo 规则。

构建成功记录保存在服务器 `.deploy-state/`，rsync 不会删除该目录；失败的构建不更新记录。日常部署不使用 `apt upgrade`、不重复下载编译镜像，也不会重启 Nextcloud 或 Runner。首次启用增量记录时会执行一次缓存构建。

如需强制重新执行应用构建（仍使用 Docker 缓存）：

```bash
DEPLOY_FORCE_BUILD=true ./deploy/deploy-to-server.sh
```

仅检查服务器发布依赖：

```bash
ssh admin1@192.168.110.17 'bash /opt/gitops-workbench/deploy/setup-host.sh --check'
```

返回 0 表示一致，10 表示需要安装更新，其他值表示检查失败。非交互环境需要已经具备对应 sudo 权限；脚本不会自动授予部署账号免密管理权限。Nextcloud 容器检查默认使用 `envs-app-1`，可通过本地 `DEPLOY_NEXTCLOUD_CONTAINER` 指定其他已有容器。

## 6. 发布后验证

查看容器状态：

```bash
ssh admin1@192.168.110.17 "cd /opt/gitops-workbench && docker compose ps"
```

期望看到：

```text
gitops-workbench   Up ... (healthy)   0.0.0.0:9910->9910/tcp
```

查看最近日志：

```bash
ssh admin1@192.168.110.17 "docker logs --tail 80 gitops-workbench"
```

验证服务器本机 API：

```bash
ssh admin1@192.168.110.17 "curl -kfsS https://127.0.0.1:9910/api/session"
```

期望返回：

```json
{
  "ok": true,
  "session": null
}
```

验证内网 IP：

```bash
curl -kfsS https://192.168.110.17:9910/api/session
```

验证域名指向内网服务器：

```bash
curl --noproxy '*' \
  --resolve www.chancee-shanghai.cn:9910:192.168.110.17 \
  https://www.chancee-shanghai.cn:9910/api/session
```

## 7. 自动启动与日志

### 7.1 服务器重启后是否会自动启动

会自动启动，依赖两个条件：

```text
1. Docker 服务开机自启
2. gitops-workbench 容器配置 restart: unless-stopped
```

当前 `docker-compose.yml` 已配置：

```yaml
restart: unless-stopped
```

确认 Docker 服务是否开机自启：

```bash
ssh admin1@192.168.110.17 "systemctl is-enabled docker"
```

期望输出：

```text
enabled
```

确认 Docker 服务当前运行中：

```bash
ssh admin1@192.168.110.17 "systemctl is-active docker"
```

期望输出：

```text
active
```

确认容器重启策略：

```bash
ssh admin1@192.168.110.17 "docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' gitops-workbench"
```

期望输出：

```text
unless-stopped
```

只要不是手动执行 `docker compose down` 或 `docker stop gitops-workbench` 后长期停掉，服务器重启后 Docker 会自动拉起该容器。

### 7.2 日志在哪里

应用日志输出到容器的 stdout/stderr，由 Docker 日志驱动统一保存。日常查看日志使用：

```bash
ssh admin1@192.168.110.17 "docker logs --tail 100 gitops-workbench"
```

持续跟踪日志：

```bash
ssh admin1@192.168.110.17 "docker logs -f gitops-workbench"
```

查看最近 30 分钟日志：

```bash
ssh admin1@192.168.110.17 "docker logs --since 30m gitops-workbench"
```

Docker 物理日志文件位于 Docker 数据目录下，路径可以这样查看：

```bash
ssh admin1@192.168.110.17 "docker inspect --format '{{.LogPath}}' gitops-workbench"
```

通常类似：

```text
/var/lib/docker/containers/<container-id>/<container-id>-json.log
```

该文件由 Docker 管理，普通用户通常不需要直接读取。使用 `docker logs` 是推荐方式。

当前 Compose 已配置日志轮转：

```yaml
logging:
  driver: json-file
  options:
    max-size: "20m"
    max-file: "5"
```

也就是说，单个日志文件最大约 `20MB`，最多保留 `5` 个文件，避免服务长期运行导致日志无限增长。

## 8. 手动重启服务

只重启容器，不重新构建：

```bash
ssh admin1@192.168.110.17
cd /opt/gitops-workbench
docker compose restart
```

重新构建并启动：

```bash
ssh admin1@192.168.110.17
cd /opt/gitops-workbench
docker compose up -d --build
```

停止服务：

```bash
ssh admin1@192.168.110.17
cd /opt/gitops-workbench
docker compose down
```

## 9. 查看和维护数据

仓库配置文件持久化在：

```text
/data/gitops-workbench/data/repositories.json
```

备份数据文件：

```bash
ssh admin1@192.168.110.17 \
  "cp /data/gitops-workbench/data/repositories.json /data/gitops-workbench/data/repositories.json.bak.$(date +%Y%m%d%H%M%S)"
```

查看文件：

```bash
ssh admin1@192.168.110.17 "cat /data/gitops-workbench/data/repositories.json"
```

不要把 GitLab Token 写入 `repositories.json`。该文件只保存 token 环境变量名，真实 token 存在 `.env` 中。

## 10. 网络访问说明

应用已经在服务器监听：

```text
192.168.110.17:9910
```

如果要让同事直接访问：

```text
https://www.chancee-shanghai.cn:9910
```

需要网络管理员配置端口映射或代理：

```text
223.166.55.198:9910 -> 192.168.110.17:9910
```

或在内网 DNS 中添加：

```text
www.chancee-shanghai.cn -> 192.168.110.17
```

`9700` 能访问不代表 `9910` 自动可访问。每个端口都需要单独放通或转发。

## 11. 常见问题

### 11.1 `docker ps` 提示 permission denied

说明当前用户没有 Docker 权限。

检查：

```bash
ssh admin1@192.168.110.17 "id"
```

需要看到 `docker` 组。

如果没有，需要管理员执行：

```bash
sudo usermod -aG docker admin1
```

然后退出 SSH 并重新登录。

### 11.2 容器启动后不是 healthy

查看状态：

```bash
ssh admin1@192.168.110.17 "cd /opt/gitops-workbench && docker compose ps"
```

查看日志：

```bash
ssh admin1@192.168.110.17 "docker logs --tail 120 gitops-workbench"
```

重点检查：

```text
证书路径是否正确
.env 是否存在
GITLAB_TOKEN 是否已设置
9910 端口是否被占用
```

### 11.3 证书读取失败

确认文件存在：

```bash
ssh admin1@192.168.110.17 "ls -la /etc/gitlab/ssl"
```

确认 `.env` 中路径正确：

```bash
ssh admin1@192.168.110.17 "grep '^GITOPS_TLS_' /opt/gitops-workbench/.env"
```

不要修改 `/etc/gitlab/ssl` 里的原证书。项目只需要只读挂载。

### 11.4 域名访问失败，但 IP 可以访问

如果下面命令可以：

```bash
curl -kfsS https://192.168.110.17:9910/api/session
```

但下面命令不可以：

```bash
curl -kfsS https://www.chancee-shanghai.cn:9910/api/session
```

通常说明 DNS、代理或公网端口映射没有配置好。需要检查：

```text
www.chancee-shanghai.cn 解析到了哪里
公网 9910 是否转发到 192.168.110.17:9910
公司代理是否允许 9910
防火墙是否放通 TCP 9910
```

### 11.5 GitLab API 操作失败

如果日志或页面出现：

```text
GitLab API 返回 401：invalid_token
```

并带有 `Token is expired`，说明 `.env` 中的 `GITLAB_TOKEN` 已过期，不是代码问题。

如果 Token 以 `glft-` 开头，说明复制成了 **Feed Token**（仅用于 RSS/Atom 订阅），不是 **Personal Access Token**，GitLab API 会返回 `401 Unauthorized`。必须使用 `glpat-` 开头、勾选 `api` scope 的 Personal Access Token。

处理步骤：

1. 登录 GitLab，进入 **User Settings → Access Tokens**，新建一个 Personal Access Token。
2. 勾选 `api` scope，并确认该账号对 `OS/simos` 至少具有 Maintainer 权限。
3. 在服务器更新 `/opt/gitops-workbench/.env`：

   ```env
   GITLAB_TOKEN=glpat-新Token
   ```

4. 重新加载容器环境并重启：

   ```bash
   ssh admin1@192.168.110.17 \
     "cd /opt/gitops-workbench && docker compose up -d --force-recreate"
   ```

5. 验证新 Token：

   ```bash
   ssh admin1@192.168.110.17 \
     "docker exec gitops-workbench python -c 'import os, server; app, _ = server.build_app(None); app.target(\"simos\").client.project()'"
   ```

   验证通过后，在 GitLab 中撤销旧 Token。

检查 `.env` 中：

```env
GITLAB_TOKEN=...
```

Token 需要具备：

```text
api scope
目标项目 Maintainer 权限
```

应用默认访问：

```text
https://www.chancee-shanghai.cn:9900
```

容器中通过 `extra_hosts` 将该域名指向：

```text
192.168.110.17
```

这样容器访问 GitLab 时走内网服务器。

## 12. 回滚方式

如果新版本有问题，推荐回到上一个 Git 提交后重新部署：

```bash
cd /home/simpleai/CodeManage/gitops-workbench
git log --oneline -5
git checkout <previous-commit>
./deploy/deploy-to-server.sh
```

确认恢复后，再切回需要开发的分支。

如果只是临时停掉服务：

```bash
ssh admin1@192.168.110.17 "cd /opt/gitops-workbench && docker compose down"
```

## 13. 安全注意事项

- 不要提交 `.env`。
- 不要在聊天、截图或文档中暴露 GitLab Token。
- 不要修改 `/etc/gitlab/ssl` 中 GitLab 正在使用的证书原件。
- 不要手动删除 `/data/gitops-workbench/data/repositories.json`。
- 发布前先运行测试。
- 发布后检查容器状态和日志。
