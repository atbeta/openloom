from __future__ import annotations

from abc import ABC, abstractmethod

from .events import Event


class Sink(ABC):
    @abstractmethod
    def on_event(self, event: Event) -> None:
        ...
