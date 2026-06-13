from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from openloom.core.events import Event
from openloom.core.sink import Sink

_logger = logging.getLogger("openloom.notify.file")

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class FileSink(Sink):
    """Append one JSON file per event under the configured directory."""

    def __init__(
        self,
        directory: Path,
        events: frozenset[str] | None = None,
        *,
        prefix: str = "openloom",
    ) -> None:
        self._directory = Path(directory)
        self._events: frozenset[str] = events or frozenset({"*"})
        self._prefix = _SAFE.sub("-", prefix) or "openloom"
        self._directory.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: Event) -> None:
        if not self._matches(event):
            return
        ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime(event.timestamp))
        # millisecond suffix to keep ordering unique when events arrive in the same second
        ts += f"-{int((event.timestamp % 1) * 1000):03d}"
        filename = f"{self._prefix}-{event.type.name}-{ts}.json"
        path = self._directory / filename
        try:
            path.write_text(json.dumps(_render_payload(event), indent=2, sort_keys=True))
        except OSError as exc:
            _logger.warning("File notify %s failed: %s", path, exc)

    def _matches(self, event: Event) -> bool:
        if "*" in self._events:
            return True
        return event.type.name in self._events


def _render_payload(event: Event) -> dict[str, Any]:
    return {
        "event": event.type.name,
        "task_id": event.task_id,
        "timestamp": event.timestamp,
        "store_version": event.store_version,
        "data": event.data,
    }
