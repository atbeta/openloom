from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckResult:
    task_complete: bool = False
    step_done: int = 0
    acceptance_checked: int = 0
    acceptance_total: int = 0
    acceptance_progress: float = 0.0


class Checker(ABC):
    @abstractmethod
    def check(self, messages: list[dict[str, Any]], spec: dict[str, Any]) -> CheckResult:
        ...
