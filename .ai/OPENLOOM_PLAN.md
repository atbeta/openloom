# OpenLoom 渐进式落地计划

> 输入：`OPENLOOM_LEVELS.md`（7 级产品设计）+ `OPENLOOM_INTEGRATION.md`（3 ABC + 事件总线架构）+ opencode-deck 现有代码（~2245 行 Python + Svelte 前端）。
>
> 本文档回答三个问题：**opencode-deck 里哪些资产能搬、怎么搬**；**按什么顺序落地 L0-L7**；**每一步的验收标准是什么**。

---

## 1. 现状盘点：opencode-deck 资产复用地图

opencode-deck 是"双进程 server 模式"（即架构文档里的 B 解法 / L6 形态）。OpenLoom 反过来从 L0（单进程 CLI）起步，但 deck 的大部分代码是**直接可搬的砖**：

| opencode-deck 文件 | 行数 | 在 OpenLoom 中的归宿 | 复用方式 |
|---|---|---|---|
| `app/opencode_client.py` | 269 | `openloom/runtime/opencode.py` | **近乎原样搬**。这是与 OpenCode HTTP API 交互的唯一通道（session 创建 / prompt 发送 / 消息拉取 / 状态合并），L0 起就需要 |
| `app/task_spec.py` | 336 | 拆三块：spec 解析 → `levels/L1_config/`；`detect_progress`（STEP DONE / TASK COMPLETE 字符串匹配）→ **L0 的 StringChecker 本体**；prompt 构造 → `runtime/prompts.py` | **拆分后原样搬**。`detect_progress` 就是 LEVELS.md 说的"L0 = 字符串匹配"，已被验证 |
| `app/session_status.py` | 225 | `openloom/runtime/session_status.py` | 原样搬。busy/idle/retry 判定是 harness 巡检的核心依赖 |
| `app/harness.py` | 309 | `openloom/core/harness.py` 的**参考实现** | **重写**。状态机逻辑（pending→running→waiting→completed/failed/archived）和巡检节奏保留，但拆为：编排走 EventBus、判定走 Checker、通知走 Sink |
| `app/store.py` | 298 | `openloom/core/store.py` | **重写瘦身**。保留 SQLite + check log 设计，新增 `store_version` 单调递增列、写穿透接口 |
| `app/status_stream.py` | 107 | `levels/L6_server/` | 暂缓。OpenCode SSE 订阅属于 server 常驻模式 |
| `app/main.py` | 577 | 拆到 `server/routes/*` | L3 时只搬 tasks/events/actions 三组路由；dispatch / recent-workspaces / folder_picker 等观测台功能留给 L6 |
| `frontend/`（Svelte 5 + Vite） | — | `levels/L3_ui/` 起复用 | **保留技术栈**，但 L3 第一版按架构文档用单 HTML 文件（~150 行 JS）起步，Svelte 版作为 L6 完整观测台 |
| `app/config.py` / `.env.example` | 60 | `openloom/config.py` | 原样搬，环境变量前缀改 `OPENLOOM_` |

**结论**：约 1200 行（client + spec + status + config）可直接迁移，约 600 行（harness + store）需按新架构重写，577 行 main.py 大部分延后到 L6。**真正从零写的只有 core 的 events / 三个 ABC / registry，约 400 行。**

### 一个架构文档没说透的点：第 4 个角色 "Runtime"

3 个 ABC（Source / Checker / Sink）覆盖"任务从哪来、怎么判完成、通知谁"，但 **"任务怎么被执行"**（创建 OpenCode session、发 bootstrap prompt、idle 时 nudge）是 deck harness 里最重的逻辑。本计划将其明确为 `runtime/` 包——它不是 ABC（v1 之前只有 OpenCode 一种 runtime），harness 通过它驱动 Agent。等未来要接第二种 Agent（如 Claude Code / Codex CLI）时再抽 `Runtime` ABC——遵循"先用上、再抽象"原则。

