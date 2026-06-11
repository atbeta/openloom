from __future__ import annotations

import asyncio
from typing import Any


async def run_serve(settings: Any) -> None:
    from openloom.server.cold import require_fastapi
    require_fastapi()

    from openloom.core.events import EventBus
    from openloom.core.harness import HarnessRunner
    from openloom.core.registry import get_checker, get_sink
    from openloom.core.store import Store
    from openloom.runtime import prompts, session_status as status_mod
    from openloom.runtime.opencode import OpenCodeClient

    import openloom.levels.manual.checker  # noqa: F401
    import openloom.levels.manual.sink  # noqa: F401
    from openloom.levels.server.monitor import SessionMonitor

    from openloom.server.app import create_app

    import uvicorn

    store = Store(settings.database_path)
    client = OpenCodeClient(settings.opencode_url, settings.opencode_username, settings.opencode_password)

    health = await client.health()
    if not health.ok:
        print(f"WARNING: OpenCode not reachable: {health.message}")
        print("  Session monitoring and dispatch will be unavailable.")

    bus = EventBus()

    sink_cls = get_sink("console")
    console_sink = sink_cls()
    bus.subscribe_all(console_sink.on_event)

    web_sink = get_sink("web")()
    bus.subscribe_all(web_sink.on_event)

    checker_cls = get_checker("string")
    checker = checker_cls()

    monitor = SessionMonitor(client)

    harness = HarnessRunner(
        opencode=client,
        bus=bus,
        store=store,
        checker=checker,
        prompts=prompts,
        status=status_mod,
        allowed_workspace=settings.is_allowed_workspace,
    )

    await monitor.refresh()

    async def harness_loop():
        while True:
            try:
                await harness.tick()
            except Exception:
                pass
            await asyncio.sleep(5)

    async def monitor_loop():
        while True:
            try:
                await monitor.refresh()
            except Exception:
                pass
            await asyncio.sleep(8)

    harness_task = asyncio.create_task(harness_loop())
    monitor_task = asyncio.create_task(monitor_loop())

    app = create_app(
        harness=harness, store=store, bus=bus, web_sink=web_sink,
        client=client, monitor=monitor,
    )

    due = store.list_due_tasks()
    print(f"openloom serve — http://{settings.ui_host}:{settings.ui_port}")
    print(f"  store:    {settings.database_path}")
    print(f"  tasks:    {len(due)} pending/running")
    print(f"  sessions: {len(monitor.sessions)} visible")
    print()

    config = uvicorn.Config(app, host=settings.ui_host, port=settings.ui_port, log_level="warning")
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        harness_task.cancel()
        monitor_task.cancel()
        with asyncio.suppress(asyncio.CancelledError):
            await harness_task
        with asyncio.suppress(asyncio.CancelledError):
            await monitor_task
