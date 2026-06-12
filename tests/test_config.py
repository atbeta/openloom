from __future__ import annotations

from openloom.config import Settings


def test_settings_task_limits_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENLOOM_MAX_TASK_TOKENS", "500000")
    monkeypatch.setenv("OPENLOOM_MAX_TASK_RUNTIME_MINUTES", "120")
    settings = Settings.from_env()
    assert settings.max_task_tokens == 500_000
    assert settings.max_task_runtime_minutes == 120


def test_settings_task_limits_unset_by_default(monkeypatch) -> None:
    monkeypatch.delenv("OPENLOOM_MAX_TASK_TOKENS", raising=False)
    monkeypatch.delenv("OPENLOOM_MAX_TASK_RUNTIME_MINUTES", raising=False)
    settings = Settings.from_env()
    assert settings.max_task_tokens is None
    assert settings.max_task_runtime_minutes is None
