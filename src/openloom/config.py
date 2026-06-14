from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from openloom.core.notify_config import NotifyConfig


@dataclass(frozen=True)
class Settings:
    opencode_url: str
    opencode_username: str
    opencode_password: str
    database_path: Path
    ui_host: str = "127.0.0.1"
    ui_port: int = 55413
    max_task_tokens: int | None = None
    max_task_runtime_minutes: int | None = None
    notify: NotifyConfig = field(default_factory=NotifyConfig.empty)
    inbox_dir: Path | None = None
    inbox_default_workspace: str = ""
    inbox_default_session: str = ""
    inbox_filename: str = "task.md"
    inbox_poll_interval_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> Settings:
        database = Path(os.getenv("OPENLOOM_DATABASE", ".openloom/openloom.sqlite3")).expanduser()
        if not database.is_absolute():
            database = Path.cwd() / database

        inbox_raw = os.getenv("OPENLOOM_INBOX_DIR", "").strip()
        inbox_dir: Path | None = None
        if inbox_raw:
            inbox_dir = Path(inbox_raw).expanduser()
            if not inbox_dir.is_absolute():
                inbox_dir = Path.cwd() / inbox_dir

        interval_raw = os.getenv("OPENLOOM_INBOX_POLL_SECONDS", "").strip()
        poll_interval = 30.0
        if interval_raw:
            try:
                poll_interval = max(1.0, float(interval_raw))
            except ValueError:
                poll_interval = 30.0

        return cls(
            opencode_url=os.getenv("OPENLOOM_OPENCODE_URL", "http://127.0.0.1:4096").rstrip("/"),
            opencode_username=os.getenv("OPENLOOM_OPENCODE_USERNAME", "opencode"),
            opencode_password=os.getenv("OPENLOOM_OPENCODE_PASSWORD", ""),
            database_path=database,
            ui_host=os.getenv("OPENLOOM_UI_HOST", "127.0.0.1"),
            ui_port=int(os.getenv("OPENLOOM_UI_PORT", "55413")),
            max_task_tokens=_optional_env_int("OPENLOOM_MAX_TASK_TOKENS"),
            max_task_runtime_minutes=_optional_env_int("OPENLOOM_MAX_TASK_RUNTIME_MINUTES"),
            notify=NotifyConfig.from_env(),
            inbox_dir=inbox_dir,
            inbox_default_workspace=os.getenv("OPENLOOM_INBOX_DEFAULT_WORKSPACE", "").strip(),
            inbox_default_session=os.getenv("OPENLOOM_INBOX_DEFAULT_SESSION", "").strip(),
            inbox_filename=os.getenv("OPENLOOM_INBOX_FILENAME", "task.md").strip() or "task.md",
            inbox_poll_interval_seconds=poll_interval,
        )


def _optional_env_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None
