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

Silent-busy probe (0.13+): OpenCode 1.16.x only emits entries in
the ``/session/status`` map while the agent is actively responding;
once the agent is mid-tool or mid-edit the entry can disappear, so
the dashboard would otherwise show "0 busy" for sessions that are
genuinely still working. We compensate by probing recently-updated
sessions that are missing from the status map: call ``messages()``
and ask ``messages_indicate_busy`` whether the latest assistant
turn is still open. Probing is rate-limited per session
(``_PROBE_COOLDOWN_S``) and capped per refresh (``_PROBE_BATCH``)
to keep the upstream from being hammered when the dashboard has
hundreds of idle sessions.
"""
from __future__ import annotations

import time
from typing import Any

from openloom.runtime.prompts import messages_indicate_busy
from openloom.runtime.session_status import (
    BUSY,
    IDLE,
    is_visible_session,
    normalize_session_status,
    session_updated_at,
)

# Probe at most this many absent sessions per refresh tick. The
# monitor runs every 8s so even a batch of 3 gives every recent
# session a probe at least every ~24s.
_PROBE_BATCH = 3

# Minimum age (seconds) of a session's last update before it is
# considered worth probing. Sessions older than this are assumed
# genuinely idle — probing them just wastes an upstream call.
_PROBE_FRESH_S = 5 * 60

# Per-session cooldown between probes (seconds). Keeps the
# monitor from re-probing the same session on every refresh tick
# when the upstream status map stays empty.
_PROBE_COOLDOWN_S = 30.0


class SessionMonitor:
    def __init__(self, client: Any) -> None:
        self.client = client
        self._status: dict[str, str] = {}
        self._sessions: list[dict[str, Any]] = []
        self._by_directory: dict[str, list[dict[str, Any]]] = {}
        # Probed status cache — what we last *measured* for each
        # session via messages(). Cleared on every refresh so a
        # genuinely-idle session does not stay pinned to BUSY
        # forever.
        self._probed: dict[str, str] = {}
        # Per-session last-probe timestamp for rate limiting.
        self._last_probed_at: dict[str, float] = {}

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
        now = time.time()

        # Upstream-reported status, normalised, for every visible
        # session that OpenCode actually mentioned. Sessions absent
        # from the map (raw_status.get(sid) is None) are candidates
        # for the silent-busy probe below — without that probe,
        # OpenCode 1.16.x's tendency to drop a session from the
        # status map mid-tool would make the dashboard show 0 busy.
        new_status: dict[str, str] = {}
        absent: list[dict[str, Any]] = []
        for session in visible:
            sid = session["id"]
            raw = raw_status.get(sid)
            if raw is None:
                absent.append(session)
                new_status[sid] = self._probed.get(sid, IDLE)
            else:
                new_status[sid] = normalize_session_status(raw) or IDLE

        await self._probe_absent(absent, new_status, now)

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

    async def _probe_absent(
        self,
        absent: list[dict[str, Any]],
        new_status: dict[str, str],
        now: float,
    ) -> None:
        """For sessions absent from the upstream status map, decide
        whether they are silent-busy by inspecting their latest
        assistant message. Rate-limited and batched — see module
        docstring."""
        # Drop stale probe results for sessions that disappeared
        # from this refresh's visible list; SessionMonitor never
        # holds a status for a session OpenCode itself has purged.
        visible_ids = {s["id"] for s in absent}
        self._probed = {k: v for k, v in self._probed.items() if k in visible_ids}
        self._last_probed_at = {
            k: v for k, v in self._last_probed_at.items() if k in visible_ids
        }

        if not absent:
            return

        # Filter: must be recently updated AND not on cooldown.
        candidates: list[dict[str, Any]] = []
        for session in absent:
            sid = session["id"]
            if session_updated_at(session) <= 0:
                continue
            if now - session_updated_at(session) > _PROBE_FRESH_S:
                continue
            if now - self._last_probed_at.get(sid, 0.0) < _PROBE_COOLDOWN_S:
                continue
            candidates.append(session)

        # Probe the most recently-updated few.
        candidates.sort(key=session_updated_at, reverse=True)
        for session in candidates[:_PROBE_BATCH]:
            sid = session["id"]
            self._last_probed_at[sid] = now
            try:
                messages = await self.client.messages(sid, limit=20)
            except Exception:
                # Probe failure → keep the previous probe result if
                # any, otherwise IDLE. Never crash the refresh.
                continue
            new_status[sid] = BUSY if messages_indicate_busy(messages) else IDLE
            self._probed[sid] = new_status[sid] 
