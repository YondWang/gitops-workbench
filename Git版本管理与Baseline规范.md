# Git版本管理规范（三分支模型）

## 1. 文档目的

本文档用于规范项目研发过程中的：

* Git分支管理
* 功能开发
* 版本级Bugfix维护
* 提测流程
* 发版流程
* 命名规范
* Merge Request流程

确保：

* 分支模型简单清晰
* 功能开发入口统一
* 固定版本修复可持续维护
* 提测和发版节点可追溯
* 多团队协同稳定

---

# 2. 总体原则

新版版本管理只保留三类分支：

```text
release
feature/*
bugfix/<版本号>
```

核心原则：

```text
release唯一
feature从release或bugfix拉出
feature合回来源分支
bugfix是版本级长期分支
bugfix稳定节点打Tag提测/发版
bugfix发版后同步回release
```

说明：

* `release` 只有一个，用于下一版本功能集成和提测
* `feature/*` 可以有多个，一个功能一个Feature分支
* `feature/*` 可以从 `release` 或当前固定版本的 `bugfix/<版本号>` 拉出
* `feature/*` 必须遵循“从哪拉出，就合入到哪”的原则
* `bugfix/<版本号>` 是固定版本的长期维护分支
* `bugfix/<版本号>` 不是个人问题级短分支
* Bugfix不是 `bugfix/<版本号>/<问题号>_<简述>` 这种个人问题级短分支
* 同一固定版本的多个问题都在对应 `bugfix/<版本号>` 中修复
* Bugfix稳定节点Tag打在 `bugfix/<版本号>`
* `bugfix/<版本号>` 达到稳定节点后，在该分支上打Tag进行提测或发版
* Bugfix发版后，修复内容必须同步回 `release`

---

# 3. Git分支模型

## 3.1 分支类型

| 分支 | 数量 | 来源 | 合入/同步目标 | 作用 |
| --- | ---: | --- | --- | --- |
| `release` | 只有一个 | 项目初始化或稳定代码线 | 不适用 | 下一版本功能集成和提测分支 |
| `feature/*` | 可以多个 | `release`、`bugfix/<版本号>` 或迁移期 `fix` | 来源分支 | 功能开发分支 |
| `bugfix/<版本号>` | 可以多个 | `release` 或版本Tag | `release` | 固定版本长期修复分支 |

## 3.2 分支关系

```text
release
   ├── feature/<来源>_<功能描述>
   │       ↓
   │   合入release
   │
   └── bugfix/<版本号>
           ├── 修复问题A
           ├── 修复问题B
           ├── Tag: bugfix-<版本号>-<yyyyMMddHHmmss>
           └── Tag: bugfix-<版本号>-<yyyyMMddHHmmss>
                   ↓
              同步回release
```

说明：

* `release` 是唯一功能集成主线
* `feature/*` 可以从 `release` 或 `bugfix/<版本号>` 拉出，并合回来源分支
* `bugfix/<版本号>` 是版本级长期分支
* `bugfix/<版本号>` 可以承载该版本的多个问题修复
* `bugfix/<版本号>` 稳定节点通过Tag提测或发版
* Bugfix发版后必须同步回 `release`

---

# 4. 分支命名规范

## 4.1 Release分支

固定名称：

```text
release
```

用途：

* Feature分支常规来源
* Feature常规合入目标
* 下一版本功能集成分支
* 下一版本提测分支
* Bugfix发版后修复内容的同步目标

规范：

* 仓库中只允许存在一个 `release`
* `release` 不带版本号
* 禁止普通开发直接push到 `release`
* `release` 必须开启分支保护
* Web工具创建 `release` 后默认设置为保护分支：禁止直接Push，仅允许Maintainer通过MR合入

---

## 4.2 Feature分支

Feature分支用于新功能开发。

格式：

```text
feature/<来源>_<功能描述>
feature/<来源>_<任务号>_<功能描述>
```

示例：

```text
feature/release_path_optimize
feature/release_ADAS-2108_new_panel
feature/v1.0.0_fusion_refactor
```

规范：

* Feature可以有多个
* 一个功能对应一个Feature分支
* 可以从 `release` 或当前固定版本的 `bugfix/<版本号>` 拉出
* 开发完成后必须合回来源分支
* 分支名称中必须简洁体现来源分支
* 任务号或需求号可选，填写后便于追踪需求来源
* 使用英文小写、数字、连字符和下划线
* 禁止中文、空格和特殊符号
* Web工具创建 `feature/*` 后不设置为保护分支，方便开发人员进行日常开发提交

---

## 4.3 Bugfix分支

