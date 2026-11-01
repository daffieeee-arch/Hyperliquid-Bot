"""Standalone, bounded Coinbase Exchange public BTC-USD research capture.

This adapter is deliberately outside the admitted native collector lanes. It
never authenticates, backfills, reconnects, submits orders or claims completeness.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import re
import signal
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Protocol, cast

import duckdb
from websockets.asyncio.client import connect
from websockets.exceptions import PayloadTooBig, WebSocketException

from .parquet_research import (
    ParquetResearchWriter,
    ParquetRotation,
    create_research_catalog,
)
from .raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    CapturedApplicationPayload,
    FrameType,
    MessageDirection,
    NanosecondClock,
    PayloadEncoding,
    RawResearchRecord,
    RawResearchSink,
    capture_application_payload,
)

COINBASE_PUBLIC_WEBSOCKET_URL: Final = "wss://ws-feed.exchange.coinbase.com"
COINBASE_RESEARCH_PRODUCT: Final = "BTC-USD"
COINBASE_FEED_PRODUCT: Final = "exchange_public_level2_batch_matches_heartbeat"
MAX_CAPTURE_SECONDS: Final = 600.0
_CHANNELS: Final = ("level2_batch", "matches", "heartbeat")
_SUBSCRIPTION: Final = json.dumps(
    {"type": "subscribe", "product_ids": [COINBASE_RESEARCH_PRODUCT], "channels": _CHANNELS},
    separators=(",", ":"),
)
_DECIMAL: Final = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_TIME: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z\Z")
_TRANSPORT_LOGGER: Final = logging.Logger(
    "hyperliquid_bot.coinbase_public_research_transport", level=logging.CRITICAL + 1
)
_TRANSPORT_LOGGER.disabled = True
_TRANSPORT_LOGGER.propagate = False
_TRANSPORT_LOGGER.addHandler(logging.NullHandler())


class CoinbaseCaptureError(RuntimeError):
    """Sanitized terminal capture failure; never includes an upstream payload."""


class CoinbaseDataIntegrityError(CoinbaseCaptureError):
    """Invalid public schema, identity, or session boundary."""


class CoinbaseSinkError(CoinbaseCaptureError):
    """A failed append cannot be retried without ambiguous lineage."""


class WebSocketConnection(Protocol):
    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]


@dataclass(frozen=True, slots=True)
class CoinbasePublicResearchConfig:
    """Short measurement bounds, independent of the existing native runtime."""

    heartbeat_timeout_seconds: float = 5.0
    max_application_payload_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        timeout = self.heartbeat_timeout_seconds
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("heartbeat_timeout_seconds must be finite and in (0, 60].")
        limit = self.max_application_payload_bytes
        if type(limit) is not int or not 1 <= limit <= 16 * 1024 * 1024:
            raise ValueError("max_application_payload_bytes must be in [1, 16777216].")


@dataclass(frozen=True, slots=True)
class CoinbaseNormalizedFrame:
    payload: dict[str, object]
    quality_events: tuple[dict[str, object], ...] = ()


class CoinbaseSessionState:
    """One snapshot boundary and trade-ID coverage; no full-feed sequence claim.

    Match/heartbeat sequence numbers refer to a broader exchange stream. Their
    jumps are retained, but are not interpreted as missing subscribed messages.
    """

    def __init__(self) -> None:
        self.acknowledged = False
        self.has_snapshot = False
        self.last_trade_id: int | None = None
        self.last_heartbeat_trade_id: int | None = None

    def normalize(self, document: dict[str, object], raw_ordinal: int) -> CoinbaseNormalizedFrame:
        message_type = document.get("type")
        if type(message_type) is not str or type(raw_ordinal) is not int or raw_ordinal <= 0:
            raise CoinbaseDataIntegrityError("Coinbase message identity failed validation.")
        if message_type == "error":
            raise CoinbaseDataIntegrityError("Coinbase rejected the public subscription.")
        if message_type == "subscriptions":
            return self._acknowledge(document)
        if not self.acknowledged or document.get("product_id") != COINBASE_RESEARCH_PRODUCT:
            raise CoinbaseDataIntegrityError("Coinbase product or subscription identity failed.")
        normalized: dict[str, object] = {
            "feed_product": COINBASE_FEED_PRODUCT,
            "product_id": COINBASE_RESEARCH_PRODUCT,
            "instrument_type": "spot",
            "base_asset": "BTC",
            "quote_asset": "USD",
            "raw_message_ordinal": raw_ordinal,
            "source_message_type": message_type,
        }
        quality: list[dict[str, object]] = []
        if message_type == "snapshot":
            if self.has_snapshot:
                raise CoinbaseDataIntegrityError("Coinbase sent a duplicate session snapshot.")
            normalized.update(event="l2", action="snapshot", levels=_snapshot_levels(document))
            self.has_snapshot = True
        elif message_type == "l2update":
            if not self.has_snapshot:
                raise CoinbaseDataIntegrityError("Coinbase L2 update preceded its snapshot.")
            normalized.update(
                event="l2",
                action="update",
                event_time=_timestamp(document),
                levels=_update_levels(document),
            )
        elif message_type in {"match", "last_match"}:
            trade_id = _integer(document, "trade_id")
            maker_side = document.get("side")
            if type(maker_side) is not str or maker_side not in {"buy", "sell"}:
                raise CoinbaseDataIntegrityError("Coinbase trade side failed validation.")
            normalized.update(
                event="trade_anchor" if message_type == "last_match" else "trade",
                trade_id=str(trade_id),
                sequence=str(_integer(document, "sequence")),
                maker_side=maker_side,
                aggressor_side="sell" if maker_side == "buy" else "buy",
                price=_decimal(document.get("price"), allow_zero=False),
                size=_decimal(document.get("size"), allow_zero=False),
                event_time=_timestamp(document),
            )
            previous = self.last_trade_id
            if message_type == "last_match" and previous is not None:
                raise CoinbaseDataIntegrityError("Coinbase last_match was not the first trade.")
            if previous is not None and trade_id <= previous:
                raise CoinbaseDataIntegrityError("Coinbase trade IDs did not strictly advance.")
            if previous is not None and trade_id > previous + 1:
                quality.append(
                    {
                        "event": "trade_gap_detected",
                        "first_missing_trade_id": str(previous + 1),
                        "last_missing_trade_id": str(trade_id - 1),
                        "backfilled": False,
                    }
                )
            if previous is None and message_type == "match":
                quality.append({"event": "trade_anchor_missing", "backfilled": False})
            self.last_trade_id = trade_id
        elif message_type == "heartbeat":
            trade_id = _integer(document, "last_trade_id")
            if self.last_heartbeat_trade_id is not None and trade_id < self.last_heartbeat_trade_id:
                raise CoinbaseDataIntegrityError("Coinbase heartbeat trade ID regressed.")
            normalized.update(
                event="heartbeat",
                last_trade_id=str(trade_id),
                sequence=str(_integer(document, "sequence")),
                event_time=_timestamp(document),
            )
            if self.last_trade_id is None or trade_id > self.last_trade_id:
                # Channel delivery can interleave. Preserve uncertainty rather than
                # pretending heartbeat sequence is a match-stream gap proof.
                quality.append(
                    {
                        "event": "trade_coverage_uncertain",
                        "heartbeat_last_trade_id": str(trade_id),
                        "last_observed_trade_id": (
                            None if self.last_trade_id is None else str(self.last_trade_id)
                        ),
                        "backfilled": False,
                    }
                )
            self.last_heartbeat_trade_id = trade_id
        else:
            raise CoinbaseDataIntegrityError("Coinbase sent an unsupported public message type.")
        return CoinbaseNormalizedFrame(normalized, tuple(quality))

    def _acknowledge(self, document: dict[str, object]) -> CoinbaseNormalizedFrame:
        channels = document.get("channels")
        if self.acknowledged or type(channels) is not list or len(channels) != len(_CHANNELS):
            raise CoinbaseDataIntegrityError("Coinbase subscription response failed validation.")
        names: list[str] = []
        for item in channels:
            if type(item) is not dict or item.get("product_ids") != [COINBASE_RESEARCH_PRODUCT]:
                raise CoinbaseDataIntegrityError("Coinbase subscription scope failed validation.")
            name = item.get("name")
            if type(name) is not str or name not in _CHANNELS:
                raise CoinbaseDataIntegrityError("Coinbase acknowledged an unexpected channel.")
            names.append(name)
        if set(names) != set(_CHANNELS):
            raise CoinbaseDataIntegrityError("Coinbase did not acknowledge all requested channels.")
        self.acknowledged = True
        return CoinbaseNormalizedFrame(
            {
                "event": "subscriptions_active",
                "feed_product": COINBASE_FEED_PRODUCT,
                "authenticated": False,
                "channels": list(_CHANNELS),
            }
        )


def _decimal(value: object, *, allow_zero: bool) -> str:
    if type(value) is not str or len(value) > 128 or _DECIMAL.fullmatch(value) is None:
        raise CoinbaseDataIntegrityError("Coinbase decimal text failed validation.")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise CoinbaseDataIntegrityError("Coinbase decimal text failed validation.") from None
    if number < 0 or (not allow_zero and number == 0):
        raise CoinbaseDataIntegrityError("Coinbase decimal range failed validation.")
    return value


def _integer(document: dict[str, object], name: str) -> int:
    value = document.get(name)
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        raise CoinbaseDataIntegrityError("Coinbase integer failed validation.")
    return value


def _timestamp(document: dict[str, object]) -> str:
    value = document.get("time")
    if type(value) is not str or _TIME.fullmatch(value) is None:
        raise CoinbaseDataIntegrityError("Coinbase event timestamp failed validation.")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise CoinbaseDataIntegrityError("Coinbase event timestamp failed validation.") from None
    return value


def _snapshot_levels(document: dict[str, object]) -> list[dict[str, str]]:
    levels: list[dict[str, str]] = []
    for field, side in (("bids", "buy"), ("asks", "sell")):
        rows = document.get(field)
        if type(rows) is not list or not rows:
            raise CoinbaseDataIntegrityError("Coinbase snapshot levels failed validation.")
        prices: set[Decimal] = set()
        for row in rows:
            if type(row) is not list or len(row) != 2:
                raise CoinbaseDataIntegrityError("Coinbase snapshot level failed validation.")
            price = _decimal(row[0], allow_zero=False)
            size = _decimal(row[1], allow_zero=False)
            if Decimal(price) in prices:
                raise CoinbaseDataIntegrityError("Coinbase snapshot duplicated a price level.")
            prices.add(Decimal(price))
            levels.append({"side": side, "price": price, "size": size})
    return levels


def _update_levels(document: dict[str, object]) -> list[dict[str, str]]:
    rows = document.get("changes")
    if type(rows) is not list or not rows:
        raise CoinbaseDataIntegrityError("Coinbase L2 changes failed validation.")
    levels: list[dict[str, str]] = []
    for row in rows:
        if (
            type(row) is not list
            or len(row) != 3
            or type(row[0]) is not str
            or row[0] not in {"buy", "sell"}
        ):
            raise CoinbaseDataIntegrityError("Coinbase L2 change failed validation.")
        levels.append(
            {
                "side": row[0],
                "price": _decimal(row[1], allow_zero=False),
                "size": _decimal(row[2], allow_zero=True),
            }
        )
    return levels


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CoinbaseDataIntegrityError("Coinbase JSON duplicated a key.")
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    raise CoinbaseDataIntegrityError("Coinbase JSON contained a non-finite number.")


def _decode(payload: bytes) -> dict[str, object]:
    try:
        value: object = json.loads(
            payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (ValueError, UnicodeError, RecursionError):
        raise CoinbaseDataIntegrityError("Coinbase JSON decoding failed.") from None
    if type(value) is not dict:
        raise CoinbaseDataIntegrityError("Coinbase JSON root was not an object.")
    return cast(dict[str, object], value)


class CoinbasePublicResearchCollector:
    """Single-session short capture; transport failure ends the measurement."""

    def __init__(
        self,
        sink: RawResearchSink,
        *,
        config: CoinbasePublicResearchConfig | None = None,
        connection_factory: ConnectionFactory | None = None,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        monotonic: Callable[[], float] = time.monotonic,
        session_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._sink = sink
        self._config = config if config is not None else CoinbasePublicResearchConfig()
        self._connection_factory = (
            connection_factory
            if connection_factory is not None
            else lambda: _public_connection(self._config)
        )
        self._utc_ns = utc_ns
        self._monotonic_ns = monotonic_ns
        self._monotonic = monotonic
        self._session_id = session_id_factory()
        self._ordinal = 0
        self._last_inbound_ordinal: int | None = None
        self._sink_failed = False
        self._used = False

    async def capture_for(
        self, duration_seconds: float, *, stop_event: asyncio.Event | None = None
    ) -> None:
        duration = _duration(duration_seconds)
        if self._used:
            raise ValueError("Coinbase collector instances are single-use.")
        self._used = True
        stop = stop_event if stop_event is not None else asyncio.Event()
        await self._marker(
            "session",
            event="session_started",
            endpoint=COINBASE_PUBLIC_WEBSOCKET_URL,
            feed_product=COINBASE_FEED_PRODUCT,
            authenticated=False,
        )
        if stop.is_set():
            await self._marker(
                "session",
                event="session_stopped",
                reason="operator_stop",
                coverage="unqualified",
                complete=False,
            )
            return
        worker = asyncio.create_task(self._session())
        stopped = asyncio.create_task(stop.wait())
        reason = "duration_reached"
        try:
            done, _ = await asyncio.wait(
                (worker, stopped), timeout=duration, return_when=asyncio.FIRST_COMPLETED
            )
            if worker in done:
                await worker
            if stopped in done:
                reason = "operator_stop"
        finally:
            worker.cancel()
            stopped.cancel()
            await asyncio.gather(worker, stopped, return_exceptions=True)
        await self._marker(
            "session",
            event="session_stopped",
            reason=reason,
            coverage="unqualified",
            complete=False,
        )

    async def _session(self) -> None:
        state = CoinbaseSessionState()
        try:
            async with self._connection_factory() as connection:
                # Coinbase closes clients without a subscription within five seconds.
                async with asyncio.timeout(4.0):
                    await connection.send(_SUBSCRIPTION)
                await self._append(
                    self._capture(_SUBSCRIPTION), "subscription", MessageDirection.OUTBOUND
                )
                heartbeat_deadline = self._monotonic() + self._config.heartbeat_timeout_seconds
                while True:
                    remaining = heartbeat_deadline - self._monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    frame = await asyncio.wait_for(connection.recv(), timeout=remaining)
                    captured = self._capture(frame)
                    received_at = self._monotonic()
                    ordinal = await self._append(
                        captured, COINBASE_FEED_PRODUCT, MessageDirection.INBOUND
                    )
                    self._last_inbound_ordinal = ordinal
                    if len(captured.payload_bytes) > self._config.max_application_payload_bytes:
                        raise CoinbaseDataIntegrityError("Coinbase payload exceeded its bound.")
                    normalized = state.normalize(_decode(captured.payload_bytes), ordinal)
                    await self._marker("normalized", **normalized.payload)
                    for quality in normalized.quality_events:
                        await self._marker("data_quality", raw_message_ordinal=ordinal, **quality)
                    if normalized.payload.get("event") == "heartbeat":
                        heartbeat_deadline = received_at + self._config.heartbeat_timeout_seconds
        except asyncio.CancelledError:
            raise
        except CoinbaseSinkError:
            raise
        except TimeoutError:
            await self._marker(
                "data_quality", event="heartbeat_or_subscribe_timeout", complete=False
            )
            raise CoinbaseCaptureError(
                "Coinbase public heartbeat/subscription timed out."
            ) from None
        except (CoinbaseDataIntegrityError, PayloadTooBig):
            await self._marker(
                "data_quality",
                event="integrity_failure",
                complete=False,
                raw_message_ordinal=self._last_inbound_ordinal,
            )
            raise CoinbaseDataIntegrityError(
                "Coinbase public capture failed integrity checks."
            ) from None
        except (WebSocketException, OSError):
            await self._marker(
                "data_quality", event="transport_gap", backfilled=False, complete=False
            )
            raise CoinbaseCaptureError("Coinbase public transport disconnected.") from None
        except Exception:
            await self._marker("data_quality", event="capture_boundary_failure", complete=False)
            raise CoinbaseCaptureError("Coinbase public capture boundary failed.") from None

    def _capture(self, frame: str | bytes) -> CapturedApplicationPayload:
        return capture_application_payload(
            frame, utc_ns=self._utc_ns, monotonic_ns=self._monotonic_ns
        )

    async def _append(
        self, captured: CapturedApplicationPayload, channel: str, direction: MessageDirection
    ) -> int:
        if self._sink_failed:
            raise CoinbaseSinkError("Coinbase research sink is unavailable.")
        self._ordinal += 1
        record = RawResearchRecord(
            schema_version=RAW_RESEARCH_SCHEMA_VERSION,
            venue="coinbase",
            product=COINBASE_RESEARCH_PRODUCT,
            channel=channel,
            session_id=self._session_id,
            message_ordinal=self._ordinal,
            received_utc_ns=captured.received_utc_ns,
            received_monotonic_ns=captured.received_monotonic_ns,
            direction=direction,
            frame_type=captured.frame_type,
            payload_encoding=captured.payload_encoding,
            payload_bytes=captured.payload_bytes,
        )
        try:
            await self._sink.append(record)
        except asyncio.CancelledError:
            self._sink_failed = True
            raise
        except Exception:
            self._sink_failed = True
            raise CoinbaseSinkError("Coinbase research sink append failed.") from None
        return self._ordinal

    async def _marker(self, channel: str, **fields: object) -> None:
        captured = self._capture(json.dumps(fields, separators=(",", ":"), sort_keys=True))
        marker = CapturedApplicationPayload(
            captured.received_utc_ns,
            captured.received_monotonic_ns,
            FrameType.MARKER,
            PayloadEncoding.UTF8_JSON,
            captured.payload_bytes,
        )
        await self._append(marker, channel, MessageDirection.LOCAL)


@asynccontextmanager
async def _public_connection(
    config: CoinbasePublicResearchConfig,
) -> AsyncIterator[WebSocketConnection]:
    async with connect(
        COINBASE_PUBLIC_WEBSOCKET_URL,
        open_timeout=5.0,
        close_timeout=2.0,
        max_size=config.max_application_payload_bytes,
        max_queue=32,
        ping_interval=20,
        ping_timeout=20,
        logger=_TRANSPORT_LOGGER,
    ) as connection:
        yield cast(WebSocketConnection, connection)


def _duration(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("Capture duration must be a built-in number.")
    duration = float(cast(int | float, value))
    if not math.isfinite(duration) or not 1 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError("Coinbase measurement duration must be between 1 and 600 seconds.")
    return duration


def create_coinbase_catalog(parquet_dir: Path, database_path: Path) -> None:
    """Join derived records to original receipt clocks and exact payload lineage."""
    create_research_catalog(parquet_dir, database_path)
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("""
            CREATE VIEW coinbase_public_events AS
            SELECT source.session_id, source.message_ordinal AS raw_message_ordinal,
                source.received_utc_ns, source.received_monotonic_ns,
                source.payload_sha256, decode(derived.payload_bytes)::JSON AS normalized
            FROM raw_records source JOIN raw_records derived
              ON source.session_id = derived.session_id
             AND source.message_ordinal = TRY_CAST(json_extract_string(
                 decode(derived.payload_bytes), '$.raw_message_ordinal') AS BIGINT)
            WHERE source.venue = 'coinbase' AND source.product = 'BTC-USD'
              AND source.direction = 'inbound' AND derived.channel = 'normalized'
              AND source.channel = 'exchange_public_level2_batch_matches_heartbeat'
              AND derived.venue = 'coinbase' AND derived.product = 'BTC-USD'
              AND derived.direction = 'local'
              AND json_extract_string(decode(derived.payload_bytes), '$.feed_product')
                    = 'exchange_public_level2_batch_matches_heartbeat'
        """)
        connection.execute("""
            CREATE VIEW coinbase_spot_trades AS
            SELECT *, json_extract_string(normalized, '$.trade_id') AS trade_id,
                json_extract_string(normalized, '$.price') AS price,
                json_extract_string(normalized, '$.size') AS size,
                json_extract_string(normalized, '$.maker_side') AS maker_side,
                json_extract_string(normalized, '$.aggressor_side') AS aggressor_side,
                json_extract_string(normalized, '$.event_time') AS event_time
            FROM coinbase_public_events
            WHERE json_extract_string(normalized, '$.event') = 'trade'
        """)
        connection.execute("""
            CREATE VIEW coinbase_spot_l2_events AS
            SELECT events.*, CAST(level.key AS BIGINT) AS event_index,
                json_extract_string(events.normalized, '$.action') AS action,
                json_extract_string(events.normalized, '$.event_time') AS event_time,
                json_extract_string(level.value, '$.side') AS side,
                json_extract_string(level.value, '$.price') AS price,
                json_extract_string(level.value, '$.size') AS size
            FROM coinbase_public_events events,
                LATERAL json_each(events.normalized, '$.levels') AS level
            WHERE json_extract_string(events.normalized, '$.event') = 'l2'
        """)
    finally:
        connection.close()


def _capture_statistics(database_path: Path) -> dict[str, object]:
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        rows = connection.execute("""
            SELECT direction, channel, count(*), sum(octet_length(payload_bytes))
            FROM raw_records GROUP BY direction, channel ORDER BY direction, channel
        """).fetchall()
        quality = connection.execute("""
            SELECT json_extract_string(decode(payload_bytes), '$.event'), count(*)
            FROM raw_records WHERE direction = 'local' AND channel = 'data_quality'
            GROUP BY 1 ORDER BY 1
        """).fetchall()
        span = connection.execute("""
            SELECT max(received_monotonic_ns) - min(received_monotonic_ns) FROM raw_records
        """).fetchone()
        return {
            "records": [
                {
                    "direction": direction,
                    "channel": channel,
                    "records": count,
                    "payload_bytes": payload_bytes,
                }
                for direction, channel, count, payload_bytes in rows
            ],
            "quality_events": [{"event": event, "count": count} for event, count in quality],
            "observed_monotonic_span_seconds": (
                None if span is None or span[0] is None else span[0] / 1_000_000_000
            ),
        }
    finally:
        connection.close()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


async def run_bounded_capture(
    *,
    output_root: Path,
    duration_seconds: float,
    source_commit: str,
    image_digest: str,
    connection_factory: ConnectionFactory | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict[str, object]:
    """Create a new corpus and preserve final status even after terminal errors.

    Source/image fields are operator assertions for an independently admitted
    immutable image, never proof that a development invocation ran that image.
    """
    duration = _duration(duration_seconds)
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("source_commit must be a lowercase full Git SHA.")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None:
        raise ValueError("image_digest must be a pinned sha256 digest.")
    output_root.mkdir(parents=True, exist_ok=False)
    parquet_dir = output_root / "raw"
    manifest: dict[str, object] = {
        "schema": "coinbase-public-bounded-capture-v1",
        "venue": "coinbase",
        "product": COINBASE_RESEARCH_PRODUCT,
        "instrument_type": "spot",
        "base_asset": "BTC",
        "quote_asset": "USD",
        "feed_product": COINBASE_FEED_PRODUCT,
        "endpoint": COINBASE_PUBLIC_WEBSOCKET_URL,
        "channels": list(_CHANNELS),
        "authenticated": False,
        "duration_seconds": duration,
        "declared_source_commit": source_commit,
        "declared_image_digest": image_digest,
        "provenance_attestation": "operator_declared_not_runtime_verified",
        "started_utc_ns": time.time_ns(),
        "complete": False,
        "backfill": False,
        "reconnect": False,
        "native_runtime_admitted": False,
        "research_admission": "separate_corpus_not_registered_for_existing_studies",
    }
    _write_json(output_root / "capture-claim.json", manifest)
    writer = ParquetResearchWriter(parquet_dir, rotation=ParquetRotation())
    collector = CoinbasePublicResearchCollector(writer, connection_factory=connection_factory)
    failure: BaseException | None = None
    try:
        await collector.capture_for(duration, stop_event=stop_event)
    except BaseException as error:
        failure = error
    try:
        await writer.aclose()
    except asyncio.CancelledError as error:
        failure = error
    except Exception:
        failure = CoinbaseSinkError("Coinbase research writer close failed.")
    files = tuple(sorted(parquet_dir.glob("*.parquet")))
    report: dict[str, object] = {
        "schema": "coinbase-public-bounded-report-v1",
        "complete": False,
        "status": "failed" if failure is not None else "bounded_stopped",
        "coverage": "unqualified",
        "ended_utc_ns": time.time_ns(),
        "parquet_files": len(files),
        "parquet_bytes": sum(path.stat().st_size for path in files),
        "failure_class": None if failure is None else type(failure).__name__,
    }
    if files:
        try:
            create_coinbase_catalog(parquet_dir, output_root / "catalog.duckdb")
            report.update(_capture_statistics(output_root / "catalog.duckdb"))
        except Exception:
            report.update(status="failed", failure_class="catalog_failure")
            if failure is None:
                failure = CoinbaseCaptureError("Coinbase research catalog creation failed.")
    _write_json(output_root / "capture-report.json", report)
    if failure is not None:
        raise failure
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    args = parser.parse_args(argv)

    async def run() -> dict[str, object]:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        return await run_bounded_capture(
            output_root=args.output_root,
            duration_seconds=args.duration_seconds,
            source_commit=args.source_commit,
            image_digest=args.image_digest,
            stop_event=stop,
        )

    try:
        report = asyncio.run(run())
    except (CoinbaseCaptureError, ValueError, OSError):
        print("Coinbase bounded research capture failed; inspect its local report.")
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
