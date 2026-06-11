# OpenLoom 发布形式与目录规划

> 配套 `OPENLOOM_PLAN.md`。决策点：pip 包形态、构建工具链、extras 矩阵、前端产物打包、目录布局。

---

## 1. 发布形式决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 分发渠道 | PyPI（`pip install openloom`），推荐 `uvx openloom` / pipx | LEVELS.md 的"35 秒安装"承诺 |
| 占名 | 骨架搭好立即发 0.0.1 占位版 | `openloom` 有被抢注风险 |
| License | 第一个 release 前定（建议 MIT 或 Apache-2.0） | deck 遗留问题，不能再拖 |
| 布局 | **src layout**（`src/openloom/`） | extras 隔离必须在"安装后形态"下测试才有效 |
| 构建后端 | hatchling | 对"前端产物注入 wheel"支持好，配置简洁 |
| 开发工具 | uv + uv.lock | CI 与本地一致 |
| Python | >= 3.11 | 与 deck 一致 |
| 类型 | 随包 `py.typed` | ABC 是插件作者的公共 API |
| 入口 | console script `openloom` + `__main__.py` | 两种调用方式都支持 |
| 版本 | 0.x 对应里程碑（M0=0.1…）；M3 后 schema 冻结、遵守 semver | 见 PLAN M3 |
| 发布流水线 | tag 驱动 GitHub Actions + PyPI Trusted Publishing（OIDC） | 无长期 token |

## 2. pyproject 核心（extras 矩阵）

```toml
[project]
name = "openloom"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27", "pyyaml>=6.0"]   # 仅此两个

[project.optional-dependencies]
openspec = ["openspec>=0.4"]
ui       = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "sse-starlette"]
validate = []            # L4 调用户项目自己的 pytest/mypy，不携带依赖
github   = ["PyGithub>=2.0"]
server   = ["openloom[ui]"]

[project.scripts]
openloom = "openloom.cli:main"
```

要点：

- **故意不提供 `[all]`**——LEVELS.md 反全家桶立场的落地。
- `validate` extra 为空是合理的：L4 跑的是用户项目的测试工具链；保留 extra 只为维持"用户主动升级"的仪式感，M4 时可重新评估是否改为"默认在、配置触发"。

## 3. 前端产物打包（deck 最痛的坑）

deck 现状："pip install 不会打包前端静态文件，需单独构建"——pip 包不可接受，**用户不得被要求装 Node**。

方案：

1. **L3 单文件 `index.html`**：作为源文件放 `src/openloom/server/static/`，零构建，天然随包分发。
2. **L6 Svelte 观测台**：`frontend/` 留仓库根、不进包；release CI 中 `npm run build` → 产物输出 `src/openloom/server/static/app/` → 再 `hatch build`。wheel 和 sdist 都含预构建产物；git 不提交产物（.gitignore + CI 保证）。

## 4. 目录规划

对架构文档的修正：**不用 `levels/L0_manual/` 这种带编号目录名**。理由：大写 L 违反 PEP 8；两份文档自身已出现编号漂移（`L5_sources` vs `L5_github`）；未来在级间插能力时编号尴尬。**目录按能力命名，Level 编号只存在于文档与 registry 元数据。**

```
open-loom/
├── pyproject.toml
├── uv.lock
├── README.md
├── LICENSE
├── .github/workflows/
│   ├── ci.yml              # lint + 测试 + 架构守门
│   └── release.yml         # tag → 构建前端 → hatch build → PyPI(OIDC)
├── .ai/                    # 架构与计划文档
├── docs/                   # 用户文档：levels 对照、权限配置指南(继承 deck)
├── examples/
│   └── task.yaml
├── frontend/               # Svelte 源码(L6)，不进 pip 包
├── src/openloom/
│   ├── __init__.py         # 仅 __version__，零 try-import（守门）
│   ├── __main__.py
│   ├── py.typed
│   ├── cli.py              # watch / init / status / log / serve
│   ├── config.py           # ← deck config.py，前缀 OPENLOOM_
│   ├── core/               # ≤600 行，不 import 本包其他子包
│   │   ├── events.py
│   │   ├── harness.py
│   │   ├── store.py
│   │   ├── source.py
│   │   ├── checker.py
│   │   ├── sink.py
│   │   └── registry.py
│   ├── runtime/            # OpenCode 适配层
│   │   ├── opencode.py     # ← deck opencode_client.py
│   │   ├── session_status.py
│   │   └── prompts.py      # ← deck task_spec.py 的 prompt 部分
│   ├── levels/             # 平级、互不 import
│   │   ├── manual/         # L0
│   │   ├── config/         # L1
│   │   ├── openspec/       # L2  [openspec]
│   │   ├── ui/             # L3  [ui]
│   │   ├── validate/       # L4
│   │   ├── github/         # L5  [github]
│   │   └── server/         # L6  [server]
│   └── server/             # L3/L6 共用 FastAPI 层（装 [ui] 后才可 import）
│       ├── app.py          # create_app(harness=) 工厂
│       ├── routes/{tasks,events,actions}.py
│       └── static/
│           ├── index.html  # L3 单文件版（源文件）
│           └── app/        # L6 Svelte 产物（CI 注入，git ignore）
└── tests/
    ├── core/
    ├── runtime/            # respx 录制回放测 client
    ├── levels/
    └── contracts/          # 架构守门测试（见下）
```

### 两个关键设计说明

- **`server/` 在 levels 之外**：被 L3/L6 共用，按"levels 互不 import"规矩共用代码必须上提；又不能进 core（core 禁碰 fastapi），故与 `runtime/` 同级单独成包。
- **`tests/contracts/` 把架构约束变成测试**：
  - core 总行数 ≤ 600（`wc -l` 断言）
  - import 方向：core 不 import levels/runtime/server；levels 互不 import（import-linter）
  - `__init__.py` 全树零 `try: import`（grep 断言）
  - **裸装测试**：CI 独立 job 只 `pip install .`（无 extras），断言 `import openloom` 全程不触碰 fastapi/openspec/PyGithub——这是"35 秒承诺"的持续验证

## 5. CI 任务矩阵

| Job | 内容 |
|---|---|
| lint | ruff + mypy |
| test | pytest（全 extras 安装） |
| contracts | 架构守门（行数 / import / try-import） |
| bare-install | 仅 `pip install .`，验证零 Web 依赖 + CLI 可用 |
| release（tag 触发） | npm build → 注入 static/app → hatch build → PyPI Trusted Publishing |
