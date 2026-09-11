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
    count_wall_clock_clusters,
    elapsed_from_report,
    require_elapsed_seconds,
    sanitize_close_reason,
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


def test_transport_exception_fields_distinguish_rcvd_vs_sent_and_sanitize_reason() -> None:
    received = Close(CloseCode.POLICY_VIOLATION, "Too many requests")
    sent = Close(CloseCode.INTERNAL_ERROR, "keepalive ping timeout")
    closed = ConnectionClosedError(received, sent, True)
    fields = transport_exception_fields(closed)
    assert fields["exception_class"] == "ConnectionClosedError"
    assert int(fields["close_code"]) == 1008
    assert int(fields["close_code_rcvd"]) == 1008
    assert int(fields["close_code_sent"]) == 1011
    assert fields["close_reason_rcvd"] == "Too many requests"
    assert fields["close_reason_sent"] == "keepalive ping timeout"

    secret_close = ConnectionClosedError(
        Close(CloseCode.GOING_AWAY, "token=super-secret-value-do-not-store"),
        None,
    )
    hidden = transport_exception_fields(secret_close)
    assert int(hidden["close_code_rcvd"]) == 1001
    assert hidden["close_reason_rcvd"] == "redacted"
    assert "super-secret-value-do-not-store" not in {str(value) for value in hidden.values()}

    wrapped = OSError("Bitvavo Market Data Pro receive failed.")
    wrapped.__cause__ = ConnectionClosedError(Close(1000, "Ping timeout"), None)
    chained = transport_exception_fields(wrapped)
    assert chained["exception_class"] == "OSError"
    assert int(chained["close_code"]) == 1000
    assert chained["close_reason_rcvd"] == "Ping timeout"

    errno_error = OSError("synthetic transport errno")
    errno_error.errno = 104
    errno_fields = transport_exception_fields(errno_error)
    assert errno_fields["exception_class"] == "OSError"
    assert int(errno_fields["errno"]) == 104

    generic = transport_exception_fields(ConnectionError("synthetic disconnect"))
    assert generic == {"exception_class": "ConnectionError"}
    assert sanitize_close_reason("Too many requests") == "Too many requests"
    assert sanitize_close_reason("api-key=abcd") == "redacted"


def test_wall_clock_reconnect_clusters_group_near_simultaneous_events() -> None:
    assert count_wall_clock_clusters(()) == 0
    assert count_wall_clock_clusters((1_000_000_000,)) == 1
    assert count_wall_clock_clusters((0, 1_000_000_000, 2_000_000_000, 10_000_000_000)) == 2
    with pytest.raises(ValueError, match="non-negative"):
        count_wall_clock_clusters((0,), gap_ns=-1)


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
                    ('sequence_gap', '{"event":"sequence_gap","transport_profile":"spot"}'),
                    ('liveness_error',
                     '{"event":"liveness_error","reason":"required_stream_starved"}')
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
        assert report["integrity_events"] == 2
        raw_profiles = report["transport_profiles"]
        assert isinstance(raw_profiles, list)
        profiles = {item["transport_profile"]: item for item in raw_profiles}
        assert profiles["spot"] == {
            "transport_profile": "spot",
            "gaps": 1,
            "reconnects": 1,
            "reconnect_clusters": 0,
        }
        assert profiles["usdm_market"] == {
            "transport_profile": "usdm_market",
            "gaps": 1,
            "reconnects": 0,
            "reconnect_clusters": 0,
        }
        assert report["reconnect_clusters"] == 0
        with pytest.raises(ValueError, match="integrity"):
            add_transport_counts(
                connection,
                {},
                gap_event="sequence_gap",
                reconnect_event="reconnect",
            )
    finally:
        connection.close()


def test_transport_counts_cluster_near_simultaneous_reconnects(tmp_path: Path) -> None:
    database = tmp_path / "research.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute("CREATE TABLE raw_records (channel VARCHAR)")
        connection.execute("CREATE TABLE data_quality_events (event VARCHAR, marker_json VARCHAR)")
        connection.execute(
            """
            CREATE TABLE sessions AS
            SELECT * FROM (
                VALUES
                    ('reconnect', 0, '{"transport_profile":"l3"}'),
                    ('reconnect', 500_000_000, '{"transport_profile":"public"}'),
                    ('reconnect', 12_000_000_000, '{"transport_profile":"l3"}')
            ) AS t(event, received_utc_ns, marker_json)
            """
        )
        report: dict[str, object] = {}
        add_transport_counts(connection, report, gap_event="gap", reconnect_event="reconnect")
        assert report["reconnects"] == 3
        assert report["reconnect_clusters"] == 2
        raw_profiles = report["transport_profiles"]
        assert isinstance(raw_profiles, list)
        profiles = {item["transport_profile"]: item for item in raw_profiles}
        assert profiles["l3"]["reconnects"] == 2
        assert profiles["l3"]["reconnect_clusters"] == 2
        assert profiles["public"]["reconnects"] == 1
        assert profiles["public"]["reconnect_clusters"] == 1
    finally:
        connection.close()


def test_capture_log_is_non_empty_and_payload_free(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log_path = capture_log_path(run_dir, "sample-run")
    logger = configure_capture_logger(log_path)
    logger.info("session_start transport_profile=spot")
    logger.info(
        "disconnect exception_class=ConnectionError close_code=%s close_reason_rcvd=%s",
        1001,
        "Going away",
    )
    text = log_path.read_text(encoding="utf-8")
    assert text.strip()
    assert "session_start" in text
    assert "ConnectionError" in text
    assert "1001" in text
    assert "Going away" in text
    assert "api-key" not in text.lower()
    assert "BEGIN PRIVATE KEY" not in text
