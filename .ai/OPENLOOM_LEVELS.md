# OpenLoom Levels：渐进式 7 级设计

> 一句话：**每一级都是一个"真产品"，不是上一级的脚手架。** 用户在任何一级停下来都不亏。

---

## 0. 重新定义"渐进式"

很多人把"渐进式"理解成"先简陋后完整"（MVP 思维）。OpenLoom 不这样。

**真正的渐进式 = 每一级都是真产品**

| 特征 | MVP 思维（错） | 渐进式（对） |
|---|---|---|
| L0 定位 | 占位符 | **一个真产品**，80% 用户永远停在这里 |
| 升级关系 | L0 是 L1 的"凑合版" | L0 独立完整，L1 是**额外能力** |
| 破坏性 | 升级时改 API | 升级只**加接口**，不改老接口 |
| 退出成本 | 必须走完全程 | 可在任意级停下 |

**反例**：`pip install openloom[full]` 这种"全家桶"做法 = 一次性给完所有能力 = 不是渐进式。
**正例**：`pip install openloom` → L0，**用户主动** `pip install openloom[openspec]` → L2。

---

## 1. 7 级全表

| Level | 名称 | 触发命令 | 解决的"夜间任务"痛点 | 代码增量 | 误判率 | 用户停留率（估计） |
|---|---|---|---|---|---|---|
| **L0** | 零配置手动 | `pip install openloom` | "跑一半我得去睡觉" | **200 行** | 30-40% | **80%** |
| **L1** | 配置文件 | `openloom init` 生成 yaml | "5 个任务配置飘到 5 个文件" | +300 行 | 30-40% | 60% |
| **L2** | OpenSpec 源 | `pip install openloom[openspec]` | "Agent 报'完成'但其实没" | +400 行 | **5-10%** | 15% |
| **L3** | Web UI | `openloom watch --ui` | "SSH 上去看日志太累" | +300 行 | 5-10% | 10%（L2 用户的 70%） |
| **L4** | 预归档校验 | `openloom validate` | "Agent 改完文件没跑测试" | +500 行 | **<1%** | 4% |
| **L5** | 多源 | `--source github` | "Issue/PR 是任务入口" | +600 行 | <1% | 1% |
| **L6** | 服务器模式 | `openloom serve` | "团队要共享任务面板" | +800 行 | <1% | <1% |
| **L7** | 插件 API | `pip install openloom-plugin-*` | "我们要接内部 Jira" | +400 行 | 不适用 | 不适用 |

**核心数据**：
- L0 误判率 30-40% → 听起来很糟，但比"完全不跑"好无限倍。L0 用户的画像是**个人开发者 + 不在乎 token + 早上花 10 分钟人工 review**。
- L0→L2 误判率从 35% → 8%，是**最有性价比的一次跨越**（3 倍代码增量换 4 倍质量）。
- L2→L4 误判率从 8% → <1%，是给"质量敏感型"团队的最后一道门。

---

## 2. 三个关键跨越的"为什么"

### 跨越 1：L0 → L2（"完成判定"的本质升级）

**L0 的 200 行**做了三件事：
1. 拉起 OpenCode 子进程
2. 定时 grep `TODO/DONE/ERROR` 字符串
3. 字符串匹配成功就标完成

**L0 = session 巡检 + 字符串匹配**。是的，就是这样。**这不是缺陷，是设计目标**。

为什么？因为：
- L0 的用户群体 = 个人 + 不在乎 token，**质量"够用即可"**。
- L0 的成功标准 = 用户**5 秒装上、5 分钟用上**。
- 任何"智能判定"（LLM 自评、文件校验、协议握手）都违反 5 秒装上的要求。

**L2 才是"严肃完成判定"的入口**：
- 不再 grep 字符串，改为读 OpenSpec 的 `tasks.md` checkbox
- 判定粒度从"任务"细化到"任务里的每个 sub-task"
- 误判率从 35% 降到 8%
- **触发条件** = 用户主动 `pip install openloom[openspec]`，**绝不**默认安装

### 跨越 2：L2 → L4（"Agent 自评 vs 系统校验"的根本分叉）

L2/L3 都还是"让 Agent 自己说完成"。L4 开始走另一条路：

> **不要相信 Agent 的话，要相信文件系统的状态。**

具体做法：归档前**必须**执行：
```bash
pytest -q          # 测试必须全过
git diff --stat    # 必须有 diff
mypy src/          # 类型必须过
```

任一失败 → 不归档、标 failed、回滚到上个工作树。

**L4 之后，误判率 < 1%**。这是给"金融/医疗/合规"场景的。

### 跨越 3：L0 vs L3（"CLI 用户不被迫装 Web UI"）

这是 OpenDeck 跌过的坑：Web UI 和 CLI 绑成双进程，新用户要装两次。

