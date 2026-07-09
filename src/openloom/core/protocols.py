"""
Protocol definitions for HarnessRunner dependencies.

These replace ``Any`` type annotations so that mypy can verify the wiring
between core, runtime, and levels without introducing circular imports.

0.12 only needs four protocols. ``CheckerPort`` and
``CheckResultProtocol`` (the manual-mode check step) are gone.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# OpenCodePort — what HarnessRunner calls on the OpenCode HTTP client
# ---------------------------------------------------------------------------


@runtime_checkable
class OpenCodePort(Protocol):
    """Minimal surface HarnessRunner needs from an OpenCode client."""

    async def list_sessions(self) -> list[dict[str, Any]]: ...
    async def session_status(self) -> dict[str, Any]: ...
    async def messages(
        self, session_id: str, *, limit: int = 50,
    ) -> list[dict[str, Any]]: ...
    async def resolve_session_permissions(
        self, session_id: str,
    ) -> dict[str, Any] | None: ...
    async def send_prompt_async(
        self,
        *,
        session_id: str,
        prompt: str,
        agent: str | None = None,
        directory: str | None = None,
    ) -> None: ...
    async def create_session(self, *, cwd: str, title: str) -> dict[str, Any]: ...
    async def abort_session(self, session_id: str) -> bool: ...
    async def diff(self, session_id: str) -> list[dict[str, Any]]: ...
    async def list_pending_permissions(
        self, session_id: str | None = None,
    ) -> list[dict[str, Any]]: ...
    async def respond_permission(
        self,
        session_id: str,
        permission_id: str,
        response: str = "once",
        *,
        directory: str | None = None,
    ) -> bool: ...
    async def list_pending_questions(
        self, session_id: str | None = None,
    ) -> list[dict[str, Any]]: ...
    async def respond_question(
        self,
        request_id: str,
        answers: list[list[str]],
        *,
        directory: str | None = None,
    ) -> bool: ...
    async def health(self) -> Any: ...
    async def set_archived(
        self, session_id: str, archived: int | None,
    ) -> dict[str, Any]: ...
    async def delete_session(self, session_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# StorePort — what HarnessRunner calls on the task store
# ---------------------------------------------------------------------------


@runtime_checkable
class StorePort(Protocol):
    """Minimal surface HarnessRunner needs from a task store."""

    def get_task(self, task_id: str) -> dict[str, Any] | None: ...
    def create_task(self, task: dict[str, Any]) -> dict[str, Any]: ...
    def update_task(self, task_id: str, **kwargs: Any) -> int: ...
    def delete_task(self, task_id: str) -> int: ...
    def list_due_tasks(self) -> list[dict[str, Any]]: ...
    def list_tasks(self) -> list[dict[str, Any]]: ...
    def append_check_log(
        self,
        task_id: str,
        *,
        status: str,
        summary: str,
        detail: str = "",
    ) -> int: ...


# ---------------------------------------------------------------------------
# PromptsPort — what HarnessRunner calls on the prompts module
# ---------------------------------------------------------------------------


@runtime_checkable
class PromptsPort(Protocol):
    """Minimal surface HarnessRunner needs from the prompts module.

    The old protocol declared a dozen methods for the manual-mode
    nudge / acceptance / step-acknowledgement protocol. The 0.12
    webhook-only cut removed every one of those — the harness now
    only needs the three primitives that survive in
    ``runtime.prompts``: the TaskSpec type itself, a busy signal
    for the monitor, and the recent-activity enricher for the
    notify payload. Webhook handlers can also call
    ``runtime.prompts.detect_progress`` client-side, but that is a
    pure function on a string and does not need to live on the
    protocol.
    """

    TaskSpec: Any

    def messages_indicate_busy(
        self, messages: list[dict[str, Any]],
    ) -> bool: ...
    def recent_assistant_activity(
        self, messages: list[dict[str, Any]], *, n: int,
    ) -> list[dict[str, Any]]: ...
    def wrap_bootstrap(self, goal: str) -> str: ...


# ---------------------------------------------------------------------------
# StatusPort — what HarnessRunner calls on the session_status module
# ---------------------------------------------------------------------------


@runtime_checkable
class StatusPort(Protocol):
    """Minimal surface HarnessRunner needs from session_status."""

    RETRY: str

    def normalize_session_status(
        self, value: Any, *, default: str | None = ...,
    ) -> str | None: ...
