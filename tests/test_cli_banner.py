"""CLI banner / verbose-flag tests."""

from __future__ import annotations

import argparse

import pytest

from openloom import __version__
from openloom.cli import _is_verbose, _print_banner
from openloom.config import Settings


def _args(verbose: bool = False) -> argparse.Namespace:
    return argparse.Namespace(command="serve", verbose=verbose)


def test_banner_prints_version_and_core_facts(
    capsys: pytest.CaptureFixture, tmp_path: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "openloom.sqlite3"  # type: ignore[operator]
    monkeypatch.setenv("OPENLOOM_DATABASE", str(db))
    settings = Settings.from_env()

    _print_banner(_args(), settings)
    out = capsys.readouterr().out

    assert __version__ in out
    assert "serve" in out
    assert settings.opencode_url in out
    assert str(db) in out
    # python version in the first line
    import platform as _p
    assert _p.python_version() in out


def test_banner_verbose_includes_env_and_notify(
    capsys: pytest.CaptureFixture, tmp_path: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "openloom.sqlite3"  # type: ignore[operator]
    monkeypatch.setenv("OPENLOOM_DATABASE", str(db))
    monkeypatch.setenv("OPENLOOM_NOTIFY_WEBHOOK_URLS", "https://hook.example/x")
    monkeypatch.setenv("OPENLOOM_INBOX_DIR", str(tmp_path / "inbox"))  # type: ignore[operator]

    settings = Settings.from_env()
    _print_banner(_args(verbose=True), settings)
    out = capsys.readouterr().out

    # Inbox block visible
    assert "inbox" in out
    assert "task.md" in out  # default filename
    # Notify block visible with the URL
    assert "https://hook.example/x" in out
    # Env block visible
    assert "OPENLOOM_NOTIFY_WEBHOOK_URLS" in out
    assert "https://hook.example/x" in out


def test_banner_non_verbose_omits_env_block(
    capsys: pytest.CaptureFixture, tmp_path: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "openloom.sqlite3"  # type: ignore[operator]
    monkeypatch.setenv("OPENLOOM_DATABASE", str(db))
    settings = Settings.from_env()
    _print_banner(_args(verbose=False), settings)
    out = capsys.readouterr().out
    # Not verbose → no env dump
    assert "OPENLOOM_DATABASE" not in out
    assert "env:" not in out


def test_is_verbose_arg_flag() -> None:
    assert _is_verbose(argparse.Namespace(verbose=True)) is True
    assert _is_verbose(argparse.Namespace(verbose=False)) is False


def test_is_verbose_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for truthy in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("OPENLOOM_VERBOSE", truthy)
        assert _is_verbose(argparse.Namespace(verbose=False)) is True
    monkeypatch.setenv("OPENLOOM_VERBOSE", "0")
    assert _is_verbose(argparse.Namespace(verbose=False)) is False
    monkeypatch.delenv("OPENLOOM_VERBOSE", raising=False)
    assert _is_verbose(argparse.Namespace(verbose=False)) is False


def test_banner_verbose_lists_no_inbox_when_unset(
    capsys: pytest.CaptureFixture, tmp_path: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "openloom.sqlite3"  # type: ignore[operator]
    monkeypatch.setenv("OPENLOOM_DATABASE", str(db))
    monkeypatch.delenv("OPENLOOM_INBOX_DIR", raising=False)
    settings = Settings.from_env()
    _print_banner(_args(verbose=True), settings)
    out = capsys.readouterr().out
    # No inbox block when OPENLOOM_INBOX_DIR is unset
    assert "inbox" not in out.split("env:")[0]  # nothing in the top facts
