"""
Protocol definitions for HarnessRunner dependencies.

These replace ``Any`` type annotations so that mypy can verify the wiring
between core, runtime, and levels without introducing circular imports.
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
    async def messages(self, session_id: str, *, limit: int = 50) -> list[dict[str, Any]]: ...
    async def resolve_session_permissions(
        self, session_id: str, auto_accept: bool,
    ) -> dict[str, str] | None: ...
    async def send_prompt_async(
        self, *, session_id: str, prompt: str, agent: str | None = None,
    ) -> None: ...
    async def create_session(self, *, cwd: str, title: str) -> dict[str, Any]: ...


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
    def list_active_tasks_for_session(
        self, session_id: str,
    ) -> list[dict[str, Any]]: ...
    def append_check_log(
        self,
        task_id: str,
        *,
        status: str,
        summary: str,
        detail: str = "",
    ) -> int: ...


# ---------------------------------------------------------------------------
# CheckerPort — what HarnessRunner calls on the progress checker
# ---------------------------------------------------------------------------

@runtime_checkable
class CheckerPort(Protocol):
    """Minimal surface HarnessRunner needs from a Checker."""

    def check(self, messages: list[dict[str, Any]], spec: Any) -> CheckResultProtocol: ...


@runtime_checkable
class CheckResultProtocol(Protocol):
    """Structural shape of a CheckResult."""

    @property
    def task_complete(self) -> bool: ...
    @property
    def step_done(self) -> int: ...
    @property
    def acceptance_checked(self) -> int: ...
    @property
    def acceptance_total(self) -> int: ...
    @property
    def acceptance_progress(self) -> float: ...


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
    notify payload. New webhook handlers can also call
    ``detect_progress`` client-side, but that is a pure function
    on a string and does not need to live on the protocol.
    """

    # Class-like: TaskSpec constructor (type[Any] — has .from_dict / .to_dict)
    TaskSpec: Any

    def messages_indicate_busy(self, messages: list[dict[str, Any]]) -> bool: ...
    def recent_assistant_activity(
        self, messages: list[dict[str, Any]], *, n: int = ...,
    ) -> list[dict[str, Any]]: ...
    def session_total_tokens(self, session: dict[str, Any]) -> int: ...


# ---------------------------------------------------------------------------
# StatusPort — what HarnessRunner calls on the session_status module
# ---------------------------------------------------------------------------

@runtime_checkable
class StatusPort(Protocol):
    """Minimal surface HarnessRunner needs from session_status."""

    RETRY: str

    def normalize_session_status(self, value: Any, *, default: str | None = ...) -> str | None: ...
