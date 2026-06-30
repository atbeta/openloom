from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from typing import Any

_logger = logging.getLogger("openloom.levels.server.serve")


async def run_serve(
    settings: Any,
    *,
    extra_sinks: Any = None,
) -> None:
    from openloom.server.cold import require_fastapi
    require_fastapi()

    print("  loading  harness...", end=" ", flush=True)

    import uvicorn

    from openloom.core.registry import get_sink
    from openloom.levels.server.console_sink import ConsoleSink  # noqa: F401 — registers sink
    from openloom.levels.server.monitor import SessionMonitor
    from openloom.levels.server.web_sink import WebSink  # noqa: F401 — registers sink
    from openloom.runtime.factory import build_harness
    from openloom.runtime.opencode import format_opencode_unreachable_help
    from openloom.server.app import create_app
    from openloom.server.recent import RecentWorkspaces

    bundle = build_harness(settings, extra_sinks=extra_sinks, subscribe_console=True)
    print("done")
    store, bus, client, harness = bundle.store, bundle.bus, bundle.client, bundle.harness

    web_sink = get_sink("web")()
    bus.subscribe_all(web_sink.on_event)

    recent = RecentWorkspaces(settings.database_path.parent / "recent.sqlite3")

    print("  connect  OpenCode...", end=" ", flush=True)
    health = await client.health()
    print("ok" if health.ok else "unreachable")
    if not health.ok:
        if settings.opencode_auto_start:
            print("OpenCode not found; auto-starting...")
            _start_opencode(settings)
            await asyncio.sleep(2)
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

    print("  refresh  sessions...", end=" ", flush=True)
    monitor = SessionMonitor(client)

    await monitor.refresh()
    print(f"{len(monitor.sessions)} found")

    # ── storage runner ──
    storage_task: asyncio.Task[Any] | None = None
    if settings.storage.enabled:
        from openloom.levels.storage import StorageRunner
        storage_runner = StorageRunner(settings.storage, bus, harness)
        storage_task = asyncio.create_task(storage_runner.run(), name="openloom-storage")

    async def harness_loop():
        while True:
            try:
                await harness.tick()
            except Exception:  # noqa: BLE001
                _logger.exception("harness tick loop failed")
            await asyncio.sleep(5)

    async def monitor_loop():
        while True:
            try:
                await monitor.refresh()
            except Exception:  # noqa: BLE001
                _logger.exception("session monitor refresh failed")
            await asyncio.sleep(8)

    harness_task = asyncio.create_task(harness_loop())
    monitor_task = asyncio.create_task(monitor_loop())

    app = create_app(
        harness=harness, store=store, bus=bus, web_sink=web_sink,
        client=client, monitor=monitor,
        recent=recent, settings=settings,
    )

    due = store.list_due_tasks()
    print()
    print("  starting server...")
    print()
    print(f"openloom serve — http://{settings.ui_host}:{settings.ui_port}")
    print(f"  store:    {settings.database_path}")
    print(f"  tasks:    {len(due)} pending/running")
    print(f"  sessions: {len(monitor.sessions)} visible")
    n_webhooks = len(settings.notify.webhooks)
    if n_webhooks:
        print(f"  notify:   {n_webhooks} webhook(s)")
    if settings.storage.enabled:
        print(f"  storage:  {settings.storage.connector_class.__name__}")
        print(f"    inbox:  {settings.storage.inbox_dir}")
        print(f"    outbox: {settings.storage.outbox_dir}")

    from openloom.core.registry import list_sources
    sources = list_sources()
    if sources:
        print(f"  sources:  {', '.join(sources)}")
    print()

    config = uvicorn.Config(
        app, host=settings.ui_host, port=settings.ui_port, log_level="warning",
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        if storage_task is not None:
            storage_runner.stop()  # type: ignore[possibly-unbound]
            storage_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await storage_task
        harness_task.cancel()
        monitor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await harness_task
        with contextlib.suppress(asyncio.CancelledError):
            await monitor_task


def _start_opencode(settings: Any) -> None:
    """Spawn OpenCode as a background subprocess."""
    import subprocess
    url = settings.opencode_url.rstrip("/")
    port = url.split(":")[-1] if ":" in url else "4096"
    subprocess.Popen(
        ["opencode", "serve", "--port", port],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"  opencode  spawned on port {port}")


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
