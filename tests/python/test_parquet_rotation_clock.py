"""Rotation uses observed clock progress without changing interleaved raw records."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from hyperliquid_bot.parquet_research import ParquetResearchWriter, ParquetRotation
from hyperliquid_bot.raw_research import RawResearchRecord
from test_parquet_research import _marker, _record, _slow_segment_write


def records_at(seconds: list[int]) -> list[RawResearchRecord]:
    result = []
    for ordinal, second in enumerate(seconds, 1):
        local = ordinal % 2 == 0
        record = (_marker if local else _record)(
            ordinal, "spot_trade" if local else "raw", f'{{"record":{ordinal}}}'.encode()
        )
        result.append(
            replace(
                record,
                venue="binance",
                product="BTCUSDT",
                received_utc_ns=1_788_105_600_000_000_000 + second * 1_000_000_000,
                received_monotonic_ns=second * 1_000_000_000,
            )
        )
    return result


def assert_exact_parts(
    writer: ParquetResearchWriter, records: list[RawResearchRecord], sizes: list[int]
) -> None:
    rows = []
    counts = []
    with duckdb.connect(":memory:") as connection:
        for path in writer.parquet_files:
            part = connection.execute(
                "SELECT * EXCLUDE(file_row_number) "
                "FROM read_parquet(?, file_row_number=true) ORDER BY file_row_number",
                [str(path)],
            ).fetchall()
            counts.append(len(part))
            rows.extend(part)
    assert counts == sizes
    assert writer.pending_record_count == 0
    assert writer.published_part_count == len(sizes)
    assert writer.orphan_partial_files == ()
    assert rows == [
        (
            record.schema_version,
            record.venue,
            record.product,
            record.channel,
            record.session_id,
            record.message_ordinal,
            record.received_utc_ns,
            record.received_monotonic_ns,
            record.direction.value,
            record.frame_type.value,
            record.payload_encoding.value,
            record.payload_bytes,
            record.payload_sha256,
        )
        for record in records
    ]


@pytest.mark.asyncio
async def test_alternating_backlog_raw_and_current_local_do_not_make_two_row_parts(
    tmp_path: Path,
) -> None:
    records = records_at([second for i in range(20) for second in (1_000 + i, 1_120 + i)])
    writer = ParquetResearchWriter(tmp_path)
    for record in records:
        await writer.append(record)
    await writer.aclose()
    # The first observed 120s jump legitimately rotates. Old receipts in the
    # next segment must not reset its rotation baseline 120s into the past.
    assert_exact_parts(writer, records, [2, 38])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seconds,sizes",
    [
        ([0, 59, 60, 61, 120, 121], [3, 3]),
        ([120, 1, 121, 179, 180, 2, 181, 240], [5, 3]),
    ],
)
async def test_interval_boundary_and_regressing_receipts_preserve_rows(
    tmp_path: Path, seconds: list[int], sizes: list[int]
) -> None:
    records = records_at(seconds)
    writer = ParquetResearchWriter(tmp_path)
    for record in records:
        await writer.append(record)
    await writer.aclose()
    assert_exact_parts(writer, records, sizes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rotation", [ParquetRotation(max_records=3), ParquetRotation(max_payload_bytes=30)]
)
async def test_regressing_receipts_do_not_relax_record_or_payload_limits(
    tmp_path: Path, rotation: ParquetRotation
) -> None:
    records = [
        replace(record, payload_bytes=f"{record.message_ordinal:010}".encode())
        for record in records_at([120, 1, 2, 3, 121, 122, 4, 123, 124])
    ]
    writer = ParquetResearchWriter(tmp_path, rotation=rotation)
    for record in records:
        await writer.append(record)
    await writer.aclose()
    assert_exact_parts(writer, records, [3, 3, 3])


@pytest.mark.asyncio
async def test_explicit_flush_does_not_forget_rotation_progress(tmp_path: Path) -> None:
    records = records_at([120, 1, 121, 2, 122])
    writer = ParquetResearchWriter(tmp_path)
    await writer.append(records[0])
    await writer.flush()
    for record in records[1:]:
        await writer.append(record)
    await writer.aclose()
    assert_exact_parts(writer, records, [1, 4])


@pytest.mark.asyncio
async def test_cancelled_backpressure_append_cannot_advance_rotation_progress(
    tmp_path: Path,
) -> None:
    records = records_at([0, 120, 1, 180, 1_000, 2, 240, 3, 241])
    writer = ParquetResearchWriter(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = {"n": 0, "overlap": 0}
    _slow_segment_write(writer, entered, release, calls)
    for record in records[:2]:
        await writer.append(record)
    assert await asyncio.to_thread(entered.wait, 2)
    for record in records[2:4]:
        await writer.append(record)
    waiting = asyncio.create_task(writer.append(records[4]))
    await asyncio.sleep(0)
    assert not waiting.done()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    release.set()
    await asyncio.wait_for(writer.flush(), timeout=3)
    for record in records[5:]:
        await writer.append(record)
    await asyncio.wait_for(writer.aclose(), timeout=3)
    await writer.aclose()
    assert calls == {"n": 4, "overlap": 0}
    assert_exact_parts(writer, records[:4] + records[5:], [2, 2, 2, 2])
