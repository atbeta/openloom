from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from .session_status import BUSY, RETRY, normalize_session_status


class OpenCodeError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _extract_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        return response.text or f"upstream error {response.status_code}"
    if isinstance(body, dict):
        data = body.get("data") or body
        if isinstance(data, dict):
            msg = data.get("message") or data.get("error")
            if msg:
                return str(msg)
        if "message" in body:
            return str(body["message"])
    return f"upstream error {response.status_code}"


@dataclass
class OpenCodeHealth:
    ok: bool
    status_code: int | None
    message: str


class OpenCodeClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password)

    async def health(self) -> OpenCodeHealth:
        try:
            async with self._client() as client:
                response = await client.get("/")
            if response.status_code in (200, 401, 404):
                return OpenCodeHealth(True, response.status_code, "reachable")
            return OpenCodeHealth(False, response.status_code, response.text[:200])
        except Exception as exc:  # noqa: BLE001
            return OpenCodeHealth(False, None, str(exc))

    async def list_sessions(self) -> list[dict[str, Any]]:
        try:
            projects = await self._request_json("GET", "/project")
        except Exception:
            return await self._list_sessions_default()

        if not isinstance(projects, list):
            return await self._list_sessions_default()

        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for project in projects:
            if not isinstance(project, dict):
                continue
            worktree = project.get("worktree") or ""
            params: dict[str, Any] = {}
            if worktree and worktree != "/":
                params["directory"] = worktree
            params["limit"] = 500
            try:
                data = await self._request_json("GET", "/session", params=params)
            except Exception:
                continue
            for item in self._extract_sessions(data):
                sid = item.get("id")
                if sid and sid in seen:
                    continue
                if sid:
                    seen.add(sid)
                result.append(self._normalize_session(item))
        return result

    async def _list_sessions_default(self) -> list[dict[str, Any]]:
        data = await self._request_json("GET", "/session", params={"limit": 500})
        return [self._normalize_session(item) for item in self._extract_sessions(data)]

    @staticmethod
    def _extract_sessions(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            sessions = data.get("sessions") or data.get("data") or []
            if isinstance(sessions, list):
                return [item for item in sessions if isinstance(item, dict)]
        return []

    async def session_status(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            merged.update(await self._request_json("GET", "/session/status") or {})

        try:
            projects = await self._request_json("GET", "/project")
        except Exception:
            return merged

        if not isinstance(projects, list):
            return merged

        for project in projects:
            if not isinstance(project, dict):
                continue
            worktree = project.get("worktree") or ""
            params: dict[str, Any] = {}
            if worktree and worktree != "/":
                params["directory"] = worktree
            with contextlib.suppress(Exception):
                data = await self._request_json("GET", "/session/status", params=params or None)
                if isinstance(data, dict):
                    merged.update(data)

        return merged

    async def create_session(self, cwd: str, title: str | None = None) -> dict[str, Any]:
        query = urlencode({"directory": cwd})
        body: dict[str, Any] = {}
        if title:
            body["title"] = title

        try:
            data = await self._request_json("POST", f"/session?{query}", json=body)
        except httpx.HTTPStatusError:
            fallback_body = dict(body)
            fallback_body["directory"] = cwd
            data = await self._request_json("POST", "/session", json=fallback_body)

        if not isinstance(data, dict):
            raise RuntimeError("OpenCode returned an unexpected session response")
        session = data.get("session") if isinstance(data.get("session"), dict) else data
        return self._normalize_session(session)

    async def send_prompt_async(self, session_id: str, prompt: str, agent: str | None = None) -> None:
        payload: dict[str, Any] = {
            "parts": [{"type": "text", "text": prompt}],
        }
        if agent:
            payload["agent"] = agent
        try:
            await self._request("POST", f"/session/{session_id}/prompt_async", json=payload)
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (404, 405):
                raise

        await self._request(
            "POST",
            f"/session/{session_id}/message",
            json=payload,
            timeout=120,
        )

    async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        data = await self._request_json("GET", f"/session/{session_id}/message?limit={limit}")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            messages = data.get("messages") or data.get("data") or []
            if isinstance(messages, list):
                return [item for item in messages if isinstance(item, dict)]
        return []

    async def diff(self, session_id: str) -> list[dict[str, Any]]:
        data = await self._request_json("GET", f"/session/{session_id}/diff")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            diff = data.get("diff") or data.get("files") or []
            if isinstance(diff, list):
                return [item for item in diff if isinstance(item, dict)]
        return []

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._request(method, path, **kwargs)
        if not response.content:
            return {}
        return response.json()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        timeout = kwargs.pop("timeout", 20)
        async with self._client(timeout=timeout) as client:
            response = await client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise OpenCodeError(response.status_code, _extract_error_message(response))
        return response

    def _client(self, timeout: float = 20) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, auth=self.auth, timeout=timeout)

    def _normalize_session(self, session: dict[str, Any]) -> dict[str, Any]:
        session_id = session.get("id") or session.get("sessionID") or session.get("sessionId")
        title = session.get("title") or session.get("slug") or session_id or "Untitled session"
        directory = session.get("directory") or session.get("cwd") or session.get("path")
        time_block = session.get("time") or {}
        created_ms = session.get("createdAt") or time_block.get("created")
        updated_ms = session.get("updatedAt") or time_block.get("updated")
        created = created_ms / 1000 if isinstance(created_ms, (int, float)) and created_ms > 1e12 else created_ms
        updated = updated_ms / 1000 if isinstance(updated_ms, (int, float)) and updated_ms > 1e12 else updated_ms
        parent_id = (
            session.get("parentID")
            or session.get("parentId")
            or session.get("parent_id")
            or session.get("parent")
        )
        return {
            **session,
            "id": session_id,
            "title": title,
            "directory": directory,
            "parentID": parent_id,
            "created": created,
            "updated": updated,
        }
