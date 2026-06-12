from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

from openloom.config import Settings
from openloom.core.events import EventBus
from openloom.core.store import Store


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print(fmt.format(*["─" * w for w in col_widths]))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def _web_extras_available() -> bool:
    return all(
        importlib.util.find_spec(name) is not None
        for name in ("fastapi", "uvicorn", "sse_starlette")
    )


def _require_web_extras() -> None:
    if not _web_extras_available():
        raise ImportError(
            "Web UI requires FastAPI extras. Install with: pip install openloom[ui]"
        )


async def _run_watch_with_ui(
    spec: str | None,
    settings: Settings,
    web_sink: Any,
    *,
    store_path: Path,
) -> None:
    from openloom.server.app import create_app
    import uvicorn
    from openloom.levels.manual.watch import run_watch

    store = Store(store_path)
    bus = EventBus()
    bus.subscribe_all(web_sink.on_event)

    app = create_app(harness=None, store=store, bus=bus, web_sink=web_sink)
    config = uvicorn.Config(
        app, host=settings.ui_host, port=settings.ui_port, log_level="warning"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        await run_watch(
            spec,
            settings,
            store_path=store_path,
            bus=bus,
            web_sink=web_sink,
        )
    finally:
        server.should_exit = True
        server_task.cancel()
        with asyncio.suppress(asyncio.CancelledError):
            await server_task


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="openloom",
        description="OpenLoom — lightweight agent task harness",
    )
    sub = parser.add_subparsers(dest="command")

    watch_p = sub.add_parser("watch", help="Watch a task spec and manage the agent session")
    watch_p.add_argument("spec", nargs="?", help="Path to task spec YAML (reads openloom.yaml if omitted)")
    watch_p.add_argument("--ui", action="store_true", help="Start web UI on http://127.0.0.1:55413")

    serve_p = sub.add_parser("serve", help="Start OpenLoom server (multi-task, web dashboard)")
    serve_p.add_argument("--host", help="Bind host (default: 127.0.0.1)")
    serve_p.add_argument("--port", type=int, help="Bind port (default: 55413)")

    init_p = sub.add_parser("init", help="Generate openloom.yaml in the current directory")
    init_p.add_argument("--path", help="Target path (default: ./openloom.yaml)")

    sub.add_parser("status", help="List all tasks from the store")

    log_p = sub.add_parser("log", help="Show check log for a task")
    log_p.add_argument("task_id", help="Task ID (prefix match supported)")

    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command == "init":
        from openloom.levels.config.spec import generate_config

        try:
            path = generate_config(args.path)
            print(f"Created {path}")
        except FileExistsError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    elif args.command == "watch":
        store_path = settings.database_path
        if args.ui:
            try:
                _require_web_extras()
            except ImportError as e:
                print(f"ERROR: {e}")
                sys.exit(1)
            from openloom.levels.ui.sink import WebSink

            web_sink = WebSink()
            asyncio.run(
                _run_watch_with_ui(
                    args.spec, settings, web_sink, store_path=store_path
                )
            )
        else:
            from openloom.levels.manual.watch import run_watch

            asyncio.run(run_watch(args.spec, settings, store_path=store_path))

    elif args.command == "serve":
        import openloom.levels.manual.checker  # noqa: F401
        import openloom.levels.manual.sink  # noqa: F401
        import openloom.levels.ui.sink  # noqa: F401
        from openloom.levels.server.serve import run_serve

        if args.host:
            settings = Settings(
                opencode_url=settings.opencode_url,
                opencode_username=settings.opencode_username,
                opencode_password=settings.opencode_password,
                database_path=settings.database_path,
                allowed_roots=settings.allowed_roots,
                strict_roots=settings.strict_roots,
                ui_host=args.host,
                ui_port=args.port or settings.ui_port,
            )
        elif args.port:
            settings = Settings(
                opencode_url=settings.opencode_url,
                opencode_username=settings.opencode_username,
                opencode_password=settings.opencode_password,
                database_path=settings.database_path,
                allowed_roots=settings.allowed_roots,
                strict_roots=settings.strict_roots,
                ui_port=args.port,
            )

        asyncio.run(run_serve(settings))

    elif args.command == "status":
        store = Store(settings.database_path)
        tasks = store.list_tasks()
        if not tasks:
            print("No tasks found.")
            return
        headers = ["ID", "Name", "Status", "Progress", "Last Summary"]
        rows = [
            [
                t["id"][:12],
                t.get("name", "")[:40],
                t.get("status", ""),
                f"{t.get('progress', 0):.0%}",
                (t.get("last_summary") or "")[:50],
            ]
            for t in tasks
        ]
        _print_table(headers, rows)

    elif args.command == "log":
        import datetime

        store = Store(settings.database_path)
        prefix = args.task_id
        tasks = store.list_tasks()
        match = next((t for t in tasks if t["id"].startswith(prefix)), None)
        if not match:
            print(f"No task found matching '{prefix}'")
            sys.exit(1)
        task = store.get_task(match["id"])
        print(f"Task:  {task['id']} — {task.get('name', '')}")
        print(f"Status: {task.get('status', '')}  Progress: {task.get('progress', 0):.0%}")
        print()
        log = task.get("check_log") or []
        for entry in log[-20:]:
            ts = entry.get("at", 0)
            dt = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            print(f"  [{dt}] {entry.get('status', '')}")
            print(f"         {entry.get('summary', '')}")
        if len(log) > 20:
            print(f"  ... ({len(log) - 20} more entries)")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
