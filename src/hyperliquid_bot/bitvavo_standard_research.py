"""Bounded public Bitvavo Standard BTC-EUR exact-raw research capture.

Credential-free Standard WebSocket at ``wss://ws.bitvavo.com/v2/`` for
``BTC-EUR``: ``trades``, ``ticker``, and ``book`` (depth 1000). Optional
``candles`` is flagged (``--include-candles``) and off by default. Duration may
be a short smoke or a retained multi-day run up to 7 days. This path never
falls back to DATA-1E Market Data Pro artifact layouts or the Pro socket.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import signal
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Protocol, cast

import duckdb
from websockets.asyncio.client import connect
from websockets.exceptions import PayloadTooBig, WebSocketException

from .capture_observability import (
    add_transport_counts,
    attach_observability_health,
    capture_log_path,
    capture_logger,
    configure_capture_logger,
    elapsed_from_report,
)
from .parquet_research import ParquetResearchWriter, ParquetRotation, create_research_catalog
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
from .reconstructable_paths import (
    DATA1D_PATH_CONTRACT_ID,
    DATA1D_PRODUCT,
    Data1DRunPaths,
    data1d_run_paths,
)

BITVAVO_STANDARD_WEBSOCKET_URL: Final = "wss://ws.bitvavo.com/v2/"
BITVAVO_RESEARCH_VENUE: Final = "bitvavo"
BITVAVO_RESEARCH_PRODUCT: Final = "BTC-EUR"
BITVAVO_FEED_PRODUCT: Final = "standard"
SMOKE_CAPTURE_SECONDS: Final = 600.0
MAX_CAPTURE_SECONDS: Final = 7 * 24 * 60 * 60
RETAINED_MAX_RECONNECTS: Final = 10_080
DATA1D_CLAIM_SCHEMA: Final = "data-1d-retained-capture-claim-v1"
DATA1D_HEALTH_SCHEMA: Final = "data-1d-retained-capture-health-v1"
BITVAVO_CANDLE_INTERVALS: Final = (
    "1m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
)
BITVAVO_OPTIONAL_CANDLES_CHANNEL: Final = "candles"
DEFAULT_CANDLE_INTERVAL: Final = "1m"
_REQUIRED_SUBSCRIPTION_CHANNELS: Final = ("trades", "ticker", "book")
_SUBSCRIPTION_CHANNELS: Final = _REQUIRED_SUBSCRIPTION_CHANNELS
PROTECTED_TRADE_KEY_ENV: Final = (
    "HYPERLIQUID_PK",
    "HYPERLIQUID_TESTNET_PK",
    "HYPERLIQUID_VAULT",
    "HYPERLIQUID_TESTNET_VAULT",
    "HYPERLIQUID_ACCOUNT_ADDRESS",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_SECRET",
    "BINANCE_API_KEY_TESTNET",
    "BINANCE_TESTNET_API_SECRET",
    "BITVAVO_API_KEY",
    "BITVAVO_API_SECRET",
    "BITVAVO_ACCESS_KEY",
    "BITVAVO_SECRET",
    "BITVAVO_SIGNING_KEY",
    "KRAKEN_API_KEY",
    "KRAKEN_API_SECRET",
    "OKX_API_KEY",
    "OKX_SECRET_KEY",
    "OKX_PASSPHRASE",
)

_DECIMAL_TEXT: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_UNSIGNED_INTEGER_TEXT: Final = re.compile(r"(?:0|[1-9][0-9]*)\Z")

_TRANSPORT_PRIVACY_LOGGER: Final = logging.Logger(
    "hyperliquid_bot.bitvavo_standard_research_transport",
    level=logging.CRITICAL + 1,
)
_TRANSPORT_PRIVACY_LOGGER.disabled = True
_TRANSPORT_PRIVACY_LOGGER.propagate = False
_TRANSPORT_PRIVACY_LOGGER.addHandler(logging.NullHandler())


class BitvavoCaptureError(RuntimeError):
    """Bounded public error that never includes a venue payload."""


class BitvavoDataIntegrityError(BitvavoCaptureError):
    """An inbound schema, identity, snapshot, or nonce rule failed."""

    def __init__(self, message: str, *, quality_event: str = "schema_error") -> None:
        super().__init__(message)
        self.quality_event = quality_event


class BitvavoTransportError(BitvavoCaptureError):
    """A required public connection or reconnect failed."""


class BitvavoSinkError(BitvavoCaptureError):
    """The shared raw sink failed and capture stopped without retry."""


class _IntegerLexeme(str):
    """Distinguish an exact JSON integer token from a quoted string."""


class _DecimalLexeme(str):
    """Distinguish an unquoted JSON decimal token from an official decimal string."""


class WebSocketConnection(Protocol):
    """Small transport surface implemented by websockets and offline fakes."""

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]
type SessionIdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class BitvavoStandardResearchConfig:
    """Fixed BTC-EUR Standard scope with bounded public transport controls."""

    reconnect_delay_seconds: float = 3.0
    max_application_payload_bytes: int = 8 * 1024 * 1024
    max_buffered_book_updates: int = 10_000
    max_snapshot_requests: int = 3
    max_reconnects: int = 1
    include_candles: bool = False
    candle_interval: str = DEFAULT_CANDLE_INTERVAL

    def __post_init__(self) -> None:
        if (
            type(self.reconnect_delay_seconds) not in (int, float)
            or self.reconnect_delay_seconds < 0
        ):
            raise ValueError("reconnect_delay_seconds must be non-negative.")
        if (
            type(self.max_application_payload_bytes) is not int
            or self.max_application_payload_bytes <= 0
        ):
            raise ValueError("max_application_payload_bytes must be a positive integer.")
        if type(self.max_buffered_book_updates) is not int or self.max_buffered_book_updates <= 0:
            raise ValueError("max_buffered_book_updates must be a positive integer.")
        if type(self.max_snapshot_requests) is not int or self.max_snapshot_requests <= 0:
            raise ValueError("max_snapshot_requests must be a positive integer.")
        if type(self.max_reconnects) is not int or self.max_reconnects < 0:
            raise ValueError("max_reconnects must be a non-negative integer.")
        if type(self.include_candles) is not bool:
            raise ValueError("include_candles must be a bool.")
        if (
            type(self.candle_interval) is not str
            or self.candle_interval not in BITVAVO_CANDLE_INTERVALS
        ):
            raise ValueError(
                "candle_interval must be one of the official Bitvavo Standard candle intervals."
            )

    @property
    def subscription_channel_names(self) -> tuple[str, ...]:
        return standard_subscription_channels(include_candles=self.include_candles)


@dataclass(frozen=True, slots=True)
class _BufferedBookUpdate:
    nonce: int
    normalized: dict[str, object]


@dataclass(frozen=True, slots=True)
class _SnapshotAcceptance:
    retry_required: bool
    normalized_frames: tuple[dict[str, object], ...]


class _BookState:
    """Bounded Standard L2 bootstrap and exact nonce-chain validator."""

    def __init__(self, *, max_buffered_updates: int = 10_000) -> None:
        if type(max_buffered_updates) is not int or max_buffered_updates <= 0:
            raise ValueError("max_buffered_updates must be a positive integer.")
        self._max_buffered_updates = max_buffered_updates
        self._buffer: list[_BufferedBookUpdate] = []
        self._last_nonce: int | None = None
        self._has_snapshot = False

    @property
    def has_buffered_update(self) -> bool:
        return bool(self._buffer)

    @property
    def has_snapshot(self) -> bool:
        return self._has_snapshot

    @property
    def last_nonce(self) -> int | None:
        return self._last_nonce

    def ingest_update(
        self,
        document: dict[str, object],
        raw_ordinal: int,
    ) -> dict[str, object] | None:
        normalized = _normalize_book_update(document, raw_ordinal)
        nonce = int(cast(str, normalized["nonce"]))
        previous_nonce = (
            self._last_nonce
            if self._has_snapshot
            else self._buffer[-1].nonce
            if self._buffer
            else None
        )
        if previous_nonce is not None:
            if nonce <= previous_nonce:
                self._clear()
                raise BitvavoDataIntegrityError(
                    "Bitvavo book nonce was duplicate, out of order, or reset.",
                    quality_event="sequence_error",
                )
            if nonce != previous_nonce + 1:
                self._clear()
                raise BitvavoDataIntegrityError(
                    "Bitvavo book nonce chain had a gap.",
                    quality_event="sequence_gap",
                )
        if self._has_snapshot:
            self._last_nonce = nonce
            return normalized
        if len(self._buffer) >= self._max_buffered_updates:
            self._clear()
            raise BitvavoDataIntegrityError(
                "Bitvavo book bootstrap buffer exceeded its bound.",
                quality_event="buffer_overflow",
            )
        self._buffer.append(_BufferedBookUpdate(nonce=nonce, normalized=normalized))
        return None

    def accept_snapshot(
        self,
        document: dict[str, object],
        raw_ordinal: int,
        *,
        expected_request_id: int,
    ) -> _SnapshotAcceptance:
        if self._has_snapshot or not self._buffer:
            self._clear()
            raise BitvavoDataIntegrityError(
                "Bitvavo snapshot arrived outside bootstrap state.",
                quality_event="snapshot_error",
            )
        snapshot = _normalize_book_snapshot(
            document,
            raw_ordinal,
            expected_request_id=expected_request_id,
        )
        snapshot_nonce = int(cast(str, snapshot["nonce"]))
        first_buffered_nonce = self._buffer[0].nonce
        if snapshot_nonce <= first_buffered_nonce:
            return _SnapshotAcceptance(retry_required=True, normalized_frames=())

        retained = tuple(update for update in self._buffer if update.nonce > snapshot_nonce)
        if retained and retained[0].nonce != snapshot_nonce + 1:
            self._clear()
            raise BitvavoDataIntegrityError(
                "Bitvavo snapshot could not join the buffered nonce chain.",
                quality_event="sequence_gap",
            )
        self._has_snapshot = True
        self._last_nonce = retained[-1].nonce if retained else snapshot_nonce
        self._buffer.clear()
        return _SnapshotAcceptance(
            retry_required=False,
            normalized_frames=(snapshot, *(update.normalized for update in retained)),
        )

    def _clear(self) -> None:
        self._buffer.clear()
        self._last_nonce = None
        self._has_snapshot = False


class BitvavoStandardResearchCollector:
    """Capture public BTC-EUR trades, ticker/BBO, and Standard L2."""

    def __init__(
        self,
        sink: RawResearchSink,
        *,
        config: BitvavoStandardResearchConfig | None = None,
        connection_factory: ConnectionFactory | None = None,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        session_id_factory: SessionIdFactory | None = None,
    ) -> None:
        self._sink = sink
        self._config = config if config is not None else BitvavoStandardResearchConfig()
        self._connection_factory = (
            connection_factory
            if connection_factory is not None
            else _connection_factory(self._config)
        )
        self._utc_ns = utc_ns
        self._monotonic_ns = monotonic_ns
        self._session_id_factory = (
            session_id_factory
            if session_id_factory is not None
            else lambda: f"standard-{uuid.uuid4().hex}"
        )
        self._message_ordinal = 0
        self._append_lock = asyncio.Lock()
        self._sink_failed = False

    async def capture_for(
        self,
        duration_seconds: float,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run the fixed public slice for a strictly bounded duration."""

        _require_bounded_duration(duration_seconds)
        capture_stop = stop_event if stop_event is not None else asyncio.Event()
        timer = asyncio.create_task(
            self._stop_after(capture_stop, float(duration_seconds)),
            name="bitvavo-standard-research-duration",
        )
        stream = asyncio.create_task(
            self._run_stream(capture_stop),
            name="bitvavo-standard-research-stream",
        )
        try:
            await stream
        finally:
            capture_stop.set()
            timer.cancel()
            if not stream.done():
                stream.cancel()
            await asyncio.gather(timer, stream, return_exceptions=True)

    async def _stop_after(self, stop_event: asyncio.Event, duration_seconds: float) -> None:
        await asyncio.sleep(duration_seconds)
        stop_event.set()

    async def _run_stream(self, stop_event: asyncio.Event) -> None:
        previous_session_id: str | None = None
        reconnects = 0
        while not stop_event.is_set():
            session_id = self._session_id_factory()
            await self._session_started(session_id, previous_session_id)
            connected = False
            try:
                async with self._connection_factory() as connection:
                    connected = True
                    await self._connected(session_id, previous_session_id)
                    await self._send_subscription(connection, session_id)
                    await self._receive_session(
                        connection,
                        session_id,
                        stop_event,
                        is_reconnect=previous_session_id is not None,
                    )
                    await self._append_marker(
                        session_id,
                        "session",
                        "session_stopped",
                        stream=BITVAVO_FEED_PRODUCT,
                        reason="capture_limit_reached",
                    )
                    return
            except asyncio.CancelledError:
                raise
            except PayloadTooBig:
                await self._terminal_quality(session_id, "truncation_error")
                raise BitvavoDataIntegrityError(
                    "Bitvavo application payload exceeded the transport bound.",
                    quality_event="truncation_error",
                ) from None
            except (BitvavoSinkError, BitvavoDataIntegrityError):
                raise
            except (WebSocketException, OSError):
                if not connected:
                    await self._connection_failed(session_id, previous_session_id)
                    raise BitvavoTransportError("Bitvavo public connection failed.") from None
                await self._disconnected(session_id)
                if reconnects >= self._config.max_reconnects:
                    raise BitvavoTransportError(
                        "Bitvavo public reconnect bound was exhausted."
                    ) from None
                previous_session_id = session_id
                reconnects += 1
            except Exception:
                raise BitvavoTransportError("Bitvavo public transport boundary failed.") from None
            await self._wait_to_reconnect(stop_event)

    async def _send_subscription(
        self,
        connection: WebSocketConnection,
        session_id: str,
    ) -> None:
        payload_text = standard_subscription_payload_text(
            include_candles=self._config.include_candles,
            candle_interval=self._config.candle_interval,
        )
        captured = capture_application_payload(
            payload_text,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )
        await connection.send(payload_text)
        await self._append_captured(
            captured,
            session_id=session_id,
            channel="subscription",
            direction=MessageDirection.OUTBOUND,
        )
        for channel in self._config.subscription_channel_names:
            await self._append_marker(
                session_id,
                "subscription",
                "subscription_sent",
                stream=BITVAVO_FEED_PRODUCT,
                subscription_type=channel,
                product=BITVAVO_RESEARCH_PRODUCT,
                authenticated=False,
            )

    async def _send_snapshot_request(
        self,
        connection: WebSocketConnection,
        session_id: str,
        request_id: int,
    ) -> None:
        payload_text = json.dumps(
            {
                "action": "getBook",
                "depth": 1000,
                "market": BITVAVO_RESEARCH_PRODUCT,
                "requestId": request_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        captured = capture_application_payload(
            payload_text,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )
        await connection.send(payload_text)
        await self._append_captured(
            captured,
            session_id=session_id,
            channel="book_snapshot_request",
            direction=MessageDirection.OUTBOUND,
        )
        await self._append_marker(
            session_id,
            "subscription",
            "snapshot_requested",
            stream=BITVAVO_FEED_PRODUCT,
            request_id=request_id,
            product=BITVAVO_RESEARCH_PRODUCT,
            depth=1000,
            authenticated=False,
        )

    async def _receive_session(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
        *,
        is_reconnect: bool,
    ) -> None:
        expected_channels = frozenset(self._config.subscription_channel_names)
        acknowledged: set[str] = set()
        subscriptions_active_marked = False
        book_state = _BookState(
            max_buffered_updates=self._config.max_buffered_book_updates,
        )
        snapshot_requests = 0
        outstanding_request_id: int | None = None

        while not stop_event.is_set():
            captured = await self._receive_or_stop(connection, stop_event)
            if captured is None:
                break
            raw_ordinal, channel, document = await self._record_inbound(captured, session_id)

            if channel == "error":
                await self._quality_failure(
                    session_id,
                    raw_ordinal,
                    BitvavoDataIntegrityError(
                        "Bitvavo rejected or errored a public request.",
                        quality_event="subscription_failed",
                    ),
                )
            if channel == "subscription":
                newly_acknowledged = await self._record_subscription_response(
                    document,
                    session_id,
                    raw_ordinal,
                )
                acknowledged.update(newly_acknowledged)
                if acknowledged == expected_channels and not subscriptions_active_marked:
                    await self._append_marker(
                        session_id,
                        "subscription",
                        "subscriptions_active",
                        stream=BITVAVO_FEED_PRODUCT,
                        product=BITVAVO_RESEARCH_PRODUCT,
                        authenticated=False,
                    )
                    subscriptions_active_marked = True
                outstanding_request_id, snapshot_requests = await self._maybe_request_snapshot(
                    connection,
                    session_id,
                    acknowledged,
                    book_state,
                    outstanding_request_id,
                    snapshot_requests,
                )
                continue
            if (
                channel in {*expected_channels, "book_snapshot"}
                and channel in expected_channels
                and channel not in acknowledged
            ):
                await self._quality_failure(
                    session_id,
                    raw_ordinal,
                    BitvavoDataIntegrityError(
                        "Bitvavo market channel was not acknowledged.",
                        quality_event="subscription_error",
                    ),
                )

            try:
                normalized_frames: tuple[dict[str, object], ...]
                if channel == "trades":
                    normalized_frames = (_normalize_trade(document, raw_ordinal),)
                elif channel == "ticker":
                    normalized_frames = (_normalize_ticker(document, raw_ordinal),)
                elif channel == "candles":
                    normalized_frames = (
                        _normalize_candles(
                            document,
                            raw_ordinal,
                            expected_interval=self._config.candle_interval,
                        ),
                    )
                elif channel == "book":
                    normalized_update = book_state.ingest_update(document, raw_ordinal)
                    normalized_frames = () if normalized_update is None else (normalized_update,)
                elif channel == "book_snapshot":
                    if outstanding_request_id is None:
                        raise BitvavoDataIntegrityError(
                            "Bitvavo returned an unsolicited book snapshot.",
                            quality_event="snapshot_error",
                        )
                    acceptance = book_state.accept_snapshot(
                        document,
                        raw_ordinal,
                        expected_request_id=outstanding_request_id,
                    )
                    outstanding_request_id = None
                    if acceptance.retry_required:
                        await self._append_marker(
                            session_id,
                            "data_quality",
                            "snapshot_retry_required",
                            stream=BITVAVO_FEED_PRODUCT,
                            raw_message_ordinal=raw_ordinal,
                            reason="snapshot_nonce_not_newer_than_first_buffered_update",
                        )
                        if snapshot_requests >= self._config.max_snapshot_requests:
                            raise BitvavoDataIntegrityError(
                                "Bitvavo snapshot retry bound was exhausted.",
                                quality_event="snapshot_error",
                            )
                        (
                            outstanding_request_id,
                            snapshot_requests,
                        ) = await self._request_next_snapshot(
                            connection,
                            session_id,
                            snapshot_requests,
                        )
                        continue
                    normalized_frames = acceptance.normalized_frames
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "resnapshot_received" if is_reconnect else "snapshot_received",
                        stream=BITVAVO_FEED_PRODUCT,
                        raw_message_ordinal=raw_ordinal,
                        reason="fresh_standard_depth_1000_book_state",
                    )
                else:
                    raise BitvavoDataIntegrityError("Bitvavo inbound channel validation failed.")
            except BitvavoDataIntegrityError as error:
                await self._quality_failure(session_id, raw_ordinal, error)
                raise AssertionError("unreachable market-data failure") from None

            for normalized in normalized_frames:
                source_channel = cast(str, normalized["source_channel"])
                await self._append_local_payload(
                    session_id,
                    f"normalized_{source_channel}",
                    normalized,
                )

            outstanding_request_id, snapshot_requests = await self._maybe_request_snapshot(
                connection,
                session_id,
                acknowledged,
                book_state,
                outstanding_request_id,
                snapshot_requests,
            )

        if acknowledged != expected_channels:
            await self._append_marker(
                session_id,
                "data_quality",
                "subscription_failed",
                stream=BITVAVO_FEED_PRODUCT,
                reason="capture_stopped_before_required_acknowledgements",
            )
            raise BitvavoDataIntegrityError("Bitvavo subscriptions were incomplete.")
        if not book_state.has_snapshot:
            await self._append_marker(
                session_id,
                "data_quality",
                "snapshot_missing",
                stream=BITVAVO_FEED_PRODUCT,
                reason="capture_stopped_before_valid_book_snapshot",
            )
            raise BitvavoDataIntegrityError(
                "Bitvavo Standard book snapshot was incomplete.",
                quality_event="snapshot_missing",
            )

    async def _maybe_request_snapshot(
        self,
        connection: WebSocketConnection,
        session_id: str,
        acknowledged: set[str],
        book_state: _BookState,
        outstanding_request_id: int | None,
        snapshot_requests: int,
    ) -> tuple[int | None, int]:
        if (
            acknowledged == set(self._config.subscription_channel_names)
            and book_state.has_buffered_update
            and not book_state.has_snapshot
            and outstanding_request_id is None
            and snapshot_requests == 0
        ):
            return await self._request_next_snapshot(
                connection,
                session_id,
                snapshot_requests,
            )
        return outstanding_request_id, snapshot_requests

    async def _request_next_snapshot(
        self,
        connection: WebSocketConnection,
        session_id: str,
        snapshot_requests: int,
    ) -> tuple[int, int]:
        request_id = snapshot_requests + 1
        await self._send_snapshot_request(connection, session_id, request_id)
        return request_id, request_id

    async def _receive_or_stop(
        self,
        connection: WebSocketConnection,
        stop_event: asyncio.Event,
    ) -> CapturedApplicationPayload | None:
        receive_task = asyncio.create_task(
            self._receive_captured(connection),
            name="bitvavo-standard-research-receive",
        )
        stop_task = asyncio.create_task(stop_event.wait(), name="bitvavo-research-stop-wait")
        try:
            done, _ = await asyncio.wait(
                (receive_task, stop_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                try:
                    return receive_task.result()
                except asyncio.CancelledError:
                    raise
                except (PayloadTooBig, WebSocketException, OSError):
                    raise
                except Exception:
                    raise OSError("Bitvavo WebSocket receive failed.") from None
            return None
        finally:
            for task in (receive_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receive_task, stop_task, return_exceptions=True)

    async def _receive_captured(
        self,
        connection: WebSocketConnection,
    ) -> CapturedApplicationPayload:
        frame = await connection.recv()
        return capture_application_payload(
            frame,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )

    async def _record_inbound(
        self,
        captured: CapturedApplicationPayload,
        session_id: str,
    ) -> tuple[int, str, dict[str, object]]:
        if len(captured.payload_bytes) > self._config.max_application_payload_bytes:
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="unknown",
                direction=MessageDirection.INBOUND,
            )
            await self._quality_failure(
                session_id,
                raw_ordinal,
                BitvavoDataIntegrityError(
                    "Bitvavo complete application payload exceeded its bound.",
                    quality_event="payload_oversize",
                ),
            )
            raise AssertionError("unreachable payload oversize failure")
        try:
            document = _decode_json_object(captured.payload_bytes)
            channel = _classify_document(document)
        except BitvavoDataIntegrityError as error:
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="unknown",
                direction=MessageDirection.INBOUND,
            )
            await self._quality_failure(session_id, raw_ordinal, error)
            raise AssertionError("unreachable classification failure") from None
        raw_ordinal = await self._append_captured(
            captured,
            session_id=session_id,
            channel=channel,
            direction=MessageDirection.INBOUND,
        )
        return raw_ordinal, channel, document

    async def _record_subscription_response(
        self,
        document: dict[str, object],
        session_id: str,
        raw_ordinal: int,
    ) -> frozenset[str]:
        try:
            acknowledged = _subscription_channels(
                document,
                allowed_channels=frozenset(self._config.subscription_channel_names),
                candle_interval=(
                    self._config.candle_interval if self._config.include_candles else None
                ),
            )
        except BitvavoDataIntegrityError as error:
            await self._quality_failure(session_id, raw_ordinal, error)
            raise AssertionError("unreachable subscription response failure") from None
        for channel in sorted(acknowledged):
            await self._append_marker(
                session_id,
                "subscription",
                "subscription_acknowledged",
                stream=BITVAVO_FEED_PRODUCT,
                subscription_type=channel,
                product=BITVAVO_RESEARCH_PRODUCT,
                authenticated=False,
                raw_message_ordinal=raw_ordinal,
            )
        return acknowledged

    async def _append_captured(
        self,
        captured: CapturedApplicationPayload,
        *,
        session_id: str,
        channel: str,
        direction: MessageDirection,
    ) -> int:
        async with self._append_lock:
            if self._sink_failed:
                raise BitvavoSinkError(
                    "Bitvavo research sink is unavailable after an append failure."
                )
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=BITVAVO_RESEARCH_VENUE,
                product=BITVAVO_RESEARCH_PRODUCT,
                channel=channel,
                session_id=session_id,
                message_ordinal=self._message_ordinal,
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
                raise BitvavoSinkError("Bitvavo research sink append failed.") from None
            return record.message_ordinal

    async def _append_marker(
        self,
        session_id: str,
        channel: str,
        event: str,
        **fields: object,
    ) -> int:
        return await self._append_local_payload(session_id, channel, {"event": event, **fields})

    async def _append_local_payload(
        self,
        session_id: str,
        channel: str,
        payload: dict[str, object],
    ) -> int:
        payload_bytes = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        async with self._append_lock:
            if self._sink_failed:
                raise BitvavoSinkError(
                    "Bitvavo research sink is unavailable after an append failure."
                )
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=BITVAVO_RESEARCH_VENUE,
                product=BITVAVO_RESEARCH_PRODUCT,
                channel=channel,
                session_id=session_id,
                message_ordinal=self._message_ordinal,
                received_utc_ns=self._utc_ns(),
                received_monotonic_ns=self._monotonic_ns(),
                direction=MessageDirection.LOCAL,
                frame_type=FrameType.MARKER,
                payload_encoding=PayloadEncoding.UTF8_JSON,
                payload_bytes=payload_bytes,
            )
            try:
                await self._sink.append(record)
            except asyncio.CancelledError:
                self._sink_failed = True
                raise
            except Exception:
                self._sink_failed = True
                raise BitvavoSinkError("Bitvavo research sink append failed.") from None
            return record.message_ordinal

    async def _session_started(
        self,
        session_id: str,
        previous_session_id: str | None,
    ) -> None:
        await self._append_marker(
            session_id,
            "session",
            "session_started",
            stream=BITVAVO_FEED_PRODUCT,
            reason="initial_connection" if previous_session_id is None else "reconnect_attempt",
        )

    async def _connected(
        self,
        session_id: str,
        previous_session_id: str | None,
    ) -> None:
        await self._append_marker(
            session_id,
            "session",
            "connected",
            stream=BITVAVO_FEED_PRODUCT,
        )
        if previous_session_id is not None:
            await self._append_marker(
                session_id,
                "session",
                "reconnected",
                stream=BITVAVO_FEED_PRODUCT,
                previous_session_id=previous_session_id,
            )

    async def _disconnected(self, session_id: str) -> None:
        await self._append_marker(
            session_id,
            "session",
            "disconnected",
            stream=BITVAVO_FEED_PRODUCT,
            reason="transport_error",
        )
        await self._append_marker(
            session_id,
            "data_quality",
            "gap_detected",
            stream=BITVAVO_FEED_PRODUCT,
            reason="transport_disconnect; missed stream history is not reconstructable",
        )

    async def _connection_failed(
        self,
        session_id: str,
        previous_session_id: str | None,
    ) -> None:
        await self._append_marker(
            session_id,
            "session",
            "reconnect_failed" if previous_session_id is not None else "connection_failed",
            stream=BITVAVO_FEED_PRODUCT,
            reason="transport_error",
        )

    async def _terminal_quality(self, session_id: str, event: str) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            event,
            stream=BITVAVO_FEED_PRODUCT,
            reason="capture_stopped_fail_closed",
        )

    async def _quality_failure(
        self,
        session_id: str,
        raw_ordinal: int,
        error: BitvavoDataIntegrityError,
    ) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            error.quality_event,
            stream=BITVAVO_FEED_PRODUCT,
            reason="market_data_integrity_failure",
            raw_message_ordinal=raw_ordinal,
        )
        raise error

    async def _wait_to_reconnect(self, stop_event: asyncio.Event) -> None:
        if stop_event.is_set():
            return
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=float(self._config.reconnect_delay_seconds),
            )
        except TimeoutError:
            pass


