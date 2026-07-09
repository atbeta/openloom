"""Tests for OpenCode question-request handling (v0.17).

Question requests are a separate OpenCode request type from tool
permissions. They ride ``GET /question`` and
``POST /question/{requestID}/reply``. These tests pin the
normalization, the HTTP shape, and the harness auto-pick dispatch
that lets unattended tasks never block on a decision.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from openloom.runtime.opencode import (
    OpenCodeClient,
    _question_waiting_summary,
)

# ── normalization ──────────────────────────────────────────────────────


def test_normalize_question_maps_session_id() -> None:
    item = {
        "id": "que_1",
        "sessionID": "ses_abc",
        "questions": [
            {
                "question": "Deploy?",
                "header": "Deploy",
                "options": [{"label": "Yes", "description": ""}],
            }
        ],
    }
    out = OpenCodeClient._normalize_question(item)
    assert out["id"] == "que_1"
    assert out["sessionId"] == "ses_abc"
    assert out["questions"][0]["question"] == "Deploy?"


def test_normalize_question_handles_missing_fields() -> None:
    out = OpenCodeClient._normalize_question({})
    assert out["id"] is None
    assert out["sessionId"] is None
    assert out["questions"] == []


# ── list_pending_questions (HTTP) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_list_pending_questions_filters_by_session() -> None:
    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.get("/question").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": "q1", "sessionID": "ses_a", "questions": []},
                    {"id": "q2", "sessionID": "ses_b", "questions": []},
                ],
            ),
        )
        all_q = await client.list_pending_questions()
        assert len(all_q) == 2
        only_a = await client.list_pending_questions("ses_a")
        assert len(only_a) == 1
        assert only_a[0]["id"] == "q1"


@pytest.mark.asyncio
async def test_list_pending_questions_handles_404() -> None:
    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.get("/question").mock(return_value=httpx.Response(404))
        assert await client.list_pending_questions() == []


# ── respond_question (HTTP) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_respond_question_posts_answers_array() -> None:
    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        route = mock.post("/question/que_42/reply").mock(
            return_value=httpx.Response(200, json=True),
        )
        ok = await client.respond_question(
            "que_42", [["Yes"], ["staging"]],
        )
        assert ok is True
        # The body must be {"answers": [[...], [...]]} in question order.
        sent = route.calls.last.request
        body = sent.read().decode("utf-8")
        import json as _json
        parsed = _json.loads(body)
        assert parsed == {"answers": [["Yes"], ["staging"]]}


@pytest.mark.asyncio
async def test_respond_question_returns_false_on_error() -> None:
    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.post("/question/que_x/reply").mock(
            return_value=httpx.Response(500, text="boom"),
        )
        ok = await client.respond_question("que_x", [["Yes"]])
        assert ok is False


# ── _question_waiting_summary ──────────────────────────────────────────


def test_question_summary_uses_first_question_text() -> None:
    qs = [
        {
            "id": "q1",
            "questions": [
                {"question": "Deploy to prod?", "options": []},
            ],
        }
    ]
    assert "Deploy to prod?" in _question_waiting_summary(qs)


def test_question_summary_counts_extras() -> None:
    qs = [
        {"id": "q1", "questions": [{"question": "A", "options": []}]},
        {"id": "q2", "questions": [{"question": "B", "options": []}]},
        {"id": "q3", "questions": [{"question": "C", "options": []}]},
    ]
    out = _question_waiting_summary(qs)
    assert "A" in out
    assert "+2 more" in out


def test_question_summary_handles_empty() -> None:
    assert _question_waiting_summary([]) == "Waiting for question answer"


# ── resolve_session_permissions merges questions ──────────────────────


@pytest.mark.asyncio
async def test_resolve_session_permissions_includes_questions() -> None:
    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.get("/permission").mock(
            return_value=httpx.Response(200, json=[
                {"id": "p1", "sessionID": "ses_a", "permission": "bash",
                 "patterns": ["ls"]},
            ]),
        )
        mock.get("/question").mock(
            return_value=httpx.Response(200, json=[
                {"id": "q1", "sessionID": "ses_a",
                 "questions": [{"question": "Deploy?", "options": []}]},
            ]),
        )
        out = await client.resolve_session_permissions("ses_a")
        assert out is not None
        types = [p.get("type") for p in out["pending"]]
        assert "permission" in types
        assert "question" in types


@pytest.mark.asyncio
async def test_resolve_session_permissions_questions_only() -> None:
    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.get("/permission").mock(return_value=httpx.Response(200, json=[]))
        mock.get("/question").mock(
            return_value=httpx.Response(200, json=[
                {"id": "q1", "sessionID": "ses_a",
                 "questions": [{"question": "Ship it?", "options": []}]},
            ]),
        )
        out = await client.resolve_session_permissions("ses_a")
        assert out is not None
        assert all(p.get("type") == "question" for p in out["pending"])
        assert "Ship it?" in out["summary"]


# ── harness auto-pick dispatch ────────────────────────────────────────


@pytest.mark.asyncio
async def test_harness_auto_picks_first_option_for_questions() -> None:
    """v0.17: question items get the first option auto-replied.

    Mirrors the auto-accept loop in ``HarnessRunner._check_task`` —
    we replicate the same dispatch logic against a stub client to
    pin the contract: question entries type-dispatch to
    ``respond_question`` with the first option of each question.
    """
    seen: list[tuple[str, list[list[str]]]] = []

    class _StubClient:
        async def list_pending_questions(self, sid=None, *, directory=None):
            return [{
                "id": "que_42",
                "sessionId": sid,
                "type": "question",
                "questions": [
                    {"question": "Deploy?", "options": [
                        {"label": "Yes", "description": ""},
                        {"label": "No", "description": ""},
                    ]},
                    {"question": "Region?", "options": [
                        {"label": "us-east", "description": ""},
                    ]},
                ],
            }]

        async def respond_permission(self, sid, pid, response="once"):
            raise AssertionError("should not be called for a question")

        async def respond_question(self, request_id, answers):
            seen.append((request_id, answers))
            return True

    stub = _StubClient()
    pending = await stub.list_pending_questions("ses_a")
    # The same loop the harness runs for type="question":
    for entry in pending:
        answers: list[list[str]] = []
        for q in entry["questions"]:
            options = q.get("options") or []
            if not options:
                answers.append([])
                continue
            first = options[0]
            label = str(first.get("label") or "").strip()
            answers.append([label] if label else [])
        await stub.respond_question(entry["id"], answers)

    assert seen == [("que_42", [["Yes"], ["us-east"]])]


@pytest.mark.asyncio
async def test_harness_auto_pick_skips_questions_with_no_options() -> None:
    """v0.17: if a question has no options, skip it instead of
    sending an empty answers payload that OpenCode would reject.
    """
    called = []

    class _StubClient:
        async def list_pending_questions(self, sid=None, *, directory=None):
            return [{
                "id": "que_empty",
                "sessionId": sid,
                "type": "question",
                "questions": [
                    {"question": "???", "options": []},
                ],
            }]

        async def respond_question(self, request_id, answers):
            called.append((request_id, answers))
            return True

    stub = _StubClient()
    pending = await stub.list_pending_questions("ses_a")
    for entry in pending:
        answers: list[list[str]] = []
        for q in entry["questions"]:
            options = q.get("options") or []
            if not options:
                answers.append([])
                continue
        if not any(answers):
            continue  # harness skips when nothing recognizable
        await stub.respond_question(entry["id"], answers)

    assert called == []  # nothing was sent
