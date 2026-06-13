from __future__ import annotations

import logging
import re
from pathlib import Path

_logger = logging.getLogger("openloom.inbox")

__all__ = ["safe_rename", "sanitise_tag"]


def extract_title(text: str, fallback: str) -> str:
    """Best-effort title extraction for log lines (not used by the spec)."""
    for line in text.splitlines()[:5]:
        if line.startswith("# "):
            t = line[2:].strip()
            if t:
                return t
    return fallback


def safe_rename(path: Path, suffix: str) -> Path:
    """Rename ``path`` with ``suffix`` appended, never overwriting an existing file."""
    target = path.with_name(path.name + suffix)
    counter = 0
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.name}{suffix}.{counter}")
    path.rename(target)
    return target


def sanitise_tag(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "task"