Bugfix分支用于固定版本的长期问题修复。

格式：

```text
bugfix/<版本号>
```

示例：

```text
bugfix/V1.0.0
bugfix/V1.1.0
bugfix/V2.0.0
```

规范：

* Bugfix可以有多个，但每个版本只维护一个Bugfix分支
* Bugfix是版本级长期分支，不是个人问题级短分支
* Bugfix不是 `bugfix/<版本号>/<问题号>_<简述>` 这种个人问题级短分支
* `<版本号>` 表示该Bugfix分支维护的固定版本
* 同一版本的多个问题修复都提交到同一个 `bugfix/<版本号>`
* Bugfix只允许修复该版本问题
* Bugfix禁止新增功能
* Bugfix稳定节点Tag打在 `bugfix/<版本号>`
* Bugfix达到稳定节点后，在该分支打Tag进行提测或发版
* Bugfix发版后，修复内容必须同步回 `release`
* Web工具创建 `bugfix/<版本号>` 后默认设置为保护分支：禁止直接Push，仅允许Maintainer通过MR合入

---

# 5. Tag命名规范

Tag用于标识稳定节点、提测节点和发版节点。

Tag名称不承载复杂版本语义，统一采用“来源 + 时间戳”。

统一格式：

```text
<来源>-<yyyyMMddHHmmss>
```

说明：

* `<来源>` 表示Tag来自哪个分支
* 时间戳使用创建Tag时的服务器时间，格式为 `yyyyMMddHHmmss`
* 如果来源分支包含 `/`，Tag名称中使用 `-` 替换 `/`
* 提测、发版、回归等用途通过Tag说明、测试记录或发版记录描述

---

## 5.1 Release Tag

`release` 上创建Tag时，来源写为：

```text
release或bugfix/<版本号>
```

示例：

```text
release-20260605143000
release-20260606101530
```

---

## 5.2 Bugfix Tag

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

* Bugfix提测Tag和Bugfix发版Tag使用同一套命名格式
* 是否用于提测或发版，不通过Tag名称区分
* 提测或发版用途应写入Tag说明、测试记录或发版记录

---

# 6. 开发流程规范

## 6.1 功能开发流程

开发流程：

```text
release
   ↓
feature分支开发
   ↓
自测通过
   ↓
Code Review
   ↓
CI / 静态检查通过
   ↓
合入来源分支
   ↓
基于来源分支进行提测或回归
```

要求：

* Feature分支禁止从其他Feature分支拉出
* Feature分支必须合回来源分支
* 如果Feature从 `bugfix/<版本号>` 拉出，可以合回同一个 `bugfix/<版本号>`
* Feature分支禁止合入非来源分支
* Feature必须通过Code Review
* Feature必须通过CI和静态检查
* Feature来源为 `release` 时，合入后由 `release` 统一集成和提测
* Feature来源为 `bugfix/<版本号>` 时，合入后基于该Bugfix分支稳定节点打Tag提测

---

## 6.2 Bugfix版本分支创建流程

适用场景：

* 当前版本已经冻结，需要集中修复该版本问题
* 已发布版本发现问题，需要持续维护该版本

流程：

```text
release或版本Tag
   ↓
bugfix/<版本号>
```

要求：

* 每个固定版本只维护一个 `bugfix/<版本号>`
* 如果该版本Bugfix分支不存在，应从 `release` 稳定点或对应版本Tag创建
* 如果该版本Bugfix分支已存在，应继续使用已有分支
* 该版本的多个问题修复都进入同一个Bugfix分支

---

## 6.3 Bugfix修复流程

修复流程：

```text
bugfix/<版本号>
   ↓
修复一个或多个当前版本问题
   ↓
自测通过
   ↓
Code Review
   ↓
CI / 静态检查通过
   ↓
在bugfix/<版本号>打候选Tag
   ↓
基于Tag提测
```

要求：

* Bugfix只允许修复该固定版本问题
* Bugfix禁止新增功能
* Bugfix禁止承接无关重构
* 问题号应体现在Issue、MR或Commit信息中
* 不再通过 `bugfix/<版本号>/<问题号>_<简述>` 创建个人短分支

---

# 7. 提测与发版规范

## 7.1 下一版本提测

下一版本功能提测基于：

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

---

## 7.2 当前固定版本Bugfix提测

当前固定版本修复提测基于：

```text
bugfix/<版本号>
```

流程：

```text
bugfix/<版本号>完成一批修复
    ↓
创建Tag，例如 bugfix-V1.0.0-20260605143000
    ↓
基于Tag提测
```

