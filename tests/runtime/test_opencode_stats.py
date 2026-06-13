"""OpenCode runtime tests — session stats backfill for older servers.

OpenCode 1.14.x / 1.15.x do not populate the session-level ``tokens``
and ``cost`` fields; they live on each message's ``info.tokens`` and
``info.cost``. OpenLoom 1.6+ backfills them at list time so the
dashboard never sees "0 tokens" on a session that actually spent some.
"""

from __future__ import annotations

import pytest

from openloom.runtime.opencode import _aggregate_message_stats


def test_aggregate_empty() -> None:
    assert _aggregate_message_stats([]) == {"tokens": None, "cost": None}


def test_aggregate_skips_messages_without_info() -> None:
    msgs = [{"role": "user", "text": "hi"}, {"parts": []}]
    assert _aggregate_message_stats(msgs) == {"tokens": None, "cost": None}


def test_aggregate_sums_tokens_and_cost() -> None:
    msgs = [
        {
            "info": {
                "role": "assistant",
                "tokens": {
                    "input": 30657, "output": 129,
                    "reasoning": 45,
                    "cache": {"read": 0, "write": 0},
                },
                "cost": 0.05,
            },
        },
        {
            "info": {
                "role": "assistant",
                "tokens": {
                    "input": 5435, "output": 162,
                    "reasoning": 321,
                    "cache": {"read": 30720, "write": 0},
                },
                "cost": 0.01,
            },
        },
        # User message with no usage — should not contribute.
        {"info": {"role": "user"}},
    ]
    out = _aggregate_message_stats(msgs)
    assert out["cost"] == pytest.approx(0.06)
    # Match the 1.16.x session-level payload shape: no ``total`` key,
    # ``cache`` is a nested dict.
    assert out["tokens"] == {
        "input": 36092,
        "output": 291,
        "reasoning": 366,
        "cache": {"read": 30720, "write": 0},
    }
    assert "total" not in out["tokens"]


def test_aggregate_handles_partial_payloads() -> None:
    """A message missing cost (or tokens) should not zero the others."""
    msgs = [
        {"info": {"tokens": {"input": 100, "output": 50, "reasoning": 0}}},
        {"info": {"cost": 0.42}},
    ]
    out = _aggregate_message_stats(msgs)
    assert out["cost"] == 0.42
    assert out["tokens"]["input"] == 100
    assert out["tokens"]["output"] == 50


def test_aggregate_ignores_non_numeric_values() -> None:
    """Defensive: bad payload should not crash the dashboard."""
    msgs = [
        {"info": {"tokens": "huge", "cost": "expensive"}},
        {"info": {"tokens": {"input": None, "output": 5, "reasoning": 0}}},
    ]
    out = _aggregate_message_stats(msgs)
    assert out["tokens"]["input"] == 0  # None skipped
    assert out["tokens"]["output"] == 5
    assert out["cost"] == 0  # string skipped


# --- _fetch_all_messages pagination regression test ---
#
# Old behaviour: the list-response branch in _fetch_all_messages
# returned after the first page (default page_size=100). Long sessions
# were truncated, producing a ~3.5x undercount vs OpenCode's own
# ``stats`` CLI. The new implementation must use the ``before`` cursor
# to walk the full feed. The test below simulates a 350-message session
# across 2 full pages + 1 short page, and asserts the aggregate sees
# all 350 messages' tokens.


def _mk_message(i: int, *, with_tokens: bool = True) -> dict:
    """Build a fake OpenCode message. Token counts grow with i so a
    partial fetch is easy to spot in the assertion."""
    m: dict = {"id": f"msg_{i:04d}", "role": "assistant"}
    if with_tokens:
        m["info"] = {
            "role": "assistant",
            "tokens": {
                "input": 100 + i,
                "output": 10 + i,
                "reasoning": i,
                "cache": {"read": i * 2, "write": 0},
            },
            "cost": 0.001 * (i + 1),
        }
    return m


def _page_of(start: int, count: int) -> list[dict]:
    return [_mk_message(i) for i in range(start, start + count)]


