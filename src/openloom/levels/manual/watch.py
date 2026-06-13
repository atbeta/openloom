from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import openloom.levels.manual.checker  # noqa: F401
import openloom.levels.manual.sink  # noqa: F401
import openloom.levels.manual.source  # noqa: F401
from openloom.core.events import EventBus
from openloom.core.harness import HarnessRunner
from openloom.core.registry import get_checker, get_sink, get_source
from openloom.core.store import Store
from openloom.runtime import prompts, session_status
from openloom.runtime.opencode import OpenCodeClient, format_opencode_unreachable_help


async def run_watch(
    spec_path: str | None,
    settings: Any,
    *,
    store_path: str | Path | None = None,
    web_sink: Any = None,
    bus: Any = None,
    harness: Any = None,
    extra_sinks: Any = None,
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
        print(
            format_opencode_unreachable_help(settings.opencode_url, detail=health.message),
            file=sys.stderr,
        )
        sys.exit(1)

    db_path = Path(store_path) if store_path else settings.database_path
    store = harness.store if harness is not None else Store(db_path)
    bus = bus if bus is not None else EventBus()

    if web_sink is not None:
        bus.subscribe_all(web_sink.on_event)

    if harness is None:
        checker_cls = get_checker("string")
        checker = checker_cls()

        harness = HarnessRunner(
            opencode=client,
            bus=bus,
            store=store,
            checker=checker,
            prompts=prompts,
            status=session_status,
            max_task_tokens=getattr(settings, "max_task_tokens", None),
            max_task_runtime_minutes=getattr(settings, "max_task_runtime_minutes", None),
        )

    sink_cls = get_sink("console")
    sink = sink_cls()
    bus.subscribe_all(sink.on_event)

    for ns in (extra_sinks or ()):
        bus.subscribe_all(ns.on_event)

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
