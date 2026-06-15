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


def test_check_task_completes_when_agent_reports_task_complete_without_step_done(
    tmp_path: Path,
) -> None:
    """Regression: when the agent reports ``TASK COMPLETE`` in a single
    final assistant message (without first saying ``STEP DONE: <n>`` for
    every step), the harness must still mark the task completed.

    Scenario: agent did the work, did not restate every ``STEP DONE:``
    checkpoint, and closed the turn with a single ``TASK COMPLETE``.
    The spec has an ``## acceptance`` block but the agent did not
    rewrite the ``- [x]`` checkboxes. The previous code routed this to
    ``Waiting on final checks`` and nudged the agent forever; the agent
    would reply ``TASK COMPLETE`` again, the next tick would re-detect
    it, and we'd never close the task. After the fix
    ``task_is_finished`` trusts ``task_complete`` as the source of
    truth, so the task transitions to ``completed`` on this tick.
    """
    harness, task_id = _make_running_task(tmp_path)
    # _make_running_task builds a spec with one step and no acceptance;
    # override the stored spec so this test mirrors the real bug
    # (acceptance block present, agent says TASK COMPLETE only).
    spec_with_final = {
        "name": "Demo with final checks",
        "workspace": "/tmp",
        "goal": "Finish something",
        "steps": ["do the thing", "report back"],
        "acceptance": [
            "deliverable exists on disk",
            "smoke test passes",
        ],
    }
    harness.store.update_task(
        task_id, **{"spec": spec_with_final, "current_step": 0, "completed_steps": []}
    )

    class _AgentSaysTaskCompleteOnly(_TaskCompleteOpencode):
        async def messages(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
            return [{
                "info": {
                    "role": "assistant",
                    "time": {"created": 1.0, "completed": 2.0},
                },
                "parts": [
                    {"type": "text", "text": "All done. TASK COMPLETE"},
                ],
            }]

    harness.opencode = _AgentSaysTaskCompleteOnly()  # type: ignore[assignment]
    client: Any = harness.opencode
    events: list[Any] = []
    harness.bus.subscribe_all(events.append)

    asyncio.run(harness._check_task(harness.store.get_task(task_id)))

    # No nudges were sent — the agent already reported TASK COMPLETE.
    assert client.send_calls == [], (
        f"expected no nudges, got {client.send_calls!r}"
    )

    # Task transitioned to completed and TASK_COMPLETED was emitted.
    assert harness.store.get_task(task_id)["status"] == "completed"
    types = [e.type for e in events]
    assert EventType.TASK_COMPLETED in types

    # And the completed summary made it to the event payload — not
    # the "Waiting on final checks" or "Periodic check" summary.
    completed_event = next(
        e for e in events if e.type == EventType.TASK_COMPLETED
    )
    summary = completed_event.data.get("summary", "")
    assert "Waiting on final checks" not in summary
    assert "Periodic check" not in summary
    assert "TASK COMPLETE" in summary or "All steps appear complete" in summary


# --- session-bound dispatch: auto-archive + replaced_task_ids ---


def test_add_task_to_busy_session_archives_prior_task(tmp_path: Path) -> None:
    """Dispatching a new task to a session that already has a
    live task should archive the prior one, not race on the
    session transcript. The new task's spec carries
    replaced_task_ids so consumers can render the takeover."""
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec_old = prompts.TaskSpec(name="First", workspace="/tmp/ws", goal="g1")
    spec_new = prompts.TaskSpec(name="Second", workspace="/tmp/ws", goal="g2")

    # Manually move the first task into 'running' with the
    # session bound — that's the state add_task will see when
    # looking for active tasks to archive.
    old_id = harness.add_task(spec_old, active_session_id="ses_attached")
    asyncio.run(harness._start_task(harness.store.get_task(old_id)))

    # Now dispatch the second task to the same session.
    new_id = harness.add_task(spec_new, active_session_id="ses_attached")

    old = harness.store.get_task(old_id)
    new = harness.store.get_task(new_id)
    assert old is not None and old["status"] == "archived"
    assert new is not None
    assert new["spec"].get("replaced_task_ids") == [old_id]


def test_add_task_to_idle_session_does_not_archive(tmp_path: Path) -> None:
    """If no live task claims the session, the new task simply
    attaches without a takeover — no archive, no
    replaced_task_ids annotation."""
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec = prompts.TaskSpec(name="Solo", workspace="/tmp/ws", goal="g")

    tid = harness.add_task(spec, active_session_id="ses_attached")
    task = harness.store.get_task(tid)
    assert task is not None
    assert task["status"] == "pending"
    assert "replaced_task_ids" not in task["spec"]


def test_add_task_to_free_session_has_no_replaced_task_ids(tmp_path: Path) -> None:
    """When nothing was replaced, the spec omits the key (None /
    missing). Downstream consumers do
    ``list(spec_data.get("replaced_task_ids") or [])`` so a
    missing key behaves identically to an empty list."""
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec = prompts.TaskSpec(name="Plain", workspace="/tmp/ws", goal="g")

    tid = harness.add_task(spec, active_session_id="ses_attached")
    new = harness.store.get_task(tid)
    assert new["spec"].get("replaced_task_ids") in (None, [])


def test_add_task_no_session_does_not_archive_anything(tmp_path: Path) -> None:
    """Tasks without an active_session_id are solo — they each
    get their own session, so no archive should happen."""
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec = prompts.TaskSpec(name="A", workspace="/tmp", goal="a")
    spec_b = prompts.TaskSpec(name="B", workspace="/tmp", goal="b")

    a_id = harness.add_task(spec)
    b_id = harness.add_task(spec_b)
    a = harness.store.get_task(a_id)
    b = harness.store.get_task(b_id)
    assert a["status"] == "pending"
    assert b["status"] == "pending"
    assert b["spec"].get("replaced_task_ids") in (None, [])


def test_auto_archive_emits_task_updated_with_replaced_by_session(tmp_path: Path) -> None:
    """The auto-archive must emit an event so a webhook handler
    can show 'task was taken over'."""
    client = _RecordingOpencode()
    bus = EventBus()
    store = Store(tmp_path / "store.sqlite3")
    harness = HarnessRunner(
        opencode=client, bus=bus, store=store,
        checker=get_checker("string")(),
        prompts=prompts, status=session_status,
    )
    events: list[Any] = []
    bus.subscribe_all(events.append)
    spec_old = prompts.TaskSpec(name="Old", workspace="/tmp/ws", goal="g")
    spec_new = prompts.TaskSpec(name="New", workspace="/tmp/ws", goal="g")

    old_id = harness.add_task(spec_old, active_session_id="ses_attached")
    asyncio.run(harness._start_task(harness.store.get_task(old_id)))
    events.clear()

    new_id = harness.add_task(spec_new, active_session_id="ses_attached")
    updates = [e for e in events if e.type == EventType.TASK_UPDATED]
    # The first TASK_UPDATED after the new dispatch should be
    # the archive event for the old task.
    assert updates[0].task_id == old_id
    assert updates[0].data["status"] == "archived"
    assert updates[0].data["replaced_by_session"] == "ses_attached"
    assert updates[0].data["active_session_id"] == "ses_attached"
    # The TASK_CREATED for the new task must carry the
    # replaced_task_ids list so the UI can render a badge.
    created = [e for e in events if e.type == EventType.TASK_CREATED]
    assert created[0].task_id == new_id
    assert created[0].data["replaced_task_ids"] == [old_id]
    assert created[0].data["active_session_id"] == "ses_attached"


def test_store_list_active_tasks_for_session_filters_terminal(tmp_path: Path) -> None:
    """The store helper only returns pending/running/waiting
    tasks — completed/failed/archived are excluded so the
    auto-archive logic doesn't re-archive an already-finished
    task by accident."""
    store = Store(tmp_path / "store.sqlite3")
    # Two tasks on the same session, but they must coexist
    # artificially — the harness would normally auto-archive
    # the first when the second is added, so we use the store
    # directly to set up the test fixture.
    a_record = {
        "id": "task_a", "name": "a", "workspace": "/w",
        "spec": {"name": "a", "workspace": "/w", "goal": "g"},
        "status": "pending", "current_step": 0, "completed_steps": [],
        "idle_checks": 0, "progress": 0.0,
        "check_interval_seconds": 300, "next_check_at": 0.0,
        "active_session_id": "ses_x", "session_ids": ["ses_x"],
        "last_summary": None, "error": None, "check_log": [],
        "created_at": 1.0, "updated_at": 1.0, "last_check_at": None,
    }
    b_record = dict(a_record)
    b_record["id"] = "task_b"
    b_record["name"] = "b"
    b_record["spec"] = {"name": "b", "workspace": "/w", "goal": "g"}
    b_record["status"] = "pending"
    b_record["created_at"] = 2.0
    b_record["updated_at"] = 2.0
    store.create_task(a_record)
    store.create_task(b_record)

    # Both pending → both active for the session.
    live = store.list_active_tasks_for_session("ses_x")
    assert {t["id"] for t in live} == {"task_a", "task_b"}

    # Mark b completed → only a remains.
    store.update_task("task_b", status="completed", next_check_at=None)
    assert {t["id"] for t in store.list_active_tasks_for_session("ses_x")} == {"task_a"}

    # Mark a archived → empty.
    store.update_task("task_a", status="archived", next_check_at=None)
    assert store.list_active_tasks_for_session("ses_x") == []

    # An unknown session is simply empty.
    assert store.list_active_tasks_for_session("ses_nope") == []


# --- session-dropped callback (wired to monitor.forget_session) ---


def test_session_dropped_fires_on_archive(tmp_path: Path) -> None:
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec = prompts.TaskSpec(name="A", workspace="/tmp/ws", goal="g")
    dropped: list[str] = []
    harness.on_session_dropped(dropped.append)

    tid = harness.add_task(spec, active_session_id="ses_attached")
    asyncio.run(harness._start_task(harness.store.get_task(tid)))
    harness.archive_task(tid)
    assert dropped == ["ses_attached"]


def test_session_dropped_fires_on_pause(tmp_path: Path) -> None:
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec = prompts.TaskSpec(name="A", workspace="/tmp/ws", goal="g")
    dropped: list[str] = []
    harness.on_session_dropped(dropped.append)

    tid = harness.add_task(spec, active_session_id="ses_attached")
    asyncio.run(harness._start_task(harness.store.get_task(tid)))
    harness.pause_task(tid)
    assert dropped == ["ses_attached"]


def test_session_dropped_fires_on_complete(tmp_path: Path) -> None:
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec = prompts.TaskSpec(name="A", workspace="/tmp/ws", goal="g")
    dropped: list[str] = []
    harness.on_session_dropped(dropped.append)

    tid = harness.add_task(spec, active_session_id="ses_attached")
    asyncio.run(harness._start_task(harness.store.get_task(tid)))
    harness.complete_task(tid)
    assert dropped == ["ses_attached"]


def test_session_dropped_fires_on_auto_pause(tmp_path: Path) -> None:
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec = prompts.TaskSpec(name="A", workspace="/tmp/ws", goal="g")
    dropped: list[str] = []
    harness.on_session_dropped(dropped.append)

    tid = harness.add_task(spec, active_session_id="ses_attached")
    asyncio.run(harness._start_task(harness.store.get_task(tid)))
    harness._auto_pause(tid, "budget exceeded")
    assert dropped == ["ses_attached"]


def test_session_dropped_does_not_fire_for_tasks_without_session(tmp_path: Path) -> None:
    """Tasks with no active_session_id emit a '' drop, which
    the callback short-circuits on. We assert the callback
    never receives a non-empty id for these tasks."""
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec = prompts.TaskSpec(name="A", workspace="/tmp", goal="g")
    dropped: list[str] = []
    harness.on_session_dropped(dropped.append)

    tid = harness.add_task(spec)  # no session
    harness.archive_task(tid)
    # No session-bound task → handler should not be invoked.
    assert dropped == []


def test_session_dropped_does_not_fire_on_auto_archive_replaced(tmp_path: Path) -> None:
    """The auto-archive path runs *because* a new task is
    taking over the same session. We must NOT drop the
    monitor state for that session — the new task will be
    the observer within microseconds of the old task's
    archive, and dropping the monitor state would race with
    the new task's registration."""
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec_a = prompts.TaskSpec(name="A", workspace="/tmp/ws", goal="g")
    spec_b = prompts.TaskSpec(name="B", workspace="/tmp/ws", goal="g")
    dropped: list[str] = []
    harness.on_session_dropped(dropped.append)

    a = harness.add_task(spec_a, active_session_id="ses_attached")
    asyncio.run(harness._start_task(harness.store.get_task(a)))
    harness.add_task(spec_b, active_session_id="ses_attached")
    assert dropped == []


def test_session_dropped_handler_failure_does_not_block(tmp_path: Path) -> None:
    """A handler raising must not stop other handlers from
    firing, and must not block the harness API call."""
    client = _RecordingOpencode()
    harness = _make_harness_with(tmp_path, client)
    spec = prompts.TaskSpec(name="A", workspace="/tmp/ws", goal="g")

    def bad_handler(_: str) -> None:
        raise RuntimeError("boom")

    good: list[str] = []
    harness.on_session_dropped(bad_handler)
    harness.on_session_dropped(good.append)

    tid = harness.add_task(spec, active_session_id="ses_attached")
    asyncio.run(harness._start_task(harness.store.get_task(tid)))
    # archive_task must complete despite bad_handler raising.
    harness.archive_task(tid)
    assert good == ["ses_attached"]
