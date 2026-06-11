from __future__ import annotations

import json
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
    except Exception as e:
        return {"error": str(e)}
    return {"messages": messages}


async def session_diff(client: Any, session_id: str) -> dict[str, Any]:
    try:
        diff = await client.diff(session_id)
    except Exception as e:
        return {"error": str(e)}
    return {"diff": diff}


async def dispatch_prompt(client: Any, request: dict[str, Any]) -> dict[str, Any]:
    target = request.get("target", {})
    prompt = request.get("prompt", "")
    agent = request.get("agent")
    cwd = request.get("cwd")

    if not prompt:
        return {"ok": False, "error": "prompt is required"}

    if cwd:
        session = await client.create_session(cwd=cwd, title=prompt[:72])
        session_id = session["id"]
    elif isinstance(target, dict) and target.get("sessionId"):
        session_id = target["sessionId"]
    else:
        return {"ok": False, "error": "target sessionId or cwd required"}

    await client.send_prompt_async(session_id=session_id, prompt=prompt, agent=agent)
    return {"ok": True, "sessionId": session_id}
