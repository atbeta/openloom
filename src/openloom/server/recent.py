"""Recent workspaces registry — server-scoped user state, not core/.

Lives outside core/ because:
- not part of the task lifecycle (no store_version bump)
- not read by harness / events / sinks
- purely a Web UI convenience (Recent Workspaces sidebar)
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class RecentWorkspaces:
    MAX_KEPT = 20

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS recent_workspaces ("
                "path TEXT PRIMARY KEY, used_at REAL NOT NULL)"
            )
            conn.commit()

    @staticmethod
    def _normalize(path: str) -> str:
        return str(Path(path).expanduser().resolve())

    def record(self, path: str) -> str:
        normalized = self._normalize(path)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO recent_workspaces (path, used_at) VALUES (?, ?)",
                    (normalized, time.time()),
                )
                conn.execute(
                    "DELETE FROM recent_workspaces WHERE path IN ("
                    "  SELECT path FROM recent_workspaces ORDER BY used_at DESC LIMIT -1 OFFSET ?)",
                    (self.MAX_KEPT,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return normalized

    def list(self, limit: int = 12) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path FROM recent_workspaces ORDER BY used_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row["path"] for row in rows]

    def remove(self, path: str) -> bool:
        normalized = self._normalize(path)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM recent_workspaces WHERE path IN (?, ?)",
                (path, normalized),
            )
            return cursor.rowcount > 0

    def seed_from_sessions(self, directories: list[str], *, limit: int = 12) -> None:
        with self._lock, self._connect() as conn:
            for d in directories:
                if not d:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO recent_workspaces (path, used_at) VALUES (?, ?)",
                    (self._normalize(d), time.time()),
                )
