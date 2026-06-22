"""
Inbound webhook route — ``POST /api/webhooks/{source}``.

Accepts a webhook payload from an external system, looks up the
registered ``SourceParser`` for *source*, normalizes the payload
into a ``WebhookInboundEvent``, and creates a harness task.

Built-in source: ``generic`` (accepts any JSON with a ``goal`` field).
Custom sources register via ``@register_source("my_ci")`` in user code.
"""

from __future__ import annotations

from typing import Any

from openloom.core.registry import get_source, list_sources


async def handle_webhook(
    source: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    harness: Any,
    recent: Any = None,
) -> dict[str, Any]:
    """Process an inbound webhook for *source*.

    Returns the task creation result, or raises ``KeyError`` /
    ``ValueError`` on failure.
    """
    from openloom.core.webhook_types import WebhookInboundEvent

    parser = get_source(source)
    event: WebhookInboundEvent | None = parser.parse(headers, body)
    if event is None:
        return {"ok": True, "action": "ignored", "reason": "parser returned null"}

    goal = event.goal.strip()
    if not goal:
        raise ValueError("goal is required")

    workspace = event.workspace.strip()
    name = (event.name or "").strip() or "Webhook task"
    session_id = event.session_id.strip()

    if not session_id and not workspace:
        raise ValueError("either workspace or sessionId is required")

    from openloom.runtime.prompts import TaskSpec

    spec = TaskSpec(name=name, workspace=workspace, goal=goal)
    task_id = harness.add_task(spec, active_session_id=session_id or None)

    if recent is not None and workspace and not session_id:
        recent.record(workspace)

    return {
        "ok": True,
        "taskId": task_id,
        "status": "pending",
        "name": spec.name,
        "workspace": spec.workspace,
        "sessionId": session_id or None,
        "source": event.source,
    }


def available_sources() -> dict[str, Any]:
    """Return the list of registered source names."""
    return {"sources": list_sources()}
