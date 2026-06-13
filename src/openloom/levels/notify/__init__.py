from __future__ import annotations

from openloom.core.sink import Sink

from .config import NotifyConfig
from .file import FileSink
from .webhook import WebhookSink

__all__ = [
    "FileSink",
    "NotifyConfig",
    "WebhookSink",
    "build_sinks",
]


def build_sinks(config: NotifyConfig | None) -> list[Sink]:
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
    for fe in config.files:
        sinks.append(FileSink(
            directory=fe.directory,
            events=fe.events,
            prefix=fe.prefix,
        ))
    return sinks