```
openloom/
├── core/            ← ~600 行，永不变（events / harness / store / 3 ABC / registry）
├── runtime/         ← ~700 行，OpenCode 适配层（client / session_status / prompts）
├── levels/          ← L0-L7，平级，互不 import
└── server/          ← create_app(harness=) 工厂 + routes（L3/L6 共用）
```

依赖方向：`levels → core ← runtime`（core 不 import levels，也不 import runtime；harness 通过构造注入 runtime）。

---

## 2. 里程碑总览

按 INTEGRATION.md §9 的行动顺序展开，每个里程碑都是**可独立交付的真产品**：

| 里程碑 | 版本 | 对应 Level | 核心交付 | 新增代码（估） | 复用 |
|---|---|---|---|---|---|
| M0 | v0.1 | L0 | `pip install openloom` + `openloom watch`，事件总线 + 最小状态机 + 写死的字符串判定 | ~500 行 | client / spec / status 直搬 |
| M1 | v0.2 | L1 | `openloom init` 生成 YAML、SQLite store（含 store_version）、抽出 Checker ABC | +400 行 | deck task_spec / store |
| M2 | v0.3 | L2 | `openloom[openspec]`：OpenSpecSource + OpenSpecChecker、显式注册表、冷检测 | +400 行 | — |
| M3 | v0.4 | L3 | `openloom watch --ui`：Sink ABC + WebSink、FastAPI + SSE、单文件前端 | +450 行 | deck routes / 前端思路 |
| M4 | v0.5 | L4 | `openloom[validate]`：PreArchiveChecker 装饰器（pytest / mypy / git diff 闸门） | +500 行 | — |
| M5 | v0.6 | L5 | `openloom[github]`：GitHubSource | +600 行 | — |
| M6 | v0.7 | L6 | `openloom serve`：双进程模式 + Svelte 完整观测台（deck 功能回归） | +800 行 | deck main.py / frontend / status_stream |
| M7 | v0.8 | L7 | 插件 API 文档化 + `openloom-plugin-dingtalk` 示例（即 L8 测试） | +400 行 | — |

下面逐个展开。

---

## 3. M0（v0.1）— L0：最小可用的夜间任务 harness

**用户故事**：晚上 `openloom watch task.yaml`，OpenLoom 拉起 OpenCode session、发 bootstrap prompt、每 5 分钟巡检一次，看到 `TASK COMPLETE` 就标完成并打印到控制台；早上人工 review。

### 做什么

1. **`core/events.py`（~80 行，第一个写）**：6 种事件（TaskCreated / TaskStarted / TaskUpdated / TaskCompleted / TaskFailed / LogLine）+ in-process pub/sub。每个事件必带 `task_id` / `timestamp` / `store_version`（M0 时 store_version 恒为 0，字段先占位——schema 决定 L0-L7 全局，不能后补）。
2. **`core/harness.py`（~150 行）**：状态机（沿用 deck 的 pending→running→waiting→completed/failed/archived）+ 巡检循环。**只发事件不直调**：完成时 `emit(TaskCompleted)`，由订阅者处理。
3. **L0 判定逻辑写死、不抽 ABC**：直接内联 deck 的 `detect_progress`（STEP DONE / TASK COMPLETE / checkbox 计数）。故意的——ABC 等 M1/M2 有第二个实现时再抽。
4. **ConsoleSink 雏形**：订阅事件 → 打印。同样先不抽 ABC。
5. **CLI**：`openloom watch <spec.yaml>`，单进程跑到任务结束。
6. **搬运**：`opencode_client.py`、`session_status.py`、`task_spec.py` 的 prompt 构造与解析、`config.py`（前缀改名）。

### 不做什么

- 不写 SQLite（状态在内存，进程退出即丢——L0 用户接受）
- 不写 Web UI、不写注册表、不抽任何 ABC
- 主依赖只有 `httpx` + `pyyaml`（**FastAPI/uvicorn 移出主依赖**，这是与 deck 的关键差异）

