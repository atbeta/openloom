"""Store contract tests — version semantics, persistence, and concurrency."""

from __future__ import annotations

import multiprocessing as mp
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from openloom.core.store import Store


def _make_store(tmp: Path) -> Store:
    return Store(tmp / "store.sqlite3")


def test_store_version_starts_at_zero(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.store_version == 0


def test_create_task_bumps_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    before = store.store_version
    result = store.create_task({
        "id": "t1",
        "name": "demo",
        "spec": {"name": "demo"},
        "workspace": str(tmp_path),
    })
    assert result["store_version"] == before + 1
    assert store.store_version == before + 1


def test_version_monotonic_across_writes(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    versions = []
    for i in range(5):
        result = store.create_task({
            "id": f"t{i}",
            "name": f"task-{i}",
            "spec": {"name": f"task-{i}"},
            "workspace": str(tmp_path),
        })
        versions.append(result["store_version"])
    assert versions == sorted(versions)
    assert len(set(versions)) == 5


def test_update_task_bumps_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_task({
        "id": "t1",
        "name": "demo",
        "spec": {"name": "demo"},
        "workspace": str(tmp_path),
    })
    before = store.store_version
    sv = store.update_task("t1", status="running")
    assert sv == before + 1
    assert store.store_version == before + 1


def test_append_check_log_bumps_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_task({
        "id": "t1",
        "name": "demo",
        "spec": {"name": "demo"},
        "workspace": str(tmp_path),
    })
    before = store.store_version
    sv = store.append_check_log("t1", status="running", summary="started")
    assert sv == before + 1
    task = store.get_task("t1")
    assert task is not None
    assert task["last_summary"] == "started"


def test_store_accepts_str_path(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "store.sqlite3"))
    assert store.path == tmp_path / "store.sqlite3"
    assert store.store_version == 0


def test_empty_update_returns_current_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.update_task("nonexistent") == 0


def test_delete_task_bumps_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_task({
        "id": "t1",
        "name": "demo",
        "spec": {"name": "demo"},
        "workspace": str(tmp_path),
    })
    before = store.store_version
    sv = store.delete_task("t1")
    assert sv == before + 1
    assert store.get_task("t1") is None


def _writer(db_path: str, task_id: str) -> int:
    store = Store(Path(db_path))
    result = store.create_task({
        "id": task_id,
        "name": task_id,
        "spec": {"name": task_id},
        "workspace": "/tmp",
    })
    return result["store_version"]


def test_store_version_monotonic_across_processes(tmp_path: Path) -> None:
    """Two processes writing to the same DB must see strictly increasing versions."""
    db = tmp_path / "shared.sqlite3"
    Store(db).create_task({
        "id": "seed",
        "name": "seed",
        "spec": {"name": "seed"},
        "workspace": "/tmp",
    })

    ctx = mp.get_context("spawn")
    procs = [
        ctx.Process(target=_writer, args=(str(db), f"proc{i}"))
        for i in range(3)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    final = Store(db).store_version
    assert final >= 4  # seed + 3 writers


def test_meta_table_initialized_once(tmp_path: Path) -> None:
    """Reopening the DB must not reset store_version to zero."""
    db = tmp_path / "store.sqlite3"
    store = Store(db)
    store.create_task({
        "id": "t1",
        "name": "demo",
        "spec": {"name": "demo"},
        "workspace": str(tmp_path),
    })
    first_version = store.store_version
    assert first_version >= 1

    store2 = Store(db)
    assert store2.store_version == first_version
