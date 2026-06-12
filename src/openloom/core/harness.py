from __future__ import annotations

import time
import uuid
from typing import Any

from .checker import CheckResult
from .events import Event, EventBus, EventType


class HarnessRunner:
    def __init__(self, opencode: Any, bus: EventBus, store: Any, checker: Any, prompts: Any, status: Any) -> None:
        self.opencode = opencode
        self.bus = bus
        self.store = store
        self.checker = checker
        self.prompts = prompts
        self.status = status

    def add_task(self, spec: Any, task_id: str | None = None) -> str:
        if not hasattr(spec, "to_dict"):
            spec = self.prompts.TaskSpec.from_dict(spec)

        task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        now = time.time()
        task = {
            "id": task_id,
            "name": spec.name,
            "spec": spec.to_dict(),
            "workspace": spec.workspace,
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
            "check_log": [],
        }
        result = self.store.create_task(task)
        sv = result.get("store_version", 0)

        self.bus.emit(Event(type=EventType.TASK_CREATED, task_id=task_id, store_version=sv,
                            data={"spec": spec.to_dict(), "workspace": spec.workspace}))
        return task_id

    async def tick(self) -> None:
        due = self.store.list_due_tasks()
        for task in due:
            try:
                await self._check_task(task)
            except Exception as exc:  # noqa: BLE001
                sv = self.store.update_task(task["id"], status="failed", error=str(exc))
                self.store.append_check_log(
                    task["id"], status="failed", summary="Harness check failed", detail=str(exc),
                )
                self.bus.emit(Event(
                    type=EventType.TASK_FAILED,
                    task_id=task["id"],
                    store_version=sv,
                    data={"error": str(exc), "summary": "Harness check failed"},
                ))

    async def _check_task(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        spec_data = task["spec"]
        now = time.time()

        if task["status"] == "pending":
            await self._start_task(task)
            return

        if task["status"] in ("paused", "completed", "failed", "archived"):
            return

        session_id = task.get("active_session_id")
        if not session_id:
            await self._start_task(task)
            return

        spec = self.prompts.TaskSpec.from_dict(spec_data)

        live_raw = await self.opencode.session_status()
        live = live_raw.get(session_id)
        status_text = self.status.normalize_session_status(live) or ""

        messages = await self.opencode.messages(session_id, limit=50)
        is_busy = self.prompts.messages_indicate_busy(messages)

        result: CheckResult = self.checker.check(messages, spec_data)

        current_step = int(task.get("current_step") or 0)
        if result.step_done > 0:
            current_step = max(current_step, min(result.step_done, len(spec.steps) - 1))

        completed_steps = list(task.get("completed_steps") or [])
        all_steps_reported = len(spec.steps) > 0 and result.step_done >= len(spec.steps)
        has_final = bool(spec.acceptance)
        task_finished = self.prompts.task_is_finished(
            task_complete=result.task_complete,
            step_done=result.step_done,
            acceptance_checked=result.acceptance_checked,
            step_count=len(spec.steps),
            acceptance_count=len(spec.acceptance),
        )

        if is_busy:
            status = "running"
            summary = "Agent is busy"
        elif status_text == self.status.RETRY or "wait" in status_text or "permission" in status_text:
            status = "waiting"
            summary = "Waiting for permission or input"
        elif task_finished:
            status = "completed"
            summary = "Agent reported TASK COMPLETE" if result.task_complete else "All steps appear complete"
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
            elif has_final and all_steps_reported and result.acceptance_checked < len(spec.acceptance):
                nudge = self.prompts.build_final_checks_nudge(spec)
                summary = "Waiting on final checks"
            elif (
                result.step_done > 0
                and result.step_done not in completed_steps
                and current_step < len(spec.steps) - 1
            ):
                next_index = min(result.step_done, len(spec.steps) - 1)
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
                    progress={
                        "task_complete": result.task_complete,
                        "step_done": result.step_done,
                        "acceptance_checked": result.acceptance_checked,
                        "acceptance_total": result.acceptance_total,
                        "acceptance_progress": result.acceptance_progress,
                    },
                    completed_steps=completed_steps,
                )
                summary = "Periodic check — requested status confirmation"

            if nudge:
                await self.opencode.send_prompt_async(
                    session_id=session_id,
                    prompt=nudge,
                    agent=agent_name,
                )

        if result.step_done > 0 and result.step_done not in completed_steps:
            completed_steps.append(result.step_done)

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

        sv = self.store.update_task(
            task_id,
            status=status,
            current_step=current_step,
            completed_steps=completed_steps,
            idle_checks=idle_checks,
            progress=step_progress,
            last_check_at=now,
            next_check_at=next_check,
            last_summary=summary,
        )
        self.store.append_check_log(task_id, status=status, summary=summary, detail=status_text)

        self.bus.emit(Event(
            type=EventType.TASK_UPDATED,
            task_id=task_id,
            store_version=sv,
            data={
                "status": status,
                "current_step": current_step,
                "progress": step_progress,
                "summary": summary,
            },
        ))

        if status == "completed":
            self.bus.emit(Event(type=EventType.TASK_COMPLETED, task_id=task_id, store_version=sv,
                                data={"summary": summary, "progress": step_progress}))
        elif status == "failed":
            self.bus.emit(Event(type=EventType.TASK_FAILED, task_id=task_id, store_version=sv, data={"summary": summary}))

    async def _start_task(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        spec = self.prompts.TaskSpec.from_dict(task["spec"])
        session_id = task.get("active_session_id")
        attached = bool(session_id)

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
                spec = self.prompts.TaskSpec.from_dict({**spec.to_dict(), "workspace": directory})
        else:
            session = await self.opencode.create_session(cwd=spec.workspace, title=spec.name)
            session_id = session["id"]

        raw_interval = task.get("check_interval_seconds")
        interval = int(raw_interval if raw_interval is not None else spec.check_interval_seconds)
        interval = max(int(self.prompts.MIN_CHECK_INTERVAL_SECONDS), interval)
        structured = bool(spec.steps or spec.acceptance or spec.step_acceptance)
        if structured:
            prompt = self.prompts.build_bootstrap_prompt(
                spec, current_step=int(task.get("current_step") or 0),
            )
        else:
            prompt = spec.initial_prompt or spec.goal
        if not prompt:
            raise ValueError("Task requires a prompt or structured plan")

        await self.opencode.send_prompt_async(
            session_id=session_id,
            prompt=prompt,
            agent=None if spec.agent == "opencode" else spec.agent,
        )

        session_ids = list(task.get("session_ids") or [])
        if session_id not in session_ids:
            session_ids.append(session_id)

        now = time.time()
        summary = (
            f"Harness attached to session {session_id}"
            if attached
            else "Harness started and bootstrap prompt sent"
        )
        sv = self.store.update_task(
            task_id,
            status="running",
            active_session_id=session_id,
            session_ids=session_ids,
            spec=spec.to_dict(),
            check_interval_seconds=interval,
            last_check_at=now,
            next_check_at=now + interval,
            error=None,
        )
        self.store.append_check_log(task_id, status="running", summary=summary, detail=f"session={session_id}")
        self.bus.emit(Event(type=EventType.TASK_STARTED, task_id=task_id, store_version=sv,
                            data={"session_id": session_id, "summary": summary}))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_task(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.store.list_tasks()
