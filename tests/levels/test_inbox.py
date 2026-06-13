"""Tests for the inbox level — source parser, watcher polling, rename semantics."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from openloom.levels.inbox import (
    InboxSource,
    safe_rename,
    sanitise_tag,
)
from openloom.levels.inbox.watcher import InboxWatcher

# --- helpers ---


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


SAMPLE_MD = """# Fix the kitchen sink

workspace: /home/me/projects/house

## goal
Replace the gasket and verify no leak.

## steps
- Buy a gasket
- Turn off water
- Replace gasket

## acceptance
- [x] No leak after 10 minutes
"""


MINIMAL_MD = """# Just a title

Do the thing.
"""


NO_WORKSPACE_MD = """# No workspace here

This file has no `workspace:` line.
"""


async def _noop_dispatch(_payload: dict[str, Any]) -> str | None:
    return "task_abc123def456"


async def _capture_dispatch(captured: list[dict[str, Any]]) -> Any:
    async def _d(payload: dict[str, Any]) -> str:
        captured.append(payload)
        return f"task_{len(captured):03d}"

    return _d


# --- InboxSource ---


def test_source_loads_markdown_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "a.md", SAMPLE_MD)
    _write(inbox / "b.md", MINIMAL_MD)

    src = InboxSource(inbox, default_workspace="/srv/default")
    specs = src.load()
    assert len(specs) == 2
    names = sorted(s["name"] for s in specs)
    assert names == ["Fix the kitchen sink", "Just a title"]
    # file with explicit workspace wins; file without falls back to default
    by_path = {Path(s["_inbox_path"]).name: s for s in specs}
    assert by_path["a.md"]["workspace"] == "/home/me/projects/house"
    assert by_path["b.md"]["workspace"] == "/srv/default"


def test_source_uses_default_workspace_when_missing(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "x.md", MINIMAL_MD)

    src = InboxSource(inbox, default_workspace="/srv/default")
    specs = src.load()
    assert len(specs) == 1
    assert specs[0]["workspace"] == "/srv/default"


def test_source_skips_files_with_no_workspace_and_no_default(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "x.md", MINIMAL_MD)

    src = InboxSource(inbox, default_workspace="")
    assert src.load() == []


def test_source_skips_unparseable_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "bad.md", "this is plain text, no title, will fail name parse")

    src = InboxSource(inbox, default_workspace="")
    assert src.load() == []


def test_source_returns_empty_for_missing_directory(tmp_path: Path) -> None:
    src = InboxSource(tmp_path / "nope", default_workspace="")
    assert src.load() == []


def test_source_ignores_non_md_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "note.txt", "not a task")
    _write(inbox / "a.md", SAMPLE_MD)
    assert len(InboxSource(inbox).load()) == 1


def test_parse_path_returns_none_for_missing_workspace(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "x.md"
    _write(target, MINIMAL_MD)
    src = InboxSource(inbox, default_workspace="")
    assert src.parse_path(target) is None


# --- safe_rename / sanitise_tag ---


def test_safe_rename_appends_suffix(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("x")
    new = safe_rename(p, ".processed-abc")
    assert new.name == "a.md.processed-abc"
    assert not p.exists()
    assert new.exists()


def test_safe_rename_avoids_overwrite_by_counter(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("x")
    (tmp_path / "a.md.processed-abc").write_text("y")
    new = safe_rename(p, ".processed-abc")
    assert new.name == "a.md.processed-abc.1"


def test_sanitise_tag_strips_invalid_chars() -> None:
    assert sanitise_tag("task/abc:123") == "task-abc-123"
    assert sanitise_tag("///") == "task"
    assert sanitise_tag("") == "task"


# --- InboxWatcher ---


async def test_watcher_dispatches_new_md_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "a.md", SAMPLE_MD)
    _write(inbox / "b.md", MINIMAL_MD)

    captured: list[dict[str, Any]] = []
    dispatch = await _capture_dispatch(captured)
    source = InboxSource(inbox, default_workspace="/srv")
    watcher = InboxWatcher(source, dispatch, poll_interval_seconds=1.0)

    dispatched = await watcher.tick()
    assert len(dispatched) == 2
    assert {p.name for p in dispatched} == {"a.md", "b.md"}
    assert {p.name for p in inbox.iterdir() if p.suffix == ".md"} == set()

    # files renamed with .processed- prefix
    renamed = sorted(p.name for p in inbox.iterdir())
    assert all(n.endswith(".md.processed-task_001") or n.endswith(".md.processed-task_002")
               for n in renamed), renamed


async def test_watcher_does_not_re_dispatch_seen_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "a.md", SAMPLE_MD)

    source = InboxSource(inbox, default_workspace="/srv")
    watcher = InboxWatcher(source, _noop_dispatch, poll_interval_seconds=1.0)

    first = await watcher.tick()
    assert len(first) == 1

    second = await watcher.tick()
    assert second == []


async def test_watcher_dispatches_only_new_file(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "a.md", SAMPLE_MD)
    source = InboxSource(inbox, default_workspace="/srv")
    watcher = InboxWatcher(source, _noop_dispatch, poll_interval_seconds=1.0)
    await watcher.tick()

    # new file lands after first tick
    _write(inbox / "b.md", MINIMAL_MD)
    dispatched = await watcher.tick()
    assert [p.name for p in dispatched] == ["b.md"]


async def test_watcher_process_existing_seeds_seen(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "a.md", SAMPLE_MD)
    source = InboxSource(inbox, default_workspace="/srv")
    watcher = InboxWatcher(
        source, _noop_dispatch, process_existing=True, poll_interval_seconds=1.0,
    )
    assert await watcher.tick() == []


async def test_watcher_marks_unparseable_with_error_suffix(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "bad.md", "no title or workspace, will be skipped by parser")
    source = InboxSource(inbox, default_workspace="")
    watcher = InboxWatcher(source, _noop_dispatch, poll_interval_seconds=1.0)
    await watcher.tick()

    names = sorted(p.name for p in inbox.iterdir())
    assert len(names) == 1
    assert names[0].startswith("bad.md.error-")


async def test_watcher_dispatch_exception_renames_to_error(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "boom.md", SAMPLE_MD)

    async def failing(_p: dict[str, Any]) -> str:
        raise RuntimeError("dispatcher down")

    source = InboxSource(inbox, default_workspace="/srv")
    watcher = InboxWatcher(source, failing, poll_interval_seconds=1.0)
    await watcher.tick()

    names = sorted(p.name for p in inbox.iterdir())
    assert len(names) == 1
    assert names[0].startswith("boom.md.error-dispatch-raised")


async def test_watcher_dispatch_returning_none_does_not_rename(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "skip.md", SAMPLE_MD)

    async def declining(_p: dict[str, Any]) -> str | None:
        return None

    source = InboxSource(inbox, default_workspace="/srv")
    watcher = InboxWatcher(source, declining, poll_interval_seconds=1.0)
    dispatched = await watcher.tick()
    assert dispatched == []
    # file untouched, but watcher marks it seen to avoid hammering
    assert (inbox / "skip.md").exists()


async def test_watcher_handles_missing_directory(tmp_path: Path) -> None:
    source = InboxSource(tmp_path / "nope", default_workspace="")
    watcher = InboxWatcher(source, _noop_dispatch, poll_interval_seconds=1.0)
    assert await watcher.tick() == []


async def test_watcher_run_sleeps_and_loops(tmp_path: Path) -> None:
    """Run the loop briefly and ensure tick is called repeatedly."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = InboxSource(inbox, default_workspace="/srv")

    ticks = 0

    async def counting_dispatch(_p: dict[str, Any]) -> str | None:
        nonlocal ticks
        ticks += 1
        return f"task_{ticks:03d}"

    watcher = InboxWatcher(source, counting_dispatch, poll_interval_seconds=0.05)
    _write(inbox / "a.md", SAMPLE_MD)

    task = asyncio.create_task(watcher.run())
    # wait until at least one dispatch happened
    for _ in range(50):
        if ticks >= 1:
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ticks >= 1


async def test_watcher_payload_carries_inbox_path(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write(inbox / "a.md", SAMPLE_MD)
    captured: list[dict[str, Any]] = []
    dispatch = await _capture_dispatch(captured)
    source = InboxSource(inbox, default_workspace="/srv")
    watcher = InboxWatcher(source, dispatch, poll_interval_seconds=1.0)
    await watcher.tick()
    assert len(captured) == 1
    assert captured[0]["_inbox_path"].endswith("a.md")
    assert captured[0]["name"] == "Fix the kitchen sink"
