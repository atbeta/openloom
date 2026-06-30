"""Generate default ~/.openloom/config.yaml and connector example.

``openloom init`` writes these files; ``openloom serve`` auto-calls
``auto_init()`` on first run if no config file exists.
"""

from __future__ import annotations

import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

DEFAULT_CONFIG_YAML = """\
# OpenLoom configuration
# =======================
#
# This file is loaded from ~/.openloom/config.yaml (user-level)
# or ./openloom.yaml (project-level, higher priority).
#
# All fields are optional — leave them commented out to use defaults.
# Environment variables (OPENLOOM_* prefix) override any value below.

# ---- OpenCode connection ----
opencode:
  # url: http://127.0.0.1:4096
  # username: opencode
  # auto_start: false          # auto-start OpenCode if unreachable

# password NEVER goes here — use OPENLOOM_OPENCODE_PASSWORD env var

# ---- Web UI ----
ui:
  # host: 127.0.0.1
  # port: 55413

# ---- Database ----
# Path to the SQLite database. Relative paths resolve against CWD.
# database: .openloom/openloom.sqlite3

# ---- Harness behaviour ----
harness:
  # idle_completes_task: true     # treat agent-idle as task-done
  # auto_accept_permissions: true # auto-approve OpenCode tool prompts
  # notify_recent_messages: 3     # context lines in webhook payloads

# ---- Outbound webhooks (task completion notifications) ----
# notify:
#   webhook:
#     - url: https://your-system.com/hook
#       events: [TASK_COMPLETED, TASK_FAILED]
#       signing_secret: ""          # optional HMAC-SHA256 secret
#       max_retries: 3
#       timeout_seconds: 30
#       headers: {}

# ---- Storage connector (file-based task dispatch) ----
# Pick a connector class and configure its kwargs.
#
# Built-in: openloom.levels.storage.fs.FilesystemConnector (local disk)
# Custom:   my_connector.MyConnector (drop .py in ~/.openloom/connectors/)
#
# storage:
#   class: openloom.levels.storage.fs.FilesystemConnector
#   kwargs:                      # passed to the connector constructor
#     root: /path/to/storage     # FilesystemConnector: root directory
#   inbox: inbox                 # poll this subdirectory for task files
#   outbox: results              # task results go here (optional)
#   archive: archive             # if set, move completed tasks here
#   poll_interval_seconds: 30
"""

CONNECTOR_EXAMPLE_PY = '''\
"""Example storage connector.

Copy this file, implement the methods, then reference it in config.yaml:

    storage:
      class: my_connector.MyConnector
      kwargs:
        api_key: "..."
      inbox: inbox

For simple local-disk use, no connector code is needed:
    storage:
      class: openloom.levels.storage.fs.FilesystemConnector
      kwargs:
        root: /path/to/watch
      inbox: inbox
"""

from openloom.levels.storage.base import Connector, FileEntry


class MyConnector(Connector):
    """Connect to your own storage backend (local filesystem, S3, WebDAV, etc.).

    The runner calls your methods; you don't need to worry about
    inbox/outbox/archive directory semantics — those live in the runner.
    """

    def __init__(self, **kwargs):
        super().__init__()
        # kwargs contains extra keys from the storage config block
        # (api_key, bucket, endpoint, etc.)
        ...

    # ── required methods ────────────────────────────────────────

    def ls(self, path: str) -> list[FileEntry]:
        """List files in *path*.

        Args:
            path: Directory path to list.

        Returns:
            List of :class:`FileEntry` objects, one per file.
        """
        raise NotImplementedError("list files in the storage backend")

    def download(self, path: str) -> bytes | None:
        """Download *path* contents.

        Args:
            path: File path to download.

        Returns:
            File contents as bytes, or ``None`` if not found.
        """
        raise NotImplementedError("download file from the storage backend")

    def upload(self, path: str, content: bytes) -> None:
        """Upload *content* to *path*.

        Args:
            path: Destination file path.
            content: File contents as bytes.
        """
        raise NotImplementedError("upload file to the storage backend")

    def delete(self, path: str) -> None:
        """Delete *path*. No-op if it doesn't exist.

        Args:
            path: File path to delete.
        """
        raise NotImplementedError("delete file from the storage backend")

    # ── optional override ───────────────────────────────────────

    def move(self, source: str, dest: str) -> None:
        """Move/rename *source* to *dest*.

        Override this if your backend provides native move/rename
        (one API call instead of the default download+upload+delete).

        Args:
            source: Source file path.
            dest: Destination file path.
        """
        super().move(source, dest)
'''


def user_config_dir() -> Path:
    """Return ``~/.openloom/`` — the user-level config directory."""
    return Path.home() / ".openloom"


def connectors_dir() -> Path:
    """Return ``~/.openloom/connectors/``."""
    return user_config_dir() / "connectors"


def ensure_dirs() -> None:
    """Create ``~/.openloom/`` and ``~/.openloom/connectors/`` if they
    don't exist."""
    user_config_dir().mkdir(parents=True, exist_ok=True)
    connectors_dir().mkdir(parents=True, exist_ok=True)


def write_default_config() -> Path:
    """Write ``~/.openloom/config.yaml`` if it doesn't already exist.
    Returns the file path (whether it was created or already present)."""
    ensure_dirs()
    target = user_config_dir() / "config.yaml"
    if not target.exists():
        target.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
        _logger.info("wrote %s", target)
    return target


def write_connector_example() -> Path:
    """Write ``~/.openloom/connectors/example.py`` if it doesn't
    already exist. Returns the file path."""
    ensure_dirs()
    target = connectors_dir() / "example.py"
    if not target.exists():
        target.write_text(CONNECTOR_EXAMPLE_PY, encoding="utf-8")
        _logger.info("wrote %s", target)
    return target


def run_init() -> None:
    """Write all default files. Safe to call multiple times — existing
    files are never overwritten."""
    cfg = write_default_config()
    ex = write_connector_example()
    print(f"  config:    {cfg}")
    print(f"  example:   {ex}")
    print(f"  connectors: {connectors_dir()}/   (drop your own .py files here)")
    print()
    print("Next steps:")
    print("  1. Edit ~/.openloom/config.yaml")
    print("  2. Set OPENLOOM_OPENCODE_PASSWORD in your environment")
    print("  3. Implement your connector in ~/.openloom/connectors/")
    print("  4. Run: openloom serve")


def auto_init() -> bool:
    """Called by ``openloom serve`` on first run. Returns True if new
    files were written, False if everything already existed."""
    from openloom.core.settings_source import find_config_file

    if find_config_file() is not None:
        return False

    ensure_dirs()
    wrote_config = write_default_config()
    wrote_example = write_connector_example()
    print("  (auto-generated default config — edit before re-running)")
    print(f"    {wrote_config}")
    print(f"    {wrote_example}")
    return True
