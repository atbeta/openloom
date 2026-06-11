from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

Handler = Callable[["Event"], None]


class EventType(Enum):
    TASK_CREATED = auto()
    TASK_STARTED = auto()
    TASK_UPDATED = auto()
    TASK_COMPLETED = auto()
    TASK_FAILED = auto()
    LOG_LINE = auto()


@dataclass(frozen=True)
class Event:
    type: EventType
    task_id: str
    timestamp: float = field(default_factory=time.time)
    store_version: int = 0
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[Handler]] = defaultdict(list)
        self._wildcards: list[Handler] = []

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        self._wildcards.append(handler)

    def emit(self, event: Event) -> None:
        for handler in self._subscribers.get(event.type, ()):
            handler(event)
        for handler in self._wildcards:
            handler(event)
