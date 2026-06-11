from __future__ import annotations

import pytest

from openloom.runtime.planner import PlanStep, TaskPlan, parse_plan_response, task_spec_from_plan


def test_parse_plan_response_json() -> None:
    text = """
    {
      "name": "Fix SSE",
      "goal": "Reconnect SSE after network drop.",
      "steps": [
        {"title": "Inspect client", "acceptance": ["Logs show reconnect attempt"]},
        {"title": "Add backoff", "acceptance": ["Backoff implemented"]},
        {"title": "Add tests", "acceptance": ["Tests cover reconnect"]}
      ],
      "global_acceptance": ["pytest passes"]
    }
    """
    plan = parse_plan_response(text, intent="fix sse reconnect")
    assert plan.name == "Fix SSE"
    assert len(plan.steps) == 3
    assert plan.steps[0].acceptance == ["Logs show reconnect attempt"]
    assert plan.global_acceptance == ["pytest passes"]
    assert plan.intent == "fix sse reconnect"


def test_parse_plan_response_legacy_flat_steps() -> None:
    text = """{"name":"T","goal":"g","steps":["one","two"],"acceptance":["done"]}"""
    plan = parse_plan_response(text, intent="x")
    assert [step.title for step in plan.steps] == ["one", "two"]
    assert plan.global_acceptance == ["done"]


def test_parse_plan_response_strips_markdown_fence() -> None:
    text = """```json
{"name":"T","goal":"g","steps":[{"title":"one","acceptance":["done"]}]}
```"""
    plan = parse_plan_response(text, intent="x")
    assert plan.name == "T"


def test_parse_plan_response_requires_steps() -> None:
    with pytest.raises(ValueError, match="step"):
        parse_plan_response('{"name":"T","goal":"g","steps":[],"global_acceptance":["a"]}')


def test_task_spec_from_plan_preserves_structure() -> None:
    plan = TaskPlan(
        name="Demo",
        goal="Do the thing",
        steps=[
            PlanStep("a", ["a done"]),
            PlanStep("b", ["b done"]),
        ],
        global_acceptance=["pytest passes"],
        intent="",
    )
    spec = task_spec_from_plan(plan, "/tmp/ws", check_interval_seconds=300)
    assert spec.steps == ["a", "b"]
    assert spec.step_acceptance == [["a done"], ["b done"]]
    assert spec.acceptance == ["pytest passes"]
    assert spec.check_interval_seconds == 300
    assert spec.initial_prompt == "Do the thing"


def test_task_plan_from_dict_without_intent() -> None:
    plan = TaskPlan.from_dict({
        "steps": [{"title": "First step", "acceptance": ["done"]}],
    })
    assert plan.name == "First step"
    assert plan.goal == "First step"
    spec = task_spec_from_plan(plan, "/tmp/ws")
    assert spec.initial_prompt == "First step"
