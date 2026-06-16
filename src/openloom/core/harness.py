"""
HarnessRunner — the four-state machine that polls OpenCode for
each task's session and emits TASK_UPDATED events.

This is a deliberate simplification from 0.11. The old harness
carried a full nudge / acceptance / step-acknowledgement
protocol, a "stale-busy" detector, an auto-archive-on-takeover
mechanism, and a session-drop callback chain. All of that
existed to support ``openloom watch`` and the file-inbox
dispatch path, which 0.12 removes. The 0.12 harness is a plain
session poller: it ticks every CHECK_INTERVAL_SECONDS, asks
OpenCode for the task's session status + the last few
messages, and emits one TASK_UPDATED event with the result.

Webhook handlers that want a finer-grained "the agent just finished"
signal can call ``runtime.prompts.detect_progress`` on the
last assistant text themselves; the harness does not do that
on their behalf any more.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from .events import Event, EventBus, EventType
from .protocols import (
    OpenCodePort,
    PromptsPort,
    StatusPort,
    StorePort,
)
from .store import new_task_record

_logger = logging.getLogger("openloom.harness")

# Hardcoded — 0.12 dropped the env-var knob. 8 s is a reasonable
# trade-off between UI freshness and OpenCode server load.
CHECK_INTERVAL_SECONDS = 8


class HarnessRunner:
    def __init__(
        self,
        opencode: OpenCodePort,
        bus: EventBus,
        store: StorePort,
        prompts: PromptsPort,
        status: StatusPort,
        *,
        notify_recent_messages: int = 3,
    ) -> None:
        self.opencode: OpenCodePort = opencode
        self.bus: EventBus = bus
        self.store: StorePort = store
        self.prompts: PromptsPort = prompts
        self.status: StatusPort = status
        self.notify_recent_messages = max(1, int(notify_recent_messages))

    def _task_name(self, task_id: str) -> str:
        task = self.store.get_task(task_id)
        if not task:
            return ""
        return str(task.get("name") or "")

    # ------------------------------------------------------------------
    # Public task lifecycle
    # ------------------------------------------------------------------

    def add_task(
        self,
        spec: Any,
        task_id: str | None = None,
        *,
        active_session_id: str | None = None,
    ) -> str:
        if not hasattr(spec, "to_dict"):
            spec = self.prompts.TaskSpec.from_dict(spec)

        task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        task = new_task_record(
            task_id=task_id,
            name=spec.name,
            spec=spec.to_dict(),
            workspace=spec.workspace,
            active_session_id=active_session_id,
        )
        result = self.store.create_task(task)
        sv = result.get("store_version", 0)

        self.bus.emit(Event(
            type=EventType.TASK_CREATED, task_id=task_id, store_version=sv,
            task_name=spec.name,
            data={
                "spec": spec.to_dict(),
                "workspace": spec.workspace,
                "active_session_id": active_session_id or None,
            },
        ))
        return task_id

    def pause_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise LookupError("Task not found")
        name = str(task.get("name") or "")
        sv = self.store.update_task(task_id, status="paused", next_check_at=None)
        self.bus.emit(Event(
            type=EventType.TASK_UPDATED, task_id=task_id, store_version=sv,
            task_name=name, data={"status": "paused"},
        ))
        return {"ok": True, "taskId": task_id, "status": "paused", "store_version": sv}

    def resume_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise LookupError("Task not found")
        name = str(task.get("name") or "")
        now = time.time()
        sv = self.store.update_task(
            task_id, status="running",
            next_check_at=now + CHECK_INTERVAL_SECONDS,
        )
        self.bus.emit(Event(
            type=EventType.TASK_UPDATED, task_id=task_id, store_version=sv,
            task_name=name, data={"status": "running"},
        ))
        return {"ok": True, "taskId": task_id, "status": "running", "store_version": sv}

    def complete_task(
        self,
        task_id: str,
        *,
        summary: str = "Marked complete manually",
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise LookupError("Task not found")
        name = str(task.get("name") or "")
        sv = self.store.update_task(
            task_id, status="completed", next_check_at=None,
            last_summary=summary, progress=1.0,
        )
        self.store.append_check_log(
            task_id, status="completed", summary=summary,
        )
        self.bus.emit(Event(
            type=EventType.TASK_UPDATED, task_id=task_id, store_version=sv,
            task_name=name,
            data={"status": "completed", "summary": summary, "progress": 1.0},
        ))
        self.bus.emit(Event(
            type=EventType.TASK_COMPLETED, task_id=task_id, store_version=sv,
            task_name=name,
            data={"summary": summary, "progress": 1.0},
        ))
        return {"ok": True, "taskId": task_id, "status": "completed", "store_version": sv}

    def archive_task(
        self,
        task_id: str,
        *,
        summary: str = "Archived manually",
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise LookupError("Task not found")
        name = str(task.get("name") or "")
        sv = self.store.update_task(
            task_id, status="archived", next_check_at=None,
            last_summary=summary,
        )
        self.store.append_check_log(task_id, status="archived", summary=summary)
        self.bus.emit(Event(
            type=EventType.TASK_UPDATED, task_id=task_id, store_version=sv,
            task_name=name, data={"status": "archived", "summary": summary},
        ))
        return {"ok": True, "taskId": task_id, "status": "archived", "store_version": sv}

    def delete_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise LookupError("Task not found")
        if str(task.get("status", "")).lower() != "archived":
            raise ValueError("only archived tasks can be deleted")
        name = str(task.get("name") or "")
        sv = self.store.delete_task(task_id)
        self.bus.emit(Event(
            type=EventType.TASK_UPDATED, task_id=task_id, store_version=sv,
            task_name=name, data={"deleted": True},
        ))
        return {"ok": True, "taskId": task_id, "store_version": sv}

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_task(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.store.list_tasks()

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        due = self.store.list_due_tasks()
        for task in due:
            try:
                await self._check_task(task)
            except Exception as exc:  # noqa: BLE001
                sv = self.store.update_task(
                    task["id"], status="failed", error=str(exc),
                )
                self.store.append_check_log(
                    task["id"], status="failed",
                    summary="Harness check failed", detail=str(exc),
                )
                self.bus.emit(Event(
                    type=EventType.TASK_FAILED,
                    task_id=task["id"],
                    store_version=sv,
                    task_name=str(task.get("name") or ""),
                    data={"error": str(exc), "summary": "Harness check failed"},
                ))

    async def _check_task(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        now = time.time()

        if task["status"] == "pending":
            await self._start_task(task)
            return

        if task["status"] in ("paused", "completed", "failed", "archived"):
            return

        session_id = str(task.get("active_session_id") or "")
        if not session_id:
            # Defensive: pending should have routed to _start_task.
            return

        # Pull a snapshot of the session for the event payload.
        # Each call may fail independently; we still emit a
        # TASK_UPDATED so the dashboard can show "OpenCode is down"
        # rather than nothing.
        messages: list[dict[str, Any]] = []
        is_busy = False
        live_status: str | None = None
        try:
            live_status_map = await self.opencode.session_status()
            live_status = self.status.normalize_session_status(
                live_status_map.get(session_id),
            )
        except Exception:
            live_status = None
        try:
            messages = await self.opencode.messages(session_id, limit=20)
            is_busy = self.prompts.messages_indicate_busy(messages)
        except Exception:
            is_busy = False

        recent_activity = self.prompts.recent_assistant_activity(
            messages, n=self.notify_recent_messages,
        )

        # Decide status:
        #   * permission_waiting → "waiting"
        #   * busy → "running" (agent in flight)
        #   * idle + assistant says TASK COMPLETE → "completed"
        #   * idle + nothing → still "running" (webhook can decide)
        permission = None
        try:
            permission = await self.opencode.resolve_session_permissions(
                session_id,
            )
        except Exception:
            permission = None

        if permission is not None:
            status = permission["status"]
            summary = permission["summary"]
        elif is_busy:
            status = "running"
            summary = "Agent is busy"
        elif live_status == self.status.RETRY:
            status = "waiting"
            summary = "OpenCode reported retry"
        else:
            # Look for a TASK COMPLETE marker in the latest
            # assistant turn. If present, mark the task completed;
            # otherwise leave it as "running" so the dashboard
            # shows the user that the agent is sitting idle.
            last_text = (
                recent_activity[0]["text"] if recent_activity else ""
            )
            if "TASK COMPLETE" in last_text.upper():
                status = "completed"
                summary = "Agent reported TASK COMPLETE"
            else:
                status = "running"
                summary = "Session idle — awaiting input"

        progress = 1.0 if status == "completed" else 0.0

        next_check = (
            None
            if status in ("completed", "failed", "archived", "waiting")
            else now + CHECK_INTERVAL_SECONDS
        )
        sv = self.store.update_task(
            task_id,
            status=status,
            progress=progress,
            last_check_at=now,
            next_check_at=next_check,
            last_summary=summary,
        )
        self.store.append_check_log(
            task_id, status=status, summary=summary, detail=live_status or "",
        )

        emit_data: dict[str, Any] = {
            "status": status,
            "progress": progress,
            "summary": summary,
            "recent_activity": recent_activity,
            "active_session_id": session_id,
        }
        self.bus.emit(Event(
            type=EventType.TASK_UPDATED, task_id=task_id, store_version=sv,
            task_name=str(task.get("name") or ""),
            data=emit_data,
        ))

        if status == "completed":
            self.bus.emit(Event(
                type=EventType.TASK_COMPLETED, task_id=task_id, store_version=sv,
                task_name=str(task.get("name") or ""),
                data={**emit_data, "summary": summary, "progress": progress},
            ))

    async def _start_task(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        spec = self.prompts.TaskSpec.from_dict(task["spec"])
        session_id = str(task.get("active_session_id") or "").strip()

        if session_id:
            try:
                sessions = await self.opencode.list_sessions()
            except Exception:
                sessions = []
            match = next((s for s in sessions if s.get("id") == session_id), None)
            if not match:
                raise ValueError(f"Session {session_id} no longer exists")
            directory = match.get("directory") or spec.workspace
            if directory:
                spec = self.prompts.TaskSpec.from_dict(
                    {**spec.to_dict(), "workspace": directory},
                )
        else:
            session = await self.opencode.create_session(
                cwd=spec.workspace, title=spec.name,
            )
            session_id = session["id"]

        if not spec.goal:
            raise ValueError("Task requires a non-empty goal")

        await self.opencode.send_prompt_async(
            session_id=session_id,
            prompt=spec.goal,
        )

        now = time.time()
        summary = (
            f"Harness attached to session {session_id}"
            if str(task.get("active_session_id") or "").strip()
            else "Harness started and bootstrap prompt sent"
        )
        sv = self.store.update_task(
            task_id,
            status="running",
            active_session_id=session_id,
            session_ids=[session_id],
            spec=spec.to_dict(),
            last_check_at=now,
            next_check_at=now + CHECK_INTERVAL_SECONDS,
            error=None,
        )
        self.store.append_check_log(
            task_id, status="running", summary=summary, detail=f"session={session_id}",
        )
        self.bus.emit(Event(
            type=EventType.TASK_STARTED, task_id=task_id, store_version=sv,
            task_name=str(task.get("name") or ""),
            data={"session_id": session_id, "summary": summary},
        ))
