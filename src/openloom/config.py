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
    allowed_roots: list[Path]
    strict_roots: bool

    @classmethod
    def from_env(cls) -> Settings:
        roots = _split_paths(os.getenv("OPENLOOM_ALLOWED_ROOTS", ""))

        return cls(
            opencode_url=os.getenv("OPENLOOM_OPENCODE_URL", "http://127.0.0.1:14096").rstrip("/"),
            opencode_username=os.getenv("OPENLOOM_OPENCODE_USERNAME", "opencode"),
            opencode_password=os.getenv("OPENLOOM_OPENCODE_PASSWORD", "xxx"),
            allowed_roots=roots,
            strict_roots=_env_bool("OPENLOOM_STRICT_ROOTS", default=False),
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
