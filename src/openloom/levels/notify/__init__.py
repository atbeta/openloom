from __future__ import annotations

from openloom.core.sink import Sink

from .config import NotifyConfig
from .inbound import GenericSource, process_inbound_event
from .webhook import WebhookSink, verify_signature

__all__ = [
    "NotifyConfig",
    "WebhookSink",
    "GenericSource",
    "build_sinks",
    "process_inbound_event",
    "verify_signature",
]


def build_sinks(config: NotifyConfig | None) -> list[Sink]:
    """Build a list of webhook sinks from the resolved config."""
    if config is None:
        return []
    sinks: list[Sink] = []
    for wh in config.webhooks:
        sinks.append(WebhookSink(
            url=wh.url,
            events=wh.events,
            timeout_seconds=wh.timeout_seconds,
            headers=wh.headers,
            signing_secret=wh.signing_secret,
            max_retries=wh.max_retries,
        ))
    return sinks