### 验收

- [ ] `pip install openloom` 真的 < 1 分钟（依赖只有 httpx + pyyaml）
- [ ] 用 deck 的 `examples/harness-task.yaml` 跑通完整流程，5 分钟内可演示
- [ ] harness 内部零处直调 sink/checker——全部走 `events.emit`
- [ ] `grep -r "import fastapi" openloom/core openloom/levels/L0*` 为空

---

## 4. M1（v0.2）— L1：配置文件 + 持久化 + 第一次抽象

**用户故事**："5 个任务的配置飘在 5 个文件里"→ `openloom init` 生成标准 `openloom.yaml`；进程重启后任务历史还在。

### 做什么

1. **`core/store.py`（~100 行）**：SQLite，schema 参考 deck 的 `store.py` 但瘦身（tasks + check_log 两张表），新增 `store_version` 单调递增（每次写 +1），harness 改为**写穿透**（先写 store、再 emit 事件、事件携带新 version）。
2. **抽 `Checker` ABC**（第一次抽象）：把 M0 写死的字符串判定重构为 `StringChecker(Checker)`。接口由 M0 的真实调用倒推，不凭空设计。
3. **`levels/L1_config/`**：`openloom init` 生成 yaml（含 workspace / agent / check_interval / steps / acceptance，格式兼容 deck spec）；`openloom watch` 无参数时自动读 `openloom.yaml`。
4. **`openloom status` / `openloom log <task-id>`**：读 store 的 CLI 查询命令（为 L3 的 GET /api/tasks 预演数据形态）。

### 验收

- [ ] kill 掉 watch 进程再 `openloom status`，历史任务与 check log 完整
- [ ] `StringChecker` 是 `Checker` ABC 的唯一实现，但接口不含"用不上的方法"
- [ ] 所有状态变更路径都满足"写 store → emit 事件"的顺序，无例外

---

## 5. M2（v0.3）— L2：OpenSpec 集成（性价比最高的跨越）

**用户故事**："Agent 报完成但其实没做完"→ 不再信 Agent 的话术，改读 OpenSpec `tasks.md` 的 checkbox 状态。误判率 35% → 8%。

### 做什么

1. **抽 `TaskSource` ABC** + `ManualSource`（M0/M1 的 yaml 入口重构进去）。
2. **`core/registry.py`（~60 行）**：显式注册表，`@register_source("openspec")` / `@register_checker("openspec")` / `@register_sink(...)`。不用 entry_points 自动发现。
3. **`levels/L2_openspec/`**：`OpenSpecSource`（从 spec 目录读任务）+ `OpenSpecChecker`（解析 `tasks.md` checkbox，sub-task 粒度判定）。
4. **extras + 冷检测**：`pip install openloom[openspec]`；`is_openspec_available()` 每次现场 try import，不缓存、`__init__.py` 零 try-import。
5. **Checker ABC 第二次验证**：如果 OpenSpecChecker 装不进 M1 抽的接口，**改 ABC 而不是绕过它**——此时改还来得及。

### 验收

- [ ] 未装 extras 时 `openloom watch --checker openspec` 给出清晰报错（提示安装命令），而非 ImportError 栈
- [ ] 主包安装目录里 `grep -r openspec` 仅命中 levels/L2 子包与冷检测函数
- [ ] 用 ≥20 个真实任务对比 StringChecker vs OpenSpecChecker 的误判率（LEVELS.md 承诺 <10%，需实测数据支撑）

---

## 6. M3（v0.4）— L3：Web UI（`--ui`，同进程）

**用户故事**："SSH 上去看日志太累"→ `openloom watch --ui` 在 55413 端口起一个本地面板，浏览器实时看任务进度。

### 做什么

