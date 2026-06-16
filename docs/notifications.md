# Notifications

OpenLoom emits a stream of events for every task lifecycle change. Configure webhook URLs in the environment to receive them as JSON POSTs.

## Configure

```bash
# Single URL
export OPENLOOM_NOTIFY_WEBHOOK_URLS='https://hooks.example.com/openloom'

# Multiple URLs (comma- or space-separated)
export OPENLOOM_NOTIFY_WEBHOOK_URLS='https://hooks.slack.com/x1,https://hooks.discord.com/x2'

# Optional: only forward specific events
export OPENLOOM_NOTIFY_WEBHOOK_EVENTS='TASK_COMPLETED,TASK_FAILED'
```

Webhooks receive a POST per event. Default timeout is 3 seconds; failures are logged but do not block the harness.

## Event types

| Event | When |
|-------|------|
| `TASK_CREATED` | A new task was added to the store (POST /api/tasks). |
| `TASK_STARTED` | The harness bound the task to an OpenCode session and sent the first user turn. |
| `TASK_UPDATED` | A periodic poll observed a status / progress / message change. Fires roughly every 8 seconds while a task is running. |
| `TASK_COMPLETED` | The agent's last assistant turn contained the marker text `TASK COMPLETE` (case-insensitive). The harness does not interpret completion any other way — agents that never emit the marker stay in `running`. |
| `TASK_FAILED` | Unrecoverable error (budget exceeded, session lost, harness check raised). |

The full lifecycle of a single task is therefore `TASK_CREATED → TASK_STARTED → N × TASK_UPDATED → TASK_COMPLETED` (or `TASK_FAILED`).

## Payload schema

```json
{
  "event": "TASK_COMPLETED",
  "task_id": "task_abc123",
  "task_name": "Fix the type error",
  "timestamp": 1749999999.123,
  "timestamp_iso": "2026-06-15T00:00:00Z",
  "store_version": 42,
  "data": {
    "status": "completed",
    "progress": 1.0,
    "summary": "Agent reported TASK COMPLETE",
    "recent_activity": [
      {
        "text": "All three steps are now green.",
        "completed_at": 1749999990.0,
        "tools": [
          {"tool": "bash", "status": "completed",
           "input_excerpt": "pytest -x"}
        ]
      }
    ],
    "active_session_id": "ses_xyz789"
  }
}
```

### `task_name` and `timestamp_iso`

Always present. `task_name` is the human-readable label set by the webhook handler (default: "Untitled task"). `timestamp_iso` is the same epoch as `timestamp` but rendered as `2026-06-15T00:00:00Z` for display in webhook handlers that don't want to format a float themselves.

### `data.recent_activity`

A list of the last `OPENLOOM_NOTIFY_RECENT_MESSAGES` (default 3) assistant messages from the session transcript. Each entry is:

```json
{
  "text": "<truncated to 1000 chars>",
  "completed_at": <float epoch, 0 if unknown>,
  "tools": [
    {"tool": "bash", "status": "completed",
     "input_excerpt": "<input JSON, truncated to 80 chars>"}
  ]
}
```

The list is the right size to fit in a Slack or Discord webhook payload even when the agent is mid-investigation: ~3 KB per event. Tool input is JSON-serialised so file paths and command snippets survive intact, and the truncation is marked with `…` so the receiver can tell the input was clipped. Tool **output is omitted on purpose** — it is unbounded and rarely what a remote operator needs.

The field is best-effort: harness events emitted from the periodic check path include it; rare paths that do not have access to the message log (the manual `pause_task` / `complete_task` API calls, the catch-all `tick()` exception handler) omit it. The shape is always a JSON list, possibly empty.

### `data.active_session_id` (or `data.session_id` on `TASK_STARTED` / `TASK_CREATED`)

Always present on `TASK_UPDATED` / `TASK_COMPLETED`. The TASK_STARTED event uses the key name `data.session_id` (because it carries the fresh session id at creation time, not the post-bind id). Omitted on `TASK_FAILED` (the harness only knows the task failed; it has not had a chance to read the session id from the store). `null` for a task with no session binding (rare — the dashboard will only show one of those for completed tasks).

## Detecting a stuck session

0.12 removed the `SESSION_STALE_BUSY` event. The webhook will not tell you "this session has been busy for 10 minutes" — instead, the `TASK_UPDATED` events simply stop arriving for a task whose session is busy but not making progress (the harness still emits them, but the recent_activity field will be empty or the same as the previous event).

To detect a stuck session, the consumer should:

1. Watch `TASK_UPDATED` events for a given `task_id`.
2. Compare consecutive `data.recent_activity[*].completed_at` timestamps.
3. If the timestamps are not advancing for 60+ seconds, treat the task as stuck.
4. `POST /api/tasks/{id}/abort` to break the agent loop.
5. `POST /api/tasks` with the same `sessionId` and a new `goal` to take over.

This puts the "is it stuck?" decision on the webhook consumer, which knows its own latency tolerances better than a hard-coded server-side threshold.

## Webhook delivery semantics

`WebhookSink` uses a single long-lived `httpx.Client` per URL. The default timeout is 3 s; failures are logged but do not block the harness. A successful delivery requires a `2xx` response; `4xx` and `5xx` are logged as warnings.

The `X-OpenLoom-Event` header carries the event name for routing on the receiving end. Body is JSON.

## Example: full task lifecycle as a webhook receiver

```python
# A minimal Flask receiver that logs every event to stdout
import json
from flask import Flask, request

app = Flask(__name__)

@app.post("/openloom")
def receive():
    event = request.json
    print(f"[{event['event']}] {event['task_name']} (store v{event['store_version']})")
    if event["event"] == "TASK_UPDATED":
        for entry in event["data"].get("recent_activity", []):
            if entry["text"]:
                print(f"  agent: {entry['text'][:80]}")
            for tool in entry["tools"]:
                print(f"  tool:  {tool['tool']} {tool['status']} {tool['input_excerpt']!r}")
    return "", 200
```
