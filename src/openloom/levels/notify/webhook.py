from __future__ import annotations

import logging
from typing import Any

import httpx

from openloom.core.events import Event, EventType
from openloom.core.sink import Sink

_logger = logging.getLogger("openloom.notify.webhook")


class WebhookSink(Sink):
    """POST a compact JSON payload to an HTTP endpoint on selected events."""

    def __init__(
        self,
        url: str,
        events: frozenset[str] | None = None,
        *,
        timeout_seconds: float = 3.0,
        headers: dict[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._events: frozenset[str] = events or frozenset({"*"})
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._extra_headers: dict[str, str] = dict(headers or {})

    def on_event(self, event: Event) -> None:
        if not self._matches(event):
            return
        payload = _render_payload(event)
        headers = {
            "Content-Type": "application/json",
            "X-OpenLoom-Event": event.type.name,
            **self._extra_headers,
        }
        try:
            response = self._client.post(self._url, json=payload, headers=headers)
            if response.status_code >= 400:
                _logger.warning(
                    "Webhook %s returned %s for %s",
                    self._url, response.status_code, event.type.name,
                )
        except httpx.HTTPError as exc:
            _logger.warning("Webhook %s failed for %s: %s", self._url, event.type.name, exc)

    def _matches(self, event: Event) -> bool:
        if "*" in self._events:
            return True
        return event.type.name in self._events or EventType(event.type).name in self._events

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _render_payload(event: Event) -> dict[str, Any]:
    return {
        "event": event.type.name,
        "task_id": event.task_id,
        "timestamp": event.timestamp,
        "store_version": event.store_version,
        "data": event.data,
    }