def _normalize_trade(document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
    if document.get("event") != "trade" or document.get("market") != BITVAVO_RESEARCH_PRODUCT:
        raise BitvavoDataIntegrityError("Bitvavo trade identity validation failed.")
    taker_side = _required_text(document, "side")
    if taker_side not in {"buy", "sell"}:
        raise BitvavoDataIntegrityError("Bitvavo trade side validation failed.")
    return {
        "event": "normalized_trade_frame",
        "source_channel": "trades",
        "raw_message_ordinal": raw_ordinal,
        "events": [
            {
                "event_index": 0,
                "market": BITVAVO_RESEARCH_PRODUCT,
                "trade_id": _required_text(document, "id"),
                "price": _decimal(document, "price", allow_zero=False),
                "quantity": _decimal(document, "amount", allow_zero=False),
                "taker_side": taker_side,
                "event_time_ms": _unsigned_integer_text(document, "timestamp"),
                "event_time_ns": _unsigned_integer_text(document, "timestampNs"),
            }
        ],
    }


def _normalize_ticker(document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
    if document.get("event") != "ticker" or document.get("market") != BITVAVO_RESEARCH_PRODUCT:
        raise BitvavoDataIntegrityError("Bitvavo ticker identity validation failed.")
    bid_price = _optional_decimal(document, "bestBid", allow_zero=False)
    bid_quantity = _optional_decimal(document, "bestBidSize", allow_zero=False)
    ask_price = _optional_decimal(document, "bestAsk", allow_zero=False)
    ask_quantity = _optional_decimal(document, "bestAskSize", allow_zero=False)
    last_price = _optional_decimal(document, "lastPrice", allow_zero=False)
    if (bid_price is None) != (bid_quantity is None) or (ask_price is None) != (
        ask_quantity is None
    ):
        raise BitvavoDataIntegrityError("Bitvavo ticker price/size pair validation failed.")
    if bid_price is None and ask_price is None and last_price is None:
        raise BitvavoDataIntegrityError("Bitvavo ticker update was empty.")
    return {
        "event": "normalized_ticker_frame",
        "source_channel": "ticker",
        "raw_message_ordinal": raw_ordinal,
        "market": BITVAVO_RESEARCH_PRODUCT,
        "bid_price": bid_price,
        "bid_quantity": bid_quantity,
        "ask_price": ask_price,
        "ask_quantity": ask_quantity,
        "last_price": last_price,
    }


def _normalize_book_update(
    document: dict[str, object],
    raw_ordinal: int,
) -> dict[str, object]:
    if document.get("event") != "book" or document.get("market") != BITVAVO_RESEARCH_PRODUCT:
        raise BitvavoDataIntegrityError("Bitvavo book update identity validation failed.")
    return {
        "event": "normalized_book_frame",
        "source_channel": "book",
        "raw_message_ordinal": raw_ordinal,
        "message_type": "update",
        "market": BITVAVO_RESEARCH_PRODUCT,
        "nonce": _unsigned_integer_text(document, "nonce"),
        "venue_timestamp_ns": _optional_unsigned_integer_text(document, "timestamp"),
        "sequence_event": "update",
        "events": _book_events(document, snapshot=False, max_levels=None),
    }


def _normalize_book_snapshot(
    document: dict[str, object],
    raw_ordinal: int,
    *,
    expected_request_id: int,
) -> dict[str, object]:
    if document.get("action") != "getBook":
        raise BitvavoDataIntegrityError("Bitvavo snapshot action validation failed.")
    request_id = _unsigned_integer_text(document, "requestId")
    if request_id != str(expected_request_id):
        raise BitvavoDataIntegrityError("Bitvavo snapshot request identity validation failed.")
    response = _object(document.get("response"))
    if response.get("market") != BITVAVO_RESEARCH_PRODUCT:
        raise BitvavoDataIntegrityError("Bitvavo snapshot market validation failed.")
    return {
        "event": "normalized_book_frame",
        "source_channel": "book_snapshot",
        "raw_message_ordinal": raw_ordinal,
        "message_type": "snapshot",
        "market": BITVAVO_RESEARCH_PRODUCT,
        "nonce": _unsigned_integer_text(response, "nonce"),
        "venue_timestamp_ns": _optional_unsigned_integer_text(response, "timestamp"),
        "sequence_event": "snapshot",
        "events": _book_events(response, snapshot=True, max_levels=1000),
    }


def _book_events(
    document: dict[str, object],
    *,
    snapshot: bool,
    max_levels: int | None,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    saw_sides: set[str] = set()
    wire_order = 0
    for key, value in document.items():
        if key not in {"asks", "bids"}:
            continue
        if type(value) is not list or (max_levels is not None and len(value) > max_levels):
            raise BitvavoDataIntegrityError("Bitvavo order-book side schema validation failed.")
        saw_sides.add(key)
        side = "ask" if key == "asks" else "bid"
        for side_index, raw_level in enumerate(value):
            if type(raw_level) is not list or len(raw_level) != 2:
                raise BitvavoDataIntegrityError(
                    "Bitvavo order-book level schema validation failed."
                )
            level = cast(list[object], raw_level)
            price = _decimal_value(level[0], allow_zero=False)
            quantity = _decimal_value(level[1], allow_zero=not snapshot)
            if snapshot and Decimal(quantity) == 0:
                raise BitvavoDataIntegrityError(
                    "Bitvavo snapshot contained an empty order-book level."
                )
            events.append(
                {
                    "wire_order": wire_order,
                    "side": side,
                    "side_index": side_index,
                    "action": "snapshot"
                    if snapshot
                    else ("delete" if Decimal(quantity) == 0 else "update"),
                    "price": price,
                    "quantity": quantity,
                }
            )
            wire_order += 1
    if saw_sides != {"asks", "bids"}:
        raise BitvavoDataIntegrityError("Bitvavo order-book sides were incomplete.")
    return events


def _normalize_candles(
    document: dict[str, object],
    raw_ordinal: int,
    *,
    expected_interval: str,
) -> dict[str, object]:
    # Docs show event "candles"; production Standard WS has also sent singular "candle".
    if document.get("event") not in {"candle", "candles"}:
        raise BitvavoDataIntegrityError("Bitvavo candles event validation failed.")
    if document.get("market") != BITVAVO_RESEARCH_PRODUCT:
        raise BitvavoDataIntegrityError("Bitvavo candles market validation failed.")
    interval = _required_text(document, "interval")
    if interval != expected_interval:
        raise BitvavoDataIntegrityError("Bitvavo candles interval validation failed.")
    raw_candles = document.get("candle")
    if type(raw_candles) is not list or not raw_candles:
        raise BitvavoDataIntegrityError("Bitvavo candles payload schema validation failed.")
    candles: list[dict[str, object]] = []
    for entry in cast(list[object], raw_candles):
        candles.append(_normalize_candle_row(entry))
    return {
        "event": "normalized_candles_frame",
        "source_channel": "candles",
        "raw_message_ordinal": raw_ordinal,
        "market": BITVAVO_RESEARCH_PRODUCT,
        "interval": interval,
        "candles": candles,
    }


def _normalize_candle_row(entry: object) -> dict[str, object]:
    """Normalize one candle row from array wire form or object form."""
    if type(entry) is list:
        if len(entry) != 6:
            raise BitvavoDataIntegrityError("Bitvavo candle row schema validation failed.")
        cells = cast(list[object], entry)
        return {
            "timestamp_ms": _candle_timestamp_text(cells[0]),
            "open": _decimal_value(cells[1], allow_zero=False),
            "high": _decimal_value(cells[2], allow_zero=False),
            "low": _decimal_value(cells[3], allow_zero=False),
            "close": _decimal_value(cells[4], allow_zero=False),
            "volume": _decimal_value(cells[5], allow_zero=True),
        }
    if type(entry) is dict:
        fields = cast(dict[str, object], entry)
        return {
            "timestamp_ms": _candle_timestamp_text(fields.get("timestamp")),
            "open": _decimal_value(fields.get("open"), allow_zero=False),
            "high": _decimal_value(fields.get("high"), allow_zero=False),
            "low": _decimal_value(fields.get("low"), allow_zero=False),
            "close": _decimal_value(fields.get("close"), allow_zero=False),
            "volume": _decimal_value(fields.get("volume"), allow_zero=True),
        }
    raise BitvavoDataIntegrityError("Bitvavo candle row schema validation failed.")


def _candle_timestamp_text(value: object) -> str:
    if type(value) is str and _UNSIGNED_INTEGER_TEXT.fullmatch(value) is not None:
        return value
    if type(value) is _IntegerLexeme and value.isascii() and value.isdigit():
        return str(value)
    raise BitvavoDataIntegrityError("Bitvavo candle timestamp schema validation failed.")


def _subscription_channels(
    document: dict[str, object],
    *,
    allowed_channels: frozenset[str] | None = None,
    candle_interval: str | None = None,
) -> frozenset[str]:
    event = document.get("event")
    subscriptions = _object(document.get("subscriptions"))
    allowed = (
        frozenset(_REQUIRED_SUBSCRIPTION_CHANNELS) if allowed_channels is None else allowed_channels
    )
    if event not in {"subscribed", "book"}:
        raise BitvavoDataIntegrityError("Bitvavo subscription response schema validation failed.")
    if not subscriptions or set(subscriptions) - set(allowed):
        raise BitvavoDataIntegrityError("Bitvavo subscription response scope validation failed.")
    acknowledged: set[str] = set()
    for channel, markets in subscriptions.items():
        if channel == BITVAVO_OPTIONAL_CANDLES_CHANNEL:
            if candle_interval is None:
                raise BitvavoDataIntegrityError(
                    "Bitvavo subscription response scope validation failed."
                )
            if type(markets) is not dict:
                raise BitvavoDataIntegrityError(
                    "Bitvavo candles subscription schema validation failed."
                )
            interval_map = cast(dict[str, object], markets)
            if set(interval_map) != {candle_interval}:
                raise BitvavoDataIntegrityError(
                    "Bitvavo candles subscription interval validation failed."
                )
            market_list = interval_map[candle_interval]
            if type(market_list) is not list or market_list != [BITVAVO_RESEARCH_PRODUCT]:
                raise BitvavoDataIntegrityError("Bitvavo subscription market validation failed.")
            acknowledged.add(channel)
            continue
        if type(markets) is not list or markets != [BITVAVO_RESEARCH_PRODUCT]:
            raise BitvavoDataIntegrityError("Bitvavo subscription market validation failed.")
        acknowledged.add(channel)
    return frozenset(acknowledged)


def _decode_json_object(payload_bytes: bytes) -> dict[str, object]:
    try:
        loaded = json.loads(
            payload_bytes,
            parse_float=_DecimalLexeme,
            parse_int=_IntegerLexeme,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise BitvavoDataIntegrityError("Bitvavo inbound JSON validation failed.") from None
    if type(loaded) is not dict:
        raise BitvavoDataIntegrityError("Bitvavo inbound JSON root validation failed.")
    return cast(dict[str, object], loaded)


def _classify_document(document: dict[str, object]) -> str:
    if "error" in document or "errorCode" in document or document.get("event") == "error":
        return "error"
    if "subscriptions" in document:
        return "subscription"
    if document.get("action") == "getBook" and "response" in document:
        return "book_snapshot"
    event = document.get("event")
    channel_by_event = {
        "trade": "trades",
        "ticker": "ticker",
        "book": "book",
        # Docs: "candles"; production Standard WS has also emitted singular "candle".
        "candle": "candles",
        "candles": "candles",
    }
    if type(event) is str and event in channel_by_event:
        return channel_by_event[event]
    raise BitvavoDataIntegrityError("Bitvavo inbound frame had no allowed channel identity.")


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise BitvavoDataIntegrityError("Bitvavo object schema validation failed.")
    return cast(dict[str, object], value)


def _required_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not str or not value:
        raise BitvavoDataIntegrityError("Bitvavo text field schema validation failed.")
    return value


def _decimal(
    document: dict[str, object],
    field: str,
    *,
    allow_zero: bool,
) -> str:
    return _decimal_value(document.get(field), allow_zero=allow_zero)


def _optional_decimal(
    document: dict[str, object],
    field: str,
    *,
    allow_zero: bool,
) -> str | None:
    if field not in document:
        return None
    return _decimal(document, field, allow_zero=allow_zero)


def _decimal_value(value: object, *, allow_zero: bool) -> str:
    if type(value) is not str or _DECIMAL_TEXT.fullmatch(value) is None:
        raise BitvavoDataIntegrityError("Bitvavo decimal field schema validation failed.")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise BitvavoDataIntegrityError("Bitvavo decimal field schema validation failed.") from None
    if decimal < 0 or (not allow_zero and decimal == 0):
        raise BitvavoDataIntegrityError("Bitvavo decimal field schema validation failed.")
    return value


def _unsigned_integer_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not _IntegerLexeme or not value.isascii() or not value.isdigit():
        raise BitvavoDataIntegrityError("Bitvavo integer field schema validation failed.")
    return str(value)


def _optional_unsigned_integer_text(
    document: dict[str, object],
    field: str,
) -> str | None:
    if field not in document:
        return None
    return _unsigned_integer_text(document, field)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    del value
    raise ValueError("non-standard JSON constant")


@asynccontextmanager
async def _websocket_connection(
    config: BitvavoStandardResearchConfig,
) -> AsyncIterator[WebSocketConnection]:
    async with connect(
        BITVAVO_STANDARD_WEBSOCKET_URL,
        open_timeout=10.0,
        close_timeout=5.0,
        ping_interval=20.0,
        ping_timeout=10.0,
        max_size=config.max_application_payload_bytes,
        max_queue=1024,
        logger=_TRANSPORT_PRIVACY_LOGGER,
    ) as connection:
        yield cast(WebSocketConnection, connection)


def _connection_factory(config: BitvavoStandardResearchConfig) -> ConnectionFactory:
    return lambda: _websocket_connection(config)


def _require_bounded_duration(duration_seconds: object) -> float:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not math.isfinite(duration) or not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
    return duration


def standard_subscription_channels(*, include_candles: bool = False) -> tuple[str, ...]:
    """Return the Standard subscribe set. Candles are optional and flagged."""

    if type(include_candles) is not bool:
        raise ValueError("include_candles must be a bool.")
    if include_candles:
        return (*_REQUIRED_SUBSCRIPTION_CHANNELS, BITVAVO_OPTIONAL_CANDLES_CHANNEL)
    return _REQUIRED_SUBSCRIPTION_CHANNELS


def standard_subscription_payload_text(
    *,
    include_candles: bool = False,
    candle_interval: str = DEFAULT_CANDLE_INTERVAL,
) -> str:
    """Deterministic Standard subscribe payload for BTC-EUR."""

    if type(include_candles) is not bool:
        raise ValueError("include_candles must be a bool.")
    if type(candle_interval) is not str or candle_interval not in BITVAVO_CANDLE_INTERVALS:
        raise ValueError(
            "candle_interval must be one of the official Bitvavo Standard candle intervals."
        )
    channels: list[dict[str, object]] = [
        {"markets": [BITVAVO_RESEARCH_PRODUCT], "name": name}
        for name in _REQUIRED_SUBSCRIPTION_CHANNELS
    ]
    if include_candles:
        channels.append(
            {
                "interval": [candle_interval],
                "markets": [BITVAVO_RESEARCH_PRODUCT],
                "name": BITVAVO_OPTIONAL_CANDLES_CHANNEL,
            }
        )
    return json.dumps(
        {"action": "subscribe", "channels": channels},
        separators=(",", ":"),
        sort_keys=True,
    )


def data1d_feed_name(
    *,
    include_candles: bool = False,
    candle_interval: str = DEFAULT_CANDLE_INTERVAL,
) -> str:
    """Fail-closed Standard feed identity. Never a DATA-1E Pro feed string."""

    if include_candles:
        return f"bitvavo-standard-btc-eur-trades-ticker-book-candles-{candle_interval}"
    return "bitvavo-standard-btc-eur-trades-ticker-book"


def _config_for_duration(
    duration_seconds: float,
    *,
    include_candles: bool = False,
    candle_interval: str = DEFAULT_CANDLE_INTERVAL,
) -> BitvavoStandardResearchConfig:
    """Smoke keeps the Phase-1 reconnect cap; retained runs retry until duration ends."""

    duration = _require_bounded_duration(duration_seconds)
    if duration <= SMOKE_CAPTURE_SECONDS:
        return BitvavoStandardResearchConfig(
            include_candles=include_candles,
            candle_interval=candle_interval,
        )
    return BitvavoStandardResearchConfig(
        max_reconnects=RETAINED_MAX_RECONNECTS,
        include_candles=include_candles,
        candle_interval=candle_interval,
    )


def refuse_protected_trade_keys(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed when trade/signing key names are set. Values are never included."""

    source = os.environ if environ is None else environ
    for name in PROTECTED_TRADE_KEY_ENV:
        if source.get(name):
            raise BitvavoCaptureError(
                f"Refuse: protected environment name {name} is set. Value not printed."
            )


def build_capture_report(database_path: Path, parquet_dir: Path) -> dict[str, object]:
    """Return payload/file counts only; never emit captured payload contents."""

    connection = duckdb.connect(str(database_path.resolve()), read_only=True)
    try:
        channel_rows = connection.execute(
            """
            SELECT channel, direction, count(*), sum(octet_length(payload_bytes))
            FROM raw_records
            GROUP BY channel, direction
            ORDER BY channel, direction
            """
        ).fetchall()
        totals = connection.execute(
            "SELECT count(*), coalesce(sum(octet_length(payload_bytes)), 0) FROM raw_records"
        ).fetchone()
        if totals is None:
            raise RuntimeError("DuckDB did not return the requested capture aggregates.")
        total_events, total_payload_bytes = totals
        report = {
            "channels": [
                {
                    "channel": str(channel),
                    "direction": str(direction),
                    "events": int(events),
                    "payload_bytes": int(payload_bytes),
                }
                for channel, direction, events, payload_bytes in channel_rows
            ],
            "events": int(total_events),
            "payload_bytes": int(total_payload_bytes),
        }
        add_transport_counts(
            connection,
            report,
            gap_event="gap_detected",
            reconnect_event="reconnected",
        )
    finally:
        connection.close()

    parquet_files = tuple(sorted(parquet_dir.resolve().glob("*.parquet")))
    parquet_bytes = sum(path.stat().st_size for path in parquet_files)
    raw_bytes = int(total_payload_bytes)
    report.update(
        {
            "parquet_files": len(parquet_files),
            "parquet_bytes": parquet_bytes,
            "raw_payload_to_parquet_ratio": (raw_bytes / parquet_bytes if parquet_bytes else None),
        }
    )
    return report


async def run_bounded_capture(
    *,
    output_dir: Path,
    database_path: Path,
    duration_seconds: float,
    stop_event: asyncio.Event | None = None,
    collector_factory: Callable[[RawResearchSink], BitvavoStandardResearchCollector] | None = None,
    include_candles: bool = False,
    candle_interval: str = DEFAULT_CANDLE_INTERVAL,
) -> dict[str, object]:
    """Run the public Standard capture, close Parquet, and build the DuckDB catalog."""

    duration = _require_bounded_duration(duration_seconds)
    writer = ParquetResearchWriter(output_dir, rotation=ParquetRotation())
    active = (
        collector_factory(writer)
        if collector_factory is not None
        else BitvavoStandardResearchCollector(
            writer,
            config=_config_for_duration(
                duration,
                include_candles=include_candles,
                candle_interval=candle_interval,
            ),
        )
    )
    try:
        await active.capture_for(duration, stop_event=stop_event)
    finally:
        await writer.aclose()
    create_research_catalog(output_dir, database_path)
    return build_capture_report(database_path, output_dir)


def data1d_capture_claim(
    *,
    run_id: str,
    duration_seconds: float,
    paths: Data1DRunPaths,
    include_candles: bool = False,
    candle_interval: str = DEFAULT_CANDLE_INTERVAL,
) -> dict[str, object]:
    duration = _require_bounded_duration(duration_seconds)
    channels = list(standard_subscription_channels(include_candles=include_candles))
    return {
        "schema": DATA1D_CLAIM_SCHEMA,
        "state": "STARTED_FAIL_CLOSED",
        "run_id": run_id,
        "path_contract": DATA1D_PATH_CONTRACT_ID,
        "venue": BITVAVO_RESEARCH_VENUE,
        "product": DATA1D_PRODUCT,
        "feed": data1d_feed_name(
            include_candles=include_candles,
            candle_interval=candle_interval,
        ),
        "feed_product": BITVAVO_FEED_PRODUCT,
        "websocket_url": BITVAVO_STANDARD_WEBSOCKET_URL,
        "channels": channels,
        "include_candles": include_candles,
        "candle_interval": candle_interval if include_candles else None,
        "credentialless": True,
        "authenticated": False,
        "signing": False,
        "mdpro_fallback": False,
        "data1e_path_fallback": False,
        "duration_seconds": duration,
        "smoke_duration_seconds": SMOKE_CAPTURE_SECONDS,
        "max_duration_seconds": MAX_CAPTURE_SECONDS,
        "retained": duration > SMOKE_CAPTURE_SECONDS,
        "run_dir": str(paths.run_dir),
        "raw_dir": str(paths.raw_dir),
        "database_path": str(paths.database_path),
        "twenty_four_seven": False,
    }


def data1d_capture_health(
    *,
    run_id: str,
    duration_seconds: float,
    status: str,
    report: dict[str, object],
    include_candles: bool = False,
    candle_interval: str = DEFAULT_CANDLE_INTERVAL,
) -> dict[str, object]:
    duration = _require_bounded_duration(duration_seconds)
    channels = list(standard_subscription_channels(include_candles=include_candles))
    elapsed = elapsed_from_report(report)
    return attach_observability_health(
        {
            "schema": DATA1D_HEALTH_SCHEMA,
            "run_id": run_id,
            "path_contract": DATA1D_PATH_CONTRACT_ID,
            "status": status,
            "duration_seconds": duration,
            "retained": duration > SMOKE_CAPTURE_SECONDS,
            "credentialless": True,
            "authenticated": False,
            "signing": False,
            "feed": data1d_feed_name(
                include_candles=include_candles,
                candle_interval=candle_interval,
            ),
            "feed_product": BITVAVO_FEED_PRODUCT,
            "channels": channels,
            "include_candles": include_candles,
            "candle_interval": candle_interval if include_candles else None,
            "mdpro_fallback": False,
            "data1e_path_fallback": False,
            "events": report.get("events"),
            "payload_bytes": report.get("payload_bytes"),
            "parquet_files": report.get("parquet_files"),
            "parquet_bytes": report.get("parquet_bytes"),
            "gaps": report.get("gaps"),
            "reconnects": report.get("reconnects"),
            "limitations": [
                "Published Parquet parts are reconstructable; a crash can lose the "
                "in-memory segment.",
                "This is not 24/7 service evidence or a trading edge.",
                "Bitvavo Standard is credential-free public L2 plus trades/ticker; "
                "optional candles are flagged and never implied by the default set.",
                "This capture never writes DATA-1E Pro paths and never uses the MD Pro socket.",
                "Standard and Pro may coexist only with distinct tmux sessions and artifact roots.",
            ],
        },
        report,
        elapsed_seconds=elapsed,
    )


def _write_create_only_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n")


async def run_reconstructable_capture(
    *,
    artifact_root: Path,
    run_id: str,
    duration_seconds: float,
    stop_event: asyncio.Event | None = None,
    operator_stop: Callable[[], bool] | None = None,
    collector_factory: Callable[[RawResearchSink], BitvavoStandardResearchCollector] | None = None,
    include_candles: bool = False,
    candle_interval: str = DEFAULT_CANDLE_INTERVAL,
) -> dict[str, object]:
    """Write DATA-1D Parquet/DuckDB to the documented reconstructable path."""

    refuse_protected_trade_keys()
    paths = data1d_run_paths(artifact_root, run_id)
    if "data-1e" in paths.run_dir.parts:
        raise RuntimeError("DATA-1D refuses DATA-1E Pro artifact path segments.")
    if paths.run_dir.exists():
        raise FileExistsError(f"DATA-1D refuses to reuse existing run directory: {paths.run_dir}")
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    paths.raw_dir.mkdir(exist_ok=False)
    log_path = capture_log_path(paths.run_dir, run_id)
    configure_capture_logger(log_path)
    capture_logger().info(
        "data1d start run_id=%s requested_duration_seconds=%s log=%s",
        run_id,
        duration_seconds,
        log_path,
    )
    _write_create_only_json(
        paths.capture_claim_path,
        data1d_capture_claim(
            run_id=run_id,
            duration_seconds=duration_seconds,
            paths=paths,
            include_candles=include_candles,
            candle_interval=candle_interval,
        ),
    )
    report: dict[str, object] = {
        "events": 0,
        "payload_bytes": 0,
        "parquet_files": 0,
        "parquet_bytes": 0,
        "gaps": 0,
        "reconnects": 0,
        "reconnect_clusters": 0,
        "elapsed_seconds": 0.0,
        "transport_profiles": [],
        "integrity_events": 0,
    }
    status = "FAILED"
    started = time.monotonic()
    try:
        report = await run_bounded_capture(
            output_dir=paths.raw_dir,
            database_path=paths.database_path,
            duration_seconds=duration_seconds,
            stop_event=stop_event,
            collector_factory=collector_factory,
            include_candles=include_candles,
            candle_interval=candle_interval,
        )
        if operator_stop is not None and operator_stop():
            status = "OPERATOR_STOP"
        else:
            status = "COMPLETED"
    finally:
        report = {**report, "elapsed_seconds": round(time.monotonic() - started, 6)}
        capture_logger().info(
            "data1d stop run_id=%s status=%s requested_duration_seconds=%s elapsed_seconds=%s",
            run_id,
            status,
            duration_seconds,
            report["elapsed_seconds"],
        )
        if not paths.capture_health_path.exists():
            _write_create_only_json(
                paths.capture_health_path,
                data1d_capture_health(
                    run_id=run_id,
                    duration_seconds=duration_seconds,
                    status=status,
                    report=report,
                    include_candles=include_candles,
                    candle_interval=candle_interval,
                ),
            )
    return {
        **report,
        "run_id": run_id,
        "path_contract": DATA1D_PATH_CONTRACT_ID,
        "run_dir": str(paths.run_dir),
        "raw_dir": str(paths.raw_dir),
        "database_path": str(paths.database_path),
        "status": status,
        "twenty_four_seven": False,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Public Bitvavo Standard BTC-EUR exact-raw research capture. "
            "Default channels: trades, ticker, book (depth 1000). Optional candles via "
            "--include-candles and --candle-interval (official Standard WebSocket intervals). "
            "Duration may exceed the historical 600s smoke cap up to 7 days. "
            "Never falls back to DATA-1E Market Data Pro paths or the Pro socket. "
            "This is not a 24/7 service. Do not start a multi-day retain from a Cloud Agent."
        )
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--duration-seconds", required=True, type=float)
    parser.add_argument(
        "--include-candles",
        action="store_true",
        default=False,
        help=(
            "Also subscribe the optional Standard candles channel "
            "(https://docs.bitvavo.com/docs/websocket-api/candles-subscription/)."
        ),
    )
    parser.add_argument(
        "--candle-interval",
        default=DEFAULT_CANDLE_INTERVAL,
        choices=BITVAVO_CANDLE_INTERVALS,
        help="Candle interval when --include-candles is set (default: 1m).",
    )
    return parser


def _resolve_cli_mode(args: argparse.Namespace) -> str:
    reconstructable = args.artifact_root is not None or args.run_id is not None
    ad_hoc = args.output_dir is not None or args.database is not None
    if reconstructable and ad_hoc:
        raise ValueError(
            "Use either --artifact-root/--run-id or --output-dir/--database, not both."
        )
    if reconstructable:
        if args.artifact_root is None or args.run_id is None:
            raise ValueError("Reconstructable capture requires both --artifact-root and --run-id.")
        return "reconstructable"
    if args.output_dir is None or args.database is None:
        raise ValueError(
            "Ad-hoc capture requires --output-dir and --database; "
            "preferred reconstructable mode uses --artifact-root and --run-id."
        )
    return "ad_hoc"


async def _run_from_args(args: argparse.Namespace) -> dict[str, object]:
    stop_event = asyncio.Event()
    operator_stopped = False

    def _request_operator_stop() -> None:
        nonlocal operator_stopped
        operator_stopped = True
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_operator_stop)
    except (NotImplementedError, RuntimeError):
        pass

    refuse_protected_trade_keys()
    include_candles = bool(args.include_candles)
    candle_interval = cast(str, args.candle_interval)
    if not include_candles and candle_interval != DEFAULT_CANDLE_INTERVAL:
        raise ValueError("--candle-interval requires --include-candles.")
    mode = _resolve_cli_mode(args)
    if mode == "reconstructable":
        return await run_reconstructable_capture(
            artifact_root=cast(Path, args.artifact_root),
            run_id=cast(str, args.run_id),
            duration_seconds=cast(float, args.duration_seconds),
            stop_event=stop_event,
            operator_stop=lambda: operator_stopped,
            include_candles=include_candles,
            candle_interval=candle_interval,
        )
    return await run_bounded_capture(
        output_dir=cast(Path, args.output_dir),
        database_path=cast(Path, args.database),
        duration_seconds=cast(float, args.duration_seconds),
        stop_event=stop_event,
        include_candles=include_candles,
        candle_interval=candle_interval,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    report = asyncio.run(_run_from_args(args))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
