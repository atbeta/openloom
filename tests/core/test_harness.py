from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import openloom.levels.manual.checker  # noqa: F401
from openloom.core.events import EventBus, EventType
from openloom.core.harness import HarnessRunner
from openloom.core.registry import get_checker
from openloom.core.store import Store, new_task_record
from openloom.runtime import prompts, session_status


class _OpencodeStub:
    pass


def _harness(tmp_path: Path) -> tuple[HarnessRunner, EventBus]:
    store = Store(tmp_path / "store.sqlite3")
    bus = EventBus()
    harness = HarnessRunner(
        opencode=_OpencodeStub(),
        bus=bus,
        store=store,
        checker=get_checker("string")(),
        prompts=prompts,
        status=session_status,
    )
    return harness, bus


def test_new_task_record_defaults() -> None:
    task = new_task_record(
        task_id="task_test",
        name="Demo",
        spec={"name": "Demo"},
        workspace="/tmp/ws",
        check_interval_seconds=300,
        active_session_id="sess_1",
    )
    assert task["status"] == "pending"
    assert task["current_step"] == 0
    assert task["session_ids"] == ["sess_1"]
    assert task["active_session_id"] == "sess_1"
    assert task["next_check_at"] is not None


def test_manual_complete_emits_updated_and_completed(tmp_path: Path) -> None:
    harness, bus = _harness(tmp_path)
    events: list[Any] = []
    bus.subscribe_all(events.append)
    tid = harness.add_task(
        {"name": "t", "workspace": "/tmp", "check_interval_seconds": 300, "steps": ["one"]},
    )
    events.clear()
    harness.complete_task(tid)
    types = [e.type for e in events]
    assert EventType.TASK_UPDATED in types
    assert EventType.TASK_COMPLETED in types


