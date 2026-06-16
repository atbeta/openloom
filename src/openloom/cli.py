"""
openloom CLI — only the ``serve`` subcommand is shipped in 0.12.

Earlier releases also offered ``init`` (write a starter
``openloom.yaml``), ``watch`` (single-spec runner with manual
checks / acceptance / step-acknowledgement), ``status`` and
``log`` (task CLI inspection). All of those belonged to the
manual-mode workflow that 0.12 removes. The web dashboard is
the single control surface now; the CLI exists only to start
the server.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import importlib.util
import logging
import os
import platform
import sys
from typing import Any

from openloom import __version__
from openloom.config import Settings


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


def _apply_serve_overrides(
    settings: Settings, *, host: str | None, port: int | None,
) -> Settings:
    """Apply ``--host`` / ``--port`` overrides to a Settings instance.

    Only the UI bind address should be touched; every other field
    (notify sinks, task limits, etc.) must come from the
    env-derived settings. Constructing a fresh ``Settings(...)`` here
    would silently drop those env-loaded values, so we use
    ``dataclasses.replace`` to patch only what changed.
    """
    overrides: dict[str, Any] = {}
    if host:
        overrides["ui_host"] = host
    if port is not None:
        overrides["ui_port"] = port
    if not overrides:
        return settings
    return dataclasses.replace(settings, **overrides)


def _build_notify_sinks(settings: Settings) -> list[Any]:
    from openloom.levels.notify import build_sinks

    return build_sinks(settings.notify)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="openloom",
        description="OpenLoom — webhook-driven agent task harness",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print the full startup banner and enable DEBUG logging for openloom.*",
    )
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser(
        "serve", help="Start the OpenLoom server (web dashboard + REST/webhook API)",
    )
    serve_p.add_argument("--host", help="Bind host (default: 127.0.0.1)")
    serve_p.add_argument("--port", type=int, help="Bind port (default: 55413)")

    args = parser.parse_args()
    settings = Settings.from_env()
    notify_sinks = _build_notify_sinks(settings)

    # Banner + log level: print as early as possible so the user sees
    # something on stdout even while the heavier subsystems import.
    _print_banner(args, settings)
    _configure_logging(_is_verbose(args))

    if args.command == "serve":
        from openloom.levels.server.serve import run_serve

        settings = _apply_serve_overrides(settings, host=args.host, port=args.port)
        asyncio.run(
            run_serve(
                settings,
                extra_sinks=notify_sinks,
            )
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
