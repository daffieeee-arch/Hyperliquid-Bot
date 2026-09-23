"""Event-driven capture_operator_alert — no polling watchdogs."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import cast

import pytest

from hyperliquid_bot.capture_operator_alert import (
    CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV,
    CAPTURE_ALERT_WEBHOOK_ENV,
    WebhookDeliveryError,
    _default_webhook_post,
    capture_alert_webhook_headers,
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
    posted: list[tuple[str, bytes, float, Mapping[str, str]]] = []

    def poster(url: str, body: bytes, timeout: float, headers: Mapping[str, str]) -> int:
        posted.append((url, body, timeout, headers))
        return 204

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
    posted: list[tuple[str, bytes, float, Mapping[str, str]]] = []
    token = "Bearer unit-test-token"

    def poster(url: str, body: bytes, timeout: float, headers: Mapping[str, str]) -> int:
        posted.append((url, body, timeout, dict(headers)))
        return 204

    with caplog.at_level("INFO", logger="hyperliquid_bot.capture"):
        payload = emit_capture_operator_alert(
            venue="bitvavo",
            run_id="run-2",
            status="FAILED",
            error=RuntimeError("Bitvavo Market Data Pro subscription failed."),
            host="chupa",
            environ={
                CAPTURE_ALERT_WEBHOOK_ENV: "https://example.test/hook",
                CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV: token,
            },
            webhook_poster=poster,
            timeout_seconds=1.5,
        )
    assert payload is not None
    assert "capture_operator_alert" in caplog.text
    assert "venue=bitvavo" in caplog.text
    assert "capture_operator_alert_webhook_ok http_status=204 attempts=1" in caplog.text
    assert token not in caplog.text
    assert len(posted) == 1
    assert posted[0][0] == "https://example.test/hook"
    assert posted[0][2] == 1.5
    assert posted[0][3]["Authorization"] == token
    body = cast(dict[str, str], json.loads(posted[0][1].decode("utf-8")))
    assert body["venue"] == "bitvavo"
    assert body["run_id"] == "run-2"
    assert body["status"] == "FAILED"
    assert body["error_class"] == "RuntimeError"


def test_webhook_failure_is_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0

    def poster(url: str, body: bytes, timeout: float, headers: Mapping[str, str]) -> int:
        del url, body, timeout, headers
        nonlocal calls
        calls += 1
        raise TimeoutError("slow")

    with caplog.at_level("WARNING", logger="hyperliquid_bot.capture"):
        payload = emit_capture_operator_alert(
            venue="binance",
            run_id="run-3",
            status="FAILED",
            error=RuntimeError("boom"),
            environ={CAPTURE_ALERT_WEBHOOK_ENV: "https://example.test/hook"},
            webhook_poster=poster,
            retry_backoff_seconds=(0.0, 0.0),
        )
    assert payload is not None
    assert calls == 3
    assert (
        "capture_operator_alert_webhook_failed error_class=TimeoutError attempts=3" in caplog.text
    )
    assert "https://example.test/hook" not in caplog.text


def test_webhook_auth_reject_is_not_retried(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0

    def poster(url: str, body: bytes, timeout: float, headers: Mapping[str, str]) -> int:
        del url, body, timeout, headers
        nonlocal calls
        calls += 1
        raise WebhookDeliveryError("HTTPError", http_status=401)

    with caplog.at_level("WARNING", logger="hyperliquid_bot.capture"):
        payload = emit_capture_operator_alert(
            venue="binance",
            run_id="run-4",
            status="FAILED",
            error=RuntimeError("boom"),
            environ={
                CAPTURE_ALERT_WEBHOOK_ENV: "https://example.test/hook",
                CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV: "Bearer unit-test-token",
            },
            webhook_poster=poster,
        )
    assert payload is not None
    assert calls == 1
    assert (
        "capture_operator_alert_webhook_failed error_class=HTTPError http_status=401 attempts=1"
        in caplog.text
    )
    assert "Bearer unit-test-token" not in caplog.text


def test_webhook_retries_then_succeeds() -> None:
    calls = 0

    def poster(url: str, body: bytes, timeout: float, headers: Mapping[str, str]) -> int:
        del url, body, timeout, headers
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("slow")
        return 202

    payload = emit_capture_operator_alert(
        venue="kraken",
        run_id="run-5",
        status="FAILED",
        error=RuntimeError("boom"),
        environ={
            CAPTURE_ALERT_WEBHOOK_ENV: "https://example.test/hook",
            CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV: "Bearer unit-test-token",
        },
        webhook_poster=poster,
        retry_backoff_seconds=(0.0, 0.0),
    )
    assert payload is not None
    assert calls == 3


def test_authorization_header_omits_unsafe_values() -> None:
    headers = capture_alert_webhook_headers({CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV: "Bearer ok"})
    assert headers["Authorization"] == "Bearer ok"
    with pytest.raises(ValueError, match="not printable"):
        capture_alert_webhook_headers(
            {CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV: "Bearer bad\r\nX-Injected: 1"}
        )
    assert "Authorization" not in capture_alert_webhook_headers({})


def test_default_webhook_post_sends_authorization_and_hides_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "Bearer unit-test-token"
    seen: dict[str, object] = {}

    class _Response:
        status = 204

        def read(self, size: int) -> bytes:
            del size
            return b""

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        seen["timeout"] = timeout
        seen["authorization"] = request.get_header("Authorization")
        seen["content_type"] = request.get_header("Content-type")
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status = _default_webhook_post(
        "https://example.test/hook?secret=supersecrettokenvalue123456",
        b"{}",
        1.25,
        {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": token,
        },
    )
    assert status == 204
    assert seen["timeout"] == 1.25
    assert seen["authorization"] == token

    def fail_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        del timeout
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    with pytest.raises(WebhookDeliveryError) as raised:
        _default_webhook_post(
            "https://example.test/hook?secret=supersecrettokenvalue123456",
            b"{}",
            1.0,
            {"Content-Type": "application/json; charset=utf-8"},
        )
    assert raised.value.http_status == 403
    assert raised.value.error_class == "HTTPError"
    assert "example.test" not in str(raised.value)
    assert "supersecrettokenvalue123456" not in str(raised.value)
