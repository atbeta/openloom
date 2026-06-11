# AGENTS.md

## 项目说明

**OpenLoom** 是一个面向 OpenCode 的渐进式无人值守任务 harness（观测面 + 完成判定），核心理念如下：

- **7 级渐进（L0-L7）**：每一级都是独立可用的真产品，用户主动升级、可随时停留。L0 = 零配置 CLI（200 行、字符串匹配判定），L2 = OpenSpec checkbox 判定，L3 = `--ui` Web 面板，L4 = 预归档校验（pytest/mypy/git diff 闸门），L6 = 团队 server 模式。
- **核心价值主张**："Don't trust the agent's word, trust the file system"——误判率 35%（L0）→ 8%（L2）→ <1%（L4）。
- **底座极小**：3 个 ABC（Source / Checker / Sink）+ 1 编排器（Harness）+ 1 事件总线，core 总计 ≤ 600 行，永不变；新能力 = 装饰器 + 显式注册表 + 冷检测，0 行改老代码。

## 关键文档（改代码前必读）

| 文档 | 内容 |
|---|---|
| `.ai/OPENLOOM_PLAN.md` | 里程碑 M0-M7 落地计划、opencode-deck 资产复用地图 |
| `.ai/OPENLOOM_PACKAGING.md` | pip 包发布策略、目录规划、CI 矩阵 |

## 项目状态

当前处于规划/骨架阶段。代码资产主要来自 opencode-deck 的迁移（`opencode_client.py` / `session_status.py` / `task_spec.py` 等约 1200 行可直接搬入 `runtime/`，详见 PLAN 第 1 节）。

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
- 运行依赖外部 OpenCode Server（默认 `http://127.0.0.1:14096`，Basic Auth 凭据走环境变量 `OPENLOOM_*`，绝不写入代码或提交 git）。

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
