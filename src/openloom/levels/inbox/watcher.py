from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import safe_rename, sanitise_tag
from .parsing import parse_path

if TYPE_CHECKING:
    pass

_logger = logging.getLogger("openloom.inbox.watcher")

DispatchFn = Callable[[dict[str, Any]], Awaitable[str | None]]


class InboxWatcher:
    """Polls a single inbox file and dispatches it as a task.

    The watcher owns a single file path (default ``task.md``) — the
    presence of that file is the trigger, and the file is renamed to
    ``<filename>.processed-<id>`` after a successful dispatch. The
    next iteration picks up a fresh file with the same name (typically
    dropped by an external sync tool like Dropbox, OneDrive, scp, …).

    No mtime cursor, no startup scan, no ``process_existing`` flag —
    the file itself is the queue slot.
    """

    def __init__(
        self,
        directory: Path,
        dispatch: DispatchFn,
        *,
        default_workspace: str = "",
        default_session_id: str = "",
        filename: str = "task.md",
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self._directory = Path(directory)
        self._dispatch = dispatch
        self._default_workspace = default_workspace
        self._default_session_id = default_session_id
        self._filename = filename.strip() or "task.md"
        self._poll_interval_seconds = max(1.0, float(poll_interval_seconds))

    @property
    def target_path(self) -> Path:
        return self._directory / self._filename

    async def run(self) -> None:
        if not self._directory.is_dir():
            _logger.info("inbox: directory %s does not exist; watcher idle", self._directory)
            return
        _logger.info(
            "inbox: watching %s every %.0fs",
            self.target_path, self._poll_interval_seconds,
        )
        while True:
            try:
                await self.tick()
            except Exception:
                _logger.exception("inbox tick failed")
            await asyncio.sleep(self._poll_interval_seconds)

    async def tick(self) -> bool:
        """One scan; returns ``True`` if a dispatch happened this pass."""
        if not self._directory.is_dir():
            return False
        path = self.target_path
        if not path.is_file():
            return False
        payload = parse_path(path, self._default_workspace, self._default_session_id)
        if payload is None:
            self._rename_error(path, "parse-failed")
            return False
        try:
            task_id = await self._dispatch(payload)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("inbox: dispatch raised for %s: %s", path.name, exc)
            self._rename_error(path, "dispatch-raised")
            return False
        if not task_id:
            _logger.info("inbox: dispatcher declined %s (skipped)", path.name)
            # mark as consumed so we don't loop on the same file
            self._rename(path, ".skipped")
            return False
        tag = sanitise_tag(task_id[:12])
        return self._rename(path, f".processed-{tag}")

    @staticmethod
    def _rename(path: Path, suffix: str) -> bool:
        try:
            safe_rename(path, suffix)
            return True
        except OSError as exc:
            _logger.warning("inbox: rename %s -> *%s failed: %s", path.name, suffix, exc)
            return False

    @classmethod
    def _rename_error(cls, path: Path, reason: str) -> None:
        cls._rename(path, f".error-{sanitise_tag(reason)}")
