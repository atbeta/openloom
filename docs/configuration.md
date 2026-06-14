# Configuration

OpenLoom is configured entirely through environment variables. There are no config files (besides `openloom.yaml` for a single watched spec, and inbox markdown files for remote dispatch).

## OpenCode connection

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_OPENCODE_URL` | `http://127.0.0.1:4096` | OpenCode HTTP base URL. OpenLoom connects at startup and on every refresh. |
| `OPENLOOM_OPENCODE_USERNAME` | `opencode` | Basic auth user. Only used if `OPENLOOM_OPENCODE_PASSWORD` is non-empty. |
| `OPENLOOM_OPENCODE_PASSWORD` | *(empty)* | Basic auth password. If empty, no auth header is sent. |

> The same `OPENLOOM_*` prefix is intentional — do not commit these. They are read once at startup; the same `Settings` object is used for the lifetime of the process.

## Storage and web server

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_DATABASE` | `.openloom/openloom.sqlite3` | Path to the SQLite task store. Relative paths are resolved against the process cwd at startup. |
| `OPENLOOM_UI_HOST` | `127.0.0.1` | Bind address for the web dashboard. |
| `OPENLOOM_UI_PORT` | `55413` | Bind port for the web dashboard. |

## Task budgets

Soft caps applied at the harness level (per task, from creation time).

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_MAX_TASK_TOKENS` | *(unset)* | Token cap derived from OpenCode's `/session/stats` endpoint. A task that exceeds this is marked failed at its next check. |
| `OPENLOOM_MAX_TASK_RUNTIME_MINUTES` | *(unset)* | Wall-clock minutes since the task was created. A task that exceeds this is marked failed at its next check. |

Both checks are evaluated at the same cadence as the task's `check_interval`. A long-running but progressing task is **not** interrupted mid-tool — the harness only fails the task on the next check tick.

## Inbox

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_INBOX_DIR` | *(unset)* | Enables the inbox. Path to a directory OpenLoom polls. The directory is created if it does not exist. |
| `OPENLOOM_INBOX_FILENAME` | `task.md` | Single-filename mode. OpenLoom looks for this exact filename in `INBOX_DIR`. After consumption, the file is renamed to `<filename>.processed-<task-id>`. |
| `OPENLOOM_INBOX_POLL_SECONDS` | `30.0` | Polling interval (clamped to a minimum of 1.0). |
| `OPENLOOM_INBOX_DEFAULT_WORKSPACE` | *(unset)* | Fallback `workspace:` when the markdown omits it. |
| `OPENLOOM_INBOX_DEFAULT_SESSION` | *(unset)* | Fallback `session:` id when the markdown omits it. Used to bind inbox tasks to a long-lived session by default. |

Without `OPENLOOM_INBOX_DIR` the inbox dispatcher is not started and the inbox card in the dashboard reads `Off`.

See [inbox.md](inbox.md) for the full markdown schema, including the `session:` and `abort:` frontmatter keys.

## Notifications

Webhook and file sinks receive every harness event by default; you can filter by event name.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_NOTIFY_WEBHOOK_URLS` | *(unset)* | Comma- or space-separated list of webhook URLs. Each becomes a `WebhookSink`. |
| `OPENLOOM_NOTIFY_WEBHOOK_EVENTS` | *(all)* | Optional comma- or space-separated list of event names to forward. Default forwards every event. |
| `OPENLOOM_NOTIFY_FILE_DIRS` | *(unset)* | Comma- or space-separated list of directory paths. Each becomes a `FileSink`. |
| `OPENLOOM_NOTIFY_FILE_PREFIX` | `openloom` | Filename prefix for file-sink outputs. |

Example: forward only task lifecycle and stale-busy events to Slack, full stream to disk:

```bash
export OPENLOOM_NOTIFY_WEBHOOK_URLS='https://hooks.slack.com/services/...'
export OPENLOOM_NOTIFY_WEBHOOK_EVENTS='TASK_COMPLETED,TASK_FAILED,SESSION_STALE_BUSY'

export OPENLOOM_NOTIFY_FILE_DIRS=/var/log/openloom
```

See [notifications.md](notifications.md) for payload schema, retry behaviour, and the list of event types.

## Stale-busy detection

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_STALE_BUSY_CHECKS` | `10` | Number of consecutive monitor refreshes (≈ refresh interval, default 8 s) during which a session has been busy with no new completed message before `SESSION_STALE_BUSY` is emitted. |

The probe path runs for every busy session on every refresh, so a long-running tool that *is* making progress (e.g. `npm install` advancing the `completed` timestamp of an assistant message every few seconds) will not be treated as stuck — the counter resets whenever fresh progress is observed.

## CLI flags

OpenLoom's CLI subcommands also accept flags. See the on-disk help:

```bash
openloom init --help
openloom watch --help
openloom serve --help
```

Notable flags:

| Command | Flag | Purpose |
|---------|------|---------|
| `watch` | `--ui` | Run the same harness with a local web UI (requires `[ui]`). |
| `watch` | `--verbose` | Set log level to `DEBUG` for the harness and the OpenCode client. |
| `serve` | `--host` | Override `OPENLOOM_UI_HOST`. |
| `serve` | `--port` | Override `OPENLOOM_UI_PORT`. |
| `init` | `--path` | Custom path for the generated `openloom.yaml`. |
