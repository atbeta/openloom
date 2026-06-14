from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger("openloom.inbox.parsing")


def parse_path(
    path: Path,
    default_workspace: str,
    default_session_id: str = "",
) -> dict[str, Any] | None:
    """Parse a single markdown file into a spec dict, or return ``None`` on skip.

    The ``try/except`` blocks live here (not in ``__init__.py``) so the
    project's architecture contract forbidding ``try: import`` in
    initialiser files is satisfied.

    Session binding: if the markdown frontmatter contains a
    ``session: <id>`` (or ``session_id: <id>``) line, that session is
    attached to the dispatched task. Otherwise the
    ``default_session_id`` arg is used. The chosen id is stashed on
    the payload as ``_session_id`` so the dispatch caller can hand it
    to ``harness.add_task``; it is stripped from the spec dict
    because ``TaskSpec`` does not model session binding.
    """
    from openloom.runtime.prompts import (
        extract_session_id_from_markdown,
        parse_task_spec,
    )

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

    session_id = extract_session_id_from_markdown(text) or default_session_id.strip()
    data = spec.to_dict()
    data["_inbox_path"] = str(path)
    if session_id:
        data["_session_id"] = session_id
    return data
