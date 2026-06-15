"""SessionMonitor contract tests — covers the ghost-busy purge.

The OpenCode server can leave stale ``{type: busy}`` entries in its
``/session/status`` map after the corresponding session is removed from
SQLite. If the dashboard trusts the upstream map blindly, those
deleted sessions stay displayed as busy forever (until the OpenCode
server is restarted).

``SessionMonitor.refresh()`` is the defensive layer: it must drop
status / busy-hold entries for any session that no longer appears in
``list_sessions()``.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from openloom.levels.server.monitor import SessionMonitor
from openloom.runtime.session_status import BUSY, IDLE


class _FakeClient:
    """Minimal OpenCodeClient stub — list_sessions + session_status."""

    def __init__(self, sessions: list[dict[str, Any]], status: dict[str, Any]) -> None:
        self._sessions = sessions
        self._status = status
        self.list_calls = 0

    async def list_sessions(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        return list(self._sessions)

    async def session_status(self) -> dict[str, Any]:
        return dict(self._status)


def _session(sid: str, updated: float = 1.0) -> dict[str, Any]:
    return {
        "id": sid,
        "title": sid,
        "directory": "/tmp",
        "time": {"created": updated, "updated": updated, "archived": 0},
        "parentID": None,
    }


@pytest.mark.asyncio
async def test_refresh_drops_status_for_deleted_session() -> None:
    """If the upstream status map still reports busy for a session that
    OpenCode has removed from its list, we must NOT leak that to
    .status. This is the ghost-busy regression."""
    visible = [_session("ses_alive", updated=1.0)]
    upstream_status = {
        "ses_alive": {"type": "idle"},
        "ses_ghost": {"type": "busy"},
    }
    client = _FakeClient(visible, upstream_status)
    monitor = SessionMonitor(client)
    await monitor.refresh()

    assert "ses_alive" in monitor.status
    assert "ses_ghost" not in monitor.status, (
        "ghost session leaked from upstream status map into monitor.status"
    )


@pytest.mark.asyncio
async def test_refresh_drops_busy_hold_for_deleted_session() -> None:
    """``_last_busy_at`` is an instance dict. Even if it was set
    by an earlier tick, when the session is no longer visible the
    entry must be evicted on the next refresh."""
    visible = [_session("ses_alive", updated=1.0)]
    client = _FakeClient(visible, {})
    monitor = SessionMonitor(client)
    monitor._last_busy_at["ses_ghost"] = 1.0  # simulate prior tick
    await monitor.refresh()

    assert "ses_ghost" not in monitor._last_busy_at


@pytest.mark.asyncio
async def test_status_preserved_for_still_visible_sessions() -> None:
    """The purge is selective — busy sessions that ARE in the list
    must keep their status."""
    visible = [
        _session("ses_busy", updated=1.0),
        _session("ses_idle", updated=0.5),
    ]
    upstream_status = {
        "ses_busy": {"type": "busy"},
        "ses_idle": {"type": "idle"},
    }
    client = _FakeClient(visible, upstream_status)
    monitor = SessionMonitor(client)
    await monitor.refresh()

    assert monitor.status["ses_busy"] == BUSY
    assert monitor.status["ses_idle"] == IDLE


@pytest.mark.asyncio
async def test_ghost_busy_does_not_pollute_visible_count() -> None:
    """``len(monitor.sessions)`` is what the UI shows as 'visible'.
    Ghost sessions must not show up there either."""
    visible = [_session("ses_alive", updated=1.0)]
    client = _FakeClient(visible, {"ses_ghost": {"type": "busy"}})
    monitor = SessionMonitor(client)
    await monitor.refresh()

    ids = {s["id"] for s in monitor.sessions}
    assert ids == {"ses_alive"}


@pytest.mark.asyncio
async def test_refresh_handles_upstream_error_gracefully() -> None:
    """If both endpoints throw, refresh is a no-op and existing state
    is left intact (the next tick will try again)."""
    class _BrokenClient:
        async def list_sessions(self) -> list[dict[str, Any]]:
            raise RuntimeError("opencode down")

        async def session_status(self) -> dict[str, Any]:
            return {}

    monitor = SessionMonitor(_BrokenClient())
    await monitor.refresh()
    assert monitor.sessions == []
    assert monitor.status == {}


@pytest.mark.asyncio
async def test_refresh_probes_when_status_map_omits_session() -> None:
    """OpenCode 1.16.2 only emits /session/status entries while an
    agent is actively running. A recently-updated session with no
    status-map entry must be probed via messages() and reported as
    busy if its latest message shows in-flight work.
    """
    busy_message = {
        "info": {
            "role": "assistant",
            "time": {"created": 0},  # no completed key
            "error": None,
        },
        "parts": [],
    }
    finished_message = {
        "info": {
            "role": "assistant",
            "time": {"created": 0, "completed": 1.0},
            "error": None,
        },
        "parts": [],
    }

    class _ProbeClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(
                sessions=[
                    _session("ses_active", updated=time.time()),
                    _session("ses_done", updated=time.time()),
                ],
                status={},  # upstream reports nothing for either
            )
            self.probed: list[str] = []
            self.responses = {
                "ses_active": [busy_message],
                "ses_done": [finished_message],
            }

        async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
            self.probed.append(session_id)
            return self.responses.get(session_id, [])

    client = _ProbeClient()
    monitor = SessionMonitor(client)
    await monitor.refresh()

    # Both recent sessions were probed (neither was in status map).
    assert set(client.probed) == {"ses_active", "ses_done"}
    # The active one shows as busy, the completed one as idle.
    assert monitor.status["ses_active"] == BUSY
    assert monitor.status["ses_done"] == IDLE


@pytest.mark.asyncio
async def test_refresh_skips_probe_for_old_sessions() -> None:
    """A session with no status-map entry but old ``updated`` time
    must NOT be probed every refresh — it would be wasted bandwidth.
    """
    class _ProbeClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(
                sessions=[_session("ses_old", updated=1.0)],
                status={},
            )
            self.probed: list[str] = []

        async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
            self.probed.append(session_id)
            return []

    client = _ProbeClient()
    monitor = SessionMonitor(client)
    await monitor.refresh()
    assert client.probed == []


@pytest.mark.asyncio
async def test_probe_failure_does_not_crash_refresh() -> None:
    """If messages() throws, the session falls back to IDLE."""
    class _FailingProbeClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(
                sessions=[_session("ses_x", updated=100.0)],
                status={},
            )

        async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
            raise RuntimeError("upstream down")

    monitor = SessionMonitor(_FailingProbeClient())
    await monitor.refresh()
    assert monitor.status.get("ses_x", IDLE) == IDLE


# --- stale-busy detection ---


from openloom.core.events import Event, EventType  # noqa: E402


class _BusyClient(_FakeClient):
    """Always reports one session as busy with no message progress."""

    def __init__(self, sid: str, updated: float) -> None:
        super().__init__(
            sessions=[_session(sid, updated=updated)],
            status={sid: {"type": "busy"}},
        )
        self.messages_calls = 0

    async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self.messages_calls += 1
        # Return a single busy assistant message with no completion
        # time so the probe path is not exercised (the session is
        # already in the status map as busy).
        return [{
            "info": {
                "role": "assistant",
                "time": {"created": 0.0},
                "error": None,
            },
            "parts": [],
        }]


def _capture_handler() -> tuple[list[Event], Any]:
    captured: list[Event] = []

    def handler(event: Event) -> None:
        captured.append(event)

    return captured, handler


@pytest.mark.asyncio
async def test_stale_busy_fires_after_threshold_consecutive_refreshes() -> None:
    """A session stuck busy for N consecutive refreshes (with no
    progress and no recovery) should fire SESSION_STALE_BUSY exactly
    once, on the Nth tick."""
    client = _BusyClient("ses_stuck", updated=time.time())
    monitor = SessionMonitor(client, stale_busy_threshold=3)
    captured, handler = _capture_handler()
    monitor.on_event(handler)

    for _ in range(2):
        await monitor.refresh()
    assert captured == [], "should not fire below the threshold"

    await monitor.refresh()
    assert len(captured) == 1, "should fire on the threshold tick"
    assert captured[0].type is EventType.SESSION_STALE_BUSY
    data = captured[0].data
    assert data["session_id"] == "ses_stuck"
    assert data["consecutive_busy_checks"] == 3
    assert data["threshold_checks"] == 3
    assert data["directory"] == "/tmp"

    # One-shot: continued stuck ticks do not re-fire.
    for _ in range(5):
        await monitor.refresh()
    assert len(captured) == 1
    assert "ses_stuck" in monitor.stale_busy_sessions


@pytest.mark.asyncio
async def test_stale_busy_rearms_after_session_recovers() -> None:
    """Once a session goes idle (or shows new progress), the
    one-shot latch releases so a future stuck episode can fire again."""
    client = _BusyClient("ses_recovers", updated=time.time())
    monitor = SessionMonitor(client, stale_busy_threshold=2)
    captured, handler = _capture_handler()
    monitor.on_event(handler)

    # Episode 1: get stuck for 2 ticks
    await monitor.refresh()
    await monitor.refresh()
    assert len(captured) == 1

    # Recovery: upstream now reports idle. The 12s BUSY_HOLD keeps
    # monitor.status['busy'] for cosmetic reasons, but the stale
    # tracker should release its one-shot latch so a future stuck
    # episode can fire again.
    client._status = {"ses_recovers": {"type": "idle"}}
    await monitor.refresh()
    assert "ses_recovers" not in monitor.stale_busy_sessions
    assert monitor._stale_fired == set()

    # Episode 2: stuck again, should re-fire
    client._status = {"ses_recovers": {"type": "busy"}}
    await monitor.refresh()
    await monitor.refresh()
    stale = [e for e in captured if e.type is EventType.SESSION_STALE_BUSY]
    assert len(stale) == 2


@pytest.mark.asyncio
async def test_stale_busy_resets_when_new_message_completes() -> None:
    """A session that is busy in the status map but whose latest
    message has a fresh ``completed`` timestamp is making real
    progress — the counter must reset, not accumulate."""
    class _ProgressClient(_BusyClient):
        def __init__(self) -> None:
            super().__init__("ses_making_progress", updated=time.time())
            self.last_completed = time.time()  # advances each call

        async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
            self.last_completed += 1.0
            return [{
                "info": {
                    "role": "assistant",
                    "time": {"created": 0, "completed": self.last_completed},
                    "error": None,
                },
                "parts": [],
            }]

    client = _ProgressClient()
    monitor = SessionMonitor(client, stale_busy_threshold=3)
    captured, handler = _capture_handler()
    monitor.on_event(handler)

    for _ in range(10):
        await monitor.refresh()
    # Despite 10 ticks, the message-completion timestamps advanced
    # each time → counter never crosses the threshold.
    assert captured == []


@pytest.mark.asyncio
async def test_stale_busy_works_when_status_map_omits_session() -> None:
    """The probe path (session absent from /session/status) must
    also feed the stale-busy counter, since that's how OpenCode
    1.16.2 reports an actively-running session whose tool is busy."""
    busy_message = {
        "info": {
            "role": "assistant",
            "time": {"created": 0.0},  # no completed key
            "error": None,
        },
        "parts": [
            {
                "type": "tool",
                "state": {"status": "running"},
            },
        ],
    }

    class _ProbeBusyClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(
                sessions=[_session("ses_probe", updated=time.time())],
                status={},  # upstream reports nothing
            )

        async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
            return [busy_message]

    client = _ProbeBusyClient()
    monitor = SessionMonitor(client, stale_busy_threshold=2)
    captured, handler = _capture_handler()
    monitor.on_event(handler)

    await monitor.refresh()
    await monitor.refresh()
    stale = [e for e in captured if e.type is EventType.SESSION_STALE_BUSY]
    assert len(stale) == 1
    assert stale[0].data["session_id"] == "ses_probe"


# --- forget_session: release per-session state on task drop ---


@pytest.mark.asyncio
async def test_forget_session_clears_all_per_session_state() -> None:
    """A session whose owning task was archived / paused /
    completed must drop out of stale_busy_sessions on the next
    property read. Without this, the dashboard 'N stuck' pill
    outlives the task that owns the session because the
    upstream-cleanup branch only fires when OpenCode itself
    forgets the session."""
    client = _BusyClient("ses_stuck", updated=time.time())
    monitor = SessionMonitor(client, stale_busy_threshold=2)
    captured, handler = _capture_handler()
    monitor.on_event(handler)

    # Drive the counter to threshold so the session is 'stuck'.
    await monitor.refresh()
    await monitor.refresh()
    assert monitor.stale_busy_sessions == ["ses_stuck"]
    assert "ses_stuck" in monitor._stale_fired

    # The owning task is now archived — drop the session.
    monitor.forget_session("ses_stuck")

    assert monitor.stale_busy_sessions == []
    assert "ses_stuck" not in monitor._stale_fired
    # All per-session dicts are empty.
    assert monitor._status == {}
    assert monitor._last_busy_at == {}
    assert monitor._stale_count == {}
    assert monitor._latest_progress_at == {}
    assert monitor._last_messages == {}


@pytest.mark.asyncio
async def test_forget_session_then_resume_re_fires_correctly() -> None:
    """After forget_session, if the same session id appears
    again in OpenCode's list with a busy status, the counter
    must start from 0 (not from the previous threshold) so the
    next stuck episode can re-fire."""
    client = _BusyClient("ses_recover", updated=time.time())
    monitor = SessionMonitor(client, stale_busy_threshold=2)
    captured, handler = _capture_handler()
    monitor.on_event(handler)

    await monitor.refresh()
    await monitor.refresh()
    assert len([e for e in captured if e.type is EventType.SESSION_STALE_BUSY]) == 1
    monitor.forget_session("ses_recover")
    assert monitor._stale_count == {}

    # Re-attach: same session id, but the latch has been
    # released so a new stuck episode can fire.
    await monitor.refresh()
    await monitor.refresh()
    stale = [e for e in captured if e.type is EventType.SESSION_STALE_BUSY]
    assert len(stale) == 2


@pytest.mark.asyncio
async def test_forget_session_idempotent_for_unknown_session() -> None:
    """Calling forget_session with an id we never tracked must
    be a no-op (it has to be safe to wire on every drop
    without a prior membership check)."""
    monitor = SessionMonitor(_FakeClient(sessions=[], status={}))
    monitor.forget_session("ses_never_seen")
    monitor.forget_session("")
    assert monitor.stale_busy_sessions == []


@pytest.mark.asyncio
async def test_forget_session_does_not_clear_other_sessions() -> None:
    """Dropping one session must not perturb the stuck counter
    of another session that is independently stuck."""

    class _TwoSessions(_FakeClient):
        def __init__(self) -> None:
            super().__init__(
                sessions=[
                    _session("ses_a", updated=time.time()),
                    _session("ses_b", updated=time.time()),
                ],
                status={"ses_a": {"type": "busy"}, "ses_b": {"type": "busy"}},
            )

    monitor = SessionMonitor(_TwoSessions(), stale_busy_threshold=2)
    await monitor.refresh()
    await monitor.refresh()
    assert set(monitor.stale_busy_sessions) == {"ses_a", "ses_b"}

    monitor.forget_session("ses_a")
    assert monitor.stale_busy_sessions == ["ses_b"]
