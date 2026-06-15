from __future__ import annotations

import time
import uuid
from typing import Any

from .events import Event, EventBus, EventType
from .protocols import (
    CheckerPort,
    CheckResultProtocol,
    OpenCodePort,
    PromptsPort,
    StatusPort,
    StorePort,
)
from .store import new_task_record


class HarnessRunner:
    def __init__(
        self,
        opencode: OpenCodePort,
        bus: EventBus,
        store: StorePort,
        checker: CheckerPort,
        prompts: PromptsPort,
        status: StatusPort,
        *,
        max_task_tokens: int | None = None,
        max_task_runtime_minutes: int | None = None,
        notify_recent_messages: int = 3,
    ) -> None:
        self.opencode: OpenCodePort = opencode
        self.bus: EventBus = bus
        self.store: StorePort = store
        self.checker: CheckerPort = checker
        self.prompts: PromptsPort = prompts
        self.status: StatusPort = status
        self.max_task_tokens = max_task_tokens
        self.max_task_runtime_minutes = max_task_runtime_minutes
        self.notify_recent_messages = max(1, int(notify_recent_messages))

    def _task_name(self, task_id: str) -> str:
        """Look up the human-readable task name. Best-effort: the
        store is the source of truth and a missing task (race
        window between delete and emit) just returns an empty
        string. The empty string is what an Event with no name
        also serialises to, so sinks are not required to special
        case it."""
        task = self.store.get_task(task_id)
        if not task:
            return ""
        return str(task.get("name") or "")

    def add_task(
        self,
        spec: Any,
        task_id: str | None = None,
        *,
        active_session_id: str | None = None,
        session_ids: list[str] | None = None,
    ) -> str:
        if not hasattr(spec, "to_dict"):
            spec = self.prompts.TaskSpec.from_dict(spec)

        # Session-bound dispatch is single-writer per session. If
        # another live task already claims this session id, we
        # auto-archive it so the new task is the only observer of
        # the session's transcript — otherwise the two tasks
        # would race on STEP DONE markers in the same conversation
        # and the older task's progress would be poisoned by the
        # newer one. The auto-archive is recorded in the new
        # task's spec as ``replaced_task_ids`` so downstream
        # consumers (UI, webhook handlers) can render a "this
        # task took over from <id>" badge if they want to.
        replaced_task_ids: list[str] = []
        if active_session_id:
            existing = self.store.list_active_tasks_for_session(active_session_id)
            for old in existing:
                old_id = old.get("id")
                if not old_id:
                    continue
                replaced_task_ids.append(old_id)
                self._auto_archive_replaced(old_id, active_session_id, task_name=str(old.get("name") or ""))

        task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        spec_dict = spec.to_dict()
        if replaced_task_ids:
            spec_dict["replaced_task_ids"] = replaced_task_ids
        task = new_task_record(
            task_id=task_id,
            name=spec.name,
            spec=spec_dict,
            workspace=spec.workspace,
            check_interval_seconds=spec.check_interval_seconds,
            active_session_id=active_session_id,
            session_ids=session_ids,
        )
        result = self.store.create_task(task)
        sv = result.get("store_version", 0)

        self.bus.emit(Event(type=EventType.TASK_CREATED, task_id=task_id, store_version=sv,
                            task_name=spec.name,
                            data={
                                "spec": spec_dict, "workspace": spec.workspace,
                                "replaced_task_ids": replaced_task_ids,
                                "active_session_id": active_session_id or None,
                            }))
        return task_id

    def _auto_archive_replaced(
        self,
        task_id: str,
        session_id: str,
        *,
        task_name: str,
    ) -> None:
        """Mark a task ``archived`` so the new dispatch is the sole
        observer of ``session_id``. Emits a TASK_UPDATED with a
        ``replaced_by`` annotation so webhook handlers can show
        a "task was taken over" link. We do not emit TASK_FAILED
        or TASK_COMPLETED because the task did not fail or finish
        — it was just superseded, which is a different lifecycle
        event in spirit. The ``replaced_by`` data field is the
        only signal; consumers that don't know about it just
        see a normal status=archived transition.
        """
        reason = f"Superseded by a new task on session {session_id[:12]}"
        try:
            sv = self.store.update_task(
                task_id,
                status="archived",
                next_check_at=None,
                last_summary=reason,
            )
        except Exception:
            return
        self.store.append_check_log(
            task_id, status="archived", summary=reason, detail=f"replaced_by_session={session_id}",
        )
        self.bus.emit(Event(
            type=EventType.TASK_UPDATED,
            task_id=task_id,
            store_version=sv,
            task_name=task_name,
            data={
                "status": "archived",
                "summary": reason,
                "replaced_by_session": session_id,
                "active_session_id": session_id,
            },
        ))

    def pause_task(self, task_id: str) -> dict[str, Any]:
        if not self.store.get_task(task_id):
            raise LookupError("Task not found")
        name = self._task_name(task_id)
        sv = self.store.update_task(task_id, status="paused", next_check_at=None)
        self.bus.emit(Event(
            type=EventType.TASK_UPDATED, task_id=task_id, store_version=sv,
            task_name=name, data={"status": "paused"},
        ))
        return {"ok": True, "taskId": task_id, "status": "paused", "store_version": sv}

    def resume_task(self, task_id: str) -> dict[str, Any]:
        if not self.store.get_task(task_id):
            raise LookupError("Task not found")
        name = self._task_name(task_id)
        sv = self.store.update_task(task_id, status="running", next_check_at=time.time())
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
        if not self.store.get_task(task_id):
            raise LookupError("Task not found")
        name = self._task_name(task_id)
        sv = self.store.update_task(
            task_id,
            status="completed",
            next_check_at=None,
            last_summary=summary,
            progress=1.0,
        )
        self.store.append_check_log(task_id, status="completed", summary=summary)
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
        if not self.store.get_task(task_id):
            raise LookupError("Task not found")
        name = self._task_name(task_id)
        sv = self.store.update_task(
            task_id,
            status="archived",
            next_check_at=None,
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

    def _auto_pause(self, task_id: str, reason: str) -> None:
        name = self._task_name(task_id)
        sv = self.store.update_task(task_id, status="paused", next_check_at=None, last_summary=reason)
        self.store.append_check_log(task_id, status="paused", summary=reason)
        self.bus.emit(Event(
            type=EventType.TASK_UPDATED, task_id=task_id, store_version=sv,
            task_name=name, data={"status": "paused", "summary": reason},
        ))

    async def _budget_limit_reason(self, task: dict[str, Any], spec: Any) -> str | None:
        max_runtime = spec.max_runtime_minutes or self.max_task_runtime_minutes
        if max_runtime:
            created = float(task.get("created_at") or time.time())
            elapsed_min = (time.time() - created) / 60.0
            if elapsed_min >= max_runtime:
                return f"Runtime limit reached ({max_runtime} min)"
        max_tokens = spec.max_tokens or self.max_task_tokens
        if not max_tokens:
            return None
        session_ids = task.get("session_ids") or []
        if not session_ids:
            return None
        try:
            sessions = await self.opencode.list_sessions()
        except Exception:
            return None
        by_id = {
            str(item.get("id")): item
            for item in sessions
            if isinstance(item, dict) and item.get("id")
        }
        total = 0
        for session_id in session_ids:
            session = by_id.get(str(session_id))
            if session is not None:
                total += self.prompts.session_total_tokens(session)
        if total >= max_tokens:
            return f"Token limit reached ({total:,} / {max_tokens:,})"
        return None

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
                    task_name=str(task.get("name") or ""),
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
        # Specs created by add_task with a session that already
        # had a live task carry a list of the task ids they
        # superseded. Surface it on every emit so consumers can
        # render a "took over from <id>" affordance.
        replaced_task_ids = list(spec_data.get("replaced_task_ids") or [])

        budget_reason = await self._budget_limit_reason(task, spec)
        if budget_reason:
            self._auto_pause(task_id, budget_reason)
            return

        live_raw = await self.opencode.session_status()
        live = live_raw.get(session_id)
        status_text = self.status.normalize_session_status(live) or ""

        messages = await self.opencode.messages(session_id, limit=50)
        is_busy = self.prompts.messages_indicate_busy(messages)
        recent_activity = self.prompts.recent_assistant_activity(
            messages, n=self.notify_recent_messages,
        )
        # Stable identifier of the latest assistant message at this
        # point in the session's history. Used to invalidate the
        # nudge dedup when the agent has produced new content since
        # the last nudge — otherwise the same prompt would be sent
        # repeatedly even though the agent had already moved on.
        assistant_signature = self.prompts.assistant_message_signature(messages)

        result: CheckResultProtocol = self.checker.check(messages, spec_data)

        current_step = int(task.get("current_step") or 0)
        progress_step = int(result.step_done or 0)
        if progress_step > 0:
            current_step = max(current_step, min(progress_step, len(spec.steps) - 1))

        completed_steps = list(task.get("completed_steps") or [])
        max_done_at_start = max(completed_steps) if completed_steps else 0
        made_progress = (
            is_busy
            or result.task_complete
            or progress_step > max_done_at_start
            or progress_step > max(current_step, max_done_at_start)
        )
        task_finished = self.prompts.task_is_finished(
            task_complete=result.task_complete,
            step_done=result.step_done,
            acceptance_checked=result.acceptance_checked,
            step_count=len(spec.steps),
            acceptance_count=len(spec.acceptance),
        )

        nudge: str | None = None
        nudge_detail = status_text

        perm = await self.opencode.resolve_session_permissions(session_id, spec.auto_accept_permissions)
        if perm:
            status = perm["status"]
            summary = perm["summary"]
        elif is_busy:
            status = "running"
            summary = "Agent is busy"
        elif status_text == self.status.RETRY or "wait" in status_text or "permission" in status_text:
            status = "waiting"
            summary = "Waiting for permission or input"
        elif task_finished:
            status = "completed"
            summary = "Agent reported TASK COMPLETE" if result.task_complete else "All steps appear complete"
        elif not task_finished:
            # Note: gating on ``not task_finished`` here is what stops the
            # harness from re-prompting an agent that has already reported
            # TASK COMPLETE. Without this guard the next ``else`` branch
            # below would call ``build_periodic_check_prompt`` and ask the
            # agent to confirm completion again, even though we just
            # marked the task ``completed`` above. The agent would reply
            # with TASK COMPLETE, the next tick would re-detect it, and
            # we'd keep nudging in a tight loop.
            status = "running"
            summary = "Session idle — verifying progress"
            agent_name: str | None = None if spec.agent == "opencode" else spec.agent

            idle_checks = int(task.get("idle_checks") or 0)
            max_idle = int(self.prompts.MAX_IDLE_NUDGES)
            if (
                max_idle > 0
                and not is_busy
                and not made_progress
                and idle_checks >= max_idle
            ):
                self._auto_pause(
                    task_id,
                    f"Auto-paused after {idle_checks} idle checks without progress",
                )
                return

            if self.prompts.needs_asking_reply(messages):
                step_name = spec.steps[min(current_step, len(spec.steps) - 1)] if spec.steps else None
                nudge = self.prompts.auto_decide_reply(step_name=step_name)
                summary = "Auto-decided: proceed without confirmation"
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

            if nudge and self.prompts.already_nudged(
                task, nudge, current_signature=assistant_signature,
            ):
                nudge = None
                summary = "Skipped duplicate nudge"

            if nudge:
                await self.opencode.send_prompt_async(
                    session_id=session_id,
                    prompt=nudge,
                    agent=agent_name,
                )
                nudge_detail = (
                    f"nudge:{self.prompts.nudge_fingerprint(nudge)}"
                    f"|{status_text}:{assistant_signature}"
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
        if made_progress or is_busy:
            idle_checks = 0
        elif status == "running":
            idle_checks += 1

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
        log_detail = nudge_detail if nudge_detail.startswith("nudge:") else status_text
        self.store.append_check_log(task_id, status=status, summary=summary, detail=log_detail)

        self.bus.emit(Event(
            type=EventType.TASK_UPDATED,
            task_id=task_id,
            store_version=sv,
            task_name=str(task.get("name") or ""),
            data={
                "status": status,
                "current_step": current_step,
                "progress": step_progress,
                "summary": summary,
                "recent_activity": recent_activity,
                "active_session_id": session_id,
                "replaced_task_ids": replaced_task_ids,
            },
        ))

        if status == "completed":
            self.bus.emit(Event(
                type=EventType.TASK_COMPLETED, task_id=task_id, store_version=sv,
                task_name=str(task.get("name") or ""),
                data={
                    "summary": summary,
                    "progress": step_progress,
                    "recent_activity": recent_activity,
                    "active_session_id": session_id,
                    "replaced_task_ids": replaced_task_ids,
                },
            ))
        elif status == "failed":
            self.bus.emit(Event(
                type=EventType.TASK_FAILED, task_id=task_id, store_version=sv,
                task_name=str(task.get("name") or ""),
                data={
                    "summary": summary,
                    "recent_activity": recent_activity,
                    "active_session_id": session_id,
                    "replaced_task_ids": replaced_task_ids,
                },
            ))

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

        # Session-bound inbox dispatch can request that the harness
        # abort any in-flight agent loop on the target session
        # *before* sending the new prompt — this is the "I'm home,
        # take over the stuck agent" path. The flag is opt-in
        # (default False) so regular watch dispatches never abort.
        if spec.abort_session and hasattr(self.opencode, "abort_session"):
            try:
                await self.opencode.abort_session(session_id)
            except Exception as exc:  # noqa: BLE001
                # Treat abort failure as a soft signal: log via the
                # event bus and continue with the send so the user
                # still gets their message into the queue.
                self.bus.emit(Event(
                    type=EventType.LOG_LINE,
                    task_id=task_id,
                    task_name=str(task.get("name") or ""),
                    data={"error": str(exc), "summary": "session abort failed"},
                ))

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
                            task_name=str(task.get("name") or ""),
                            data={"session_id": session_id, "summary": summary}))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_task(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.store.list_tasks()
