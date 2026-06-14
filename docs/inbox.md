# Inbox — remote task dispatch

The inbox lets you dispatch tasks to OpenLoom from anywhere a file can land (or an HTTP request can be made). The same `TaskSpec` is used whether the dispatch comes from the local CLI, a file drop, or a webhook.

## Enable the inbox

```bash
export OPENLOOM_INBOX_DIR=/path/to/inbox
openloom serve     # or openloom watch
```

The directory is created if missing. A polling loop (default 30 s) looks for `task.md` in the directory, parses it, and creates a task. After consumption, the file is renamed to `task.md.processed-<task-id>` so it is not picked up again.

You can also override the consumed filename and poll interval:

```bash
export OPENLOOM_INBOX_FILENAME=inbox.md
export OPENLOOM_INBOX_POLL_SECONDS=15
```

## Markdown schema

```markdown
# Task title               ← falls back to "Untitled task"

workspace: /path/to/proj   ← optional if OPENLOOM_INBOX_DEFAULT_WORKSPACE is set
mode: normal               ← normal | loop (reserved for future)
agent: opencode            ← opencode | <custom agent name>
check_interval: 5m         ← minimum 5 minutes; same semantics as in YAML

session: ses_abc           ← optional; bind to an existing OpenCode session
abort: true                ← optional; abort the session before sending (see below)

## goal
Free-form goal text. Used as the bootstrap prompt for the agent.

## acceptance
- [ ] Acceptance criteria for the overall task

## steps
1. First step
2. Second step
3. Third step
```

Frontmatter keys (`workspace`, `mode`, `agent`, `check_interval`, `session`, `abort`) are parsed from the first 20 lines of the file. The body is `## goal`, `## acceptance`, and `## steps` sections. Lines starting with `# ` become the task name; lines under `## acceptance` and `## steps` become list items.

## Session binding

When the markdown carries `session: <id>`, OpenLoom does not create a new OpenCode session — it appends a new turn to the existing session's transcript. This is the "continue where we left off" flow.

If the markdown omits `session:`, OpenLoom falls back to `OPENLOOM_INBOX_DEFAULT_SESSION` from the environment. Otherwise it creates a fresh session bound to the resolved `workspace:`.

The dispatch entry points look for these in priority order:

1. `session: <id>` in the markdown frontmatter
2. `sessionId` field on the `/api/inbox/trigger` request body
3. `OPENLOOM_INBOX_DEFAULT_SESSION` environment variable
4. *no session* — OpenLoom creates one and the task runs in a fresh transcript

## Abort and resume

When a session has been busy for `OPENLOOM_STALE_BUSY_CHECKS` consecutive checks with no new completed message (the [stale-busy](notifications.md#session_stale_busy) detector), a `SESSION_STALE_BUSY` event is emitted. The typical recovery from a phone or remote shell:

```markdown
# Resume the long task

session: ses_abc
abort: true
workspace: /path/to/proj

## goal
Stop the hung npm install and continue with the type check.
```

`abort: true` is opt-in. When set, the harness:

1. Validates the session id still exists (a typo fails fast with `Session <id> no longer exists`).
2. Calls `POST /session/<id>/abort` to release any in-flight tool the agent is blocked on.
3. Calls `POST /session/<id>/prompt_async` with the new goal so the agent picks it up immediately.

Abort failure is a soft signal — if the session was already idle, OpenCode returns 404/409, the harness logs a `LOG_LINE` event, and the prompt is still sent. The new prompt always lands.

The flag has zero effect on the regular `openloom watch` path or on inbox dispatches that omit `abort:` — they keep appending, never interrupting.

## HTTP trigger

The same flow is reachable over HTTP for callers that cannot write into the inbox directory (CI runners, phone push notifications, etc.):

```bash
curl -X POST http://127.0.0.1:55413/api/inbox/trigger \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "# Resume\n\nsession: ses_abc\nabort: true\nworkspace: /path\n\n## goal\nContinue.",
    "defaultWorkspace": "/path"
  }'
```

Request body:

| Field | Required | Purpose |
|-------|----------|---------|
| `text` | one of | Raw markdown body, parsed by the same parser as the file watcher. |
| `path` | one of | Absolute path to a markdown file on disk. The server reads and parses it. |
| `sessionId` | optional | Overrides the markdown `session:` frontmatter when both are set. |
| `defaultWorkspace` | optional | Fallback `workspace:` for markdown that omits it. |

Response:

```json
{
  "ok": true,
  "taskId": "task_a1b2c3d4e5f6",
  "name": "Resume",
  "status": "pending",
  "source": "text",
  "sessionId": "ses_abc"
}
```

If both `text` and `path` are present, or neither is, the request is rejected with `400` and a `detail` message.

## What it looks like in the dashboard

When the inbox is enabled, a compact icon card labelled **Inbox** appears above the Tasks table:

- **off** (grey ring, "OFF" status) — `OPENLOOM_INBOX_DIR` is unset
- **on** (green ring, "ON" status) — directory, filename, and poll interval are listed in the detail line; if `OPENLOOM_INBOX_DEFAULT_SESSION` is set, it appears as `session:<first 12 chars>`

The card's detail line is rendered inline (not behind a hover tooltip) so the configuration is always visible.
