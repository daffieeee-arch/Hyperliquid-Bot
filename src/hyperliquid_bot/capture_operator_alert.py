"""Operator alerts for terminal PAPER capture failures.

The collector emits once on a fail-closed stop. A separate checker
(``capture_alert_watch``) can send the same payload when the process is already
dead. Optional webhook via ``CAPTURE_ALERT_WEBHOOK_URL``, also read from
``~/.config/hyperliquid-bot/capture-alert.env`` when the process environment
does not already set it. ``CAPTURE_ALERT_WEBHOOK_AUTHORIZATION`` is sent as the
Authorization header and never logged. Delivery retries are bounded. A failed
POST is logged and does not raise into the capture writer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .capture_live_status import process_code_version
from .capture_observability import capture_logger

CAPTURE_ALERT_WEBHOOK_ENV: Final = "CAPTURE_ALERT_WEBHOOK_URL"
CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV: Final = "CAPTURE_ALERT_WEBHOOK_AUTHORIZATION"
CAPTURE_ALERT_ENV_FILE_ENV: Final = "CAPTURE_ALERT_ENV_FILE"
CAPTURE_ALERT_RECEIPT_DIR_ENV: Final = "CAPTURE_ALERT_RECEIPT_DIR"
CAPTURE_ALERT_WEBHOOK_TIMEOUT_SECONDS: Final = 5.0
CAPTURE_ALERT_WEBHOOK_ATTEMPTS: Final = 3
CAPTURE_ALERT_WEBHOOK_RETRY_BACKOFF_SECONDS: Final = (0.5, 1.5)
# Worst-case webhook budget: per-attempt timeout, plus the retry sleeps, plus slack.
CAPTURE_ALERT_LANE_DRAIN_SECONDS: Final = (
    CAPTURE_ALERT_WEBHOOK_TIMEOUT_SECONDS * CAPTURE_ALERT_WEBHOOK_ATTEMPTS
    + sum(CAPTURE_ALERT_WEBHOOK_RETRY_BACKOFF_SECONDS)
    + 0.5
)
_ALERT_STATES: Final = frozenset({"FAILED", "DEAD", "STALE", "TEST"})
_ALLOWED_ENV_KEYS: Final = frozenset(
    {CAPTURE_ALERT_WEBHOOK_ENV, CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV}
)
_MAX_ERROR_MESSAGE_CHARS: Final = 240
_SECRETISH: Final = re.compile(
    r"(?i)(?:token|secret|password|passwd|authorization|api[-_]?key|"
    r"access[-_]?key|private[-_]?key|signature|bearer)"
    r"|[A-Za-z0-9+/_-]{24,}"
)
_AMSTERDAM: ZoneInfo | None
try:
    _AMSTERDAM = ZoneInfo("Europe/Amsterdam")
except ZoneInfoNotFoundError:
    _AMSTERDAM = None

type WebhookPoster = Callable[[str, bytes, float, Mapping[str, str]], int]
type HealthWriter = Callable[[], None]


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


def capture_alert_idempotency_key(
    *,
    venue: str,
    run_id: str,
    state: str,
    reason: str,
    nonce: str | None = None,
) -> str:
    """Stable key for one failure. A test nonce makes each operator test distinct."""

    material = "\n".join((venue, run_id, state, reason, nonce or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def format_alert_timestamps(moment: datetime | None) -> tuple[str, str]:
    """UTC plus Europe/Amsterdam. Unknown moments stay the literal ``unknown``."""

    if moment is None:
        return "unknown", "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    utc_text = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if _AMSTERDAM is None:
        return utc_text, "unknown"
    local = moment.astimezone(_AMSTERDAM)
    offset = local.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return utc_text, local.strftime("%Y-%m-%dT%H:%M:%S") + offset


def default_capture_alert_env_path() -> Path:
    return Path.home() / ".config" / "hyperliquid-bot" / "capture-alert.env"


def default_capture_alert_receipt_dir() -> Path:
    return Path.home() / ".local" / "state" / "hyperliquid-bot" / "capture-alert-receipts"


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def load_capture_alert_env_file(path: Path) -> dict[str, str]:
    """Read only the webhook URL and authorization. Never logs file contents."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        capture_logger().warning("capture_alert_env_unreadable")
        return {}
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        name = key.strip()
        if name not in _ALLOWED_ENV_KEYS:
            continue
        values[name] = _unquote_env_value(raw_value.strip())
    return values


def _capture_alert_env_file(source: Mapping[str, str]) -> Path | None:
    override = source.get(CAPTURE_ALERT_ENV_FILE_ENV, "").strip()
    if override:
        return Path(override)
    # Pytest must not read an operator file and POST the real webhook.
    if source.get("PYTEST_CURRENT_TEST", "").strip():
        return None
    candidate = default_capture_alert_env_path()
    if candidate.is_file():
        return candidate
    return None


