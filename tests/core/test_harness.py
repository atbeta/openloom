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
    ) -> None:
        self._session_status_value = session_status
        default_msg = {
            "role": "assistant",
            "info": {"role": "assistant", "time": {"completed": 1234.0}},
            "parts": [],
            "content": "I have answered your question.",
        }
        self._messages = messages if messages is not None else [default_msg]

    async def session_status(self) -> dict[str, str]:
        return {"sess_1": self._session_status_value} if self._session_status_value else {}

    async def resolve_session_permissions(self, session_id: str) -> dict[str, Any] | None:
        return None

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


async def test_idle_without_marker_stays_running_by_default(tmp_path: Path) -> None:
    task = _completed_task(tmp_path)
    harness, bus = _harness(tmp_path, opencode=_SessionStub())
    events: list[Any] = []
    bus.subscribe_all(events.append)
    await _run_check(harness, task)
    types = [e.type for e in events]
    assert EventType.TASK_UPDATED in types
    # No TASK_COMPLETED without idle_completes_task or TASK COMPLETE marker.
    assert EventType.TASK_COMPLETED not in types
    stored = harness.store.get_task(task["id"])
    assert stored["status"] == "running"


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