OpenLoom 的解法：
- L0 = 纯 CLI，**0 个 Web 依赖**
- L3 = **可选**，`openloom watch --ui` 才启 FastAPI
- `[ui]` extra 控制 FastAPI/uvicorn 依赖，CI/服务器用户**永远装不到**

效果：CLI 用户的 `pip install openloom` 还是 35 秒，加上 FastAPI 变 17 分钟。
**35 秒 vs 17 分钟 = 30 倍安装差距 = 100 倍采用率差距。**

---

## 3. 不做什么（6 个明确反模式）

| 反模式 | 为什么不做 |
|---|---|
| ❌ 默认装 Web UI | 90% 用户用不到，污染 CLI 体验 |
| ❌ 强制要求 OpenSpec | 5% 团队写 spec，95% 用 Markdown/TODO |
| ❌ 内置 LLM 做"完成判定" | 调用一次就破坏 L0 的"无外部依赖"承诺 |
| ❌ 数据库必装 Postgres | SQLite 撑到 10K 任务无压力 |
| ❌ 实时 WebSocket | SSE 够了，复杂度低 5 倍 |
| ❌ 提供 SaaS 平台 | v1 之前只做 OSS，企业版是 6 个月后的事 |

---

## 4. 代码侧的统一抽象（怎么用 600 行核心撑 7 级）

**核心思想**：7 级**不是 7 个产品**，是**同一个核心库的 7 个可选能力**。

```
openloom/core/                 ← 600 行，永不变
├── harness.py                 ← 状态机（source-agnostic）
├── events.py                  ← EventBus（in-process pub/sub）
├── checker.py                 ← CompletionChecker ABC
├── sources/base.py            ← TaskSource ABC
└── store.py                   ← SQLite

openloom/levels/               ← 7 个独立 sub-package
├── L0_manual/                 ← 默认装
├── L1_config/                 ← 默认装
├── L2_openspec/               ← openloom[openspec]
├── L3_ui/                     ← openloom[ui]
├── L4_validate/               ← openloom[validate]
├── L5_sources/                ← openloom[sources-github]
├── L6_server/                 ← openloom[server]
└── L7_plugins/                ← plugin API
```

**关键约束**：
1. `core/` **不 import 任何 level 的代码**（单向依赖）
2. Level 之间**不互相 import**（平级）
3. 升级 = **加 level**，不删老 level
4. 废弃某个 level = 标 `deprecated` 标签、保留至少 6 个月、给迁移路径

**CLI 入口统一**（L0-L6 都用同一个 `openloom` 命令）：
```bash
openloom watch         # L0-L2 默认行为
openloom watch --ui    # L3 触发
openloom watch --source github  # L5 触发
openloom serve         # L6 触发
```

**Web UI 也统一**（L3-L6 共用同一份前端代码）：
- 启动时 `harness.list_levels()` 看装了哪些 level
- 对未安装的 level 按钮显示"需 `pip install openloom[X]`"
- **前端绝不依赖后端能力**，永远先 render 再 disabled

---

## 5. 决策权交给用户

> "用户**主动**升级" vs "系统**强制**升级"

OpenLoom 的核心承诺：
- 装 L0 的用户**永远**不会被强制装 L2 的依赖
- 装 L2 的用户**永远**不会被强制装 L3 的 Web 框架
- 装 L3 的用户**永远**不会被强制装 L4 的校验器
- 装 L4 的用户**永远**不会被强制装 L5 的 GitHub 集成

**每一次跨越都是用户主动选择，每一次选择都是可逆的**（`pip uninstall openloom[openspec]` 退回 L0）。

这是 OpenLoom 和 OpenDeck、和几乎所有"全家桶" AI 工具的根本区别。

---

## 6. 验收清单（写代码前自检）

- [ ] L0 的 `pip install openloom` **真的** < 1 分钟
- [ ] L0 的 200 行 Python **真的**跑得起来、能在 5 分钟内演示完整流程
- [ ] L2 的 OpenSpec 集成**真的**能做到误判率 < 10%（跑 100 个真实任务验证）
- [ ] L3 的 Web UI 关闭后 CLI **真的**功能不变
- [ ] L4 的预归档校验**真的**能拦住"Agent 报完成但测试失败"的情况
- [ ] L5-L7 的代码**真的**是 0 行（v0.1 不实现）
- [ ] 全程**没有** SaaS 依赖、**没有** 强制注册、**没有** 远程 API

---

**TL;DR for the impatient**：
- 7 级、每级独立、用户主动升级
- L0 故意简陋、故意字符串匹配、故意 200 行
- L0→L2 误判率 35%→8%，是性价比最高的跨越
- L2→L4 误判率 8%→<1%，给"质量敏感"团队
- 35 秒安装 vs 17 分钟安装 = 100 倍采用率差距
- `core/` 600 行不变，level 之间互不依赖
