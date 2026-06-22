"""
Inbound webhook processing — generic source parser and helpers.

The ``GenericSource`` parser accepts a simple JSON envelope that any
external system can produce without knowing OpenLoom internals:

    {
      "name":      "Fix the bug",         // optional
      "workspace": "/path/to/project",    // required (or goal carries it)
      "goal":      "What the agent should do",
      "event":     "push",                // optional metadata
      "metadata":  { ... }                // optional extra context
    }

Custom sources register via ``@register_source("my_ci")`` and
implement ``SourceParser.parse``.
"""

from __future__ import annotations

from typing import Any

from openloom.core.registry import register_source
from openloom.core.webhook_types import SourceParser, WebhookInboundEvent


@register_source("generic")
class GenericSource(SourceParser):
    """Parse the generic OpenLoom webhook JSON envelope."""

    def parse(
        self,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> WebhookInboundEvent | None:
        if not body:
            return None

        goal = str(body.get("goal") or body.get("message") or "").strip()
        if not goal:
            return None

        return WebhookInboundEvent(
            source="generic",
            event_name=str(body.get("event") or "webhook"),
            name=str(body.get("name") or body.get("title") or ""),
            workspace=str(body.get("workspace") or body.get("cwd") or ""),
            goal=goal,
            session_id=str(body.get("sessionId") or body.get("session_id") or ""),
            metadata=dict(body.get("metadata") or {}),
        )


def process_inbound_event(
    event: WebhookInboundEvent,
    *,
    harness: Any,
    recent: Any = None,
) -> dict[str, Any]:
    """Map a parsed ``WebhookInboundEvent`` to a harness task creation.

    Returns the task creation result dict. Raises ``ValueError`` if
    the event cannot be mapped (missing workspace + goal, etc.).
    """
    from openloom.runtime.prompts import TaskSpec

    goal = event.goal.strip()
    if not goal:
        raise ValueError("goal is required")

    workspace = event.workspace.strip()
    name = (event.name or "").strip() or "Webhook task"

    spec = TaskSpec(name=name, workspace=workspace, goal=goal)
    task_id = harness.add_task(spec)

    if recent is not None and workspace:
        recent.record(workspace)

    return {
        "ok": True,
        "taskId": task_id,
        "status": "pending",
        "name": spec.name,
        "workspace": spec.workspace,
        "source": event.source,
    }
