# OpenLoom 集成架构：演进 + 复用 + UI 匹配

> 一句话：**底座 = 3 ABC + 1 编排器 + 1 事件总线（600 行，永不变）**
> **演进 = 装饰器 + 注册表 + 冷检测（不破坏老代码）**
> **UI 匹配 = 6 种事件 + store_version + 写穿透读 store + 命令走 EventBus**

---

## 0. 核心矛盾：L0（CLI）和 L3（Web UI）的根本不同

| L0（CLI） | L3（Web UI） |
|---|---|
| 一个进程，一个 event loop | 一个浏览器，多个 tab |
| 状态全在内存 | 状态在 server，UI 是"远程瘦客户端" |
| 用户主动 `openloom status` 查 | 浏览器要**自动**收到更新 |
| 推一下动一下（pull） | 要实时推送（push） |

**"匹配"的本质** = **L3 启动时怎么知道 L0 跑过什么 + L0 跑的时候 L3 怎么实时看到**。

---

## 1. 两种解法对比

| 解法 | 实现 | 状态来源 | 实时性 | 适用场景 |
|---|---|---|---|---|
| **A. 同进程** | `--ui` 启 FastAPI + SSE，读 harness 内存 | 内存（直读） | < 50ms | **L3（个人用户，推荐）** |
| **B. 双进程** | OpenDeck 风格，server 独立跑，CLI 写 store | SQLite | < 200ms | **L6（团队模式）** |

**A 适合 L0→L3 个人用户**（你的 `--ui` 模式），**B 适合 L6 团队模式**（`openloom serve`）。

L3 用 A，L6 用 B，**两套各做各的，不要混**。

---

## 2. L3 的"匹配"架构（同进程）

### 三层 + 三个边界

```
┌─────────────────────────────────────────────────┐
│ Browser (http://127.0.0.1:55413)                │
│  ┌──────────────────────────────────────┐       │
│  │ Single HTML file (~150 行 JS)        │       │
│  │ - 启动时 GET /api/tasks 拉全量       │       │
│  │ - 订阅 /api/events (SSE) 拿增量      │       │
│  │ - 渲染时按 task_id 找增量更新        │       │
│  └──────────────────────────────────────┘       │
└──────────────┬──────────────────────────────────┘
               │ HTTP + SSE
┌──────────────▼──────────────────────────────────┐
│ FastAPI app (in-process)                         │
│  ┌──────────────────────────────────────┐       │
│  │ Routes:                              │       │
│  │   GET  /api/tasks        → 全量      │       │
│  │   GET  /api/tasks/{id}   → 详情      │       │
│  │   GET  /api/events       → SSE 流    │       │
│  │   POST /api/tasks/{id}/archive       │       │
│  └──────────────────────────────────────┘       │
└──────────────┬──────────────────────────────────┘
               │ 调函数（同进程）
┌──────────────▼──────────────────────────────────┐
│ Harness (in-process)                             │
│  ┌──────────────────────────────────────┐       │
│  │  - 状态机 (内存)                     │       │
│  │  - EventBus (内存 pub/sub)           │       │
│  │  - 调用 Source / Checker / Sink      │       │
│  └──────────────────────────────────────┘       │
└─────────────────────────────────────────────────┘
```

### 三个边界的设计原则

| 边界 | 谁拥有数据 | 谁不能碰 |
|---|---|---|
| Browser ↔ FastAPI | FastAPI（HTTP 协议） | Browser 不读 store，FastAPI 不直接读 harness 内存之外的源 |
| FastAPI ↔ Harness | Harness（内存） | FastAPI 不写 harness 状态，Harness 不解析 HTTP |
| Harness ↔ Source/Checker/Sink | 各 ABC | Harness 不持有业务逻辑 |

**这条线划对了，"匹配"问题就解决了 80%**。

---

## 3. 演进 + 复用的核心原则

