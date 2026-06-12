from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from .session_status import BUSY, RETRY, extract_status_type


PROJECT_CACHE_TTL_SECONDS = 10.0
MAX_MESSAGES_PER_SESSION = 1000  # backfill ceiling for very long sessions


def _aggregate_message_stats(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum tokens / cost across all messages in a session.

    Tokens live at info.tokens on each message; cost at info.cost.
    OpenCode has been emitting these since 1.14.x. We sum the same
    fields that 1.16.x's session-level payload carries —
    ``input / output / reasoning / cache.{read,write}`` — and add
    the cost values. We deliberately omit a ``total`` key to match
    the 1.16.x native shape; the dashboard's telemetry layer sums
    the components itself.

    Returns ``{"tokens": {...}, "cost": float}``; either may be
    ``None`` if the input had nothing to aggregate.
    """
    totals: dict[str, float] = {
        "input": 0, "output": 0, "reasoning": 0,
        "cache.read": 0, "cache.write": 0,
    }
    cost = 0.0
    saw_any = False

    for message in messages or []:
        info = message.get("info")
        if not isinstance(info, dict):
            continue
        tokens = info.get("tokens")
        if isinstance(tokens, dict):
            for key in ("input", "output", "reasoning"):
                value = tokens.get(key)
                if isinstance(value, (int, float)):
                    totals[key] += value
            cache = tokens.get("cache")
            if isinstance(cache, dict):
                for sub in ("read", "write"):
                    value = cache.get(sub)
                    if isinstance(value, (int, float)):
                        totals[f"cache.{sub}"] += value
            saw_any = True
        cost_value = info.get("cost")
        if isinstance(cost_value, (int, float)):
            cost += cost_value
            saw_any = True

    if not saw_any:
        return {"tokens": None, "cost": None}
    return {
        "tokens": {
            "input": totals["input"],
            "output": totals["output"],
            "reasoning": totals["reasoning"],
            "cache": {
                "read": totals["cache.read"],
                "write": totals["cache.write"],
            },
        },
        "cost": cost,
    }


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


def _status_priority(value: Any) -> int:
    text = extract_status_type(value) or "idle"
    if text in {BUSY, "running", "streaming", "working"}:
        return 3
    if text in {RETRY, "waiting", "permission"}:
        return 2
    return 1


def _absorb_status_map(target: dict[str, Any], payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(inner, dict):
        return
    for session_id, value in inner.items():
        if not session_id or not isinstance(value, (dict, str)):
            continue
        if (
            session_id not in target
            or _status_priority(value) > _status_priority(target[session_id])
        ):
            target[session_id] = value


@dataclass
class OpenCodeHealth:
    ok: bool
    status_code: int | None
    message: str


class OpenCodeClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password)
        self._project_cache: list[dict[str, Any]] | None = None
        self._project_cache_at: float = 0.0
        self._project_lock = asyncio.Lock()
        # Session-stats backfill cache, keyed by session_id. The ``updated``
        # field doubles as a content-addressable signature: when the
        # session advances in OpenCode, ``updated`` moves and we know to
        # refetch. Keeps the dashboard from refetching 100 messages for
        # every visible session on every 5-15s poll.
        self._stats_cache: dict[str, tuple[int, dict[str, Any]]] = {}

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
            projects = await self._get_projects()
        except Exception:
            return await self._list_sessions_default()

        if not isinstance(projects, list) or not projects:
            return await self._list_sessions_default()

        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        targets: list[dict[str, str]] = []
        for project in projects:
            if not isinstance(project, dict):
                continue
            worktree = project.get("worktree") or ""
            params: dict[str, str] = {"limit": "500"}
            if worktree and worktree != "/":
                params["directory"] = worktree
            targets.append(params)

        async def fetch_one(params: dict[str, str]) -> list[dict[str, Any]]:
            try:
                data = await self._request_json("GET", "/session", params=params)
            except Exception:
                return []
            return [self._normalize_session(item) for item in self._extract_sessions(data)]

        per_project = await asyncio.gather(*(fetch_one(p) for p in targets))
        for items in per_project:
            for session in items:
                sid = session.get("id")
                if sid and sid in seen:
                    continue
                if sid:
                    seen.add(sid)
                result.append(session)
        await self._populate_session_stats(result)
        return result

    async def _list_sessions_default(self) -> list[dict[str, Any]]:
        data = await self._request_json("GET", "/session", params={"limit": 500})
        sessions = [self._normalize_session(item) for item in self._extract_sessions(data)]
        await self._populate_session_stats(sessions)
        return sessions

    async def _populate_session_stats(self, sessions: list[dict[str, Any]]) -> None:
        """Backfill tokens / cost from per-message payload for older
        OpenCode (1.14.x) servers that omit those fields in the session
        list response. Runs in parallel; only touches sessions that
        already lack the fields. Results are cached in
        ``self._stats_cache`` keyed by session_id and invalidated when
        the session's ``updated`` timestamp advances, so the 5-15s
        dashboard poll does not refetch messages for unchanged sessions.
        """
        targets = [
            s for s in sessions
            if s.get("id") and not s.get("tokens")
        ]

        async def fill(session: dict[str, Any]) -> None:
            sid = session["id"]
            updated = session.get("updated") or 0
            # updated is in seconds on the wire; cache key uses the same
            # value so a moving session always re-fetches.
            signature = int(updated) if isinstance(updated, (int, float)) else 0
            cached = self._stats_cache.get(sid)
            if cached is not None and cached[0] == signature:
                agg = cached[1]
            else:
                messages = await self._fetch_all_messages(sid)
                agg = _aggregate_message_stats(messages)
                if agg["tokens"] or agg["cost"] is not None:
                    self._stats_cache[sid] = (signature, agg)
            if agg["tokens"]:
                session["tokens"] = agg["tokens"]
            if agg["cost"] is not None:
                session["cost"] = agg["cost"]

        if targets:
            await asyncio.gather(*(fill(s) for s in targets))

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
        merge_lock = asyncio.Lock()

        async def absorb(payload: Any) -> None:
            async with merge_lock:
                _absorb_status_map(merged, payload)

        try:
            projects = await self._get_projects()
        except Exception:
            projects = None

        async def global_status() -> None:
            try:
                payload = await self._request_json("GET", "/session/status")
            except Exception:
                return
            await absorb(payload)

        async def per_worktree_status() -> None:
            if not isinstance(projects, list) or not projects:
                return

            async def fetch(project: dict[str, Any]) -> Any:
                worktree = project.get("worktree") or ""
                params: dict[str, Any] = {}
                if worktree and worktree != "/":
                    params["directory"] = worktree
                try:
                    return await self._request_json("GET", "/session/status", params=params or None)
                except Exception:
                    return None

            payloads = await asyncio.gather(*(fetch(p) for p in projects))
            for payload in payloads:
                if payload is not None:
                    await absorb(payload)

        await asyncio.gather(global_status(), per_worktree_status())
        return merged

    async def _get_projects(self) -> list[Any]:
        now = time.monotonic()
        if self._project_cache is not None and now - self._project_cache_at < PROJECT_CACHE_TTL_SECONDS:
            return self._project_cache
        async with self._project_lock:
            now = time.monotonic()
            if self._project_cache is not None and now - self._project_cache_at < PROJECT_CACHE_TTL_SECONDS:
                return self._project_cache
            try:
                data = await self._request_json("GET", "/project")
            except Exception:
                return self._project_cache or []
            if not isinstance(data, list):
                return self._project_cache or []
            self._project_cache = [p for p in data if isinstance(p, dict)]
            self._project_cache_at = time.monotonic()
            return self._project_cache

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

    async def complete_prompt(
        self,
        session_id: str,
        prompt: str,
        agent: str | None = None,
        *,
        timeout: float = 120,
    ) -> str:
        payload: dict[str, Any] = {"parts": [{"type": "text", "text": prompt}]}
        if agent:
            payload["agent"] = agent
        await self._request(
            "POST",
            f"/session/{session_id}/message",
            json=payload,
            timeout=timeout,
        )
        messages = await self.messages(session_id, limit=30)
        from .prompts import assistant_transcript

        return assistant_transcript(messages, limit=1)

    async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        data = await self._request_json("GET", f"/session/{session_id}/message?limit={limit}")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            messages = data.get("messages") or data.get("data") or []
            if isinstance(messages, list):
                return [item for item in messages if isinstance(item, dict)]
        return []

    async def _fetch_all_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Walk the entire message feed, paginated, up to
        MAX_MESSAGES_PER_SESSION. Used by the stats backfill — the
        single ``limit=N`` call is a footgun for long sessions
        (truncates token totals).
        """
        page_size = 100
        out: list[dict[str, Any]] = []
        cursor: int | None = 0
        while len(out) < MAX_MESSAGES_PER_SESSION:
            params: dict[str, str] = {"limit": str(page_size)}
            if cursor is not None:
                params["cursor"] = str(cursor)
            try:
                data = await self._request_json(
                    "GET", f"/session/{session_id}/message", params=params,
                )
            except Exception:
                return out
            if isinstance(data, list):
                page = [m for m in data if isinstance(m, dict)]
                out.extend(page)
                break  # list response: server returned everything
            if isinstance(data, dict):
                page = data.get("messages") or data.get("data") or []
                if not isinstance(page, list):
                    return out
                page = [m for m in page if isinstance(m, dict)]
                out.extend(page)
                # Some servers return ``next_cursor`` / ``has_more``.
                has_more = data.get("has_more") or data.get("hasMore")
                next_cursor = data.get("next_cursor") or data.get("nextCursor")
                if has_more and next_cursor is not None:
                    cursor = int(next_cursor)
                    continue
                break
            return out
        return out[:MAX_MESSAGES_PER_SESSION]

    async def diff(self, session_id: str) -> list[dict[str, Any]]:
        data = await self._request_json("GET", f"/session/{session_id}/diff")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            diff = data.get("diff") or data.get("files") or []
            if isinstance(diff, list):
                return [item for item in diff if isinstance(item, dict)]
        return []

    async def set_archived(self, session_id: str, archived: int | None) -> dict[str, Any]:
        return await self._request_json(
            "PATCH",
            f"/session/{session_id}",
            json={"time": {"archived": archived}},
        )

    async def delete_session(self, session_id: str) -> bool:
        try:
            response = await self._request("DELETE", f"/session/{session_id}")
        except Exception:
            return False
        return response.status_code == 200

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
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=self.auth,
            timeout=timeout,
            transport=httpx.AsyncHTTPTransport(trust_env=False),
        )

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
