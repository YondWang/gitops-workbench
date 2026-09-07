# Feature 用户使用说明与开放前检查

> 适用对象：Workbench 管理员、Feature 功能使用者、部门对外开放负责人。

本说明对应 Workbench 的账号体系与 Feature 测试包（T 包）流程。账号由管理员账号集中维护；生产环境不提供默认密码、不回退到默认账号，每位使用者通过独立账号登录并可按操作人审计。

---

## 1. 外部访问地址

- Web UI：`https://www.chancee-shanghai.cn:9910/`
- GitLab：`https://www.chancee-shanghai.cn:9900/`

登录页面即 Web 根路径。账号由管理员在「账号管理」中创建。

## 2. `user` 角色权限

`user` 角色只拥有以下三项权限，不包含任何写控制面（Release / Bugfix / Tag / 发版任务 / 仓库管理 / 账号管理）接口：

| 权限 | 说明 |
| --- | --- |
| `view` | 查看仓库总览、分支、Tag、Feature 构建记录 |
| `create_feature` | 创建 Feature 分支 |
| `create_feature_package` | 预览、创建 Feature 测试包 |

`admin` 权限不在普通账号上扩展。`admin` 除上述功能外可执行 Release / Bugfix / Tag、发版任务、仓库管理与账号管理。

## 3. 独立账号登录方式

1. 管理员进入「账号管理」，点击「添加账号」。
2. 填写用户名与角色（`user` / `admin`），**密码必填**，不允许使用默认密码。
3. 保存后该账号即时生效。账号信息持久化保存在 `/app/data/users.json`（仅保存 PBKDF2 哈希，不保存明文密码）。
4. 使用者在登录页输入该用户名与密码。
5. 密码被重置或账号被删除后，其已有会话会同步失效。

> 说明：Workbench 不提供用户自助改密页面。密码的创建与重置统一由管理员在「账号管理」中操作。

## 4. Feature 分支命名规则

- 分支前缀固定为 `feature/`。
- 命名建议携带可读描述或任务号，例如 `feature/user_login`、`feature/TASK-1234_hmi`。
- 一个 Feature 包需要所选的每个组件仓库都存在同名 Feature 分支；若只构建部分组件，则必须显式填写基线分支，保证未选组件有稳定的 commit 快照。

## 5. Feature 包流程

1. **预览**：在「Feature → 创建 Feature 测试包」选择 Feature 分支、基线分支（可选）与云盘分类，点击选择后会自动计算本次 T 包版本。预览只读，不占用任何构建资源。
2. **创建**：点击「创建测试包」，系统会校验分支与基线、解析各组件 commit 快照、生成可信上下文，并在可信 CI 仓库 `software_hmi_app/gitops-control` 的 `ci/feature-package` 上创建 Pipeline。
3. **结果查看**：在「Feature → Feature 构建记录」查看版本、来源、状态、Pipeline 链接与云盘目录。

## 6. Registry 成功与 Nextcloud 发布成功的区别

- **Registry 成功**：表示 Feature 包已构建并推送到包仓库（Registry），产物物理上可被拉取。
- **Nextcloud 发布成功**：表示在 Registry 成功的基础上，包还已从 Nextcloud 生成供测试下载的地址（云盘目录）。

两者状态均来自可信 Pipeline 的 `feature-package-result.json`，Workbench 不会根据构建产物自行猜测发布成功。若 Pipeline 成功但未生成结果文件，记录会显示为“失败”而不是冒报成功，以避免 UI 中的误报。

## 7. 失败处理与重试规则

- **参数校验失败**（分支不存在、未选 simos、云盘分类非法、客户端伪造字段等）：不会创建 Pipeline，也不会消耗任何资源，可直接修正后重试。
- **Pipeline 启动失败**：记录标记为 `failed` 并保留错误信息，可在修复后重新创建。
- 状态为 `success`、`failed`、`canceled` 的运行不会继续占用资源；处于构建中的运行会定期刷新状态。
- 管理员可删除异常的构建记录，但不会改变 Runner/Registry/Nextcloud 上的实际产物。

