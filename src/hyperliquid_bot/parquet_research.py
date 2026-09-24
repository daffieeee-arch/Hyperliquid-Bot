"""DuckDB-only ZSTD-Parquet storage and research views for DATA-1A."""

from __future__ import annotations

import asyncio
import enum
import os
import threading
import uuid
from collections import deque
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
    "bitvavo_spot_trades",
    "bitvavo_spot_bbo",
    "bitvavo_spot_l2_events",
    "bitvavo_mdpro_spot_l2_events",
    "bitvavo_mdpro_spot_trades",
    "bitvavo_mdpro_spot_bbo",
    "binance_spot_trades",
    "binance_spot_bbo",
    "binance_spot_l2_events",
    "binance_usdm_context",
    "deribit_btc_trades",
    "deribit_btc_l2_events",
    "deribit_btc_derivative_context",
    "deribit_btc_option_sample",
    "polymarket_crypto_market_metadata",
    "polymarket_crypto_l2_events",
    "polymarket_crypto_bbo",
    "polymarket_crypto_last_trade_prices",
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

# One in-flight segment plus one queued segment. Further appends wait.
# The bound is the backpressure that keeps a slow disk from growing memory
# without bound and from dropping records.
_MAX_PENDING_SEGMENTS: Final = 2


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


class _WriterPhase(enum.Enum):
    """Explicit close lifecycle. Success is only ``CLOSED`` after publish."""

    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class _WriteResult:
    """Filled by the segment thread. Visible to asyncio only after ``Thread.join``."""

    path: Path | None = None
    error: BaseException | None = None


@dataclass(slots=True)
class _InflightWrite:
    thread: threading.Thread
    part_number: int
    result: _WriteResult
    done: asyncio.Future[None]


