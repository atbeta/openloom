from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def new_task_record(
    *,
    task_id: str,
    name: str,
    spec: dict[str, Any],
    workspace: str,
    check_interval_seconds: int,
    active_session_id: str | None = None,
    session_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Canonical initial task dict — single source for create_task field defaults."""
    now = time.time()
    if session_ids is not None:
        ids = list(session_ids)
    elif active_session_id:
        ids = [active_session_id]
    else:
        ids = []
    return {
        "id": task_id,
        "name": name,
        "spec": spec,
        "workspace": workspace,
        "status": "pending",
        "current_step": 0,
        "completed_steps": [],
        "idle_checks": 0,
        "progress": 0.0,
        "check_interval_seconds": check_interval_seconds,
        "last_check_at": None,
        "next_check_at": now,
        "active_session_id": active_session_id,
        "session_ids": ids,
        "last_summary": None,
        "error": None,
        "check_log": [],
    }


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS tasks ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, spec_json TEXT NOT NULL,"
                "workspace TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',"
                "current_step INTEGER NOT NULL DEFAULT 0,"
                "session_ids_json TEXT NOT NULL DEFAULT '[]',"
                "active_session_id TEXT, check_interval_seconds INTEGER NOT NULL DEFAULT 300,"
                "completed_steps_json TEXT NOT NULL DEFAULT '[]',"
                "idle_checks INTEGER NOT NULL DEFAULT 0, progress REAL NOT NULL DEFAULT 0,"
                "check_log_json TEXT NOT NULL DEFAULT '[]',"
                "last_summary TEXT, error TEXT, last_check_at REAL, next_check_at REAL,"
                "created_at REAL NOT NULL, updated_at REAL NOT NULL);"
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('store_version', '0');"
            )
            conn.commit()

    @property
    def store_version(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'store_version'").fetchone()
        return int(row["value"]) if row else 0

    def _write(self, mutator: Callable[[sqlite3.Connection], None]) -> int:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                mutator(conn)
                conn.execute(
                    "UPDATE meta SET value = CAST(value AS INTEGER) + 1 "
                    "WHERE key = 'store_version'"
                )
                row = conn.execute("SELECT value FROM meta WHERE key = 'store_version'").fetchone()
                version = int(row["value"]) if row else 0
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return version

    def create_task(self, task: dict[str, Any]) -> dict[str, Any]:
        now = time.time()

        def mutator(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO tasks (id, name, spec_json, workspace, status, current_step,"
                "session_ids_json, active_session_id, check_interval_seconds,"
                "completed_steps_json, idle_checks, progress, check_log_json,"
                "last_summary, error, last_check_at, next_check_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task["id"], task.get("name", ""), json.dumps(task.get("spec", {}), ensure_ascii=False),
                 task.get("workspace", ""), task.get("status", "pending"),
                 int(task.get("current_step") or 0), json.dumps(task.get("session_ids") or [], ensure_ascii=False),
                 task.get("active_session_id"),
                 int(task["check_interval_seconds"]) if "check_interval_seconds" in task else 300,
                 json.dumps(task.get("completed_steps") or [], ensure_ascii=False),
                 int(task.get("idle_checks") or 0), float(task.get("progress") or 0),
                 json.dumps(task.get("check_log") or [], ensure_ascii=False),
                 task.get("last_summary"), task.get("error"), task.get("last_check_at"),
                 task.get("next_check_at", now), now, now),
            )

        return {"store_version": self._write(mutator)}

    def update_task(self, task_id: str, **fields: Any) -> int:
        if not fields:
            return self.store_version
        json_map = {"spec": "spec_json", "session_ids": "session_ids_json",
                     "completed_steps": "completed_steps_json", "check_log": "check_log_json"}
        normalized = {
            (json_map[k] if k in json_map else k): (json.dumps(v, ensure_ascii=False) if k in json_map else v)
            for k, v in fields.items()
        }
        normalized["updated_at"] = time.time()
        assignments = ", ".join(f"{k} = ?" for k in normalized)
        values = list(normalized.values()) + [task_id]

        def mutator(conn: sqlite3.Connection) -> None:
            conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", values)

        return self._write(mutator)

    def delete_task(self, task_id: str) -> int:
        def mutator(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

        return self._write(mutator)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_due_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status IN ('pending', 'running', 'waiting')"
                " AND (next_check_at IS NULL OR next_check_at <= ?)"
                " ORDER BY COALESCE(next_check_at, 0) ASC LIMIT ?",
                (now, limit),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def append_check_log(self, task_id: str, *, status: str, summary: str, detail: str = "") -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT check_log_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return self.store_version
            log = list(json.loads(row["check_log_json"] or "[]"))
            log.append({"at": time.time(), "status": status, "summary": summary, "detail": detail[:2000]})
            log_json = json.dumps(log[-100:], ensure_ascii=False)

        def mutator(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE tasks SET check_log_json = ?, last_summary = ?, updated_at = ? WHERE id = ?",
                (log_json, summary, time.time(), task_id),
            )

        return self._write(mutator)

    def _row_to_task(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json") or "{}")
        item["session_ids"] = json.loads(item.pop("session_ids_json") or "[]")
        item["completed_steps"] = json.loads(item.pop("completed_steps_json") or "[]")
        item["check_log"] = json.loads(item.pop("check_log_json") or "[]")
        return item
