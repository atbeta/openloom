from __future__ import annotations

from pathlib import Path
from typing import Any

from openloom.core.events import EventBus, EventType
from openloom.core.harness import HarnessRunner
from openloom.core.store import Store, new_task_record
from openloom.runtime import prompts, session_status


class _OpencodeStub:
    pass


def _harness(
    tmp_path: Path,
    *,
    idle_completes_task: bool = False,
    auto_accept_permissions: bool = True,
    opencode: Any = None,
) -> tuple[HarnessRunner, EventBus]:
    store = Store(tmp_path / "store.sqlite3")
    bus = EventBus()
    harness = HarnessRunner(
        opencode=opencode or _OpencodeStub(),
        bus=bus,
        store=store,
        prompts=prompts,
        status=session_status,
        idle_completes_task=idle_completes_task,
        auto_accept_permissions=auto_accept_permissions,
    )
    return harness, bus


def test_new_task_record_defaults() -> None:
    task = new_task_record(
        task_id="task_test",
        name="Demo",
        spec={"name": "Demo"},
        workspace="/tmp/ws",
        active_session_id="sess_1",
    )
    assert task["status"] == "pending"
    assert task["session_ids"] == ["sess_1"]
    assert task["active_session_id"] == "sess_1"
    assert task["progress"] == 0.0


def test_manual_complete_emits_updated_and_completed(tmp_path: Path) -> None:
    harness, bus = _harness(tmp_path)
    events: list[Any] = []
    bus.subscribe_all(events.append)
    spec = prompts.TaskSpec(name="t", workspace="/tmp", goal="do it")
    tid = harness.add_task(spec)
    events.clear()
    harness.complete_task(tid)
    types = [e.type for e in events]
    assert EventType.TASK_UPDATED in types
    assert EventType.TASK_COMPLETED in types


# ── idle_completes_task ────────────────────────────────────────────────


class _SessionStub:
    """OpenCode port stub for ``_check_task``.

    Returns a configurable ``session_status`` map, no permissions, and a
    single assistant message (so ``recent_activity`` is non-empty but
    contains no ``TASK COMPLETE`` marker).
    """

    def __init__(
        self,
        *,
        session_status: str | None = "idle",
        messages: list[dict[str, Any]] | None = None,
        pending_permissions: list[dict[str, Any]] | None = None,
    ) -> None:
        self._session_status_value = session_status
        default_msg = {
            "role": "assistant",
            "info": {"role": "assistant", "time": {"completed": 1234.0}},
            "parts": [],
            "content": "I have answered your question.",
        }
        self._messages = messages if messages is not None else [default_msg]
        self._pending_permissions = pending_permissions or []
        self.responded: list[tuple[str, str]] = []  # (session_id, permission_id)

    async def session_status(self) -> dict[str, str]:
        return {"sess_1": self._session_status_value} if self._session_status_value else {}

    async def resolve_session_permissions(self, session_id: str) -> dict[str, Any] | None:
        if not self._pending_permissions:
            return None
        return {
            "status": "waiting",
            "summary": "Waiting for permission approval",
            "pending": list(self._pending_permissions),
        }

    async def respond_permission(
        self,
        session_id: str,
        permission_id: str,
        response: str = "once",
        **kwargs: Any,
    ) -> bool:
        self.responded.append((session_id, permission_id))
        return True

    async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._messages)

    async def list_sessions(self) -> list[dict[str, Any]]:
        return []

    async def create_session(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"id": "sess_1"}

    async def send_message(self, *args: Any, **kwargs: Any) -> Any:
        return None


async def _run_check(harness: HarnessRunner, task: dict[str, Any]) -> None:
    await harness._check_task(task)


def _completed_task(tmp_path: Path) -> dict[str, Any]:
    """Build a task already past the start phase so ``_check_task`` runs the
    status decision branch instead of routing to ``_start_task``."""
    harness, _ = _harness(tmp_path)
    spec = prompts.TaskSpec(name="t", workspace="/tmp", goal="do it")
    tid = harness.add_task(spec)
    # Drive it through pending → running by stubbing the start path: just
    # set the task's status and active_session_id directly on the store.
    harness.store.update_task(
        tid, status="running", active_session_id="sess_1",
    )
    return harness.store.get_task(tid)  # type: ignore[return-value]