### 核心思想：每个能力 = (Source) × (Checker) × (Sink)

不要按"功能"划分（不要有 `L2 OpenSpec 类`、`L4 校验类`、`L5 GitHub 类`）。

**按"职责"划分**：3 个 ABC + 1 个编排器。

```
openloom/core/
├── source.py      ← TaskSource ABC：任务从哪儿来
├── checker.py     ← CompletionChecker ABC：怎么判断"完成"
├── sink.py        ← TaskSink ABC：完成后通知谁（CLI/Web/钉钉/Slack）
└── harness.py     ← 编排器：把 source/checker/sink 拼起来
```

L0-L7 不是 7 个产品，是**这 3 个 ABC 的不同实现 + 不同组合**。

**复用度举例**：
- L0 = `ManualSource` + `StringChecker` + `ConsoleSink`
- L2 = `OpenSpecSource` + `OpenSpecChecker` + `ConsoleSink`（**只换 source + checker，sink 复用**）
- L3 = L2 + `WebSink`（**只加 sink，其他复用**）
- L4 = L2 + `PreArchiveChecker`（**包装 OpenSpecChecker，不替换**）
- L5 = `GitHubSource` + `OpenSpecChecker` + `WebSink`（3 个都换，但都是已有实现）

**未来加 L8 钉钉通知 = 写一个 `DingTalkSink`，其他都不动**。

---

## 4. 6 条具体建议

### 建议 1：Core 做"3 + 1"极小集合，不超过 600 行

```
core/source.py       ~120 行  （ABC + 1 个 ManualSource）
core/checker.py      ~150 行  （ABC + 1 个 StringChecker）
core/sink.py         ~80 行   （ABC + 1 个 ConsoleSink）
core/harness.py      ~150 行  （状态机 + 事件总线）
core/store.py        ~100 行  （SQLite）
```

**绝不超过 600 行**。这就是底座。

**反模式**：`core` 里塞 2000 行"工具函数" → 演进时互相牵动。

### 建议 2：每个 ABC 必须"先 L0 用上"，再"演化出 L1/L2"

不是"先设计 ABC 等着用"，是"**L0 用了一个超简实现 → 抽 ABC → L2 写第二个实现**"。

**例子**：
- v0.1：L0 直接 `grep "DONE"` → 200 行，**没有 ABC**
- v0.2：复用 L0 的代码**抽出 `StringChecker` ABC**
- v0.3：L2 写 `OpenSpecChecker implements Checker` → 此时 ABC 才有第二个实现
- v0.4：L4 写 `PreArchiveChecker`（包装而非替换）→ 第三次验证 ABC

**好处**：ABC 的接口是**真实需求驱动**的，不是凭空设计。等到 L4 写完，ABC 一定是"对的"。

**反模式**：v0.1 写 ABC，L0 用 → ABC 一定有"实际用不上"的接口。

### 建议 3：用"装饰器模式"做能力组合，不用"继承"

`PreArchiveChecker` 应该是：
```python
def OpenSpecChecker().with_pre_archive(validate_fn)  # 装饰
```
不是：
```python
class PreArchiveOpenSpecChecker(OpenSpecChecker)  # 继承
```

**理由**：装饰器可以**任意组合**（`with_retry + with_pre_archive + with_audit`），继承会产生组合爆炸（`RetryPreArchive` / `PreArchiveRetry` / ...）。

`functools.wraps` + 一个 `Checker` 接受另一个 `Checker` 即可实现，~30 行。

### 建议 4：能力按"extras"挂载，不在主包

```toml
[project.optional-dependencies]
openspec = ["openspec>=0.4.0"]     # L2
ui       = ["fastapi", "uvicorn"]  # L3
validate = ["pytest", "mypy"]       # L4
github   = ["PyGithub"]            # L5
```

**绝不放主依赖**。**绝不**在 `__init__.py` 里 `try import openspec` —— 那是"看起来可选其实耦合"。

