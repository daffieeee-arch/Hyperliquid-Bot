"""Event-driven capture_operator_alert — no polling watchdogs."""

from __future__ import annotations

import json
from typing import cast

import pytest

from hyperliquid_bot.capture_operator_alert import (
    CAPTURE_ALERT_WEBHOOK_ENV,
    capture_operator_alert_payload,
    emit_capture_operator_alert,
    sanitize_alert_error_message,
)


def test_sanitize_alert_error_message_redacts_secret_shaped_tokens() -> None:
    assert sanitize_alert_error_message("Binance required public stream was starved.") == (
        "Binance required public stream was starved."
    )
    assert sanitize_alert_error_message("key=abcdefghijklmnopqrstuvwxyz0123") == "redacted"


def test_payload_is_secret_free_and_stable() -> None:
    payload = capture_operator_alert_payload(
        venue="binance",
        run_id="run-1",
        status="FAILED",
        error=RuntimeError("Binance required public stream was starved."),
        host="chupa",
        ts_utc="2026-09-20T09:00:00Z",
    )
    assert payload == {
        "venue": "binance",
        "run_id": "run-1",
        "status": "FAILED",
        "error_class": "RuntimeError",
        "error_message": "Binance required public stream was starved.",
        "host": "chupa",
        "ts_utc": "2026-09-20T09:00:00Z",
    }


def test_emit_skips_non_failed_status() -> None:
    posted: list[tuple[str, bytes, float]] = []

    def poster(url: str, body: bytes, timeout: float) -> None:
        posted.append((url, body, timeout))

    assert (
        emit_capture_operator_alert(
            venue="binance",
            run_id="run-1",
            status="COMPLETED",
            environ={CAPTURE_ALERT_WEBHOOK_ENV: "https://example.test/hook"},
            webhook_poster=poster,
        )
        is None
    )
    assert posted == []


def test_emit_logs_and_posts_webhook_once(caplog: pytest.LogCaptureFixture) -> None:
    posted: list[tuple[str, bytes, float]] = []

    def poster(url: str, body: bytes, timeout: float) -> None:
        posted.append((url, body, timeout))

    with caplog.at_level("ERROR", logger="hyperliquid_bot.capture"):
        payload = emit_capture_operator_alert(
            venue="bitvavo",
            run_id="run-2",
            status="FAILED",
            error=RuntimeError("Bitvavo Market Data Pro subscription failed."),
            host="chupa",
            environ={CAPTURE_ALERT_WEBHOOK_ENV: "https://example.test/hook"},
            webhook_poster=poster,
            timeout_seconds=1.5,
        )
    assert payload is not None
    assert "capture_operator_alert" in caplog.text
    assert "venue=bitvavo" in caplog.text
    assert len(posted) == 1
    assert posted[0][0] == "https://example.test/hook"
    assert posted[0][2] == 1.5
    body = cast(dict[str, str], json.loads(posted[0][1].decode("utf-8")))
    assert body["venue"] == "bitvavo"
    assert body["run_id"] == "run-2"
    assert body["status"] == "FAILED"
    assert body["error_class"] == "RuntimeError"


def test_webhook_failure_is_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    def poster(url: str, body: bytes, timeout: float) -> None:
        del url, body, timeout
        raise TimeoutError("slow")

    with caplog.at_level("WARNING", logger="hyperliquid_bot.capture"):
        payload = emit_capture_operator_alert(
            venue="binance",
            run_id="run-3",
            status="FAILED",
            error=RuntimeError("boom"),
            environ={CAPTURE_ALERT_WEBHOOK_ENV: "https://example.test/hook"},
            webhook_poster=poster,
        )
    assert payload is not None
    assert "capture_operator_alert_webhook_failed" in caplog.text
