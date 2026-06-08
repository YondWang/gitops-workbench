# 代码合入与版本管理规则说明

- [代码合入与版本管理规则说明](#代码合入与版本管理规则说明)
  - [1. 文档目的](#1-文档目的)
  - [2. 总体原则](#2-总体原则)
  - [3. 角色与权限](#3-角色与权限)
  - [4. 分支模型](#4-分支模型)
  - [5. 分支命名规则](#5-分支命名规则)
  - [6. Tag规则](#6-tag规则)
  - [7. 标准操作流程](#7-标准操作流程)
  - [8. 代码合入与同步规则](#8-代码合入与同步规则)
  - [9. 提测与发版规则](#9-提测与发版规则)
  - [10. 多仓库管理规则](#10-多仓库管理规则)
  - [11. Pipeline 使用策略](#11-pipeline-使用策略)
  - [12. 现有分支迁移方案](#12-现有分支迁移方案)
  - [13. 常见问题](#13-常见问题)
  - [14. 重点总结](#14-重点总结)

## 1. 文档目的

本文用于说明当前版本管理工具中的分支创建、代码合入、版本提测、版本发版和 Tag 管理规则，便于团队成员统一理解和执行。

当前版本管理模型只保留三类分支：

```text
release
feature/*
bugfix/<版本号>
```

旧模型中的 `baseline/*`、`develop`、`hotfix/*` 不再作为日常版本管理分支使用。

## 2. 总体原则

1. `release` 只有一个，是功能集成和下一版本提测分支。
2. `feature/*` 可以有多个，一个功能一个 Feature 分支。
3. Feature可以从 `release` 或当前固定版本的 `bugfix/<版本号>` 拉出。
4. Feature必须遵循“从哪拉出，就合入到哪”的原则。
5. Bugfix是版本级长期分支，格式为 `bugfix/<版本号>`。
6. Bugfix分支用于维护某个当前固定版本，不是个人问题级短分支。
7. Bugfix初始化时从 `release` 拉出，后续该版本的问题修复都提交到对应 `bugfix/<版本号>`。
8. `bugfix/<版本号>` 到达稳定节点后，在该分支上打 Tag 进行提测或发版。
9. Bugfix发版后，修复内容必须同步回 `release`，保证后续版本不丢修复。
10. 如果合入或同步存在冲突，不自动强行处理，由负责人在 GitLab MR 中手动解决。

关键约束：

- `release` 只有一个。
- Feature可以从 `release` 或 `bugfix/<版本号>` 拉出。
- Feature从哪拉出，就必须合入到哪。
- Bugfix是版本级长期分支：`bugfix/<版本号>`。
- Bugfix不是 `bugfix/<版本号>/<问题号>_<简述>` 这种个人问题级短分支。
- Bugfix稳定节点Tag打在 `bugfix/<版本号>`。
- Bugfix发版后必须同步回 `release`。
- 不再使用 `baseline/*`、`develop`、`hotfix/*` 作为日常版本管理分支。

## 3. 角色与权限

当前 Web 工具分为两个角色：

| 角色 | 主要权限 | 适用人员 |
|---|---|---|
| `user` | 查看分支、查看 Tag、创建 Feature 分支 | 普通开发人员 |
| `admin` | `user` 的全部权限，加上创建 Release 分支、创建 Bugfix版本分支、创建 Tag、仓库管理 | 版本管理员、发布负责人 |

建议：

- 普通开发只创建和维护自己负责的 `feature/*`。
- `bugfix/<版本号>` 由版本管理员或发布负责人创建和维护。
- Bugfix分支是版本级长期分支，不归某个个人独占。
- Web 工具只负责创建分支和创建 Tag，不负责合入 Feature 或同步 Bugfix；合入和同步通过 GitLab MR 完成。
- Web 工具创建的 `release`、`bugfix/<版本号>` 默认设置为 GitLab 保护分支。
- `release` 和 `bugfix/<版本号>` 禁止直接 Push，仅允许 Maintainer 通过 MR 合入。
- `feature/*` 不设置为保护分支，方便开发人员进行日常开发提交。
- 多仓库批量操作前，必须确认所有目标仓库处于同一版本节奏。

## 4. 分支模型

三类分支关系如下：

```text
release
   ├── feature/TASK-1001_xxx
   ├── feature/TASK-1002_yyy
   │
   └── bugfix/V1.0.0
           ├── 修复BUG-2001
           ├── 修复BUG-2002
           ├── Tag: bugfix-V1.0.0-20260605143000
           └── Tag: bugfix-V1.0.0-20260606101530
```

说明：

- `release` 是唯一功能集成分支。
- `feature/*` 从 `release` 或 `bugfix/<版本号>` 拉出，完成后合回来源分支。
- `bugfix/<版本号>` 从 `release` 或对应版本Tag初始化。
- 一个 `bugfix/<版本号>` 可以承载该版本的多个问题修复。
- Bugfix稳定节点通过 Tag 提测或发版。
- Bugfix发版后，应同步回 `release`。

## 5. 分支命名规则

### 5.1 Release 分支

Release 分支固定为：

```text
release
```

用途：

- Feature 分支常规来源。
- Feature 常规合入目标。
- 下一版本功能集成分支。
- 下一版本提测分支。
- 接收已发布 Bugfix 修复内容的同步目标。

规则：

- 仓库中只保留一个 `release`。
- `release` 不带版本号。
- `release` 禁止普通开发直接 push。
- `release` 只能通过 MR 或 Web 工具更新。

### 5.2 Feature 分支

Feature 表示新功能开发分支。

格式：

```text
feature/{TASKID}_{desc}
```

示例：

```text
feature/TASK-1234_user_login
feature/ADAS-2108_new_panel
feature/REQ-778_map_update
```

规则：

- Feature 可以从 `release` 或 `bugfix/<版本号>` 拉出。
- 一个功能对应一个 Feature 分支。
- `TASKID` 使用任务号或需求号，便于追踪需求来源。
- `desc` 使用简短英文或拼音描述，避免中文、空格和特殊符号。
- Feature 开发完成、自测通过、Code Review 通过后，必须合回来源分支。

### 5.3 Bugfix 分支

Bugfix 表示固定版本的长期问题修复分支。

格式：

```text
bugfix/{version}
```

示例：

```text
bugfix/V1.0.0
bugfix/V1.1.0
bugfix/V2.0.0
```

规则：

- Bugfix 分支可以有多个，但每个版本只维护一个 Bugfix 分支。
- `bugfix/{version}` 是版本级长期分支，不是个人问题级短分支。
- `{version}` 表示该 Bugfix 分支维护的固定版本。
- 同一版本的多个问题修复都提交到同一个 `bugfix/{version}`。
- Bugfix 分支只允许修复该版本问题，不允许新增功能。
- Bugfix 分支达到稳定节点后，在该分支上打 Tag 进行提测或发版。
- Bugfix 发版后，修复内容必须同步回 `release`。

## 6. Tag规则

Tag命名统一采用“来源 + 时间戳”，避免在名称中承载过多版本语义。

统一格式：

```text
<来源>-<yyyyMMddHHmmss>
```

说明：

- `<来源>` 表示Tag来自哪个分支。
- 时间戳使用创建Tag时的服务器时间，格式为 `yyyyMMddHHmmss`。
- 如果来源分支包含 `/`，Tag名称中使用 `-` 替换 `/`。
- Tag用途，例如提测、发版、回归节点，通过Tag message、发版说明或测试记录描述。

### 6.1 Release Tag

`release` 上创建Tag时，来源写为：

```text
release
```

示例：

```text
release-20260605143000
release-20260606101530
```

### 6.2 Bugfix Tag

`bugfix/<版本号>` 上创建Tag时，来源写为：

```text
bugfix-<版本号>
```

示例：

```text
bugfix-V1.0.0-20260605143000
bugfix-V1.0.0-20260606101530
bugfix-V1.1.0-20260607102045
```

说明：

- Bugfix提测Tag和Bugfix发版Tag使用同一套命名格式。
- 是否用于提测或发版，不通过Tag名称区分，通过Tag说明和发布记录区分。

## 7. 标准操作流程

### 7.1 初始化 Release 分支

1. 使用 `admin` 登录 Web 工具。
2. 选择稳定来源分支或 Tag。
3. 创建固定分支：

```text
release
```

4. 对 `release` 开启保护策略。

### 7.2 创建 Feature 分支

1. 使用 `user` 或 `admin` 登录 Web 工具。
2. 进入 `Feature` 页面。
3. 选择来源分支，通常是 `release`；如果是当前固定版本中的小需求，也可以选择对应 `bugfix/<版本号>`。
4. 填写任务号和简短描述。
5. 创建：

```text
feature/{TASKID}_{desc}
```

### 7.3 Feature 合入来源分支

1. Feature 开发完成并自测通过。
2. 提交 `feature/* -> 来源分支` 的 MR。
3. 完成 Code Review、CI 和静态检查。
4. 合入来源分支。
5. 如果来源是 `release`，合入后基于 `release` 进行集成测试或下一版本提测。
6. 如果来源是 `bugfix/<版本号>`，合入后基于该Bugfix分支的稳定节点打Tag提测。

### 7.4 创建或进入 Bugfix 版本分支

适用场景：

- 当前版本已经冻结，需要集中修复该版本问题。
- 已发布版本发现问题，需要继续维护该版本。

操作流程：

1. 确认当前固定版本，例如 `V1.0.0`。
2. 如果不存在版本分支，则从 `release` 当前稳定点或对应版本 Tag 创建：

```text
bugfix/V1.0.0
```

3. 如果已存在该版本分支，则继续使用已有 `bugfix/V1.0.0`。
4. 该版本的多个问题修复都提交到 `bugfix/V1.0.0`。

### 7.5 Bugfix 稳定节点提测

1. `bugfix/<版本号>` 上完成一批问题修复。
2. 完成自测、Code Review、CI 和静态检查。
3. 在 `bugfix/<版本号>` 上创建候选提测 Tag。

示例：

```text
bugfix/V1.0.0
    ↓
Tag: bugfix-V1.0.0-20260605143000
```

4. 测试团队基于该 Tag 或对应提交进行提测。

### 7.6 Bugfix 发版与同步 Release

1. `bugfix/<版本号>` 回归测试通过。
2. 在 `bugfix/<版本号>` 上创建修复发版 Tag。

示例：

```text
Tag: bugfix-V1.0.0-20260606101530
```

3. 对外交付该 Tag。
4. 创建 `bugfix/<版本号> -> release` 的 MR。
5. 将已发布修复同步回 `release`。
6. 如果存在冲突，由负责人在 GitLab MR 中手动解决。

## 8. 代码合入与同步规则

允许关系：

```text
feature/*        → 来源分支
bugfix/<版本号>   → release
```

说明：

- Feature 合回来源分支；来源可以是 `release` 或 `bugfix/<版本号>`。
- Bugfix 同步回 `release`，用于保证后续版本包含已发布修复。
- Bugfix 分支本身可长期存在，持续维护对应版本。

禁止关系：

```text
feature/*      → feature/*
bugfix/<版本号> → feature/*
bugfix/<版本号> → bugfix/<其他版本号>
```

约束：

- Feature允许合入它的来源分支。
- 如果Feature从 `bugfix/<版本号>` 拉出，可以合回同一个 `bugfix/<版本号>`。
- Feature禁止合入非来源分支。

## 9. 提测与发版规则

### 9.1 下一版本提测

下一版本功能集成提测基于：

```text
release
```

流程：

```text
feature/*合入release
      ↓
release达到可测状态
      ↓
基于release提测
```

### 9.2 当前固定版本修复提测

当前固定版本修复提测基于：

```text
bugfix/<版本号>
```

流程：

```text
bugfix/<版本号>完成一批修复
      ↓
创建候选Tag
      ↓
基于Tag提测
```

### 9.3 当前固定版本修复发版

```text
bugfix/<版本号>回归通过
      ↓
在bugfix/<版本号>创建发版Tag
      ↓
对外交付
      ↓
同步回release
```

## 10. 多仓库管理规则

当前工具支持管理多个 GitLab 仓库。

批量操作策略：

1. 先对所有目标仓库做预检查。
2. 确认所有目标仓库都存在 `release` 分支。
3. 创建 `bugfix/<版本号>` 前，确认所有仓库的版本来源一致。
4. 只要有任一仓库预检查失败，就不执行实际操作。
5. 所有仓库预检查通过后，再统一执行。

## 11. Pipeline 使用策略

当前仓库可保留历史 Pipeline 代码，但日常版本管理不依赖 Pipeline 自动执行。

推荐：

- 日常版本管理使用 Web 页面或 GitLab MR。
- Pipeline 可用于 CI、静态检查、构建和测试。
- Pipeline 不负责自动创建多套版本分支。

## 12. 现有分支迁移方案

当前项目正在使用 `fix` 和 `dev` 两个分支，其中 `fix` 相对更新。迁移到新规则时，不强制立即重命名现有 `fix` 分支。

迁移原则：

1. 将现有 `fix` 分支视为上一版本的 Bugfix 维护线。
2. `fix` 分支暂时保持原名称不变。
3. 所有上一版本或历史版本发现的问题，继续在 `fix` 分支上修复。
4. 从 `fix` 分支拉出新的 `release` 分支。
5. 后续新版本的常规功能开发从 `release` 拉出 `feature/*`。
6. 后续新版本需要固定版本修复时，从 `release` 拉出新的 `bugfix/<版本号>`。
7. 如果上一版本的小需求确实需要进入 `fix`，可以从 `fix` 拉出 `feature/*`，但必须合回 `fix`。

迁移流程：

```text
fix
    ↓
创建 release
    ↓
从 release 创建 feature/*
    ↓
从 release 创建新版本 bugfix/<版本号>
```

说明：

- `fix` 在迁移期等价于“上一版本 bugfix 分支”。
- `dev` 不再作为新规则下的功能开发来源。
- 新版本常规功能不再从 `dev` 或 `fix` 拉出，统一从 `release` 拉出。
- 如果上一版本的小需求确实需要进入 `fix`，可以从 `fix` 拉出 Feature，但必须合回 `fix`。
- 迁移期 `fix` 的行为等同于 `bugfix/<版本号>`，也遵循“从哪拉出，就合入到哪”的原则。

## 13. 常见问题

### 13.1 Bugfix为什么不再使用问题级分支？

因为当前需要的是版本级长期维护分支。`bugfix/V1.0.0` 代表 `V1.0.0` 这个固定版本的修复线，多个问题可以在同一个版本分支上连续修复、提测和打Tag。

### 13.2 Bugfix上的问题如何追踪？

通过 Commit、MR、Issue ID 和发版说明追踪。例如 Commit 或 MR 标题中包含 `BUG-884`，而不是再创建 `bugfix/V1.0.0/BUG-884_xxx` 分支。

### 13.3 Bugfix发版后为什么还要同步回release？

因为 `release` 是后续功能和版本演进的主线。如果 Bugfix 发版后不同步回 `release`，后续版本可能丢失已经发布的修复。

### 13.4 自动合入失败怎么办？

自动合入失败通常是代码冲突。

处理方式：

1. 打开页面日志中的 MR。
2. 在 GitLab 中查看冲突文件。
3. 由负责人手动解决冲突。
4. 冲突解决后继续合入。

## 14. 重点总结

团队需要统一记住以下几点：

1. 只保留三类分支：`release`、`feature/*`、`bugfix/<版本号>`。
2. `release` 只有一个，用于下一版本功能集成和提测。
3. `feature/*` 可以多个，可以从 `release` 或 `bugfix/<版本号>` 拉出，完成后必须合回来源分支。
4. `bugfix/<版本号>` 是版本级长期分支，不是个人问题级短分支。
5. 当前固定版本的问题修复都进入对应 `bugfix/<版本号>`。
6. `bugfix/<版本号>` 稳定后在自身分支打Tag提测或发版。
7. Bugfix发版后必须同步回 `release`。
8. 不再使用 `baseline/*`、`develop`、`hotfix/*` 作为日常版本管理分支。
