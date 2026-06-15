# GitOps Workbench 部署与操作手册

本文档记录 `gitops-workbench` 的服务器部署、更新发布、重启验证和常见排障步骤。

## 1. 当前部署信息

| 项目 | 值 |
| --- | --- |
| 应用名称 | GitOps Workbench |
| 服务器 | `192.168.110.222` |
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
/etc/gitlab/ssl/www.chancee-shanghai.cn-crt.pem
/etc/gitlab/ssl/www.chancee-shanghai.cn-key.pem
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
GITOPS_TLS_CERT=/etc/gitlab/ssl/www.chancee-shanghai.cn-crt.pem
GITOPS_TLS_KEY=/etc/gitlab/ssl/www.chancee-shanghai.cn-key.pem
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
GITLAB_HOST_IP=192.168.110.222
GITOPS_TLS_CERT=/etc/gitlab/ssl/www.chancee-shanghai.cn-crt.pem
GITOPS_TLS_KEY=/etc/gitlab/ssl/www.chancee-shanghai.cn-key.pem
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
ssh admin1@192.168.110.222 "whoami && hostname"
```

确认 Docker 权限可用：

```bash
ssh admin1@192.168.110.222 "docker ps"
```

确认部署目录存在：

```bash
ssh admin1@192.168.110.222 "ls -ld /opt/gitops-workbench /data/gitops-workbench/data"
```

确认生产环境变量存在：

```bash
ssh admin1@192.168.110.222 "test -f /opt/gitops-workbench/.env && echo ENV_OK"
```

确认证书存在：

```bash
ssh admin1@192.168.110.222 "test -f /etc/gitlab/ssl/www.chancee-shanghai.cn-crt.pem && echo CERT_OK"
ssh admin1@192.168.110.222 "test -f /etc/gitlab/ssl/www.chancee-shanghai.cn-key.pem && echo KEY_OK"
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
1. 检查服务器部署目录、数据目录、.env 和证书
2. 检查 admin1 是否能执行 docker
3. 使用 rsync 同步项目文件到 /opt/gitops-workbench
4. 在服务器执行 docker compose up -d --build --remove-orphans
5. 查看容器状态
6. 验证 https://127.0.0.1:9910/api/session
```

部署脚本不会上传本地 `.env`，不会修改服务器证书，也不会删除服务器上的生产环境变量。

## 6. 发布后验证

查看容器状态：

```bash
ssh admin1@192.168.110.222 "cd /opt/gitops-workbench && docker compose ps"
```

期望看到：

```text
gitops-workbench   Up ... (healthy)   0.0.0.0:9910->9910/tcp
```

查看最近日志：

```bash
ssh admin1@192.168.110.222 "docker logs --tail 80 gitops-workbench"
```

验证服务器本机 API：

```bash
ssh admin1@192.168.110.222 "curl -kfsS https://127.0.0.1:9910/api/session"
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
curl -kfsS https://192.168.110.222:9910/api/session
```

验证域名指向内网服务器：

```bash
curl --noproxy '*' \
  --resolve www.chancee-shanghai.cn:9910:192.168.110.222 \
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
ssh admin1@192.168.110.222 "systemctl is-enabled docker"
```

期望输出：

```text
enabled
```

确认 Docker 服务当前运行中：

```bash
ssh admin1@192.168.110.222 "systemctl is-active docker"
```

期望输出：

```text
active
```

确认容器重启策略：

```bash
ssh admin1@192.168.110.222 "docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' gitops-workbench"
```

期望输出：

```text
unless-stopped
```

只要不是手动执行 `docker compose down` 或 `docker stop gitops-workbench` 后长期停掉，服务器重启后 Docker 会自动拉起该容器。

### 7.2 日志在哪里

应用日志输出到容器的 stdout/stderr，由 Docker 日志驱动统一保存。日常查看日志使用：

```bash
ssh admin1@192.168.110.222 "docker logs --tail 100 gitops-workbench"
```

持续跟踪日志：

```bash
ssh admin1@192.168.110.222 "docker logs -f gitops-workbench"
```

查看最近 30 分钟日志：

```bash
ssh admin1@192.168.110.222 "docker logs --since 30m gitops-workbench"
```

Docker 物理日志文件位于 Docker 数据目录下，路径可以这样查看：

```bash
ssh admin1@192.168.110.222 "docker inspect --format '{{.LogPath}}' gitops-workbench"
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
ssh admin1@192.168.110.222
cd /opt/gitops-workbench
docker compose restart
```

重新构建并启动：

```bash
ssh admin1@192.168.110.222
cd /opt/gitops-workbench
docker compose up -d --build
```

停止服务：

```bash
ssh admin1@192.168.110.222
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
ssh admin1@192.168.110.222 \
  "cp /data/gitops-workbench/data/repositories.json /data/gitops-workbench/data/repositories.json.bak.$(date +%Y%m%d%H%M%S)"
```

查看文件：

```bash
ssh admin1@192.168.110.222 "cat /data/gitops-workbench/data/repositories.json"
```

不要把 GitLab Token 写入 `repositories.json`。该文件只保存 token 环境变量名，真实 token 存在 `.env` 中。

## 10. 网络访问说明

应用已经在服务器监听：

```text
192.168.110.222:9910
```

如果要让同事直接访问：

```text
https://www.chancee-shanghai.cn:9910
```

需要网络管理员配置端口映射或代理：

```text
223.166.55.198:9910 -> 192.168.110.222:9910
```

或在内网 DNS 中添加：

```text
www.chancee-shanghai.cn -> 192.168.110.222
```

`9700` 能访问不代表 `9910` 自动可访问。每个端口都需要单独放通或转发。

## 11. 常见问题

### 11.1 `docker ps` 提示 permission denied

说明当前用户没有 Docker 权限。

检查：

```bash
ssh admin1@192.168.110.222 "id"
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
ssh admin1@192.168.110.222 "cd /opt/gitops-workbench && docker compose ps"
```

查看日志：

```bash
ssh admin1@192.168.110.222 "docker logs --tail 120 gitops-workbench"
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
ssh admin1@192.168.110.222 "ls -la /etc/gitlab/ssl"
```

确认 `.env` 中路径正确：

```bash
ssh admin1@192.168.110.222 "grep '^GITOPS_TLS_' /opt/gitops-workbench/.env"
```

不要修改 `/etc/gitlab/ssl` 里的原证书。项目只需要只读挂载。

### 11.4 域名访问失败，但 IP 可以访问

如果下面命令可以：

```bash
curl -kfsS https://192.168.110.222:9910/api/session
```

但下面命令不可以：

```bash
curl -kfsS https://www.chancee-shanghai.cn:9910/api/session
```

通常说明 DNS、代理或公网端口映射没有配置好。需要检查：

```text
www.chancee-shanghai.cn 解析到了哪里
公网 9910 是否转发到 192.168.110.222:9910
公司代理是否允许 9910
防火墙是否放通 TCP 9910
```

### 11.5 GitLab API 操作失败

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
192.168.110.222
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
ssh admin1@192.168.110.222 "cd /opt/gitops-workbench && docker compose down"
```

## 13. 安全注意事项

- 不要提交 `.env`。
- 不要在聊天、截图或文档中暴露 GitLab Token。
- 不要修改 `/etc/gitlab/ssl` 中 GitLab 正在使用的证书原件。
- 不要手动删除 `/data/gitops-workbench/data/repositories.json`。
- 发布前先运行测试。
- 发布后检查容器状态和日志。