**检测模式**：用户 `pip install openloom` 后，目录里**完全没有** `openspec` 的痕迹。
```python
def is_openspec_available() -> bool:
    try:
        import openspec  # noqa
        return True
    except ImportError:
        return False
```
**冷检测**（不缓存），不强依赖。

### 建议 5：能力注册用"显式注册表"，不用"自动发现"

```python
# openloom/levels/L2_openspec/__init__.py
from openloom.core.registry import register_source, register_checker

@register_source("openspec")
class OpenSpecSource:
    ...

@register_checker("openspec")
class OpenSpecChecker:
    ...
```

**好处**：
- 用户看 `pyproject.toml` 就知道有哪些 level
- Web UI 启动时 `registry.list_all()` 知道按钮哪些 enabled
- 不会出现"装了 plugin 但不知道有什么能力"

**反例**：`importlib.metadata.entry_points()` 插件系统 —— 看着高级，但**调试地狱**（看不到谁注册了什么）。

### 建议 6：Harness 永远不直接调 ABC 方法，调"事件"

```python
# 错：harness 直接调 checker
if checker.is_done(task):  # 直接调用
    sink.notify(task)

# 对：harness 发事件，谁订阅谁处理
events.emit(TaskCompletedEvent(task))
# → ConsoleSink 订阅 → 打印
# → WebSink 订阅 → 推送 SSE
# → AuditSink 订阅 → 写审计日志（v0.5 才实现，但接口已留）
```

**好处**：加 L6/L7 时**不改 harness**。底座可演进。

`core/events.py` 只要 80 行就够（in-process pub/sub + 6 种事件 type）。

---

## 5. 验证标准：L8 测试

> **"加 L8 钉钉通知需要改几行代码？"**

| 设计 | 改的行数 | 评级 |
|---|---|---|
| ❌ 在 harness 里加 `if sink == "dingtalk":` | 改 harness + 1 个新文件 | 烂 |
| ❌ 继承 ConsoleSink 写 DingTalkSink | 1 个新文件 | 中 |
| ✅ 实现 `Sink` ABC + `@register_sink("dingtalk")` | **1 个新文件，0 行改老代码** | 对 |

**这就是"复用通用能力"的可验证定义。**

如果某天加新能力要改老代码，说明 ABC 抽错了，回到建议 2 重新演化。

---

## 6. 4 个具体匹配点（最易翻车的细节）

### 匹配点 1：启动时 L3 怎么"追上" L0 已发生的事？

**问题**：用户先跑 `openloom watch` 跑了一夜，第二天早上开浏览器，他要看历史。

**解法**：SSE 不带历史，**全量 + 增量结合**。

```python
# FastAPI route
@app.get("/api/tasks")
def list_tasks():
    return harness.store.list_tasks()  # 一次拉完

@app.get("/api/events")
async def events():
    async def gen():
        # 1. 先发个"快照点"事件，告诉客户端当前 store version
        yield f"event: snapshot\ndata: {harness.store.version()}\n\n"
        # 2. 再订阅 EventBus，转 SSE
        queue = harness.events.subscribe()
        try:
            while True:
                evt = await queue.get()
                yield f"event: {evt.type}\ndata: {json.dumps(evt.data)}\n\n"
        finally:
            harness.events.unsubscribe(queue)
    return StreamingResponse(gen(), media_type="text/event-stream")
```

**前端逻辑**（~30 行 JS）：
```javascript
// 1. 启动拉全量
const tasks = await fetch('/api/tasks').then(r => r.json());
renderTasks(tasks);

// 2. 开 SSE
const sse = new EventSource('/api/events');
sse.addEventListener('task_updated', e => {
    const task = JSON.parse(e.data);
    updateTaskInDOM(task);  // 按 task_id 找 DOM 节点 patch
});
```

**好处**：全量 + 增量 = 不重不漏，**断网重连也对**（重连时重拉全量）。

