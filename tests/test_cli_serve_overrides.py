"""Regression tests for ``openloom serve`` --host / --port overrides.

The override helper patches only ``ui_host`` / ``ui_port`` on the
``Settings`` object. Earlier versions rebuilt a fresh ``Settings``
with only a handful of fields and silently dropped
``notify.webhooks`` and the ``inbox_dir`` env var, breaking the
dashboard. The fix is one line in ``_apply_serve_overrides``; the
test is the contract that guards it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openloom.cli import _apply_serve_overrides
from openloom.config import Settings


def _env_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Settings:
    monkeypatch.setenv("OPENLOOM_DATABASE", str(tmp_path / "openloom.sqlite3"))
    monkeypatch.setenv(
        "OPENLOOM_NOTIFY_WEBHOOK_URLS", "https://hook.example/x,https://hook.example/y",
    )
    return Settings.from_env()


def test_no_overrides_returns_same_settings() -> None:
    settings = Settings(
        opencode_url="http://127.0.0.1:4096",
        opencode_username="opencode",
        opencode_password="",
        database_path=Path("/tmp/x.sqlite3"),
        ui_host="127.0.0.1",
        ui_port=55413,
    )
    out = _apply_serve_overrides(settings, host=None, port=None)
    assert out is settings


def test_host_override_preserves_notify_sinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    settings = _env_settings(monkeypatch, tmp_path)
    assert len(settings.notify.webhooks) == 2

    out = _apply_serve_overrides(settings, host="0.0.0.0", port=None)

    assert {w.url for w in out.notify.webhooks} == {
        "https://hook.example/x",
        "https://hook.example/y",
    }
    assert out.ui_host == "0.0.0.0"
    assert out.ui_port == 55413  # unchanged


def test_port_override_preserves_notify_sinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    settings = _env_settings(monkeypatch, tmp_path)
    out = _apply_serve_overrides(settings, host=None, port=60000)

    assert out.ui_host == "127.0.0.1"  # unchanged
    assert out.ui_port == 60000
    assert len(out.notify.webhooks) == 2


def test_host_and_port_overrides_preserve_notify_sinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    settings = _env_settings(monkeypatch, tmp_path)
    out = _apply_serve_overrides(settings, host="::", port=60001)

    assert out.ui_host == "::"
    assert out.ui_port == 60001
    assert len(out.notify.webhooks) == 2
