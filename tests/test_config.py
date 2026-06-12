"""Tests for config module — including Windows path corner cases."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from openloom.config import _split_paths


def test_split_paths_single_unix():
    paths = _split_paths("/Users/beta/Projects")
    assert len(paths) == 1
    assert paths[0] == Path("/Users/beta/Projects").resolve()


def test_split_paths_multiple_unix():
    paths = _split_paths(
        f"/Users/beta/Projects{os.pathsep}/tmp/work"
    )
    assert len(paths) == 2
    assert paths[0] == Path("/Users/beta/Projects").resolve()
    assert paths[1] == Path("/tmp/work").resolve()


def test_split_paths_empty():
    assert _split_paths("") == []
    assert _split_paths("   ") == []


def test_split_paths_extra_separators():
    """Extra separators produce empty items that are skipped."""
    paths = _split_paths(f"/foo{os.pathsep}{os.pathsep}/bar")
    assert len(paths) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows pathsep = ;")
def test_split_paths_windows_single():
    """Single Windows path must not be split at the C: drive colon."""
    paths = _split_paths("C:\\Projects")
    assert len(paths) == 1
    assert "Projects" in str(paths[0])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows pathsep = ;")
def test_split_paths_windows_multiple():
    """Multiple Windows paths separated by ; (os.pathsep on Windows)."""
    paths = _split_paths("C:\\Projects;D:\\Work")
    assert len(paths) == 2


def test_split_paths_handles_tilde():
    paths = _split_paths("~/work")
    assert paths[0] == Path.home() / "work"


def test_split_paths_ignores_blanks():
    with_blanks = f"  /foo  {os.pathsep}  {os.pathsep} /bar "
    paths = _split_paths(with_blanks)
    assert len(paths) == 2
