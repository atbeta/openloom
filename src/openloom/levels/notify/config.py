from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WebhookEntry:
    url: str
    events: frozenset[str] = field(default_factory=frozenset)
    timeout_seconds: float = 3.0
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FileEntry:
    directory: Path
    events: frozenset[str] = field(default_factory=frozenset)
    prefix: str = "openloom"


@dataclass(frozen=True)
class NotifyConfig:
    webhooks: list[WebhookEntry] = field(default_factory=list)
    files: list[FileEntry] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.webhooks or self.files)

    @classmethod
    def empty(cls) -> NotifyConfig:
        return cls()

    @classmethod
    def from_mapping(cls, raw: Any) -> NotifyConfig:
        if raw is None:
            return cls.empty()
        if not isinstance(raw, dict):
            raise ValueError(f"notify config must be a mapping, got {type(raw).__name__}")

        webhooks: list[WebhookEntry] = []
        for entry in raw.get("webhook", []) or []:
            if not isinstance(entry, dict):
                raise ValueError("notify.webhook entries must be mappings")
            url = entry.get("url")
            if not url:
                raise ValueError("notify.webhook entry missing 'url'")
            webhooks.append(WebhookEntry(
                url=str(url),
                events=_coerce_event_filter(entry.get("events")),
                timeout_seconds=float(entry.get("timeout_seconds", 3.0)),
                headers={str(k): str(v) for k, v in (entry.get("headers") or {}).items()},
            ))

        files: list[FileEntry] = []
        for entry in raw.get("file", []) or []:
            if not isinstance(entry, dict):
                raise ValueError("notify.file entries must be mappings")
            dir_raw = entry.get("dir") or entry.get("directory")
            if not dir_raw:
                raise ValueError("notify.file entry missing 'dir'")
            directory = Path(str(dir_raw)).expanduser()
            if not directory.is_absolute():
                directory = Path.cwd() / directory
            files.append(FileEntry(
                directory=directory,
                events=_coerce_event_filter(entry.get("events")),
                prefix=str(entry.get("prefix", "openloom")),
            ))

        return cls(webhooks=webhooks, files=files)

    @classmethod
    def from_env(cls) -> NotifyConfig:
        """Read simple env-based config — used when no yaml section is supplied."""
        webhooks: list[WebhookEntry] = []
        urls = _split_env(os.getenv("OPENLOOM_NOTIFY_WEBHOOK_URLS", ""))
        for url in urls:
            webhooks.append(WebhookEntry(
                url=url,
                events=_coerce_event_filter(
                    os.getenv("OPENLOOM_NOTIFY_WEBHOOK_EVENTS", "*"),
                ),
            ))

        files: list[FileEntry] = []
        dirs = _split_env(os.getenv("OPENLOOM_NOTIFY_FILE_DIRS", ""))
        for d in dirs:
            directory = Path(d).expanduser()
            if not directory.is_absolute():
                directory = Path.cwd() / directory
            files.append(FileEntry(
                directory=directory,
                events=_coerce_event_filter(
                    os.getenv("OPENLOOM_NOTIFY_FILE_EVENTS", "*"),
                ),
            ))

        return cls(webhooks=webhooks, files=files)


def _split_env(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _coerce_event_filter(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset({"*"})
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",") if v.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        raise ValueError(f"notify events filter must be string or list, got {type(value).__name__}")
    return frozenset(items) if items else frozenset({"*"})
