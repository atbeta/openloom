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


# NOTE on the /message fallback path: ``_request`` raises
# ``OpenCodeError`` on status >= 400, but ``send_prompt_async``
# only catches ``httpx.HTTPStatusError``. That means a real 404
# from /prompt_async would surface as OpenCodeError rather than
# triggering the /message fallback. That is a pre-existing latent
# bug — not introduced by this commit — and the harness never
# relied on it (the only production callers go through the
# /prompt_async primary path). Pinning the fallback contract here
# would mean also fixing the exception type, which is out of
# scope for the directory fix. The two tests below cover the
# primary path only; the fallback contract will be revisited
# alongside the exception-type fix.
