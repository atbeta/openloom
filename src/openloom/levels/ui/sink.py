from __future__ import annotations

import asyncio
import json
from typing import Any

from openloom.core.events import Event
from openloom.core.registry import register_sink
from openloom.core.sink import Sink


@register_sink("web")
class WebSink(Sink):
    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[dict[str, Any]]] = []

    def on_event(self, event: Event) -> None:
        payload = {
            "type": event.type.name,
            "task_id": event.task_id,
            "timestamp": event.timestamp,
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
            del self._queues[i]

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass
