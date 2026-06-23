from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openloom.core.notify_config import NotifyConfig
from openloom.core.settings_source import load_config_file


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
        """Build Settings from YAML config file + OPENLOOM_* env vars.

        Order of precedence (highest wins):
        1. ``OPENLOOM_*`` environment variables
        2. ``./openloom.yaml`` (project-level)
        3. ``~/.openloom/config.yaml`` (user-level)
        4. Built-in defaults

        OpenCode's password stays env-only — see ``from_sources``
        for why. The notify webhook block falls through to env vars
        when the YAML file has no ``notify`` section.
        """
        cfg = load_config_file()
        return cls.from_sources(cfg)

    @classmethod
    def from_sources(cls, file_cfg: dict[str, Any]) -> Settings:
        """Build Settings from a parsed YAML config dict + the
        current process environment. Public for tests; production
        callers use ``from_env`` which loads the file first.

        ``opencode.password`` is *intentionally* not read from
        ``file_cfg`` — secrets don't belong in YAML files, even
        user-private ones. Password comes from
        ``OPENLOOM_OPENCODE_PASSWORD`` only.
        """
        opencode = file_cfg.get("opencode") if isinstance(file_cfg, dict) else None
        opencode = opencode if isinstance(opencode, dict) else {}

        ui = file_cfg.get("ui") if isinstance(file_cfg, dict) else None
        ui = ui if isinstance(ui, dict) else {}

        harness = file_cfg.get("harness") if isinstance(file_cfg, dict) else None
        harness = harness if isinstance(harness, dict) else {}

        database_raw = file_cfg.get("database") if isinstance(file_cfg, dict) else None
        # Env var wins over the file; falls back to file, then default.
        env_database = os.getenv("OPENLOOM_DATABASE", "").strip()
        if env_database:
            database_raw = env_database
        database = Path(str(database_raw) if database_raw else ".openloom/openloom.sqlite3")
        database = database.expanduser()
        if not database.is_absolute():
            database = Path.cwd() / database

        notify_raw = (
            file_cfg.get("notify") if isinstance(file_cfg, dict) else None
        )

        return cls(
            opencode_url=_str_or_env(
                opencode.get("url"),
                "OPENLOOM_OPENCODE_URL",
                default="http://127.0.0.1:4096",
            ).rstrip("/"),
            opencode_username=_str_or_env(
                opencode.get("username"),
                "OPENLOOM_OPENCODE_USERNAME",
                default="opencode",
            ),
            # Password is env-only; the YAML file is never consulted.
            opencode_password=os.getenv("OPENLOOM_OPENCODE_PASSWORD", ""),
            database_path=database,
            ui_host=_str_or_env(
                ui.get("host"), "OPENLOOM_UI_HOST", default="127.0.0.1",
            ),
            ui_port=_int_or_env(
                ui.get("port"), "OPENLOOM_UI_PORT", default=55413,
            ),
            notify=NotifyConfig.from_sources(
                notify_raw if isinstance(notify_raw, dict) else None,
            ),
            notify_recent_messages=_int_or_env(
                harness.get("notify_recent_messages"),
                "OPENLOOM_NOTIFY_RECENT_MESSAGES",
                default=3,
            ),
            idle_completes_task=_bool_or_env(
                harness.get("idle_completes_task"),
                "OPENLOOM_IDLE_COMPLETES_TASK",
                default=True,
            ),
            auto_accept_permissions=_bool_or_env(
                harness.get("auto_accept_permissions"),
                "OPENLOOM_AUTO_ACCEPT_PERMISSIONS",
                default=True,
            ),
        )


def _str_or_env(file_value: Any, env_name: str, *, default: str) -> str:
    """Pick the first non-empty value in priority: env > file > default."""
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        return env_value
    if file_value is not None and str(file_value).strip():
        return str(file_value).strip()
    return default


def _int_or_env(file_value: Any, env_name: str, *, default: int) -> int:
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        try:
            value = int(env_value)
            if value > 0:
                return value
        except ValueError:
            pass
    if file_value is not None:
        try:
            value = int(file_value)
            if value > 0:
                return value
        except (ValueError, TypeError):
            pass
    return default


def _bool_or_env(file_value: Any, env_name: str, *, default: bool) -> bool:
    env_value = os.getenv(env_name, "").strip().lower()
    if env_value:
        return env_value in ("1", "true", "yes", "on")
    if file_value is not None:
        if isinstance(file_value, bool):
            return file_value
        text = str(file_value).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    return default
