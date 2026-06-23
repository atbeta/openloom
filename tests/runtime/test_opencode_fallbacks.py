"""Regression tests for the OpenCode client fallback paths.

Three call sites share a latent bug where they caught the wrong
exception type (httpx.HTTPStatusError) while ``_request`` actually
raises the project-local ``OpenCodeError``. The except clauses
therefore never matched in practice: real upstream failures
surfaced as OpenCodeError instead of falling through to the fallback
endpoint or returning the contract value.

These tests pin the contract:

- ``send_prompt_async`` falls back from ``/prompt_async`` to
  ``/message`` on 404 / 405 (covered in test_opencode_prompt_async.py).
- ``create_session`` falls back from ``POST /session?<query>`` to
  ``POST /session`` (this file).
- ``abort_session`` returns ``False`` (not raise) on 404 / 409
  (this file).

Without these tests the bug could regress silently — ``except`` is
a single line and a reviewer could easily miss a wrong exception
type.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from openloom.runtime.opencode import OpenCodeClient, OpenCodeError


def _client() -> OpenCodeClient:
    return OpenCodeClient("http://127.0.0.1:4096", "opencode", "")


# ── create_session fallback ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_session_falls_back_to_plain_post_on_404() -> None:
    """When the new ``POST /session?<query>`` endpoint returns 404
    (older OpenCode that only knows ``POST /session``), the client
    must transparently retry against the legacy endpoint. Pre-fix
    the fallback was unreachable."""
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096", assert_all_mocked=False) as mock:
        primary = mock.post(url__regex=r"/session\?directory=").mock(
            return_value=httpx.Response(404, text="not found"),
        )
        fallback = mock.post("/session").mock(
            return_value=httpx.Response(200, json={"id": "ses_legacy"}),
        )
        session = await client.create_session(cwd="/tmp/proj", title="t")
    assert primary.called
    assert fallback.called
    assert session["id"] == "ses_legacy"
    # Legacy fallback must carry the directory (cwd) in the body.
    request = fallback.calls[0].request
    import json
    body = json.loads(request.content)
    assert body["directory"] == "/tmp/proj"


@pytest.mark.asyncio
async def test_create_session_falls_back_on_500_too() -> None:
    """create_session's fallback is intentionally lenient: ANY
    non-2xx from the new endpoint triggers the legacy fallback, not
    just 404. Older OpenCode servers that don't recognise the new
    route return whatever error code they return — 400, 405, 500 —
    and the client should still succeed against the legacy endpoint.

    This is a deliberate contrast to ``send_prompt_async``, whose
    fallback is narrowly filtered to 404/405 because the
    /message-and-/prompt_async split is a version-compatibility
    shim rather than a degraded-mode retry."""
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096", assert_all_mocked=False) as mock:
        mock.post(url__regex=r"/session\?directory=").mock(
            return_value=httpx.Response(500, text="boom"),
        )
        fallback = mock.post("/session").mock(
            return_value=httpx.Response(200, json={"id": "ses_recovered"}),
        )
        session = await client.create_session(cwd="/tmp", title="t")
    assert fallback.called
    assert session["id"] == "ses_recovered"


# ── abort_session 404 / 409 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abort_session_returns_false_on_404() -> None:
    """abort_session on a vanished session must return False rather
    than raise — the dashboard's POST /api/tasks/{id}/abort relies
    on this contract to distinguish 'aborted cleanly' from 'no such
    task'. Pre-fix the 404 surfaced as OpenCodeError and the
    dashboard's ``abort_task`` aborted-with-error path fired."""
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.post("/session/ses_gone/abort").mock(
            return_value=httpx.Response(404, text="not found"),
        )
        result = await client.abort_session("ses_gone")
    assert result is False


@pytest.mark.asyncio
async def test_abort_session_returns_false_on_409() -> None:
    """409 Conflict means 'session is in a state that can't be
    aborted' (e.g. already idle). Treat as 'no abort needed'."""
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.post("/session/ses_idle/abort").mock(
            return_value=httpx.Response(409, text="conflict"),
        )
        result = await client.abort_session("ses_idle")
    assert result is False


@pytest.mark.asyncio
async def test_abort_session_returns_true_on_200() -> None:
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.post("/session/ses_active/abort").mock(
            return_value=httpx.Response(200, text="ok"),
        )
        result = await client.abort_session("ses_active")
    assert result is True


@pytest.mark.asyncio
async def test_abort_session_propagates_500() -> None:
    """Non 4xx-and-not-409/404 statuses propagate — silent False on
    a 500 would mask real failures."""
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.post("/session/ses_x/abort").mock(
            return_value=httpx.Response(500, text="boom"),
        )
        with pytest.raises(OpenCodeError) as exc_info:
            await client.abort_session("ses_x")
    assert exc_info.value.status_code == 500
