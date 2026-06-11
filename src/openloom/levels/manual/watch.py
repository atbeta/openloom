from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from openloom.core.events import Event, EventBus, EventType
from openloom.core.harness import HarnessRunner
from openloom.core.store import Store
from openloom.levels.manual.checker import StringChecker
from openloom.runtime import prompts, session_status
from openloom.runtime.opencode import OpenCodeClient
from openloom.runtime.prompts import load_task_spec, parse_task_spec


class ConsoleSink:
    def __init__(self) -> None:
        self._task_names: dict[str, str] = {}

    def handle(self, event: Event) -> None:
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


async def run_watch(spec_path: str | None, settings: Any, store_path: str | None = None) -> None:
    if spec_path:
        with open(spec_path) as f:
            spec = parse_task_spec(f.read())
    else:
        spec = load_task_spec()

    client = OpenCodeClient(settings.opencode_url, settings.opencode_username, settings.opencode_password)

    health = await client.health()
    if not health.ok:
        print(f"ERROR: OpenCode server not reachable: {health.message}")
        return

    db_path = Path(store_path) if store_path else Path.cwd() / ".openloom" / "openloom.sqlite3"
    store = Store(db_path)

    bus = EventBus()
    sink = ConsoleSink()
    bus.subscribe_all(sink.handle)

    checker = StringChecker()

    harness = HarnessRunner(
        opencode=client,
        bus=bus,
        store=store,
        checker=checker,
        prompts=prompts,
        status=session_status,
        allowed_workspace=settings.is_allowed_workspace,
    )

    task_id = harness.add_task(spec)
    print(f"openloom: watching {task_id[:12]} — {spec.name}")
    print(f"  workspace: {spec.workspace}")
    print(f"  interval:  {spec.check_interval_seconds}s")
    print(f"  store:     {db_path}")
    print()

    try:
        while True:
            await harness.tick()
            task = harness.get_task(task_id)
            if task and task["status"] in ("completed", "failed", "archived"):
                print(f"\nopenloom: task {task_id[:12]} finished — {task['status']}")
                break
            await asyncio.sleep(5)
    except KeyboardInterrupt:
        print("\nopenloom: interrupted (task may still run in OpenCode)")
