"""Route contract tests — cover the full deck-compatible API surface."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response


@pytest.fixture
def opencode_running() -> Any:
    """Stub out OpenCodeClient with a catch-all respx mock."""
    with respx.mock(assert_all_called=False) as mock:
        def passthrough(request):
            return Response(200, json={} if request.method != "GET" else [])
        mock.route(method="GET", url__regex=r".*session(_status)?($|\?|/[^/]*/?message)").mock(
            return_value=Response(200, json=[]),
        )
        mock.route(method="GET", url__regex=r".*session(_status)?($|\?|/[^/]*/?diff)").mock(
            return_value=Response(200, json=""),
        )
        mock.route(method="GET", url__regex=r".*session($|\?)").mock(
            return_value=Response(200, json=[]),
        )
        mock.route(method="GET").mock(return_value=Response(200, json=[]))
        mock.route(method="POST", url__regex=r".*session($|\?|/)").mock(
            return_value=Response(200, json={"id": "sess_test"}),
        )
        mock.route(method="PATCH").mock(return_value=Response(200, json={}))
        mock.route(method="DELETE").mock(return_value=Response(200, json={}))
        yield mock


@pytest.fixture
def client(opencode_running: Any, tmp_path: Path) -> TestClient:
    os.environ["OPENLOOM_DATABASE"] = str(tmp_path / "store.sqlite3")
    os.environ["OPENLOOM_ALLOWED_ROOTS"] = str(tmp_path)
    os.environ["OPENLOOM_STRICT_ROOTS"] = "false"

    import openloom.levels.manual.checker  # noqa
    import openloom.levels.manual.sink  # noqa
    import openloom.levels.ui.sink  # noqa

    from openloom.config import Settings
    from openloom.core.events import EventBus
    from openloom.core.store import Store
    from openloom.core.registry import get_sink
    from openloom.levels.server.monitor import SessionMonitor
    from openloom.runtime.opencode import OpenCodeClient
    from openloom.server.app import create_app
    from openloom.server.recent import RecentWorkspaces

    settings = Settings.from_env()
    store = Store(settings.database_path)
    recent = RecentWorkspaces(tmp_path / "recent.sqlite3")
    client_obj = OpenCodeClient(
        settings.opencode_url, settings.opencode_username, settings.opencode_password,
    )
    web_sink = get_sink("web")()

    def parse_spec(text: str, fmt: str):
        from openloom.runtime.prompts import parse_task_spec
        return parse_task_spec(text, fmt)

    app = create_app(
        harness=None, store=store, bus=EventBus(), web_sink=web_sink,
        client=client_obj, monitor=SessionMonitor(client_obj),
        recent=recent, settings=settings,
        parse_spec=parse_spec, pick_folder=None,
    )
    return TestClient(app)


def test_root_serves_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_api_state_shape(client: TestClient) -> None:
    r = client.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    for key in ("server", "recentWorkspaces", "tasks", "sessions",
                "sessionsByDirectory", "archivedSessions", "sessionStatus",
                "metrics", "now"):
        assert key in body, f"missing key: {key}"


def test_post_task_creates_and_records_recent(client: TestClient) -> None:
    r = client.post("/api/tasks", json={
        "format": "yaml",
        "spec": "name: t\nworkspace: /tmp/openloom-smoke\nsteps:\n  - one\n",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "taskId" in body
    tid = body["taskId"]

    workspaces = client.get("/api/recent-workspaces").json()["workspaces"]
    assert any("openloom-smoke" in w for w in workspaces)


def test_task_pause_resume_complete_archive(client: TestClient) -> None:
    r = client.post("/api/tasks", json={
        "format": "yaml",
        "spec": "name: t\nworkspace: /tmp/openloom-smoke\nsteps:\n  - one\n",
    })
    tid = r.json()["taskId"]
    assert client.post(f"/api/tasks/{tid}/pause").json()["status"] == "paused"
    assert client.post(f"/api/tasks/{tid}/resume").json()["status"] == "running"
    assert client.post(f"/api/tasks/{tid}/complete").json()["status"] == "completed"
    assert client.post(f"/api/tasks/{tid}/archive").json()["status"] == "archived"


def test_dispatch_rejects_empty_prompt(client: TestClient) -> None:
    r = client.post("/api/dispatch", json={"prompt": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "prompt" in body["error"]


def test_browse_directory(client: TestClient) -> None:
    r = client.get("/api/browse", params={"path": "/tmp"})
    assert r.status_code == 200
    body = r.json()
    assert "children" in body
    assert body["parent"] is not None


def test_browse_rejects_nonexistent(client: TestClient) -> None:
    r = client.get("/api/browse", params={"path": "/this/does/not/exist"})
    assert r.status_code in (400, 404)


def test_recent_workspaces_round_trip(client: TestClient) -> None:
    assert client.get("/api/recent-workspaces").json()["workspaces"] == []
    client.post("/api/tasks", json={
        "format": "yaml",
        "spec": "name: t\nworkspace: /tmp/openloom-smoke\nsteps:\n  - one\n",
    })
    r = client.delete("/api/recent-workspaces", params={"path": "/private/tmp/openloom-smoke"})
    assert r.json()["removed"] is True
    assert client.get("/api/recent-workspaces").json()["workspaces"] == []


def test_session_archive_endpoints(client: TestClient) -> None:
    r = client.post("/api/sessions/sess_test/archive")
    assert r.status_code == 200
    assert r.json()["archived"] is True
    r = client.delete("/api/sessions/sess_test/archive")
    assert r.status_code == 200
    assert r.json()["archived"] is False


def test_session_delete(client: TestClient) -> None:
    r = client.post("/api/sessions/sess_test/delete")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


def test_session_messages_and_diff(client: TestClient) -> None:
    r = client.get("/api/sessions/sess_test/messages")
    assert r.status_code == 200
    assert "messages" in r.json()
    r = client.get("/api/sessions/sess_test/diff")
    assert r.status_code == 200
    assert "diff" in r.json()


def test_404_task_returns_404(client: TestClient) -> None:
    assert client.post("/api/tasks/nonexistent/pause").status_code == 404


def test_static_spa_fallback(client: TestClient) -> None:
    r = client.get("/some/spa/route")
    assert r.status_code == 200
    assert "<html" in r.text.lower()
