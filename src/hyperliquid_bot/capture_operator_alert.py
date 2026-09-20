"""Event-driven operator alerts for terminal PAPER capture failures.

Emits exactly once on fail-closed stop. No interval watchdogs or polling cron.
Optional webhook via CAPTURE_ALERT_WEBHOOK_URL (Grok Bot / CoS capture-fail).
Never blocks shutdown longer than the short HTTP timeout; failures are logged.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Final

from .capture_observability import capture_logger

CAPTURE_ALERT_WEBHOOK_ENV: Final = "CAPTURE_ALERT_WEBHOOK_URL"
CAPTURE_ALERT_WEBHOOK_TIMEOUT_SECONDS: Final = 2.0
_MAX_ERROR_MESSAGE_CHARS: Final = 240
_SECRETISH: Final = re.compile(
    r"(?i)(?:token|secret|password|passwd|authorization|api[-_]?key|"
    r"access[-_]?key|private[-_]?key|signature|bearer)"
    r"|[A-Za-z0-9+/_-]{24,}"
)

type WebhookPoster = Callable[[str, bytes, float], None]


def sanitize_alert_error_message(message: object) -> str:
    """Keep a short printable message; redact credential-shaped tokens."""

    if type(message) is not str:
        text = type(message).__name__ if message is not None else "unknown"
    else:
        text = message.strip() or "unknown"
    if len(text) > _MAX_ERROR_MESSAGE_CHARS:
        text = text[:_MAX_ERROR_MESSAGE_CHARS]
    if not all(32 <= ord(character) <= 126 for character in text):
        return "redacted"
    if _SECRETISH.search(text) is not None:
        return "redacted"
    return text


def capture_operator_alert_payload(
    *,
    venue: str,
    run_id: str,
    status: str,
    error: BaseException | None = None,
    host: str | None = None,
    ts_utc: str | None = None,
) -> dict[str, str]:
    """Build the secret-free operator alert payload."""

    error_class = type(error).__name__ if error is not None else "unknown"
    error_message = (
        sanitize_alert_error_message(str(error)) if error is not None else "terminal_failure"
    )
    return {
        "venue": venue,
        "run_id": run_id,
        "status": status,
        "error_class": error_class,
        "error_message": error_message,
        "host": host if host is not None else socket.gethostname(),
        "ts_utc": (
            ts_utc if ts_utc is not None else datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        ),
    }


def _default_webhook_post(url: str, body: bytes, timeout_seconds: float) -> None:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response.read(64)


def emit_capture_operator_alert(
    *,
    venue: str,
    run_id: str,
    status: str,
    error: BaseException | None = None,
    host: str | None = None,
    environ: Mapping[str, str] | None = None,
    webhook_poster: WebhookPoster | None = None,
    timeout_seconds: float = CAPTURE_ALERT_WEBHOOK_TIMEOUT_SECONDS,
) -> dict[str, str] | None:
    """Log one capture_operator_alert and optionally POST once to the webhook.

    Returns the payload when an alert was emitted, else None. Never raises.
    """

    if status != "FAILED":
        return None
    try:
        payload = capture_operator_alert_payload(
            venue=venue,
            run_id=run_id,
            status=status,
            error=error,
            host=host,
        )
        capture_logger().error(
            "capture_operator_alert venue=%s run_id=%s status=%s error_class=%s "
            "error_message=%s host=%s ts_utc=%s",
            payload["venue"],
            payload["run_id"],
            payload["status"],
            payload["error_class"],
            payload["error_message"],
            payload["host"],
            payload["ts_utc"],
        )
        source = os.environ if environ is None else environ
        webhook_url = source.get(CAPTURE_ALERT_WEBHOOK_ENV, "").strip()
        if webhook_url:
            body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
            poster = webhook_poster if webhook_poster is not None else _default_webhook_post
            try:
                poster(webhook_url, body, float(timeout_seconds))
            except (TimeoutError, urllib.error.URLError, OSError, ValueError) as post_error:
                capture_logger().warning(
                    "capture_operator_alert_webhook_failed error_class=%s",
                    type(post_error).__name__,
                )
            except Exception as post_error:
                capture_logger().warning(
                    "capture_operator_alert_webhook_failed error_class=%s",
                    type(post_error).__name__,
                )
        return payload
    except Exception as alert_error:
        logging.getLogger(__name__).warning(
            "capture_operator_alert_emit_failed error_class=%s",
            type(alert_error).__name__,
        )
        return None
