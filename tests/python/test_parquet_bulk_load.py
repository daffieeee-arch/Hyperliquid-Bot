"""Lossless bulk staging and real writer failure/retry contracts."""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast

import duckdb
import pytest

from hyperliquid_bot import parquet_research as storage
from hyperliquid_bot.raw_research import (
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
)


def record(ordinal: int, payload: bytes) -> RawResearchRecord:
    return RawResearchRecord(
        1,
        "synthetic",
        "TEST",
        "raw",
        "session",
        ordinal,
        2**63 - 1 - ordinal,
        2**53 + 19 + ordinal,
        MessageDirection.INBOUND,
        FrameType.BINARY,
        PayloadEncoding.BINARY,
        payload,
    )


def rows(path: Path) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    connection = duckdb.connect(":memory:")
    try:
        schema = connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
        ).fetchall()
        values = connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
        return schema, values
    finally:
        connection.close()


@pytest.mark.parametrize("length", [0, 1, 2, 3, 48 * 1024 - 1, 48 * 1024, 48 * 1024 + 1])
def test_bulk_matches_legacy_all_columns_types_order_and_parquet_bytes(
    tmp_path: Path, length: int
) -> None:
    payload = (bytes(range(256)) * (length // 256 + 1))[:length]
    records = (
        record(1, payload),
        replace(record(2, b'\\N\x00\xff\r\n"'), venue="\\N", product="NULL", channel="null"),
        replace(record(3, b""), session_id='s\x00🪙,\r\n"quote"\\end'),
        replace(record(4, b"0"), schema_version=True),
        replace(record(5, b"1"), schema_version=cast(int, 1.0)),
    )
    actual = storage.ParquetResearchWriter(tmp_path / "bulk")._write_segment(records, 1)
    reference = tmp_path / "legacy.parquet"
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(storage._CREATE_SEGMENT_TABLE)
        connection.executemany(
            "INSERT INTO raw_segment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [storage._storage_row(value) for value in records],
        )
        connection.execute(
            "COPY raw_segment TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(reference)]
        )
    finally:
        connection.close()
    assert rows(actual) == rows(reference)
    assert actual.read_bytes() == reference.read_bytes()
    assert rows(actual)[1] == [storage._storage_row(value) for value in records]
    assert sorted(path.name for path in actual.parent.iterdir()) == [actual.name]


def test_staging_is_anonymous_on_own_filesystem_and_reader_uses_four_line_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(storage._CREATE_SEGMENT_TABLE)
    seen = []
    opened: list[BinaryIO] = []
    real_temporary = tempfile.TemporaryFile
    forbidden = tmp_path / "shared-tmp"
    forbidden.mkdir()
    spool = tmp_path / "spool"
    spool.mkdir()

    def temporary(*args: object, **kwargs: object) -> BinaryIO:
        assert kwargs["dir"] == spool
        stream = cast(BinaryIO, real_temporary(*args, **kwargs))  # type: ignore[call-overload]
        opened.append(stream)
        return stream

    class Probe:
        def execute(self, sql: str, params: list[object]) -> None:
            path, _types, maximum, buffer = params
            fd = int(str(path).rsplit("/", 1)[1])
            identity = os.fstat(fd)
            assert identity.st_nlink == 0
            assert identity.st_dev == spool.stat().st_dev
            assert (
                maximum == identity.st_size + 1
            )  # One logical CSV row, including quoted newlines.
            assert buffer == 4 * (identity.st_size + 1)
            assert list(spool.iterdir()) == []
            seen.append((maximum, buffer))
            connection.execute(sql, params)

    monkeypatch.setattr(tempfile, "tempdir", str(forbidden))
    monkeypatch.setattr(tempfile, "TemporaryFile", temporary)
    try:
        storage._load_segment(
            cast(duckdb.DuckDBPyConnection, Probe()),
            (replace(record(1, b"\x00\xff" * 40_000), session_id='s\n"quoted"'),),
            spool,
        )
        stored = connection.execute("SELECT payload_bytes FROM raw_segment").fetchone()
    finally:
        connection.close()
    assert stored == (b"\x00\xff" * 40_000,)
    assert len(seen) == 1 and opened and all(stream.closed for stream in opened)
    assert list(forbidden.iterdir()) == list(spool.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["load", "scratch"])
async def test_failed_bulk_staging_keeps_queued_records_and_retries_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    writer = storage.ParquetResearchWriter(
        tmp_path, rotation=storage.ParquetRotation(max_records=2)
    )
    records = (record(1, b"\x00\xff"), record(2, b""))
    opened: list[BinaryIO] = []
    real_temporary = tempfile.TemporaryFile
    real_sql = storage._LOAD_SEGMENT_CSV
    real_payload = storage._csv_payload

    def temporary(*args: object, **kwargs: object) -> BinaryIO:
        stream = cast(BinaryIO, real_temporary(*args, **kwargs))  # type: ignore[call-overload]
        opened.append(stream)
        return stream

    def disk_full(stream: BinaryIO, _value: bytes) -> None:
        stream.write(b'"partially-staged')
        raise OSError("injected scratch full")

    monkeypatch.setattr(tempfile, "TemporaryFile", temporary)
    if failure == "load":
        monkeypatch.setattr(
            storage, "_LOAD_SEGMENT_CSV", "INSERT INTO missing_bulk_test SELECT ?, ?, ?, ?"
        )
        expected: type[Exception] = duckdb.CatalogException
    else:
        monkeypatch.setattr(storage, "_csv_payload", disk_full)
        expected = OSError
    for value in records:
        await writer.append(value)
    with pytest.raises(expected):
        await writer.flush()
    assert writer.pending_record_count == 2
    assert writer.published_part_count == 0
    assert writer.parquet_files == writer.orphan_partial_files == ()
    assert list(tmp_path.iterdir()) == []
    assert opened and all(stream.closed for stream in opened)
    monkeypatch.setattr(storage, "_LOAD_SEGMENT_CSV", real_sql)
    monkeypatch.setattr(storage, "_csv_payload", real_payload)
    await writer.aclose()
    await writer.aclose()
    assert writer.pending_record_count == 0 and writer.published_part_count == 1
    published = list(tmp_path.glob("*.parquet"))
    assert len(published) == 1
    assert rows(published[0])[1] == [storage._storage_row(value) for value in records]
    assert all(stream.closed for stream in opened)


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [3 * 1024**2, 17 * 1024**2 + 1])
async def test_single_record_larger_than_rotation_limit_is_not_rejected(
    tmp_path: Path, length: int
) -> None:
    writer = storage.ParquetResearchWriter(
        tmp_path,
        rotation=storage.ParquetRotation(max_payload_bytes=1024),
    )
    payload = (bytes(range(256)) * (length // 256 + 1))[:length]
    value = record(1, payload)  # Also covers a record above the default 16 MiB rotation.
    await writer.append(value)
    await writer.aclose()
    assert rows(writer.parquet_files[0])[1] == [storage._storage_row(value)]
