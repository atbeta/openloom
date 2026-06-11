from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _split_paths(value: str) -> list[Path]:
    paths: list[Path] = []
    for item in value.split(":"):
        item = item.strip()
        if not item:
            continue
        paths.append(Path(item).expanduser().resolve())
    return paths


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    opencode_url: str
    opencode_username: str
    opencode_password: str
    database_path: Path
    allowed_roots: list[Path]
    strict_roots: bool
    ui_host: str = "127.0.0.1"
    ui_port: int = 55413

    @classmethod
    def from_env(cls) -> Settings:
        database = Path(os.getenv("OPENLOOM_DATABASE", ".openloom/openloom.sqlite3")).expanduser()
        if not database.is_absolute():
            database = Path.cwd() / database
        roots = _split_paths(os.getenv("OPENLOOM_ALLOWED_ROOTS", ""))

        return cls(
            opencode_url=os.getenv("OPENLOOM_OPENCODE_URL", "http://127.0.0.1:14096").rstrip("/"),
            opencode_username=os.getenv("OPENLOOM_OPENCODE_USERNAME", "opencode"),
            opencode_password=os.getenv("OPENLOOM_OPENCODE_PASSWORD", "xxx"),
            database_path=database,
            allowed_roots=roots,
            strict_roots=_env_bool("OPENLOOM_STRICT_ROOTS", default=False),
            ui_host=os.getenv("OPENLOOM_UI_HOST", "127.0.0.1"),
            ui_port=int(os.getenv("OPENLOOM_UI_PORT", "55413")),
        )

    def is_allowed_workspace(self, cwd: str) -> bool:
        path = Path(cwd).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            return False
        if not self.strict_roots:
            return True
        if not self.allowed_roots:
            return False
        return any(path == root or root in path.parents for root in self.allowed_roots)
