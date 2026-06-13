"""Tests for the inbox level — parsing, watcher (single-file), rename semantics."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from openloom.levels.inbox import safe_rename, sanitise_tag
from openloom.levels.inbox.watcher import InboxWatcher

SAMPLE_MD = """# Fix the kitchen sink

workspace: /home/me/projects/house

## goal
Replace the gasket and verify no leak.
"""

MINIMAL_MD = """# Just a title

Do the thing.
"""


async def _noop_dispatch(_payload: dict[str, Any]) -> str | None:
    return "task_abc123def456"


def _make_capturing_dispatch(captured: list[dict[str, Any]]) -> Any:
    counter = {"n": 0}

    async def _d(payload: dict[str, Any]) -> str:
        counter["n"] += 1
        captured.append(payload)
        return f"task_{counter['n']:03d}"

    return _d


def _make_watcher(
    inbox: Path,
    dispatch: Any,
    *,
    filename: str = "task.md",
    workspace: str = "/srv",
    poll: float = 1.0,
) -> InboxWatcher:
    return InboxWatcher(
        directory=inbox,
        dispatch=dispatch,
        default_workspace=workspace,
        filename=filename,
        poll_interval_seconds=poll,
    )


# --- watcher: file presence semantics ---


async def test_tick_is_noop_when_target_missing(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    watcher = _make_watcher(inbox, _noop_dispatch)
    assert await watcher.tick() is False
    assert list(inbox.iterdir()) == []


async def test_tick_is_noop_when_directory_missing(tmp_path: Path) -> None:
    watcher = _make_watcher(tmp_path / "nope", _noop_dispatch)
    assert await watcher.tick() is False


async def test_default_filename_is_task_md() -> None:
    watcher = InboxWatcher(
        directory=Path("/tmp"),
        dispatch=_noop_dispatch,
    )
    assert watcher.target_path.name == "task.md"


async def test_filename_configurable(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "trigger.md").write_text(MINIMAL_MD)
    watcher = _make_watcher(inbox, _noop_dispatch, filename="trigger.md")
    assert await watcher.tick() is True
    assert not (inbox / "trigger.md").exists()
    assert any(p.name.startswith("trigger.md.processed-") for p in inbox.iterdir())


async def test_blank_filename_falls_back_to_task_md(tmp_path: Path) -> None:
    watcher = InboxWatcher(
        directory=tmp_path, dispatch=_noop_dispatch, filename="   ",
    )
    assert watcher.target_path.name == "task.md"


# --- watcher: dispatch ---


async def test_dispatches_existing_file_and_renames(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "task.md").write_text(SAMPLE_MD)

    captured: list[dict[str, Any]] = []
    watcher = _make_watcher(inbox, _make_capturing_dispatch(captured))
    dispatched = await watcher.tick()

    assert dispatched is True
    assert not (inbox / "task.md").exists()
    renamed = list(inbox.iterdir())
    assert len(renamed) == 1
    assert renamed[0].name.startswith("task.md.processed-task_001")
    assert len(captured) == 1
    assert captured[0]["name"] == "Fix the kitchen sink"
    assert captured[0]["_inbox_path"].endswith("task.md")


async def test_default_workspace_fills_in(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "task.md").write_text(MINIMAL_MD)
    captured: list[dict[str, Any]] = []
    watcher = _make_watcher(
        inbox, _make_capturing_dispatch(captured), workspace="/srv/default",
    )
    await watcher.tick()
    assert captured[0]["workspace"] == "/srv/default"


async def test_missing_workspace_marks_error(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "task.md").write_text(MINIMAL_MD)
    watcher = _make_watcher(inbox, _noop_dispatch, workspace="")
    assert await watcher.tick() is False
    renamed = [p.name for p in inbox.iterdir()]
    assert renamed[0].startswith("task.md.error-")


async def test_dispatch_exception_renames_to_error(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "task.md").write_text(SAMPLE_MD)

    async def failing(_p: dict[str, Any]) -> str:
        raise RuntimeError("dispatcher down")

    watcher = _make_watcher(inbox, failing)
    await watcher.tick()

    names = [p.name for p in inbox.iterdir()]
    assert len(names) == 1
    assert names[0].startswith("task.md.error-dispatch-raised")


async def test_dispatch_returning_none_renames_skipped(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "task.md").write_text(SAMPLE_MD)

    async def declining(_p: dict[str, Any]) -> str | None:
        return None

    watcher = _make_watcher(inbox, declining)
    assert await watcher.tick() is False
    names = [p.name for p in inbox.iterdir()]
    assert names[0].startswith("task.md.skipped")


# --- watcher: cycle / queue semantics ---


async def test_subsequent_ticks_idle_until_new_file_appears(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "task.md").write_text(SAMPLE_MD)
    captured: list[dict[str, Any]] = []
    watcher = _make_watcher(inbox, _make_capturing_dispatch(captured))

    assert await watcher.tick() is True
    # file is now renamed; further ticks are no-ops
    assert await watcher.tick() is False
    assert await watcher.tick() is False
    assert len(captured) == 1

    # drop a new task.md → next tick picks it up
    (inbox / "task.md").write_text("# Second\n\nDo another thing.\n")
    assert await watcher.tick() is True
    assert len(captured) == 2
    assert captured[1]["name"] == "Second"


async def test_picks_up_new_file_with_same_name(tmp_path: Path) -> None:
    """External sync tool overwrites the same filename: watcher must fire again."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "task.md").write_text("# First\n\nDo A.\n")
    captured: list[dict[str, Any]] = []
    watcher = _make_watcher(inbox, _make_capturing_dispatch(captured))

    await watcher.tick()
    # simulate Dropbox re-writing the file under the same name
    (inbox / "task.md").write_text("# Second\n\nDo B.\n")
    await watcher.tick()

    assert [c["name"] for c in captured] == ["First", "Second"]
    processed = sorted(p.name for p in inbox.iterdir())
    assert all(n.startswith("task.md.processed-task_") for n in processed)