async def test_idle_without_marker_stays_running_when_flag_disabled(
    tmp_path: Path,
) -> None:
    """With idle_completes_task=False (opt-out), an idle session with no
    TASK COMPLETE marker must stay in ``running`` so the operator can see
    the agent has gone quiet. The default since 0.13.6 is True; this test
    exercises the explicit opt-out path."""
    task = _completed_task(tmp_path)
    harness, bus = _harness(
        tmp_path, idle_completes_task=False, opencode=_SessionStub(),
    )
    events: list[Any] = []
    bus.subscribe_all(events.append)
    await _run_check(harness, task)
    types = [e.type for e in events]
    assert EventType.TASK_UPDATED in types
    # No TASK_COMPLETED without idle_completes_task or TASK COMPLETE marker.
    assert EventType.TASK_COMPLETED not in types
    stored = harness.store.get_task(task["id"])
    assert stored["status"] == "running"


def test_settings_idle_completes_task_defaults_to_true() -> None:
    """``idle_completes_task`` is True by default since 0.13.6 — webhook
    / connector users want the task to terminate as soon as the agent
    stops responding. The harness layer will revisit this default once
    retry / nudge controls land."""
    from openloom.config import Settings

    s = Settings(
        opencode_url="http://127.0.0.1:4096",
        opencode_username="opencode",
        opencode_password="",
        database_path=Path("/tmp/x.sqlite3"),
    )
    assert s.idle_completes_task is True


async def test_idle_with_idle_completes_task_emits_completed(tmp_path: Path) -> None:
    task = _completed_task(tmp_path)
    harness, bus = _harness(
        tmp_path, idle_completes_task=True, opencode=_SessionStub(),
    )
    events: list[Any] = []
    bus.subscribe_all(events.append)
    await _run_check(harness, task)
    types = [e.type for e in events]
    assert EventType.TASK_COMPLETED in types
    stored = harness.store.get_task(task["id"])
    assert stored["status"] == "completed"
    stored = harness.store.get_task(task["id"])
    assert stored["status"] == "completed"
    # Also: summary text reflects the new "idle-as-complete" mode.
    updated = next(e for e in events if e.type == EventType.TASK_UPDATED)
    assert "idle" in updated.data["summary"].lower()


async def test_idle_completes_task_does_not_fire_for_brand_new_session(tmp_path: Path) -> None:
    """``idle_completes_task`` must not auto-complete a task whose session
    has produced no assistant activity yet — otherwise a freshly-created
    task would be marked complete before the agent even responded."""
    task = _completed_task(tmp_path)
    harness, bus = _harness(
        tmp_path,
        idle_completes_task=True,
        opencode=_SessionStub(messages=[]),  # empty transcript
    )
    events: list[Any] = []
    bus.subscribe_all(events.append)
    await _run_check(harness, task)
    types = [e.type for e in events]
    assert EventType.TASK_COMPLETED not in types
    stored = harness.store.get_task(task["id"])
    assert stored["status"] == "running"


async def test_idle_completes_task_does_not_override_busy(tmp_path: Path) -> None:
    """A busy agent must never be marked completed, even with the flag on."""
    task = _completed_task(tmp_path)

    class BusyStub(_SessionStub):
        pass

    busy = _SessionStub()
    # Override messages_indicate_busy by injecting an in-progress tool call
    busy._messages = [{
        "role": "assistant",
        "info": {"role": "assistant"},  # no time.completed → still in flight
        "parts": [{
            "type": "tool",
            "state": {"status": "running"},
        }],
        "content": "",
    }]
    harness, bus = _harness(
        tmp_path, idle_completes_task=True, opencode=busy,
    )
    events: list[Any] = []
    bus.subscribe_all(events.append)
    await _run_check(harness, task)
    types = [e.type for e in events]
    assert EventType.TASK_COMPLETED not in types
    stored = harness.store.get_task(task["id"])
    assert stored["status"] == "running"


async def test_task_complete_marker_still_works_with_idle_flag_on(tmp_path: Path) -> None:
    """The TASK COMPLETE marker should take precedence over idle when both
    conditions apply — marker is the explicit signal."""
    task = _completed_task(tmp_path)
    stub = _SessionStub(messages=[{
        "role": "assistant",
        "info": {"role": "assistant", "time": {"completed": 1234.0}},
        "parts": [],
        "text": "All done. TASK COMPLETE",
    }])
    harness, bus = _harness(
        tmp_path, idle_completes_task=True, opencode=stub,
    )
    events: list[Any] = []
    bus.subscribe_all(events.append)
    await _run_check(harness, task)
    types = [e.type for e in events]
    assert EventType.TASK_COMPLETED in types
    updated = next(e for e in events if e.type == EventType.TASK_UPDATED)
    assert updated.data["summary"] == "Agent reported TASK COMPLETE"


