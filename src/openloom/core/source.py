from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TaskSource(ABC):
    @abstractmethod
    def load(self, **kwargs: Any) -> list[dict[str, Any]]:
        ...
