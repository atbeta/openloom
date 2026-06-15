"""Regression tests for ``openloom serve`` --host / --port overrides.

Bug: passing ``--host`` or ``--port`` to ``openloom serve`` used to rebuild
the entire ``Settings`` object with only a handful of fields, silently
dropping ``notify`` sinks and ``inbox`` that came from environment
variables. The dashboard would then show "Webhook: off" / "Inbox: off"
even when the env vars were set correctly.
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
    monkeypatch.setenv(
        "OPENLOOM_NOTIFY_FILE_DIRS", str(tmp_path / "notify"),
    )
    monkeypatch.setenv("OPENLOOM_INBOX_DIR", str(tmp_path / "inbox"))
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
    assert len(settings.notify.files) == 1
    assert settings.inbox_dir == tmp_path / "inbox"

    out = _apply_serve_overrides(settings, host="0.0.0.0", port=None)

    # Bug regression: these env-derived fields must survive the override.
    assert len(out.notify.webhooks) == 2
    assert {w.url for w in out.notify.webhooks} == {
        "https://hook.example/x",
        "https://hook.example/y",
    }
    assert len(out.notify.files) == 1
    assert out.notify.files[0].directory == tmp_path / "notify"
    assert out.inbox_dir == tmp_path / "inbox"
    # And the override itself is applied.
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
    assert len(out.notify.files) == 1
    assert out.inbox_dir == tmp_path / "inbox"


def test_host_and_port_overrides_preserve_notify_sinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    settings = _env_settings(monkeypatch, tmp_path)
    out = _apply_serve_overrides(settings, host="::", port=60001)

    assert out.ui_host == "::"
    assert out.ui_port == 60001
    assert len(out.notify.webhooks) == 2
    assert len(out.notify.files) == 1
    assert out.inbox_dir == tmp_path / "inbox"
