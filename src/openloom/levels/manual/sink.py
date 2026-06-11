from __future__ import annotations

import sys

from openloom.core.events import Event, EventType
from openloom.core.registry import register_sink
from openloom.core.sink import Sink


@register_sink("console")
class ConsoleSink(Sink):
    def __init__(self) -> None:
        self._task_names: dict[str, str] = {}

    def on_event(self, event: Event) -> None:
        if event.type == EventType.TASK_CREATED:
            name = event.data.get("spec", {}).get("name", event.task_id)
            self._task_names[event.task_id] = name
            print(f"[{event.task_id[:12]}] CREATED  {name}")

        elif event.type == EventType.TASK_STARTED:
            name = self._task_names.get(event.task_id, event.task_id[:12])
            sid = event.data.get("session_id", "")[:12]
            print(f"[{event.task_id[:12]}] STARTED  {name}  session={sid}")

        elif event.type == EventType.TASK_UPDATED:
            name = self._task_names.get(event.task_id, event.task_id[:12])
            summary = event.data.get("summary", "")
            progress = event.data.get("progress", 0)
            print(f"[{event.task_id[:12]}] {event.data.get('status', '?')}  {name}  p={progress:.0%}  {summary}")

        elif event.type == EventType.TASK_COMPLETED:
            name = self._task_names.get(event.task_id, event.task_id[:12])
            print(f"[{event.task_id[:12]}] COMPLETE  {name}  {event.data.get('summary', '')}")

        elif event.type == EventType.TASK_FAILED:
            name = self._task_names.get(event.task_id, event.task_id[:12])
            error = event.data.get("error", event.data.get("summary", ""))
            print(f"[{event.task_id[:12]}] FAILED   {name}  {error}")

        elif event.type == EventType.LOG_LINE:
            print(f"[{event.task_id[:12]}] LOG      {event.data.get('summary', '')}")

        sys.stdout.flush()