## 8. 构建并发

Workbench 侧**当前不实现服务端限流**，也没有每用户 1 小时创建次数限制或全局并发上限。构建的并行度由 CI Pipeline 的 Runner 自身并发（`concurrent`）控制，以保护 Runner、Registry 与 Nextcloud 资源。

> 若未来需要接入持久化限流（如每用户每小时 N 次、全局并发 M），需在 Workbench 侧补充限流模块并重新部署。

## 9. 联系管理员的场景

出现以下情况时请联系管理员，而不是自行绕过：

- 忘记密码/需要重置密码；
- 需要新建、停用或删除账号；
- 误操作删除/需要清理异常的构建记录；
- 怀疑 Token、密码或 CI 密钥可能泄露；
- 需要调整 `user`/`admin` 角色或新增使用人员；
- 部门开放前，需要管理员按第 11 节检查表确认。

## 10. 安全要求

- 严禁向任何人透露个人登录密码、GitLab Token、`GITOPS_SESSION_SECRET`、`GITOPS_FEATURE_CONTEXT_HMAC_KEY` 或任何 CI/CD 密钥。
- 密码只在本机创建/重置，并按安全策略定期由管理员轮换（重置后旧会话自动失效）。
- 生产 `.env` 中的 `GITOPS_USERS_JSON` 只允许 `password_hash`，禁止明文密码；`.env` 不提交仓库、不落入日志。
- 禁止共享同一个账号多人使用——账号用于追溯具体操作人（Feature 包记录的 `actor`）。

## 11. 部门开放前的管理员检查表

- [ ] 服务可正常启动，管理员能通过「账号管理」维护账号。
- [ ] 每个真实使用者都拥有独立账号，并由其本人持有密码。
- [ ] 建议覆盖默认 `admin`/`user` 密码，或为每个使用者配置独立账号与强密码。
- [ ] 所有账号密码在首登后已按策略使用强密码。
- [ ] 已用 1 个普通账号验证：登录 → 查看功能 → 创建 Feature 分支 → 预览 → 创建 Feature 包 → 查看运行记录。
- [ ] 用第 2 个普通账号验证操作人记录彼此独立（Feature 包记录可区分实际使用者）。
- [ ] 已用普通账号验证不能访问 Release / Bugfix / Tag / 发版任务 / 仓库管理 / 账号管理等管理接口。
- [ ] 确认 Runner、Registry、Nextcloud 的并发与存储足够支撑部门开放，且至少观察一个工作周期的日志。
- [ ] 已向使用人员宣贯第 4/7/10 节的命名、重试与安全要求。

## 12. 独立账号配置与密码哈希生成

生产账号来源为 `.env` 中的 `GITOPS_USERS_JSON`，每个账号必须包含 `role` 与 `password_hash`。

生成密码哈希（只输出哈希，不写仓库、不保存明文）：

```bash
# 交互式
python3 webapp/manage_auth.py hash-password

# 管道输入（同样不回显，仅打印哈希）
echo '这里填入你的强密码' | python3 webapp/manage_auth.py hash-password
```

推荐生产配置示例：

```json
{
  "zhangsan": {"role": "user", "password_hash": "pbkdf2_sha256$160000$..."},
  "lisi": {"role": "user", "password_hash": "pbkdf2_sha256$160000$..."},
  "workbench-admin": {"role": "admin", "password_hash": "pbkdf2_sha256$160000$..."}
}
```

操作说明：

1. 首次部署时，服务会以 `GITOPS_USERS_JSON` 初始化账号并写入 `/app/data/users.json`。
2. 之后管理员在「账号管理」中增删改账号，改动持久化到该文件。
3. `GITOPS_USERS_JSON` 可选，用于在启动时预置账号（建议不要用 `admin`/`user` 作为账号名，改用如 `workbench-admin`）。
4. `GITOPS_SESSION_SECRET` 建议用足够强度的随机值：`openssl rand -base64 48`。
5. 登录后管理员可直接在「账号管理」中增删改账号，账号持久化到 `data/users.json`。