from __future__ import annotations

from openloom.runtime.prompts import (
    auto_decide_reply,
    looks_like_asking,
    needs_asking_reply,
)


def test_looks_like_asking_detects_choice_question() -> None:
    assert looks_like_asking("Should I use Redis or SQLite for this cache?")
    assert looks_like_asking("Which approach would you prefer for auth?")


def test_looks_like_asking_ignores_code_blocks() -> None:
    assert not looks_like_asking("See `https://example.com/docs` for details?")


def test_needs_asking_reply_when_unanswered() -> None:
    messages = [
        {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Should I proceed?"}]},
    ]
    assert needs_asking_reply(messages)


def test_needs_asking_reply_false_after_user_answer() -> None:
    messages = [
        {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Should I proceed?"}]},
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "Yes"}]},
    ]
    assert not needs_asking_reply(messages)


def test_auto_decide_reply_mentions_autonomy() -> None:
    text = auto_decide_reply(step_name="Add tests")
    assert "autonomously" in text.lower()
    assert "Add tests" in text
