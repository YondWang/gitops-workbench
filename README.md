# gitops-control

`gitops-control` 是 GitLab 原生的版本/分支管理控制项目，用于管理：

```text
software_hmi_app/business
```

它不保存业务代码，只通过 GitLab CI Inputs + GitLab REST API 执行分支、MR、Tag 和同步检查操作。

## 1. 使用方式

进入 GitLab 页面：

```text
gitops-control -> 构建 -> 流水线 -> 新流水线
```

选择分支：

```text
codex/gitlab-manual-operation-buttons
```

创建流水线时只需要填写一组公共信息。进入流水线详情后，会看到多个手动 job 按钮，每个按钮就是一个管理操作。

推荐第一次只填写：

```text
target_project = software_hmi_app/business
version = 3.2.0.0
task_id = TASK1234
bug_id = BUG001
description = test
```

然后先点击：

```text
button_branch_list
```

确认能列出业务仓库分支后，再测试创建类按钮。

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

## 3. 按钮和自动规则

| 按钮 | 用途 | 自动规则 |
| --- | --- | --- |
| `button_branch_list` | 列出分支 | 使用 `pattern` 过滤；留空显示全部 |
| `button_version_status` | 查看版本状态 | 使用 `version` 查询相关分支和 tag |
| `button_version_init` | 初始化版本 | 创建 `baseline/{version}`；`from_ref` 留空默认从 `fix` 创建 |
| `button_feature_create` | 创建功能分支 | 创建 `feature/{version}/{TASK_ID-description}` |
| `button_feature_merge` | 合并功能分支 | `feature_branch` 留空时自动推导，并创建 `feature -> baseline` MR |
| `button_fix_create` | 创建提测分支 | 创建 `fix/{version}-rcN`；`rc_number` 留空时自动递增 |
| `button_bugfix_create` | 创建测试修复分支 | 创建 `bugfix/{version}/{BUG_ID-description}`，来源是 `fix/{version}-rc{rc_number}` |
| `button_bugfix_merge` | 合并测试修复 | `bugfix_branch` 留空时自动推导，创建 `bugfix -> fix` MR，并可同步 baseline |
| `button_release_tag` | 发布打 tag | `fix_branch` 留空时自动选择当前版本最新 RC，`tag_name` 留空时自动推导 |
| `button_release_stable` | 创建稳定归档分支 | `from_tag` 留空时使用 `v{version}`，创建 `stable/{major.minor}` |
| `button_hotfix_create` | 创建线上热修复分支 | 创建 `hotfix/{current_version+1}/{BUG_ID-description}`；`from_ref` 留空默认 `main` |
| `button_hotfix_merge` | 发布线上热修复 | `hotfix_branch` 留空时自动推导，创建 `hotfix -> main` MR、tag，并可同步 baseline |
| `button_branch_compare` | 比较分支 | `from_branch` 留空时使用当前版本最新 RC，`to_branch` 留空时使用 `baseline/{version}` |
| `button_sync_check` | 检查同步状态 | `fix_branch` 留空时使用当前版本最新 RC |
| `button_mr_list` | 查询 MR | 使用 `state` 和 `target_branch` 过滤 |
| `button_tag_list` | 查询 tag | 使用 `pattern` 过滤 |

## 4. 分支模型

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
stable/{major.minor}        从正式 tag 创建的稳定归档分支
```

## 5. 推荐验收顺序

### 5.1 查询分支

创建流水线后点击：

```text
button_branch_list
```

### 5.2 初始化测试版本

输入：

```text
version = 3.2.0.0
from_ref =
```

点击：

```text
button_version_init
```

创建：

```text
baseline/3.2.0.0
```

### 5.3 创建功能分支

输入：

```text
version = 3.2.0.0
task_id = TASK1234
description = user-login
```

点击：

```text
button_feature_create
```

创建：

```text
feature/3.2.0.0/TASK1234-user-login
```

### 5.4 创建提测分支

输入：

```text
version = 3.2.0.0
rc_number =
```

点击：

```text
button_fix_create
```

留空 `rc_number` 时自动查找已有 `fix/3.2.0.0-rcN` 并递增。

### 5.5 创建 bugfix 分支

输入：

```text
version = 3.2.0.0
rc_number = 1
bug_id = BUG001
description = crash
```

点击：

```text
button_bugfix_create
```

创建：

```text
bugfix/3.2.0.0/BUG001-crash
```

## 6. 同步与冲突

`button_bugfix_merge` 和 `button_hotfix_merge` 会尝试使用 GitLab cherry-pick API 同步 commit。

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
