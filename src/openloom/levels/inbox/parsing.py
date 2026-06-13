from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger("openloom.inbox.parsing")


def parse_path(path: Path, default_workspace: str) -> dict[str, Any] | None:
    """Parse a single markdown file into a spec dict, or return ``None`` on skip.

    The ``try/except`` blocks live here (not in ``__init__.py``) so the
    project's architecture contract forbidding ``try: import`` in
    initialiser files is satisfied.
    """
    from openloom.runtime.prompts import parse_task_spec

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning("inbox: cannot read %s: %s", path.name, exc)
        return None
    try:
        spec = parse_task_spec(text, "markdown")
    except Exception as exc:  # noqa: BLE001
        _logger.warning("inbox: cannot parse %s: %s", path.name, exc)
        return None
    if not spec.workspace:
        spec.workspace = default_workspace.strip()
    if not spec.workspace:
        _logger.warning(
            "inbox: %s has no workspace (and no default_workspace configured); skipping",
            path.name,
        )
        return None
    data = spec.to_dict()
    data["_inbox_path"] = str(path)
    return data
