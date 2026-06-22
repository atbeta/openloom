"""SessionMonitor contract tests — covers status map purging and the
silent-busy probe.

The OpenCode server can leave stale ``{type: busy}`` entries in its
``/session/status`` map after the corresponding session is removed from
SQLite. If the dashboard trusts the upstream map blindly, those
deleted sessions stay displayed as busy forever (until the OpenCode
server is restarted).

``SessionMonitor.refresh()`` is the defensive layer: it must drop
status / busy-hold entries for any session that no longer appears in
``list_sessions()``.

Silent-busy probe (0.13+): OpenCode 1.16.x sometimes drops a session
from the status map mid-tool (the upstream entry vanishes while the
agent is still working). The monitor compensates by probing
recently-updated sessions that are absent from the status map: it
calls ``messages()`` and asks ``messages_indicate_busy`` whether the
latest assistant turn is still open. Probing is rate-limited
per-session and batched per refresh.
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


def _session(sid: str, updated: float = 1.0, time_block: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": sid,
        "title": sid,
        "directory": "/tmp",
        "time": time_block or {"created": updated, "updated": updated, "archived": 0},
        "parentID": None,
    }


# ── ghost-busy purge (pre-existing 0.12 contract) ──────────────────────


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


# ── silent-busy probe (0.13+) ────────────────────────────────────────────


def _busy_message() -> dict[str, Any]:
    """A message whose assistant turn is still open (no completed time)."""
    return {
        "info": {
            "role": "assistant",
            "time": {"created": 0},  # no 'completed' key
            "error": None,
        },
        "parts": [],
    }


def _finished_message() -> dict[str, Any]:
    return {
        "info": {
            "role": "assistant",
            "time": {"created": 0, "completed": 1.0},
            "error": None,
        },
        "parts": [],
    }


@pytest.mark.asyncio
async def test_absent_recently_updated_session_is_probed_busy() -> None:
    """A recently-updated session absent from /session/status is
    probed via messages(); an open assistant turn promotes it to BUSY."""
    updated = time.time()
    client = _FakeClient(
        sessions=[_session("ses_silent", updated=updated)],
        status={},
    )
    client.messages_response = [_busy_message()]

    async def _messages(sid: str, limit: int = 20) -> list[dict[str, Any]]:
        return client.messages_response

    client.messages = _messages  # type: ignore[method-assign]

    monitor = SessionMonitor(client)
    await monitor.refresh()

    assert monitor.status["ses_silent"] == BUSY


@pytest.mark.asyncio
async def test_absent_session_with_finished_message_stays_idle() -> None:
    """If messages() shows the assistant turn is completed, the absent
    session is genuinely idle and stays IDLE."""
    updated = time.time()
    client = _FakeClient(
        sessions=[_session("ses_done", updated=updated)],
        status={},
    )

    async def _messages(sid: str, limit: int = 20) -> list[dict[str, Any]]:
        return [_finished_message()]

    client.messages = _messages  # type: ignore[method-assign]

    monitor = SessionMonitor(client)
    await monitor.refresh()

    assert monitor.status["ses_done"] == IDLE


@pytest.mark.asyncio
async def test_stale_session_is_not_probed() -> None:
    """A session whose last update is older than the freshness
    window (5 min by default) is assumed idle — probing it would
    just waste an upstream call."""
    stale = time.time() - 600  # 10 minutes ago
    client = _FakeClient(
        sessions=[_session("ses_old", updated=stale)],
        status={},
    )
    probe_calls: list[str] = []

    async def _messages(sid: str, limit: int = 20) -> list[dict[str, Any]]:
        probe_calls.append(sid)
        return [_busy_message()]

    client.messages = _messages  # type: ignore[method-assign]

    monitor = SessionMonitor(client)
    await monitor.refresh()

    assert probe_calls == []
    assert monitor.status["ses_old"] == IDLE


@pytest.mark.asyncio
async def test_probe_is_rate_limited_per_session() -> None:
    """A session that was probed within the cooldown window is not
    re-probed on the next refresh."""
    updated = time.time()
    client = _FakeClient(
        sessions=[_session("ses_x", updated=updated)],
        status={},
    )
    probe_calls: list[str] = []

    async def _messages(sid: str, limit: int = 20) -> list[dict[str, Any]]:
        probe_calls.append(sid)
        return [_busy_message()]

    client.messages = _messages  # type: ignore[method-assign]

    monitor = SessionMonitor(client)
    await monitor.refresh()
    await monitor.refresh()

    assert len(probe_calls) == 1, (
        f"expected exactly one probe call, got {len(probe_calls)}"
    )


@pytest.mark.asyncio
async def test_probe_failure_does_not_crash_refresh() -> None:
    """If messages() throws during the probe, the session falls back
    to IDLE and the refresh continues for other sessions."""
    updated = time.time()
    client = _FakeClient(
        sessions=[
            _session("ses_broken", updated=updated),
            _session("ses_ok", updated=updated),
        ],
        status={},
    )

    async def _messages(sid: str, limit: int = 20) -> list[dict[str, Any]]:
        if sid == "ses_broken":
            raise RuntimeError("upstream down")
        return [_busy_message()]

    client.messages = _messages  # type: ignore[method-assign]

    monitor = SessionMonitor(client)
    await monitor.refresh()

    # Broken session falls back to IDLE (no probe succeeded), ok
    # session is probed and promoted to BUSY.
    assert monitor.status["ses_broken"] == IDLE
    assert monitor.status["ses_ok"] == BUSY


@pytest.mark.asyncio
async def test_probe_batch_is_capped() -> None:
    """Only a bounded number of absent sessions are probed per
    refresh — the rest fall back to IDLE without an upstream call."""
    now = time.time()
    sessions = [
        _session(f"ses_{i:02d}", updated=now - i) for i in range(10)
    ]
    client = _FakeClient(sessions=sessions, status={})

    probe_calls: list[str] = []

    async def _messages(sid: str, limit: int = 20) -> list[dict[str, Any]]:
        probe_calls.append(sid)
        return [_busy_message()]

    client.messages = _messages  # type: ignore[method-assign]

    monitor = SessionMonitor(client)
    await monitor.refresh()

    # The batch cap is hardcoded to 3 in monitor.py; the 3 most
    # recently updated sessions are probed.
    assert len(probe_calls) == 3
    assert probe_calls == ["ses_00", "ses_01", "ses_02"]


@pytest.mark.asyncio
async def test_probed_status_does_not_leak_to_ghost_sessions() -> None:
    """If a session disappeared from list_sessions but still has a
    cached probe result, the next refresh must drop it — same
    purge semantics as upstream-reported status."""
    visible = [_session("ses_alive", updated=1.0)]
    client = _FakeClient(visible, {})
    monitor = SessionMonitor(client)
    monitor._probed["ses_ghost"] = BUSY
    monitor._last_probed_at["ses_ghost"] = time.time()
    await monitor.refresh()

    assert "ses_ghost" not in monitor._probed
    assert "ses_ghost" not in monitor._last_probed_at
