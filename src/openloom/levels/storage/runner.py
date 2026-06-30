"""StorageRunner — poll-based inbox watcher + EventBus subscriber.

Watches a storage backend for task files, pushes them to the harness,
subscribes to the EventBus for lifecycle events, and writes status /
result files back to storage.

The runner replaces the ``openloom-connector`` standalone process.
Internally it talks to the ``EventBus`` directly instead of going
through HTTP webhooks — no listener port, no webhook URL config.

**Architecture note:** the entire poll loop runs synchronously inside
``asyncio.to_thread``, just like the original ``openloom-connector``.
Connector implementations (WeLink, corporate drives) often have
thread-affinity guarantees — keeping all connector calls on a single
background thread avoids subtle races with HTTP session pools,
non-reentrant clients, and other per-thread state.  EventBus handlers
receive events on the asyncio loop and schedule connector I/O
(upload / move / delete) back to the same thread pool.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import PurePosixPath
from typing import Any

from openloom.core.events import Event, EventType

from .base import Connector, FileEntry
from .config import StorageConfig
from .formats import (
    TASK_EXTENSIONS,
    parse_spec,
    render_result,
    render_status,
    result_suffix,
    status_suffix,
)

_logger = logging.getLogger("openloom.storage")

# ── throttle ────────────────────────────────────────────────────────────

STATUS_INTERVAL_S = 30.0   # minimum seconds between status writes for same task
POLL_RETRY_MAX = 3          # retries for connector.ls()
POLL_RETRY_DELAY_S = 2.0    # delay between ls retries
POLL_TIMEOUT_S = 30.0       # per-poll-cycle deadline


class StorageRunner:
    """Polls inbox → harness.add_task → EventBus → write-back.

    The poll loop runs synchronously inside ``asyncio.to_thread`` so
    connector calls (which may use blocking HTTP, file I/O, or
    thread-local state) stay on a single background thread — just
    like the original ``openloom-connector`` process.
    """

    def __init__(
        self,
        config: StorageConfig,
        bus: Any,   # EventBus
        harness: Any,  # HarnessRunner
    ) -> None:
        self._cfg = config
        self._bus = bus
        self._harness = harness
        self._connector: Connector = config.connector_class(**config.connector_kwargs)
        self._stopped = asyncio.Event()
        self._seen: set[str] = set()
        self._task_file: dict[str, str] = {}       # task_id → source file path
        self._last_status: dict[str, tuple[str, float]] = {}  # task_id → (status, epoch)

    def stop(self) -> None:
        self._stopped.set()

    async def run(self) -> None:
        _logger.debug(
            "storage: start — class=%s inbox=%s outbox=%s archive=%s interval=%ds prefix=%r",
            self._cfg.connector_class.__name__, self._cfg.inbox_dir,
            self._cfg.outbox_dir, self._cfg.archive_dir or "(none)",
            self._cfg.poll_interval_seconds, self._cfg.task_prefix,
        )
        _logger.info(
            "storage: polling %s every %ds, prefix=%r",
            self._cfg.inbox_dir, self._cfg.poll_interval_seconds, self._cfg.task_prefix,
        )
        _logger.info("storage: outbox=%s archive=%s",
                     self._cfg.outbox_dir, self._cfg.archive_dir or "(none)")

        self._bus.subscribe(EventType.TASK_UPDATED, self._on_task_updated)
        self._bus.subscribe(EventType.TASK_COMPLETED, self._on_task_terminal)
        self._bus.subscribe(EventType.TASK_FAILED, self._on_task_terminal)

        try:
            # Entire poll loop runs synchronously in a background thread —
            # same architecture as the original openloom-connector.
            await asyncio.to_thread(self._poll_sync)
        finally:
            self._bus.unsubscribe(EventType.TASK_UPDATED, self._on_task_updated)
            self._bus.unsubscribe(EventType.TASK_COMPLETED, self._on_task_terminal)
            self._bus.unsubscribe(EventType.TASK_FAILED, self._on_task_terminal)

    # ── synchronous poll loop (runs in asyncio.to_thread) ────────────────

    def _poll_sync(self) -> None:
        """Blocking poll loop — runs inside ``asyncio.to_thread``.

        All connector calls (download, upload, move, delete) are
        synchronous and run on this thread.  SQLite is configured
        with WAL + check_same_thread=False, so ``harness.add_task``
        is also safe to call from here.
        """
        _logger.debug(
            "storage: poll sync started, interval=%ds prefix=%r inbox=%s",
            self._cfg.poll_interval_seconds, self._cfg.task_prefix,
            self._cfg.inbox_dir,
        )
        while not self._stopped.is_set():
            t0 = time.monotonic()
            try:
                self._poll_once()
                _logger.debug("storage: poll cycle took %.1fs", time.monotonic() - t0)
            except Exception:
                _logger.warning(
                    "storage: poll failed, next cycle in %ds",
                    self._cfg.poll_interval_seconds,
                )
            self._stopped.wait(timeout=self._cfg.poll_interval_seconds)

    def _poll_once(self) -> None:
        """One synchronous poll cycle: ls inbox, dispatch new task files."""
        _logger.debug("storage: ls(%s)...", self._cfg.inbox_dir)
        entries: list[FileEntry] = []
        for attempt in range(POLL_RETRY_MAX):
            try:
                entries = self._connector.ls(self._cfg.inbox_dir)
                break
            except Exception as exc:
                _logger.warning(
                    "storage: ls failed (attempt %d/%d): %s",
                    attempt + 1, POLL_RETRY_MAX, exc,
                )
                if attempt < POLL_RETRY_MAX - 1:
                    time.sleep(POLL_RETRY_DELAY_S)

        _logger.debug("storage: ls → %d entries, %d seen", len(entries), len(self._seen))
        dispatched = 0
        for entry in entries:
            name = PurePosixPath(entry.path).name
            if entry.path in self._seen:
                _logger.debug("storage:   skip (seen) — %s", entry.path)
                continue
            if not name.startswith(self._cfg.task_prefix):
                _logger.debug("storage:   skip (prefix) — %s (prefix=%r)",
                             name, self._cfg.task_prefix)
                continue
            if ".done." in name:
                _logger.debug("storage:   skip (.done) — %s", entry.path)
                continue
            ext = PurePosixPath(entry.path).suffix.lower()
            if ext not in TASK_EXTENSIONS:
                _logger.debug("storage:   skip (ext=%s) — %s", ext, entry.path)
                continue
            dispatched += 1
            self._dispatch(entry)
        _logger.debug("storage: dispatched %d/%d entries", dispatched, len(entries))

    def _dispatch(self, entry: FileEntry) -> None:
        """Download → parse → push to harness. All synchronous."""
        _logger.info("storage: download %s...", entry.path)
        try:
            content = self._connector.download(entry.path)
        except Exception:
            _logger.exception("storage: download failed — %s", entry.path)
            return
        if content is None:
            _logger.warning("storage: download returned None — %s", entry.path)
            return
        _logger.info("storage: parsed %s (%d bytes)", entry.path, len(content))
        _logger.debug("storage: content preview: %.200r", content[:200])

        spec = parse_spec(content, entry.path)
        if spec is None:
            _logger.info("storage: skipping %s — not a valid task spec", entry.path)
            self._seen.add(entry.path)
            return
        _logger.debug("storage: spec keys=%s", list(spec))

        goal = str(spec.get("goal") or "").strip()
        if not goal:
            _logger.info("storage: skipping %s — missing goal", entry.path)
            self._seen.add(entry.path)
            return
        workspace = str(spec.get("workspace") or spec.get("cwd") or "").strip()
        session_id = str(spec.get("sessionId") or spec.get("session_id") or "").strip()
        if not workspace and not session_id:
            _logger.info("storage: skipping %s — need workspace or sessionId (got=%r)",
                         entry.path, spec)
            self._seen.add(entry.path)
            return

        _logger.debug("storage: calling add_task(goal=%r, workspace=%r, sessionId=%r)",
                      goal, workspace, session_id)
        try:
            task_id = self._harness.add_task(spec, active_session_id=session_id or None)
        except Exception:
            _logger.exception("storage: add_task failed — %s", entry.path)
            return
        if not task_id:
            _logger.warning("storage: add_task returned empty id — %s", entry.path)
            return
        self._seen.add(entry.path)
        self._task_file[task_id] = entry.path
        _logger.info("storage: task %s ← %s (workspace=%r sessionId=%r)",
                     task_id, entry.path, workspace, session_id)

    # ── EventBus handlers (on asyncio loop) ──────────────────────────────

    def _on_task_updated(self, event: Event) -> None:
        """Fire-and-forget: schedule async handler to avoid blocking EventBus."""
        asyncio.create_task(self._a_on_task_updated(event))

    async def _a_on_task_updated(self, event: Event) -> None:
        task_id = event.task_id
        if task_id not in self._task_file:
            return

        data = event.data if isinstance(event.data, dict) else {}
        status = str(data.get("status") or "running")
        enriched = {**data}
        now = time.time()
        await self._a_write_status(task_id, event.task_name, status, enriched, now)

    def _on_task_terminal(self, event: Event) -> None:
        """Fire-and-forget: schedule async handler to avoid blocking EventBus."""
        asyncio.create_task(self._a_on_task_terminal(event))

    async def _a_on_task_terminal(self, event: Event) -> None:
        task_id = event.task_id
        source_file = self._task_file.pop(task_id, None)
        if source_file is None:
            return

        data = event.data if isinstance(event.data, dict) else {}
        status = str(data.get("status") or event.type.name.lower().replace("task_", ""))
        now = time.time()

        payload = {
            "schema_version": "1.0",
            "task_id": task_id,
            "task_name": event.task_name,
            "status": status,
            "timestamp": now,
            "data": data,
        }

        # Write result file in thread.
        stem = PurePosixPath(source_file).stem
        suffix = result_suffix(source_file)
        out_name = f"{self._cfg.task_prefix}{_drop_prefix(stem, self._cfg.task_prefix)}{suffix}"
        out_path = f"{self._cfg.outbox_dir}/{out_name}"

        try:
            content = render_result(payload, source_file)
            await asyncio.to_thread(self._connector.upload, out_path, content)
            _logger.info("storage: result written → %s (%s)", out_path, status)
        except Exception:
            _logger.exception("storage: result upload failed: %s", out_path)

        await self._a_cleanup(source_file)
        self._last_status.pop(task_id, None)

    # ── status write (throttled) ─────────────────────────────────────────

    async def _a_write_status(
        self, task_id: str, task_name: str, status: str,
        data: dict[str, Any], now: float,
    ) -> None:
        source_file = self._task_file.get(task_id)
        if source_file is None:
            return

        last = self._last_status.get(task_id)
        changed = last is None or last[0] != status
        expired = last is None or (now - last[1]) >= STATUS_INTERVAL_S

        if not changed and not expired:
            return

        suffix = status_suffix(source_file)
        stem = PurePosixPath(source_file).stem
        out_name = f"{self._cfg.task_prefix}{_drop_prefix(stem, self._cfg.task_prefix)}{suffix}"
        out_path = f"{self._cfg.outbox_dir}/{out_name}"

        payload = {
            "schema_version": "1.0",
            "task_id": task_id,
            "task_name": task_name,
            "status": status,
            "timestamp": now,
            "timestamp_iso": _iso_utc(now),
            "summary": str(data.get("summary") or ""),
            "data": data,
        }

        try:
            content = render_status(payload, source_file)
            await asyncio.to_thread(self._connector.upload, out_path, content)
            self._last_status[task_id] = (status, now)
            reason = "changed" if changed else "throttle_expired"
            _logger.debug("storage: status → %s (%s, reason=%s)", out_path, status, reason)
        except Exception:
            _logger.warning("storage: status upload failed: %s", out_path)

    # ── cleanup ──────────────────────────────────────────────────────────

    async def _a_cleanup(self, source_file: str) -> None:
        """Move source to archive/done, delete status file. All IO in thread."""
        name = PurePosixPath(source_file).name
        stem = PurePosixPath(source_file).stem
        status_name = f"{stem}{status_suffix(source_file)}"
        status_path = f"{self._cfg.outbox_dir}/{status_name}"

        if self._cfg.archive_dir:
            try:
                await asyncio.to_thread(
                    self._connector.move, source_file, f"{self._cfg.archive_dir}/{name}",
                )
                _logger.debug("storage: archived → %s/%s", self._cfg.archive_dir, name)
            except Exception:
                _logger.warning("storage: archive failed for %s", source_file)
            try:
                await asyncio.to_thread(
                    self._connector.move, status_path, f"{self._cfg.archive_dir}/{status_name}",
                )
            except Exception:
                _logger.debug("storage: status cleanup skipped (may not exist): %s", status_path)
        else:
            done_name = f"{stem}.done{ext}" if (ext := PurePosixPath(source_file).suffix) else f"{stem}.done"
            done_path = f"{self._cfg.inbox_dir}/{done_name}"
            try:
                await asyncio.to_thread(self._connector.move, source_file, done_path)
                _logger.debug("storage: renamed → %s", done_path)
            except Exception:
                _logger.warning("storage: inline-done rename failed for %s", source_file)
            try:
                await asyncio.to_thread(self._connector.delete, status_path)
            except Exception:
                _logger.debug("storage: status delete skipped: %s", status_path)


# ── helpers ──────────────────────────────────────────────────────────────


def _drop_prefix(name: str, prefix: str) -> str:
    return name[len(prefix):] if name.startswith(prefix) else name


def _iso_utc(epoch: float) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