# ── auto_accept_permissions ────────────────────────────────────────────


async def test_auto_accept_permissions_responds_once(tmp_path: Path) -> None:
    """When a permission is pending and auto_accept is on (default),
    the harness must call ``respond_permission`` so the agent does not
    stay stuck in ``waiting``. Without auto_accept (or with the env var
    explicitly disabled), the harness must leave the permission alone.
    """
    task = _completed_task(tmp_path)
    stub = _SessionStub(pending_permissions=[
        {"id": "perm_abc", "sessionId": "sess_1", "permission": "bash"},
    ])

    # auto_accept ON — harness responds once, task no longer waiting.
    harness, bus = _harness(
        tmp_path, auto_accept_permissions=True, opencode=stub,
    )
    events: list[Any] = []
    bus.subscribe_all(events.append)
    await _run_check(harness, task)
    assert ("sess_1", "perm_abc") in stub.responded
    stored = harness.store.get_task(task["id"])
    # After auto-accept, the next check should see status="running"
    # because the session is no longer blocked. But this single tick
    # may still show "waiting" — the assert below documents the
    # immediate behaviour; what matters is that the harness *did*
    # respond and would not deadlock.
    assert stored["status"] in {"waiting", "running"}


async def test_auto_accept_disabled_does_not_respond(tmp_path: Path) -> None:
    """With auto_accept_permissions=False the harness must leave
    pending permissions alone so the operator can answer them via
    the dashboard's /api/permissions endpoint."""
    task = _completed_task(tmp_path)
    stub = _SessionStub(pending_permissions=[
        {"id": "perm_xyz", "sessionId": "sess_1", "permission": "bash"},
    ])
    harness, bus = _harness(
        tmp_path, auto_accept_permissions=False, opencode=stub,
    )
    events: list[Any] = []
    bus.subscribe_all(events.append)
    await _run_check(harness, task)
    assert stub.responded == []
    stored = harness.store.get_task(task["id"])
    assert stored["status"] == "waiting"


async def test_auto_accept_handles_multiple_pending_permissions(tmp_path: Path) -> None:
    """When several permissions are queued at once, every one gets
    a "once" reply in the same tick."""
    task = _completed_task(tmp_path)
    stub = _SessionStub(pending_permissions=[
        {"id": "perm_1", "sessionId": "sess_1", "permission": "bash"},
        {"id": "perm_2", "sessionId": "sess_1", "permission": "edit"},
        {"id": "perm_3", "sessionId": "sess_1", "permission": "fetch"},
    ])
    harness, bus = _harness(
        tmp_path, auto_accept_permissions=True, opencode=stub,
    )
    await _run_check(harness, task)
    assert {pid for _, pid in stub.responded} == {"perm_1", "perm_2", "perm_3"}


async def test_auto_accept_continue_when_respond_fails(tmp_path: Path) -> None:
    """If respond_permission raises on one entry, the harness must
    not abort the whole check — try the rest, log the failure."""
    task = _completed_task(tmp_path)

    class FailingStub(_SessionStub):
        async def respond_permission(
            self, session_id: str, permission_id: str,
            response: str = "once", **kwargs: Any,
        ) -> bool:
            if permission_id == "perm_bad":
                raise RuntimeError("upstream timeout")
            return await super().respond_permission(
                session_id, permission_id, response,
            )

    stub = FailingStub(pending_permissions=[
        {"id": "perm_bad", "sessionId": "sess_1", "permission": "bash"},
        {"id": "perm_ok", "sessionId": "sess_1", "permission": "edit"},
    ])
    harness, bus = _harness(
        tmp_path, auto_accept_permissions=True, opencode=stub,
    )
    await _run_check(harness, task)
    # The healthy permission still got answered.
    assert ("sess_1", "perm_ok") in stub.responded


def test_settings_auto_accept_permissions_defaults_to_true() -> None:
    """``auto_accept_permissions`` is True by default since 0.13.6 —
    webhook / connector users are usually remote and cannot drive the
    dashboard to click 'Allow'."""
    from openloom.config import Settings

    s = Settings(
        opencode_url="http://127.0.0.1:4096",
        opencode_username="opencode",
        opencode_password="",
        database_path=Path("/tmp/x.sqlite3"),
    )
    assert s.auto_accept_permissions is True


