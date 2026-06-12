from __future__ import annotations

from typing import Any

IDLE = "idle"
BUSY = "busy"
RETRY = "retry"


def extract_status_type(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip().lower()
        return text or None
    if isinstance(value, dict):
        for key in ("type", "status", "state"):
            raw = value.get(key)
            if raw:
                return str(raw).strip().lower()
    return None


def normalize_session_status(value: Any, *, default: str | None = IDLE) -> str | None:
    status_type = extract_status_type(value)
    if not status_type:
        return default
    if status_type in {BUSY, "running", "streaming", "working"}:
        return BUSY
    if status_type in {RETRY, "waiting", "permission"}:
        return RETRY
    if status_type in {IDLE, "available", "complete", "completed", "stopped", "aborted"}:
        return IDLE
    return status_type


def session_parent_id(session: dict[str, Any]) -> str | None:
    for key in ("parentID", "parentId", "parent_id", "parent"):
        value = session.get(key)
        if value:
            text = str(value).strip()
            if text:
                return text
    return None


def is_subagent_session(session: dict[str, Any]) -> bool:
    return session_parent_id(session) is not None


def is_archived_session(session: dict[str, Any]) -> bool:
    # OpenCode 1.16.2 returns the archived timestamp inside the time
    # block as time.archived (epoch ms). Earlier OpenLoom code looked at
    # the top-level "archived" key, which the server never populates,
    # so the archived count was always 0.
    time_block = session.get("time")
    if not isinstance(time_block, dict):
        return False
    return (time_block.get("archived") or 0) > 0


def is_visible_session(session: dict[str, Any]) -> bool:
    if not session.get("id"):
        return False
    return not is_subagent_session(session) and not is_archived_session(session)


def session_updated_at(session: dict[str, Any]) -> float:
    value = session.get("updated")
    if isinstance(value, (int, float)) and value > 0:
        return float(value) / 1000 if value > 1e12 else float(value)
    time_block = session.get("time")
    if isinstance(time_block, dict):
        for key in ("updated", "created"):
            raw = time_block.get(key)
            if isinstance(raw, (int, float)) and raw > 0:
                return float(raw) / 1000 if raw > 1e12 else float(raw)
    for key in ("updated_at", "last_check_at", "created", "at"):
        raw = session.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw) / 1000 if raw > 1e12 else float(raw)
    return 0.0
