from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openloom.core.registry import register_source
from openloom.core.source import TaskSource

if TYPE_CHECKING:
    from .watcher import InboxWatcher as InboxWatcher

_logger = logging.getLogger("openloom.inbox")

_MD_SUFFIX = ".md"


@register_source("inbox")
class InboxSource(TaskSource):
    """Reads every ``.md`` file under ``directory`` as a markdown task spec.

    Each file's parsed spec dict carries an extra ``_inbox_path`` so the
    watcher can rename it after dispatch. Files that fail to parse or
    miss a workspace are silently skipped with a warning — the watcher
    will rename them to ``.error-...`` on its own.
    """

    def __init__(self, directory: Path, default_workspace: str = "") -> None:
        self._directory = Path(directory)
        self._default_workspace = default_workspace.strip()

    def load(self, **kwargs: Any) -> list[dict[str, Any]]:
        if not self._directory.is_dir():
            return []
        from .parsing import parse_path

        specs: list[dict[str, Any]] = []
        for path in sorted(self._directory.glob(f"*{_MD_SUFFIX}")):
            if not path.is_file():
                continue
            spec = parse_path(path, self._default_workspace)
            if spec is not None:
                specs.append(spec)
        return specs

    def parse_path(self, path: Path) -> dict[str, Any] | None:
        """Parse a single file. Public for the watcher to use per-file."""
        from .parsing import parse_path as _parse_path

        return _parse_path(Path(path), self._default_workspace)

    @property
    def directory(self) -> Path:
        return self._directory


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
