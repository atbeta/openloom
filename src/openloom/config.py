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
    notify: NotifyConfig = field(default_factory=NotifyConfig.empty)
    notify_recent_messages: int = 3

    @classmethod
    def from_env(cls) -> Settings:
        database = Path(
            os.getenv("OPENLOOM_DATABASE", ".openloom/openloom.sqlite3"),
        ).expanduser()
        if not database.is_absolute():
            database = Path.cwd() / database

        return cls(
            opencode_url=os.getenv("OPENLOOM_OPENCODE_URL", "http://127.0.0.1:4096").rstrip("/"),
            opencode_username=os.getenv("OPENLOOM_OPENCODE_USERNAME", "opencode"),
            opencode_password=os.getenv("OPENLOOM_OPENCODE_PASSWORD", ""),
            database_path=database,
            ui_host=os.getenv("OPENLOOM_UI_HOST", "127.0.0.1"),
            ui_port=int(os.getenv("OPENLOOM_UI_PORT", "55413")),
            notify=NotifyConfig.from_env(),
            notify_recent_messages=(
                _optional_env_int("OPENLOOM_NOTIFY_RECENT_MESSAGES") or 3
            ),
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