class ParquetResearchWriter:
    """Buffer records and publish complete ZSTD-Parquet parts atomically.

    ``append`` hands a full segment to one writer task and returns before the
    disk flush. Callers do not hold their own locks across the segment thread.
    At most two segments sit in the handoff queue; further appends wait, and a
    failed segment stays queued so ``aclose`` can retry it.
    ``received_utc_ns`` and ``received_monotonic_ns`` are stored as the caller
    set them. A wait inside this writer does not rewrite them.

    The writer task owns the segment thread. Cancelling ``flush`` or one
    ``aclose`` waiter does not cancel that task and does not start a second
    write of the same segment. ``aclose`` reports success only after the
    buffer and the queue are published. A failed close stays failed for every
    later ``aclose``.

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
        self._phase = _WriterPhase.OPEN
        self._condition = asyncio.Condition()
        self._pending: deque[tuple[tuple[RawResearchRecord, ...], int]] = deque()
        self._writer_task: asyncio.Task[None] | None = None
        self._writer_error: BaseException | None = None
        self._inflight: _InflightWrite | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._close_error: BaseException | None = None

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    @property
    def parquet_files(self) -> tuple[Path, ...]:
        return tuple(sorted(self._output_dir.glob("*.parquet")))

    @property
    def orphan_partial_files(self) -> tuple[Path, ...]:
        return tuple(sorted(self._output_dir.glob(".*.partial")))

    @property
    def pending_record_count(self) -> int:
        """Buffered plus queued records. Safe to read between awaits on this loop."""

        return len(self._buffer) + sum(len(segment) for segment, _part in self._pending)

    async def append(self, record: RawResearchRecord) -> None:
        if type(record) is not RawResearchRecord:
            raise TypeError("record must be a RawResearchRecord.")
        async with self._condition:
            while (
                len(self._pending) >= _MAX_PENDING_SEGMENTS
                and self._writer_error is None
                and self._phase is _WriterPhase.OPEN
            ):
                await self._condition.wait()
            if self._writer_error is not None:
                raise self._writer_error
            if self._phase is not _WriterPhase.OPEN:
                raise RuntimeError("Cannot append to a closed Parquet research writer.")
            if self._segment_started_monotonic_ns is None:
                self._segment_started_monotonic_ns = record.received_monotonic_ns
            self._buffer.append(record)
            self._buffer_payload_bytes += len(record.payload_bytes)
            if self._should_rotate(record.received_monotonic_ns):
                self._enqueue_segment_locked()

    async def flush(self) -> None:
        async with self._condition:
            if self._phase is not _WriterPhase.OPEN:
                raise RuntimeError("Cannot flush a closed Parquet research writer.")
            if self._writer_error is not None:
                raise self._writer_error
            if self._buffer:
                self._enqueue_segment_locked()
        await self._wait_until_settled()
        async with self._condition:
            if self._writer_error is not None:
                raise self._writer_error

    async def aclose(self) -> None:
        async with self._condition:
            if self._phase is _WriterPhase.CLOSED:
                return
            if self._phase is _WriterPhase.FAILED:
                if self._close_error is None:
                    raise RuntimeError("Parquet close failed without a recorded error.")
                raise self._close_error
            if self._close_task is None:
                self._phase = _WriterPhase.CLOSING
                if self._buffer:
                    self._enqueue_segment_locked()
                self._close_task = asyncio.create_task(
                    self._finish_close(),
                    name="parquet-research-close",
                )
            close_task = self._close_task
        try:
            # Waiters do not own the close task. Shield keeps one cancelled
            # waiter from cancelling the publish; the commit after the segment
            # thread joins is what prevents a second write of the same part.
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            raise
        async with self._condition:
            if self._phase is _WriterPhase.FAILED:
                if self._close_error is None:
                    raise RuntimeError("Parquet close failed without a recorded error.")
                raise self._close_error

    def _should_rotate(self, current_monotonic_ns: int) -> bool:
        if len(self._buffer) >= self._rotation.max_records:
            return True
        if self._buffer_payload_bytes >= self._rotation.max_payload_bytes:
            return True
        if self._segment_started_monotonic_ns is None:
            return False
        elapsed_ns = max(0, current_monotonic_ns - self._segment_started_monotonic_ns)
        return elapsed_ns >= int(self._rotation.max_interval_seconds * 1_000_000_000)

    def _enqueue_segment_locked(self) -> None:
        """Detach the buffer and hand it to the writer. Caller holds the condition."""

        if not self._buffer:
            return
        segment = tuple(self._buffer)
        self._part_number += 1
        part_number = self._part_number
        self._buffer.clear()
        self._buffer_payload_bytes = 0
        self._segment_started_monotonic_ns = None
        self._pending.append((segment, part_number))
        self._ensure_writer_locked()

    def _ensure_writer_locked(self) -> None:
        if (
            self._writer_task is None
            and self._pending
            and self._writer_error is None
            and self._inflight is None
        ):
            self._writer_task = asyncio.create_task(
                self._writer_loop(),
                name="parquet-research-writer",
            )

    async def _wait_until_settled(self) -> None:
        """Wait until the queue is idle or a storage error is recorded.

        This waits on the condition, not on the writer task, so cancelling the
        waiter does not cancel the writer.
        """

        while True:
            async with self._condition:
                if self._writer_error is not None and self._inflight is None:
                    return
                writer_running = self._writer_task is not None and not self._writer_task.done()
                if not self._pending and self._inflight is None and not writer_running:
                    return
                if (
                    self._pending
                    and self._inflight is None
                    and not writer_running
                    and self._writer_error is None
                ):
                    self._ensure_writer_locked()
                await self._condition.wait()

    async def _finish_close(self) -> None:
        try:
            for _attempt in (1, 2):
                await self._wait_until_settled()
                async with self._condition:
                    if self._writer_error is not None and self._pending and self._inflight is None:
                        self._writer_error = None
                        self._ensure_writer_locked()
                        continue
                    break
            await self._wait_until_settled()
            async with self._condition:
                unpublished = bool(self._pending or self._buffer or self._inflight is not None)
                if self._writer_error is not None or unpublished:
                    self._phase = _WriterPhase.FAILED
                    self._close_error = self._writer_error or RuntimeError(
                        "Parquet close finished with unpublished records."
                    )
                else:
                    self._phase = _WriterPhase.CLOSED
                    self._close_error = None
                self._condition.notify_all()
        except asyncio.CancelledError:
            async with self._condition:
                if self._phase is _WriterPhase.CLOSING:
                    self._phase = _WriterPhase.FAILED
                    self._close_error = RuntimeError(
                        "Parquet close was cancelled before publish completed."
                    )
                self._condition.notify_all()
            raise

    async def _writer_loop(self) -> None:
        try:
            while True:
                async with self._condition:
                    if self._writer_error is not None and self._inflight is None:
                        return
                    if self._inflight is not None:
                        await self._condition.wait()
                        continue
                    if not self._pending:
                        current = asyncio.current_task()
                        if self._writer_task is current:
                            self._writer_task = None
                        self._condition.notify_all()
                        return
                    segment, part_number = self._pending[0]
                await self._publish_one(segment, part_number)
        except asyncio.CancelledError:
            await self._join_and_commit_inflight()
            raise
        finally:
            async with self._condition:
                current = asyncio.current_task()
                if self._writer_task is current:
                    self._writer_task = None
                self._condition.notify_all()

    async def _publish_one(
        self,
        segment: tuple[RawResearchRecord, ...],
        part_number: int,
    ) -> None:
        loop = asyncio.get_running_loop()
        result = _WriteResult()
        done: asyncio.Future[None] = loop.create_future()

        def _finish_from_thread() -> None:
            if not done.done():
                done.set_result(None)

        def _run() -> None:
            try:
                result.path = self._write_segment(segment, part_number)
            except BaseException as exc:
                result.error = exc
            finally:
                loop.call_soon_threadsafe(_finish_from_thread)

        thread = threading.Thread(target=_run, name=f"parquet-part-{part_number}")
        inflight = _InflightWrite(
            thread=thread,
            part_number=part_number,
            result=result,
            done=done,
        )
        async with self._condition:
            if self._inflight is not None:
                raise RuntimeError("Parquet writer refused a second in-flight segment write.")
            self._inflight = inflight
        thread.start()
        try:
            await done
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(thread.join))
            await self._commit_inflight(inflight)
            raise
        await asyncio.to_thread(thread.join)
        await self._commit_inflight(inflight)

    async def _join_and_commit_inflight(self) -> None:
        async with self._condition:
            inflight = self._inflight
        if inflight is None:
            return
        await asyncio.shield(asyncio.to_thread(inflight.thread.join))
        await self._commit_inflight(inflight)

    async def _commit_inflight(self, inflight: _InflightWrite) -> None:
        async with self._condition:
            if self._inflight is not inflight:
                return
            self._inflight = None
            error = inflight.result.error
            if error is not None:
                self._writer_error = error
                self._condition.notify_all()
                return
            path = inflight.result.path
            if not isinstance(path, Path):
                self._writer_error = RuntimeError(
                    "Parquet segment write ended without a published path."
                )
                self._condition.notify_all()
                return
            if not self._pending or self._pending[0][1] != inflight.part_number:
                self._writer_error = RuntimeError(
                    "Parquet writer queue lost the segment it just published."
                )
                self._condition.notify_all()
                return
            if path not in self.parquet_files:
                self._writer_error = RuntimeError(
                    "Published Parquet part is not visible after atomic rename."
                )
                self._condition.notify_all()
                return
            self._pending.popleft()
            self._condition.notify_all()

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
    _create_bitvavo_payload_views(connection)
    _create_bitvavo_mdpro_payload_views(connection)
    _create_binance_payload_views(connection)
    _create_deribit_payload_views(connection)
    _create_polymarket_payload_views(connection)


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
         AND source.product = 'BTC/USD'
         AND source.channel = 'trade'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'kraken'
          AND raw.product = 'BTC/USD'
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
         AND source.product = 'BTC/USD'
         AND source.channel = 'book'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'kraken'
          AND raw.product = 'BTC/USD'
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
         AND source.product = 'BTC/USD'
         AND source.channel = 'level3'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'kraken'
          AND raw.product = 'BTC/USD'
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


def _create_bitvavo_payload_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Create DATA-1D Standard views from string-preserving local normalizations."""

    connection.execute(
        """
        CREATE OR REPLACE VIEW bitvavo_spot_trades AS
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
            'standard' AS feed_product,
            CAST(json_extract_string(event.value, '$.event_index') AS BIGINT) AS event_index,
            json_extract_string(event.value, '$.market') AS market,
            json_extract_string(event.value, '$.trade_id') AS trade_id,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.quantity') AS quantity,
            json_extract_string(event.value, '$.taker_side') AS taker_side,
            json_extract_string(event.value, '$.event_time_ms') AS event_time_ms,
            json_extract_string(event.value, '$.event_time_ns') AS event_time_ns
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'bitvavo'
         AND source.product = 'BTC-EUR'
         AND source.channel = 'trades'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'bitvavo'
          AND raw.product = 'BTC-EUR'
          AND raw.channel = 'normalized_trades'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW bitvavo_spot_bbo AS
        WITH updates AS (
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
                'standard' AS feed_product,
                json_extract_string(decode(raw.payload_bytes), '$.market') AS market,
                json_extract_string(decode(raw.payload_bytes), '$.bid_price')
                    AS bid_price_update,
                json_extract_string(decode(raw.payload_bytes), '$.bid_quantity')
                    AS bid_quantity_update,
                json_extract_string(decode(raw.payload_bytes), '$.ask_price')
                    AS ask_price_update,
                json_extract_string(decode(raw.payload_bytes), '$.ask_quantity')
                    AS ask_quantity_update,
                json_extract_string(decode(raw.payload_bytes), '$.last_price')
                    AS last_price_update
            FROM raw_records AS raw
            JOIN raw_records AS source
              ON source.session_id = raw.session_id
             AND source.message_ordinal = CAST(
                 json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
             )
             AND source.venue = 'bitvavo'
             AND source.product = 'BTC-EUR'
             AND source.channel = 'ticker'
             AND source.direction = 'inbound'
            WHERE raw.venue = 'bitvavo'
              AND raw.product = 'BTC-EUR'
              AND raw.channel = 'normalized_ticker'
              AND raw.direction = 'local'
              AND raw.frame_type = 'marker'
        ), states AS (
            SELECT
                updates.*,
                last_value(bid_price_update IGNORE NULLS) OVER feed_order AS bid_price,
                last_value(bid_quantity_update IGNORE NULLS) OVER feed_order AS bid_quantity,
                last_value(ask_price_update IGNORE NULLS) OVER feed_order AS ask_price,
                last_value(ask_quantity_update IGNORE NULLS) OVER feed_order AS ask_quantity,
                last_value(last_price_update IGNORE NULLS) OVER feed_order AS last_price
            FROM updates
            WINDOW feed_order AS (
                PARTITION BY session_id
                ORDER BY raw_message_ordinal
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            )
        )
        SELECT
            *,
            bid_price IS NOT NULL AND ask_price IS NOT NULL AS bbo_complete
        FROM states
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW bitvavo_spot_l2_events AS
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
            'standard' AS feed_product,
            json_extract_string(decode(raw.payload_bytes), '$.source_channel') AS source_channel,
            json_extract_string(decode(raw.payload_bytes), '$.market') AS market,
            json_extract_string(decode(raw.payload_bytes), '$.message_type') AS message_type,
            json_extract_string(decode(raw.payload_bytes), '$.nonce') AS nonce,
            json_extract_string(decode(raw.payload_bytes), '$.venue_timestamp_ns')
                AS venue_timestamp_ns,
            json_extract_string(decode(raw.payload_bytes), '$.sequence_event')
                AS sequence_event,
            CAST(json_extract_string(event.value, '$.wire_order') AS BIGINT) AS wire_order,
            json_extract_string(event.value, '$.side') AS side,
            CAST(json_extract_string(event.value, '$.side_index') AS BIGINT) AS side_index,
            json_extract_string(event.value, '$.action') AS action,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.quantity') AS quantity
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'bitvavo'
         AND source.product = 'BTC-EUR'
         AND source.channel = json_extract_string(
             decode(raw.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound'
        LEFT JOIN LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
          ON true
        WHERE raw.venue = 'bitvavo'
          AND raw.product = 'BTC-EUR'
          AND raw.channel IN ('normalized_book', 'normalized_book_snapshot')
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )


def _create_bitvavo_mdpro_payload_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Create isolated DATA-1E Market Data Pro L2, trades, and optional ticker views."""

    connection.execute(
        """
        CREATE OR REPLACE VIEW bitvavo_mdpro_spot_l2_events AS
        WITH normalized AS MATERIALIZED (
            SELECT
                raw.session_id,
                raw.message_ordinal AS normalization_message_ordinal,
                json_transform(
                    decode(raw.payload_bytes),
                    '{
                        "raw_message_ordinal":"BIGINT",
                        "source_channel":"VARCHAR",
                        "market":"VARCHAR",
                        "message_type":"VARCHAR",
                        "deprecated_nonce":"VARCHAR",
                        "venue_timestamp_ns":"VARCHAR",
                        "sequence_start":"VARCHAR",
                        "sequence_end":"VARCHAR",
                        "sequence_event":"VARCHAR",
                        "events":[{
                            "wire_order":"BIGINT",
                            "side":"VARCHAR",
                            "side_index":"BIGINT",
                            "action":"VARCHAR",
                            "price":"VARCHAR",
                            "quantity":"VARCHAR"
                        }]
                    }'
                ) AS payload
            FROM raw_records AS raw
            WHERE raw.venue = 'bitvavo'
              AND raw.product = 'BTC-EUR'
              AND raw.channel = 'normalized_mdpro_book'
              AND raw.direction = 'local'
              AND raw.frame_type = 'marker'
        ),
        expanded AS (
            SELECT
                normalized.session_id,
                normalized.normalization_message_ordinal,
                normalized.payload.raw_message_ordinal AS raw_message_ordinal,
                normalized.payload.source_channel AS source_channel,
                normalized.payload.market AS market,
                normalized.payload.message_type AS message_type,
                normalized.payload.deprecated_nonce AS deprecated_nonce,
                normalized.payload.venue_timestamp_ns AS venue_timestamp_ns,
                normalized.payload.sequence_start AS sequence_start,
                normalized.payload.sequence_end AS sequence_end,
                normalized.payload.sequence_event AS sequence_event,
                unnest(normalized.payload.events) AS event
            FROM normalized
        )
        SELECT
            expanded.session_id,
            expanded.normalization_message_ordinal,
            expanded.raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            'market_data_pro' AS feed_product,
            expanded.source_channel,
            expanded.market,
            expanded.message_type,
            expanded.deprecated_nonce,
            expanded.venue_timestamp_ns,
            expanded.sequence_start,
            expanded.sequence_end,
            expanded.sequence_event,
            expanded.event.wire_order,
            expanded.event.side,
            expanded.event.side_index,
            expanded.event.action,
            expanded.event.price,
            expanded.event.quantity
        FROM expanded
        JOIN raw_records AS source
          ON source.session_id = expanded.session_id
         AND source.message_ordinal = expanded.raw_message_ordinal
         AND source.venue = 'bitvavo'
         AND source.product = 'BTC-EUR'
         AND source.channel = expanded.source_channel
         AND source.direction = 'inbound'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW bitvavo_mdpro_spot_trades AS
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
            'market_data_pro' AS feed_product,
            CAST(json_extract_string(event.value, '$.event_index') AS BIGINT) AS event_index,
            json_extract_string(event.value, '$.market') AS market,
            json_extract_string(event.value, '$.trade_id') AS trade_id,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.quantity') AS quantity,
            json_extract_string(event.value, '$.taker_side') AS taker_side,
            json_extract_string(event.value, '$.event_time_ms') AS event_time_ms,
            json_extract_string(event.value, '$.event_time_ns') AS event_time_ns
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'bitvavo'
         AND source.product = 'BTC-EUR'
         AND source.channel = 'mdpro_trades'
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'bitvavo'
          AND raw.product = 'BTC-EUR'
          AND raw.channel = 'normalized_mdpro_trades'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW bitvavo_mdpro_spot_bbo AS
        WITH updates AS (
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
                'market_data_pro' AS feed_product,
                json_extract_string(decode(raw.payload_bytes), '$.market') AS market,
                json_extract_string(decode(raw.payload_bytes), '$.bid_price')
                    AS bid_price_update,
                json_extract_string(decode(raw.payload_bytes), '$.bid_quantity')
                    AS bid_quantity_update,
                json_extract_string(decode(raw.payload_bytes), '$.ask_price')
                    AS ask_price_update,
                json_extract_string(decode(raw.payload_bytes), '$.ask_quantity')
                    AS ask_quantity_update,
                json_extract_string(decode(raw.payload_bytes), '$.last_price')
                    AS last_price_update
            FROM raw_records AS raw
            JOIN raw_records AS source
              ON source.session_id = raw.session_id
             AND source.message_ordinal = CAST(
                 json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
             )
             AND source.venue = 'bitvavo'
             AND source.product = 'BTC-EUR'
             AND source.channel = 'mdpro_ticker'
             AND source.direction = 'inbound'
            WHERE raw.venue = 'bitvavo'
              AND raw.product = 'BTC-EUR'
              AND raw.channel = 'normalized_mdpro_ticker'
              AND raw.direction = 'local'
              AND raw.frame_type = 'marker'
        ), states AS (
            SELECT
                updates.*,
                last_value(bid_price_update IGNORE NULLS) OVER feed_order AS bid_price,
                last_value(bid_quantity_update IGNORE NULLS) OVER feed_order AS bid_quantity,
                last_value(ask_price_update IGNORE NULLS) OVER feed_order AS ask_price,
                last_value(ask_quantity_update IGNORE NULLS) OVER feed_order AS ask_quantity,
                last_value(last_price_update IGNORE NULLS) OVER feed_order AS last_price
            FROM updates
            WINDOW feed_order AS (
                PARTITION BY session_id
                ORDER BY raw_message_ordinal
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            )
        )
        SELECT
            *,
            bid_price IS NOT NULL AND ask_price IS NOT NULL AS bbo_complete
        FROM states
        """
    )


def _create_binance_payload_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Create DATA-1F views from source-linked, string-preserving local markers."""

    connection.execute(
        """
        CREATE OR REPLACE VIEW binance_spot_trades AS
        SELECT
            marker.session_id,
            marker.message_ordinal AS normalization_message_ordinal,
            CAST(json_extract_string(decode(marker.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(marker.payload_bytes), '$.symbol') AS symbol,
            json_extract_string(decode(marker.payload_bytes), '$.trade_id') AS trade_id,
            json_extract_string(decode(marker.payload_bytes), '$.price') AS price,
            json_extract_string(decode(marker.payload_bytes), '$.quantity') AS quantity,
            json_extract_string(decode(marker.payload_bytes), '$.exchange_event_time')
                AS exchange_event_time,
            json_extract_string(decode(marker.payload_bytes), '$.trade_time') AS trade_time,
            json_extract_string(decode(marker.payload_bytes), '$.timestamp_unit')
                AS timestamp_unit,
            CAST(json_extract(decode(marker.payload_bytes), '$.buyer_was_maker') AS BOOLEAN)
                AS buyer_was_maker,
            json_extract_string(decode(marker.payload_bytes), '$.aggressor_side')
                AS aggressor_side
        FROM raw_records AS marker
        JOIN raw_records AS source
          ON source.session_id = marker.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(marker.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'binance'
         AND source.product = 'BTCUSDT-SPOT'
         AND source.channel = json_extract_string(
             decode(marker.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound'
        WHERE marker.venue = 'binance'
          AND marker.product = 'BTCUSDT-SPOT'
          AND marker.channel = 'normalized_spot_trade'
          AND marker.direction = 'local'
          AND marker.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW binance_spot_bbo AS
        SELECT
            marker.session_id,
            marker.message_ordinal AS normalization_message_ordinal,
            CAST(json_extract_string(decode(marker.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(marker.payload_bytes), '$.symbol') AS symbol,
            json_extract_string(decode(marker.payload_bytes), '$.update_id') AS update_id,
            json_extract_string(decode(marker.payload_bytes), '$.bid_price') AS bid_price,
            json_extract_string(decode(marker.payload_bytes), '$.bid_quantity') AS bid_quantity,
            json_extract_string(decode(marker.payload_bytes), '$.ask_price') AS ask_price,
            json_extract_string(decode(marker.payload_bytes), '$.ask_quantity') AS ask_quantity
        FROM raw_records AS marker
        JOIN raw_records AS source
          ON source.session_id = marker.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(marker.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'binance'
         AND source.product = 'BTCUSDT-SPOT'
         AND source.channel = json_extract_string(
             decode(marker.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound'
        WHERE marker.venue = 'binance'
          AND marker.product = 'BTCUSDT-SPOT'
          AND marker.channel = 'normalized_spot_bbo'
          AND marker.direction = 'local'
          AND marker.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW binance_spot_l2_events AS
        SELECT
            marker.session_id,
            marker.message_ordinal AS normalization_message_ordinal,
            CAST(json_extract_string(decode(marker.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(marker.payload_bytes), '$.source_channel')
                AS source_channel,
            json_extract_string(decode(marker.payload_bytes), '$.message_type') AS message_type,
            json_extract_string(decode(marker.payload_bytes), '$.symbol') AS symbol,
            json_extract_string(decode(marker.payload_bytes), '$.exchange_event_time')
                AS exchange_event_time,
            json_extract_string(decode(marker.payload_bytes), '$.last_update_id')
                AS last_update_id,
            json_extract_string(decode(marker.payload_bytes), '$.first_update_id')
                AS first_update_id,
            json_extract_string(decode(marker.payload_bytes), '$.final_update_id')
                AS final_update_id,
            json_extract_string(decode(marker.payload_bytes), '$.sequence_event')
                AS sequence_event,
            CAST(json_extract_string(event.value, '$.wire_order') AS BIGINT) AS wire_order,
            json_extract_string(event.value, '$.side') AS side,
            CAST(json_extract_string(event.value, '$.side_index') AS BIGINT) AS side_index,
            json_extract_string(event.value, '$.action') AS action,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.quantity') AS quantity
        FROM raw_records AS marker
        JOIN raw_records AS source
          ON source.session_id = marker.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(marker.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'binance'
         AND source.product = 'BTCUSDT-SPOT'
         AND source.channel = json_extract_string(
             decode(marker.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound'
        LEFT JOIN LATERAL json_each(decode(marker.payload_bytes), '$.events') AS event
          ON true
        WHERE marker.venue = 'binance'
          AND marker.product = 'BTCUSDT-SPOT'
          AND marker.channel = 'normalized_spot_depth'
          AND marker.direction = 'local'
          AND marker.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW binance_usdm_context AS
        SELECT
            marker.session_id,
            marker.message_ordinal AS normalization_message_ordinal,
            CAST(json_extract_string(decode(marker.payload_bytes), '$.raw_message_ordinal')
                AS BIGINT) AS raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            json_extract_string(decode(marker.payload_bytes), '$.source_channel')
                AS source_channel,
            json_extract_string(decode(marker.payload_bytes), '$.context_type') AS context_type,
            json_extract_string(decode(marker.payload_bytes), '$.symbol') AS symbol,
            json_extract_string(decode(marker.payload_bytes), '$.exchange_event_time')
                AS exchange_event_time,
            json_extract_string(decode(marker.payload_bytes), '$.transaction_time')
                AS transaction_time,
            json_extract_string(decode(marker.payload_bytes), '$.update_id') AS update_id,
            json_extract_string(decode(marker.payload_bytes), '$.aggregate_trade_id')
                AS aggregate_trade_id,
            json_extract_string(decode(marker.payload_bytes), '$.first_trade_id')
                AS first_trade_id,
            json_extract_string(decode(marker.payload_bytes), '$.last_trade_id') AS last_trade_id,
            json_extract_string(decode(marker.payload_bytes), '$.price') AS price,
            json_extract_string(decode(marker.payload_bytes), '$.quantity') AS quantity,
            json_extract_string(decode(marker.payload_bytes), '$.normal_quantity')
                AS normal_quantity,
            json_extract_string(decode(marker.payload_bytes), '$.bid_price') AS bid_price,
            json_extract_string(decode(marker.payload_bytes), '$.bid_quantity') AS bid_quantity,
            json_extract_string(decode(marker.payload_bytes), '$.ask_price') AS ask_price,
            json_extract_string(decode(marker.payload_bytes), '$.ask_quantity') AS ask_quantity,
            json_extract_string(decode(marker.payload_bytes), '$.mark_price') AS mark_price,
            json_extract_string(decode(marker.payload_bytes), '$.index_price') AS index_price,
            json_extract_string(decode(marker.payload_bytes), '$.estimated_settle_price')
                AS estimated_settle_price,
            json_extract_string(decode(marker.payload_bytes), '$.mark_moving_average')
                AS mark_moving_average,
            json_extract_string(decode(marker.payload_bytes), '$.funding_rate') AS funding_rate,
            json_extract_string(decode(marker.payload_bytes), '$.next_funding_time')
                AS next_funding_time,
            json_extract_string(decode(marker.payload_bytes), '$.open_interest')
                AS open_interest,
            json_extract_string(decode(marker.payload_bytes), '$.side') AS side,
            json_extract_string(decode(marker.payload_bytes), '$.order_type') AS order_type,
            json_extract_string(decode(marker.payload_bytes), '$.time_in_force') AS time_in_force,
            json_extract_string(decode(marker.payload_bytes), '$.order_status') AS order_status,
            json_extract_string(decode(marker.payload_bytes), '$.average_price') AS average_price,
            json_extract_string(decode(marker.payload_bytes), '$.last_filled_quantity')
                AS last_filled_quantity,
            json_extract_string(decode(marker.payload_bytes), '$.accumulated_filled_quantity')
                AS accumulated_filled_quantity,
            CAST(json_extract(decode(marker.payload_bytes), '$.buyer_was_maker') AS BOOLEAN)
                AS buyer_was_maker,
            json_extract_string(decode(marker.payload_bytes), '$.aggressor_side')
                AS aggressor_side,
            CAST(json_extract(decode(marker.payload_bytes), '$.rpi_excluded') AS BOOLEAN)
                AS rpi_excluded,
            CAST(json_extract(decode(marker.payload_bytes), '$.incomplete_snapshot') AS BOOLEAN)
                AS incomplete_snapshot
        FROM raw_records AS marker
        JOIN raw_records AS source
          ON source.session_id = marker.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(marker.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'binance'
         AND source.product = 'BTCUSDT-USDS-M-PERPETUAL'
         AND source.channel = json_extract_string(
             decode(marker.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound'
        WHERE marker.venue = 'binance'
          AND marker.product = 'BTCUSDT-USDS-M-PERPETUAL'
          AND marker.channel = 'normalized_usdm_context'
          AND marker.direction = 'local'
          AND marker.frame_type = 'marker'
        """
    )


def _create_deribit_payload_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Create DATA-1H views from exact source-linked local normalizations."""

    connection.execute(
        """
        CREATE OR REPLACE VIEW deribit_btc_trades AS
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
            CAST(json_extract_string(event.value, '$.wire_order') AS BIGINT) AS wire_order,
            json_extract_string(event.value, '$.trade_seq') AS trade_seq,
            json_extract_string(event.value, '$.trade_id') AS trade_id,
            json_extract_string(event.value, '$.direction') AS direction,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.amount') AS amount,
            json_extract_string(event.value, '$.event_time_ms') AS event_time_ms,
            json_extract_string(event.value, '$.tick_direction') AS tick_direction,
            json_extract_string(event.value, '$.index_price') AS index_price,
            json_extract_string(event.value, '$.mark_price') AS mark_price,
            json_extract_string(event.value, '$.contracts') AS contracts,
            json_extract_string(event.value, '$.liquidation') AS liquidation,
            json_extract_string(event.value, '$.iv') AS implied_volatility
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'deribit'
         AND source.product = 'BTC-DERIVATIVES'
         AND source.channel = json_extract_string(
             decode(raw.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'deribit'
          AND raw.product = 'BTC-DERIVATIVES'
          AND raw.channel = 'normalized_trades'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW deribit_btc_l2_events AS
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
            json_extract_string(decode(raw.payload_bytes), '$.instrument_name') AS instrument_name,
            json_extract_string(decode(raw.payload_bytes), '$.message_type') AS message_type,
            json_extract_string(decode(raw.payload_bytes), '$.event_time_ms') AS event_time_ms,
            json_extract_string(decode(raw.payload_bytes), '$.change_id') AS change_id,
            json_extract_string(decode(raw.payload_bytes), '$.prev_change_id') AS prev_change_id,
            CAST(json_extract_string(event.value, '$.wire_order') AS BIGINT) AS wire_order,
            json_extract_string(event.value, '$.side') AS side,
            CAST(json_extract_string(event.value, '$.side_index') AS BIGINT) AS side_index,
            json_extract_string(event.value, '$.action') AS action,
            json_extract_string(event.value, '$.price') AS price,
            json_extract_string(event.value, '$.quantity') AS quantity
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'deribit'
         AND source.product = 'BTC-DERIVATIVES'
         AND source.channel = json_extract_string(
             decode(raw.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound',
             LATERAL json_each(decode(raw.payload_bytes), '$.events') AS event
        WHERE raw.venue = 'deribit'
          AND raw.product = 'BTC-DERIVATIVES'
          AND raw.channel = 'normalized_book'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW deribit_btc_derivative_context AS
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
            CASE raw.channel
                WHEN 'normalized_derivative_ticker' THEN 'ticker'
                WHEN 'normalized_index' THEN 'index_price'
                WHEN 'normalized_dvol' THEN 'volatility_index'
            END AS context_type,
            json_extract_string(decode(raw.payload_bytes), '$.instrument_name') AS instrument_name,
            json_extract_string(decode(raw.payload_bytes), '$.instrument_kind') AS instrument_kind,
            json_extract_string(decode(raw.payload_bytes), '$.index_name') AS index_name,
            json_extract_string(decode(raw.payload_bytes), '$.event_time_ms') AS event_time_ms,
            json_extract_string(decode(raw.payload_bytes), '$.state') AS state,
            json_extract_string(decode(raw.payload_bytes), '$.mark_price') AS mark_price,
            json_extract_string(decode(raw.payload_bytes), '$.index_price') AS index_price,
            json_extract_string(decode(raw.payload_bytes), '$.open_interest') AS open_interest,
            json_extract_string(decode(raw.payload_bytes), '$.current_funding') AS current_funding,
            json_extract_string(decode(raw.payload_bytes), '$.funding_8h') AS funding_8h,
            json_extract_string(decode(raw.payload_bytes), '$.best_bid_price') AS best_bid_price,
            json_extract_string(decode(raw.payload_bytes), '$.best_bid_amount') AS best_bid_amount,
            json_extract_string(decode(raw.payload_bytes), '$.best_ask_price') AS best_ask_price,
            json_extract_string(decode(raw.payload_bytes), '$.best_ask_amount') AS best_ask_amount,
            json_extract_string(decode(raw.payload_bytes), '$.volatility') AS volatility
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'deribit'
         AND source.product = 'BTC-DERIVATIVES'
         AND source.channel = json_extract_string(
             decode(raw.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound'
        WHERE raw.venue = 'deribit'
          AND raw.product = 'BTC-DERIVATIVES'
          AND raw.channel IN ('normalized_derivative_ticker', 'normalized_index', 'normalized_dvol')
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW deribit_btc_option_sample AS
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
            json_extract_string(decode(raw.payload_bytes), '$.instrument_name') AS instrument_name,
            json_extract_string(decode(raw.payload_bytes), '$.expiry_code') AS expiry_code,
            json_extract_string(decode(raw.payload_bytes), '$.strike') AS strike,
            json_extract_string(decode(raw.payload_bytes), '$.option_type') AS option_type,
            json_extract_string(decode(raw.payload_bytes), '$.event_time_ms') AS event_time_ms,
            json_extract_string(decode(raw.payload_bytes), '$.state') AS state,
            json_extract_string(decode(raw.payload_bytes), '$.underlying_index')
                AS underlying_index,
            json_extract_string(decode(raw.payload_bytes), '$.underlying_price')
                AS underlying_price,
            json_extract_string(decode(raw.payload_bytes), '$.mark_price') AS mark_price,
            json_extract_string(decode(raw.payload_bytes), '$.mark_iv') AS mark_iv,
            json_extract_string(decode(raw.payload_bytes), '$.bid_iv') AS bid_iv,
            json_extract_string(decode(raw.payload_bytes), '$.ask_iv') AS ask_iv,
            json_extract_string(decode(raw.payload_bytes), '$.best_bid_price') AS best_bid_price,
            json_extract_string(decode(raw.payload_bytes), '$.best_bid_amount') AS best_bid_amount,
            json_extract_string(decode(raw.payload_bytes), '$.best_ask_price') AS best_ask_price,
            json_extract_string(decode(raw.payload_bytes), '$.best_ask_amount') AS best_ask_amount,
            json_extract_string(decode(raw.payload_bytes), '$.open_interest') AS open_interest,
            json_extract_string(decode(raw.payload_bytes), '$.greeks.delta') AS delta,
            json_extract_string(decode(raw.payload_bytes), '$.greeks.gamma') AS gamma,
            json_extract_string(decode(raw.payload_bytes), '$.greeks.vega') AS vega,
            json_extract_string(decode(raw.payload_bytes), '$.greeks.theta') AS theta,
            json_extract_string(decode(raw.payload_bytes), '$.greeks.rho') AS rho
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'deribit'
         AND source.product = 'BTC-DERIVATIVES'
         AND source.channel = json_extract_string(
             decode(raw.payload_bytes), '$.source_channel'
         )
         AND source.direction = 'inbound'
        WHERE raw.venue = 'deribit'
          AND raw.product = 'BTC-DERIVATIVES'
          AND raw.channel = 'normalized_option_ticker'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
        """
    )


def _create_polymarket_payload_views(connection: duckdb.DuckDBPyConnection) -> None:
    """Create bounded DATA-1I views without implying sequence or checksum coverage."""

    connection.execute(
        """
        CREATE OR REPLACE VIEW polymarket_crypto_market_metadata AS
        WITH metadata AS (
            SELECT
                raw.session_id,
                raw.message_ordinal AS normalization_message_ordinal,
                json_extract_string(decode(raw.payload_bytes), '$.record_type') AS record_type,
                json_extract_string(decode(raw.payload_bytes), '$.source_channel')
                    AS source_channel,
                json_extract_string(decode(raw.payload_bytes), '$.source_frame_channel')
                    AS source_frame_channel,
                CAST(
                    json_extract_string(decode(raw.payload_bytes), '$.frame_wire_order')
                    AS BIGINT
                ) AS frame_wire_order,
                CAST(
                    json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal')
                    AS BIGINT
                ) AS raw_message_ordinal,
                source.received_utc_ns,
                source.received_monotonic_ns,
                source.frame_type,
                source.payload_sha256,
                json_extract_string(decode(raw.payload_bytes), '$.event_id') AS event_id,
                json_extract_string(decode(raw.payload_bytes), '$.market_id') AS market_id,
                json_extract_string(decode(raw.payload_bytes), '$.market') AS market,
                json_extract_string(decode(raw.payload_bytes), '$.slug') AS slug,
                json_extract_string(decode(raw.payload_bytes), '$.question') AS question,
                json_extract_string(decode(raw.payload_bytes), '$.end_time') AS end_time,
                CAST(json_extract(decode(raw.payload_bytes), '$.active') AS BOOLEAN) AS active,
                CAST(json_extract(decode(raw.payload_bytes), '$.closed') AS BOOLEAN) AS closed,
                CAST(json_extract(decode(raw.payload_bytes), '$.enable_order_book') AS BOOLEAN)
                    AS enable_order_book,
                CAST(json_extract(decode(raw.payload_bytes), '$.accepting_orders') AS BOOLEAN)
                    AS accepting_orders,
                CAST(json_extract(decode(raw.payload_bytes), '$.neg_risk') AS BOOLEAN) AS neg_risk,
                CAST(json_extract_string(decode(raw.payload_bytes), '$.outcome_index') AS BIGINT)
                    AS outcome_index,
                json_extract_string(decode(raw.payload_bytes), '$.outcome') AS outcome,
                json_extract_string(decode(raw.payload_bytes), '$.asset_id') AS asset_id,
                json_extract_string(decode(raw.payload_bytes), '$.tick_size') AS tick_size,
                json_extract_string(decode(raw.payload_bytes), '$.min_order_size')
                    AS min_order_size,
                json_extract_string(decode(raw.payload_bytes), '$.old_tick_size')
                    AS old_tick_size,
                json_extract_string(decode(raw.payload_bytes), '$.new_tick_size')
                    AS new_tick_size,
                json_extract_string(decode(raw.payload_bytes), '$.timestamp_ms') AS timestamp_ms,
                json_extract_string(decode(raw.payload_bytes), '$.metadata_origin')
                    AS metadata_origin
            FROM raw_records AS raw
            LEFT JOIN raw_records AS source
              ON source.session_id = raw.session_id
             AND source.message_ordinal = CAST(
                 json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
             )
             AND source.venue = 'polymarket'
             AND source.product = 'BTC-CRYPTO-RESEARCH'
             AND source.channel = json_extract_string(
                 decode(raw.payload_bytes), '$.source_frame_channel'
             )
             AND source.direction = 'inbound'
            WHERE raw.venue = 'polymarket'
              AND raw.product = 'BTC-CRYPTO-RESEARCH'
              AND raw.channel = 'normalized_market_metadata'
              AND raw.direction = 'local'
              AND raw.frame_type = 'marker'
        )
        SELECT *
        FROM metadata
        WHERE (
            record_type = 'configured_market'
            AND source_channel = 'local_config'
            AND raw_message_ordinal IS NULL
        ) OR (
            record_type = 'tick_size_change'
            AND source_channel = 'tick_size_change'
            AND source_frame_channel IN ('tick_size_change', 'market_batch')
            AND frame_wire_order IS NOT NULL
            AND raw_message_ordinal IS NOT NULL
            AND payload_sha256 IS NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW polymarket_crypto_l2_events AS
        WITH normalized AS MATERIALIZED (
            SELECT
                raw.session_id,
                raw.message_ordinal AS normalization_message_ordinal,
                json_transform(
                    decode(raw.payload_bytes),
                    '{
                        "raw_message_ordinal":"BIGINT",
                        "source_channel":"VARCHAR",
                        "source_frame_channel":"VARCHAR",
                        "frame_wire_order":"BIGINT",
                        "market":"VARCHAR",
                        "asset_id":"VARCHAR",
                        "message_type":"VARCHAR",
                        "timestamp_ms":"VARCHAR",
                        "opaque_hash":"VARCHAR",
                        "sequence_available":"BOOLEAN",
                        "checksum_available":"BOOLEAN",
                        "events":[{
                            "wire_order":"BIGINT",
                            "side":"VARCHAR",
                            "side_index":"BIGINT",
                            "action":"VARCHAR",
                            "price":"VARCHAR",
                            "size":"VARCHAR",
                            "opaque_hash":"VARCHAR"
                        }]
                    }'
                ) AS payload
            FROM raw_records AS raw
            WHERE raw.venue = 'polymarket'
              AND raw.product = 'BTC-CRYPTO-RESEARCH'
              AND raw.channel = 'normalized_l2'
              AND raw.direction = 'local'
              AND raw.frame_type = 'marker'
              AND json_extract_string(
                  decode(raw.payload_bytes), '$.source_channel'
              ) IN ('book', 'price_change')
        ),
        expanded AS (
            SELECT
                normalized.session_id,
                normalized.normalization_message_ordinal,
                normalized.payload.raw_message_ordinal AS raw_message_ordinal,
                normalized.payload.source_channel AS source_channel,
                normalized.payload.source_frame_channel AS source_frame_channel,
                normalized.payload.frame_wire_order AS frame_wire_order,
                normalized.payload.market AS market,
                normalized.payload.asset_id AS asset_id,
                normalized.payload.message_type AS message_type,
                normalized.payload.timestamp_ms AS timestamp_ms,
                normalized.payload.opaque_hash AS opaque_hash,
                normalized.payload.sequence_available AS sequence_available,
                normalized.payload.checksum_available AS checksum_available,
                unnest(normalized.payload.events) AS event
            FROM normalized
        )
        SELECT
            expanded.session_id,
            expanded.normalization_message_ordinal,
            expanded.raw_message_ordinal,
            source.received_utc_ns,
            source.received_monotonic_ns,
            source.frame_type,
            source.payload_sha256,
            expanded.source_channel,
            expanded.source_frame_channel,
            expanded.frame_wire_order,
            expanded.market,
            expanded.asset_id,
            expanded.message_type,
            expanded.timestamp_ms,
            expanded.opaque_hash,
            expanded.sequence_available,
            expanded.checksum_available,
            expanded.event.wire_order,
            expanded.event.side,
            expanded.event.side_index,
            expanded.event.action,
            expanded.event.price,
            expanded.event.size,
            expanded.event.opaque_hash AS change_hash
        FROM expanded
        JOIN raw_records AS source
          ON source.session_id = expanded.session_id
         AND source.message_ordinal = expanded.raw_message_ordinal
         AND source.venue = 'polymarket'
         AND source.product = 'BTC-CRYPTO-RESEARCH'
         AND source.channel = expanded.source_frame_channel
         AND source.direction = 'inbound'
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW polymarket_crypto_bbo AS
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
            json_extract_string(decode(raw.payload_bytes), '$.source_frame_channel')
                AS source_frame_channel,
            CAST(
                json_extract_string(decode(raw.payload_bytes), '$.frame_wire_order') AS BIGINT
            ) AS frame_wire_order,
            json_extract_string(decode(raw.payload_bytes), '$.market') AS market,
            json_extract_string(decode(raw.payload_bytes), '$.asset_id') AS asset_id,
            json_extract_string(decode(raw.payload_bytes), '$.best_bid') AS best_bid,
            json_extract_string(decode(raw.payload_bytes), '$.best_ask') AS best_ask,
            json_extract_string(decode(raw.payload_bytes), '$.spread') AS spread,
            json_extract_string(decode(raw.payload_bytes), '$.timestamp_ms') AS timestamp_ms
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'polymarket'
         AND source.product = 'BTC-CRYPTO-RESEARCH'
         AND source.channel = json_extract_string(
             decode(raw.payload_bytes), '$.source_frame_channel'
         )
         AND source.direction = 'inbound'
        WHERE raw.venue = 'polymarket'
          AND raw.product = 'BTC-CRYPTO-RESEARCH'
          AND raw.channel = 'normalized_bbo'
          AND raw.direction = 'local'
          AND raw.frame_type = 'marker'
          AND json_extract_string(
              decode(raw.payload_bytes), '$.source_channel'
          ) IN ('price_change', 'best_bid_ask')
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW polymarket_crypto_last_trade_prices AS
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
            json_extract_string(decode(raw.payload_bytes), '$.source_frame_channel')
                AS source_frame_channel,
            CAST(
                json_extract_string(decode(raw.payload_bytes), '$.frame_wire_order') AS BIGINT
            ) AS frame_wire_order,
            json_extract_string(decode(raw.payload_bytes), '$.market') AS market,
            json_extract_string(decode(raw.payload_bytes), '$.asset_id') AS asset_id,
            json_extract_string(decode(raw.payload_bytes), '$.price') AS price,
            json_extract_string(decode(raw.payload_bytes), '$.size') AS size,
            json_extract_string(decode(raw.payload_bytes), '$.fee_rate_bps') AS fee_rate_bps,
            json_extract_string(decode(raw.payload_bytes), '$.side') AS side,
            json_extract_string(decode(raw.payload_bytes), '$.timestamp_ms') AS timestamp_ms,
            json_extract_string(decode(raw.payload_bytes), '$.transaction_hash')
                AS transaction_hash,
            CAST(json_extract(decode(raw.payload_bytes), '$.complete_trade_tape') AS BOOLEAN)
                AS complete_trade_tape
        FROM raw_records AS raw
        JOIN raw_records AS source
          ON source.session_id = raw.session_id
         AND source.message_ordinal = CAST(
             json_extract_string(decode(raw.payload_bytes), '$.raw_message_ordinal') AS BIGINT
         )
         AND source.venue = 'polymarket'
         AND source.product = 'BTC-CRYPTO-RESEARCH'
         AND source.channel = json_extract_string(
             decode(raw.payload_bytes), '$.source_frame_channel'
         )
         AND source.direction = 'inbound'
        WHERE raw.venue = 'polymarket'
          AND raw.product = 'BTC-CRYPTO-RESEARCH'
          AND raw.channel = 'normalized_last_trade_price'
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
