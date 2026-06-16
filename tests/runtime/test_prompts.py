from __future__ import annotations

from typing import Any

from openloom.runtime.prompts import (
    recent_assistant_activity,
)

# --- recent_assistant_activity (notify payload enrichment) ---


def _assistant_message(
    text: str,
    *,
    completed: float | None = 100.0,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {"role": "assistant"}
    if completed is not None:
        info["time"] = {"created": completed - 1.0, "completed": completed}
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    if tools:
        for t in tools:
            parts.append({
                "type": "tool",
                "state": {
                    "tool": t.get("tool", "bash"),
                    "status": t.get("status", "completed"),
                    "input": t.get("input"),
                },
            })
    return {"info": info, "parts": parts}


def test_recent_activity_returns_empty_for_no_assistant() -> None:
    assert recent_assistant_activity([
        {"info": {"role": "user", "time": {"completed": 50.0}}},
    ]) == []


def test_recent_activity_returns_text_completed_at_and_tools() -> None:
    msgs = [
        _assistant_message(
            "I'll run the tests.",
            completed=100.0,
            tools=[{"tool": "bash", "status": "completed",
                    "input": {"command": "pytest -x"}}],
        ),
    ]
    [entry] = recent_assistant_activity(msgs)
    assert entry["text"] == "I'll run the tests."
    assert entry["completed_at"] == 100.0
    assert entry["tools"] == [
        {"tool": "bash", "status": "completed",
         "input_excerpt": '{"command": "pytest -x"}'},
    ]


def test_recent_activity_truncates_text_to_max_chars() -> None:
    long_text = "x" * 2_000
    msgs = [_assistant_message(long_text)]
    [entry] = recent_assistant_activity(msgs)
    assert len(entry["text"]) == 1_000  # 999 chars + ellipsis
    assert entry["text"].endswith("…")


def test_recent_activity_truncates_tool_input_to_excerpt_chars() -> None:
    long_input = "x" * 500
    msgs = [_assistant_message(
        "running",
        tools=[{"tool": "bash", "status": "completed",
                "input": {"command": long_input}}],
    )]
    [entry] = recent_assistant_activity(msgs)
    assert len(entry["tools"][0]["input_excerpt"]) == 80


def test_recent_activity_returns_last_n_messages() -> None:
    msgs = [
        _assistant_message(f"msg {i}", completed=float(i))
        for i in range(5)
    ]
    out = recent_assistant_activity(msgs, n=2)
    assert [e["text"] for e in out] == ["msg 3", "msg 4"]


def test_recent_activity_n_is_clamped_to_at_least_one() -> None:
    msgs = [_assistant_message("only one")]
    assert len(recent_assistant_activity(msgs, n=0)) == 1


def test_recent_activity_skips_tool_parts_that_are_not_dicts() -> None:
    """Tool parts may arrive in odd shapes (string, list, None)
    depending on the OpenCode version; the helper must not
    raise and must produce no tool entries for a tool part whose
    state is not a dict (we cannot summarise it meaningfully)."""
    msg = {
        "info": {"role": "assistant", "time": {"completed": 1.0}},
        "parts": [
            {"type": "tool", "state": "not-a-dict"},
        ],
    }
    [entry] = recent_assistant_activity([msg])
    assert entry["tools"] == []


def test_recent_activity_completed_at_zero_when_unknown() -> None:
    """Messages without a time.completed field still serialise
    with a stable 0.0 placeholder rather than missing the key
    — webhook handlers should not have to special-case None."""
    msg = {"info": {"role": "assistant"}, "parts": []}
    [entry] = recent_assistant_activity([msg])
    assert entry["completed_at"] == 0.0
    assert entry["text"] == ""
    assert entry["tools"] == []
