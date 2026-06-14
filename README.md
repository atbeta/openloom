# OpenLoom

[![CI](https://github.com/atbeta/openloom/actions/workflows/ci.yml/badge.svg)](https://github.com/atbeta/openloom/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/openloom)](https://pypi.org/project/openloom/)

**Don't trust the agent's word — trust the file system.**

OpenLoom is a lightweight harness and observer for [OpenCode](https://github.com/opencode-ai/opencode). It schedules tasks, watches sessions, verifies completion with file-system checks instead of model self-reports, and keeps you notified from anywhere — even when a long-running task gets stuck on a hung child process.

OpenLoom does **not** replace OpenCode. It fills the gaps in OpenCode's HTTP API: session monitoring, task plans, periodic checks, a web dashboard, an inbox for remote dispatch, and webhook / file notifications.

## What's in 0.10

- **Inbox dispatch** — drop a markdown file (or POST to a webhook) to enqueue a task from anywhere. Bind it to an existing session, or spin up a new one.
- **Abort & resume** — send a markdown with `abort: true` and `session: <id>` to take over a stuck session from your phone.
- **Notifications** — webhook + file sinks for every harness event, including a new `SESSION_STALE_BUSY` alert that fires when a session has been busy with no progress for N consecutive checks.
- **Config summary card** — the web dashboard now shows at a glance which channels are live (inbox watcher, webhooks, file sinks).
- **UI refresh** — JetBrains Mono, less AI-app feel, Windows font parity.
- **Architecture hardening** — `core.protocols` (typed injection), `runtime.factory.build_harness()` (single assembly), connection-pool reuse on the OpenCode client.

Full notes per feature: [docs/inbox.md](docs/inbox.md), [docs/notifications.md](docs/notifications.md), [docs/architecture.md](docs/architecture.md).

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

Optional extras — pick what you need; there is intentionally no `[all]`:

| Extra | Purpose |
|-------|---------|
| `ui` | Web dashboard (`openloom serve`, `openloom watch --ui`) |
| `server` | Same as `ui` (team server mode alias) |
| `openspec` | OpenSpec checkbox completion checks (cold-detected) |
| `github` | GitHub integration |
| `validate` | Pre-archive validation hooks (your project's pytest/mypy) |

## Quick start

### 0. Start OpenCode

OpenLoom talks to OpenCode over HTTP. In a separate terminal:

```bash
opencode serve
# or launch the OpenCode app / TUI (it also exposes the API on port 4096)
```

If OpenCode is not running, `openloom watch` exits with setup hints; `openloom serve` starts the dashboard but session features stay offline until OpenCode is up.

### 1. Configure the OpenCode connection (optional)

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

Full env-var reference: [docs/configuration.md](docs/configuration.md).

### 2. CLI — watch a task spec

```bash
openloom init                    # writes openloom.yaml in cwd
openloom watch                   # run harness from openloom.yaml
openloom watch --ui              # same + local web UI (needs [ui])
openloom watch --verbose         # debug-level logs
openloom status
openloom log <task-id-prefix>
openloom serve --host 127.0.0.1 --port 55413
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
openloom serve
```

Open `http://127.0.0.1:55413` for:

- **Dashboard** — session token usage, by-model breakdown, period summaries (today / week / month / total)
- **Activity** — tasks, archived tasks, sessions by workspace, **config summary card** showing live inbox / webhook / file-sink status, **stuck-session pill** when any session has been busy without progress
- **New Task** — plan with goal / steps / acceptance, attach to a workspace or an existing session
- **Permissions** — pending tool approvals with Once / Always / Deny (optional auto-accept, OpenCode Desktop–compatible)
- **Session drawer** — messages, usage, diff, metadata per session

The UI static assets are **pre-built inside the wheel** — no Node.js required at install time.

## Remote dispatch — the inbox

The inbox turns any device that can write a file (or POST an HTTP request) into a dispatch surface.

```bash
# 1. Point OpenLoom at a directory on the host
export OPENLOOM_INBOX_DIR=/path/to/inbox
openloom serve   # or openloom watch
```

Drop a markdown into `/path/to/inbox/task.md`:

```markdown
# Implement retry policy

workspace: /Users/you/project

## goal
Add exponential backoff to the SDK's HTTP client.
```

OpenLoom polls every 30 s (configurable), consumes the file, creates a task, and renames the file to `processed-<id>`. From the UI or the CLI, you can also POST to `/api/inbox/trigger` instead of writing a file.

**Bind to an existing session** so a follow-up turns append to the same transcript:

```markdown
# Continue the type check

session: ses_abc
workspace: /Users/you/project

## goal
Pick up from where you left off.
```

**Take over a stuck session from anywhere** — when you receive a `SESSION_STALE_BUSY` alert, drop a file like this:

```markdown
# Resume the long task

session: ses_abc
abort: true
workspace: /Users/you/project

## goal
Stop the hung npm install and finish the type check.
```

`abort: true` is opt-in. OpenLoom calls `POST /session/ses_abc/abort` (releasing any in-flight tool) and then `prompt_async` with your new goal, so the agent picks up the new task immediately. Full schema and examples: [docs/inbox.md](docs/inbox.md).

## Notifications

Configure webhook or file sinks to receive harness events anywhere:

```bash
# Webhook — POST a JSON event to your endpoint
export OPENLOOM_NOTIFY_WEBHOOK_URLS='https://hooks.example.com/openloom'

# File sink — one JSON file per event written to the directory
export OPENLOOM_NOTIFY_FILE_DIRS=/var/log/openloom-events
export OPENLOOM_NOTIFY_FILE_PREFIX=openloom
```

Built-in event types:

| Event | When |
|-------|------|
| `TASK_CREATED` | A new task was added to the store |
| `TASK_STARTED` | The harness started the first turn of a task |
| `TASK_UPDATED` | Progress, status, or step change |
| `TASK_COMPLETED` | The agent reported completion (or auto-archive) |
| `TASK_FAILED` | Budget exceeded, session lost, etc. |
| `LOG_LINE` | Notable log line (e.g. abort failure) |
| `SESSION_STALE_BUSY` | A session has been busy for `OPENLOOM_STALE_BUSY_CHECKS` consecutive checks (default 10) with no new completed message — typically a hung child process |

`SESSION_STALE_BUSY` is **one-shot per stuck episode** — the latch releases when the session recovers. The alert carries the session id, title, workspace, and stuck duration, so a webhook handler can deep-link to the session drawer.

Full payload schema and per-event filtering: [docs/notifications.md](docs/notifications.md).

## Architecture (short)

```
core/      Harness, store, event bus, Source / Checker / Sink ABCs (lean)
runtime/   OpenCode HTTP client, session status, prompts
levels/    Progressive capabilities (manual, config, openspec, ui, inbox, notify, server, …)
server/    FastAPI app + routes + static UI ([ui] extra)
docs/      Detailed guides (inbox, notifications, configuration, architecture)
```

State changes flow: **store write → event emit**. API routes read the store; the event bus pushes notifications only. See [docs/architecture.md](docs/architecture.md) for the contracts that keep this honest.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src/ tests/

# Rebuild frontend into the package (before release)
cd frontend && npm install && npm run build
cd .. && uv build
```

## License

MIT — see [LICENSE](LICENSE).
