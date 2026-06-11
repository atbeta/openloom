from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .prompts import TaskSpec, _title_from_prompt, assistant_transcript


@dataclass
class PlanStep:
    title: str
    acceptance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "acceptance": self.acceptance}


@dataclass
class TaskPlan:
    name: str
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    global_acceptance: list[str] = field(default_factory=list)
    intent: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "goal": self.goal,
            "steps": [step.to_dict() for step in self.steps],
            "global_acceptance": self.global_acceptance,
            "intent": self.intent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, intent: str = "") -> TaskPlan:
        steps = _parse_plan_steps(data.get("steps") or [])
        goal = str(data.get("goal") or intent or "").strip()
        name = str(data.get("name") or "").strip()
        if not name and goal:
            name = _title_from_prompt(goal)
        if not name and steps:
            name = steps[0].title[:60]
        if not name:
            name = _title_from_prompt(intent or "Untitled task")
        if not goal:
            goal = name
        global_acceptance = [
            str(a).strip()
            for a in (data.get("global_acceptance") or data.get("acceptance") or [])
            if str(a).strip()
        ]
        return cls(
            name=name,
            goal=goal,
            steps=steps,
            global_acceptance=global_acceptance,
            intent=str(data.get("intent") or intent or "").strip(),
        )


def _parse_plan_steps(raw_steps: list[Any]) -> list[PlanStep]:
    steps: list[PlanStep] = []
    if raw_steps and isinstance(raw_steps[0], dict):
        for item in raw_steps:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                continue
            acc = [str(a).strip() for a in (item.get("acceptance") or []) if str(a).strip()]
            steps.append(PlanStep(title=title, acceptance=acc))
        return steps

    flat_acceptance: list[str] = []
    for item in raw_steps:
        text = str(item).strip()
        if text:
            steps.append(PlanStep(title=text, acceptance=[]))
    return steps


PLANNER_INSTRUCTIONS = """You are an OpenLoom task planner. Turn the user's intent into an executable plan.

Reply with ONLY a JSON object (no markdown fences, no commentary) using this schema:
{
  "name": "short task title",
  "goal": "one paragraph summary of the outcome",
  "steps": [
    {
      "title": "ordered actionable step",
      "acceptance": ["verifiable outcome for this step"]
    }
  ],
  "global_acceptance": ["optional whole-task check such as tests pass"]
}

Rules:
- 3 to 8 steps, each one concrete action for a coding agent
- Each step needs 1 to 2 acceptance criteria for that step only
- global_acceptance is optional (0 to 3 items) for cross-cutting final checks
- Do not ask questions; make reasonable assumptions
"""


def build_planner_prompt(intent: str, workspace: str) -> str:
    return (
        f"{PLANNER_INSTRUCTIONS}\n\n"
        f"Workspace: {workspace or '(unspecified)'}\n\n"
        f"User intent:\n{intent.strip()}\n"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        raise ValueError("Planner returned empty response")

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.I)
    if fence:
        raw = fence.group(1)

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Planner response did not contain JSON")
    payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Planner JSON must be an object")
    return payload


def parse_plan_response(text: str, *, intent: str = "") -> TaskPlan:
    data = _extract_json_object(text)
    plan = TaskPlan.from_dict(data, intent=intent)
    if not plan.goal:
        raise ValueError("Plan must include a goal")
    if not plan.steps:
        raise ValueError("Plan must include at least one step")
    return plan


def task_spec_from_plan(
    plan: TaskPlan | dict[str, Any],
    workspace: str,
    *,
    check_interval_seconds: int | None = None,
    agent: str = "opencode",
    mode: str = "normal",
) -> TaskSpec:
    item = plan if isinstance(plan, TaskPlan) else TaskPlan.from_dict(plan)
    if check_interval_seconds is None:
        interval = 0
    else:
        interval = max(0, int(check_interval_seconds))
    return TaskSpec(
        name=item.name,
        workspace=workspace.strip(),
        goal=item.goal,
        steps=[step.title for step in item.steps],
        step_acceptance=[list(step.acceptance) for step in item.steps],
        acceptance=list(item.global_acceptance),
        mode=mode.strip() or "normal",
        agent=agent.strip() or "opencode",
        check_interval_seconds=interval,
        initial_prompt=item.intent or item.goal or None,
    )


async def generate_plan(
    client: Any,
    *,
    workspace: str,
    intent: str,
    agent: str | None = None,
) -> TaskPlan:
    text = intent.strip()
    if not text:
        raise ValueError("intent is required")
    if not workspace.strip():
        raise ValueError("workspace is required")

    session = await client.create_session(cwd=workspace, title="OpenLoom plan")
    session_id = session["id"]
    try:
        reply = await client.complete_prompt(
            session_id,
            build_planner_prompt(text, workspace),
            agent=agent,
        )
        if not reply.strip():
            messages = await client.messages(session_id, limit=20)
            reply = assistant_transcript(messages, limit=1)
        return parse_plan_response(reply, intent=text)
    finally:
        try:
            await client.delete_session(session_id)
        except Exception:
            pass
