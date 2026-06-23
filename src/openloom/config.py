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
    # Treat "agent went idle" as completion by default. Webhook /
    # connector users want the task to terminate as soon as the
    # agent stops responding; without this the task sits in
    # "running" forever once OpenCode's last message is delivered.
    # Set OPENLOOM_IDLE_COMPLETES_TASK=false to revert to the strict
    # "only TASK COMPLETE marker counts" behaviour. The harness
    # layer will introduce more nuanced retry / nudge controls and
    # may revisit this default.
    idle_completes_task: bool = True
    # Auto-accept every pending tool-permission prompt that
    # OpenCode raises during a session. Webhook / connector users
    # are usually remote and cannot drive a dashboard to click
    # "Allow" — leaving the default off means tasks stay stuck in
    # ``waiting`` until somebody logs into the UI. The acceptance
    # uses OpenCode's "once" response so each tool call still asks
    # permission in a long-lived session (the user keeps audit
    # visibility on the OpenCode side); what changes is that the
    # harness proactively answers the prompt instead of waiting
    # for an operator. Set OPENLOOM_AUTO_ACCEPT_PERMISSIONS=false
    # to keep the previous behaviour and route every permission
    # through /api/permissions for manual approval.
    auto_accept_permissions: bool = True

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
            idle_completes_task=_optional_env_bool(
                "OPENLOOM_IDLE_COMPLETES_TASK", default=True,
            ),
            auto_accept_permissions=_optional_env_bool(
                "OPENLOOM_AUTO_ACCEPT_PERMISSIONS", default=True,
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


def _optional_env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")
