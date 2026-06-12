# AGENTS.md

## 项目说明

**OpenLoom** 是 OpenCode 的**观测面 + 极简无人值守 harness**，不替代 OpenCode、不发明新编排概念。

- **产品边界**：补 OpenCode HTTP API 缺的界面（session 监控、发 prompt、归档、diff）；**Task** 是唯一用户操作实体——创建即带 harness 巡检（默认 5 分钟，最短 5 分钟）。
- **单一 Task 概念**：`POST /api/tasks` 接受 `{ intent, plan?, checkIntervalMinutes?, sessionId? }`（UI：Generate plan → 审阅 → Start）或 CLI 的 `{ format, spec }`。
- **明确不做**：`ephemeral` / `bind` / `/api/dispatch` / Send|Watch 双入口 UI；一切 deck 37130ca 任务模型实验一律丢弃。DB 无历史兼容负担，schema 可直接改。
- **7 级渐进（L0-L7）**：每一级独立可用、用户主动升级。L0 字符串判定 → L2 OpenSpec → L4 预归档校验 → L6 server 观测台。
- **底座极小**：3 ABC + Harness + EventBus，core ≤ 600 行；新能力 = 装饰器 + 注册表 + 冷检测。

## 关键文档（改代码前必读）

| 文档 | 内容 |
|---|---|
| `.ai/OPENLOOM_PLAN.md` | 里程碑、资产复用地图、**§15 UI 合并原则** |
| `.ai/OPENLOOM_PACKAGING.md` | pip 包发布策略、目录规划、CI 矩阵 |

## 项目状态

M0–M3 + M6 主线已落地（`openloom watch` / `openloom serve` + Svelte 观测台）。当前重点是 **Web UI 对齐 deck 必要能力**，且严格按 §15 原则——只搬 OpenCode API 的界面补全，不搬 deck 的任务模型实验。

## 目录约定（目标结构）

```
src/openloom/
├── core/        # ≤600 行；events / harness / store / 3 ABC / registry
├── runtime/     # OpenCode HTTP 适配层（client / session_status / prompts）
├── levels/      # manual / config / openspec / ui / validate / github / server
│                # 按能力命名（不带 L 编号），平级、互不 import
└── server/      # create_app(harness=) 工厂 + routes + static（L3/L6 共用）
frontend/        # Svelte 源码（L6），不进 pip 包，CI 构建注入 static/app/
tests/contracts/ # 架构守门测试
```

## 硬性架构约束（违反即架构错误，PR 必须守）

1. `core/` 不 import 本包任何其他子包（levels / runtime / server）；总行数 ≤ 600。
2. levels 之间互不 import；共用代码上提到 `runtime/` 或 `server/`。
3. 任何 `__init__.py` 禁止 `try: import` 可选依赖——用冷检测函数（现场 try import、不缓存）。
4. Harness 不直调 sink 方法：状态变更唯一路径 = 写 store（store_version +1）→ emit 事件。Checker 是 harness 的内部判定器（无外部副作用），可以由 harness 构造期注入并直接调用 —— M4 的 PreArchiveChecker 装饰器正是建立在这个边界上。
5. API 路由只读 store；EventBus 只推通知不带全量数据。
6. 主依赖仅 `httpx` + `pyyaml`；fastapi/uvicorn 等一律走 extras（`[ui]` / `[server]`）。
7. 组合用装饰器（`checker.with_pre_archive(...)`）不用继承。

## 开发环境

- Python >= 3.11，包管理用 `uv`，构建后端 hatchling，src layout。
- 测试 `pytest`，HTTP mock 用 `respx`；lint 用 `ruff` + `mypy`。
- 运行依赖外部 OpenCode Server（默认 `http://127.0.0.1:4096`；仅当 OpenCode 启用了 `OPENCODE_SERVER_PASSWORD` 时才需配置 `OPENLOOM_OPENCODE_*` 凭据，绝不写入代码或提交 git）。

## 提交信息规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/)，**简洁英文**。

格式：

```
<type>(<scope>): <subject>
```

- type：`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `ci` / `perf`
- scope 推荐用包名：`core` / `runtime` / `levels` / `server` / `cli` / `frontend`
- subject：小写开头、祈使句、无句号、≤ 72 字符

**修改内容不止一处时**，正文用 2-4 个 `###` 子标题分块说明：

```
feat(core): add event bus with six event types

### Events
- define TaskCreated/TaskStarted/TaskUpdated/TaskCompleted/TaskFailed/LogLine
- every event carries task_id, timestamp, store_version

### Harness wiring
- emit events on every state transition instead of direct sink calls

### Tests
- add contract test asserting no direct checker/sink invocation
```

单一改动则不需要正文或仅一段简述：

```
fix(runtime): fall back to /session/message when prompt_async returns 404
```

破坏性变更标注 `!` 并在正文加 `BREAKING CHANGE:`（M3 事件 schema 冻结后尤其注意）。
