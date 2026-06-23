"""
Notify configuration data classes — lifted to core/ so that
``openloom.config`` does not need to import from ``levels/notify``.

The YAML / env parsing helpers live here as private module functions;
``levels/notify/config.py`` re-exports them for backward compatibility.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WebhookEntry:
    url: str
    events: frozenset[str] = field(default_factory=frozenset)
    timeout_seconds: float = 3.0
    headers: dict[str, str] = field(default_factory=dict)
    signing_secret: str = ""
    max_retries: int = 3


@dataclass(frozen=True)
class NotifyConfig:
    webhooks: list[WebhookEntry] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.webhooks)

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
                signing_secret=str(entry.get("signing_secret") or ""),
                max_retries=int(entry.get("max_retries", 3)),
            ))

        return cls(webhooks=webhooks)

    @classmethod
    def from_env(cls) -> NotifyConfig:
        """Read env-var-only config. Used as a fallback when the YAML
        file has no ``notify`` section. Webhook list comes from
        ``OPENLOOM_NOTIFY_WEBHOOK_URLS`` (comma-separated); per-webhook
        secrets/events/retries come from
        ``OPENLOOM_NOTIFY_WEBHOOK_SECRET`` /
        ``OPENLOOM_NOTIFY_WEBHOOK_EVENTS`` /
        ``OPENLOOM_NOTIFY_WEBHOOK_MAX_RETRIES``.
        """
        webhooks: list[WebhookEntry] = []
        urls = _split_env(os.getenv("OPENLOOM_NOTIFY_WEBHOOK_URLS", ""))
        for url in urls:
            webhooks.append(WebhookEntry(
                url=url,
                events=_coerce_event_filter(
                    os.getenv("OPENLOOM_NOTIFY_WEBHOOK_EVENTS", "*"),
                ),
                signing_secret=os.getenv("OPENLOOM_NOTIFY_WEBHOOK_SECRET", ""),
                max_retries=_optional_int(os.getenv("OPENLOOM_NOTIFY_WEBHOOK_MAX_RETRIES", "3"), 3),
            ))

        return cls(webhooks=webhooks)

    @classmethod
    def from_sources(cls, file_cfg: dict[str, Any] | None) -> NotifyConfig:
        """Build NotifyConfig from YAML notify section + OPENLOOM_* env vars.

        Order of precedence (highest wins):
        1. Env vars (``OPENLOOM_NOTIFY_WEBHOOK_*``)
        2. YAML ``notify.webhook`` entries

        The YAML list replaces the env-var list when present — env
        vars fill in defaults for entries that don't specify the
        field, not the other way around. A YAML file with no
        ``notify`` section falls through to env vars.
        """
        env_webhooks = cls.from_env().webhooks
        env_by_url: dict[str, WebhookEntry] = {wh.url: wh for wh in env_webhooks}

        if not file_cfg:
            return cls(webhooks=list(env_webhooks))

        # The YAML file's webhook list, with env vars merged in for
        # fields each entry didn't override (so signing_secret,
        # events, and max_retries are inherited from the env-var
        # defaults — matching how a "yaml partial override" should
        # feel).
        out: list[WebhookEntry] = []
        for entry in cls.from_mapping(file_cfg).webhooks:
            base = env_by_url.get(entry.url, WebhookEntry(url=entry.url))
            out.append(WebhookEntry(
                url=entry.url,
                events=entry.events or base.events,
                timeout_seconds=entry.timeout_seconds or base.timeout_seconds,
                headers=entry.headers or base.headers,
                signing_secret=entry.signing_secret or base.signing_secret,
                max_retries=entry.max_retries or base.max_retries,
            ))

        return cls(webhooks=out)


def _split_env(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _optional_int(raw: str, default: int) -> int:
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


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
