from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._version: int = 0
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  spec_json TEXT NOT NULL,
                  workspace TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending',
                  current_step INTEGER NOT NULL DEFAULT 0,
                  session_ids_json TEXT NOT NULL DEFAULT '[]',
                  active_session_id TEXT,
                  check_interval_seconds INTEGER NOT NULL DEFAULT 300,
                  completed_steps_json TEXT NOT NULL DEFAULT '[]',
                  idle_checks INTEGER NOT NULL DEFAULT 0,
                  progress REAL NOT NULL DEFAULT 0,
                  check_log_json TEXT NOT NULL DEFAULT '[]',
                  last_summary TEXT,
                  error TEXT,
                  last_check_at REAL,
                  next_check_at REAL,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL
                )
                """
            )
            conn.execute("PRAGMA user_version")
            conn.execute("PRAGMA user_version = 0")

    @property
    def store_version(self) -> int:
        return self._version

    def _increment_version(self) -> int:
        with self._lock:
            self._version += 1
            return self._version

    def create_task(self, task: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        version = self._increment_version()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                  id, name, spec_json, workspace, status, current_step,
                  session_ids_json, active_session_id, check_interval_seconds,
                  completed_steps_json, idle_checks, progress, check_log_json,
                  last_summary, error, last_check_at, next_check_at,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["id"],
                    task.get("name", ""),
                    json.dumps(task.get("spec", {}), ensure_ascii=False),
                    task.get("workspace", ""),
                    task.get("status", "pending"),
                    int(task.get("current_step") or 0),
                    json.dumps(task.get("session_ids") or [], ensure_ascii=False),
                    task.get("active_session_id"),
                    int(task.get("check_interval_seconds") or 300),
                    json.dumps(task.get("completed_steps") or [], ensure_ascii=False),
                    int(task.get("idle_checks") or 0),
                    float(task.get("progress") or 0),
                    json.dumps(task.get("check_log") or [], ensure_ascii=False),
                    task.get("last_summary"),
                    task.get("error"),
                    task.get("last_check_at"),
                    task.get("next_check_at", now),
                    now,
                    now,
                ),
            )
        return {"store_version": version}

    def update_task(self, task_id: str, **fields: Any) -> int:
        if not fields:
            return self._version
        json_fields = {
            "spec": "spec_json",
            "session_ids": "session_ids_json",
            "completed_steps": "completed_steps_json",
            "check_log": "check_log_json",
        }
        normalized: dict[str, Any] = {}
        for key, value in fields.items():
            if key in json_fields:
                normalized[json_fields[key]] = json.dumps(value, ensure_ascii=False)
            else:
                normalized[key] = value
        normalized["updated_at"] = time.time()
        assignments = ", ".join(f"{key} = ?" for key in normalized)
        values = list(normalized.values())
        values.append(task_id)
        version = self._increment_version()
        with self._connect() as conn:
            conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", values)
        return version

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_due_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status IN ('pending', 'running', 'waiting')
                  AND (next_check_at IS NULL OR next_check_at <= ?)
                ORDER BY COALESCE(next_check_at, 0) ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def append_check_log(self, task_id: str, *, status: str, summary: str, detail: str = "") -> int:
        task = self.get_task(task_id)
        if not task:
            return self._version
        log = list(task.get("check_log") or [])
        log.append({
            "at": time.time(),
            "status": status,
            "summary": summary,
            "detail": detail[:2000],
        })
        return self.update_task(task_id, check_log=log[-100:], last_summary=summary)

    def _row_to_task(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json") or "{}")
        item["session_ids"] = json.loads(item.pop("session_ids_json") or "[]")
        item["completed_steps"] = json.loads(item.pop("completed_steps_json") or "[]")
        item["check_log"] = json.loads(item.pop("check_log_json") or "[]")
        return item
