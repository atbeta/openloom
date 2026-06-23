from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
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

# Poll interval for the worker's blocking ``get()``. Short enough that
# ``close()`` shuts down within ~100ms even if the queue is idle; long
# enough that the polling overhead is negligible compared to a single
# HTTP POST.
_GET_POLL_SECONDS = 0.1


class WebhookSink(Sink):
    """POST a compact JSON payload to an HTTP endpoint on selected events.

    Features:
    - HMAC-SHA256 signing (optional, enabled when ``signing_secret`` is set)
    - Exponential backoff retry (configurable via ``max_retries``)
    - Canonical v1 payload envelope via ``render_payload``
    - **Non-blocking delivery** — ``on_event`` returns immediately and the
      HTTP POST + retry backoff happens on a background worker thread.
      This keeps the OpenLoom event bus / FastAPI dashboard responsive
      even when a webhook target is slow or unreachable. (Prior
      implementation did delivery synchronously, which blocked the bus
      for up to ``(1 + max_retries) * timeout_seconds`` per event.)
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
        # ``trust_env=False`` makes httpx ignore HTTP_PROXY / HTTPS_PROXY /
        # ALL_PROXY and the OS proxy config. Without it, a system-wide proxy
        # (corporate VPN, Clash, mitmproxy, etc.) hijacks requests to
        # 127.0.0.1 and the connector ends up receiving the webhook through
        # someone else's gateway — which either times out or returns a
        # generic "Content Filter - Access Denied" HTML page. See also
        # openloom-connector commit 62f432b for the matching hardening on
        # the inbound side.
        self._client = client or httpx.Client(
            timeout=timeout_seconds, trust_env=False,
        )
        self._extra_headers: dict[str, str] = dict(headers or {})
        self._signing_secret = signing_secret
        self._max_retries = max(0, max_retries)

        self._queue: queue.Queue[Event] = queue.Queue()
        self._shutdown = threading.Event()
        self._worker = threading.Thread(
            target=self._run,
            name=f"openloom-webhook-{url}",
            daemon=True,
        )
        self._worker.start()

    def on_event(self, event: Event) -> None:
        if not self._matches(event):
            return
        # Enqueue and return. The worker thread owns the HTTP I/O and the
        # retry backoff so this call site never blocks the event bus.
        try:
            self._queue.put_nowait(event)
        except queue.Full:  # pragma: no cover — Queue() is unbounded
            _logger.warning(
                "Webhook %s queue full; dropping %s", self._url, event.type.name,
            )

    def _run(self) -> None:
        """Worker thread loop — drains the event queue until shutdown."""
        while True:
            try:
                item = self._queue.get(timeout=_GET_POLL_SECONDS)
            except queue.Empty:
                if self._shutdown.is_set():
                    return
                continue
            self._deliver(item)

    def _deliver(self, event: Event) -> None:
        """Render + POST + retry a single event on the worker thread."""
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
            # Check before each attempt. The retry loop and the close()
            # path share this flag — if shutdown fires mid-retry, abandon
            # the chain rather than hold up process shutdown.
            if self._shutdown.is_set():
                _logger.debug(
                    "Webhook %s aborting retry for %s due to shutdown",
                    self._url, event_name,
                )
                return

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
                # Sleep on the worker thread, polling the shutdown event so
                # ``close()`` can interrupt long backoffs (up to 60s).
                if self._sleep_interruptible(delay):
                    _logger.debug(
                        "Webhook %s aborting retry for %s during backoff",
                        self._url, event_name,
                    )
                    return

        if last_exc is not None:
            _logger.error(
                "Webhook %s exhausted %d attempts for %s",
                self._url, 1 + self._max_retries, event_name,
            )

    def _sleep_interruptible(self, total: float) -> bool:
        """Sleep up to *total* seconds, returning early if shutdown fires.

        Returns ``True`` if shutdown interrupted the sleep (worker should
        stop retrying), ``False`` if the full sleep completed normally.
        """
        deadline = time.monotonic() + total
        while True:
            if self._shutdown.is_set():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(_GET_POLL_SECONDS, remaining))

    def _matches(self, event: Event) -> bool:
        if "*" in self._events:
            return True
        return event.type.name in self._events

    def close(self, *, timeout: float = 5.0) -> None:
        """Signal the worker to drain and stop.

        Bounded by *timeout* seconds so a wedged webhook endpoint cannot
        hold up process shutdown. The worker is a daemon thread, so even
        if ``timeout`` is exceeded the process can still exit.
        """
        self._shutdown.set()
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            _logger.warning(
                "Webhook %s worker did not exit within %.1fs; abandoning "
                "in-flight retries (daemon thread will be torn down with "
                "the process)", self._url, timeout,
            )
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