class _RecordingOpencode:
    """Captures call order so the test can assert abort runs *before* send."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "ses_attached",
                "directory": "/tmp/ws",
                "title": "existing",
                "time": {"created": 1.0, "updated": 1.0, "archived": 0},
                "parentID": None,
            },
        ]

    async def abort_session(self, session_id: str) -> bool:
        self.calls.append(("abort", (session_id,)))
        return True

    async def send_prompt_async(self, session_id: str, prompt: str, agent: str | None = None) -> None:
        self.calls.append(("send", (session_id, prompt, agent)))


def _make_harness_with(tmp_path: Path, client: Any) -> HarnessRunner:
    store = Store(tmp_path / "store.sqlite3")
    bus = EventBus()
    return HarnessRunner(
        opencode=client, bus=bus, store=store,
        checker=get_checker("string")(),
        prompts=prompts, status=session_status,
    )


def test_start_task_calls_abort_before_send_when_flag_set(tmp_path: Path) -> None:
    """The inbox ``abort: true`` flag must trigger an
    ``abort_session`` call *before* ``send_prompt_async`` — otherwise
    the new prompt lands in the queue behind the stuck tool."""
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)

    spec = prompts.TaskSpec(
        name="Resume after the hang",
        workspace="/tmp/ws",
        goal="Pick up from where you stopped.",
        abort_session=True,
    )
    task_id = harness.add_task(spec, active_session_id="ses_attached")

    asyncio.run(harness._start_task(harness.store.get_task(task_id)))

    method_sequence = [c[0] for c in client.calls]
    assert method_sequence == ["abort", "send"], (
        f"expected abort then send, got {method_sequence}"
    )
    assert client.calls[0][1] == ("ses_attached",)


def test_start_task_skips_abort_when_flag_not_set(tmp_path: Path) -> None:
    """Regular watch dispatches must NEVER abort an existing
    session — that would silently destroy in-flight work."""
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)

    spec = prompts.TaskSpec(
        name="Continue",
        workspace="/tmp/ws",
        goal="Continue working.",
        abort_session=False,
    )
    task_id = harness.add_task(spec, active_session_id="ses_attached")

    asyncio.run(harness._start_task(harness.store.get_task(task_id)))

    method_sequence = [c[0] for c in client.calls]
    assert "abort" not in method_sequence
    assert method_sequence == ["send"]


# --- Periodic-check / task-finished regression tests -----------------------

class _TaskCompleteOpencode:
    """Stub that simulates an agent which has just replied with
    ``TASK COMPLETE``. The harness must transition the task to
    ``completed`` and emit ``TASK_COMPLETED`` — but it must NOT
    re-prompt the agent with a periodic-check nudge asking the
    agent to confirm completion again. That re-prompt loop is the
    bug this test guards against."""

    def __init__(self) -> None:
        self.send_calls: list[tuple[str, str, Any]] = []

    async def list_sessions(self) -> list[dict[str, Any]]:
        return []

    async def session_status(self) -> dict[str, Any]:
        # Idle session — not running, not waiting, no error.
        return {"ses_a": {"type": "session", "status": {"type": "idle"}}}

    async def messages(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        # A single assistant message that says TASK COMPLETE.
        return [{
            "info": {
                "role": "assistant",
                "time": {"created": 1.0, "completed": 2.0},
            },
            "parts": [
                {"type": "text", "text": "All done. TASK COMPLETE"},
            ],
        }]

    async def resolve_session_permissions(self, session_id: str, auto: bool) -> dict[str, Any] | None:
        return None

    async def send_prompt_async(
        self, session_id: str, prompt: str, agent: str | None = None,
    ) -> None:
        self.send_calls.append((session_id, prompt, agent))


def _make_running_task(tmp_path: Path) -> tuple[HarnessRunner, str]:
    """Create a task already in 'running' state with an active session.

    The harness short-circuits ``_check_task`` early for tasks that
    are pending / paused / completed / failed / archived, so we have
    to seed the store with a 'running' task directly to exercise the
    task-finished branch.
    """
    from openloom.core.store import new_task_record
    store = Store(tmp_path / "store.sqlite3")
    rec = new_task_record(
        task_id="task_done",
        name="Demo",
        spec={
            "name": "Demo",
            "workspace": "/tmp",
            "goal": "Finish something",
            "steps": ["do the thing"],
        },
        workspace="/tmp",
        check_interval_seconds=300,
        active_session_id="ses_a",
    )
    rec["status"] = "running"
    store.create_task(rec)
    bus = EventBus()
    harness = HarnessRunner(
        opencode=_TaskCompleteOpencode(),
        bus=bus,
        store=store,
        checker=get_checker("string")(),
        prompts=prompts,
        status=session_status,
    )
    return harness, "task_done"


def test_check_task_does_not_nudge_when_agent_reported_complete(tmp_path: Path) -> None:
    """Regression: harness must NOT re-send a periodic-check nudge
    after the agent has already replied with ``TASK COMPLETE``.
    Before the fix the same task got a fresh ``Periodic check —
    requested status confirmation`` nudge on every tick, even
    though ``task_is_finished`` already evaluated to True."""
    harness, task_id = _make_running_task(tmp_path)
    client: Any = harness.opencode  # type: ignore[assignment]
    events: list[Any] = []
    harness.bus.subscribe_all(events.append)

    asyncio.run(harness._check_task(harness.store.get_task(task_id)))

    # No nudges were sent — the agent had already said TASK COMPLETE.
    assert client.send_calls == [], (
        f"expected no nudges, got {client.send_calls!r}"
    )

    # Task is now completed, and TASK_COMPLETED was emitted.
    assert harness.store.get_task(task_id)["status"] == "completed"
    types = [e.type for e in events]
    assert EventType.TASK_COMPLETED in types

    # And the completed summary made it to the event payload — not
    # the periodic-check summary, which is what the buggy code path
    # produced.
    completed_event = next(
        e for e in events if e.type == EventType.TASK_COMPLETED
    )
    summary = completed_event.data.get("summary", "")
    assert "Periodic check" not in summary
