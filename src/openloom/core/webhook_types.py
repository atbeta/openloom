"""
Webhook integration abstractions — core types for inbound and outbound webhooks.

Inbound: external systems POST to ``/api/webhooks/{source}``; a
``SourceParser`` (registered via the registry) normalizes the
source-specific payload into a ``WebhookInboundEvent`` which the
server then maps to a harness task.

Outbound: the canonical ``render_payload`` function produces the
v1 JSON envelope that every ``WebhookSink`` sends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any  # noqa: F401 — used in type hints

# ---------------------------------------------------------------------------
# Inbound
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookInboundEvent:
    """Normalized representation of an incoming webhook payload.

    Source parsers produce this; the server maps it to a TaskSpec.
    All fields except ``source`` are optional — the server / harness
    fills defaults for anything the source did not provide.
    """

    source: str
    event_name: str = ""
    name: str = ""
    workspace: str = ""
    goal: str = ""
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceParser(ABC):
    """Parse a source-specific HTTP webhook into a ``WebhookInboundEvent``.

    Implement ``parse`` and register with ``@register_source("name")``.
    Return ``None`` to silently acknowledge without creating a task
    (e.g. a ping event).
    """

    @abstractmethod
    def parse(
        self,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> WebhookInboundEvent | None:
        ...


# ---------------------------------------------------------------------------
# Outbound — canonical v1 payload
# ---------------------------------------------------------------------------

def render_payload(event: Any) -> dict[str, Any]:
    """Render the canonical v1 outbound webhook payload envelope.

    ``event`` is an ``openloom.core.events.Event`` — typed as Any
    here to avoid a circular import with events.py.
    """
    from openloom.core.events import iso_utc

    return {
        "schema_version": "1.0",
        "event": event.type.name,
        "task_id": event.task_id,
        "task_name": event.task_name,
        "timestamp": event.timestamp,
        "timestamp_iso": iso_utc(event.timestamp),
        "store_version": event.store_version,
        "data": event.data,
    }
