from __future__ import annotations

import asyncio
import json
import time
from typing import Any


def tasks_list(store: Any) -> dict[str, Any]:
    tasks = store.list_tasks()
    return {"tasks": tasks, "store_version": store.store_version}


def task_detail(store: Any, task_id: str) -> dict[str, Any]:
    task = store.get_task(task_id)
    if not task:
        return {"error": "not found"}
    return {"task": task, "store_version": store.store_version}


async def event_stream(store: Any, web_sink: Any):

    from sse_starlette.sse import EventSourceResponse

    async def generate():
        tasks = store.list_tasks()
        snapshot = {"type": "snapshot", "store_version": store.store_version, "tasks": tasks}
        yield {"event": "snapshot", "data": json.dumps(snapshot, default=str)}

        queue = web_sink.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield {"event": "task", "data": json.dumps(event, default=str)}
                except TimeoutError:
                    yield {"event": "heartbeat", "data": json.dumps({"store_version": store.store_version})}
        except asyncio.CancelledError:
            pass
        finally:
            web_sink.unsubscribe(queue)

    return EventSourceResponse(generate())


async def abort_task(client: Any, harness: Any, task_id: str) -> dict[str, Any]:
    """POST /api/tasks/{id}/abort — release any in-flight agent
    loop on the task's session via OpenCode's POST /session/{id}/abort.

    Used by webhook handlers to break a stuck session before sending
    a follow-up prompt. Returns 404 if the task or session is gone.
    """
    task = harness.get_task(task_id)
    if not task:
        return {"error": "task not found"}
    session_id = str(task.get("active_session_id") or "").strip()
    if not session_id:
        return {"error": "task has no active session"}
    if not hasattr(client, "abort_session"):
        return {"error": "client does not support abort"}
    ok = await client.abort_session(session_id)
    return {
        "ok": True,
        "taskId": task_id,
        "sessionId": session_id,
        "aborted": bool(ok),
    }


async def full_state(
    client: Any,
    store: Any,
    monitor: Any,
    recent: Any,
    settings: Any,
) -> dict[str, Any]:
    """Composite state for the Web UI dashboard (OpenDeck protocol compat)."""
    from openloom.runtime.session_status import (
        is_archived_session,
        is_visible_session,
        session_updated_at,
    )
    from openloom.runtime.telemetry import aggregate_usage_periods

    health = await client.health()
    tasks = store.list_tasks()
    sessions: list[dict[str, Any]] = []
    session_status: dict[str, str] = {}
    session_error: str | None = None

    if health.ok:
        try:
            sessions = await client.list_sessions()
            session_status = monitor.status
        except Exception as exc:  # noqa: BLE001
            session_error = str(exc)
            sessions = []

    try:
        archived_sessions = [s for s in sessions if is_archived_session(s)]
    except Exception:
        archived_sessions = []

    visible_sessions = sorted(
        [s for s in sessions if is_visible_session(s)],
        key=session_updated_at, reverse=True,
    )

    sessions_by_directory: dict[str, list[dict[str, Any]]] = {}
    for session in visible_sessions:
        directory = session.get("directory") or "(unknown)"
        sessions_by_directory.setdefault(directory, []).append(session)
    for path, items in sessions_by_directory.items():
        sessions_by_directory[path] = sorted(items, key=session_updated_at, reverse=True)
    unknown_sessions = sessions_by_directory.pop("(unknown)", None)
    ordered = sorted(
        sessions_by_directory.items(),
        key=lambda kv: max(session_updated_at(s) for s in kv[1]) if kv[1] else 0,
        reverse=True,
    )
    sessions_by_directory = dict(ordered)
    if unknown_sessions is not None:
        sessions_by_directory["(unknown)"] = unknown_sessions

    recent_workspaces = recent.list_workspaces() if recent is not None else []
    if recent is not None and not recent_workspaces:
        session_dirs = [
            s.get("directory") for s in sessions
            if s.get("directory") and is_visible_session(s)
        ]
        recent.seed_from_sessions(session_dirs)
        recent_workspaces = recent.list_workspaces()

    permissions: list[dict[str, Any]] = []
    if health.ok:
        try:
            permissions = await client.list_pending_permissions()
        except Exception:
            permissions = []

    webhooks: list[dict[str, Any]] = []
    for wh in settings.notify.webhooks:
        webhooks.append({
            "url": wh.url,
            "events": sorted(wh.events),
        })

    return {
        "server": {
            "ok": health.ok, "message": health.message,
            "statusCode": health.status_code, "url": settings.opencode_url,
            "username": settings.opencode_username,
        },
        "recentWorkspaces": recent_workspaces,
        "tasks": tasks,
        "sessions": visible_sessions,
        "sessionsByDirectory": sessions_by_directory,
        "archivedSessions": [
            {"id": s["id"], "title": s.get("title", ""),
             "directory": s.get("directory", ""),
             "archived_at": s.get("archived")}
            for s in archived_sessions
        ],
        "sessionStatus": session_status,
        "sessionError": session_error,
        "permissions": permissions,
        "notify": {"webhooks": webhooks},
        "metrics": _status_counts(tasks, session_status),
        "usage": aggregate_usage_periods(sessions, now=time.time()),
        "now": time.time(),
    }


def _status_counts(tasks: list[dict[str, Any]], session_status: dict[str, Any]) -> dict[str, int]:
    running = waiting = failed = completed = 0
    for task in tasks:
        status = str(task.get("status", "")).lower()
        if status == "waiting":
            waiting += 1
        elif status == "failed":
            failed += 1
        elif status in {"completed", "archived"}:
            completed += 1
        elif status in {"running", "pending"}:
            running += 1

    sessions_busy = sessions_idle = sessions_retry = 0
    for value in session_status.values():
        text = str(value.get("type") if isinstance(value, dict) else value).lower()
        if text in {"busy", "running", "streaming", "working"}:
            sessions_busy += 1
        elif text in {"retry", "waiting", "permission"}:
            sessions_retry += 1
        else:
            sessions_idle += 1

    return {
        "running": running,
        "waiting": waiting,
        "failed": failed,
        "completedToday": completed,
        "sessionsBusy": sessions_busy,
        "sessionsIdle": sessions_idle,
        "sessionsRetry": sessions_retry,
    }
