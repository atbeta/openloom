"""Tests for the notify level — webhook sink, config loader, builder."""

from __future__ import annotations

import json
import threading

import httpx
import pytest
import respx

from openloom.core.events import Event, EventType
from openloom.levels.notify import NotifyConfig, WebhookSink, build_sinks
from openloom.levels.notify.config import (
    NotifyConfig as NotifyConfigCls,
)
from openloom.levels.notify.config import (
    WebhookEntry,
)


def _event(
    type_: EventType = EventType.TASK_COMPLETED,
    task_id: str = "t-1",
    store_version: int = 3,
) -> Event:
    return Event(
        type=type_,
        task_id=task_id,
        timestamp=1_700_000_000.0,
        store_version=store_version,
        data={"status": "completed", "summary": "ok"},
    )


# --- config ---


def test_config_from_mapping_empty() -> None:
    cfg = NotifyConfig.from_mapping(None)
    assert not cfg.enabled
    assert cfg.webhooks == []


def test_config_from_mapping_webhook() -> None:
    cfg = NotifyConfigCls.from_mapping({
        "webhook": [
            {
                "url": "https://example.com/hook",
                "events": ["TASK_COMPLETED", "TASK_FAILED"],
                "timeout_seconds": 5,
                "headers": {"X-Token": "abc"},
            }
        ],
    })
    assert len(cfg.webhooks) == 1
    entry = cfg.webhooks[0]
    assert entry.url == "https://example.com/hook"
    assert entry.events == frozenset({"TASK_COMPLETED", "TASK_FAILED"})
    assert entry.timeout_seconds == 5.0
    assert entry.headers == {"X-Token": "abc"}


def test_config_from_mapping_event_filter_string_comma() -> None:
    cfg = NotifyConfigCls.from_mapping({
        "webhook": [{"url": "https://x", "events": "A,B"}],
    })
    assert cfg.webhooks[0].events == frozenset({"A", "B"})


def test_config_from_mapping_event_filter_default_is_wildcard() -> None:
    cfg = NotifyConfigCls.from_mapping({
        "webhook": [{"url": "https://x"}],
    })
    assert cfg.webhooks[0].events == frozenset({"*"})


def test_config_from_mapping_rejects_non_mapping_entry() -> None:
    import pytest
    with pytest.raises(ValueError, match="entries must be mappings"):
        NotifyConfigCls.from_mapping({"webhook": ["bad"]})


def test_config_from_mapping_rejects_webhook_without_url() -> None:
    import pytest
    with pytest.raises(ValueError, match="missing 'url'"):
        NotifyConfigCls.from_mapping({"webhook": [{}]})


def test_config_from_env_returns_empty_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("OPENLOOM_NOTIFY_WEBHOOK_URLS", "OPENLOOM_NOTIFY_WEBHOOK_EVENTS"):
        monkeypatch.delenv(name, raising=False)
    cfg = NotifyConfigCls.from_env()
    assert cfg.webhooks == []


def test_config_from_env_parses_csv_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENLOOM_NOTIFY_WEBHOOK_URLS", "https://a, https://b ,")
    cfg = NotifyConfigCls.from_env()
    assert [w.url for w in cfg.webhooks] == ["https://a", "https://b"]


# --- WebhookSink ---


@respx.mock
def test_webhook_posts_event_payload() -> None:
    route = respx.post("https://example.com/hook").mock(
        return_value=httpx.Response(200, text="ok"),
    )
    sink = WebhookSink(
        url="https://example.com/hook",
        events=frozenset({"TASK_COMPLETED"}),
    )
    sink.on_event(_event())
    sink.close()

    assert route.called
    request = route.calls.last.request
    assert request.headers["X-OpenLoom-Event"] == "TASK_COMPLETED"
    body = json.loads(request.content)
    assert body["event"] == "TASK_COMPLETED"
    assert body["task_id"] == "t-1"
    assert body["store_version"] == 3
    assert body["data"] == {"status": "completed", "summary": "ok"}


@respx.mock
def test_webhook_filters_by_event_name() -> None:
    route = respx.post("https://example.com/hook").mock(
        return_value=httpx.Response(200),
    )
    sink = WebhookSink(
        url="https://example.com/hook",
        events=frozenset({"TASK_FAILED"}),
    )
    sink.on_event(_event(EventType.TASK_COMPLETED))
    sink.close()
    assert not route.called


@respx.mock
def test_webhook_wildcard_matches_everything() -> None:
    route = respx.post("https://example.com/hook").mock(
        return_value=httpx.Response(200),
    )
    sink = WebhookSink(url="https://example.com/hook", events=frozenset({"*"}))
    sink.on_event(_event(EventType.TASK_CREATED))
    sink.on_event(_event(EventType.TASK_UPDATED))
    sink.close()
    assert route.call_count == 2


@respx.mock
def test_webhook_logs_but_does_not_raise_on_5xx() -> None:
    respx.post("https://example.com/hook").mock(
        return_value=httpx.Response(500, text="boom"),
    )
    sink = WebhookSink(url="https://example.com/hook")
    sink.on_event(_event())  # should not raise
    sink.close()


@respx.mock
def test_webhook_logs_but_does_not_raise_on_network_error() -> None:
    respx.post("https://example.com/hook").mock(
        side_effect=httpx.ConnectError("nope"),
    )
    sink = WebhookSink(url="https://example.com/hook")
    sink.on_event(_event())
    sink.close()


def test_webhook_sends_custom_headers() -> None:
    captured: list[httpx.Request] = None  # type: ignore[assignment]
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        with lock:
            captured = request
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    sink = WebhookSink(
        url="https://example.com/hook",
        headers={"Authorization": "Bearer t"},
        client=client,
    )
    sink.on_event(_event())
    sink.close()
    assert captured is not None
    assert captured.headers["Authorization"] == "Bearer t"


# --- builder ---


def test_build_sinks_handles_none() -> None:
    assert build_sinks(None) == []


def test_build_sinks_handles_empty_config() -> None:
    assert build_sinks(NotifyConfig.empty()) == []


def test_build_sinks_emits_webhook(tmp_path) -> None:
    cfg = NotifyConfig(webhooks=[WebhookEntry(url="https://x")])
    sinks = build_sinks(cfg)
    assert len(sinks) == 1
    assert isinstance(sinks[0], WebhookSink)
