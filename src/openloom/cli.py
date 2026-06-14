from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any

from openloom import __version__
from openloom.config import Settings
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


def _is_verbose(args: argparse.Namespace) -> bool:
    """True if --verbose was passed or OPENLOOM_VERBOSE=1 in the env."""
    if getattr(args, "verbose", False):
        return True
    return os.getenv("OPENLOOM_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on")


def _print_banner(args: argparse.Namespace, settings: Settings) -> None:
    """Always-on startup banner — one line per fact, prefixed by the
    command name. With ``--verbose`` (or OPENLOOM_VERBOSE=1) we add
    a second block with all resolved env vars and the resolved
    config objects so cold-start issues are easier to diagnose."""
    cmd = getattr(args, "command", "") or "openloom"
    print(f"{cmd} {__version__} (python {platform.python_version()})")

    facts: list[tuple[str, str]] = [
        ("opencode", settings.opencode_url),
        ("store", str(settings.database_path)),
    ]
    for label, value in facts:
        print(f"  {label:<10} {value}")

    if _is_verbose(args):
        print("  env:")
        for key in sorted(os.environ):
            if key.startswith("OPENLOOM_"):
                print(f"    {key}={os.environ[key]}")
        if settings.notify.webhooks:
            print(f"  notify    {len(settings.notify.webhooks)} webhook(s)")
        for wh in settings.notify.webhooks:
            print(f"    - {wh.url}  events={sorted(wh.events)}")
        if settings.notify.files:
            print(f"  notify    {len(settings.notify.files)} file sink(s)")
        for fe in settings.notify.files:
            print(f"    - dir={fe.directory}  events={sorted(fe.events)}")
        if settings.inbox_dir is not None:
            print(
                f"  inbox     {settings.inbox_dir}/{settings.inbox_filename}"
                f"  poll={settings.inbox_poll_interval_seconds:.0f}s"
            )
    print()


def _configure_logging(verbose: bool) -> None:
    """Bump openloom loggers to DEBUG when verbose, INFO otherwise.
    Does not touch third-party loggers (uvicorn, httpx, etc.)."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def _build_notify_sinks(settings: Settings) -> list[Any]:
    from openloom.levels.notify import build_sinks

    return build_sinks(settings.notify)


def _build_inbox_factory(settings: Settings) -> Any:
    """Return a factory that spawns the inbox watcher task, or ``None`` if disabled.

    Kept as a factory so ``levels.server`` stays level-agnostic — it just
    calls ``factory(harness)`` to receive background ``asyncio.Task``s.
    """
    if settings.inbox_dir is None:
        return None

    from openloom.levels.inbox.watcher import InboxWatcher
    from openloom.runtime.prompts import TaskSpec

    def factory(harness: Any) -> list[Any]:
        assert settings.inbox_dir is not None  # guarded by caller

        async def inbox_dispatch(payload: dict[str, Any]) -> str | None:
            spec_dict = {k: v for k, v in payload.items() if not k.startswith("_")}
            return harness.add_task(TaskSpec.from_dict(spec_dict))

        watcher = InboxWatcher(
            directory=settings.inbox_dir,
            dispatch=inbox_dispatch,
            default_workspace=settings.inbox_default_workspace,
            filename=settings.inbox_filename,
            poll_interval_seconds=settings.inbox_poll_interval_seconds,
        )
        return [asyncio.create_task(watcher.run())]

    return factory


async def _run_watch_with_ui(
    spec: str | None,
    settings: Settings,
    web_sink: Any,
    extra_sinks: Any,
    *,
    store_path: Path,
) -> None:
    import uvicorn

    from openloom.levels.manual.watch import run_watch
    from openloom.runtime import prompts
    from openloom.runtime.factory import build_harness
    from openloom.server.app import create_app

    bundle = build_harness(
        settings,
        store_path=store_path,
        extra_sinks=[web_sink, *extra_sinks],
    )
    store, bus, _, harness = bundle.store, bundle.bus, bundle.client, bundle.harness

    app = create_app(
        harness=harness, store=store, bus=bus, web_sink=web_sink,
        settings=settings, parse_spec=prompts.parse_task_spec,
    )
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
            harness=harness,
            extra_sinks=extra_sinks,
        )
    finally:
        server.should_exit = True
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="openloom",
        description="OpenLoom — lightweight agent task harness",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print the full startup banner and enable DEBUG logging for openloom.*",
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
    notify_sinks = _build_notify_sinks(settings)
    inbox_factory = _build_inbox_factory(settings)

    # Banner + log level: print as early as possible so the user sees
    # something on stdout even while the heavier subsystems import
    # (fastapi, uvicorn for `serve`; the prompt parser for `watch`).
    _print_banner(args, settings)
    _configure_logging(_is_verbose(args))

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
                    args.spec, settings, web_sink, notify_sinks, store_path=store_path,
                )
            )
        else:
            from openloom.levels.manual.watch import run_watch

            asyncio.run(
                run_watch(
                    args.spec, settings, store_path=store_path, extra_sinks=notify_sinks,
                )
            )

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
                ui_host=args.host,
                ui_port=args.port or settings.ui_port,
            )
        elif args.port:
            settings = Settings(
                opencode_url=settings.opencode_url,
                opencode_username=settings.opencode_username,
                opencode_password=settings.opencode_password,
                database_path=settings.database_path,
                ui_port=args.port,
            )

        asyncio.run(
            run_serve(
                settings,
                extra_sinks=notify_sinks,
                background_tasks_factory=inbox_factory,
            )
        )

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
        if task is None:
            print(f"No task found matching '{prefix}'")
            sys.exit(1)
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