def resolve_capture_alert_source(environ: Mapping[str, str] | None) -> dict[str, str]:
    """Process environment wins. Missing keys are filled from the env file.

    An explicit ``environ`` mapping is used as-is so tests stay hermetic.
    """

    if environ is not None:
        allowed = _ALLOWED_ENV_KEYS | {CAPTURE_ALERT_ENV_FILE_ENV, "PYTEST_CURRENT_TEST"}
        return {key: value for key, value in environ.items() if key in allowed}
    source = os.environ
    merged: dict[str, str] = {}
    for key in _ALLOWED_ENV_KEYS:
        value = source.get(key, "")
        if type(value) is str and value.strip():
            merged[key] = value
    path = _capture_alert_env_file(source)
    if path is None:
        return merged
    for key, value in load_capture_alert_env_file(path).items():
        if key not in merged and value.strip():
            merged[key] = value
    return merged


def resolve_receipt_dir(explicit: Path | None = None) -> Path | None:
    """Where successful deliveries are recorded. Pytest skips the home default."""

    if explicit is not None:
        return explicit
    override = os.environ.get(CAPTURE_ALERT_RECEIPT_DIR_ENV, "").strip()
    if override:
        return Path(override)
    if os.environ.get("PYTEST_CURRENT_TEST", "").strip():
        return None
    return default_capture_alert_receipt_dir()


