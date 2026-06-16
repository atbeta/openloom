"""
SessionMonitor — polls OpenCode for the session list and status
map. The dashboard reads ``monitor.sessions`` / ``monitor.status``
to render the Sessions panel; nothing else.

The 0.11 monitor also ran a stale-busy detector (N consecutive
busy refreshes ⇒ SESSION_STALE_BUSY event on the bus). That
detector only existed to back the manual-mode nudge lifecycle,
which 0.12 removes; without a long-lived observer per session
there is nothing to call "stuck". The detector is gone; this
file is now a thin list + status cache.
"""
from __future__ import annotations

from typing import Any

from openloom.runtime.session_status import (
    IDLE,
    is_visible_session,
    normalize_session_status,
    session_updated_at,
)


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

        visible = [s for s in sessions if is_visible_session(s)]

        # The status map only contains entries for sessions the
        # upstream OpenCode server considers "active". A session
        # that is genuinely idle does not appear in the map, so
        # we treat absent + visible as IDLE. The OpenCode 1.16.2
        # "silent busy" pattern (where a long-running tool
        # vanished from the map) is a known upstream issue; the
        # 0.12 harness does not pretend to fix it, because the
        # fix is to upgrade OpenCode rather than work around it
        # in the harness.
        new_status: dict[str, str] = {}
        for session in visible:
            sid = session["id"]
            raw = raw_status.get(sid)
            new_status[sid] = normalize_session_status(raw) or IDLE
        self._status = new_status

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
