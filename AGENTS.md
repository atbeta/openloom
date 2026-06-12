# AGENTS.md

## 项目说明

**OpenLoom** 是 OpenCode 的**观测面 + 极简无人值守 harness**，不替代 OpenCode、不发明新编排概念。

- **产品边界**：补 OpenCode HTTP API 缺的界面（session 监控、发 prompt、归档、diff、权限审批）；**Task** 是唯一用户操作实体——创建即带 harness 巡检（默认 5 分钟，最短 5 分钟）。
- **单一 Task 概念**：`POST /api/tasks` 接受 `{ intent, plan?, checkIntervalMinutes?, sessionId? }`（UI：Generate plan → 审阅 → Start）或 CLI 的 `openloom watch` + YAML spec。
- **明确不做**：`ephemeral` / `bind` / `/api/dispatch` / Send|Watch 双入口 UI；不维护 workspace 路径白名单（权限交给 OpenCode `opencode.json`）。
- **渐进能力（L0–L7）**：extras 按需安装（`[ui]` / `[openspec]` / `[github]` 等），不捆绑全家桶。
- **底座极小**：3 ABC + Harness + EventBus，`core/` ≤ 600 行；新能力 = 装饰器 + 注册表 + 冷检测。

## 项目状态

已可用：`openloom watch`、`openloom serve`、Svelte 观测台（Dashboard / Activity / New Task / session drawer / 权限 UI）。PyPI 包名 `openloom`，默认连本机 OpenCode `http://127.0.0.1:4096`。

## 目录约定

```
src/openloom/
├── core/        # ≤600 行；events / harness / store / 3 ABC / registry
├── runtime/     # OpenCode HTTP 适配（client / session_status / prompts）
├── levels/      # manual / config / openspec / ui / validate / github / server
│                # 按能力命名，平级、互不 import
└── server/      # create_app(harness=) 工厂 + routes + static（[ui] extra）
frontend/        # Svelte 源码，不进 pip 包；release 前 build → static/app/
tests/contracts/ # 架构守门测试
```

## 硬性架构约束（违反即架构错误，PR 必须守）

1. `core/` 不 import 本包任何其他子包（levels / runtime / server）；总行数 ≤ 600。
2. levels 之间互不 import；共用代码上提到 `runtime/` 或 `server/`。
3. 任何 `__init__.py` 禁止 `try: import` 可选依赖——用冷检测函数（现场 try import、不缓存）。
4. Harness 不直调 sink 方法：状态变更唯一路径 = 写 store（store_version +1）→ emit 事件。Checker 是 harness 的内部判定器（无外部副作用），可以由 harness 构造期注入并直接调用。
5. API 路由只读 store；EventBus 只推通知不带全量数据。
6. 主依赖仅 `httpx` + `pyyaml`；fastapi/uvicorn 等一律走 extras（`[ui]` / `[server]`）。
7. 组合用装饰器（`checker.with_pre_archive(...)`）不用继承。

## 开发环境

- Python >= 3.11，包管理用 `uv`，构建后端 hatchling，src layout。
- 测试 `pytest`，HTTP mock 用 `respx`；lint 用 `ruff` + `mypy`。
- 运行依赖外部 OpenCode Server（默认 `http://127.0.0.1:4096`；仅当 OpenCode 启用了 `OPENCODE_SERVER_PASSWORD` 时才需配置 `OPENLOOM_OPENCODE_*` 凭据，绝不写入代码或提交 git）。
- 发版前：`cd frontend && npm run build`，再 `uv build`；`static/app/` 预构建产物需提交进 wheel。
- CI（GitHub Actions）：`ci.yml` 跑 pytest + import-linter + frontend 构建校验 + wheel；`lint.yml` 跑 ruff/mypy（暂 `continue-on-error`）；tag `v*` 触发 `release.yml` 发 PyPI（需配置 Trusted Publishing）。

## 提交信息规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/)，**简洁英文**。

格式：

```
<type>(<scope>): <subject>
```

- type：`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `ci` / `perf`
- scope 推荐用包名：`core` / `runtime` / `levels` / `server` / `cli` / `frontend`
- subject：小写开头、祈使句、无句号、≤ 72 字符

**修改内容不止一处时**，正文用 2-4 个 `###` 子标题分块说明。

破坏性变更标注 `!` 并在正文加 `BREAKING CHANGE:`。
