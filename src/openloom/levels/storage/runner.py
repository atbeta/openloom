"""StorageRunner — poll-based inbox watcher + EventBus subscriber.

Watches a storage backend for task files, pushes them to the harness,
subscribes to the EventBus for lifecycle events, and writes status /
result files back to storage.

The runner replaces the ``openloom-connector`` standalone process.
Internally it talks to the ``EventBus`` directly instead of going
through HTTP webhooks — no listener port, no webhook URL config.
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
    """Polls inbox → harness.add_task → EventBus → write-back."""

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
            await self._poll_loop()
        finally:
            self._bus.unsubscribe(EventType.TASK_UPDATED, self._on_task_updated)
            self._bus.unsubscribe(EventType.TASK_COMPLETED, self._on_task_terminal)
            self._bus.unsubscribe(EventType.TASK_FAILED, self._on_task_terminal)

    # ── poll loop ────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._poll_once()
            except Exception:
                _logger.warning("storage poll failed, next cycle in %ds",
                                self._cfg.poll_interval_seconds)
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._cfg.poll_interval_seconds,
                )
            except TimeoutError:
                pass

    async def _poll_once(self) -> None:
        """One poll cycle with retry on ls()."""
        entries = await self._ls_with_retry()
        for entry in entries:
            if entry.path in self._seen:
                continue
            name = PurePosixPath(entry.path).name
            if not name.startswith(self._cfg.task_prefix):
                continue
            if ".done." in name:
                continue
            ext = PurePosixPath(entry.path).suffix.lower()
            if ext not in TASK_EXTENSIONS:
                continue
            await self._dispatch(entry)

    async def _ls_with_retry(self) -> list[FileEntry]:
        deadline = time.monotonic() + POLL_TIMEOUT_S
        last_exc: Exception | None = None
        for attempt in range(POLL_RETRY_MAX):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._connector.ls, self._cfg.inbox_dir),
                    timeout=min(POLL_TIMEOUT_S, deadline - time.monotonic()),
                )
            except TimeoutError:
                _logger.warning("storage.ls timed out (attempt %d/%d)",
                                attempt + 1, POLL_RETRY_MAX)
                last_exc = TimeoutError("ls deadline exceeded")
            except Exception as exc:
                _logger.warning("storage.ls failed (attempt %d/%d): %s",
                                attempt + 1, POLL_RETRY_MAX, exc)
                last_exc = exc
            if attempt < POLL_RETRY_MAX - 1:
                await asyncio.sleep(POLL_RETRY_DELAY_S)
        raise last_exc or RuntimeError("ls retries exhausted")

    # ── dispatch ─────────────────────────────────────────────────────────

    async def _dispatch(self, entry: FileEntry) -> None:
        """Download task file → parse → add to harness.

        Only ``connector.download`` runs in a thread (external IO).
        ``harness.add_task`` stays on the event loop — it touches the
        SQLite store which is not thread-safe.
        """
        content = await asyncio.to_thread(self._connector.download, entry.path)
        if content is None:
            return
        spec = parse_spec(content, entry.path)
        if spec is None:
            _logger.debug("storage: skipping %s — not a valid task spec", entry.path)
            self._seen.add(entry.path)
            return
        goal = str(spec.get("goal") or "").strip()
        if not goal:
            _logger.debug("storage: skipping %s — missing goal", entry.path)
            self._seen.add(entry.path)
            return
        workspace = str(spec.get("workspace") or spec.get("cwd") or "").strip()
        session_id = str(spec.get("sessionId") or spec.get("session_id") or "").strip()
        if not workspace and not session_id:
            _logger.debug("storage: skipping %s — need workspace or sessionId", entry.path)
            self._seen.add(entry.path)
            return

        # add_task touches the SQLite store — keep it on the event loop thread.
        task_id = self._harness.add_task(spec, active_session_id=session_id or None)
        if task_id:
            self._seen.add(entry.path)
            self._task_file[task_id] = entry.path
            _logger.info("storage: task %s ← %s (workspace=%r sessionId=%r)",
                         task_id, entry.path, workspace, session_id)

    # ── EventBus handlers ────────────────────────────────────────────────

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
        ext = PurePosixPath(source_file).suffix
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
            done_name = f"{stem}.done{ext}"
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