---

## 7.3 当前固定版本Bugfix发版

Bugfix发版流程：

```text
bugfix/<版本号>回归通过
    ↓
在bugfix/<版本号>创建发版Tag
    ↓
对外交付
    ↓
同步回release
```

说明：

* Bugfix发版Tag打在 `bugfix/<版本号>` 分支上
* Bugfix发版后必须同步回 `release`
* 同步回 `release` 用于保证后续版本包含已发布修复

---

# 8. 代码合入与同步规范

## 8.1 允许关系

```text
feature/*       → 来源分支
bugfix/<版本号>  → release
```

说明：

* Feature合回来源分支；来源可以是 `release` 或 `bugfix/<版本号>`
* Bugfix同步回 `release` 是修复回流
* Bugfix分支自身可以长期存在，持续维护对应版本

---

## 8.2 禁止关系

```text
feature/*       → feature/*
bugfix/<版本号>  → feature/*
bugfix/<版本号>  → bugfix/<其他版本号>
```

约束：

* Feature允许合入它的来源分支
* 如果Feature从 `bugfix/<版本号>` 拉出，可以合回同一个 `bugfix/<版本号>`
* Feature禁止合入非来源分支

---

# 9. 现有分支迁移方案

当前项目正在使用 `fix` 和 `dev` 两个分支，其中 `fix` 相对更新。迁移到新规则时，不强制立即重命名现有 `fix` 分支。

迁移原则：

* 将现有 `fix` 分支视为上一版本的Bugfix维护线
* `fix` 分支暂时保持原名称不变
* 所有上一版本或历史版本发现的问题，继续在 `fix` 分支上修复
* 从 `fix` 分支拉出新的 `release` 分支
* 后续新版本的常规功能开发从 `release` 拉出 `feature/*`
* 后续新版本需要固定版本修复时，从 `release` 拉出新的 `bugfix/<版本号>`
* 如果上一版本的小需求确实需要进入 `fix`，可以从 `fix` 拉出 `feature/*`，但必须合回 `fix`

迁移流程：

```text
fix
    ↓
创建release
    ↓
从release创建feature/*
    ↓
从release创建新版本bugfix/<版本号>
```

说明：

* `fix` 在迁移期等价于“上一版本Bugfix分支”
* `dev` 不再作为新规则下的功能开发来源
* 新版本常规功能不再从 `dev` 或 `fix` 拉出，统一从 `release` 拉出
* 如果上一版本的小需求确实需要进入 `fix`，可以从 `fix` 拉出Feature，但必须合回 `fix`
* 迁移期 `fix` 的行为等同于 `bugfix/<版本号>`，也遵循“从哪拉出，就合入到哪”的原则

---

# 10. 禁止事项

禁止：

* 直接push到 `release`
* 未Review直接Merge
* Feature从Feature拉出
* Feature合入Bugfix
* Bugfix新增功能
* 创建 `bugfix/<版本号>/<问题号>_<简述>` 作为个人短分支
* Bugfix发版后不同步回 `release`
* 超大MR
* 强制push公共分支
* 提交无意义commit

禁止示例：

```text
fix bug
update code
test
123
```

---

# 11. 版本演进示例

## 阶段1：功能开发

```text
release
    ↓
feature/release_ADAS-1024_path_optimize
    ↓
合入release
```

## 阶段2：下一版本提测

```text
release
    ↓
提交测试团队提测
```

## 阶段3：固定版本Bugfix维护

```text
release或Tag: V1.0.0
    ↓
bugfix/V1.0.0
    ↓
修复BUG-884、BUG-992
    ↓
Tag: bugfix-V1.0.0-20260605143000
```

## 阶段4：固定版本Bugfix发版

```text
bugfix/V1.0.0回归通过
    ↓
Tag: bugfix-V1.0.0-20260606101530
    ↓
同步回release
```

---

# 12. 推荐Git保护策略

建议开启：

* Protected Branch：保护 `release`
* Protected Branch：保护长期 `bugfix/<版本号>`
* Mandatory Review
* CI Required
* 禁止Force Push
* 禁止删除Tag
* Merge后自动删除Feature分支
* Bugfix版本分支长期保留，不自动删除

---

# 13. 总结

核心原则：

```text
release唯一
feature短期开发
bugfix版本级长期维护
bugfix稳定节点Tag提测/发版
bugfix发版后同步回release
```

最终目标：

* 降低分支维护复杂度
* 避免个人Bugfix分支泛滥
* 固定版本修复集中管理
* 保证提测和发版节点可追溯
* 保证后续版本不丢失已发布修复
