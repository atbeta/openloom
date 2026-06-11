from __future__ import annotations

import asyncio
import sys
from typing import Any

from openloom.core.events import Event, EventType
from openloom.core.harness import HarnessRunner
from openloom.runtime import prompts, session_status
from openloom.runtime.opencode import OpenCodeClient
from openloom.runtime.prompts import parse_task_spec


class ConsoleSink:
    def __init__(self) -> None:
        self._task_names: dict[str, str] = {}

    def handle(self, event: Event) -> None:
        if event.type == EventType.TASK_CREATED:
            name = event.data.get("spec", {}).get("name", event.task_id)
            self._task_names[event.task_id] = name
            print(f"[openloom] CREATED  {event.task_id[:12]}  {name}")

        elif event.type == EventType.TASK_STARTED:
            name = self._task_names.get(event.task_id, event.task_id[:12])
            sid = event.data.get("session_id", "")[:12]
            print(f"[openloom] STARTED  {event.task_id[:12]}  {name}  session={sid}")

        elif event.type == EventType.TASK_UPDATED:
            name = self._task_names.get(event.task_id, event.task_id[:12])
            summary = event.data.get("summary", "")
            progress = event.data.get("progress", 0)
            print(f"[openloom] UPDATED  {event.task_id[:12]}  {name}  status={event.data.get('status')}  progress={progress:.0%}  {summary}")

        elif event.type == EventType.TASK_COMPLETED:
            name = self._task_names.get(event.task_id, event.task_id[:12])
            print(f"[openloom] COMPLETE {event.task_id[:12]}  {name}  {event.data.get('summary', '')}")

        elif event.type == EventType.TASK_FAILED:
            name = self._task_names.get(event.task_id, event.task_id[:12])
            error = event.data.get("error", event.data.get("summary", ""))
            print(f"[openloom] FAILED   {event.task_id[:12]}  {name}  {error}")

        elif event.type == EventType.LOG_LINE:
            print(f"[openloom] LOG      {event.task_id[:12]}  {event.data.get('summary', '')}")

        sys.stdout.flush()


async def run_watch(spec_path: str, settings: Any) -> None:
    with open(spec_path) as f:
        spec = parse_task_spec(f.read())

    client = OpenCodeClient(settings.opencode_url, settings.opencode_username, settings.opencode_password)

    health = await client.health()
    if not health.ok:
        print(f"ERROR: OpenCode server not reachable: {health.message}")
        return

    from openloom.core.events import EventBus

    bus = EventBus()
    sink = ConsoleSink()
    bus.subscribe_all(sink.handle)

    harness = HarnessRunner(
        opencode=client,
        bus=bus,
        prompts=prompts,
        status=session_status,
        allowed_workspace=settings.is_allowed_workspace,
    )

    task_id = harness.add_task(spec)
    print(f"[openloom] Watching task {task_id[:12]}: {spec.name}")
    print(f"[openloom] Workspace: {spec.workspace}")
    print(f"[openloom] Check interval: {spec.check_interval_seconds}s")
    print(f"[openloom] Press Ctrl+C to stop\n")

    try:
        while True:
            await harness.tick()
            task = harness.get_task(task_id)
            if task and task["status"] in ("completed", "failed", "archived"):
                status = task["status"]
                print(f"\n[openloom] Task {task_id[:12]} finished with status: {status}")
                break
            await asyncio.sleep(5)
    except KeyboardInterrupt:
        print("\n[openloom] Interrupted. Task may still be running in OpenCode.")
