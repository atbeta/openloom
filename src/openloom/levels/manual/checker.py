from __future__ import annotations

from typing import Any

from openloom.core.checker import Checker, CheckResult
from openloom.core.registry import register_checker
from openloom.runtime.prompts import TaskSpec, assistant_transcript, detect_progress


@register_checker("string")
class StringChecker(Checker):
    def check(self, messages: list[dict[str, Any]], spec: dict[str, Any]) -> CheckResult:
        text = assistant_transcript(messages)
        progress = detect_progress(text, TaskSpec.from_dict(spec))
        return CheckResult(
            task_complete=progress["task_complete"],
            step_done=progress["step_done"],
            acceptance_checked=progress["acceptance_checked"],
            acceptance_total=progress["acceptance_total"],
            acceptance_progress=progress["acceptance_progress"],
        )
