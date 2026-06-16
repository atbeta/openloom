# Configuration

OpenLoom is configured entirely through environment variables. There is no configuration file (the `openloom watch` runner that took a YAML spec is gone in 0.12 — webhook handlers now construct the task directly).

## OpenCode connection

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_OPENCODE_URL` | `http://127.0.0.1:4096` | OpenCode HTTP base URL. OpenLoom connects at startup and on every refresh. |
| `OPENLOOM_OPENCODE_USERNAME` | `opencode` | Basic auth user. Only used if `OPENLOOM_OPENCODE_PASSWORD` is non-empty. |
| `OPENLOOM_OPENCODE_PASSWORD` | *(empty)* | Basic auth password. If empty, no auth header is sent. |

The same `OPENLOOM_*` prefix is intentional — do not commit these. They are read once at startup; the same `Settings` object is used for the lifetime of the process.

## Storage and web server

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_DATABASE` | `.openloom/openloom.sqlite3` | Path to the SQLite task store. Relative paths are resolved against the process cwd at startup. |
| `OPENLOOM_UI_HOST` | `127.0.0.1` | Bind address for the web dashboard. |
| `OPENLOOM_UI_PORT` | `55413` | Bind port for the web dashboard. |

The CLI flags `--host` and `--port` override `OPENLOOM_UI_HOST` and `OPENLOOM_UI_PORT` at startup. Override happens after the env vars are read, so any webhook consumers see consistent values.

## Task budgets

Soft caps applied at the harness level (per task, from creation time).

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_MAX_TASK_TOKENS` | *(unset)* | Token cap derived from OpenCode's `/session/stats` endpoint. A task that exceeds this is marked failed at its next check. |
| `OPENLOOM_MAX_TASK_RUNTIME_MINUTES` | *(unset)* | Wall-clock minutes since the task was created. A task that exceeds this is marked failed at its next check. |

Both checks are evaluated at the same cadence as the harness's poll loop (default 8 s). A long-running but progressing task is **not** interrupted mid-tool; the harness only fails the task on the next check.

## Notifications

Webhook is the only delivery path in 0.12. The previous file-based notification sink is removed.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_NOTIFY_WEBHOOK_URLS` | *(unset)* | Comma- or space-separated list of webhook URLs. Each becomes a `WebhookSink`. |
| `OPENLOOM_NOTIFY_WEBHOOK_EVENTS` | `*` | Optional comma- or space-separated list of event names to forward. Default forwards every event. |
| `OPENLOOM_NOTIFY_RECENT_MESSAGES` | `3` | How many of the latest assistant messages to include in `data.recent_activity` on every event. Each entry is truncated (1 000 chars of text, 80 chars of tool input) so the webhook stays well under 40 KB even for verbose agents. |

Payload schema and event types: [notifications.md](notifications.md).

## Removed in 0.12

The following env vars are gone in 0.12 and will be silently ignored if set:

| Removed var | Reason |
|-------------|--------|
| `OPENLOOM_INBOX_DIR` | File-inbox dispatch removed. The dashboard no longer reads a directory. |
| `OPENLOOM_INBOX_FILENAME` | Same. |
| `OPENLOOM_INBOX_POLL_SECONDS` | Same. |
| `OPENLOOM_INBOX_DEFAULT_WORKSPACE` | Same. |
| `OPENLOOM_INBOX_DEFAULT_SESSION` | Same. |
| `OPENLOOM_STALE_BUSY_CHECKS` | Stale-busy detection removed. The dashboard no longer shows a "stuck" pill. |
| `OPENLOOM_NOTIFY_FILE_DIRS` | File notification sink removed. Webhook is the only delivery path. |
| `OPENLOOM_NOTIFY_FILE_PREFIX` | Same. |
| `OPENLOOM_NOTIFY_FILE_EVENTS` | Same. |

If you are migrating from 0.11, drop these from your deployment scripts.
