from __future__ import annotations

from pathlib import Path
from typing import Any

from openloom.server.cold import require_fastapi


def create_app(harness: Any, store: Any, bus: Any, web_sink: Any, *, client: Any = None, monitor: Any = None):
    require_fastapi()

    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="OpenLoom", version="0.4.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from .routes import tasks as task_routes
    from .routes import sessions as session_routes

    @app.get("/api/tasks")
    async def list_tasks():
        return task_routes.tasks_list(store)

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        return task_routes.task_detail(store, task_id)

    @app.get("/api/events")
    async def sse_events():
        return await task_routes.event_stream(store, web_sink)

    @app.post("/api/tasks/{task_id}/archive")
    async def archive(task_id: str):
        return task_routes.archive_task(store, task_id)

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

        @app.post("/api/dispatch")
        async def dispatch(req: Request):
            body = await req.json()
            return await session_routes.dispatch_prompt(client, body)

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
