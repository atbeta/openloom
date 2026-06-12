from __future__ import annotations

from pathlib import Path
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


async def list_permissions(client: Any, session_id: str | None = None) -> dict[str, Any]:
    try:
        permissions = await client.list_pending_permissions(session_id)
    except Exception as e:  # noqa: BLE001
        return {"permissions": [], "error": str(e)}
    return {"permissions": permissions}


async def respond_permission(
    client: Any,
    session_id: str,
    permission_id: str,
    response: str,
    *,
    directory: str | None = None,
) -> dict[str, Any]:
    await client.respond_permission(
        session_id,
        permission_id,
        response,
        directory=directory,
    )
    return {"ok": True, "sessionId": session_id, "permissionId": permission_id, "response": response}


def _title_from_prompt(prompt: str) -> str:
    first = " ".join(prompt.strip().split())
    if not first:
        return "Untitled"
    return first[:72] + ("…" if len(first) > 72 else "")


async def dispatch_prompt(
    client: Any,
    request: dict[str, Any],
    *,
    recent: Any = None,
) -> dict[str, Any]:
    """One-shot prompt dispatch — OpenCode API only, no harness task."""
    prompt = str(request.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt is required"}

    agent = request.get("agent")
    agent_name = None if agent in (None, "opencode") else str(agent)

    target = request.get("target") or {}
    session_id: str | None = None
    cwd: str | None = None

    if isinstance(target, dict) and target.get("type") == "workspace":
        raw = target.get("cwd") or request.get("cwd")
        if not raw:
            return {"ok": False, "error": "workspace cwd is required"}
        cwd = str(Path(str(raw)).expanduser().resolve())
        title = _title_from_prompt(prompt)
        session = await client.create_session(cwd=cwd, title=title)
        session_id = session["id"]
        if recent is not None:
            recent.record(cwd)
    elif isinstance(target, dict) and target.get("type") == "session":
        session_id = target.get("sessionId")
        if not session_id:
            return {"ok": False, "error": "sessionId is required"}
    elif request.get("cwd"):
        cwd = str(Path(str(request["cwd"])).expanduser().resolve())
        session = await client.create_session(cwd=cwd, title=_title_from_prompt(prompt))
        session_id = session["id"]
        if recent is not None:
            recent.record(cwd)
    elif isinstance(target, dict) and target.get("sessionId"):
        session_id = target["sessionId"]
    else:
        return {"ok": False, "error": "target workspace or session is required"}

    await client.send_prompt_async(session_id=session_id, prompt=prompt, agent=agent_name)
    return {"ok": True, "sessionId": session_id, "title": _title_from_prompt(prompt)}
