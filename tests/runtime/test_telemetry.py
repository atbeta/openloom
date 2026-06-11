from __future__ import annotations

from datetime import datetime

from openloom.runtime.telemetry import (
    UNKNOWN_MODEL,
    aggregate_session_usage,
    aggregate_usage_periods,
    session_model_name,
    session_usage_row,
)


def test_session_model_name_unknown_without_metadata() -> None:
    assert session_model_name({"model": None}) == UNKNOWN_MODEL
    assert session_model_name({}) == UNKNOWN_MODEL


def test_session_model_name_uses_agent_fallback() -> None:
    assert session_model_name({"agent": "build"}) == "agent:build"


def test_session_usage_row_parses_tokens() -> None:
    row = session_usage_row({
        "id": "s1",
        "title": "Demo",
        "directory": "/tmp/ws",
        "cost": 0.12,
        "tokens": {
            "input": 100,
            "output": 20,
            "reasoning": 5,
            "cache": {"read": 500, "write": 10},
        },
        "model": {"providerID": "p", "id": "m"},
    })
    assert row["cost"] == 0.12
    assert row["tokens"]["cacheRead"] == 500
    assert row["model"] == "p/m"


def test_aggregate_session_usage() -> None:
    usage = aggregate_session_usage([
        {
            "id": "a",
            "title": "A",
            "cost": 0.1,
            "tokens": {"input": 10, "output": 1, "cache": {"read": 100, "write": 0}},
            "model": {"providerID": "p", "id": "m1"},
        },
        {
            "id": "b",
            "title": "B",
            "cost": 0.2,
            "tokens": {"input": 20, "output": 2, "cache": {"read": 0, "write": 0}},
            "model": {"providerID": "p", "id": "m2"},
        },
    ])
    assert usage["totalCost"] == 0.3
    assert usage["sessionsWithUsage"] == 2
    assert usage["totalTokens"]["input"] == 30
    assert len(usage["byModel"]) == 2
    assert usage["topSessions"][0]["id"] == "a"


def test_top_sessions_sorted_by_tokens() -> None:
    usage = aggregate_session_usage([
        {"id": "cheap", "title": "Cheap", "cost": 0.01, "tokens": {"input": 100, "output": 1, "cache": {"read": 0, "write": 0}}},
        {"id": "heavy", "title": "Heavy", "cost": 0.5, "tokens": {"input": 100000, "output": 1, "cache": {"read": 0, "write": 0}}},
    ])
    assert usage["topSessions"][0]["id"] == "heavy"


def test_aggregate_usage_periods_filters_by_updated_at() -> None:
    now = datetime(2026, 6, 12, 15, 0, 0).timestamp()
    sessions = [
        {
            "id": "old",
            "title": "Old",
            "cost": 0.5,
            "updated": datetime(2026, 6, 1, 12, 0, 0).timestamp(),
            "tokens": {"input": 50, "output": 1, "cache": {"read": 0, "write": 0}},
        },
        {
            "id": "today",
            "title": "Today",
            "cost": 0.2,
            "updated": datetime(2026, 6, 12, 10, 0, 0).timestamp(),
            "tokens": {"input": 20, "output": 2, "cache": {"read": 0, "write": 0}},
        },
    ]
    usage = aggregate_usage_periods(sessions, now=now)
    assert usage["periods"]["total"]["totalCost"] == 0.7
    assert usage["periods"]["today"]["totalCost"] == 0.2
    assert usage["periods"]["today"]["sessionCount"] == 1
    assert usage["periods"]["month"]["sessionsWithUsage"] == 2
