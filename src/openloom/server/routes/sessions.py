from __future__ import annotations

from typing import Any


def session_list(monitor: Any) -> dict[str, Any]:
    return {
        "sessions": monitor.sessions,
        "byDirectory": monitor.by_directory,
        "status": monitor.status,
    }


async def session_messages(client: Any, session_id: str) -> dict[str, Any]:
    try:
        messages = await client.messages(session_id, limit=50)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    return {"messages": messages}


async def session_diff(client: Any, session_id: str) -> dict[str, Any]:
    try:
        diff = await client.diff(session_id)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    return {"diff": diff}


async def dispatch_prompt(
    client: Any, request: dict[str, Any],
    *, recent: Any = None, settings: Any = None,
) -> dict[str, Any]:
    target = request.get("target", {})
    prompt = request.get("prompt", "")
    agent = request.get("agent")

    if not prompt:
        return {"ok": False, "error": "prompt is required"}

    from pathlib import Path as _P

    cwd = request.get("cwd")
    if cwd:
        resolved = str(_P(cwd).expanduser().resolve())
        if settings is not None and not settings.is_allowed_workspace(resolved):
            return {"ok": False, "error": "Workspace not allowed"}
        session = await client.create_session(cwd=resolved, title=prompt[:72])
        session_id = session["id"]
        if recent is not None:
            recent.record(resolved)
    elif isinstance(target, dict) and target.get("sessionId"):
        session_id = target["sessionId"]
    else:
        return {"ok": False, "error": "target sessionId or cwd required"}

    await client.send_prompt_async(session_id=session_id, prompt=prompt, agent=agent)
    return {"ok": True, "sessionId": session_id}
