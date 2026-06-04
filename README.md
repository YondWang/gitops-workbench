# gitops-control

`gitops-control` 是 GitLab 原生的版本/分支管理控制项目，用于管理：

```text
software_hmi_app/business
```

它不保存业务代码，只通过 GitLab CI Inputs + GitLab REST API 执行分支、MR、Tag 和同步检查操作。

## 1. 运行入口

进入 GitLab 页面：

```text
gitops-control -> 构建 -> 流水线 -> 新流水线
```

选择分支：

```text
main
```

然后在 `输入 / Inputs` 区域选择：

```text
operation
```

第一次建议先运行：

```text
operation = branch_list
target_project = software_hmi_app/business
```

## 2. 前置变量

项目必须配置 CI/CD 变量：

```text
GITLAB_API_TOKEN
```

要求：

```text
scope: api
role: Maintainer
```

位置：

```text
gitops-control -> 设置 -> CI/CD -> 变量
```

## 3. 多版本并行模型

示例：

```text
main
├── baseline/3.1.0.0
│   ├── fix/3.1.0.0-rc1
│   └── fix/3.1.0.0-rc2
├── baseline/3.2.0.0
│   ├── feature/3.2.0.0/TASK1234-user-login
│   └── fix/3.2.0.0-rc1
└── baseline/3.3.0.0
    └── feature/3.3.0.0/TASK2000-new-module
```

规则：

```text
baseline/{version}          每个版本独立基线
feature/{version}/{task}    从对应 baseline 拉出
fix/{version}-rcN           从 baseline 的提测节点拉出
bugfix/{version}/{bug}      从对应 fix 分支拉出
hotfix/{new_version}/{bug}  从 main 或 stable 拉出
```

## 4. 操作列表

| operation | 用途 |
| --- | --- |
| `version_init` | 初始化新版本，创建 `baseline/{version}` |
| `feature_create` | 创建功能分支 |
| `feature_merge` | 创建 feature -> baseline 的 MR |
| `fix_create` | 创建 `fix/{version}-rcN` 提测分支 |
| `bugfix_create` | 创建 bugfix 分支 |
| `bugfix_merge` | 创建 bugfix -> fix 的 MR，并可同步到 baseline |
| `release_tag` | 在 fix 分支创建 tag，可选创建 fix -> main MR |
| `release_stable` | 从 tag 创建 `stable/{major.minor}` |
| `hotfix_create` | 创建 hotfix 分支 |
| `hotfix_merge` | 创建 hotfix -> main MR、tag，并可同步到 baseline |
| `branch_list` | 列出分支 |
| `branch_compare` | 比较分支差异 |
| `mr_list` | 列出 MR |
| `tag_list` | 列出 tag |
| `sync_check` | 检查 fix 相对 baseline 的未同步 commit |
| `version_status` | 查看某版本相关分支和 tag |

## 5. 推荐测试顺序

### 5.1 查询分支

```text
operation = branch_list
pattern =
```

### 5.2 初始化测试版本

如果业务仓库还没有 `main`，把 `from_ref` 改成真实存在的分支，例如：

```text
operation = version_init
version = 3.2.0.0
from_ref = fix
```

创建：

```text
baseline/3.2.0.0
```

### 5.3 创建功能分支

```text
operation = feature_create
version = 3.2.0.0
task_id = TASK1234
description = user-login
```

创建：

```text
feature/3.2.0.0/TASK1234-user-login
```

### 5.4 创建提测 fix 分支

```text
operation = fix_create
version = 3.2.0.0
rc_number =
```

留空 `rc_number` 时自动查找已有 `fix/3.2.0.0-rcN` 并递增。

### 5.5 创建 bugfix 分支

```text
operation = bugfix_create
version = 3.2.0.0
rc_number = 1
bug_id = BUG001
description = crash
```

创建：

```text
bugfix/3.2.0.0/BUG001-crash
```

## 6. 同步与冲突

`bugfix_merge` 和 `hotfix_merge` 会尝试使用 GitLab cherry-pick API 同步 commit。

如果发生冲突，流水线会失败并输出：

```text
cherry-pick failed
```

这时需要人工在对应目标分支解决冲突，再重新发起 MR 或手动 cherry-pick。

## 7. 文件结构

```text
.gitlab-ci.yml
.gitlab/scripts/git-utils.sh
.gitlab/ci/version-init.yml
.gitlab/ci/feature-create.yml
.gitlab/ci/feature-merge.yml
.gitlab/ci/fix-create.yml
.gitlab/ci/bugfix-create.yml
.gitlab/ci/bugfix-merge.yml
.gitlab/ci/release-tag.yml
.gitlab/ci/release-stable.yml
.gitlab/ci/hotfix-create.yml
.gitlab/ci/hotfix-merge.yml
.gitlab/ci/branch-list.yml
.gitlab/ci/branch-compare.yml
.gitlab/ci/mr-list.yml
.gitlab/ci/tag-list.yml
.gitlab/ci/sync-check.yml
.gitlab/ci/version-status.yml
```

## Web 控制台

GitLab 分支管理 Web 工具位于 `webapp/` 目录。根目录 `.gitlab-ci.yml` 和 `.gitlab/` 下的 pipeline 文件保持原有用途，不受 Web 工具影响。
