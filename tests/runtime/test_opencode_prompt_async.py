"""Regression tests for ``OpenCodeClient.send_prompt_async`` directory handling.

The original implementation posted the prompt body but never forwarded
the session's workspace as a ``?directory=`` query param. On Windows
in particular the OpenCode server then ran the agent's tool
subprocess with the server's own CWD (typically
``C:\\Users\\<user>``), so the agent would report
``C:\\Users\\xxx is not a git directory`` even though the session
metadata showed the correct path. These tests pin the contract that
``directory`` is forwarded to both the primary ``/prompt_async`` route
and the fallback ``/message`` route.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from openloom.runtime.opencode import OpenCodeClient


def _client() -> OpenCodeClient:
    return OpenCodeClient("http://127.0.0.1:4096", "opencode", "")


@pytest.mark.asyncio
async def test_send_prompt_async_forwards_directory_to_prompt_async() -> None:
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        route = mock.post("/session/ses_abc/prompt_async").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )
        await client.send_prompt_async(
            session_id="ses_abc",
            prompt="look at recent commits",
            directory="D:\\MyRepo",
        )
    assert route.called
    request = route.calls[0].request
    assert request.url.params["directory"] == "D:\\MyRepo"
    body = request.content.decode("utf-8")
    assert "look at recent commits" in body


@pytest.mark.asyncio
async def test_send_prompt_async_omits_directory_when_not_provided() -> None:
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        route = mock.post("/session/ses_abc/prompt_async").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )
        await client.send_prompt_async(
            session_id="ses_abc",
            prompt="hello",
        )
    request = route.calls[0].request
    assert "directory" not in request.url.params


# Regression coverage for the /message fallback path.

# ``send_prompt_async`` historically caught ``httpx.HTTPStatusError``
# in its fallback branch, but ``_request`` raises ``OpenCodeError`` —
# a plain Exception subclass, NOT an ``httpx.HTTPStatusError``. The
# fallback clause therefore never matched in practice: a real 404
# from /prompt_async surfaced as an uncaught OpenCodeError instead
# of falling through to /message. Three call sites had the same
# latent bug (create_session fallback, abort_session 404/409,
# send_prompt_async /message fallback); all three now catch
# OpenCodeError. The fallback path is now reachable from real
# upstream failures, not just from test scaffolding.


@pytest.mark.asyncio
async def test_send_prompt_async_falls_back_to_message_on_404() -> None:
    """A 404 from /prompt_async must trigger the /message fallback.

    Old OpenCode (pre-1.16) does not have /prompt_async; it only
    exposes /session/{id}/message. When the harness is connected to
    such a server, the fallback is the *only* way to dispatch a
    prompt. Pre-fix, the fallback never fired because the except
    clause caught the wrong exception type.
    """
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.post("/session/ses_abc/prompt_async").mock(
            return_value=httpx.Response(404, text="not found"),
        )
        fallback = mock.post("/session/ses_abc/message").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )
        await client.send_prompt_async(
            session_id="ses_abc",
            prompt="look at recent commits",
            directory="/Users/me/repo",
        )
    assert fallback.called, (
        "expected /message fallback to fire when /prompt_async returns 404"
    )


@pytest.mark.asyncio
async def test_send_prompt_async_falls_back_on_405() -> None:
    """405 Method Not Allowed is the other status code that should
    trigger the fallback — some OpenCode versions reject POST on
    /prompt_async without the right content type."""
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.post("/session/ses_abc/prompt_async").mock(
            return_value=httpx.Response(405, text="method not allowed"),
        )
        fallback = mock.post("/session/ses_abc/message").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )
        await client.send_prompt_async(session_id="ses_abc", prompt="x")
    assert fallback.called


@pytest.mark.asyncio
async def test_send_prompt_async_does_not_fall_back_on_500() -> None:
    """A 500 from /prompt_async must propagate, not trigger the
    fallback — silently retrying on /message would mask real
    upstream failures."""
    from openloom.runtime.opencode import OpenCodeError

    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.post("/session/ses_abc/prompt_async").mock(
            return_value=httpx.Response(500, text="server error"),
        )
        with pytest.raises(OpenCodeError) as exc_info:
            await client.send_prompt_async(session_id="ses_abc", prompt="x")
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_send_prompt_async_forwards_directory_to_fallback() -> None:
    """The directory query param must be forwarded to /message too —
    not just /prompt_async. Tested directly so a regression here
    can't hide behind the 404 path."""
    client = _client()
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.post("/session/ses_abc/prompt_async").mock(
            return_value=httpx.Response(404),
        )
        fallback = mock.post("/session/ses_abc/message").mock(
            return_value=httpx.Response(200),
        )
        await client.send_prompt_async(
            session_id="ses_abc", prompt="x", directory="/path/to/ws",
        )
    request = fallback.calls[0].request
    assert request.url.params["directory"] == "/path/to/ws"
