from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    opencode_url: str
    opencode_username: str
    opencode_password: str
    database_path: Path
    ui_host: str = "127.0.0.1"
    ui_port: int = 55413

    @classmethod
    def from_env(cls) -> Settings:
        database = Path(os.getenv("OPENLOOM_DATABASE", ".openloom/openloom.sqlite3")).expanduser()
        if not database.is_absolute():
            database = Path.cwd() / database

        return cls(
            opencode_url=os.getenv("OPENLOOM_OPENCODE_URL", "http://127.0.0.1:14096").rstrip("/"),
            opencode_username=os.getenv("OPENLOOM_OPENCODE_USERNAME", "opencode"),
            opencode_password=os.getenv("OPENLOOM_OPENCODE_PASSWORD", "xxx"),
            database_path=database,
            ui_host=os.getenv("OPENLOOM_UI_HOST", "127.0.0.1"),
            ui_port=int(os.getenv("OPENLOOM_UI_PORT", "55413")),
        )
