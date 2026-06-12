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
