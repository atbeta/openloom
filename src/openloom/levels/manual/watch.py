from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openloom.core.events import EventBus
from openloom.core.harness import HarnessRunner
from openloom.core.registry import get_checker, get_sink, get_source
from openloom.core.store import Store
from openloom.runtime import prompts, session_status
from openloom.runtime.opencode import OpenCodeClient

import openloom.levels.manual.checker  # noqa: F401
import openloom.levels.manual.sink  # noqa: F401
import openloom.levels.manual.source  # noqa: F401


async def run_watch(
    spec_path: str | None,
    settings: Any,
    *,
    store_path: str | Path | None = None,
    web_sink: Any = None,
    bus: Any = None,
) -> None:
    source_cls = get_source("manual")
    source = source_cls()
    specs = source.load(spec_path=spec_path)

    if not specs:
        print("ERROR: No tasks loaded from source")
        return

    client = OpenCodeClient(settings.opencode_url, settings.opencode_username, settings.opencode_password)

    health = await client.health()
    if not health.ok:
        print(f"ERROR: OpenCode server not reachable: {health.message}")
        return

    db_path = Path(store_path) if store_path else settings.database_path
    store = Store(db_path)
    bus = bus if bus is not None else EventBus()

    if web_sink is not None:
        bus.subscribe_all(web_sink.on_event)

    sink_cls = get_sink("console")
    sink = sink_cls()
    bus.subscribe_all(sink.on_event)

    checker_cls = get_checker("string")
    checker = checker_cls()

    harness = HarnessRunner(
        opencode=client,
        bus=bus,
        store=store,
        checker=checker,
        prompts=prompts,
        status=session_status,
        allowed_workspace=settings.is_allowed_workspace,
    )

    first_spec = specs[0]
    spec_name = first_spec.get("name", "Untitled")
    task_id = harness.add_task(first_spec)

    print(f"openloom: watching {task_id[:12]} — {spec_name}")
    print(f"  workspace: {first_spec.get('workspace', '?')}")
    print(f"  interval:  {first_spec.get('check_interval_seconds', 300)}s")
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