async def test_other_files_in_directory_are_ignored(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "task.md").write_text(SAMPLE_MD)
    (inbox / "README.md").write_text("Not the target — should be ignored.")
    (inbox / "notes.txt").write_text("Also ignored.")
    captured: list[dict[str, Any]] = []
    watcher = _make_watcher(inbox, _make_capturing_dispatch(captured))
    await watcher.tick()
    assert len(captured) == 1
    assert captured[0]["name"] == "Fix the kitchen sink"
    # target was processed; the others are still there untouched
    assert (inbox / "README.md").exists()
    assert (inbox / "notes.txt").exists()


# --- watcher: run() loop ---


async def test_run_loop_processes_then_idles(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "task.md").write_text(SAMPLE_MD)
    captured: list[dict[str, Any]] = []
    watcher = _make_watcher(
        inbox, _make_capturing_dispatch(captured), poll=0.05,
    )
    task = asyncio.create_task(watcher.run())
    for _ in range(50):
        if len(captured) >= 1:
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(captured) == 1


async def test_run_loop_idle_when_directory_missing(tmp_path: Path) -> None:
    watcher = InboxWatcher(
        directory=tmp_path / "nope",
        dispatch=_noop_dispatch,
        poll_interval_seconds=0.05,
    )
    # run() returns immediately when the directory is absent — the
    # task simply completes; no cancellation needed.
    await asyncio.wait_for(watcher.run(), timeout=0.5)


# --- helpers ---


def test_safe_rename_appends_suffix(tmp_path: Path) -> None:
    p = tmp_path / "task.md"
    p.write_text("x")
    new = safe_rename(p, ".processed-abc")
    assert new.name == "task.md.processed-abc"
    assert not p.exists()
    assert new.exists()


def test_safe_rename_avoids_overwrite_by_counter(tmp_path: Path) -> None:
    p = tmp_path / "task.md"
    p.write_text("x")
    (tmp_path / "task.md.processed-abc").write_text("y")
    new = safe_rename(p, ".processed-abc")
    assert new.name == "task.md.processed-abc.1"


def test_sanitise_tag_strips_invalid_chars() -> None:
    assert sanitise_tag("task/abc:123") == "task-abc-123"
    assert sanitise_tag("///") == "task"
    assert sanitise_tag("") == "task"
