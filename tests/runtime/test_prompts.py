from __future__ import annotations

from openloom.runtime.prompts import (
    MIN_CHECK_INTERVAL_SECONDS,
    TaskSpec,
    count_global_acceptance_checked,
    detect_progress,
    extract_abort_from_markdown,
    extract_session_id_from_markdown,
    normalize_check_interval_seconds,
    parse_task_spec,
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


# --- markdown frontmatter: session / abort ---


def test_markdown_parses_session_id_frontmatter() -> None:
    md = """# x
session: ses_abc
workspace: /w

## goal
Do it.
"""
    assert extract_session_id_from_markdown(md) == "ses_abc"


def test_markdown_parses_session_id_underscore_alias() -> None:
    md = "# x\n\nsession_id: ses_xyz\nworkspace: /w\n"
    assert extract_session_id_from_markdown(md) == "ses_xyz"


def test_markdown_abort_default_false() -> None:
    md = """# x
workspace: /w

## goal
Do it.
"""
    spec = parse_task_spec(md, "markdown")
    assert spec.abort_session is False
    assert extract_abort_from_markdown(md) is False


def test_markdown_abort_true_sets_flag_on_spec() -> None:
    md = """# Resume after the hang

session: ses_abc
abort: true
workspace: /w

## goal
Pick up from where you stopped.
"""
    spec = parse_task_spec(md, "markdown")
    assert spec.abort_session is True
    assert extract_abort_from_markdown(md) is True


def test_markdown_abort_session_alias_works() -> None:
    md = "# x\n\nabort session: yes\nworkspace: /w\n"
    spec = parse_task_spec(md, "markdown")
    assert spec.abort_session is True


def test_markdown_abort_false_value() -> None:
    md = "# x\n\nabort: false\nworkspace: /w\n"
    spec = parse_task_spec(md, "markdown")
    assert spec.abort_session is False


def test_taskspec_to_dict_round_trips_abort_flag() -> None:
    spec = TaskSpec(
        name="x", workspace="/w", goal="g", abort_session=True,
    )
    data = spec.to_dict()
    assert data["abort_session"] is True
    assert TaskSpec.from_dict(data).abort_session is True


def test_taskspec_from_dict_default_abort_false() -> None:
    """Pre-existing YAML specs that have no abort_session key must
    deserialize with the flag off — a regression here would silently
    change the behaviour of every existing openloom.yaml in the wild."""
    spec = TaskSpec.from_dict({"name": "x", "workspace": "/w", "goal": "g"})
    assert spec.abort_session is False
