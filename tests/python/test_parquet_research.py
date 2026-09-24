"""Offline tests for exact BLOB storage, rotation, and DuckDB research views."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from pathlib import Path

import duckdb
import pytest

from hyperliquid_bot.parquet_research import (
    RESEARCH_VIEW_NAMES,
    ParquetResearchWriter,
    ParquetRotation,
    create_research_catalog,
)
from hyperliquid_bot.raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
    capture_application_payload,
)

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "hyperliquid"


def _fixture(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


def _record(
    ordinal: int,
    channel: str,
    payload: bytes,
    *,
    direction: MessageDirection = MessageDirection.INBOUND,
    frame_type: FrameType = FrameType.TEXT,
    payload_encoding: PayloadEncoding = PayloadEncoding.UTF8,
) -> RawResearchRecord:
    return RawResearchRecord(
        schema_version=RAW_RESEARCH_SCHEMA_VERSION,
        venue="hyperliquid",
        product="BTC-PERP",
        channel=channel,
        session_id="offline-session-1",
        message_ordinal=ordinal,
        received_utc_ns=1_788_105_600_000_000_000 + ordinal,
        received_monotonic_ns=9_000_000_000 + ordinal,
        direction=direction,
        frame_type=frame_type,
        payload_encoding=payload_encoding,
        payload_bytes=payload,
    )


def _marker(ordinal: int, channel: str, payload: bytes) -> RawResearchRecord:
    return _record(
        ordinal,
        channel,
        payload,
        direction=MessageDirection.LOCAL,
        frame_type=FrameType.MARKER,
        payload_encoding=PayloadEncoding.UTF8_JSON,
    )


def test_callback_capture_clocks_precede_text_or_binary_copy() -> None:
    calls: list[str] = []

    def utc_ns() -> int:
        calls.append("utc")
        return 101

    def monotonic_ns() -> int:
        calls.append("monotonic")
        return 202

    text = capture_application_payload('{"px":"1.2300"}', utc_ns=utc_ns, monotonic_ns=monotonic_ns)
    binary = capture_application_payload(b"\x00\xffraw", utc_ns=utc_ns, monotonic_ns=monotonic_ns)

    assert calls == ["utc", "monotonic", "utc", "monotonic"]
    assert text.payload_bytes == b'{"px":"1.2300"}'
    assert text.frame_type is FrameType.TEXT
    assert text.payload_encoding is PayloadEncoding.UTF8
    assert binary.payload_bytes == b"\x00\xffraw"
    assert binary.frame_type is FrameType.BINARY
    assert binary.payload_encoding is PayloadEncoding.BINARY


@pytest.mark.asyncio
async def test_zstd_parquet_round_trip_and_all_research_views(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "raw"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=5,
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    records = (
        _record(1, "trades", _fixture("trades_frame.json")),
        _record(2, "bbo", _fixture("bbo_frame.json")),
        _record(3, "l2Book", _fixture("l2_book_frame.json")),
        _record(4, "activeAssetCtx", _fixture("active_asset_ctx_frame.json")),
        _marker(5, "session", b'{"event":"connected"}'),
        _record(
            6,
            "subscription",
            b'{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}',
            direction=MessageDirection.OUTBOUND,
        ),
        _record(7, "subscriptionResponse", _fixture("subscription_response_frame.json")),
        _marker(
            8,
            "data_quality",
            b'{"event":"gap_detected","reason":"offline disconnect"}',
        ),
        _record(
            9,
            "unknown",
            b"\x00\xffexact-binary-application-payload",
            frame_type=FrameType.BINARY,
            payload_encoding=PayloadEncoding.BINARY,
        ),
    )

    for record in records:
        await writer.append(record)
    await writer.aclose()

    assert len(writer.parquet_files) == 2
    assert all(path.stat().st_size > 0 for path in writer.parquet_files)
    assert writer.orphan_partial_files == ()

    part_connection = duckdb.connect(":memory:")
    try:
        records_per_part = part_connection.execute(
            """
            SELECT filename, count(*)
            FROM read_parquet(?, filename = true)
            GROUP BY filename
            ORDER BY filename
            """,
            [str(parquet_dir / "*.parquet")],
        ).fetchall()
    finally:
        part_connection.close()
    assert [row[1] for row in records_per_part] == [5, 4]

    create_research_catalog(parquet_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        stored = connection.execute(
            """
            SELECT message_ordinal, payload_bytes, payload_sha256,
                   sha256(payload_bytes), typeof(payload_bytes)
            FROM raw_records
            ORDER BY message_ordinal
            """
        ).fetchall()
        assert len(stored) == len(records)
        for source, returned in zip(records, stored, strict=True):
            ordinal, payload_bytes, stored_digest, computed_digest, logical_type = returned
            assert ordinal == source.message_ordinal
            assert payload_bytes == source.payload_bytes
            assert hashlib.sha256(payload_bytes).hexdigest() == source.payload_sha256
            assert stored_digest == source.payload_sha256
            assert computed_digest == source.payload_sha256
            assert logical_type == "BLOB"

        assert connection.execute(
            "SELECT price, size FROM trades ORDER BY event_index"
        ).fetchall() == [
            ("12345.678900000000000001", "0.000000010000000000"),
            ("0.123456789012345678", "987654321.000000000000000001"),
        ]
        assert connection.execute(
            "SELECT bid_price, bid_size, ask_price, ask_size FROM bbo"
        ).fetchone() == ("104321.2300", "1.2000", "104321.2400", "0.8000")
        assert connection.execute(
            "SELECT side, level_index, price, size FROM l2 ORDER BY side, level_index"
        ).fetchall() == [
            ("ask", 0, "104321.2400", "0.8000"),
            ("ask", 1, "104321.2500", "4.5600"),
            ("bid", 0, "104321.2300", "1.2000"),
            ("bid", 1, "104321.2200", "2.3400"),
        ]
        assert connection.execute(
            """
            SELECT funding, open_interest, oracle_price, mark_price, mid_price
            FROM derivative_context
            """
        ).fetchone() == (
            "0.00001250",
            "12345.6700",
            "104320.5000",
            "104321.2000",
            "104321.2350",
        )
        assert connection.execute("SELECT event FROM sessions").fetchone() == ("connected",)
        assert connection.execute("SELECT count(*) FROM subscription_events").fetchone() == (2,)
        assert connection.execute("SELECT event FROM data_quality_events").fetchone() == (
            "gap_detected",
        )
        views = {
            row[0]
            for row in connection.execute(
                "SELECT view_name FROM duckdb_views() WHERE database_name <> 'system'"
            ).fetchall()
        }
        assert set(RESEARCH_VIEW_NAMES) <= views
    finally:
        connection.close()

    parquet_glob = str(parquet_dir / "*.parquet")
    metadata_connection = duckdb.connect(":memory:")
    try:
        compression = metadata_connection.execute(
            "SELECT DISTINCT compression FROM parquet_metadata(?)",
            [parquet_glob],
        ).fetchall()
    finally:
        metadata_connection.close()
    assert compression == [("ZSTD",)]


@pytest.mark.asyncio
async def test_failed_rotation_preserves_published_part_and_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ParquetResearchWriter(
        tmp_path,
        rotation=ParquetRotation(
            max_records=2,
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    await writer.append(_record(1, "trades", b'{"channel":"trades","data":[]}'))
    await writer.append(_record(2, "trades", b'{"channel":"trades","data":[]}'))
    await writer.flush()
    published_path = writer.parquet_files[0]
    published_bytes = published_path.read_bytes()

    real_write_segment = writer._write_segment

    def fail_write_segment(
        segment: tuple[RawResearchRecord, ...],
        part_number: int,
    ) -> Path:
        del segment, part_number
        raise RuntimeError("injected DuckDB failure")

    monkeypatch.setattr(writer, "_write_segment", fail_write_segment)
    await writer.append(_record(3, "bbo", _fixture("bbo_frame.json")))
    await writer.append(_record(4, "l2Book", _fixture("l2_book_frame.json")))
    with pytest.raises(RuntimeError, match="injected DuckDB failure"):
        await writer.flush()

    assert writer.parquet_files == (published_path,)
    assert published_path.read_bytes() == published_bytes
    assert writer.orphan_partial_files == ()

    monkeypatch.setattr(writer, "_write_segment", real_write_segment)
    await writer.aclose()
    assert len(writer.parquet_files) == 2
    connection = duckdb.connect(":memory:")
    try:
        count_row = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(tmp_path / "*.parquet")]
        ).fetchone()
    finally:
        connection.close()
    assert count_row == (4,)


@pytest.mark.asyncio
async def test_delayed_flush_does_not_serialize_the_next_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ParquetResearchWriter(
        tmp_path,
        rotation=ParquetRotation(
            max_records=2,
            max_payload_bytes=1024 * 1024,
            max_interval_seconds=60.0,
        ),
    )
    entered = threading.Event()
    release = threading.Event()
    real_write_segment = writer._write_segment

    def slow_write_segment(
        segment: tuple[RawResearchRecord, ...],
        part_number: int,
    ) -> Path:
        entered.set()
        assert release.wait(timeout=2)
        return real_write_segment(segment, part_number)

    monkeypatch.setattr(writer, "_write_segment", slow_write_segment)
    await writer.append(_record(1, "trades", b'{"channel":"trades","data":[]}'))
    await writer.append(_record(2, "trades", b'{"channel":"trades","data":[]}'))
    assert await asyncio.to_thread(entered.wait, 2)
    started = time.perf_counter()
    await writer.append(_record(3, "bbo", b'{"channel":"bbo","data":{}}'))
    elapsed = time.perf_counter() - started
    assert elapsed < 0.15
    assert writer.pending_record_count >= 1
    release.set()
    await writer.aclose()
    connection = duckdb.connect(":memory:")
    try:
        count_row = connection.execute(
            "SELECT count(*), min(received_utc_ns), max(received_utc_ns) FROM read_parquet(?)",
            [str(tmp_path / "*.parquet")],
        ).fetchone()
    finally:
        connection.close()
    assert count_row == (3, 1_788_105_600_000_000_001, 1_788_105_600_000_000_003)


def test_duckdb_runtime_dependency_is_exact_and_extensions_are_bundled() -> None:
    assert duckdb.__version__ == "1.5.5"
    connection = duckdb.connect(":memory:")
    try:
        extensions = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                """
                SELECT extension_name, loaded, installed
                FROM duckdb_extensions()
                WHERE extension_name IN ('json', 'parquet')
                """
            ).fetchall()
        }
    finally:
        connection.close()
    assert extensions == {"json": (True, True), "parquet": (True, True)}
