from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto
from typing import Any

_logger = logging.getLogger("openloom.events")


class EventType(Enum):
    TASK_CREATED = auto()
    TASK_STARTED = auto()
    TASK_UPDATED = auto()
    TASK_COMPLETED = auto()
    TASK_FAILED = auto()


@dataclass(frozen=True)
class Event:
    type: EventType
    task_id: str
    timestamp: float = field(default_factory=time.time)
    store_version: int = 0
    task_name: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def iso_utc(epoch_seconds: float) -> str:
    """Format a Unix-epoch timestamp as an ISO 8601 UTC string
    with a ``Z`` suffix (e.g. ``2026-06-15T03:10:56Z``). The
    event-bus emit path stores the float for ordering; sinks
    use this helper to render a human-readable copy alongside
    the float so webhook handlers and file dumps can sort by
    either representation.
    """
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[Callable[[Event], None]]] = defaultdict(list)
        self._wildcards: list[Callable[[Event], None]] = []

    def subscribe(self, event_type: EventType, handler: Callable[[Event], None]) -> None:
        self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: Callable[[Event], None]) -> None:
        self._wildcards.append(handler)

    def emit(self, event: Event) -> None:
        for handler in (*self._subscribers.get(event.type, ()), *self._wildcards):
            try:
                handler(event)
            except Exception:
                _logger.exception("Event handler %r failed for %s", handler, event.type.name)
