"""Event-driven operator alerts for terminal PAPER capture failures.

Emits exactly once on fail-closed stop. No interval watchdogs or polling cron.
Optional webhook via CAPTURE_ALERT_WEBHOOK_URL (Grok Bot / CoS capture-fail).
When CAPTURE_ALERT_WEBHOOK_AUTHORIZATION is set, it is sent as the Authorization
header and never logged. Delivery retries are bounded; a failed POST is logged
and does not raise into the capture writer.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from .capture_observability import capture_logger

CAPTURE_ALERT_WEBHOOK_ENV: Final = "CAPTURE_ALERT_WEBHOOK_URL"
CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV: Final = "CAPTURE_ALERT_WEBHOOK_AUTHORIZATION"
CAPTURE_ALERT_WEBHOOK_TIMEOUT_SECONDS: Final = 2.0
CAPTURE_ALERT_WEBHOOK_ATTEMPTS: Final = 3
CAPTURE_ALERT_WEBHOOK_RETRY_BACKOFF_SECONDS: Final = (0.25, 0.5)
_MAX_ERROR_MESSAGE_CHARS: Final = 240
_SECRETISH: Final = re.compile(
    r"(?i)(?:token|secret|password|passwd|authorization|api[-_]?key|"
    r"access[-_]?key|private[-_]?key|signature|bearer)"
    r"|[A-Za-z0-9+/_-]{24,}"
)

type WebhookPoster = Callable[[str, bytes, float, Mapping[str, str]], int]


class WebhookDeliveryError(Exception):
    """Secret-free webhook failure. ``str(self)`` is only the error class name."""

    def __init__(self, error_class: str, *, http_status: int | None = None) -> None:
        super().__init__(error_class)
        self.error_class = error_class
        self.http_status = http_status


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


def _printable_header_value(value: str) -> bool:
    return bool(value) and all(32 <= ord(character) <= 126 for character in value)


def capture_alert_webhook_headers(source: Mapping[str, str]) -> dict[str, str]:
    """Build the POST headers. Authorization is included only when it is safe to send."""

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    raw = source.get(CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV, "")
    if type(raw) is not str:
        return headers
    authorization = raw.strip()
    if not authorization:
        return headers
    if not _printable_header_value(authorization):
        raise ValueError("webhook authorization header is not printable")
    headers["Authorization"] = authorization
    return headers


def _webhook_status_is_retryable(http_status: int | None) -> bool:
    """Retry transport failures and transient HTTP statuses. Do not retry auth rejects."""

    if http_status is None:
        return True
    if http_status in {408, 429}:
        return True
    return 500 <= http_status <= 599


def _retry_delay(attempt: int, backoff_seconds: Sequence[float]) -> float:
    if not backoff_seconds:
        return 0.0
    index = min(max(attempt - 1, 0), len(backoff_seconds) - 1)
    delay = backoff_seconds[index]
    if type(delay) not in (int, float):
        return 0.0
    seconds = float(delay)
    if seconds < 0:
        return 0.0
    return seconds


def _default_webhook_post(
    url: str,
    body: bytes,
    timeout_seconds: float,
    headers: Mapping[str, str],
) -> int:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers=dict(headers),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response.read(64)
            status = getattr(response, "status", None)
    except urllib.error.HTTPError as error:
        status_code = error.code if type(error.code) is int else None
        error.close()
        raise WebhookDeliveryError("HTTPError", http_status=status_code) from None
    except urllib.error.URLError as error:
        reason = error.reason
        error_class = type(reason).__name__ if isinstance(reason, BaseException) else "URLError"
        raise WebhookDeliveryError(error_class) from None
    if type(status) is not int:
        return 0
    return status


def _log_webhook_failure(error: WebhookDeliveryError, *, attempts: int) -> None:
    if error.http_status is None:
        capture_logger().warning(
            "capture_operator_alert_webhook_failed error_class=%s attempts=%s",
            error.error_class,
            attempts,
        )
        return
    capture_logger().warning(
        "capture_operator_alert_webhook_failed error_class=%s http_status=%s attempts=%s",
        error.error_class,
        error.http_status,
        attempts,
    )


def _post_capture_alert_webhook(
    *,
    url: str,
    payload: Mapping[str, str],
    source: Mapping[str, str],
    webhook_poster: WebhookPoster | None,
    timeout_seconds: float,
    attempts: int,
    retry_backoff_seconds: Sequence[float],
) -> None:
    """POST once per attempt. Failures are logged and never raised."""

    authorization_rejected = False
    try:
        headers = capture_alert_webhook_headers(source)
    except ValueError:
        authorization_rejected = True
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        }
    if authorization_rejected:
        capture_logger().warning("capture_operator_alert_webhook_authorization_rejected")
    elif "Authorization" not in headers:
        capture_logger().warning("capture_operator_alert_webhook_authorization_unset")
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    poster = webhook_poster if webhook_poster is not None else _default_webhook_post
    budget = attempts if type(attempts) is int and attempts >= 1 else 1
    delivered: int | None = None
    last_error: WebhookDeliveryError | None = None
    used_attempts = 0
    for attempt in range(1, budget + 1):
        used_attempts = attempt
        try:
            delivered = int(poster(url, body, float(timeout_seconds), headers))
            last_error = None
            break
        except WebhookDeliveryError as post_error:
            last_error = post_error
        except (TimeoutError, urllib.error.URLError, OSError, ValueError) as post_error:
            last_error = WebhookDeliveryError(type(post_error).__name__)
        except Exception as post_error:
            last_error = WebhookDeliveryError(type(post_error).__name__)
        if not _webhook_status_is_retryable(last_error.http_status) or attempt == budget:
            break
        delay = _retry_delay(attempt, retry_backoff_seconds)
        if delay > 0:
            time.sleep(delay)
    if delivered is not None:
        capture_logger().info(
            "capture_operator_alert_webhook_ok http_status=%s attempts=%s",
            delivered,
            used_attempts,
        )
        return
    if last_error is not None:
        _log_webhook_failure(last_error, attempts=used_attempts)


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
    attempts: int = CAPTURE_ALERT_WEBHOOK_ATTEMPTS,
    retry_backoff_seconds: Sequence[float] = CAPTURE_ALERT_WEBHOOK_RETRY_BACKOFF_SECONDS,
) -> dict[str, str] | None:
    """Log one capture_operator_alert and optionally POST the webhook.

    Returns the payload when an alert was emitted, else None. Never raises.
    Webhook delivery uses a short per-attempt timeout and a bounded retry budget.
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
            _post_capture_alert_webhook(
                url=webhook_url,
                payload=payload,
                source=source,
                webhook_poster=webhook_poster,
                timeout_seconds=timeout_seconds,
                attempts=attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
        return payload
    except Exception as alert_error:
        logging.getLogger(__name__).warning(
            "capture_operator_alert_emit_failed error_class=%s",
            type(alert_error).__name__,
        )
        return None