def _receipt_path(directory: Path, key: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        return None
    return directory / f"{key}.json"


def alert_receipt_exists(key: str, directory: Path | None) -> bool:
    if directory is None:
        return False
    path = _receipt_path(directory, key)
    if path is None:
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def write_alert_receipt(
    *,
    directory: Path | None,
    key: str,
    venue: str,
    run_id: str,
    state: str,
    http_status: int,
) -> None:
    """Record a 2xx delivery. The file contains no URL and no authorization."""

    if directory is None:
        return
    path = _receipt_path(directory, key)
    if path is None:
        return
    body = {
        "idempotency_key": key,
        "venue": venue,
        "run_id": run_id,
        "state": state,
        "http_status": http_status,
        "delivered_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(body, ensure_ascii=True, sort_keys=True) + "\n")
    except FileExistsError:
        return
    except OSError:
        capture_logger().warning("capture_operator_alert_receipt_write_failed")


def newest_parquet_write(raw_dir: Path | None) -> datetime | None:
    """Newest published part mtime. Readers ignore hidden partial files."""

    if raw_dir is None:
        return None
    try:
        if not raw_dir.is_dir():
            return None
        candidates = list(raw_dir.glob("part-*.parquet"))
    except OSError:
        return None
    latest: float | None = None
    for path in candidates:
        try:
            if not path.is_file() or path.is_symlink():
                continue
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest:
            latest = mtime
    if latest is None:
        return None
    return datetime.fromtimestamp(latest, tz=UTC)


def capture_operator_alert_payload(
    *,
    venue: str,
    run_id: str,
    status: str,
    error: BaseException | None = None,
    host: str | None = None,
    ts_utc: str | None = None,
    reason: str | None = None,
    last_write: datetime | None = None,
    code_version: str | None = None,
    alert_kind: str = "failure",
    idempotency_nonce: str | None = None,
    error_class: str | None = None,
) -> dict[str, str]:
    """Build the secret-free operator alert payload."""

    if error_class is not None:
        resolved_error_class = error_class
    elif error is not None:
        resolved_error_class = type(error).__name__
    else:
        resolved_error_class = "alert"
    if reason is None:
        error_message = (
            sanitize_alert_error_message(str(error)) if error is not None else "terminal_failure"
        )
    else:
        error_message = sanitize_alert_error_message(reason)
    if ts_utc is None:
        ts_text = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        ts_text = ts_utc
    last_write_utc, last_write_amsterdam = format_alert_timestamps(last_write)
    version = code_version if code_version is not None else process_code_version()
    state = status
    kind = alert_kind if alert_kind in {"failure", "test"} else "failure"
    return {
        "venue": venue,
        "run_id": run_id,
        "state": state,
        "status": state,
        "reason": error_message,
        "error_class": resolved_error_class,
        "error_message": error_message,
        "last_write_utc": last_write_utc,
        "last_write_amsterdam": last_write_amsterdam,
        "host": host if host is not None else socket.gethostname(),
        "code_version": version,
        "ts_utc": ts_text,
        "idempotency_key": capture_alert_idempotency_key(
            venue=venue,
            run_id=run_id,
            state=state,
            reason=error_message,
            nonce=idempotency_nonce,
        ),
        "alert_kind": kind,
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
) -> int | None:
    """POST until a non-exception response or the budget ends. Never raises."""

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
    idempotency_key = payload.get("idempotency_key", "")
    if _printable_header_value(idempotency_key):
        headers["Idempotency-Key"] = idempotency_key
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
        return delivered
    if last_error is not None:
        _log_webhook_failure(last_error, attempts=used_attempts)
    return None


class CaptureAlertLane:
    """Run webhook delivery off the capture event loop.

    The caller records dedup state, then submits the emit. ``shutdown`` waits
    up to ``timeout_seconds`` so a normal retry can finish, then returns.
    Threads are daemons, so a webhook that ignores its timeout cannot keep the
    process alive after that bound. A delivery that does not finish is not
    treated as sent; the terminal hook may try again.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._closed = False
        self._failure: BaseException | None = None

    def submit(self, emit: Callable[[], None]) -> None:
        def run() -> None:
            try:
                emit()
            except Exception as error:
                with self._lock:
                    if self._failure is None:
                        self._failure = error
                capture_logger().warning(
                    "capture_operator_alert_lane_failed error_class=%s",
                    type(error).__name__,
                )

        with self._lock:
            if self._closed:
                capture_logger().warning("capture_operator_alert_dropped_after_shutdown")
                return
            thread = threading.Thread(target=run, name="capture-alert", daemon=True)
            self._threads.append(thread)
            thread.start()

    def shutdown(self, *, timeout_seconds: float) -> None:
        with self._lock:
            self._closed = True
            threads = tuple(self._threads)
        if type(timeout_seconds) not in (int, float):
            timeout_seconds = 0.0
        seconds = max(0.0, float(timeout_seconds))
        deadline = time.monotonic() + seconds
        timed_out = False
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = thread.is_alive() or timed_out
                break
            thread.join(timeout=remaining)
            if thread.is_alive():
                timed_out = True
                break
        if timed_out:
            capture_logger().warning("capture_operator_alert_shutdown_timeout")
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure


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
    reason: str | None = None,
    last_write: datetime | None = None,
    code_version: str | None = None,
    alert_kind: str = "failure",
    idempotency_nonce: str | None = None,
    error_class: str | None = None,
    receipt_dir: Path | None = None,
    force: bool = False,
) -> dict[str, str] | None:
    """Log one capture_operator_alert and optionally POST the webhook.

    Returns the payload when an alert was emitted, else None. Never raises.
    ``webhook_delivered`` is ``yes`` only after a non-exception POST response
    or when a receipt already records that key. It is not part of the POST body.
    """

    if status not in _ALERT_STATES:
        return None
    try:
        payload = capture_operator_alert_payload(
            venue=venue,
            run_id=run_id,
            status=status,
            error=error,
            host=host,
            reason=reason,
            last_write=last_write,
            code_version=code_version,
            alert_kind=alert_kind,
            idempotency_nonce=idempotency_nonce,
            error_class=error_class,
        )
        directory = resolve_receipt_dir(receipt_dir)
        key = payload["idempotency_key"]
        if not force and alert_receipt_exists(key, directory):
            capture_logger().info(
                "capture_operator_alert_already_delivered idempotency_key=%s",
                key,
            )
            delivered = dict(payload)
            delivered["webhook_delivered"] = "yes"
            return delivered
        capture_logger().error(
            "capture_operator_alert venue=%s run_id=%s status=%s state=%s "
            "error_class=%s reason=%s host=%s code_version=%s "
            "last_write_utc=%s last_write_amsterdam=%s ts_utc=%s idempotency_key=%s",
            payload["venue"],
            payload["run_id"],
            payload["status"],
            payload["state"],
            payload["error_class"],
            payload["reason"],
            payload["host"],
            payload["code_version"],
            payload["last_write_utc"],
            payload["last_write_amsterdam"],
            payload["ts_utc"],
            payload["idempotency_key"],
        )
        source = resolve_capture_alert_source(environ)
        webhook_url = source.get(CAPTURE_ALERT_WEBHOOK_ENV, "").strip()
        result = dict(payload)
        if not webhook_url:
            capture_logger().warning("capture_operator_alert_webhook_unset")
            result["webhook_delivered"] = "no"
            return result
        http_status = _post_capture_alert_webhook(
            url=webhook_url,
            payload=payload,
            source=source,
            webhook_poster=webhook_poster,
            timeout_seconds=timeout_seconds,
            attempts=attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        if http_status is None:
            result["webhook_delivered"] = "no"
            return result
        write_alert_receipt(
            directory=directory,
            key=key,
            venue=payload["venue"],
            run_id=payload["run_id"],
            state=payload["state"],
            http_status=http_status,
        )
        result["webhook_delivered"] = "yes"
        return result
    except Exception as alert_error:
        logging.getLogger(__name__).warning(
            "capture_operator_alert_emit_failed error_class=%s",
            type(alert_error).__name__,
        )
        return None


def write_health_then_alert(
    *,
    should_write_health: bool,
    write_health: HealthWriter,
    status: str,
    venue: str,
    run_id: str,
    error: BaseException | None,
    raw_dir: Path | None = None,
    skip_alert: bool = False,
) -> None:
    """Write terminal health, then alert. A health-write error cannot skip the alert."""

    health_error: Exception | None = None
    try:
        if should_write_health:
            write_health()
    except Exception as write_error:
        health_error = write_error
        capture_logger().warning(
            "capture_health_write_failed error_class=%s",
            type(write_error).__name__,
        )
    if status == "FAILED" and not skip_alert:
        emit_capture_operator_alert(
            venue=venue,
            run_id=run_id,
            status=status,
            error=error,
            last_write=newest_parquet_write(raw_dir),
        )
    if health_error is not None:
        raise health_error
