from __future__ import annotations

import asyncio
import time
from typing import Any

from openloom.runtime.session_status import (
    BUSY,
    IDLE,
    RETRY,
    is_visible_session,
    normalize_session_status,
    session_updated_at,
)
from openloom.runtime.prompts import messages_indicate_busy

_last_busy_at: dict[str, float] = {}
BUSY_HOLD_SECONDS = 12


class SessionMonitor:
    def __init__(self, client: Any) -> None:
        self.client = client
        self._status: dict[str, str] = {}
        self._sessions: list[dict[str, Any]] = []
        self._by_directory: dict[str, list[dict[str, Any]]] = {}

    @property
    def status(self) -> dict[str, str]:
        return dict(self._status)

    @property
    def sessions(self) -> list[dict[str, Any]]:
        return list(self._sessions)

    @property
    def by_directory(self) -> dict[str, list[dict[str, Any]]]:
        return {k: list(v) for k, v in self._by_directory.items()}

    async def refresh(self) -> None:
        try:
            sessions = await self.client.list_sessions()
            raw_status = await self.client.session_status()
        except Exception:
            return

        now = time.time()
        visible = [s for s in sessions if is_visible_session(s)]

        for session in visible:
            sid = session["id"]
            raw = raw_status.get(sid)
            status = normalize_session_status(raw) or IDLE

            updated = session_updated_at(session)
            if updated > 0 and self._status.get(sid) != status:
                self._status[sid] = status
                if status in (BUSY, RETRY):
                    _last_busy_at[sid] = now

            last = _last_busy_at.get(sid, 0)
            if last and now - last < BUSY_HOLD_SECONDS:
                self._status[sid] = BUSY

        self._sessions = sorted(visible, key=session_updated_at, reverse=True)

        by_dir: dict[str, list[dict[str, Any]]] = {}
        for s in self._sessions:
            d = s.get("directory") or "(unknown)"
            by_dir.setdefault(d, []).append(s)
        for d, items in by_dir.items():
            by_dir[d] = sorted(items, key=session_updated_at, reverse=True)
        ordered = sorted(
            by_dir.items(),
            key=lambda kv: max(session_updated_at(s) for s in kv[1]) if kv[1] else 0,
            reverse=True,
        )
        self._by_directory = dict(ordered)

    async def probe_busy(self, limit: int = 12) -> dict[str, bool]:
        now = time.time()
        recent = [
            s for s in self._sessions
            if is_visible_session(s) and session_updated_at(s) >= now - 600
        ]
        recent.sort(key=session_updated_at, reverse=True)
        recent = recent[:limit]

        async def probe(session: dict[str, Any]) -> tuple[str, bool]:
            sid = session["id"]
            try:
                msgs = await self.client.messages(sid, limit=4)
            except Exception:
                return sid, False
            return sid, messages_indicate_busy(msgs)

        results = await asyncio.gather(*(probe(s) for s in recent))
        result_map = dict(results)
        for sid, busy in result_map.items():
            self._status[sid] = BUSY if busy else (self._status.get(sid, IDLE))
            if busy:
                _last_busy_at[sid] = now
        return result_map
