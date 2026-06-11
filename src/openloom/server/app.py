from __future__ import annotations

from typing import Any

from openloom.server.cold import require_fastapi


def create_app(harness: Any, store: Any, bus: Any, web_sink: Any):
    require_fastapi()

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from pathlib import Path

    app = FastAPI(title="OpenLoom", version="0.3.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from .routes import tasks as task_routes

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

    static_dir = Path(__file__).resolve().parent / "static"
    index_file = static_dir / "index.html"
    if index_file.exists():

        @app.get("/")
        async def index():
            return FileResponse(str(index_file))

    return app
