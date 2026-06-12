from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from openloom import __version__
from openloom.server.cold import require_fastapi


def create_app(
    harness: Any,
    store: Any,
    bus: Any,
    web_sink: Any,
    *,
    client: Any = None,
    monitor: Any = None,
    recent: Any = None,
    settings: Any = None,
    parse_spec: Any = None,
    pick_folder: Any = None,
):
    require_fastapi()

    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    # Make Request / Query / etc. resolvable from module globals so future
    # annotation strings inside nested route handlers can be evaluated.
    globals()["Request"] = Request
    globals()["Query"] = Query
    globals()["HTTPException"] = HTTPException

    app = FastAPI(title="OpenLoom", version=__version__)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from .routes import sessions as session_routes
    from .routes import tasks as task_routes

    @app.get("/api/tasks")
    async def list_tasks():
        return task_routes.tasks_list(store)

    @app.post("/api/tasks/plan")
    async def generate_task_plan(req: Request):
        if client is None or settings is None:
            raise HTTPException(status_code=503, detail="Plan generation requires OpenCode client")
        body = await req.json()
        intent = str(body.get("intent") or body.get("prompt") or "").strip()
        if not intent:
            raise HTTPException(status_code=400, detail="intent is required")
        session_id = body.get("sessionId")
        from pathlib import Path as _P

        from openloom.runtime.planner import generate_plan

        cwd: str
        if session_id:
            try:
                sessions = await client.list_sessions()
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"opencode: {exc}") from exc
            match = next((s for s in sessions if s.get("id") == session_id), None)
            if not match:
                raise HTTPException(status_code=404, detail="Session not found")
            directory = match.get("directory") or str(body.get("workspace") or "")
            if not directory:
                raise HTTPException(status_code=400, detail="Cannot resolve workspace for session")
            cwd = str(_P(directory).expanduser().resolve())
        else:
            workspace = str(body.get("workspace") or "").strip()
            if not workspace:
                raise HTTPException(status_code=400, detail="workspace is required")
            cwd = str(_P(workspace).expanduser().resolve())

        agent = str(body.get("agent") or "opencode")
        agent_name = None if agent == "opencode" else agent
        try:
            plan = await generate_plan(client, workspace=cwd, intent=intent, agent=agent_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"plan generation failed: {exc}") from exc
        return {"ok": True, "plan": plan.to_dict(), "workspace": cwd}

    @app.post("/api/tasks")
    async def create_task(req: Request):
        if settings is None:
            raise HTTPException(status_code=501, detail="Task creation needs settings binding")
        body = await req.json()
        session_id = body.get("sessionId")

        plan_data = body.get("plan")
        prompt_text = body.get("prompt")
        intent_text = body.get("intent")
        spec_text = body.get("spec")
        interval: int | None = None
        if "checkIntervalMinutes" in body:
            from openloom.runtime.prompts import normalize_check_interval_seconds

            interval = normalize_check_interval_seconds(minutes=int(body["checkIntervalMinutes"]))
        elif body.get("checkIntervalSeconds") is not None:
            from openloom.runtime.prompts import normalize_check_interval_seconds

            interval = normalize_check_interval_seconds(value=int(body["checkIntervalSeconds"]))

        agent = str(body.get("agent") or "opencode")
        mode = str(body.get("mode") or "normal")

        if isinstance(plan_data, dict):
            from openloom.runtime.planner import TaskPlan, task_spec_from_plan

            intent = str(intent_text or prompt_text or plan_data.get("intent") or "").strip()
            try:
                plan = TaskPlan.from_dict({**plan_data, "intent": plan_data.get("intent") or intent})
                spec = task_spec_from_plan(
                    plan,
                    str(body.get("workspace") or ""),
                    check_interval_seconds=interval,
                    agent=agent,
                    mode=mode,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        elif prompt_text is not None:
            from openloom.runtime.prompts import task_spec_from_prompt

            prompt = str(prompt_text).strip()
            if not prompt:
                raise HTTPException(status_code=400, detail="prompt is required")
            try:
                spec = task_spec_from_prompt(
                    prompt,
                    str(body.get("workspace") or ""),
                    check_interval_seconds=interval,
                    agent=agent,
                    mode=mode,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        elif spec_text:
            if parse_spec is None:
                raise HTTPException(status_code=501, detail="YAML spec needs parse_spec binding")
            try:
                spec = parse_spec(str(spec_text), body.get("format", "yaml"))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"Invalid task spec: {exc}") from exc
        else:
            raise HTTPException(status_code=400, detail="plan, prompt, or spec is required")

        auto_accept = bool(body["autoAcceptPermissions"]) if "autoAcceptPermissions" in body else True
        from openloom.runtime.prompts import TaskSpec

        spec = TaskSpec.from_dict({**spec.to_dict(), "auto_accept_permissions": auto_accept})

        from pathlib import Path as _P

        cwd: str | None = None
        if session_id:
            if client is None:
                raise HTTPException(status_code=503, detail="OpenCode client unavailable")
            try:
                sessions = await client.list_sessions()
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"opencode: {exc}") from exc
            match = next((s for s in sessions if s.get("id") == session_id), None)
            if not match:
                raise HTTPException(status_code=404, detail="Session not found")
            directory = match.get("directory") or spec.workspace
            if not directory:
                raise HTTPException(status_code=400, detail="Cannot resolve workspace for session")
            cwd = str(_P(directory).expanduser().resolve())
        else:
            if not spec.workspace:
                raise HTTPException(status_code=400, detail="workspace is required")
            cwd = str(_P(spec.workspace).expanduser().resolve())

        spec.workspace = cwd

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        now = time.time()
        task = {
            "id": task_id,
            "name": spec.name,
            "spec": spec.to_dict(),
            "workspace": cwd,
            "status": "pending",
            "check_interval_seconds": spec.check_interval_seconds,
            "next_check_at": now,
            "active_session_id": session_id,
            "session_ids": [session_id] if session_id else [],
        }
        store.create_task(task)
        if recent is not None and not session_id:
            recent.record(cwd)
        return {
            "ok": True,
            "taskId": task_id,
            "status": "pending",
            "name": spec.name,
            "watch": True,
            "sessionId": session_id,
            "autoAcceptPermissions": spec.auto_accept_permissions,
            "steps": len(spec.steps),
            "acceptance": len(spec.acceptance),
        }

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        return task_routes.task_detail(store, task_id)

    @app.post("/api/tasks/{task_id}/pause")
    async def pause(task_id: str):
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        store.update_task(task_id, status="paused", next_check_at=None)
        return {"ok": True, "taskId": task_id, "status": "paused"}

    @app.post("/api/tasks/{task_id}/resume")
    async def resume(task_id: str):
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        store.update_task(task_id, status="running", next_check_at=time.time())
        return {"ok": True, "taskId": task_id, "status": "running"}

    @app.post("/api/tasks/{task_id}/complete")
    async def complete(task_id: str):
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        store.update_task(task_id, status="completed", next_check_at=None,
                          last_summary="Marked complete manually", progress=1.0)
        store.append_check_log(task_id, status="completed", summary="Marked complete manually")
        return {"ok": True, "taskId": task_id, "status": "completed"}

    @app.get("/api/events")
    async def sse_events():
        return await task_routes.event_stream(store, web_sink)

    @app.post("/api/tasks/{task_id}/archive")
    async def archive(task_id: str):
        return task_routes.archive_task(store, task_id)

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str):
        result = task_routes.delete_task(store, task_id)
        if not result.get("ok"):
            if result.get("error") == "not found":
                raise HTTPException(status_code=404, detail="Task not found")
            raise HTTPException(status_code=400, detail=result.get("error", "delete failed"))
        return result

    @app.get("/api/state")
    async def state():
        if client is None or monitor is None or settings is None:
            raise HTTPException(status_code=501, detail="state requires client+monitor+settings")
        return await task_routes.full_state(client, store, monitor, recent, settings)

    @app.get("/api/recent-workspaces")
    async def list_recent():
        if recent is None:
            return {"workspaces": []}
        return {"workspaces": recent.list_workspaces()}

    @app.delete("/api/recent-workspaces")
    async def remove_recent(path: str = Query(...)):
        if recent is None:
            return {"ok": True, "removed": False}
        removed = recent.remove(path)
        return {"ok": True, "removed": removed, "path": path}

    @app.get("/api/browse")
    async def browse(path: str | None = Query(default=None)):
        if settings is None:
            raise HTTPException(status_code=501, detail="browse needs settings binding")
        from pathlib import Path as _P
        if path:
            root_path = _P(path).expanduser().resolve()
        elif recent is not None:
            existing = recent.list_workspaces(limit=1)
            root_path = _P(existing[0]).expanduser().resolve() if existing else _P.home().resolve()
        else:
            root_path = _P.home().resolve()
        if not root_path.exists() or not root_path.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        children = []
        for child in sorted(root_path.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir() and not child.name.startswith("."):
                children.append({"name": child.name, "path": str(child)})
        parent = root_path.parent
        return {"path": str(root_path),
                "parent": str(parent) if parent != root_path else None,
                "children": children[:200]}

    @app.post("/api/pick-folder")
    async def pick_folder_endpoint():
        if pick_folder is None:
            return {"ok": False, "unsupported": True}
        import asyncio
        picked = await asyncio.to_thread(pick_folder)
        if not picked:
            return {"ok": False, "cancelled": True}
        if recent is not None:
            recent.record(picked)
        return {"ok": True, "path": picked}

    if client and monitor:
        @app.get("/api/sessions")
        async def list_sessions():
            return session_routes.session_list(monitor)

        @app.get("/api/sessions/{session_id}/messages")
        async def get_messages(session_id: str):
            return await session_routes.session_messages(client, session_id)

        @app.get("/api/sessions/{session_id}/diff")
        async def get_diff(session_id: str):
            return await session_routes.session_diff(client, session_id)

        @app.get("/api/permissions")
        async def list_permissions(sessionId: str | None = Query(default=None)):
            return await session_routes.list_permissions(client, sessionId)

        @app.post("/api/sessions/{session_id}/permissions/{permission_id}")
        async def respond_permission(session_id: str, permission_id: str, req: Request):
            body = await req.json()
            response = str(body.get("response") or "once")
            if response not in {"once", "always", "reject"}:
                raise HTTPException(status_code=400, detail="response must be once, always, or reject")
            directory = body.get("directory")
            try:
                return await session_routes.respond_permission(
                    client,
                    session_id,
                    permission_id,
                    response,
                    directory=str(directory) if directory else None,
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"opencode: {exc}") from exc

        @app.post("/api/sessions/{session_id}/archive")
        async def archive_session(session_id: str):
            try:
                await client.set_archived(session_id, int(time.time() * 1000))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"opencode: {exc}")
            return {"ok": True, "sessionId": session_id, "archived": True}

        @app.delete("/api/sessions/{session_id}/archive")
        async def unarchive_session(session_id: str):
            try:
                await client.set_archived(session_id, 0)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"opencode: {exc}")
            return {"ok": True, "sessionId": session_id, "archived": False}

        @app.post("/api/sessions/{session_id}/delete")
        async def hard_delete_session(session_id: str):
            ok = False
            try:
                ok = await client.delete_session(session_id)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"opencode: {exc}")
            if not ok:
                raise HTTPException(status_code=404, detail="Session not found or already deleted")
            return {"ok": True, "sessionId": session_id, "deleted": True}

    static_dir = Path(__file__).resolve().parent / "static"
    app_dir = static_dir / "app"
    if app_dir.exists() and (app_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=app_dir / "assets"), name="assets")

        @app.middleware("http")
        async def no_cache_assets(request: Request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/assets/"):
                response.headers["Cache-Control"] = "no-store"
            return response

        @app.get("/{path:path}")
        async def spa_fallback(path: str = ""):
            index_file = app_dir / "index.html"
            if index_file.exists():
                return FileResponse(str(index_file))
            return {"message": "OpenLoom API"}

    return app
