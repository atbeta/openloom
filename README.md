# OpenLoom

[![CI](https://github.com/atbeta/openloom/actions/workflows/ci.yml/badge.svg)](https://github.com/atbeta/openloom/actions/workflows/ci.yml)

**Don't trust the agent's word — trust the file system.**

OpenLoom is a lightweight harness and observer for [OpenCode](https://github.com/opencode-ai/opencode). It schedules tasks, watches sessions, and verifies completion with file-system checks instead of model self-reports.

OpenLoom does **not** replace OpenCode. It fills the gaps in OpenCode's HTTP API: session monitoring, task plans, periodic checks, a web dashboard, and token usage summaries.

## Install

Requires Python 3.11+ and a running OpenCode server.

```bash
pip install openloom
# or
uv tool install openloom
```

**Web dashboard** (FastAPI + bundled Svelte UI):

```bash
pip install "openloom[ui]"
```

Optional extras (install only what you need):

| Extra | Purpose |
|-------|---------|
| `ui` | Web dashboard (`openloom serve`, `openloom watch --ui`) |
| `server` | Same as `ui` (team server mode alias) |
| `openspec` | OpenSpec checkbox completion checks (cold-detected; no extra deps yet) |
| `github` | GitHub integration |
| `validate` | Pre-archive validation hooks (uses your project's pytest/mypy) |

There is intentionally **no `[all]`** extra — pick capabilities as you grow.

## Quick start

### 0. Start OpenCode

OpenLoom talks to OpenCode over HTTP. In a separate terminal:

```bash
opencode serve
# or launch the OpenCode app/TUI (it also exposes the API on port 4096)
```

If OpenCode is not running, `openloom watch` exits with setup hints; `openloom serve` starts the dashboard but session features stay offline until OpenCode is up.

### 1. Configure OpenCode connection (optional)

Most local setups need **no env vars** — OpenLoom defaults to `http://127.0.0.1:4096` with no HTTP auth (same as `opencode serve` without `OPENCODE_SERVER_PASSWORD`).

If your OpenCode server uses HTTP basic auth:

```bash
export OPENLOOM_OPENCODE_URL=http://127.0.0.1:4096
export OPENLOOM_OPENCODE_USERNAME=opencode
export OPENLOOM_OPENCODE_PASSWORD=your-password
```

Windows (PowerShell):

```powershell
$env:OPENLOOM_OPENCODE_URL = "http://127.0.0.1:4096"
$env:OPENLOOM_OPENCODE_USERNAME = "opencode"
$env:OPENLOOM_OPENCODE_PASSWORD = "your-password"
```

Other optional variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_OPENCODE_URL` | `http://127.0.0.1:4096` | OpenCode HTTP base URL |
| `OPENLOOM_OPENCODE_USERNAME` | `opencode` | Basic auth user (only if OpenCode has a server password) |
| `OPENLOOM_OPENCODE_PASSWORD` | *(empty)* | Basic auth password |
| `OPENLOOM_DATABASE` | `.openloom/openloom.sqlite3` | Task store path |
| `OPENLOOM_UI_HOST` | `127.0.0.1` | Web UI bind address |
| `OPENLOOM_UI_PORT` | `55413` | Web UI port |

### 2. CLI — watch a task spec

```bash
openloom init                    # writes openloom.yaml in cwd
openloom watch                   # run harness from openloom.yaml
openloom watch --ui              # same + local web UI (needs [ui])
openloom status
openloom log <task-id-prefix>
```

Example spec (`openloom.yaml`):

```yaml
name: Fix SSE reconnect
workspace: /path/to/project
check_interval_minutes: 5   # minimum 5; all tasks are harness-watched
goal: |
  Fix SSE reconnect after network drop.
steps:
  - Investigate current SSE implementation
  - Implement reconnect with backoff
  - Add regression coverage
```

### 3. Web dashboard — multi-task server

```bash
pip install "openloom[ui]"
openloom serve --host 127.0.0.1 --port 55413
```

Open `http://127.0.0.1:55413` for:

- **Dashboard** — session token usage, by-model breakdown, period summaries (Today / week / month / total)
- **Activity** — tasks, archived tasks, sessions by workspace
- **New Task** — plan with goal/steps/acceptance, attach to workspace or existing session
- **Permissions** — pending tool approvals with Once / Always / Deny (optional auto-accept, OpenCode Desktop–compatible)
- **Session drawer** — messages, usage, diff, metadata per session

The UI static assets are **pre-built inside the wheel** — no Node.js required at install time.

## Architecture (short)

```
core/      Harness, store, event bus, Source / Checker / Sink ABCs (keep lean)
runtime/   OpenCode HTTP client, session status, prompts
levels/    Progressive capabilities (manual, config, openspec, ui, …)
server/    FastAPI app + routes + static UI ([ui] extra)
```

State changes flow: **store write → event emit**. API routes read the store; the event bus pushes notifications only.

## Development

```bash
uv sync
uv run pytest

# Rebuild frontend into the package (before release)
cd frontend && npm install && npm run build
cd .. && uv build
```

## License

MIT — see [LICENSE](LICENSE).
