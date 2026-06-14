# Notifications

OpenLoom emits events for every state change in the harness (task lifecycle, log lines, **session-stale detection**). You can fan those events out to webhooks, files on disk, or both.

## Configure

```bash
# Webhook — one POST per event, per URL
export OPENLOOM_NOTIFY_WEBHOOK_URLS='https://hooks.example.com/openloom'

# File sink — one JSON file per event, per directory
export OPENLOOM_NOTIFY_FILE_DIRS=/var/log/openloom
export OPENLOOM_NOTIFY_FILE_PREFIX=openloom
```

Multiple URLs / directories are space- or comma-separated. Each becomes an independent sink — the same event fans out to all of them. By default each sink forwards every event type.

To filter, set `OPENLOOM_NOTIFY_WEBHOOK_EVENTS` to a comma- or space-separated list of event names. File sinks are filtered by filename pattern at the directory level, so set one directory per filter if you want a strict partition.

## Event types

| Event | When |
|-------|------|
| `TASK_CREATED` | A new task was added to the store. `data` includes the parsed spec and workspace. |
| `TASK_STARTED` | The harness started the first turn of a task. |
| `TASK_UPDATED` | Progress, status, or step change. `data` includes `summary` and optional `error`. |
| `TASK_COMPLETED` | The agent reported completion (or auto-archive). |
| `TASK_FAILED` | Budget exceeded, session lost, or other unrecoverable error. |
| `LOG_LINE` | Notable log line (e.g. `session abort failed`). |
| `SESSION_STALE_BUSY` | A session has been busy for `OPENLOOM_STALE_BUSY_CHECKS` consecutive checks with no new completed message. **One-shot per stuck episode.** |

## Payload schema

All sinks receive the same JSON shape:

```json
{
  "event": "TASK_COMPLETED",
  "task_id": "task_a1b2c3d4e5f6",
  "timestamp": 1749999999.123,
  "store_version": 42,
  "data": {
    "summary": "Agent reported TASK COMPLETE",
    "step_done": 3,
    "step_count": 3
  }
}
```

For session-level events, `task_id` is `""` and the subject lives in `data`:

```json
{
  "event": "SESSION_STALE_BUSY",
  "task_id": "",
  "timestamp": 1749999999.123,
  "store_version": 0,
  "data": {
    "session_id": "ses_abc",
    "title": "Resume the long task",
    "directory": "/Users/you/project",
    "consecutive_busy_checks": 10,
    "threshold_checks": 10,
    "stuck_for_seconds": 84
  }
}
```

## `SESSION_STALE_BUSY`

This event is the linchpin of the "notice from anywhere" loop. It fires when a session has been observed busy on the OpenCode status map (or detected as busy by a recent-messages probe) for `OPENLOOM_STALE_BUSY_CHECKS` consecutive monitor refreshes, with **no new completed message** in that window.

A long-running but progressing tool (`npm install`, `docker build`) advances the latest `time.completed` timestamp on every refresh, so the counter resets and the event does not fire. Only a session that stays busy *with no progress* — typically one blocked on a hung child process — crosses the threshold.

The event is **one-shot per stuck episode**: once the session goes idle or shows fresh progress, the latch releases so a future stuck episode can fire again. This stops the webhook from flooding.

### Recovery flow

The typical recovery is the [abort-and-resume](inbox.md#abort-and-resume) flow:

1. `SESSION_STALE_BUSY` hits your webhook / phone.
2. You write a markdown with `session: <id>` and `abort: true` into the inbox directory.
3. OpenLoom aborts the in-flight tool and sends your new goal as the next turn.
4. The agent picks up your new task immediately.

## Webhook delivery

`WebhookSink` uses a single long-lived `httpx.Client` per URL. The default timeout is 3 s; failures are logged but do not block the harness. Successful delivery requires a `2xx` response; `4xx` and `5xx` are logged as warnings.

The `X-OpenLoom-Event` header carries the event name for routing on the receiving end. Body is JSON.

## File sink delivery

`FileSink` writes one JSON file per event into the configured directory:

```
openloom-TASK_COMPLETED-20260514T120000-123.json
openloom-SESSION_STALE_BUSY-20260514T120100-004.json
```

Filename pattern: `<prefix>-<EVENT_NAME>-<UTC ISO 8601>-<ms suffix>.json`. The millisecond suffix keeps ordering unique when multiple events land in the same second.

Files are written atomically (single `path.write_text()`); a write failure logs a warning and the event is dropped (no retry queue). The directory is created at startup if it does not exist.

## Where events are produced

All state changes flow through the event bus. API routes read the store; the bus pushes notifications only. There is no in-band coupling between `notify` and the harness — adding or removing sinks does not affect task scheduling.

This means:

- If you disable webhooks, the harness still runs identically.
- If a webhook is slow / down, the harness loop is not blocked.
- Sinks are cold-detected: they are imported lazily by `openloom/levels/notify/__init__.py:build_sinks`, so a missing optional dep does not break the core.
