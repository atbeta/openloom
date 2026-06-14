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
    """Minimal surface HarnessRunner needs from the prompts module."""

    # Class-like: TaskSpec constructor (type[Any] — has .from_dict / .to_dict)
    TaskSpec: Any

    # Module constants
    MIN_CHECK_INTERVAL_SECONDS: int
    MAX_IDLE_NUDGES: int

    def messages_indicate_busy(self, messages: list[dict[str, Any]]) -> bool: ...
    def task_is_finished(
        self,
        *,
        task_complete: bool,
        step_done: int,
        acceptance_checked: int,
        step_count: int,
        acceptance_count: int,
    ) -> bool: ...
    def needs_asking_reply(self, messages: list[dict[str, Any]]) -> bool: ...
    def auto_decide_reply(self, *, step_name: str | None = None) -> str: ...
    def build_final_checks_nudge(self, spec: Any) -> str: ...
    def build_bootstrap_prompt(self, spec: Any, *, current_step: int = 0) -> str: ...
    def build_periodic_check_prompt(
        self,
        spec: Any,
        *,
        current_step: int,
        progress: dict[str, Any],
        completed_steps: list[int],
    ) -> str: ...
    def already_nudged(self, task: dict[str, Any], nudge: str) -> bool: ...
    def nudge_fingerprint(self, text: str) -> str: ...
    def session_total_tokens(self, session: dict[str, Any]) -> int: ...


# ---------------------------------------------------------------------------
# StatusPort — what HarnessRunner calls on the session_status module
# ---------------------------------------------------------------------------

@runtime_checkable
class StatusPort(Protocol):
    """Minimal surface HarnessRunner needs from session_status."""

    RETRY: str

    def normalize_session_status(self, value: Any, *, default: str | None = ...) -> str | None: ...
