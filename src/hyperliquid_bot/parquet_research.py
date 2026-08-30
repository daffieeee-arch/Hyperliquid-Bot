"""DuckDB-only ZSTD-Parquet storage and research views for DATA-1A."""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import duckdb

from .raw_research import RawResearchRecord

RESEARCH_VIEW_NAMES: Final = (
    "raw_records",
    "trades",
    "bbo",
    "l2",
    "derivative_context",
    "sessions",
    "subscription_events",
    "data_quality_events",
)

_CREATE_SEGMENT_TABLE: Final = """
CREATE TABLE raw_segment (
    schema_version INTEGER NOT NULL,
    venue VARCHAR NOT NULL,
    product VARCHAR NOT NULL,
    channel VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    message_ordinal BIGINT NOT NULL,
    received_utc_ns BIGINT NOT NULL,
    received_monotonic_ns BIGINT NOT NULL,
    direction VARCHAR NOT NULL,
    frame_type VARCHAR NOT NULL,
    payload_encoding VARCHAR NOT NULL,
    payload_bytes BLOB NOT NULL,
    payload_sha256 VARCHAR NOT NULL
)
"""

_INSERT_SEGMENT_ROW: Final = """
INSERT INTO raw_segment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass(frozen=True, slots=True)
class ParquetRotation:
    """Bound one in-memory segment before an atomic Parquet publication."""

    max_records: int = 5_000
    max_payload_bytes: int = 16 * 1024 * 1024
    max_interval_seconds: float = 60.0

    def __post_init__(self) -> None:
        if type(self.max_records) is not int or self.max_records < 2:
            raise ValueError("max_records must be an integer of at least two.")
        if type(self.max_payload_bytes) is not int or self.max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be a positive integer.")
        if type(self.max_interval_seconds) not in (int, float) or self.max_interval_seconds <= 0:
            raise ValueError("max_interval_seconds must be positive.")


class ParquetResearchWriter:
    """Buffer records and publish complete ZSTD-Parquet parts atomically.

    Published parts survive a process crash. A hard crash can lose the current
    in-memory segment and may leave a hidden ``.partial`` file, which readers
    deliberately ignore.
    """

    def __init__(self, output_dir: Path, *, rotation: ParquetRotation | None = None) -> None:
        if not isinstance(output_dir, Path):
            raise TypeError("output_dir must be a pathlib.Path.")
        self._output_dir = output_dir.resolve()
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._rotation = rotation if rotation is not None else ParquetRotation()
        if type(self._rotation) is not ParquetRotation:
            raise TypeError("rotation must be a ParquetRotation.")
        self._buffer: list[RawResearchRecord] = []
        self._buffer_payload_bytes = 0
        self._segment_started_monotonic_ns: int | None = None
        self._part_number = 0
        self._writer_tag = uuid.uuid4().hex[:12]
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    @property
    def parquet_files(self) -> tuple[Path, ...]:
        return tuple(sorted(self._output_dir.glob("*.parquet")))

    @property
    def orphan_partial_files(self) -> tuple[Path, ...]:
        return tuple(sorted(self._output_dir.glob(".*.partial")))

    async def append(self, record: RawResearchRecord) -> None:
        if type(record) is not RawResearchRecord:
            raise TypeError("record must be a RawResearchRecord.")
        async with self._lock:
            if self._closed:
                raise RuntimeError("Cannot append to a closed Parquet research writer.")
            if self._segment_started_monotonic_ns is None:
                self._segment_started_monotonic_ns = record.received_monotonic_ns
            self._buffer.append(record)
            self._buffer_payload_bytes += len(record.payload_bytes)
            if self._should_rotate(record.received_monotonic_ns):
                await self._flush_locked()

    async def flush(self) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Cannot flush a closed Parquet research writer.")
            await self._flush_locked()

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            await self._flush_locked()
            self._closed = True

    def _should_rotate(self, current_monotonic_ns: int) -> bool:
        if len(self._buffer) >= self._rotation.max_records:
            return True
        if self._buffer_payload_bytes >= self._rotation.max_payload_bytes:
            return True
        if self._segment_started_monotonic_ns is None:
            return False
        elapsed_ns = max(0, current_monotonic_ns - self._segment_started_monotonic_ns)
        return elapsed_ns >= int(self._rotation.max_interval_seconds * 1_000_000_000)

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return
        segment = tuple(self._buffer)
        next_part_number = self._part_number + 1
        final_path = await asyncio.to_thread(
            self._write_segment,
            segment,
            next_part_number,
        )
        del self._buffer[: len(segment)]
        self._buffer_payload_bytes = sum(len(record.payload_bytes) for record in self._buffer)
        self._segment_started_monotonic_ns = (
            self._buffer[0].received_monotonic_ns if self._buffer else None
        )
        self._part_number = next_part_number
        if final_path not in self.parquet_files:
            raise RuntimeError("Published Parquet part is not visible after atomic rename.")

    def _write_segment(
        self,
        segment: tuple[RawResearchRecord, ...],
        part_number: int,
    ) -> Path:
        first_ordinal = segment[0].message_ordinal
        last_ordinal = segment[-1].message_ordinal
        stem = f"part-{part_number:06d}-{first_ordinal:012d}-{last_ordinal:012d}-{self._writer_tag}"
        partial_path = self._output_dir / f".{stem}.partial"
        final_path = self._output_dir / f"{stem}.parquet"
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(_CREATE_SEGMENT_TABLE)
            connection.executemany(
                _INSERT_SEGMENT_ROW,
                [_storage_row(record) for record in segment],
            )
            escaped_partial_path = _sql_string(partial_path.as_posix())
            connection.execute(
                f"COPY raw_segment TO '{escaped_partial_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        except BaseException:
            partial_path.unlink(missing_ok=True)
            raise
        finally:
            connection.close()

        try:
            with partial_path.open("rb") as partial_file:
                os.fsync(partial_file.fileno())
            os.replace(partial_path, final_path)
            directory_fd = os.open(self._output_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            partial_path.unlink(missing_ok=True)
            raise
        return final_path


def create_research_catalog(parquet_dir: Path, database_path: Path) -> None:
    """Create persistent DuckDB views over completed Parquet parts."""

    if not isinstance(parquet_dir, Path) or not isinstance(database_path, Path):
        raise TypeError("parquet_dir and database_path must be pathlib.Path values.")
    resolved_parquet_dir = parquet_dir.resolve()
    if not tuple(resolved_parquet_dir.glob("*.parquet")):
        raise ValueError("At least one completed Parquet part is required.")
    resolved_database_path = database_path.resolve()
    resolved_database_path.parent.mkdir(parents=True, exist_ok=True)
    parquet_glob = _sql_string((resolved_parquet_dir / "*.parquet").as_posix())

    connection = duckdb.connect(str(resolved_database_path))
    try:
        connection.execute(
            "CREATE OR REPLACE VIEW raw_records AS "
            f"SELECT * FROM read_parquet('{parquet_glob}', union_by_name = true, "
            "filename = true)"
        )
        _create_payload_views(connection)
    finally:
        connection.close()


def _create_payload_views(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE OR REPLACE VIEW trades AS
        SELECT
            raw.session_id,
            raw.message_ordinal,
            raw.received_utc_ns,
            raw.received_monotonic_ns,
            CAST(trade.key AS BIGINT) AS event_index,
            json_extract_string(trade.value, '$.coin') AS coin,
            json_extract_string(trade.value, '$.side') AS side,
            json_extract_string(trade.value, '$.px') AS price,
            json_extract_string(trade.value, '$.sz') AS size,
            json_extract_string(trade.value, '$.time') AS event_time_ms,
            json_extract_string(trade.value, '$.tid') AS trade_id,
            json_extract_string(trade.value, '$.hash') AS transaction_hash
        FROM raw_records AS raw,
             LATERAL json_each(decode(raw.payload_bytes), '$.data') AS trade
        WHERE raw.venue = 'hyperliquid'
          AND raw.product = 'BTC-PERP'
          AND raw.channel = 'trades'
          AND raw.direction = 'inbound'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW bbo AS
        SELECT
            session_id,
            message_ordinal,
            received_utc_ns,
            received_monotonic_ns,
            json_extract_string(decode(payload_bytes), '$.data.coin') AS coin,
            json_extract_string(decode(payload_bytes), '$.data.time') AS event_time_ms,
            json_extract_string(decode(payload_bytes), '$.data.bbo[0].px') AS bid_price,
            json_extract_string(decode(payload_bytes), '$.data.bbo[0].sz') AS bid_size,
            json_extract_string(decode(payload_bytes), '$.data.bbo[0].n') AS bid_order_count,
            json_extract_string(decode(payload_bytes), '$.data.bbo[1].px') AS ask_price,
            json_extract_string(decode(payload_bytes), '$.data.bbo[1].sz') AS ask_size,
            json_extract_string(decode(payload_bytes), '$.data.bbo[1].n') AS ask_order_count
        FROM raw_records
        WHERE venue = 'hyperliquid'
          AND product = 'BTC-PERP'
          AND channel = 'bbo'
          AND direction = 'inbound'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW l2 AS
        SELECT
            raw.session_id,
            raw.message_ordinal,
            raw.received_utc_ns,
            raw.received_monotonic_ns,
            json_extract_string(decode(raw.payload_bytes), '$.data.coin') AS coin,
            json_extract_string(decode(raw.payload_bytes), '$.data.time') AS event_time_ms,
            side.side,
            CAST(level.key AS BIGINT) AS level_index,
            json_extract_string(level.value, '$.px') AS price,
            json_extract_string(level.value, '$.sz') AS size,
            json_extract_string(level.value, '$.n') AS order_count
        FROM raw_records AS raw
        JOIN (VALUES ('bid', 0), ('ask', 1)) AS side(side, side_index) ON true,
             LATERAL json_each(
                 decode(raw.payload_bytes),
                 printf('$.data.levels[%d]', side.side_index)
             ) AS level
        WHERE raw.venue = 'hyperliquid'
          AND raw.product = 'BTC-PERP'
          AND raw.channel = 'l2Book'
          AND raw.direction = 'inbound'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW derivative_context AS
        SELECT
            session_id,
            message_ordinal,
            received_utc_ns,
            received_monotonic_ns,
            json_extract_string(decode(payload_bytes), '$.data.coin') AS coin,
            json_extract_string(decode(payload_bytes), '$.data.ctx.funding') AS funding,
            json_extract_string(decode(payload_bytes), '$.data.ctx.openInterest') AS open_interest,
            json_extract_string(decode(payload_bytes), '$.data.ctx.oraclePx') AS oracle_price,
            json_extract_string(decode(payload_bytes), '$.data.ctx.markPx') AS mark_price,
            json_extract_string(decode(payload_bytes), '$.data.ctx.midPx') AS mid_price,
            json_extract_string(
                decode(payload_bytes), '$.data.ctx.prevDayPx'
            ) AS previous_day_price,
            json_extract_string(
                decode(payload_bytes), '$.data.ctx.dayNtlVlm'
            ) AS day_notional_volume
        FROM raw_records
        WHERE venue = 'hyperliquid'
          AND product = 'BTC-PERP'
          AND channel = 'activeAssetCtx'
          AND direction = 'inbound'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW sessions AS
        SELECT
            session_id,
            message_ordinal,
            received_utc_ns,
            received_monotonic_ns,
            json_extract_string(decode(payload_bytes), '$.event') AS event,
            json_extract_string(decode(payload_bytes), '$.reason') AS reason,
            json_extract_string(decode(payload_bytes), '$.previous_session_id')
                AS previous_session_id,
            decode(payload_bytes) AS marker_json
        FROM raw_records
        WHERE channel = 'session'
          AND direction = 'local'
          AND frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW subscription_events AS
        SELECT
            session_id,
            message_ordinal,
            received_utc_ns,
            received_monotonic_ns,
            direction,
            frame_type,
            CASE
                WHEN direction = 'local'
                    THEN json_extract_string(decode(payload_bytes), '$.event')
                WHEN direction = 'outbound' THEN 'subscription_payload_sent'
                ELSE 'subscription_response_received'
            END AS event,
            COALESCE(
                json_extract_string(decode(payload_bytes), '$.subscription_type'),
                json_extract_string(decode(payload_bytes), '$.subscription.type'),
                json_extract_string(decode(payload_bytes), '$.data.subscription.type')
            ) AS subscription_type,
            decode(payload_bytes) AS payload_text
        FROM raw_records
        WHERE channel IN ('subscription', 'subscriptionResponse')
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW data_quality_events AS
        SELECT
            session_id,
            message_ordinal,
            received_utc_ns,
            received_monotonic_ns,
            json_extract_string(decode(payload_bytes), '$.event') AS event,
            json_extract_string(decode(payload_bytes), '$.reason') AS reason,
            json_extract_string(decode(payload_bytes), '$.raw_message_ordinal')
                AS raw_message_ordinal,
            decode(payload_bytes) AS marker_json
        FROM raw_records
        WHERE channel = 'data_quality'
          AND direction = 'local'
          AND frame_type = 'marker'
        """
    )


def _storage_row(record: RawResearchRecord) -> tuple[object, ...]:
    return (
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


def _sql_string(value: str) -> str:
    return value.replace("'", "''")
