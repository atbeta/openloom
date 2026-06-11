from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="openloom",
        description="OpenLoom — lightweight agent task harness",
    )
    sub = parser.add_subparsers(dest="command")

    watch_parser = sub.add_parser("watch", help="Watch a task spec and manage the agent session")
    watch_parser.add_argument("spec", help="Path to task spec YAML file")

    args = parser.parse_args()

    if args.command == "watch":
        from openloom.config import Settings
        from openloom.levels.manual.watch import run_watch

        settings = Settings.from_env()
        asyncio.run(run_watch(args.spec, settings))
    else:
        parser.print_help()
        sys.exit(1)
