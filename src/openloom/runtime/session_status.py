from __future__ import annotations

from typing import Any

IDLE = "idle"
BUSY = "busy"
RETRY = "retry"


def normalize_session_status(value: Any, *, default: str | None = IDLE) -> str | None:
    if isinstance(value, str):
        text = value.strip().lower()
    elif isinstance(value, dict):
        text = str(value.get("type") or value.get("status") or value.get("state") or "").strip().lower()
    else:
        return default

    if not text:
        return default

    if text in {BUSY, "running", "streaming", "working"}:
        return BUSY
    if text in {RETRY, "waiting", "permission"}:
        return RETRY
    if text in {IDLE, "available", "complete", "completed", "stopped", "aborted"}:
        return IDLE
    return text
