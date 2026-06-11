from __future__ import annotations

import argparse
import asyncio
import sys

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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="openloom",
        description="OpenLoom — lightweight agent task harness",
    )
    sub = parser.add_subparsers(dest="command")

    watch_p = sub.add_parser("watch", help="Watch a task spec and manage the agent session")
    watch_p.add_argument("spec", nargs="?", help="Path to task spec YAML (reads openloom.yaml if omitted)")
    watch_p.add_argument("--ui", action="store_true", help="Start web UI on http://127.0.0.1:55413")

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
        from openloom.levels.manual.watch import run_watch

        web_sink = None
        if args.ui:
            from openloom.server.cold import require_fastapi
            try:
                require_fastapi()
            except ImportError as e:
                print(f"ERROR: {e}")
                sys.exit(1)
            import openloom.levels.ui.sink  # noqa: F401
            from openloom.levels.ui.sink import WebSink
            web_sink = WebSink()

        asyncio.run(run_watch(args.spec, settings, ui=args.ui, web_sink=web_sink, store_path=str(settings.database_path)))

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
            import datetime
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
