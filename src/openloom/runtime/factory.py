"""
Harness assembly factory — single source of truth for wiring up
Store + EventBus + OpenCodeClient + HarnessRunner.

Used by ``levels/server/serve.py`` and ``cli.py`` to eliminate
duplicated assembly boilerplate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openloom.core.events import EventBus
from openloom.core.harness import HarnessRunner
from openloom.core.registry import get_sink
from openloom.core.store import Store
from openloom.runtime import prompts, session_status
from openloom.runtime.opencode import OpenCodeClient


@dataclass
class HarnessBundle:
    """All objects produced by a single harness assembly."""

    harness: HarnessRunner
    store: Store
    bus: EventBus
    client: OpenCodeClient


def build_harness(
    settings: Any,
    *,
    store_path: Path | None = None,
    extra_sinks: list[Any] | None = None,
    bus: EventBus | None = None,
    store: Store | None = None,
    client: OpenCodeClient | None = None,
    subscribe_console: bool = False,
) -> HarnessBundle:
    """Assemble and return a fully-wired HarnessBundle.

    Parameters allow callers to inject pre-existing objects (e.g. when
    testing) or fall back to fresh ones.
    """
    db_path = store_path or settings.database_path
    store = store or Store(db_path)
    bus = bus or EventBus()
    client = client or OpenCodeClient(
        settings.opencode_url,
        settings.opencode_username,
        settings.opencode_password,
    )

    if subscribe_console:
        console_sink = get_sink("console")()
        bus.subscribe_all(console_sink.on_event)

    for ns in (extra_sinks or ()):
        bus.subscribe_all(ns.on_event)

    harness = HarnessRunner(
        opencode=client,
        bus=bus,
        store=store,
        prompts=prompts,
        status=session_status,
        max_task_tokens=getattr(settings, "max_task_tokens", None),
        max_task_runtime_minutes=getattr(settings, "max_task_runtime_minutes", None),
    )

    return HarnessBundle(
        harness=harness,
        store=store,
        bus=bus,
        client=client,
    )

