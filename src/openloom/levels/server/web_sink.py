from __future__ import annotations

import asyncio
from typing import Any

from openloom.core.events import Event, iso_utc
from openloom.core.registry import register_sink
from openloom.core.sink import Sink


@register_sink("web")
class WebSink(Sink):
    """Server-sent event fan-out for the web dashboard.

    The dashboard subscribes via ``/api/events``; each subscriber
    gets a bounded queue and the sink drops events for slow
    consumers (the dashboard reconnects and asks for a snapshot
    via ``/api/state`` on the next refresh tick).
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[dict[str, Any]]] = []

    def on_event(self, event: Event) -> None:
        payload = {
            "type": event.type.name,
            "task_id": event.task_id,
            "task_name": event.task_name,
            "timestamp": event.timestamp,
            "timestamp_iso": iso_utc(event.timestamp),
            "store_version": event.store_version,
            "data": event.data,
        }
        dead: list[int] = []
        for i, q in enumerate(self._queues):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(i)
        for i in reversed(dead):
            self._queues.pop(i)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass
