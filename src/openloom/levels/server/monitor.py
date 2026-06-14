from __future__ import annotations

import asyncio
import time
from typing import Any

from openloom.runtime.prompts import messages_indicate_busy
from openloom.runtime.session_status import (
    BUSY,
    IDLE,
    RETRY,
    is_visible_session,
    normalize_session_status,
    session_updated_at,
)

BUSY_HOLD_SECONDS = 12


class SessionMonitor:
    def __init__(self, client: Any) -> None:
        self.client = client
        self._status: dict[str, str] = {}
        self._sessions: list[dict[str, Any]] = []
        self._by_directory: dict[str, list[dict[str, Any]]] = {}
        self._last_busy_at: dict[str, float] = {}

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

        # OpenCode 1.16.2 only emits entries in /session/status while a
        # session is actively running. Once an agent goes idle, the
        # status map entry disappears — we must probe messages for
        # recent sessions to tell "agent is genuinely idle" from
        # "server never reported". Probe only recent sessions to keep
        # the dashboard poll cheap.
        recent_window = 60.0
        to_probe: list[dict[str, Any]] = [
            s for s in visible
            if s["id"] not in raw_status
            and session_updated_at(s) >= now - recent_window
        ]
        if to_probe:
            await self._probe_busy_inplace(to_probe, now)

        for session in visible:
            sid = session["id"]
            raw = raw_status.get(sid)
            status = normalize_session_status(raw) or IDLE
            if self._status.get(sid) != status:
                self._status[sid] = status
                if status in (BUSY, RETRY):
                    self._last_busy_at[sid] = now

            last = self._last_busy_at.get(sid, 0)
            if last and now - last < BUSY_HOLD_SECONDS:
                self._status[sid] = BUSY

        # Purge stale status entries for sessions no longer in the list.
        # Guards against OpenCode server bug where status map retains
        # entries for deleted sessions (ghost "busy" after SQLite removal).
        visible_ids = {s["id"] for s in visible}
        for stale in list(self._status):
            if stale not in visible_ids:
                del self._status[stale]
        for stale in list(self._last_busy_at):
            if stale not in visible_ids:
                del self._last_busy_at[stale]

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

    async def _probe_busy_inplace(
        self, sessions: list[dict[str, Any]], now: float,
    ) -> None:
        """For each session whose status is unknown, walk the latest
        message to detect in-flight agent work. The upstream
        /session/status endpoint only emits an entry while the agent
        is actively responding; once it goes idle the entry vanishes
        and we would otherwise show "idle" forever. This probe fills
        that gap using the same messages_indicate_busy() helper the
        router uses, and stamps _last_busy_at so the 12s hold keeps
        the row sticky.
        """
        async def probe(session: dict[str, Any]) -> tuple[str, bool]:
            sid = session["id"]
            try:
                msgs = await self.client.messages(sid, limit=4)
            except Exception:
                return sid, False
            return sid, messages_indicate_busy(msgs)

        results = await asyncio.gather(*(probe(s) for s in sessions))
        for sid, busy in results:
            if busy:
                self._status[sid] = BUSY
                self._last_busy_at[sid] = now
            # If not busy we leave the row as IDLE; refresh() will
            # pick that up next pass without polluting the cache.
