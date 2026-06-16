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
    return {"ok": True}
