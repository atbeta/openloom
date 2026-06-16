# OpenLoom

[![CI](https://github.com/atbeta/openloom/actions/workflows/ci.yml/badge.svg)](https://github.com/atbeta/openloom/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/openloom)](https://pypi.org/project/openloom/)

**Don't trust the agent's word — trust the file system.**

OpenLoom is a webhook-driven task harness and observer for [OpenCode](https://github.com/opencode-ai/opencode). Webhook handlers call `POST /api/tasks` to dispatch a goal; OpenLoom attaches the goal to an OpenCode session, watches the session for activity, and emits a stream of `TASK_UPDATED` events on the bus. Webhook consumers can take over a stuck session with `POST /api/tasks/{id}/abort` and follow up with a fresh prompt — even from a phone, without touching the host.

OpenLoom does **not** replace OpenCode. It fills the gaps in OpenCode's HTTP API: a JSON task API, a session monitor, a webhook notification fan-out, and a web dashboard for browsing live work.

## What's in 0.12

0.12 is a YAGNI cut. The previous release also offered an `openloom watch` runner (single-spec file mode with manual checks / acceptance / step-acknowledgement), a file-inbox dispatch path, a stale-busy alert, file-based notification sinks, and an AI task planner. All of that is gone. The webhook API below is the single control surface.

What stayed:

- The 0.10 task lifecycle (create / start / update / complete / fail / archive / delete).
- The 0.11 webhook notification format with `task_name` + `timestamp` + `timestamp_iso` + `recent_activity` (last 3 assistant messages, 1 000 chars each, with a tool-call summary).
- The web dashboard (Dashboard / Activity / Tasks / Permissions / session drawer).

What's new in 0.12:

- **`POST /api/tasks/{id}/abort`** — break a stuck agent loop on the task's session before sending a follow-up prompt.
- **`TaskSpec` reduced to three fields** (`name`, `workspace`, `goal`) — every webhook handler payload is now 4 lines.
- **Single CLI subcommand** — `openloom serve` is the only thing the CLI does. `init`, `watch`, `status`, `log` are removed.

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
| `ui` | Web dashboard (`openloom serve`) |
| `server` | Same as `ui` (alias) |

## Quick start

### 1. Start OpenCode

OpenLoom talks to OpenCode over HTTP. In a separate terminal:

```bash
opencode serve
# or launch the OpenCode app / TUI (it also exposes the API on port 4096)
```

If OpenCode is not running, `openloom serve` starts the dashboard but session features stay offline until OpenCode is up.

### 2. Configure the OpenCode connection (optional)

Most local setups need **no env vars** — OpenLoom defaults to `http://127.0.0.1:4096` with no HTTP auth (same as `opencode serve` without `OPENCODE_SERVER_PASSWORD`).

If your OpenCode server uses HTTP basic auth:

```bash
export OPENLOOM_OPENCODE_URL=http://127.0.0.1:4096
export OPENLOOM_OPENCODE_USERNAME=opencode
export OPENLOOM_OPENCODE_PASSWORD=your-password
```

Full env-var reference: [docs/configuration.md](docs/configuration.md).

### 3. Start OpenLoom

```bash
openloom serve
# or with overrides
openloom serve --host 0.0.0.0 --port 55413
```

Open `http://127.0.0.1:55413` for:

- **Dashboard** — task list, per-session token usage, model breakdown.
- **Activity** — sessions by workspace, archived tasks.
- **New Task** — the same `POST /api/tasks` body as a webhook would send.
- **Permissions** — pending tool approvals with Once / Always / Deny.
- **Session drawer** — full transcript, diff, metadata.

The UI static assets are **pre-built inside the wheel** — no Node.js required at install time.

### 4. Create a task via webhook

```bash
curl -X POST http://127.0.0.1:55413/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Fix the type error",
    "workspace": "/Users/you/project",
    "goal": "There is a type error in src/foo.ts line 42. Fix it and run npm test."
  }'
```

The response is `{ok, taskId, status, name, workspace, sessionId}`. The task is now `pending`; on the next harness tick (within 8 seconds) it transitions to `running` with an OpenCode session id bound to it.

If you have a webhook configured, the next events arrive on it:

```json
{
  "event": "TASK_STARTED",
  "task_id": "task_abc123",
  "task_name": "Fix the type error",
  "timestamp": 1749999999.123,
  "timestamp_iso": "2026-06-15T00:00:00Z",
  "store_version": 1,
  "data": {
    "session_id": "ses_xyz789",
    "summary": "Harness started and bootstrap prompt sent"
  }
}
```

### 5. Take over a stuck session

```bash
# 1. Get the stuck task's id from the webhook or dashboard
TASK=task_abc123

# 2. Abort the in-flight agent loop on the task's session
curl -X POST http://127.0.0.1:55413/api/tasks/$TASK/abort

# 3. Archive the old task so it stops showing in the active list
curl -X POST http://127.0.0.1:55413/api/tasks/$TASK/archive

# 4. Send a follow-up prompt as a new task bound to the same session
curl -X POST http://127.0.0.1:55413/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Take over: fix the type error",
    "sessionId": "ses_xyz789",
    "goal": "Forget what you were doing. Just look at src/foo.ts line 42 and fix the type error."
  }'
```

The new task binds to the same OpenCode session; the old task is archived. The session itself keeps its full transcript — both the old and new task can be opened in the dashboard and read each other's messages.

## API surface

```
POST   /api/tasks                       create a task from {name, workspace, goal, sessionId?}
GET    /api/tasks                       list tasks
GET    /api/tasks/{id}                  task detail
POST   /api/tasks/{id}/abort            release any in-flight agent loop on the task's session
POST   /api/tasks/{id}/pause            pause a running task
POST   /api/tasks/{id}/resume           resume a paused task
POST   /api/tasks/{id}/complete         mark a task complete
POST   /api/tasks/{id}/archive          archive a task
DELETE /api/tasks/{id}                  delete an archived task

GET    /api/sessions                    list sessions (monitor snapshot)
GET    /api/sessions/{id}/messages      full transcript
GET    /api/sessions/{id}/diff          accumulated diff
GET    /api/permissions                 list pending permissions
POST   /api/sessions/{id}/permissions/{pid}   respond to a permission
POST   /api/sessions/{id}/archive       archive a session
POST   /api/sessions/{id}/delete        hard-delete a session

GET    /api/state                       composite dashboard state
GET    /api/events                      server-sent event stream
GET    /api/recent-workspaces           recently used workspaces
DELETE /api/recent-workspaces           remove from recent
GET    /api/browse                      directory listing for the path picker
POST   /api/pick-folder                 native OS folder picker
```

Webhook payload format and event types: [docs/notifications.md](docs/notifications.md).

## Architecture (short)

```
core/      Harness, store, event bus, protocols
runtime/   OpenCode HTTP client, prompts
levels/
  notify/  WebhookSink + NotifyConfig
  server/  SessionMonitor, WebSink, ConsoleSink, serve
server/    FastAPI app + routes + static UI ([ui] extra)
docs/      Detailed guides (configuration, notifications, architecture)
tests/     contracts/ holds the architecture-enforcement tests
```

State changes flow: **store write → event emit → sinks**. API routes only read the store; the event bus pushes notifications, not data. Details: [docs/architecture.md](docs/architecture.md).

## Development

```bash
uv sync
uv run pytest
uv run ruff check src/ tests/
uv run mypy src/openloom

# Rebuild frontend into the package (before release)
cd frontend && npm install && npm run build
cd .. && uv build
```

## License

MIT — see [LICENSE](LICENSE).
