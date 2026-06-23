"""Tests for the notify level — webhook sink, config loader, builder."""

from __future__ import annotations

import json
import threading
import time

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


@respx.mock
def test_webhook_on_event_does_not_block_event_bus() -> None:
    """Regression test: ``on_event`` must return immediately even when
    the webhook target times out. The previous implementation did the
    HTTP POST + retry backoff synchronously, which blocked the OpenLoom
    event bus (and therefore the FastAPI dashboard / SSE stream) for up
    to ``(1 + max_retries) * timeout_seconds`` per event. With the
    background-worker implementation, ``on_event`` returns in milliseconds
    regardless of how slow or unreachable the webhook target is.
    """
    respx.post("https://example.com/hook").mock(
        side_effect=httpx.ConnectError("nope"),
    )
    sink = WebhookSink(
        url="https://example.com/hook",
        max_retries=3,
    )
    # If ``on_event`` blocked, this would take at least ~21 seconds
    # (4 attempts * 3s default timeout + 1s + 4s + 16s backoff). Assert it
    # returns in well under that bound.
    start = time.monotonic()
    sink.on_event(_event())
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, (
        f"on_event blocked for {elapsed:.2f}s — webhook delivery is not "
        "offloaded to a background worker"
    )
    sink.close(timeout=10.0)


@respx.mock
def test_webhook_ignores_system_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: WebhookSink's internal httpx.Client must not
    honour HTTP_PROXY / HTTPS_PROXY environment variables. On a machine
    with a system-wide proxy (corporate VPN, Clash, mitmproxy, etc.),
    requests to 127.0.0.1 get routed through that proxy, which either
    times out or returns a generic \"Content Filter - Access Denied\" HTML
    page instead of reaching the actual webhook target. Without
    trust_env=False the connector's inbound webhook listener is
    unreachable and every TASK_UPDATED event wastes its full retry budget.

    Mirrors the openloom-connector fix in commit 62f432b (the connector's
    inbound push side already does this).
    """
    import httpx as _httpx

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:9999")

    with respx.mock:
        route = respx.post("https://example.com/hook").mock(
            return_value=_httpx.Response(200, text="ok"),
        )
        sink = WebhookSink(
            url="https://example.com/hook",
            events=frozenset({"TASK_COMPLETED"}),
        )
        sink.on_event(_event())
        _wait_for_route(route, timeout=2.0)
        sink.close()

    # If trust_env is on, httpx would route the request through the
    # proxy.invalid host and respx would not see the call. With
    # trust_env=False, respx intercepts the request directly.
    assert route.call_count == 1


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
    _wait_for_route(route, timeout=2.0)
    sink.close()

    assert route.called
    request = route.calls.last.request
    assert request.headers["X-OpenLoom-Event"] == "TASK_COMPLETED"
    body = json.loads(request.content)
    assert body["event"] == "TASK_COMPLETED"
    assert body["task_id"] == "t-1"
    assert body["store_version"] == 3
    assert body["data"] == {"status": "completed", "summary": "ok"}


def _wait_for_route(route, timeout: float = 2.0) -> None:
    """Wait until the respx route has been called or timeout.

    ``WebhookSink`` delivers events on a background worker thread, so the
    route's call counter is updated asynchronously. Tests that assert on
    ``route.called`` after ``sink.on_event`` need to wait for the worker
    to actually issue the HTTP request before reading it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if route.called:
            return
        time.sleep(0.02)
    raise AssertionError(
        f"webhook route not called within {timeout}s "
        f"(worker thread may be stuck or respx mock not engaged)"
    )


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
    _wait_for_route(route, timeout=2.0)
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
    captured_event = threading.Event()
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        with lock:
            captured = request
        captured_event.set()
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    sink = WebhookSink(
        url="https://example.com/hook",
        headers={"Authorization": "Bearer t"},
        client=client,
    )
    sink.on_event(_event())
    assert captured_event.wait(timeout=2.0), (
        "webhook handler never invoked — worker thread may be stuck"
    )
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
