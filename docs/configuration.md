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

0.12 does **not** enforce token or runtime caps server-side. The harness polls every 8 seconds and emits `TASK_UPDATED` until the agent reports `TASK COMPLETE` (or you `POST /api/tasks/{id}/abort` to break a stuck loop). A long-running but progressing task is left alone; the webhook consumer decides what counts as "too long".

If you need a hard cap, do it on the webhook side: count `TASK_UPDATED` events with no new `recent_activity[*].completed_at` advance for N consecutive checks, then call `abort` from your handler.

## Notifications

Webhook is the only delivery path in 0.12. The previous file-based notification sink is removed.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_NOTIFY_WEBHOOK_URLS` | *(unset)* | Comma- or space-separated list of webhook URLs. Each becomes a `WebhookSink`. |
| `OPENLOOM_NOTIFY_WEBHOOK_EVENTS` | `*` | Optional comma- or space-separated list of event names to forward. Default forwards every event. |
| `OPENLOOM_NOTIFY_RECENT_MESSAGES` | `3` | How many of the latest assistant messages to include in `data.recent_activity` on every event. Each entry is truncated (1 000 chars of text, 80 chars of tool input) so the webhook stays well under 40 KB even for verbose agents. |
| `OPENLOOM_IDLE_COMPLETES_TASK` | `true` | When the agent session is idle but has produced at least one assistant message, treat that as task completion. Set to `false` to require an explicit `TASK COMPLETE` marker. The harness layer will introduce more nuanced retry / nudge controls and may revisit this default. |
| `OPENLOOM_AUTO_ACCEPT_PERMISSIONS` | `true` | Auto-answer every pending tool-permission prompt with OpenCode's "once" reply. Webhook / connector users are usually remote and cannot drive the dashboard to click "Allow" — leaving this off means tasks stay stuck in `waiting` until somebody logs into the UI. Set to `false` to keep the previous behaviour and route every permission through `POST /api/sessions/{id}/permissions/{perm_id}` for manual approval. |

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
| `OPENLOOM_MAX_TASK_TOKENS` | Token budget was defined in 0.11 but never enforced server-side. Removed to avoid suggesting a feature that did not exist. |
| `OPENLOOM_MAX_TASK_RUNTIME_MINUTES` | Same. |

If you are migrating from 0.11, drop these from your deployment scripts.
