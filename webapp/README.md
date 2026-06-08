# GitLab Branch Workbench

轻量 Web 分支管理工具。后端使用 Python 标准库，核心 Git 操作统一通过 GitLab REST API v4 完成，不依赖本地 clone。工具支持维护多个 GitLab 仓库，并可选择对当前仓库或全部启用仓库执行创建操作。

## 功能

- 页面中添加、编辑、启用、停用多个 GitLab 仓库。
- 读取并分组展示当前仓库的远端分支和 Tag。
- `admin` 可从指定来源分支或 Tag 创建唯一 `release` 分支。
- `user` 和 `admin` 可从 `release`、`bugfix/<版本号>` 或迁移期 `fix` 创建 `feature/{TASKID}_{desc}`。
- `admin` 可从指定来源分支或 Tag 创建版本级长期分支 `bugfix/<版本号>`。
- `admin` 可基于分支创建 Tag，默认命名为 `<来源>-<yyyyMMddHHmmss>`；来源分支中的 `/` 会替换为 `-`。
- 写操作可选择“当前仓库”或“全部启用仓库”；全部仓库会先做预检查，预检查失败时不会写任何仓库。

Web 工具只做分支创建和 Tag 创建，不做 Feature 合入、Bugfix 同步或自动 MR。Feature 合回来源分支、Bugfix 发版后同步回 `release`，统一在 GitLab MR 中完成。

## 分支规则

```text
release
feature/{TASKID}_{desc}
bugfix/<版本号>
```

规则摘要：

- `release` 只有一个，是下一版本功能集成和提测分支。
- `feature/*` 可以从 `release`、`bugfix/<版本号>` 或迁移期 `fix` 拉出。
- `feature/*` 必须遵循“从哪拉出，就合入到哪”的原则。
- `bugfix/<版本号>` 是版本级长期维护分支，不是个人问题级短分支。
- `bugfix/<版本号>` 稳定节点在自身分支打 Tag 进行提测或发版。
- Bugfix 发版后，修复内容必须通过 GitLab MR 同步回 `release`。

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
