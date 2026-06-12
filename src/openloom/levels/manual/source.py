from __future__ import annotations

from typing import Any

from openloom.core.registry import register_source
from openloom.core.source import TaskSource
from openloom.runtime.prompts import load_task_spec, parse_task_spec


@register_source("manual")
class ManualSource(TaskSource):
    def load(self, **kwargs: Any) -> list[dict[str, Any]]:
        spec_path = kwargs.get("spec_path")
        if spec_path:
            with open(spec_path) as f:
                spec = parse_task_spec(f.read())
        else:
            spec = load_task_spec()
        return [spec.to_dict()]