@pytest.mark.asyncio
async def test_fetch_all_messages_paginates_with_before_cursor() -> None:
    """3 pages: 200, 200, 50. total = 450 messages. Old code would
    only see the first 200. New code must aggregate across all 450."""
    import httpx
    import respx

    from openloom.runtime.opencode import (
        MAX_MESSAGES_PER_SESSION,
        OpenCodeClient,
    )

    # If MAX was set to a tiny number for the test, restore it.
    # (This is purely defensive — we don't shrink the cap in the test.)
    assert MAX_MESSAGES_PER_SESSION >= 450

    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")

    pages = {
        0: _page_of(0, 200),    # initial page, no cursor
        1: _page_of(200, 200),  # before=msg_0399
        2: _page_of(400, 50),   # before=msg_0599, short page = end
    }
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        before = request.url.params.get("before")
        if before is None:
            page = pages[0]
        elif before == "msg_0199":
            page = pages[1]
        elif before == "msg_0399":
            page = pages[2]
        else:
            return httpx.Response(200, json=[])
        call_count["n"] += 1
        return httpx.Response(200, json=page)

    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        route = mock.get("/session/ses_long/message").mock(side_effect=_handler)
        messages = await client._fetch_all_messages("ses_long")

    # All 450 messages collected.
    assert len(messages) == 450
    # Three round-trips (initial + 2 cursor advances).
    assert call_count["n"] == 3
    # Cursors were the oldest id of each non-terminal page.
    cursors = [c.request.url.params.get("before") for c in route.calls]
    assert cursors == [None, "msg_0199", "msg_0399"]

    # Aggregate must reflect the FULL session, not the first 200.
    agg = _aggregate_message_stats(messages)
    assert agg["tokens"] is not None
    # Expected: input = sum(100..549), output = sum(10..459),
    # reasoning = sum(0..449), cache.read = sum(2*i for i in 0..449).
    # Using simple closed forms:
    n = 450
    expected_input = 100 * n + n * (n - 1) // 2          # 100 + i, i in 0..n-1
    expected_output = 10 * n + n * (n - 1) // 2          # 10 + i
    expected_reasoning = n * (n - 1) // 2                # i
    expected_cache_read = 2 * n * (n - 1) // 2          # 2*i
    assert agg["tokens"]["input"] == expected_input
    assert agg["tokens"]["output"] == expected_output
    assert agg["tokens"]["reasoning"] == expected_reasoning
    assert agg["tokens"]["cache"]["read"] == expected_cache_read

    # And specifically NOT the truncated value the old code would have
    # produced (first 200 messages only).
    truncated_input = 100 * 200 + 200 * 199 // 2
    assert agg["tokens"]["input"] != truncated_input
    assert agg["tokens"]["input"] > truncated_input


@pytest.mark.asyncio
async def test_fetch_all_messages_single_short_page() -> None:
    """A session with fewer than page_size messages must return them
    all without an extra round-trip."""
    import httpx
    import respx

    from openloom.runtime.opencode import OpenCodeClient

    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")
    page = _page_of(0, 37)  # short page

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.get("/session/ses_short/message").mock(side_effect=_handler)
        messages = await client._fetch_all_messages("ses_short")

    assert len(messages) == 37


@pytest.mark.asyncio
async def test_fetch_all_messages_empty_session() -> None:
    import httpx
    import respx

    from openloom.runtime.opencode import OpenCodeClient

    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")

    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.get("/session/ses_empty/message").mock(
            return_value=httpx.Response(200, json=[]),
        )
        messages = await client._fetch_all_messages("ses_empty")

    assert messages == []


@pytest.mark.asyncio
async def test_populate_session_stats_uses_backfill_for_114(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end: 1.14-style session list (no ``tokens`` field) gets
    backfilled by paging through /message with the ``before`` cursor.
    A 350-message session with deterministic token counts must end up
    with the full sum, not the truncated first-page sum."""
    import logging

    import httpx
    import respx

    from openloom.runtime.opencode import OpenCodeClient

    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")

    sessions = [
        {
            "id": "ses_long_114",
            "title": "long session",
            "updated": 1_700_000_000,
            # NB: no "tokens" / "cost" — simulates 1.14 server response
        },
    ]
    page1 = _page_of(0, 200)
    page2 = _page_of(200, 150)  # short page

    def _handler(request: httpx.Request) -> httpx.Response:
        before = request.url.params.get("before")
        page = page1 if before is None else page2
        return httpx.Response(200, json=page)

    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.get("/session/ses_long_114/message").mock(side_effect=_handler)
        with caplog.at_level(logging.WARNING, logger="openloom.runtime.opencode"):
            await client._populate_session_stats(sessions)

    # tokens / cost were backfilled
    assert "tokens" in sessions[0]
    assert "cost" in sessions[0]
    # Full 350 messages' worth, not the 200-page truncation
    n = 350
    expected_input = 100 * n + n * (n - 1) // 2
    assert sessions[0]["tokens"]["input"] == expected_input
    # No "hit MAX" warning expected — 350 < 5000
    assert not any("MAX_MESSAGES_PER_SESSION" in r.message for r in caplog.records)
