"""EventBus contract tests — handler isolation, fan-out semantics."""

from __future__ import annotations

import logging

import pytest

from openloom.core.events import Event, EventBus, EventType


def _event(task_id: str = "t1") -> Event:
    return Event(type=EventType.TASK_CREATED, task_id=task_id)


def test_emit_to_single_subscriber() -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(EventType.TASK_CREATED, seen.append)
    bus.emit(_event())
    assert len(seen) == 1


def test_wildcard_subscribers_receive_all_events() -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    bus.emit(_event("a"))
    bus.emit(Event(type=EventType.TASK_COMPLETED, task_id="a"))
    assert len(seen) == 2


def test_handler_exception_does_not_block_others(caplog: pytest.LogCaptureFixture) -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(EventType.TASK_CREATED, seen.append)
    bus.subscribe(EventType.TASK_CREATED, lambda _e: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe(EventType.TASK_CREATED, seen.append)

    with caplog.at_level(logging.ERROR, logger="openloom.events"):
        bus.emit(_event())

    assert len(seen) == 2  # first + last both ran despite middle throwing
    assert any(rec.exc_info is not None for rec in caplog.records), \
        "expected at least one record with exc_info"


def test_wildcard_handler_exception_does_not_block_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    bus.subscribe_all(lambda _e: (_ for _ in ()).throw(ValueError("nope")))

    with caplog.at_level(logging.ERROR, logger="openloom.events"):
        bus.emit(_event())

    assert len(seen) == 1
    assert any(rec.exc_info is not None for rec in caplog.records)


def test_event_is_immutable() -> None:
    e = _event()
    with pytest.raises(Exception):
        e.task_id = "tampered"  # type: ignore[misc]
