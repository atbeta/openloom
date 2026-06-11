from __future__ import annotations

import json
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
    import asyncio

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
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": json.dumps({"store_version": store.store_version})}
        except asyncio.CancelledError:
            pass
        finally:
            web_sink.unsubscribe(queue)

    return EventSourceResponse(generate())


def archive_task(store: Any, task_id: str) -> dict[str, Any]:
    task = store.get_task(task_id)
    if not task:
        return {"ok": False, "error": "not found"}
    sv = store.update_task(task_id, status="archived", next_check_at=None)
    store.append_check_log(task_id, status="archived", summary="Archived manually")
    return {"ok": True, "task_id": task_id, "store_version": sv}
