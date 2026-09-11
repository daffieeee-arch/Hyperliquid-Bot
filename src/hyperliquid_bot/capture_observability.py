"""Shared capture-health and transport-marker helpers.

Public PAPER collectors only. Never logs payloads or secrets. Sanitized
WebSocket close-frame reason text is allowed; raw exception messages are not.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, cast

import duckdb

DURATION_CONTRACT: Final = (
    "duration_seconds is the requested window. elapsed_seconds is wall-clock "
    "time until stop. OPERATOR_STOP with elapsed_seconds < duration_seconds is "
    "an operator interrupt, not a completed tape."
)
CAPTURE_LOGGER_NAME: Final = "hyperliquid_bot.capture"
_INTEGRITY_GAP_EVENTS: Final = frozenset(
    {
        "sequence_gap",
        "sequence_error",
        "schema_error",
        "snapshot_error",
        "subscription_error",
        "liveness_error",
        "truncation_error",
        "buffer_overflow",
        "payload_size_error",
    }
)
_INTEGRITY_EVENT_SQL: Final = ", ".join(f"'{event}'" for event in sorted(_INTEGRITY_GAP_EVENTS))
RECONNECT_CLUSTER_GAP_NS: Final = 5_000_000_000
_MAX_CLOSE_REASON_CHARS: Final = 120
_SECRETISH_CLOSE_REASON: Final = re.compile(
    r"(?i)(?:token|secret|password|passwd|authorization|api[-_]?key|"
    r"access[-_]?key|private[-_]?key|signature|bearer)"
    r"|[A-Za-z0-9+/_-]{24,}"
)
DISCONNECT_LOG_SUFFIX: Final = (
    "exception_class=%s close_code=%s close_code_rcvd=%s close_code_sent=%s "
    "close_reason_rcvd=%s close_reason_sent=%s errno=%s"
)


def require_elapsed_seconds(value: object) -> float:
    """Accept a non-negative finite elapsed duration distinct from a request."""

    if type(value) not in (int, float):
        raise TypeError("elapsed_seconds must be a built-in number.")
    elapsed = float(cast(int | float, value))
    if not math.isfinite(elapsed) or elapsed < 0.0:
        raise ValueError("elapsed_seconds must be a non-negative finite number.")
    return elapsed


def elapsed_from_report(report: dict[str, object], fallback: float | None = None) -> float:
    """Read measured elapsed_seconds from a capture report."""

    raw = report.get("elapsed_seconds", fallback)
    return require_elapsed_seconds(raw)


def sanitize_close_reason(value: object) -> str | None:
    """Keep short printable venue close text. Redact credential-shaped values."""

    if type(value) is not str:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > _MAX_CLOSE_REASON_CHARS:
        text = text[:_MAX_CLOSE_REASON_CHARS]
    if not all(32 <= ord(character) <= 126 for character in text):
        return "redacted"
    if _SECRETISH_CLOSE_REASON.search(text) is not None:
        return "redacted"
    return text


def disconnect_log_values(fields: Mapping[str, int | str]) -> tuple[object, ...]:
    """Positional values for DISCONNECT_LOG_SUFFIX. Never include raw exceptions."""

    return (
        fields.get("exception_class"),
        fields.get("close_code"),
        fields.get("close_code_rcvd"),
        fields.get("close_code_sent"),
        fields.get("close_reason_rcvd"),
        fields.get("close_reason_sent"),
        fields.get("errno"),
    )


def count_wall_clock_clusters(
    timestamps_ns: Sequence[int],
    *,
    gap_ns: int = RECONNECT_CLUSTER_GAP_NS,
) -> int:
    """Count unique reconnect bursts. Gaps larger than gap_ns start a new cluster."""

    if type(gap_ns) is not int or gap_ns < 0:
        raise ValueError("gap_ns must be a non-negative integer.")
    if not timestamps_ns:
        return 0
    ordered = sorted(int(stamp) for stamp in timestamps_ns)
    clusters = 1
    previous = ordered[0]
    for stamp in ordered[1:]:
        if stamp - previous > gap_ns:
            clusters += 1
        previous = stamp
    return clusters


def transport_exception_fields(error: BaseException) -> dict[str, int | str]:
    """Persist close codes, sanitized reasons, errno, and exception class.

    Distinguishes rcvd (peer) vs sent (local) close frames. Walks __cause__ /
    __context__ so a wrapper OSError cannot hide ConnectionClosedError. Callers
    must not keep the exception object in frame locals across a later raise.
    """

    fields: dict[str, int | str] = {"exception_class": type(error).__name__}
    received_code: int | None = None
    sent_code: int | None = None
    received_reason: str | None = None
    sent_reason: str | None = None
    errno_value: int | None = None
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if errno_value is None:
            raw_errno = getattr(current, "errno", None)
            if isinstance(raw_errno, int):
                errno_value = int(raw_errno)
        received = getattr(current, "rcvd", None)
        sent = getattr(current, "sent", None)
        if received is not None and received_code is None:
            code = getattr(received, "code", None)
            if isinstance(code, int):
                received_code = int(code)
            reason = sanitize_close_reason(getattr(received, "reason", None))
            if reason is not None:
                received_reason = reason
        if sent is not None and sent_code is None:
            code = getattr(sent, "code", None)
            if isinstance(code, int):
                sent_code = int(code)
            reason = sanitize_close_reason(getattr(sent, "reason", None))
            if reason is not None:
                sent_reason = reason
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    if received_code is not None:
        fields["close_code_rcvd"] = received_code
        fields["close_code"] = received_code
    if sent_code is not None:
        fields["close_code_sent"] = sent_code
        if "close_code" not in fields:
            fields["close_code"] = sent_code
    if received_reason is not None:
        fields["close_reason_rcvd"] = received_reason
    if sent_reason is not None:
        fields["close_reason_sent"] = sent_reason
    if errno_value is not None:
        fields["errno"] = errno_value
    return fields


def capture_log_path(run_dir: Path, run_id: str) -> Path:
    """Documented WSL/VPS collector log inside the reconstructable run directory."""

    return run_dir / f"capture-{run_id}.log"


def configure_capture_logger(log_path: Path | None = None) -> logging.Logger:
    """INFO logger for session/disconnect/reconnect. No payloads or secrets."""

    logger = logging.getLogger(CAPTURE_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        if log_path is not None and not any(
            isinstance(handler, logging.FileHandler)
            and Path(getattr(handler, "baseFilename", "")) == log_path.resolve()
            for handler in logger.handlers
        ):
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(_capture_log_formatter())
            logger.addHandler(file_handler)
        return logger

    formatter = _capture_log_formatter()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger


def _capture_log_formatter() -> logging.Formatter:
    return logging.Formatter("%(asctime)s %(levelname)s %(message)s")


def capture_logger() -> logging.Logger:
    return logging.getLogger(CAPTURE_LOGGER_NAME)


def add_transport_counts(
    connection: duckdb.DuckDBPyConnection,
    report: dict[str, object],
    *,
    gap_event: str,
    reconnect_event: str,
) -> dict[str, object]:
    """Fill transport gaps/reconnects. Integrity events stay out of `gaps`."""

    if gap_event in _INTEGRITY_GAP_EVENTS:
        raise ValueError("transport gap_event must not be an integrity event name.")
    profile_sql = """
        coalesce(
            json_extract_string(marker_json, '$.transport_profile'),
            json_extract_string(marker_json, '$.stream'),
            'unknown'
        )
    """
    gap_rows = connection.execute(
        f"""
        SELECT {profile_sql} AS transport_profile, count(*)
        FROM data_quality_events
        WHERE event = ?
        GROUP BY 1
        ORDER BY 1
        """,
        [gap_event],
    ).fetchall()
    reconnect_rows = connection.execute(
        f"""
        SELECT {profile_sql} AS transport_profile, count(*)
        FROM sessions
        WHERE event = ?
        GROUP BY 1
        ORDER BY 1
        """,
        [reconnect_event],
    ).fetchall()
    integrity_row = connection.execute(
        f"""
        SELECT count(*)
        FROM data_quality_events
        WHERE event IN ({_INTEGRITY_EVENT_SQL})
        """
    ).fetchone()
    if integrity_row is None:
        raise RuntimeError("DuckDB did not return integrity-event aggregates.")

    gap_by_profile = {str(profile): int(count) for profile, count in gap_rows}
    reconnect_by_profile = {str(profile): int(count) for profile, count in reconnect_rows}
    cluster_by_profile, reconnect_clusters = _reconnect_cluster_counts(
        connection,
        reconnect_event=reconnect_event,
        profile_sql=profile_sql,
    )
    names = sorted(set(gap_by_profile) | set(reconnect_by_profile) | set(cluster_by_profile))
    profiles = [
        {
            "transport_profile": name,
            "gaps": gap_by_profile.get(name, 0),
            "reconnects": reconnect_by_profile.get(name, 0),
            "reconnect_clusters": cluster_by_profile.get(name, 0),
        }
        for name in names
    ]
    report["gaps"] = sum(gap_by_profile.get(name, 0) for name in names)
    report["reconnects"] = sum(reconnect_by_profile.get(name, 0) for name in names)
    report["reconnect_clusters"] = reconnect_clusters
    report["transport_profiles"] = profiles
    report["integrity_events"] = int(integrity_row[0])
    return report


def _session_column_names(connection: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(row[0]) for row in connection.execute("DESCRIBE sessions").fetchall()}


def _reconnect_cluster_counts(
    connection: duckdb.DuckDBPyConnection,
    *,
    reconnect_event: str,
    profile_sql: str,
) -> tuple[dict[str, int], int]:
    """Unique wall-clock reconnect bursts when sessions.received_utc_ns exists."""

    if "received_utc_ns" not in _session_column_names(connection):
        return {}, 0
    rows = connection.execute(
        f"""
        SELECT received_utc_ns, {profile_sql} AS transport_profile
        FROM sessions
        WHERE event = ?
        ORDER BY 1
        """,
        [reconnect_event],
    ).fetchall()
    stamps_by_profile: dict[str, list[int]] = {}
    all_stamps: list[int] = []
    for stamp, profile in rows:
        try:
            stamp_ns = int(stamp)
        except (TypeError, ValueError):
            continue
        name = str(profile)
        stamps_by_profile.setdefault(name, []).append(stamp_ns)
        all_stamps.append(stamp_ns)
    return (
        {name: count_wall_clock_clusters(stamps) for name, stamps in stamps_by_profile.items()},
        count_wall_clock_clusters(all_stamps),
    )


def attach_observability_health(
    health: dict[str, object],
    report: dict[str, object],
    *,
    elapsed_seconds: float,
) -> dict[str, object]:
    """Add elapsed/profile fields without changing requested duration_seconds."""

    health["elapsed_seconds"] = require_elapsed_seconds(elapsed_seconds)
    health["duration_contract"] = DURATION_CONTRACT
    profiles = report.get("transport_profiles")
    if isinstance(profiles, list):
        health["transport_profiles"] = profiles
    integrity_events = report.get("integrity_events")
    if type(integrity_events) is int:
        health["integrity_events"] = integrity_events
    reconnect_clusters = report.get("reconnect_clusters")
    if type(reconnect_clusters) is int:
        health["reconnect_clusters"] = reconnect_clusters
    return health