1. **抽 `Sink` ABC**（第二次抽象）：ConsoleSink 重构进去 + 新写 `WebSink`（订阅 EventBus → 转 SSE）。
2. **`server/app.py`**：`create_app(harness=)` 工厂函数——这个签名是 L3（同进程）和 L6（双进程）零改动复用的关键。
3. **`server/routes/`**：照架构文档 §7 落地：
   - `tasks.py`：GET /api/tasks（全量，**永远读 store**）、GET /api/tasks/{id}
   - `events.py`：GET /api/events（SSE，先发 snapshot 事件携带当前 store_version，再转发增量）
   - `actions.py`：POST archive/rollback——**只 emit UserCommandEvent 立即返回**，由 harness 串行消费 + 幂等检查（CLI 和 Web 同一条命令路径，解决并发打架）
4. **前端第一版 = 单 HTML 文件（~150 行 JS）**：启动拉全量 → SSE 订阅增量 → 按 task_id patch DOM → 收到事件时比较 store_version，落后就重拉全量 → 断线重连重拉全量（不做 Last-Event-ID）。deck 的 Svelte 观测台留到 M6。
5. **extras**：`openloom[ui]` = fastapi + uvicorn。

### 验收

- [ ] 不带 `--ui` 时进程零 Web 依赖（CI 加 import-linter 或启动断言守护）
- [ ] "先跑一夜再开浏览器"场景：打开即见完整历史（全量）+ 后续实时更新（增量）
- [ ] 同时在 CLI 和 Web 点归档同一任务，结果幂等、两端状态一致
- [ ] 手动 kill SSE 连接，前端自动重连并重拉全量，数据不丢不重

---

## 7. M4（v0.5）— L4：预归档校验（装饰器模式验证）

**用户故事**："Agent 改完文件没跑测试就报完成"→ 归档前强制 `pytest -q` / `git diff --stat` 非空 / `mypy`，任一失败 → 不归档、标 failed。误判率 8% → <1%。

### 做什么

1. **`PreArchiveChecker`：包装不继承**——接受另一个 Checker 实例，内层判定通过后再跑校验命令。`checker.with_pre_archive(...)` 风格，~30 行组合逻辑。
2. 校验命令可配置（`openloom.yaml` 的 `validate:` 块），默认 pytest + git diff。
3. 校验失败 → emit TaskFailed（detail 带命令输出尾部）→ 可选回滚提示。
4. **extras**：`openloom[validate]`。
5. **装饰器模式验证点**：确认 `StringChecker`、`OpenSpecChecker` 都能被 `with_pre_archive` 包装且可叠加（为未来 with_retry / with_audit 留路）。

### 验收

- [ ] 构造一个"Agent 报 TASK COMPLETE 但测试红"的任务，确认被拦住、状态为 failed、check log 含 pytest 输出
- [ ] PreArchiveChecker 落地过程中 **0 行修改** OpenSpecChecker / StringChecker 源码

---

## 8. M5（v0.6）— L5：GitHub 源

1. `levels/L5_github/`：`GitHubSource`（Issue label 触发 → 生成 TaskSpec）+ 完成后回写 PR/comment（一个 `GitHubSink`，顺便第三次验证 Sink ABC）。
2. `--source github` CLI 参数走注册表查找。
3. **extras**：`openloom[github]`（PyGithub）。

验收：`GitHubSource` + `OpenSpecChecker` + `WebSink` 三件套自由组合跑通一个真实 Issue → 任务 → PR 流程；新增 source **0 行改 core**。

---

## 9. M6（v0.7）— L6：服务器模式（opencode-deck 全功能回归）

**这一步把 deck 的观测台正式吸收进 OpenLoom**：

1. **`openloom serve`**：双进程模式——server 独立常驻、`Harness.load_from_store()` 启动、CLI 与 Web 都通过 HTTP/store 交互。`create_app(harness=)` 不改一行。
2. **搬回 deck 的观测台能力**（main.py 剩余部分 + status_stream.py + frontend）：
   - Session 监控（按项目分组、busy/idle/retry）
   - Dispatch（一次性派发）
   - Session 抽屉（消息 / diff / 元数据）
   - Recent Workspaces、workspace 白名单（`OPENLOOM_ALLOWED_ROOTS`）
