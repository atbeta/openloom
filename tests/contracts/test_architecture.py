"""Architecture contract tests — catch violations of PLAN §11 / AGENTS.md.

These run as plain `pytest` (no FastAPI / network) so they execute in CI
even before optional extras are installed.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "openloom"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _walk(rel: str) -> list[Path]:
    root = SRC / rel
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if p.is_file()]


# The set of core/ modules that must not import from levels/,
# runtime/, or server/. The list is the source of truth —
# `test_architecture.py` would otherwise drift when modules are
# added or removed.
@pytest.mark.parametrize(
    "module",
    [
        "openloom.core",
        "openloom.core.events",
        "openloom.core.harness",
        "openloom.core.notify_config",
        "openloom.core.protocols",
        "openloom.core.registry",
        "openloom.core.sink",
        "openloom.core.store",
        "openloom.core.webhook_types",
    ],
)
def test_core_does_not_import_levels_runtime_or_server(module: str) -> None:
    """PLAN §11.1: core/ must not import levels/, runtime/, server/."""
    mod = importlib.import_module(module)
    mod_file = Path(mod.__file__ or "")
    assert mod_file.is_relative_to(SRC / "core"), f"{module} is not under core/"

    forbidden = {
        "openloom.levels",
        "openloom.levels.server",
        "openloom.runtime",
        "openloom.server",
    }
    bad = [name for name in mod.__dict__ if name in forbidden]
    assert not bad, f"{module} leaks references to: {bad}"


def test_core_total_lines_report() -> None:
    """Informational: core/ line count (soft guideline, not a CI gate)."""
    total = 0
    for p in _walk("core"):
        if p.name == "__init__.py":
            continue
        total += sum(1 for _ in p.open(encoding="utf-8"))
    assert total > 0, "core/ should contain Python modules"


def test_no_try_import_in_init_files() -> None:
    """PLAN §11.4: __init__.py must not contain `try: import` (cold detection pattern)."""
    pattern = re.compile(r"^\s*try\s*:\s*$", re.MULTILINE)
    for p in SRC.rglob("__init__.py"):
        text = _read(p)
        assert not pattern.search(text), f"{p.relative_to(SRC)} uses try: import"


def test_harness_does_not_directly_call_sink() -> None:
    """PLAN §11.5: harness must not call Sink.on_event directly (only emit events)."""
    text = _read(SRC / "core" / "harness.py")
    assert "self.sink" not in text, "harness must not hold a sink reference"
    assert "self._sinks" not in text
    assert ".on_event(" not in text, "harness must not invoke sink.on_event directly"


def test_server_does_not_import_levels() -> None:
    """Server/ must be level-agnostic — it consumes core/ + runtime/ only."""
    for p in _walk("server"):
        text = _read(p)
        assert "openloom.levels" not in text, (
            f"{p.relative_to(SRC)} imports openloom.levels"
        )
