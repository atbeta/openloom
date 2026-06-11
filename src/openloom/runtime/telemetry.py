from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .session_status import session_updated_at

UNKNOWN_MODEL = "Unknown"


def _safe_num(value: Any) -> float:
    if isinstance(value, (int, float)) and value == value:
        return float(value)
    return 0.0


def parse_session_tokens(session: dict[str, Any]) -> dict[str, float]:
    tokens = session.get("tokens") if isinstance(session.get("tokens"), dict) else {}
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return {
        "input": _safe_num(tokens.get("input")),
        "output": _safe_num(tokens.get("output")),
        "reasoning": _safe_num(tokens.get("reasoning")),
        "cacheRead": _safe_num(cache.get("read")),
        "cacheWrite": _safe_num(cache.get("write")),
    }


def session_model_ref(session: dict[str, Any]) -> tuple[str, str]:
    model = session.get("model") if isinstance(session.get("model"), dict) else {}
    provider_id = str(model.get("providerID") or "").strip()
    model_id = str(model.get("id") or model.get("modelID") or "").strip()
    return provider_id, model_id


def session_model_name(session: dict[str, Any]) -> str:
    provider_id, model_id = session_model_ref(session)
    if provider_id and model_id:
        return f"{provider_id}/{model_id}"
    if model_id:
        return model_id
    if provider_id:
        return provider_id
    agent = str(session.get("agent") or "").strip()
    if agent:
        return f"agent:{agent}"
    return UNKNOWN_MODEL


def session_has_usage(session: dict[str, Any]) -> bool:
    tokens = parse_session_tokens(session)
    return _safe_num(session.get("cost")) > 0 or sum(tokens.values()) > 0


def session_usage_row(session: dict[str, Any]) -> dict[str, Any]:
    tokens = parse_session_tokens(session)
    provider_id, model_id = session_model_ref(session)
    total_tokens = sum(tokens.values())
    return {
        "id": session.get("id"),
        "title": session.get("title") or session.get("id") or "Untitled",
        "directory": session.get("directory") or "",
        "cost": round(_safe_num(session.get("cost")), 6),
        "tokens": tokens,
        "totalTokens": int(total_tokens),
        "model": session_model_name(session),
        "providerID": provider_id,
        "modelID": model_id,
        "updatedAt": session_updated_at(session),
    }


def period_start_timestamps(now: float) -> dict[str, float | None]:
    current = datetime.fromtimestamp(now)
    start_today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    start_week = start_today - timedelta(days=start_today.weekday())
    start_month = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "today": start_today.timestamp(),
        "week": start_week.timestamp(),
        "month": start_month.timestamp(),
        "total": None,
    }


def filter_sessions_for_period(
    sessions: list[dict[str, Any]],
    since: float | None,
) -> list[dict[str, Any]]:
    if since is None:
        return [session for session in sessions if isinstance(session, dict)]
    return [
        session
        for session in sessions
        if isinstance(session, dict) and session_updated_at(session) >= since
    ]


def aggregate_session_usage(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "input": 0.0,
        "output": 0.0,
        "reasoning": 0.0,
        "cacheRead": 0.0,
        "cacheWrite": 0.0,
    }
    total_cost = 0.0
    sessions_with_usage = 0
    by_model: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    for session in sessions:
        if not isinstance(session, dict):
            continue
        row = session_usage_row(session)
        if not session_has_usage(session):
            continue
        sessions_with_usage += 1
        total_cost += row["cost"]
        for key, value in row["tokens"].items():
            totals[key] += value

        model_key = f"{row['providerID']}\0{row['modelID']}"
        bucket = by_model.get(model_key)
        if bucket is None:
            bucket = {
                "providerID": row["providerID"],
                "modelID": row["modelID"],
                "model": row["model"],
                "cost": 0.0,
                "sessionCount": 0,
                "tokens": {k: 0.0 for k in totals},
            }
            by_model[model_key] = bucket
        bucket["cost"] += row["cost"]
        bucket["sessionCount"] += 1
        for key, value in row["tokens"].items():
            bucket["tokens"][key] += value
        rows.append(row)

    total_tokens = sum(totals.values())
    cache_read = totals["cacheRead"]
    cache_write = totals["cacheWrite"]
    fresh_input = totals["input"]
    cache_efficiency = (
        round(cache_read / (cache_read + fresh_input), 4)
        if cache_read + fresh_input > 0
        else 0.0
    )

    by_model_list = sorted(
        [
            {
                **item,
                "cost": round(item["cost"], 6),
                "tokens": {k: int(v) for k, v in item["tokens"].items()},
                "totalTokens": int(sum(item["tokens"].values())),
            }
            for item in by_model.values()
        ],
        key=lambda item: (-item["cost"], -item["totalTokens"], item["model"]),
    )
    top_sessions = sorted(rows, key=lambda item: (-item["totalTokens"], -item["cost"]))[:12]

    return {
        "totalCost": round(total_cost, 6),
        "totalTokens": {k: int(v) for k, v in totals.items()},
        "tokenTotal": int(total_tokens),
        "sessionCount": len([session for session in sessions if isinstance(session, dict)]),
        "sessionsWithUsage": sessions_with_usage,
        "cacheEfficiency": cache_efficiency,
        "byModel": by_model_list,
        "topSessions": top_sessions,
    }


def aggregate_usage_periods(
    sessions: list[dict[str, Any]],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    timestamp = now if now is not None else datetime.now().timestamp()
    starts = period_start_timestamps(timestamp)
    periods = {
        key: aggregate_session_usage(filter_sessions_for_period(sessions, since))
        for key, since in starts.items()
    }
    return {"periods": periods, "now": timestamp}
