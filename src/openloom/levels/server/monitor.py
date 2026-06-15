from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from openloom.core.events import Event, EventType
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

DEFAULT_STALE_BUSY_CHECKS = 10


class SessionMonitor:
    def __init__(
        self,
        client: Any,
        *,
        stale_busy_threshold: int = DEFAULT_STALE_BUSY_CHECKS,
    ) -> None:
        self.client = client
        self._status: dict[str, str] = {}
        self._sessions: list[dict[str, Any]] = []
        self._by_directory: dict[str, list[dict[str, Any]]] = {}
        self._last_busy_at: dict[str, float] = {}
        # Stale-busy detection. A session is "stuck" when it has been
        # observed busy for `stale_busy_threshold` consecutive refresh
        # passes with no fresh message / status change. We remember
        # (a) the timestamp of the latest completed message we have
        # seen for that session, and (b) whether we have already
        # fired the SESSION_STALE_BUSY event for the current stuck
        # episode — it should only fire once until the session
        # recovers, otherwise the user's webhook would flood.
        self._stale_busy_threshold = max(1, int(stale_busy_threshold))
        self._latest_progress_at: dict[str, float] = {}
        self._stale_count: dict[str, int] = {}
        self._stale_fired: set[str] = set()
        self._event_sink: Callable[[Event], None] | None = None
        # Cache of the most recently fetched message list per
        # session. We populate this from the busy-probe path so
        # the SESSION_STALE_BUSY emit can attach a recent-activity
        # excerpt without a second HTTP round-trip.
        self._last_messages: dict[str, list[dict[str, Any]]] = {}
        self._prompts_module: Any | None = None
        self._recent_activity_n: int = 3

    def on_event(self, handler: Callable[[Event], None]) -> None:
        """Register a handler for monitor-emitted events (e.g. SESSION_STALE_BUSY).

        The factory wires the bus's ``emit`` here so the existing
        notify sinks (webhook / file) receive these without any
        further plumbing.
        """
        self._event_sink = handler

    def attach_prompts(self, prompts: Any, *, recent_n: int = 3) -> None:
        """Wire the monitor to the same ``PromptsPort`` the harness
        uses so the SESSION_STALE_BUSY event can include a
        recent-activity excerpt (last assistant messages + tool
        summary). The monitor does not need this for its core
        job — it is a payload-enrichment concern only.
        """
        self._prompts_module = prompts
        self._recent_activity_n = max(1, int(recent_n))

    @property
    def status(self) -> dict[str, str]:
        return dict(self._status)

    @property
    def sessions(self) -> list[dict[str, Any]]:
        return list(self._sessions)

    @property
    def by_directory(self) -> dict[str, list[dict[str, Any]]]:
        return {k: list(v) for k, v in self._by_directory.items()}

    @property
    def stale_busy_sessions(self) -> list[str]:
        """IDs of sessions currently counted as stuck, for the dashboard badge."""
        return [sid for sid, n in self._stale_count.items() if n >= self._stale_busy_threshold]

    def forget_session(self, session_id: str) -> None:
        """Drop all per-session state when the owning task is
        archived, auto-paused, or otherwise taken off the
        session. Without this, a session whose only OpenLoom
        task was archived would still appear in
        :attr:`stale_busy_sessions` and the dashboard's "N
        stuck" pill would never go away until the upstream
        OpenCode server stopped listing the session entirely
        (which never happens for a session that is genuinely
        busy — i.e. a hung agent).

        The upstream cleanup in :meth:`refresh` only fires when
        the session is missing from OpenCode's
        ``list_sessions``; for sessions that are still alive
        there, the only signal that they should be released is
        this explicit drop.
        """
        if not session_id:
            return
        self._status.pop(session_id, None)
        self._last_busy_at.pop(session_id, None)
        self._latest_progress_at.pop(session_id, None)
        self._stale_count.pop(session_id, None)
        self._stale_fired.discard(session_id)
        self._last_messages.pop(session_id, None)

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
        # Probe two cohorts:
        #   * sessions absent from /session/status but recently
        #     updated (the OpenCode 1.16.2 "silent busy" pattern)
        #   * sessions present in /session/status as busy — we still
        #     want their latest message ``completed`` timestamp so
        #     the stale-busy counter can distinguish a long-running
        #     tool from a stuck one. Probing a busy session is cheap
        #     (single GET, 4 messages).
        to_probe: list[dict[str, Any]] = [
            s for s in visible
            if (s["id"] not in raw_status and session_updated_at(s) >= now - recent_window)
            or raw_status.get(s["id"], {}).get("type") in (BUSY, RETRY)
        ]
        if to_probe:
            progress, probed_busy = await self._probe_busy_inplace(to_probe, now)
        else:
            progress, probed_busy = {}, set()

        for session in visible:
            sid = session["id"]
            raw = raw_status.get(sid)
            normalized = normalize_session_status(raw) or IDLE
            # The probe can independently detect in-flight work even
            # when the upstream status map is silent (OpenCode 1.16.2
            # stops emitting the entry once the agent pauses). The
            # stale-busy tracker needs to see the effective status
            # (raw OR probe), not just the raw value.
            effective_busy = normalized in (BUSY, RETRY) or sid in probed_busy
            effective_status = BUSY if effective_busy else normalized

            prev_status = self._status.get(sid)
            if prev_status != effective_status:
                self._status[sid] = effective_status
                if effective_status in (BUSY, RETRY):
                    self._last_busy_at[sid] = now

            last = self._last_busy_at.get(sid, 0)
            if last and now - last < BUSY_HOLD_SECONDS:
                self._status[sid] = BUSY

            self._update_stale_state(
                sid, session, effective_status, progress.get(sid, 0.0), now,
            )

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
        for stale in list(self._stale_count):
            if stale not in visible_ids:
                del self._stale_count[stale]
        for stale in list(self._latest_progress_at):
            if stale not in visible_ids:
                del self._latest_progress_at[stale]
        self._stale_fired.intersection_update(visible_ids)

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

    def _update_stale_state(
        self,
        sid: str,
        session: dict[str, Any],
        status: str,
        latest_completed_ts: float,
        now: float,
    ) -> None:
        """Track consecutive busy-without-progress observations and
        fire a SESSION_STALE_BUSY event the moment the threshold is
        crossed. The event is one-shot per stuck episode — it re-arms
        only after the session recovers (status != busy for one
        refresh).

        "Consecutive busy observations" counts the first sighting of
        a session as 1 — the user-facing promise is "N consecutive
        refreshes of busy ⇒ fire on the Nth one", which matches the
        intuitive reading of "10 checks in a row with no progress".
        """
        # First observation for this session: seed the progress
        # baseline. The "latest completed" timestamp is the strongest
        # signal of real progress — if we have one, anchor to it,
        # otherwise fall back to the session's own updated time so
        # we do not count pre-existing quietness against it. The
        # counter starts at 1 because this is the first observation
        # of the episode (matches the user-facing promise "N
        # consecutive busy checks ⇒ fire on the Nth").
        baseline = self._latest_progress_at.get(sid)
        if baseline is None:
            if latest_completed_ts > 0:
                anchor = latest_completed_ts
            else:
                anchor = session_updated_at(session)
            self._latest_progress_at[sid] = anchor
            self._stale_count[sid] = 1 if status in (BUSY, RETRY) else 0
            self._stale_fired.discard(sid)
            if self._stale_count[sid] >= self._stale_busy_threshold:
                self._stale_fired.add(sid)
                self._fire_stale(sid, session, now)
            return

        # Progress observed → reset the counter. "Progress" means
        # either a new completed message (with a strictly greater
        # timestamp than what we last saw) OR a non-busy status.
        if (latest_completed_ts > 0 and latest_completed_ts > baseline) or status not in (BUSY, RETRY):
            self._latest_progress_at[sid] = max(
                baseline, latest_completed_ts if latest_completed_ts > 0 else baseline,
            )
            self._stale_count[sid] = 0
            self._stale_fired.discard(sid)
            return

        # No progress and still busy → increment.
        self._stale_count[sid] = self._stale_count.get(sid, 0) + 1
        if (
            self._stale_count[sid] >= self._stale_busy_threshold
            and sid not in self._stale_fired
        ):
            self._stale_fired.add(sid)
            self._fire_stale(sid, session, now)

    def _fire_stale(
        self, sid: str, session: dict[str, Any], now: float,
    ) -> None:
        if self._event_sink is None:
            return
        checks = self._stale_count[sid]
        baseline = self._latest_progress_at.get(sid, now)
        elapsed = max(0.0, now - baseline) if baseline else 0.0
        title = str(session.get("title") or "").strip()
        directory = str(session.get("directory") or "").strip()
        data: dict[str, Any] = {
            "session_id": sid,
            "title": title or None,
            "directory": directory or None,
            "consecutive_busy_checks": checks,
            "threshold_checks": self._stale_busy_threshold,
            "stuck_for_seconds": int(elapsed),
        }
        # Best-effort recent-activity excerpt. The monitor does
        # not depend on the prompts module for its core work, so
        # we silently skip the field when it has not been wired
        # (e.g. older tests or alternative harnesses).
        if self._prompts_module is not None:
            try:
                data["recent_activity"] = (
                    self._prompts_module.recent_assistant_activity(
                        self._last_messages.get(sid) or [],
                        n=self._recent_activity_n,
                    )
                )
            except Exception:
                # Payload enrichment must never break the alert.
                pass
        event = Event(
            type=EventType.SESSION_STALE_BUSY,
            task_id="",
            timestamp=now,
            data=data,
        )
        try:
            self._event_sink(event)
        except Exception:
            pass

    async def _probe_busy_inplace(
        self, sessions: list[dict[str, Any]], now: float,
    ) -> tuple[dict[str, float], set[str]]:
        """For each session whose status is unknown, walk the latest
        message to detect in-flight agent work. The upstream
        /session/status endpoint only emits an entry while the agent
        is actively responding; once it goes idle the entry vanishes
        and we would otherwise show "idle" forever. This probe fills
        that gap using the same messages_indicate_busy() helper the
        router uses, stamps _last_busy_at so the 12s hold keeps the
        row sticky, and returns ``({sid: latest_completed_ts},
        {sid, ...})`` so the stale-busy tracker can detect real
        progress even when /session/status itself is silent.
        """
        async def probe(session: dict[str, Any]) -> tuple[str, bool, float, list[dict[str, Any]]]:
            sid = session["id"]
            try:
                msgs = await self.client.messages(sid, limit=20)
            except Exception:
                return sid, False, 0.0, []
            busy = messages_indicate_busy(msgs)
            latest = 0.0
            for msg in msgs:
                info = msg.get("info") if isinstance(msg.get("info"), dict) else msg
                if not isinstance(info, dict):
                    continue
                t = info.get("time") or {}
                completed = t.get("completed")
                if isinstance(completed, (int, float)) and completed > latest:
                    latest = float(completed)
            return sid, busy, latest, msgs

        results = await asyncio.gather(*(probe(s) for s in sessions))
        progress: dict[str, float] = {}
        probed_busy: set[str] = set()
        for sid, busy, latest, msgs in results:
            progress[sid] = latest
            if msgs:
                # Replace (do not append) so the cache reflects
                # "the messages we just observed", not a stale
                # snapshot from a previous refresh.
                self._last_messages[sid] = msgs
            if busy:
                probed_busy.add(sid)
                self._status[sid] = BUSY
                self._last_busy_at[sid] = now
            # If not busy we leave the row as IDLE; refresh() will
            # pick that up next pass without polluting the cache.
        return progress, probed_busy
