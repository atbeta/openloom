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
import os
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

# Polling cadence. OpenLoom emits TASK_UPDATED every CHECK_INTERVAL_S
# seconds per task, asks OpenCode for the task's session status + the
# last few messages. Default 30 s is a good trade-off for remote /
# unattended use: cheap on OpenCode, fast enough that a phone watching
# the connector's status files sees progress within one polling window.
# Override with OPENLOOM_CHECK_INTERVAL_SECONDS; clamped to [1, 3600].
DEFAULT_CHECK_INTERVAL_SECONDS = 30


def _resolve_check_interval() -> int:
    raw = os.getenv("OPENLOOM_CHECK_INTERVAL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_CHECK_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CHECK_INTERVAL_SECONDS
    return max(1, min(3600, value))


CHECK_INTERVAL_SECONDS = _resolve_check_interval()


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
        idle_completes_task: bool = False,
        auto_accept_permissions: bool = True,
    ) -> None:
        self.opencode: OpenCodePort = opencode
        self.bus: EventBus = bus
        self.store: StorePort = store
        self.prompts: PromptsPort = prompts
        self.status: StatusPort = status
        self.notify_recent_messages = max(1, int(notify_recent_messages))
        self.idle_completes_task = bool(idle_completes_task)
        self.auto_accept_permissions = bool(auto_accept_permissions)

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
        #   * idle + session has produced any activity → "completed"
        #     (treat "agent went quiet" as done; this is the default.
        #     Set OPENLOOM_IDLE_COMPLETES_TASK=false to require the
        #     explicit TASK COMPLETE marker.)
        #   * idle + no recent_activity → still "running" (protect
        #     freshly-created tasks from being auto-completed before
        #     the agent has even responded)
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
            if self.auto_accept_permissions:
                # Auto-answer every pending prompt with "once" so the
                # agent can keep working without an operator clicking
                # through the dashboard. We do this *before* falling
                # through to the running/busy check below because, in
                # practice, the permission is the only thing keeping
                # the session idle — accepting it makes the next tick
                # see the agent back in flight.
                for entry in permission.get("pending") or ():
                    perm_id = str(entry.get("id") or "")
                    if not perm_id:
                        continue
                    try:
                        await self.opencode.respond_permission(
                            session_id, perm_id,
                        )
                    except Exception:
                        _logger.warning(
                            "auto-accept failed for permission %s on session %s",
                            perm_id, session_id,
                        )
        elif is_busy:
            status = "running"
            summary = "Agent is busy"
        elif live_status == self.status.RETRY:
            status = "waiting"
            summary = "OpenCode reported retry"
        else:
            # Session is idle. Look for a TASK COMPLETE marker in the
            # latest assistant turn; if found, mark the task completed.
            # Otherwise leave it as "running" so the dashboard shows the
            # user that the agent is sitting idle — UNLESS the operator
            # opted into idle_completes_task, in which case "idle" itself
            # is treated as completion. We require at least one assistant
            # message so freshly-created tasks aren't auto-completed before
            # the agent has even responded.
            last_text = (
                recent_activity[0]["text"] if recent_activity else ""
            )
            if "TASK COMPLETE" in last_text.upper():
                status = "completed"
                summary = "Agent reported TASK COMPLETE"
            elif self.idle_completes_task and recent_activity:
                status = "completed"
                summary = "Agent idle, treated as complete"
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
            prompt=self.prompts.wrap_bootstrap(spec.goal),
            directory=spec.workspace or None,
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
