from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import safe_rename, sanitise_tag
from .parsing import parse_path

if TYPE_CHECKING:
    from . import InboxSource

_logger = logging.getLogger("openloom.inbox.watcher")

DispatchFn = Callable[[dict[str, Any]], Awaitable[str | None]]


class InboxWatcher:
    """Polls an inbox directory and dispatches each new ``.md`` file as a task.

    Files are tracked by ``(name, mtime_ns)`` so that:

    * files present at startup are ignored unless ``process_existing`` is set;
    * re-edits after dispatch do **not** re-trigger (file already renamed);
    * same-name concurrent files get unique rename targets.
    """

    def __init__(
        self,
        source: InboxSource,
        dispatch: DispatchFn,
        *,
        process_existing: bool = False,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self._source = source
        self._dispatch = dispatch
        self._process_existing = process_existing
        self._poll_interval_seconds = max(1.0, float(poll_interval_seconds))
        self._seen: set[tuple[str, int]] = set()
        if process_existing:
            self._seed_seen()

    def _seed_seen(self) -> None:
        directory = self._source.directory
        if not directory.is_dir():
            return
        for path in directory.glob("*.md"):
            if path.is_file():
                self._seen.add(_fingerprint(path))

    async def run(self) -> None:
        directory = self._source.directory
        if not directory.is_dir():
            _logger.info("inbox: directory %s does not exist; watcher idle", directory)
            return
        _logger.info("inbox: watching %s every %.0fs", directory, self._poll_interval_seconds)
        while True:
            try:
                await self.tick()
            except Exception:
                _logger.exception("inbox tick failed")
            await asyncio.sleep(self._poll_interval_seconds)

    async def tick(self) -> list[Path]:
        """One scan; returns paths that were dispatched in this pass (testable)."""
        directory = self._source.directory
        if not directory.is_dir():
            return []
        dispatched: list[Path] = []
        for path in sorted(directory.glob("*.md")):
            if not path.is_file():
                continue
            fp = _fingerprint(path)
            if fp in self._seen:
                continue
            self._seen.add(fp)
            payload = parse_path(path, self._source._default_workspace)  # noqa: SLF001
            if payload is None:
                self._rename_error(path, "parse-failed")
                continue
            try:
                task_id = await self._dispatch(payload)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("inbox: dispatch raised for %s: %s", path.name, exc)
                self._rename_error(path, "dispatch-raised")
                continue
            if not task_id:
                _logger.info("inbox: dispatcher declined %s (skipped)", path.name)
                continue
            tag = sanitise_tag(task_id[:12])
            try:
                safe_rename(path, f".processed-{tag}")
            except OSError as exc:
                _logger.warning("inbox: dispatched but rename failed for %s: %s", path.name, exc)
            else:
                dispatched.append(path)
        return dispatched

    @staticmethod
    def _rename_error(path: Path, reason: str) -> None:
        try:
            safe_rename(path, f".error-{sanitise_tag(reason)}")
        except OSError as exc:
            _logger.warning("inbox: could not rename %s after error: %s", path.name, exc)


def _fingerprint(path: Path) -> tuple[str, int]:
    stat = path.stat()
    return (path.name, stat.st_mtime_ns)
