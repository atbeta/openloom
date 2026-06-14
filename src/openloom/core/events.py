from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

_logger = logging.getLogger("openloom.events")


class EventType(Enum):
    TASK_CREATED = auto()
    TASK_STARTED = auto()
    TASK_UPDATED = auto()
    TASK_COMPLETED = auto()
    TASK_FAILED = auto()
    LOG_LINE = auto()
    SESSION_STALE_BUSY = auto()


@dataclass(frozen=True)
class Event:
    type: EventType
    task_id: str
    timestamp: float = field(default_factory=time.time)
    store_version: int = 0
    data: dict[str, Any] = field(default_factory=dict)


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
