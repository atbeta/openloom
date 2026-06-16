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


