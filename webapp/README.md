# GitLab Branch Workbench

轻量 Web 分支管理工具。后端使用 Python 标准库，核心 Git 操作统一通过 GitLab REST API v4 完成，不依赖本地 clone。工具支持在页面中维护多个 GitLab 仓库，并选择对当前仓库或全部启用仓库执行一键操作。

## 功能

- 页面中添加、编辑、启用、停用多个 GitLab 仓库。
- 读取并分组展示当前仓库的远端分支和 Tag。
- 从任意来源分支初始化 `baseline/{version}`。
- 从 baseline 创建下一个 `fix_{version}/rcN`。
- 从 baseline 创建 `feature/{version}/{TASKID-desc}`。
- 从 fix 分支创建发版 Tag，再创建并尝试合并 `fix -> baseline` Merge Request。
- 写操作可选择“当前仓库”或“全部启用仓库”；全部仓库会先做预检查，预检查失败时不会写任何仓库。
- 简单登录与角色权限：`user` 可查看和创建 feature；`admin` 可执行 baseline、fix、发版和 MR 操作。

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

## 版本号自动生成

Baseline 初始化时，版本号可以留空，由后端根据当前仓库已有分支和 Tag 中的最大四段式版本自动生成。

变更类型对应规则：

```text
major  架构不兼容：A+1，B/C/D 清零
minor  新功能基线：B+1，C/D 清零
patch  修复/优化：C+1，D 清零
build  构建/热修复：D+1
```

例如当前仓库最大版本为 `3.2.0.0`：

```text
minor -> 3.3.0.0
patch -> 3.2.1.0
build -> 3.2.0.1
```

前端也提供“自动填充版本号”按钮，便于操作前预览生成结果；特殊场景仍可手动覆盖。

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

Ubuntu 22.04 服务器迁移时，只需要 Python 3、项目文件和可访问 GitLab 的网络环境。

## 分支规则

```text
baseline/{version}
fix_{version}/rcN
feature/{version}/{TASKID-desc}
```

版本号必须为四段式，例如 `3.2.0.0`。发版默认 Tag 为 `v{version}-rcN`，页面中可以手动覆盖。
