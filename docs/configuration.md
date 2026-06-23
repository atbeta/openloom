# Configuration

OpenLoom 0.14 reads configuration from **two** sources, in priority order
(highest wins):

1. **`OPENLOOM_*` environment variables** — the deployment layer.
2. **YAML config file** — persistent user-level defaults. Searched at:
   - `./openloom.yaml` (project-level override)
   - `~/.openloom/config.yaml` (user-level default)

If neither is set, OpenLoom falls back to built-in defaults. Env vars
overriding file values follows the 12-factor convention.

## File format

```yaml
# ~/.openloom/config.yaml
opencode:
  url: http://127.0.0.1:4096
  username: opencode
  # password stays in OPENLOOM_OPENCODE_PASSWORD env var — never in a file

ui:
  host: 127.0.0.1
  port: 55413

database: .openloom/openloom.sqlite3

harness:
  check_interval_seconds: 30
  idle_completes_task: true
  auto_accept_permissions: true
  notify_recent_messages: 3

notify:
  webhook:
    - url: https://your-system.com/hook
      events: [TASK_COMPLETED, TASK_FAILED]
      signing_secret: ""
      max_retries: 3
```

Passwords and other secrets should stay in env vars even though the YAML
file is user-private; the loader will not read `opencode.password` from
the file even if present.

## OpenCode connection

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_OPENCODE_URL` | `http://127.0.0.1:4096` | OpenCode HTTP base URL. OpenLoom connects at startup and on every refresh. |
| `OPENLOOM_OPENCODE_USERNAME` | `opencode` | Basic auth user. Only used if `OPENLOOM_OPENCODE_PASSWORD` is non-empty. |
| `OPENLOOM_OPENCODE_PASSWORD` | *(empty)* | Basic auth password. If empty, no auth header is sent. **Env-only — never put this in `openloom.yaml`.** |

The same `OPENLOOM_*` prefix is intentional — do not commit these. They
are read once at startup; the same `Settings` object is used for the
lifetime of the process.

## Storage and web server

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_DATABASE` | `.openloom/openloom.sqlite3` | Path to the SQLite task store. Relative paths are resolved against the process cwd at startup. |
| `OPENLOOM_UI_HOST` | `127.0.0.1` | Bind address for the web dashboard. |
| `OPENLOOM_UI_PORT` | `55413` | Bind port for the web dashboard. |

The CLI flags `--host` and `--port` override `OPENLOOM_UI_HOST` and
`OPENLOOM_UI_PORT` at startup. Override happens after the env vars are
read, so any webhook consumers see consistent values.

## Notifications

Webhook is the only delivery path.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_NOTIFY_WEBHOOK_URLS` | *(unset)* | Comma- or space-separated list of webhook URLs. Each becomes a `WebhookSink`. |
| `OPENLOOM_NOTIFY_WEBHOOK_EVENTS` | `*` | Optional comma- or space-separated list of event names to forward. Default forwards every event. |
| `OPENLOOM_NOTIFY_RECENT_MESSAGES` | `3` | How many of the latest assistant messages to include in `data.recent_activity` on every event. Each entry is truncated (1 000 chars of text, 80 chars of tool input) so the webhook stays well under 40 KB even for verbose agents. |

### Connecting to openloom-connector

The recommended consumer is
[openloom-connector](https://github.com/atbeta/openloom-connector) —
it accepts OpenLoom's outbound events at a fixed URL and writes
status / result files to whatever storage backend you plug in. The
integration is one env var:

```bash
export OPENLOOM_NOTIFY_WEBHOOK_URLS='http://127.0.0.1:55414/listener/openloom'
```

The connector's listener address is hardcoded and the listener is
always on when the connector process is running — see the
[connector README](https://github.com/atbeta/openloom-connector) for
the connector's own config surface.

### Receiver hardening

- Webhooks are POSTed on a background thread so the harness never
  blocks on a slow consumer.
- OpenLoom ignores system proxies (`HTTP_PROXY` / `HTTPS_PROXY`) when
  the URL host is `127.0.0.1` / `localhost`. This avoids the
  "Content Filter - Access Denied" trap on corporate machines with a
  VPN proxy.
- Each URL is retried with exponential backoff (1 s → 4 s → 16 s by
  default), overridable via the YAML `notify.webhook[].max_retries`.

## Harness behaviour

These are environment-variable-only — too small to warrant a YAML
section.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENLOOM_IDLE_COMPLETES_TASK` | `true` | When the agent session is idle but has produced at least one assistant message, treat that as task completion. Set to `false` to require an explicit `TASK COMPLETE` marker. Bootstrap framing (see below) makes this mostly automatic — the harness stays in the loop for safety. |
| `OPENLOOM_AUTO_ACCEPT_PERMISSIONS` | `true` | Auto-answer every pending tool-permission prompt with OpenCode's "once" reply. Webhook / connector users are usually remote and cannot drive the dashboard to click "Allow" — leaving this off means tasks stay stuck in `waiting` until somebody logs into the UI. Set to `false` to keep the previous behaviour and route every permission through `POST /api/sessions/{id}/permissions/{perm_id}` for manual approval. |
| `OPENLOOM_CHECK_INTERVAL_SECONDS` | `30` | How often the harness polls OpenCode for each task's session status + last few messages. Clamped to `[1, 3600]`. Default raised from 8 since 0.13.6: friendlier on OpenCode when many tasks run in parallel and gives the connector's status-file throttle enough headroom that a phone only sees meaningful transitions rather than every tick. |

### Bootstrap framing (0.13.6+)

Every prompt the harness sends to OpenCode gets a short completion-protocol
suffix appended automatically:

```
<your goal>

---
When you have finished the task and verified the result is correct,
include a line containing exactly: TASK COMPLETE
Do not include that line until the work is actually done.
```

This is what makes the `TASK_COMPLETE` marker (and the
`OPENLOOM_IDLE_COMPLETES_TASK` fallback) actually useful — without
it, the agent has no way to know what the harness is waiting for.

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

## Removed in 0.14

The YAML file supports `opencode.url`, `opencode.username`,
`ui.host`, `ui.port`, `database`, `harness.*` (check_interval_seconds,
idle_completes_task, auto_accept_permissions, notify_recent_messages),
and `notify.webhook[*]`. The `opencode.password` field is read from
env vars only — never from the YAML file, even if present. This is
intentional: secrets don't belong in user-private config files that
might end up in shell history or backups.
