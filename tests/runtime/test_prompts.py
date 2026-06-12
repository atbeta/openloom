from __future__ import annotations

from openloom.runtime.prompts import (
    MIN_CHECK_INTERVAL_SECONDS,
    normalize_check_interval_seconds,
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
