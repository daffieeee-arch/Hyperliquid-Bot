"""Shared capture-health and transport-marker helpers.

Public PAPER collectors only. Never logs payloads, close reasons, or secrets.
"""

from __future__ import annotations

import logging
import math
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
_INTEGRITY_EVENT_SQL: Final = ", ".join(
    f"'{event}'" for event in sorted(_INTEGRITY_GAP_EVENTS)
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


def transport_exception_fields(error: BaseException) -> dict[str, int | str]:
    """Persist close code and exception class only. Never include reason text.

    Callers must not keep the exception object in frame locals across a later
    raise; extract these fields and drop the exception reference.
    """

    fields: dict[str, int | str] = {"exception_class": type(error).__name__}
    received = getattr(error, "rcvd", None)
    sent = getattr(error, "sent", None)
    close_code = getattr(received, "code", None) if received is not None else None
    if not isinstance(close_code, int):
        close_code = getattr(sent, "code", None) if sent is not None else None
    if isinstance(close_code, int):
        fields["close_code"] = close_code
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
    names = sorted(set(gap_by_profile) | set(reconnect_by_profile))
    profiles = [
        {
            "transport_profile": name,
            "gaps": gap_by_profile.get(name, 0),
            "reconnects": reconnect_by_profile.get(name, 0),
        }
        for name in names
    ]
    report["gaps"] = sum(gap_by_profile.get(name, 0) for name in names)
    report["reconnects"] = sum(reconnect_by_profile.get(name, 0) for name in names)
    report["transport_profiles"] = profiles
    report["integrity_events"] = int(integrity_row[0])
    return report


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
    return health
