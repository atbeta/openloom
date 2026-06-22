from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

import httpx

from openloom.core.events import Event
from openloom.core.sink import Sink
from openloom.core.webhook_types import render_payload

_logger = logging.getLogger("openloom.notify.webhook")

# Retry backoff: 1s → 4s → 16s (factor of 4, capped at 60s)
_BACKOFF_BASE = 1.0
_BACKOFF_FACTOR = 4
_BACKOFF_CAP = 60.0


class WebhookSink(Sink):
    """POST a compact JSON payload to an HTTP endpoint on selected events.

    Features:
    - HMAC-SHA256 signing (optional, enabled when ``signing_secret`` is set)
    - Exponential backoff retry (configurable via ``max_retries``)
    - Canonical v1 payload envelope via ``render_payload``
    """

    def __init__(
        self,
        url: str,
        events: frozenset[str] | None = None,
        *,
        timeout_seconds: float = 3.0,
        headers: dict[str, str] | None = None,
        signing_secret: str = "",
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._events: frozenset[str] = events or frozenset({"*"})
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._extra_headers: dict[str, str] = dict(headers or {})
        self._signing_secret = signing_secret
        self._max_retries = max(0, max_retries)

    def on_event(self, event: Event) -> None:
        if not self._matches(event):
            return
        payload = render_payload(event)
        body = json.dumps(payload, separators=(",", ":"))
        headers = {
            "Content-Type": "application/json",
            "X-OpenLoom-Event": event.type.name,
            **self._extra_headers,
        }
        if self._signing_secret:
            sig = _compute_signature(self._signing_secret, body)
            headers["X-OpenLoom-Signature-256"] = f"sha256={sig}"

        self._deliver_with_retry(body, headers, event.type.name)

    def _deliver_with_retry(
        self, body: str, headers: dict[str, str], event_name: str,
    ) -> None:
        last_exc: Exception | None = None
        for attempt in range(1 + self._max_retries):
            try:
                response = self._client.post(
                    self._url, content=body, headers=headers,
                )
                if response.status_code < 400:
                    return
                _logger.warning(
                    "Webhook %s returned %s for %s (attempt %d/%d)",
                    self._url, response.status_code, event_name,
                    attempt + 1, 1 + self._max_retries,
                )
                last_exc = None
            except httpx.HTTPError as exc:
                _logger.warning(
                    "Webhook %s failed for %s: %s (attempt %d/%d)",
                    self._url, event_name, exc,
                    attempt + 1, 1 + self._max_retries,
                )
                last_exc = exc

            if attempt < self._max_retries:
                delay = min(_BACKOFF_BASE * (_BACKOFF_FACTOR ** attempt), _BACKOFF_CAP)
                time.sleep(delay)

        if last_exc is not None:
            _logger.error(
                "Webhook %s exhausted %d attempts for %s",
                self._url, 1 + self._max_retries, event_name,
            )

    def _matches(self, event: Event) -> bool:
        if "*" in self._events:
            return True
        return event.type.name in self._events

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _compute_signature(secret: str, body: str) -> str:
    """Compute HMAC-SHA256 hex digest of *body* using *secret*."""
    return hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(secret: str, body: str, signature_header: str) -> bool:
    """Verify an ``X-OpenLoom-Signature-256`` header value.

    Expected format: ``sha256=<hex>``. Returns ``True`` if valid.
    Raises ``ValueError`` on malformed header.
    """
    if not signature_header.startswith("sha256="):
        raise ValueError("signature header must start with 'sha256='")
    received = signature_header[7:]
    expected = _compute_signature(secret, body)
    return hmac.compare_digest(received, expected)
