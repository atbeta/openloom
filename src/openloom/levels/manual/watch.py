from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import openloom.levels.manual.checker  # noqa: F401
import openloom.levels.manual.sink  # noqa: F401
import openloom.levels.manual.source  # noqa: F401
from openloom.core.registry import get_sink, get_source
from openloom.runtime.factory import build_harness
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

    if harness is None:
        db_path = Path(store_path) if store_path else settings.database_path
        sink_list: list[Any] = []
        if web_sink is not None:
            sink_list.append(web_sink)
        sink_list.extend(extra_sinks or ())
        bundle = build_harness(
            settings,
            store_path=db_path,
            client=client,
            bus=bus,
            extra_sinks=sink_list,
            subscribe_console=True,
        )
        harness = bundle.harness
        bus = bundle.bus
    else:
        if web_sink is not None:
            bus.subscribe_all(web_sink.on_event)
        for ns in (extra_sinks or ()):
            bus.subscribe_all(ns.on_event)
        sink_cls = get_sink("console")
        sink = sink_cls()
        bus.subscribe_all(sink.on_event)

    first_spec = specs[0]
    spec_name = first_spec.get("name", "Untitled")
    task_id = harness.add_task(first_spec)

    print(f"openloom: watching {task_id[:12]} — {spec_name}")
    print(f"  opencode:  {settings.opencode_url}")
    print(f"  workspace: {first_spec.get('workspace', '?')}")
    print(f"  interval:  {first_spec.get('check_interval_seconds', 300)}s")
    n_webhooks = len(settings.notify.webhooks)
    n_files = len(settings.notify.files)
    if n_webhooks:
        print(f"  notify:    {n_webhooks} webhook(s)")
    if n_files:
        print(f"  notify:    {n_files} file sink(s)")
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