### 匹配点 2：状态在内存 vs 在 store，谁说了算？

**问题**：Harness 改了内存状态但还没写 SQLite，UI 来查怎么办？

**解法**：**写穿透 + 读 store**。

```python
# harness.py - 状态变更时同步写 store
def transition(task_id, new_state):
    self.store.update_state(task_id, new_state)  # 写穿透
    self.events.emit(TaskStateChangedEvent(...))  # 再发事件

# FastAPI route - 永远读 store
@app.get("/api/tasks/{task_id}")
def get_task(task_id):
    return harness.store.get_task(task_id)  # 不读 harness 内存
```

**好处**：
- UI 永远从 store 读 → 数据一致
- EventBus 只推"变化通知"，不带数据本身 → **避免数据双源**

**反模式**：前端既拉全量又从 SSE 里拿 task 对象 → 数据会不一致。

### 匹配点 3：用户操作（archive / rollback）怎么不打架？

**问题**：用户在 CLI 输 `openloom archive abc`，同时在 Web 点"归档"按钮，**两个请求同时到**。

**解法**：**所有变更走 EventBus，FastAPI 不直接调 harness**。

```python
# FastAPI route - 只发命令事件
@app.post("/api/tasks/{task_id}/archive")
def archive(task_id):
    harness.events.emit(UserCommandEvent(
        type="archive",
        task_id=task_id,
        source="web"  # 标记来源，去重用
    ))
    return {"ok": True}  # 立刻返回，不等执行

# harness - 收到命令后执行，再发结果事件
def on_user_command(evt):
    if evt.type == "archive":
        if self.store.get_state(evt.task_id) != "running":
            return  # 幂等
        self.archive_task(evt.task_id)
        self.events.emit(TaskArchivedEvent(...))
```

**好处**：
- EventBus 天然串行化（单线程 loop）
- 重复命令幂等（先看状态再动）
- CLI 和 Web 走同一条路 → 不会出现"CLI 归档成功但 Web 还显示 running"

### 匹配点 4：Web UI 关闭后再打开，怎么"接续"？

**问题**：用户早上开浏览器看一眼，关掉，下午再开。

**解法**：**SSE 不持久化 + 重连时重拉全量**。

- SSE 连接断开 → 前端 `EventSource` 自动重连（浏览器原生）
- 重连时 `GET /api/tasks` 重新拉全量（**前端代码固定这样写**）
- 不需要复杂的"断点续传" / "last event id" 机制

**反模式**：把 SSE 事件持久化到 SQLite、客户端用 `Last-Event-ID` 续传 → 复杂度暴涨 10 倍，**没必要**。

---

## 7. 文件层面的"匹配"结构

```
openloom/server/
├── app.py              # create_app(harness=) 工厂函数
├── routes/
│   ├── tasks.py        # GET /api/tasks, /api/tasks/{id}
│   ├── events.py       # GET /api/events (SSE)
│   └── actions.py      # POST /api/tasks/{id}/archive, /rollback
└── static/
    └── index.html      # ~150 行：HTML + CSS + JS 单文件
```

**`create_app(harness=)` 工厂函数**是关键：

```python
# L3 模式（同进程）
harness = Harness(...)
app = create_app(harness=harness)
# uvicorn.run(app, port=55413)  # 在同一进程

# L6 模式（双进程，server 独立）
# openloom serve 启动时：
harness = Harness.load_from_store()
app = create_app(harness=harness)
# uvicorn.run(app, port=55413)
# 同时 harness.run() 在另一个线程/进程
```

**两套用法，零代码改动**。

---

## 8. 6 种事件 schema（够 L0-L4 用）

```python
# openloom/core/events.py
class TaskCreated:      # 任务入队
class TaskStarted:      # 开始执行
class TaskUpdated:      # 任意状态变更
class TaskCompleted:    # 成功
class TaskFailed:       # 失败
class LogLine:          # Agent 输出新行
```

