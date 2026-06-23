"""
Settings source — read OpenLoom's optional YAML config file.

The config file is searched at these paths (in order, first hit wins):

1. ``./openloom.yaml`` and ``./openloom.yml`` — project-level override,
   useful for repo-specific runs.
2. ``~/.openloom/config.yaml`` and ``~/.openloom/config.yml`` — user-level
   default, the recommended place for personal OpenLoom settings.

Schema:

    opencode:
      url: http://127.0.0.1:4096
      username: opencode
      # password stays in OPENLOOM_OPENCODE_PASSWORD env var — never
      # in a file, even a user-private one.
    ui:
      host: 127.0.0.1
      port: 55413
    database: .openloom/openloom.sqlite3
    harness:
      check_interval_seconds: 30
      idle_completes_task: true
      auto_accept_permissions: true
      notify_recent_messages: 3
    notify:
      webhook:
        - url: https://your-system.com/hook
          events: [TASK_COMPLETED, TASK_FAILED]
          signing_secret: ""
          max_retries: 3

Environment variables (OPENLOOM_*) override any value found in a
file. This follows the 12-factor convention: files for *persistent*
defaults, env vars for *deployment-specific* overrides.

Missing files and missing keys are both non-fatal — the loader
returns an empty dict and the caller falls back to its existing
defaults. A malformed file raises a clear error pointing at the
path so the user can fix it without having to grep the stack.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CONFIG_FILE_BASENAMES = (
    "openloom.yaml",
    "openloom.yml",
)

USER_CONFIG_BASENAMES = (
    "config.yaml",
    "config.yml",
)


def _candidate_paths() -> list[Path]:
    """Return config-file paths in priority order (project > user)."""
    paths: list[Path] = []
    cwd = Path.cwd()
    for name in CONFIG_FILE_BASENAMES:
        paths.append(cwd / name)
    user_dir = Path.home() / ".openloom"
    for name in USER_CONFIG_BASENAMES:
        paths.append(user_dir / name)
    return paths


def _read_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML config file into a nested dict. Empty/missing
    fields are returned as ``{}`` rather than ``None`` so callers
    can use ``.get()`` uniformly."""
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path}: top-level must be a mapping, got {type(raw).__name__}",
        )
    return raw


def find_config_file() -> Path | None:
    """Return the first existing config file path, or None if none
    of the candidates exist. Used by tests and by ``Settings`` to
    report which file was loaded."""
    for candidate in _candidate_paths():
        if candidate.is_file():
            return candidate
    return None


def load_config_file() -> dict[str, Any]:
    """Read the highest-priority existing config file and return its
    contents as a dict. Returns an empty dict if no file exists."""
    path = find_config_file()
    if path is None:
        return {}
    return _read_yaml(path)


def load_config_file_from(path: Path) -> dict[str, Any]:
    """Read a specific config file. Used by tests; production code
    uses ``load_config_file`` (searches the standard paths)."""
    if not path.is_file():
        return {}
    return _read_yaml(path)
