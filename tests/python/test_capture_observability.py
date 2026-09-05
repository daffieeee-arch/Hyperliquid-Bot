"""Offline tests for capture-health elapsed/profile helpers and close-code markers."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close, CloseCode

from hyperliquid_bot.capture_observability import (
    DURATION_CONTRACT,
    add_transport_counts,
    attach_observability_health,
    capture_log_path,
    configure_capture_logger,
    elapsed_from_report,
    require_elapsed_seconds,
    transport_exception_fields,
)


def test_elapsed_seconds_is_separate_from_requested_duration() -> None:
    assert require_elapsed_seconds(12.5) == 12.5
    assert elapsed_from_report({"elapsed_seconds": 41.0}) == 41.0
    with pytest.raises(TypeError):
        require_elapsed_seconds("12")
    with pytest.raises(ValueError):
        require_elapsed_seconds(-0.1)
    health = attach_observability_health(
        {"duration_seconds": 259200.0, "status": "OPERATOR_STOP"},
        {"elapsed_seconds": 41.0, "transport_profiles": [], "integrity_events": 0},
        elapsed_seconds=41.0,
    )
    assert health["duration_seconds"] == 259200.0
    assert health["elapsed_seconds"] == 41.0
    assert health["duration_contract"] == DURATION_CONTRACT
    assert health["elapsed_seconds"] < health["duration_seconds"]


def test_transport_exception_fields_keep_code_and_hide_reason() -> None:
    closed = ConnectionClosedError(Close(CloseCode.GOING_AWAY, "do-not-store"), None)
    fields = transport_exception_fields(closed)
    assert fields["exception_class"] == "ConnectionClosedError"
    assert int(fields["close_code"]) == 1001
    assert "do-not-store" not in {str(value) for value in fields.values()}
    assert "reason" not in fields
    generic = transport_exception_fields(ConnectionError("synthetic disconnect"))
    assert generic == {"exception_class": "ConnectionError"}


def test_transport_counts_exclude_integrity_events(tmp_path: Path) -> None:
    database = tmp_path / "research.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute("CREATE TABLE raw_records (channel VARCHAR, direction VARCHAR)")
        connection.execute(
            """
            CREATE TABLE data_quality_events AS
            SELECT * FROM (
                VALUES
                    ('gap', '{"event":"gap","transport_profile":"spot"}'),
                    ('gap', '{"event":"gap","transport_profile":"usdm_market"}'),
                    ('sequence_gap', '{"event":"sequence_gap","transport_profile":"spot"}')
            ) AS t(event, marker_json)
            """
        )
        connection.execute(
            """
            CREATE TABLE sessions AS
            SELECT * FROM (
                VALUES
                    ('reconnect', '{"event":"reconnect","transport_profile":"spot"}')
            ) AS t(event, marker_json)
            """
        )
        report: dict[str, object] = {}
        add_transport_counts(connection, report, gap_event="gap", reconnect_event="reconnect")
        assert report["gaps"] == 2
        assert report["reconnects"] == 1
        assert report["integrity_events"] == 1
        raw_profiles = report["transport_profiles"]
        assert isinstance(raw_profiles, list)
        profiles = {item["transport_profile"]: item for item in raw_profiles}
        assert profiles["spot"] == {"transport_profile": "spot", "gaps": 1, "reconnects": 1}
        assert profiles["usdm_market"] == {
            "transport_profile": "usdm_market",
            "gaps": 1,
            "reconnects": 0,
        }
        with pytest.raises(ValueError, match="integrity"):
            add_transport_counts(
                connection,
                {},
                gap_event="sequence_gap",
                reconnect_event="reconnect",
            )
    finally:
        connection.close()


def test_capture_log_is_non_empty_and_payload_free(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log_path = capture_log_path(run_dir, "sample-run")
    logger = configure_capture_logger(log_path)
    logger.info("session_start transport_profile=spot")
    logger.info("disconnect exception_class=ConnectionError close_code=%s", 1001)
    text = log_path.read_text(encoding="utf-8")
    assert text.strip()
    assert "session_start" in text
    assert "ConnectionError" in text
    assert "1001" in text
    assert "api-key" not in text.lower()
    assert "BEGIN PRIVATE KEY" not in text
