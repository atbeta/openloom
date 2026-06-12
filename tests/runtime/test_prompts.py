from __future__ import annotations

from openloom.runtime.prompts import (
    MIN_CHECK_INTERVAL_SECONDS,
    TaskSpec,
    count_global_acceptance_checked,
    detect_progress,
    normalize_check_interval_seconds,
    task_is_finished,
    task_spec_from_prompt,
)


def test_normalize_check_interval_defaults_to_five_minutes() -> None:
    assert normalize_check_interval_seconds() == MIN_CHECK_INTERVAL_SECONDS
    assert MIN_CHECK_INTERVAL_SECONDS == 300


def test_normalize_check_interval_clamps_below_minimum() -> None:
    assert normalize_check_interval_seconds(minutes=0) == 300
    assert normalize_check_interval_seconds(minutes=1) == 300
    assert normalize_check_interval_seconds(value=60) == 300


def test_normalize_check_interval_preserves_valid_values() -> None:
    assert normalize_check_interval_seconds(minutes=5) == 300
    assert normalize_check_interval_seconds(minutes=10) == 600
    assert normalize_check_interval_seconds(value=900) == 900


def test_task_spec_from_prompt_always_watches() -> None:
    spec = task_spec_from_prompt("do thing", "/tmp/ws")
    assert spec.check_interval_seconds == 300


def test_count_global_acceptance_ignores_step_checkboxes() -> None:
    text = """
Steps:
1. Implement
   - [x] Code written

Final checks (whole task):
- [ ] pytest passes
"""
    assert count_global_acceptance_checked(text, ["pytest passes"]) == 0


def test_count_global_acceptance_from_final_section() -> None:
    text = """
Final checks (whole task):
- [x] pytest passes
- [x] lint clean
"""
    assert count_global_acceptance_checked(text, ["pytest passes", "lint clean"]) == 2


def test_count_global_acceptance_matches_criterion_lines() -> None:
    text = "Done.\n- [x] pytest passes\nThanks."
    assert count_global_acceptance_checked(text, ["pytest passes"]) == 1


def test_detect_progress_does_not_complete_on_step_done_with_final_checks() -> None:
    spec = TaskSpec(
        name="T",
        workspace="/tmp",
        goal="g",
        steps=["a", "b", "c"],
        acceptance=["pytest passes"],
    )
    progress = detect_progress("STEP DONE: 3\n   - [x] step item done", spec)
    assert progress["step_done"] == 3
    assert progress["acceptance_checked"] == 0
    assert not task_is_finished(
        task_complete=progress["task_complete"],
        step_done=progress["step_done"],
        acceptance_checked=progress["acceptance_checked"],
        step_count=len(spec.steps),
        acceptance_count=len(spec.acceptance),
    )


def test_task_is_finished_requires_global_when_configured() -> None:
    assert not task_is_finished(
        task_complete=False,
        step_done=3,
        acceptance_checked=0,
        step_count=3,
        acceptance_count=1,
    )
    assert task_is_finished(
        task_complete=False,
        step_done=3,
        acceptance_checked=1,
        step_count=3,
        acceptance_count=1,
    )
    assert task_is_finished(
        task_complete=True,
        step_done=1,
        acceptance_checked=1,
        step_count=3,
        acceptance_count=1,
    )
    assert not task_is_finished(
        task_complete=True,
        step_done=1,
        acceptance_checked=0,
        step_count=3,
        acceptance_count=1,
    )


def test_task_is_finished_without_final_checks_allows_last_step() -> None:
    assert task_is_finished(
        task_complete=False,
        step_done=2,
        acceptance_checked=0,
        step_count=2,
        acceptance_count=0,
    )
    assert task_is_finished(
        task_complete=True,
        step_done=0,
        acceptance_checked=0,
        step_count=2,
        acceptance_count=0,
    )