**每个事件必带 3 个字段**：
- `task_id`：定位
- `timestamp`：排序
- `store_version`：前端用它判断"该重拉全量了吗"

`store_version` 是个单调递增整数，**每次写 store 就 +1**。前端 SSE 收到事件时比较 `本地 version < 事件 version` → 主动重拉全量。

**这是"匹配"的最后一道保险**。

---

## 9. 行动顺序

| 步骤 | 做什么 | 为什么 |
|---|---|---|
| 1 | 写 `core/events.py`（6 种事件 + EventBus） | 事件 schema 决定 L0-L7 全局 |
| 2 | 写 `core/harness.py`（最小状态机） | 验证事件 schema 在 L0 真能用 |
| 3 | 写 `core/checker.py`（L0 字符串匹配，**不抽 ABC**） | 故意不抽，避免过度设计 |
| 4 | 跑 L0 真实流程 | 验证事件 + harness + checker 串得通 |
| 5 | v0.2 抽 `Checker` ABC + 写 `OpenSpecChecker` | 首次复用 → ABC 验证 |
| 6 | v0.3 抽 `Sink` ABC + 写 `WebSink` | 二次复用 → ABC 验证 |
| 7 | v0.4 写 `PreArchiveChecker`（装饰器组合） | 三次复用 → 装饰器模式验证 |
| 8 | v0.5+ 加 L5-L7 | 每加一个 level 跑一次 L8 测试 |

**现在做 = 步骤 1（事件 schema），不写 Web UI**。

L0 跑通后才有 L3 的"匹配"问题可解 —— 事件 schema 错了，UI 怎么写都错。

---

## 10. 一页纸总结（TL;DR）

### 演进原则
- **底座 600 行**：3 ABC + 1 编排器 + 1 事件总线，**永不变**
- **L0→L7**：3 ABC 的不同实现 + 不同组合，不是 7 个产品
- **加新能力**：装饰器 + 注册表 + 冷检测，**0 行改老代码**
- **抽 ABC 顺序**：L0 写死 → L1 抽 ABC → L2 写第二个实现 → L3 验证
- **L8 测试**：加新 sink/source/checker 改老代码 = 设计错了

### UI 匹配原则
- **L3 同进程**：FastAPI + SSE，读 harness 函数
- **L6 双进程**：server 独立跑，读 store
- **数据单一来源**：写穿透 store + UI 永远读 store + EventBus 只推通知
- **全量 + 增量**：GET /api/tasks 拉全量 + SSE 拿增量
- **断线重连**：重连时重拉全量，不做 last-event-id
- **命令去重**：所有变更走 EventBus + 幂等检查
- **store_version**：每个事件带版本号，前端判断要不要重拉

### 文件结构
```
openloom/core/          ← 600 行，永不变
├── source.py           ← TaskSource ABC
├── checker.py          ← CompletionChecker ABC
├── sink.py             ← TaskSink ABC
├── harness.py          ← 编排器
├── events.py           ← EventBus + 6 种事件
└── store.py            ← SQLite

openloom/levels/        ← 7 个独立 sub-package，平级
├── L0_manual/          ← 默认装
├── L1_config/          ← 默认装
├── L2_openspec/        ← openloom[openspec]
├── L3_ui/              ← openloom[ui]
├── L4_validate/        ← openloom[validate]
├── L5_sources/         ← openloom[sources-github]
├── L6_server/          ← openloom[server]
└── L7_plugins/         ← plugin API
```

### 关键约束
1. `core/` **不 import 任何 level**（单向依赖）
2. Level 之间**不互相 import**（平级）
3. 升级 = **加 level**，不删老 level
4. 废弃 = 标 `deprecated` + 保留 6 个月 + 给迁移路径
5. **`__init__.py` 不 `try import`** 可选依赖（冷检测）
6. **Harness 不直接调 ABC**（走事件）
