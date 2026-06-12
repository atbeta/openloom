from __future__ import annotations

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
