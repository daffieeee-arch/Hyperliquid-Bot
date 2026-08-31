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
    "kraken_spot_trades",
    "kraken_spot_l2_events",
    "kraken_spot_l3_order_events",
    "kraken_spot_l3_order_lifecycle",
    "okx_swap_trades",
    "okx_swap_bbo",
    "okx_swap_l2_events",
    "okx_swap_derivative_context",
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
                json_extract_string(decode(payload_bytes), '$.data.subscription.type'),
                json_extract_string(decode(payload_bytes), '$.arg.channel'),
                json_extract_string(decode(payload_bytes), '$.args[0].channel')
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
    _create_kraken_payload_views(connection)
    _create_okx_payload_views(connection)


def _create_kraken_payload_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Create DATA-1B views from explicitly local, string-preserving normalizations."""

    connection.execute(
        """
        CREATE OR REPLACE VIEW kraken_spot_trades AS
        SELECT
            raw.session_id,
            raw.message_ordinal AS normalization_message_ordinal,
            CAST(
                json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT
            ) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(raw.payload_bytes), '$.message_type') AS message_type,
            CAST(json_extract_string(event.value, '$.event_index') AS BIGINT) AS event_index,
            json_extract_string(event.value, '$.symbol') AS symbol,
            json_extract_string(event.value, '$.side') AS side,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.qty') AS quantity,
            json_extract_string(event.value, '$.order_type') AS order_type,
            json_extract_string(event.value, '$.trade_id') AS trade_id,
            json_extract_string(event.value, '$.timestamp') AS event_timestamp
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'kraken'
         AND source.product = 'BTC/EUR'
         AND source.channel = 'trade'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'kraken'
          AND raw.product = 'BTC/EUR'
          AND raw.channel = 'normalized_trade'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW kraken_spot_l2_events AS
        SELECT
            raw.session_id,
            raw.message_ordinal AS normalization_message_ordinal,
            CAST(
                json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT
            ) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(raw.payload_bytes), '$.message_type') AS message_type,
            json_extract_string(decode(raw.payload_bytes), '$.symbol') AS symbol,
            json_extract_string(decode(raw.payload_bytes), '$.message_timestamp')
                AS message_timestamp,
            json_extract_string(decode(raw.payload_bytes), '$.checksum') AS checksum,
            CAST(json_extract_string(event.value, '$.data_index') AS BIGINT) AS data_index,
            CAST(json_extract_string(event.value, '$.wire_order') AS BIGINT) AS wire_order,
            json_extract_string(event.value, '$.side') AS side,
            CAST(json_extract_string(event.value, '$.side_index') AS BIGINT) AS side_index,
            json_extract_string(event.value, '$.action') AS action,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.qty') AS quantity
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'kraken'
         AND source.product = 'BTC/EUR'
         AND source.channel = 'book'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'kraken'
          AND raw.product = 'BTC/EUR'
          AND raw.channel = 'normalized_book'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW kraken_spot_l3_order_events AS
        SELECT
            raw.session_id,
            raw.message_ordinal AS normalization_message_ordinal,
            CAST(
                json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT
            ) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(raw.payload_bytes), '$.message_type') AS message_type,
            json_extract_string(decode(raw.payload_bytes), '$.symbol') AS symbol,
            json_extract_string(decode(raw.payload_bytes), '$.message_timestamp')
                AS message_timestamp,
            json_extract_string(decode(raw.payload_bytes), '$.checksum') AS checksum,
            CAST(json_extract_string(event.value, '$.data_index') AS BIGINT) AS data_index,
            CAST(json_extract_string(event.value, '$.wire_order') AS BIGINT) AS wire_order,
            json_extract_string(event.value, '$.side') AS side,
            CAST(json_extract_string(event.value, '$.side_index') AS BIGINT) AS side_index,
            json_extract_string(event.value, '$.event') AS event,
            json_extract_string(event.value, '$.event_source') AS event_source,
            json_extract_string(event.value, '$.order_id') AS order_id,
            json_extract_string(event.value, '$.limit_price') AS limit_price,
            json_extract_string(event.value, '$.order_qty') AS order_quantity,
            json_extract_string(event.value, '$.timestamp') AS order_timestamp
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'kraken'
         AND source.product = 'BTC/EUR'
         AND source.channel = 'level3'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'kraken'
          AND raw.product = 'BTC/EUR'
          AND raw.channel = 'normalized_level3'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW kraken_spot_l3_order_lifecycle AS
        SELECT
            *,
            row_number() OVER (
                PARTITION BY session_id, order_id
                ORDER BY raw_message_ordinal, data_index, wire_order NULLS LAST
            ) AS observation_index,
            lag(event) OVER (
                PARTITION BY session_id, order_id
                ORDER BY raw_message_ordinal, data_index, wire_order NULLS LAST
            ) AS previous_event,
            lag(order_quantity) OVER (
                PARTITION BY session_id, order_id
                ORDER BY raw_message_ordinal, data_index, wire_order NULLS LAST
            ) AS previous_order_quantity
        FROM kraken_spot_l3_order_events
        """
    )


def _create_okx_payload_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Create DATA-1C views from local string-preserving validated markers."""

    connection.execute(
        """
        CREATE OR REPLACE VIEW okx_swap_trades AS
        SELECT
            raw.session_id,
            raw.message_ordinal AS normalization_message_ordinal,
            CAST(
                json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT
            ) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            CAST(json_extract_string(event.value, '$.event_index') AS BIGINT) AS event_index,
            json_extract_string(event.value, '$.instrument_id') AS instrument_id,
            json_extract_string(event.value, '$.trade_id') AS trade_id,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.quantity') AS quantity,
            json_extract_string(event.value, '$.side') AS side,
            json_extract_string(event.value, '$.source') AS source,
            json_extract_string(event.value, '$.event_time_ms') AS event_time_ms
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'okx'
         AND source.product = 'BTC-USDT-SWAP'
         AND source.channel = 'trades-all'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'okx'
          AND raw.product = 'BTC-USDT-SWAP'
          AND raw.channel = 'normalized_trades-all'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW okx_swap_bbo AS
        SELECT
            raw.session_id,
            raw.message_ordinal AS normalization_message_ordinal,
            CAST(
                json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT
            ) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(raw.payload_bytes), '$.instrument_id') AS instrument_id,
            json_extract_string(decode(raw.payload_bytes), '$.event_time_ms') AS event_time_ms,
            json_extract_string(decode(raw.payload_bytes), '$.seq_id') AS seq_id,
            CAST(json_extract_string(event.value, '$.wire_order') AS BIGINT) AS wire_order,
            json_extract_string(event.value, '$.side') AS side,
            CAST(json_extract_string(event.value, '$.side_index') AS BIGINT) AS side_index,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.quantity') AS quantity,
            json_extract_string(event.value, '$.order_count') AS order_count
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'okx'
         AND source.product = 'BTC-USDT-SWAP'
         AND source.channel = 'bbo-tbt'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'okx'
          AND raw.product = 'BTC-USDT-SWAP'
          AND raw.channel = 'normalized_bbo-tbt'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW okx_swap_l2_events AS
        SELECT
            raw.session_id,
            raw.message_ordinal AS normalization_message_ordinal,
            CAST(
                json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT
            ) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(raw.payload_bytes), '$.instrument_id') AS instrument_id,
            json_extract_string(decode(raw.payload_bytes), '$.message_type') AS message_type,
            json_extract_string(decode(raw.payload_bytes), '$.event_time_ms') AS event_time_ms,
            json_extract_string(decode(raw.payload_bytes), '$.checksum_wire') AS checksum_wire,
            json_extract_string(decode(raw.payload_bytes), '$.prev_seq_id') AS prev_seq_id,
            json_extract_string(decode(raw.payload_bytes), '$.seq_id') AS seq_id,
            json_extract_string(decode(raw.payload_bytes), '$.sequence_event') AS sequence_event,
            CAST(json_extract_string(event.value, '$.wire_order') AS BIGINT) AS wire_order,
            json_extract_string(event.value, '$.side') AS side,
            CAST(json_extract_string(event.value, '$.side_index') AS BIGINT) AS side_index,
            json_extract_string(event.value, '$.action') AS action,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.quantity') AS quantity,
            json_extract_string(event.value, '$.order_count') AS order_count
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'okx'
         AND source.product = 'BTC-USDT-SWAP'
         AND source.channel = 'books'
         AND source.direction = 'inbound'
        LEFT JOIN LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
          ON true
        WHERE raw.venue = 'okx'
          AND raw.product = 'BTC-USDT-SWAP'
          AND raw.channel = 'normalized_books'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW okx_swap_derivative_context AS
        SELECT
            raw.session_id,
            raw.message_ordinal AS normalization_message_ordinal,
            CAST(
                json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT
            ) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(raw.payload_bytes), '$.source_channel') AS source_channel,
            json_extract_string(decode(raw.payload_bytes), '$.context_type') AS context_type,
            json_extract_string(decode(raw.payload_bytes), '$.instrument_id') AS instrument_id,
            json_extract_string(decode(raw.payload_bytes), '$.event_time_ms') AS event_time_ms,
            json_extract_string(decode(raw.payload_bytes), '$.funding_rate') AS funding_rate,
            json_extract_string(decode(raw.payload_bytes), '$.next_funding_rate')
                AS next_funding_rate,
            json_extract_string(decode(raw.payload_bytes), '$.funding_time_ms')
                AS funding_time_ms,
            json_extract_string(decode(raw.payload_bytes), '$.next_funding_time_ms')
                AS next_funding_time_ms,
            json_extract_string(decode(raw.payload_bytes), '$.premium') AS premium,
            json_extract_string(decode(raw.payload_bytes), '$.open_interest_contracts')
                AS open_interest_contracts,
            json_extract_string(decode(raw.payload_bytes), '$.open_interest_currency')
                AS open_interest_currency,
            json_extract_string(decode(raw.payload_bytes), '$.open_interest_usd')
                AS open_interest_usd,
            json_extract_string(decode(raw.payload_bytes), '$.mark_price') AS mark_price,
            json_extract_string(decode(raw.payload_bytes), '$.index_price') AS index_price
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'okx'
         AND source.product = 'BTC-USDT-SWAP'
         AND source.channel = json_extract_string(
             decode(raw.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound'
        WHERE raw.venue = 'okx'
          AND raw.product = 'BTC-USDT-SWAP'
          AND raw.channel IN (
              'normalized_funding-rate',
              'normalized_open-interest',
              'normalized_mark-price',
              'normalized_index-tickers'
          )
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
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
