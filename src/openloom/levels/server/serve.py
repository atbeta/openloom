from __future__ import annotations

import asyncio
import contextlib
import sys
from typing import Any


async def run_serve(
    settings: Any,
    *,
    extra_sinks: Any = None,
    background_tasks_factory: Any = None,
) -> None:
    from openloom.server.cold import require_fastapi
    require_fastapi()

    import uvicorn

    from openloom.core.events import EventBus
    from openloom.core.harness import HarnessRunner
    from openloom.core.registry import get_checker, get_sink
    from openloom.core.store import Store
    from openloom.levels.server.monitor import SessionMonitor
    from openloom.runtime import prompts
    from openloom.runtime import session_status as status_mod
    from openloom.runtime.opencode import OpenCodeClient, format_opencode_unreachable_help
    from openloom.server.app import create_app
    from openloom.server.recent import RecentWorkspaces

    store = Store(settings.database_path)
    recent = RecentWorkspaces(settings.database_path.parent / "recent.sqlite3")
    client = OpenCodeClient(settings.opencode_url, settings.opencode_username, settings.opencode_password)

    health = await client.health()
    if not health.ok:
        print(
            format_opencode_unreachable_help(settings.opencode_url, detail=health.message),
            file=sys.stderr,
        )
        print(
            "OpenLoom will keep running; connect OpenCode above, then refresh the dashboard.",
            file=sys.stderr,
        )

    bus = EventBus()

    sink_cls = get_sink("console")
    console_sink = sink_cls()
    bus.subscribe_all(console_sink.on_event)

    web_sink = get_sink("web")()
    bus.subscribe_all(web_sink.on_event)

    for ns in (extra_sinks or ()):
        bus.subscribe_all(ns.on_event)

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
        max_task_tokens=settings.max_task_tokens,
        max_task_runtime_minutes=settings.max_task_runtime_minutes,
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

    extra_background_tasks: list[asyncio.Task[Any]] = []
    if background_tasks_factory is not None:
        extra_background_tasks = list(background_tasks_factory(harness) or [])

    app = create_app(
        harness=harness, store=store, bus=bus, web_sink=web_sink,
        client=client, monitor=monitor,
        recent=recent, settings=settings,
        parse_spec=prompts.parse_task_spec,
        pick_folder=_native_pick_folder,
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
        for t in extra_background_tasks:
            t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await harness_task
        with contextlib.suppress(asyncio.CancelledError):
            await monitor_task
        for t in extra_background_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t


def _native_pick_folder(initial: str | None = None) -> str | None:
    import platform
    import subprocess

    if platform.system() == "Darwin":
        script = (
            'set chosenFolder to choose folder with prompt "Select workspace"\n'
            'POSIX path of chosenFolder'
        )
        if initial:
            script = script.replace(
                'choose folder',
                f'choose folder with prompt "Select workspace" default location POSIX file "{initial}"',
            )
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askdirectory(initialdir=initial, mustexist=True)
    finally:
        root.destroy()
    return path or None
