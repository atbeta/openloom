"""Tests for the docx timezone resolver and rendering.

The timezone for human-readable timestamps in .docx output is resolved
at render time via ``_resolve_tz()``: env > config > system local.
These tests pin that order and verify the timestamps in the docx
match the resolved zone.
"""

from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from openloom.levels.storage.formats import (
    _resolve_tz,
    render_result,
)

# ── _resolve_tz() precedence ──────────────────────────────────────────


def test_resolve_tz_default_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env, no config → None (system local fallback)."""
    monkeypatch.delenv("OPENLOOM_TIMEZONE", raising=False)
    with patch(
        "openloom.core.settings_source.find_config_file",
        return_value=None,
    ):
        assert _resolve_tz() is None


def test_resolve_tz_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env wins over config."""
    monkeypatch.setenv("OPENLOOM_TIMEZONE", "Asia/Tokyo")
    assert _resolve_tz() == ZoneInfo("Asia/Tokyo")


def test_resolve_tz_env_empty_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty env var is treated as not set."""
    monkeypatch.setenv("OPENLOOM_TIMEZONE", "")
    with patch(
        "openloom.core.settings_source.find_config_file",
        return_value=None,
    ):
        assert _resolve_tz() is None


def test_resolve_tz_env_invalid_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid zone name in env → fall through, don't crash."""
    monkeypatch.setenv("OPENLOOM_TIMEZONE", "Not/A/Real/Zone")
    with patch(
        "openloom.core.settings_source.find_config_file",
        return_value=None,
    ):
        assert _resolve_tz() is None


def test_resolve_tz_from_config(tmp_path: Path) -> None:
    """Config file's harness.timezone is read when env is unset."""
    cfg = tmp_path / "openloom.yaml"
    cfg.write_text("harness:\n  timezone: Europe/London\n", encoding="utf-8")
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENLOOM_TIMEZONE", None)
        with patch(
            "openloom.core.settings_source.find_config_file",
            return_value=cfg,
        ):
            assert _resolve_tz() == ZoneInfo("Europe/London")


def test_resolve_tz_env_wins_over_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When both are set, env wins."""
    cfg = tmp_path / "openloom.yaml"
    cfg.write_text("harness:\n  timezone: Europe/London\n", encoding="utf-8")
    monkeypatch.setenv("OPENLOOM_TIMEZONE", "Asia/Shanghai")
    with patch(
        "openloom.core.settings_source.find_config_file",
        return_value=cfg,
    ):
        assert _resolve_tz() == ZoneInfo("Asia/Shanghai")


def test_resolve_tz_config_invalid_falls_through(tmp_path: Path) -> None:
    """Bad zone in config → fall through to system local, don't crash."""
    cfg = tmp_path / "openloom.yaml"
    cfg.write_text("harness:\n  timezone: Not/A/Zone\n", encoding="utf-8")
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENLOOM_TIMEZONE", None)
        with patch(
            "openloom.core.settings_source.find_config_file",
            return_value=cfg,
        ):
            assert _resolve_tz() is None


def test_resolve_tz_missing_config_key(tmp_path: Path) -> None:
    """Config without harness.timezone key → None."""
    cfg = tmp_path / "openloom.yaml"
    cfg.write_text("ui:\n  port: 55413\n", encoding="utf-8")
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENLOOM_TIMEZONE", None)
        with patch(
            "openloom.core.settings_source.find_config_file",
            return_value=cfg,
        ):
            assert _resolve_tz() is None


# ── render_result() uses the resolved timezone ────────────────────────


def _read_docx_text(content: bytes) -> str:
    """Pull the visible text out of a docx blob for assertions."""
    from docx import Document
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs)


def test_docx_uses_resolved_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docx meta block renders the timestamp in the resolved zone.

    Pick a zone 9h ahead of UTC (Asia/Tokyo) so the difference shows
    in the formatted string regardless of where the test machine is.
    """
    monkeypatch.setenv("OPENLOOM_TIMEZONE", "Asia/Tokyo")
    # 2026-07-09 00:00:00 UTC == 2026-07-09 09:00:00 JST
    ts = datetime(2026, 7, 9, 0, 0, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    payload = {
        "schema_version": "1.0",
        "task_id": "task_test",
        "task_name": "tz test",
        "status": "completed",
        "timestamp": ts,
        "data": {},
    }
    blob = render_result(payload, "task_test.docx")
    text = _read_docx_text(blob)
    assert "时间: 2026-07-09 09:00:00" in text, (
        f"expected JST 09:00:00 in docx, got: {text!r}"
    )


def test_docx_default_is_system_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without env or config, the docx uses the platform's local time.

    Build a fixed UTC instant, render with no overrides, and assert
    the docx text matches what the platform would format. This pins
    the contract: default = "wherever openloom runs".
    """
    monkeypatch.delenv("OPENLOOM_TIMEZONE", raising=False)
    ts = datetime(2026, 7, 9, 0, 0, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    expected = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "schema_version": "1.0",
        "task_id": "task_local",
        "task_name": "local test",
        "status": "completed",
        "timestamp": ts,
        "data": {},
    }
    # Force the system-local path: no env var, no config file.
    with patch(
        "openloom.core.settings_source.find_config_file",
        return_value=None,
    ):
        blob = render_result(payload, "task_local.docx")
    text = _read_docx_text(blob)
    assert f"时间: {expected}" in text, (
        f"expected system-local {expected!r} in docx, got: {text!r}"
    )
