from __future__ import annotations

import time
import uuid
from typing import Any

from .events import Event, EventBus, EventType


class HarnessRunner:
    def __init__(
        self,
        opencode: Any,
        bus: EventBus,
        prompts: Any,
        status: Any,
        allowed_workspace: Any = None,
    ) -> None:
        self.opencode = opencode
        self.bus = bus
        self.prompts = prompts
        self.status = status
        self.allowed_workspace = allowed_workspace or (lambda _: True)
        self._tasks: dict[str, dict[str, Any]] = {}

    def add_task(self, spec: Any, task_id: str | None = None) -> str:
        if not hasattr(spec, "to_dict"):
            spec = self.prompts.TaskSpec.from_dict(spec)

        task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        now = time.time()
        task: dict[str, Any] = {
            "id": task_id,
            "spec": spec.to_dict(),
            "status": "pending",
            "current_step": 0,
            "completed_steps": [],
            "idle_checks": 0,
            "progress": 0.0,
            "check_interval_seconds": spec.check_interval_seconds,
            "last_check_at": None,
            "next_check_at": now,
            "active_session_id": None,
            "session_ids": [],
            "last_summary": None,
            "error": None,
        }
        self._tasks[task_id] = task
        self.bus.emit(Event(
            type=EventType.TASK_CREATED,
            task_id=task_id,
            data={"spec": spec.to_dict(), "workspace": spec.workspace},
        ))
        return task_id

    async def tick(self) -> None:
        now = time.time()
        due = [
            t for t in self._tasks.values()
            if t["status"] in ("pending", "running", "waiting")
            and (t["next_check_at"] is None or t["next_check_at"] <= now)
        ]
        for task in due:
            try:
                await self._check_task(task, now=now)
            except Exception as exc:  # noqa: BLE001
                self.bus.emit(Event(
                    type=EventType.TASK_FAILED,
                    task_id=task["id"],
                    data={"error": str(exc), "summary": "Harness check failed"},
                ))
                task["status"] = "failed"
                task["error"] = str(exc)

    async def _check_task(self, task: dict[str, Any], *, now: float) -> None:
        task_id = task["id"]
        spec_data = task["spec"]

        if task["status"] == "pending":
            await self._start_task(task)
            return

        if task["status"] in ("paused", "completed", "failed", "archived"):
            return

        session_id = task.get("active_session_id")
        if not session_id:
            await self._start_task(task)
            return

        live_raw = await self.opencode.session_status()
        live = live_raw.get(session_id)

        spec = self.prompts.TaskSpec.from_dict(spec_data)

        status_text = self.status.normalize_session_status(live) or ""
        messages = await self.opencode.messages(session_id, limit=50)
        progress = self.prompts.detect_progress_from_messages(messages, spec)
        is_busy = self.prompts.messages_indicate_busy(messages)

        current_step = int(task.get("current_step") or 0)
        if progress["step_done"] > 0:
            current_step = max(current_step, min(progress["step_done"], len(spec.steps) - 1))

        completed_steps = list(task.get("completed_steps") or [])
        all_steps_reported = len(spec.steps) > 0 and progress["step_done"] >= len(spec.steps)
        task_finished = (
            progress["task_complete"]
            or (spec.acceptance and progress["acceptance_checked"] >= len(spec.acceptance))
            or all_steps_reported
        )

        if is_busy:
            status = "running"
            summary = "Agent is busy"
        elif status_text == self.status.RETRY or "wait" in status_text or "permission" in status_text:
            status = "waiting"
            summary = "Waiting for permission or input"
        elif task_finished:
            status = "completed"
            summary = "Agent reported TASK COMPLETE" if progress["task_complete"] else "All steps appear complete"
        else:
            status = "running"
            summary = "Session idle — verifying progress"
            agent_name: str | None = None if spec.agent == "opencode" else spec.agent
            nudge: str | None = None

            if self.prompts.needs_continue_reply(messages):
                step_name = spec.steps[min(current_step, len(spec.steps) - 1)]
                nudge = (
                    f"Yes — proceed autonomously with step {current_step + 1}: {step_name}. "
                    "Do not ask for confirmation between harness steps; implement directly. "
                    f"Reply with STEP DONE: {current_step + 1} when this step is finished."
                )
                summary = f"Auto-continued to step {current_step + 1}"
            elif (
                progress["step_done"] > 0
                and progress["step_done"] not in completed_steps
                and current_step < len(spec.steps) - 1
            ):
                next_index = min(progress["step_done"], len(spec.steps) - 1)
                nudge = (
                    f"Continue harness task '{spec.name}'. "
                    f"Focus on step {next_index + 1}: {spec.steps[next_index]}. "
                    "Proceed without asking for permission. "
                    "Reply with STEP DONE: <number> or TASK COMPLETE when appropriate."
                )
                current_step = next_index
                summary = f"Nudged agent toward step {next_index + 1}"
            else:
                nudge = self.prompts.build_periodic_check_prompt(
                    spec,
                    current_step=current_step,
                    progress=progress,
                    completed_steps=completed_steps,
                )
                summary = "Periodic check — requested status confirmation"

            if nudge:
                await self.opencode.send_prompt_async(
                    session_id=session_id,
                    prompt=nudge,
                    agent=agent_name,
                )

        if progress["step_done"] > 0 and progress["step_done"] not in completed_steps:
            completed_steps.append(progress["step_done"])

        total_steps = len(spec.steps)
        if status == "completed":
            step_progress = 1.0
        elif total_steps:
            step_progress = min(1.0, len(completed_steps) / total_steps)
        else:
            step_progress = 1.0 if task_finished else 0.0

        idle_checks = int(task.get("idle_checks") or 0)
        if not is_busy:
            idle_checks += 1
        else:
            idle_checks = 0

        interval = int(task.get("check_interval_seconds", 300))
        next_check = None if status in ("completed", "failed", "archived") else now + interval

        task["status"] = status
        task["current_step"] = current_step
        task["completed_steps"] = completed_steps
        task["idle_checks"] = idle_checks
        task["progress"] = step_progress
        task["last_check_at"] = now
        task["next_check_at"] = next_check
        task["last_summary"] = summary

        self.bus.emit(Event(
            type=EventType.TASK_UPDATED,
            task_id=task_id,
            data={
                "status": status,
                "current_step": current_step,
                "progress": step_progress,
                "summary": summary,
            },
        ))

        if status == "completed":
            self.bus.emit(Event(
                type=EventType.TASK_COMPLETED,
                task_id=task_id,
                data={"summary": summary, "progress": step_progress},
            ))
        elif status == "failed":
            self.bus.emit(Event(
                type=EventType.TASK_FAILED,
                task_id=task_id,
                data={"summary": summary},
            ))

    async def _start_task(self, task: dict[str, Any]) -> None:
        task_id = task["id"]

        spec = self.prompts.TaskSpec.from_dict(task["spec"])
        if not self.allowed_workspace(spec.workspace):
            raise ValueError("Workspace is outside allowed roots")

        session = await self.opencode.create_session(cwd=spec.workspace, title=spec.name)
        session_id = session["id"]
        prompt = self.prompts.build_bootstrap_prompt(spec, current_step=int(task.get("current_step") or 0))
        await self.opencode.send_prompt_async(
            session_id=session_id,
            prompt=prompt,
            agent=None if spec.agent == "opencode" else spec.agent,
        )

        session_ids = list(task.get("session_ids") or [])
        if session_id not in session_ids:
            session_ids.append(session_id)

        now = time.time()
        task["status"] = "running"
        task["active_session_id"] = session_id
        task["session_ids"] = session_ids
        task["last_check_at"] = now
        task["next_check_at"] = now + spec.check_interval_seconds
        task["error"] = None

        self.bus.emit(Event(
            type=EventType.TASK_STARTED,
            task_id=task_id,
            data={"session_id": session_id},
        ))
        self.bus.emit(Event(
            type=EventType.LOG_LINE,
            task_id=task_id,
            data={
                "status": "running",
                "summary": "Harness started and bootstrap prompt sent",
                "detail": f"session={session_id}",
            },
        ))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return sorted(self._tasks.values(), key=lambda t: t.get("next_check_at") or 0)
