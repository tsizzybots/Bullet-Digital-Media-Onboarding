"""Unit tests for the Slack reconciliation notifier (S1-23).

No DB: these exercise the PURE `format_reconciliation_alert` formatter, the
`FakeSlackNotifier` test double, and `HttpSlackNotifier` against an injected
`httpx.MockTransport` (so no network call). The suite runs with
`asyncio_mode = "auto"`, so async test functions need no decorator.
"""

from __future__ import annotations

import json
import logging
import types

import httpx
import pytest

from bullet_api.integrations.slack import (
    FakeSlackNotifier,
    HttpSlackNotifier,
    format_reconciliation_alert,
)


def test_format_reconciliation_alert_includes_id_name_and_intent() -> None:
    doc = types.SimpleNamespace(
        document_id="doc_x",
        name="Agreement",
        date_completed="2026-06-01T00:00:00Z",
    )

    text = format_reconciliation_alert(doc)

    # Identifying fields are present.
    assert "doc_x" in text
    assert "Agreement" in text
    # Reads as a "webhook missed / reconciliation created" alert.
    lowered = text.lower()
    assert "webhook" in lowered
    assert "missed" in lowered
    assert "reconciliation" in lowered
    assert "created" in lowered
    # The optional completion timestamp is surfaced when present.
    assert "2026-06-01T00:00:00Z" in text


def test_format_reconciliation_alert_omits_completed_when_absent() -> None:
    doc = types.SimpleNamespace(document_id="doc_y", name="Service Agreement")

    text = format_reconciliation_alert(doc)

    assert "doc_y" in text
    assert "Service Agreement" in text
    assert "completed" not in text.lower()


async def test_fake_notifier_records_posts() -> None:
    notifier = FakeSlackNotifier()

    await notifier.post("hi")
    await notifier.post("there")

    assert notifier.posted == ["hi", "there"]


async def test_http_notifier_empty_webhook_is_noop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty webhook -> warning logged, NO HTTP call made."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP call must not happen when webhook_url is empty")

    notifier = HttpSlackNotifier("", transport=httpx.MockTransport(handler))

    with caplog.at_level(logging.WARNING):
        await notifier.post("x")

    # No exception raised (the handler would have raised AssertionError).
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert any("empty" in record.getMessage().lower() for record in caplog.records)


async def test_http_notifier_posts_json_to_webhook() -> None:
    """Non-empty webhook -> POSTs {"text": ...} to the webhook URL."""
    webhook_url = "https://hooks.slack.example/services/T000/B000/xyz"
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, text="ok")

    notifier = HttpSlackNotifier(webhook_url, transport=httpx.MockTransport(handler))

    await notifier.post("x")

    assert captured["method"] == "POST"
    assert captured["url"] == webhook_url
    assert captured["json"] == {"text": "x"}
