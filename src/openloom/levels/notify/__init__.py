from __future__ import annotations

from openloom.core.sink import Sink

from .config import NotifyConfig
from .webhook import WebhookSink

__all__ = [
    "NotifyConfig",
    "WebhookSink",
    "build_sinks",
]


def build_sinks(config: NotifyConfig | None) -> list[Sink]:
    """Build a list of webhook sinks from the resolved config.

    The previous version of this function also produced ``FileSink``
    entries (one JSON file per event written to a directory).
    0.12 removes the file sink entirely — webhook is the only
    delivery path. The function signature is unchanged so a
    caller that has been updated to the new ``NotifyConfig``
    shape can still be wired by name.
    """
    if config is None:
        return []
    sinks: list[Sink] = []
    for wh in config.webhooks:
        sinks.append(WebhookSink(
            url=wh.url,
            events=wh.events,
            timeout_seconds=wh.timeout_seconds,
            headers=wh.headers,
        ))
    return sinks
