"""Route contract tests — cover the full deck-compatible API surface."""

from __future__ import annotations

import os
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

    from openloom.config import Settings
    from openloom.core.events import EventBus
    from openloom.core.harness import HarnessRunner
    from openloom.core.registry import get_sink
    from openloom.core.store import Store
    from openloom.levels.server.console_sink import ConsoleSink  # noqa: F401 — registers
    from openloom.levels.server.monitor import SessionMonitor
    from openloom.levels.server.web_sink import WebSink  # noqa: F401 — registers
    from openloom.runtime import prompts, session_status
    from openloom.runtime.opencode import OpenCodeClient
    from openloom.server.app import create_app
    from openloom.server.recent import RecentWorkspaces

    settings = Settings.from_env()
    store = Store(settings.database_path)
    recent = RecentWorkspaces(tmp_path / "recent.sqlite3")
    client_obj = OpenCodeClient(
        settings.opencode_url, settings.opencode_username, settings.opencode_password,
    )
    bus = EventBus()
    web_sink = get_sink("web")()
    bus.subscribe_all(web_sink.on_event)
    harness = HarnessRunner(
        opencode=client_obj,
        bus=bus,
        store=store,
        prompts=prompts,
        status=session_status,
    )

    app = create_app(
        harness=harness, store=store, bus=bus, web_sink=web_sink,
        client=client_obj, monitor=SessionMonitor(client_obj),
        recent=recent, settings=settings,
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
                "metrics", "usage", "now"):
        assert key in body, f"missing key: {key}"


def test_post_task_creates_and_records_recent(client: TestClient) -> None:
    r = client.post("/api/tasks", json={
        "name": "demo",
        "workspace": "/tmp/openloom-smoke",
        "goal": "do the thing",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "taskId" in body
    assert body["ok"] is True
    assert body["status"] == "pending"
    assert body["sessionId"] is None

    workspaces = client.get("/api/recent-workspaces").json()["workspaces"]
    assert any("openloom-smoke" in w for w in workspaces)


def test_post_task_rejects_missing_goal(client: TestClient) -> None:
    r = client.post("/api/tasks", json={
        "name": "demo",
        "workspace": "/tmp/openloom-smoke",
    })
    assert r.status_code == 400
    assert "goal" in r.text.lower()


def test_post_task_rejects_missing_workspace_and_session(client: TestClient) -> None:
    r = client.post("/api/tasks", json={"name": "demo", "goal": "g"})
    assert r.status_code == 400
    assert "workspace" in r.text.lower() or "sessionid" in r.text.lower()


def test_task_pause_resume_complete_archive(client: TestClient) -> None:
    r = client.post("/api/tasks", json={
        "name": "t",
        "workspace": "/tmp/openloom-smoke",
        "goal": "do the thing",
    })
    tid = r.json()["taskId"]
    assert client.post(f"/api/tasks/{tid}/pause").json()["status"] == "paused"
    assert client.post(f"/api/tasks/{tid}/resume").json()["status"] == "running"
    assert client.post(f"/api/tasks/{tid}/complete").json()["status"] == "completed"
    assert client.post(f"/api/tasks/{tid}/archive").json()["status"] == "archived"


def test_delete_archived_task(client: TestClient) -> None:
    r = client.post("/api/tasks", json={
        "name": "t",
        "workspace": "/tmp/openloom-smoke",
        "goal": "do the thing",
    })
    tid = r.json()["taskId"]
    assert client.post(f"/api/tasks/{tid}/archive").json()["status"] == "archived"
    deleted = client.delete(f"/api/tasks/{tid}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["ok"] is True
    assert client.get(f"/api/tasks/{tid}").json().get("error") == "not found"


def test_delete_active_task_rejected(client: TestClient) -> None:
    r = client.post("/api/tasks", json={
        "name": "t",
        "workspace": "/tmp/openloom-smoke",
        "goal": "do the thing",
    })
    tid = r.json()["taskId"]
    rejected = client.delete(f"/api/tasks/{tid}")
    assert rejected.status_code == 400