3. **前端升级为 Svelte 版**：deck 的 `App.svelte` 迁移过来，对接 M3 定义的 /api/tasks + SSE 协议；按 `registry.list_all()` 渲染能力按钮（未装的 level 显示 "需 pip install openloom[X]"，先 render 再 disabled）。
4. **extras**：`openloom[server]`。

验收：deck README 里的每个功能在 `openloom serve` 下都有对等物；L3 单进程模式行为不受影响。**到这一步，opencode-deck 仓库可以标记为 superseded。**

---

## 10. M7（v0.8）— L7：插件 API + L8 测试

1. 把 registry + 3 ABC 的公开接口文档化、语义化版本承诺。
2. **写一个真实的外部插件 `openloom-plugin-dingtalk`**（独立 repo/包）：实现 `Sink` ABC + `@register_sink("dingtalk")`。
3. **这就是 L8 测试本体**：如果钉钉通知做不到"1 个新文件、0 行改老代码"，说明 ABC 抽错了——回到对应里程碑修正后才能发 v1.0。

---

## 11. 贯穿性约束（每个 PR 都要守）

来自两份架构文档，列为 CI 可检查项：

1. `core/` 不 import 任何 level、不 import runtime 之外的实现（import-linter 规则）
2. level 之间不互相 import（import-linter 规则）
3. `core/` 总行数 ≤ 600（CI 里 `wc -l` 守门，超了说明职责漏到 core 了）
4. 任何 `__init__.py` 不出现 `try: import`（grep 守门）
5. harness 不直调 checker/sink 方法——状态变更只有"写 store → emit 事件"一条路
6. 数据单一来源：API 路由只读 store；EventBus 只推通知不带全量数据
7. 升级只加 level 不删老 level；废弃走 deprecated + 6 个月 + 迁移路径

## 12. 主要风险与对策

| 风险 | 对策 |
|---|---|
| OpenCode API 变动（deck client 里大量 fallback 逻辑暗示 API 不稳定） | runtime/ 层隔离所有 API 差异；client 迁移时保留现有 fallback；M0 起为 client 写录制回放式测试 |
| 权限卡死（deck README 重点警告：`ask` 权限会让无人值守卡 waiting） | M0 文档显著位置继承 deck 的权限配置指南；`openloom watch` 启动时预检 agent 权限配置并警告；自动代批留作 L7 之后的独立提案 |
| 事件 schema 一旦定错，L3 UI 怎么写都错 | M0 只做 schema + L0 验证，**不写任何 UI**；M3 前允许 breaking change，M3 后 schema 冻结 |
| "600 行 core"在重写 deck harness 时膨胀 | 巡检 nudge / prompt 构造留在 runtime/，core/harness 只管状态机与事件；CI 行数守门 |
| 误判率承诺（<10%、<1%）无实测支撑 | M2、M4 各安排一次 ≥20 真实任务的对比测量，数据写进 README |

## 13. 立即行动（本周）

1. 初始化仓库骨架：`pyproject.toml`（主依赖仅 httpx + pyyaml + extras 占位）、`openloom/core/`、`openloom/runtime/`、`openloom/levels/L0_manual/`
2. 写 `core/events.py`（6 事件 + EventBus，~80 行）——**第一个文件，schema 即宪法**
3. 从 deck 搬 `opencode_client.py` / `session_status.py` / `config.py` 进 `runtime/`
4. 写 `core/harness.py` 最小状态机（参考 deck harness.py 的状态流转，但只发事件）
5. 内联字符串判定 + ConsoleSink（不抽 ABC），跑通 `openloom watch examples/task.yaml` 真实流程

M0 跑通即发 v0.1 tag——之后每个里程碑都是一个可发布、可停留的真产品。
