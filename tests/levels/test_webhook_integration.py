"""Tests for webhook integration — inbound parsing, outbound signing, registry."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from openloom.core.events import Event, EventType
from openloom.core.registry import get_source, list_sources, register_source
from openloom.core.webhook_types import (
    SourceParser,
    WebhookInboundEvent,
    render_payload,
)
from openloom.levels.notify.inbound import GenericSource
from openloom.levels.notify.webhook import WebhookSink, verify_signature


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


# ── Inbound: WebhookInboundEvent ──────────────────────────────────────────


def test_webhook_inbound_event_frozen() -> None:
    e = WebhookInboundEvent(source="test", goal="do it")
    with pytest.raises(Exception):
        e.source = "tampered"  # type: ignore[misc]


def test_webhook_inbound_event_defaults() -> None:
    e = WebhookInboundEvent(source="x")
    assert e.event_name == ""
    assert e.name == ""
    assert e.workspace == ""
    assert e.goal == ""
    assert e.metadata == {}


# ── Inbound: SourceParser ABC ─────────────────────────────────────────────


def test_source_parser_is_abstract() -> None:
    with pytest.raises(TypeError):
        SourceParser()  # type: ignore[abstract]


def test_custom_source_parser() -> None:
    class MySource(SourceParser):
        def parse(self, headers: dict[str, str], body: dict[str, Any]) -> WebhookInboundEvent | None:
            return WebhookInboundEvent(source="my", goal=body.get("task", ""))

    p = MySource()
    result = p.parse({}, {"task": "fix tests"})
    assert result is not None
    assert result.source == "my"
    assert result.goal == "fix tests"


# ── Inbound: Source registry ──────────────────────────────────────────────


def test_generic_source_registered() -> None:
    sources = list_sources()
    assert "generic" in sources


def test_get_source_returns_instance() -> None:
    src = get_source("generic")
    assert isinstance(src, GenericSource)


def test_get_source_unknown_raises() -> None:
    with pytest.raises(KeyError, match="Unknown webhook source"):
        get_source("nonexistent_source_xyz")


def test_register_source_decorator() -> None:
    @register_source("test_custom")
    class TestSource(SourceParser):
        def parse(self, headers: dict[str, str], body: dict[str, Any]) -> WebhookInboundEvent | None:
            return WebhookInboundEvent(source="test_custom", goal="test")

    assert "test_custom" in list_sources()
    result = get_source("test_custom").parse({}, {})
    assert result is not None
    assert result.source == "test_custom"


# ── Inbound: GenericSource parser ─────────────────────────────────────────


def test_generic_source_parses_goal() -> None:
    src = GenericSource()
    result = src.parse({}, {"goal": "fix the bug"})
    assert result is not None
    assert result.source == "generic"
    assert result.goal == "fix the bug"
    assert result.event_name == "webhook"


def test_generic_source_parses_message_alias() -> None:
    src = GenericSource()
    result = src.parse({}, {"message": "deploy it"})
    assert result is not None
    assert result.goal == "deploy it"


def test_generic_source_parses_full_payload() -> None:
    src = GenericSource()
    result = src.parse({}, {
        "name": "CI Build",
        "workspace": "/tmp/proj",
        "goal": "run tests",
        "event": "push",
        "metadata": {"branch": "main"},
    })
    assert result is not None
    assert result.name == "CI Build"
    assert result.workspace == "/tmp/proj"
    assert result.goal == "run tests"
    assert result.event_name == "push"
    assert result.metadata == {"branch": "main"}


def test_generic_source_returns_none_on_empty_body() -> None:
    src = GenericSource()
    assert src.parse({}, {}) is None
    assert src.parse({}, {"name": "no goal"}) is None


def test_generic_source_ignores_whitespace_goal() -> None:
    src = GenericSource()
    assert src.parse({}, {"goal": "   "}) is None


# ── Outbound: render_payload v1 schema ────────────────────────────────────


def test_render_payload_v1_schema() -> None:
    payload = render_payload(_event())
    assert payload["schema_version"] == "1.0"
    assert payload["event"] == "TASK_COMPLETED"
    assert payload["task_id"] == "t-1"
    assert payload["task_name"] == ""
    assert payload["store_version"] == 3
    assert payload["timestamp"] == 1_700_000_000.0
    assert "timestamp_iso" in payload
    assert payload["data"] == {"status": "completed", "summary": "ok"}


# ── Outbound: HMAC signing ────────────────────────────────────────────────


@respx.mock
def test_webhook_signing_when_secret_set() -> None:
    route = respx.post("https://example.com/hook").mock(
        return_value=httpx.Response(200),
    )
    sink = WebhookSink(
        url="https://example.com/hook",
        signing_secret="my-secret",
    )
    sink.on_event(_event())
    sink.close()

    request = route.calls.last.request
    sig = request.headers.get("X-OpenLoom-Signature-256")
    assert sig is not None
    assert sig.startswith("sha256=")

    body = request.content.decode()
    assert verify_signature("my-secret", body, sig)


@respx.mock
def test_webhook_no_signing_when_no_secret() -> None:
    route = respx.post("https://example.com/hook").mock(
        return_value=httpx.Response(200),
    )
    sink = WebhookSink(url="https://example.com/hook")
    sink.on_event(_event())
    sink.close()

    request = route.calls.last.request
    assert "X-OpenLoom-Signature-256" not in request.headers


def test_verify_signature_valid() -> None:
    import hashlib
    import hmac

    body = '{"test": true}'
    sig = hmac.new(b"secret", body.encode(), hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, f"sha256={sig}")


def test_verify_signature_invalid() -> None:
    assert not verify_signature("secret", '{"test": true}', "sha256=deadbeef")


def test_verify_signature_malformed_header() -> None:
    with pytest.raises(ValueError, match="sha256="):
        verify_signature("secret", "body", "bad-format")


# ── Outbound: retry logic ─────────────────────────────────────────────────


@respx.mock
def test_webhook_retries_on_5xx() -> None:
    route = respx.post("https://example.com/hook").mock(
        side_effect=[
            httpx.Response(500, text="err"),
            httpx.Response(500, text="err"),
            httpx.Response(200, text="ok"),
        ],
    )
    sink = WebhookSink(
        url="https://example.com/hook",
        max_retries=2,
    )
    sink.on_event(_event())
    sink.close()
    assert route.call_count == 3


@respx.mock
def test_webhook_stops_retrying_on_success() -> None:
    route = respx.post("https://example.com/hook").mock(
        side_effect=[
            httpx.Response(500, text="err"),
            httpx.Response(200, text="ok"),
        ],
    )
    sink = WebhookSink(
        url="https://example.com/hook",
        max_retries=3,
    )
    sink.on_event(_event())
    sink.close()
    assert route.call_count == 2


@respx.mock
def test_webhook_zero_retries_no_extra_attempts() -> None:
    route = respx.post("https://example.com/hook").mock(
        return_value=httpx.Response(500, text="err"),
    )
    sink = WebhookSink(
        url="https://example.com/hook",
        max_retries=0,
    )
    sink.on_event(_event())
    sink.close()
    assert route.call_count == 1


# ── Outbound: payload schema_version ──────────────────────────────────────


@respx.mock
def test_webhook_payload_has_schema_version() -> None:
    route = respx.post("https://example.com/hook").mock(
        return_value=httpx.Response(200),
    )
    sink = WebhookSink(url="https://example.com/hook")
    sink.on_event(_event())
    sink.close()

    body = json.loads(route.calls.last.request.content)
    assert body["schema_version"] == "1.0"


# ── Config: signing_secret and max_retries ────────────────────────────────


def test_config_from_mapping_signing_secret() -> None:
    from openloom.core.notify_config import NotifyConfig

    cfg = NotifyConfig.from_mapping({
        "webhook": [{
            "url": "https://x",
            "signing_secret": "s3cret",
            "max_retries": 5,
        }],
    })
    assert cfg.webhooks[0].signing_secret == "s3cret"
    assert cfg.webhooks[0].max_retries == 5


def test_config_from_mapping_defaults() -> None:
    from openloom.core.notify_config import NotifyConfig

    cfg = NotifyConfig.from_mapping({
        "webhook": [{"url": "https://x"}],
    })
    assert cfg.webhooks[0].signing_secret == ""
    assert cfg.webhooks[0].max_retries == 3
