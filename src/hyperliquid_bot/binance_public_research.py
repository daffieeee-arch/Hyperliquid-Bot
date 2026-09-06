"""Public Binance BTCUSDT exact-raw research capture.

Duration may be a short smoke or a retained multi-day run. The process still
stops at an explicit duration or operator signal; this is not a 24/7 service.
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
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, NoReturn, Protocol, cast
from urllib.request import ProxyHandler, Request, build_opener

import duckdb
from websockets.asyncio.client import connect
from websockets.exceptions import PayloadTooBig, WebSocketException

from .binance_spot_trades import BinanceTimestampUnit, decode_binance_spot_trade
from .capture_observability import (
    add_transport_counts,
    attach_observability_health,
    capture_log_path,
    capture_logger,
    configure_capture_logger,
    elapsed_from_report,
    transport_exception_fields,
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
    DATA1F_PATH_CONTRACT_ID,
    DATA1F_PRODUCT,
    Data1FRunPaths,
    data1f_run_paths,
)

BINANCE_RESEARCH_VENUE: Final = "binance"
BINANCE_SPOT_PRODUCT: Final = "BTCUSDT-SPOT"
BINANCE_USDM_PRODUCT: Final = "BTCUSDT-USDS-M-PERPETUAL"
BINANCE_NATIVE_SYMBOL: Final = "BTCUSDT"
SMOKE_CAPTURE_SECONDS: Final = 180.0
MAX_CAPTURE_SECONDS: Final = 7 * 24 * 60 * 60
RETAINED_MAX_RECONNECTS: Final = 10_080
DATA1F_CLAIM_SCHEMA: Final = "data-1f-retained-capture-claim-v1"
DATA1F_HEALTH_SCHEMA: Final = "data-1f-retained-capture-health-v1"
# Application-silence bound, not a client keepalive. Official Spot JSON/SBE
# streams require a pong within one minute of the server ping (every ~20s).
# Official USD-M Connect: server ping every 3 minutes, pong within 10 minutes.
# Required DATA-1F streams update much faster (100ms-1s or real-time).
REQUIRED_STREAM_STARVATION_SECONDS: Final = 60.0
# Disable websockets client keepalive Pings. The library default
# (ping_interval=20, ping_timeout=20) closes with code 1011 when a client
# Ping is not answered; that is not Binance's documented keepalive. The
# library still auto-replies to server Pings when these are None.
BINANCE_WEBSOCKET_CLIENT_PING_INTERVAL: Final[float | None] = None
BINANCE_WEBSOCKET_CLIENT_PING_TIMEOUT: Final[float | None] = None
# websockets default incoming queue is 16. Spot depth@100ms and USD-M
# /public bookTicker can fill that under load (live 1011 asymmetry:
# usdm_public >> spot >> usdm_market). Match the HL/OKX raw collectors.
BINANCE_WEBSOCKET_HIGH_FREQUENCY_MAX_QUEUE: Final = 1024
BINANCE_WEBSOCKET_MARKET_MAX_QUEUE: Final = 16
# Cap below required_stream_starvation_seconds so backoff cannot starve
# the mid-run liveness gate. Official Spot limit: 300 connections / 5 min / IP.
BINANCE_RECONNECT_BACKOFF_CAP_SECONDS: Final = 24.0

BINANCE_SPOT_WEBSOCKET_URL: Final = (
    "wss://data-stream.binance.vision:443/stream?streams="
    "btcusdt@trade/btcusdt@bookTicker/btcusdt@depth@100ms&timeUnit=MICROSECOND"
)
BINANCE_USDM_MARKET_WEBSOCKET_URL: Final = (
    "wss://fstream.binance.com/market/stream?streams="
    "btcusdt@aggTrade/btcusdt@markPrice@1s/btcusdt@forceOrder"
)
BINANCE_USDM_PUBLIC_WEBSOCKET_URL: Final = (
    "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker"
)
BINANCE_SPOT_DEPTH_URL: Final = (
    "https://data-api.binance.vision/api/v3/depth?symbol=BTCUSDT&limit=1000"
)
BINANCE_USDM_OPEN_INTEREST_URL: Final = (
    "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT"
)

_SPOT_STREAM_CHANNELS: Final = {
    "btcusdt@trade": "spot_trade",
    "btcusdt@bookTicker": "spot_book_ticker",
    "btcusdt@depth@100ms": "spot_depth",
}
_USDM_MARKET_STREAM_CHANNELS: Final = {
    "btcusdt@aggTrade": "usdm_agg_trade",
    "btcusdt@markPrice@1s": "usdm_mark_price",
    "btcusdt@forceOrder": "usdm_force_order",
}
_USDM_PUBLIC_STREAM_CHANNELS: Final = {
    "btcusdt@bookTicker": "usdm_book_ticker",
}
_DECIMAL_TEXT: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")

_TRANSPORT_PRIVACY_LOGGER: Final = logging.Logger(
    "hyperliquid_bot.binance_public_research_transport",
    level=logging.CRITICAL + 1,
)
_TRANSPORT_PRIVACY_LOGGER.disabled = True
_TRANSPORT_PRIVACY_LOGGER.propagate = False
_TRANSPORT_PRIVACY_LOGGER.addHandler(logging.NullHandler())


class BinanceCaptureError(RuntimeError):
    """Bounded public error that never includes a venue payload or transport detail."""


class BinanceDataIntegrityError(BinanceCaptureError):
    """An inbound identity, schema, snapshot, or sequence contract failed."""

    def __init__(self, message: str, *, quality_event: str = "schema_error") -> None:
        super().__init__(message)
        self.quality_event = quality_event
        self.reported = False


class BinanceTransportError(BinanceCaptureError):
    """A required public transport or bounded reconnect failed."""


class BinanceSinkError(BinanceCaptureError):
    """The shared raw sink failed and capture stopped without retry."""


class _BinanceServerShutdown(BinanceCaptureError):
    """Documented Spot server shutdown that requires a fresh connection."""


class _IntegerLexeme(str):
    """Distinguish an exact JSON integer token from a quoted string."""


class _DecimalLexeme(str):
    """Distinguish an unquoted JSON decimal token from an official decimal string."""


class WebSocketConnection(Protocol):
    """Small transport surface implemented by websockets and offline fakes."""

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]
type PayloadFetcher = Callable[[], Awaitable[CapturedApplicationPayload]]
type SessionIdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class BinancePublicResearchConfig:
    """Fixed public scope with bounded transport and bootstrap controls."""

    reconnect_delay_seconds: float = 3.0
    max_application_payload_bytes: int = 8 * 1024 * 1024
    max_buffered_spot_depth_updates: int = 10_000
    max_spot_snapshot_requests: int = 3
    max_reconnects: int = 1
    http_timeout_seconds: float = 10.0
    required_stream_starvation_seconds: float = REQUIRED_STREAM_STARVATION_SECONDS

    def __post_init__(self) -> None:
        if (
            type(self.reconnect_delay_seconds) not in (int, float)
            or not math.isfinite(float(self.reconnect_delay_seconds))
            or self.reconnect_delay_seconds < 0
        ):
            raise ValueError("reconnect_delay_seconds must be non-negative.")
        if (
            type(self.max_application_payload_bytes) is not int
            or self.max_application_payload_bytes <= 0
        ):
            raise ValueError("max_application_payload_bytes must be a positive integer.")
        if (
            type(self.max_buffered_spot_depth_updates) is not int
            or self.max_buffered_spot_depth_updates <= 0
        ):
            raise ValueError("max_buffered_spot_depth_updates must be a positive integer.")
        if type(self.max_spot_snapshot_requests) is not int or self.max_spot_snapshot_requests <= 0:
            raise ValueError("max_spot_snapshot_requests must be a positive integer.")
        if type(self.max_reconnects) is not int or self.max_reconnects < 0:
            raise ValueError("max_reconnects must be a non-negative integer.")
        if (
            type(self.http_timeout_seconds) not in (int, float)
            or not math.isfinite(float(self.http_timeout_seconds))
            or self.http_timeout_seconds <= 0
        ):
            raise ValueError("http_timeout_seconds must be positive.")
        if (
            type(self.required_stream_starvation_seconds) not in (int, float)
            or not math.isfinite(float(self.required_stream_starvation_seconds))
            or self.required_stream_starvation_seconds <= 0
        ):
            raise ValueError("required_stream_starvation_seconds must be positive.")


@dataclass(frozen=True, slots=True)
class _BufferedSpotDepth:
    first_update_id: int
    final_update_id: int
    normalized: dict[str, object]


@dataclass(frozen=True, slots=True)
class _SpotSnapshotAcceptance:
    retry_required: bool
    normalized_frames: tuple[dict[str, object], ...]


class _SpotBookState:
    """Official Spot REST-snapshot plus diff-depth sequence boundary."""

    def __init__(self, *, max_buffered_updates: int = 10_000) -> None:
        if type(max_buffered_updates) is not int or max_buffered_updates <= 0:
            raise ValueError("max_buffered_updates must be a positive integer.")
        self._max_buffered_updates = max_buffered_updates
        self._buffer: list[_BufferedSpotDepth] = []
        self._last_update_id: int | None = None
        self._has_snapshot = False
        self._validated_post_snapshot_updates = 0

    @property
    def has_buffered_update(self) -> bool:
        return bool(self._buffer)

    @property
    def has_snapshot(self) -> bool:
        return self._has_snapshot

    @property
    def last_update_id(self) -> int | None:
        return self._last_update_id

    @property
    def validated_post_snapshot_updates(self) -> int:
        return self._validated_post_snapshot_updates

    def ingest_update(
        self,
        document: dict[str, object],
        raw_ordinal: int,
    ) -> dict[str, object] | None:
        normalized = _normalize_spot_depth_update(document, raw_ordinal)
        first_id = int(cast(str, normalized["first_update_id"]))
        final_id = int(cast(str, normalized["final_update_id"]))
        buffered = _BufferedSpotDepth(first_id, final_id, normalized)
        if not self._has_snapshot:
            if len(self._buffer) >= self._max_buffered_updates:
                self._clear()
                raise BinanceDataIntegrityError(
                    "Binance Spot depth bootstrap buffer exceeded its bound.",
                    quality_event="buffer_overflow",
                )
            if self._buffer and final_id < self._buffer[-1].final_update_id:
                self._clear()
                raise BinanceDataIntegrityError(
                    "Binance Spot depth update arrived out of order before snapshot.",
                    quality_event="sequence_error",
                )
            if self._buffer and final_id == self._buffer[-1].final_update_id:
                return None
            self._buffer.append(buffered)
            return None

        if self._last_update_id is None:
            raise AssertionError("snapshot state must carry a local update ID")
        if final_id < self._last_update_id:
            return None
        if first_id > self._last_update_id + 1:
            self._clear()
            raise BinanceDataIntegrityError(
                "Binance Spot depth sequence chain had a gap.",
                quality_event="sequence_gap",
            )
        if final_id == self._last_update_id:
            return {**normalized, "sequence_event": "equal_final_reapplication"}
        self._last_update_id = final_id
        self._validated_post_snapshot_updates += 1
        return {**normalized, "sequence_event": "update"}

    def accept_snapshot(
        self,
        document: dict[str, object],
        raw_ordinal: int,
    ) -> _SpotSnapshotAcceptance:
        if self._has_snapshot or not self._buffer:
            self._clear()
            raise BinanceDataIntegrityError(
                "Binance Spot depth snapshot arrived outside bootstrap state.",
                quality_event="snapshot_error",
            )
        snapshot = _normalize_spot_depth_snapshot(document, raw_ordinal)
        snapshot_id = int(cast(str, snapshot["last_update_id"]))
        if snapshot_id < self._buffer[0].first_update_id:
            return _SpotSnapshotAcceptance(retry_required=True, normalized_frames=())

        retained = tuple(update for update in self._buffer if update.final_update_id > snapshot_id)
        if retained and not (
            retained[0].first_update_id <= snapshot_id <= retained[0].final_update_id
        ):
            self._clear()
            raise BinanceDataIntegrityError(
                "Binance Spot depth snapshot could not join the buffered sequence.",
                quality_event="sequence_gap",
            )

        normalized_updates: list[dict[str, object]] = []
        local_id = snapshot_id
        for index, update in enumerate(retained):
            if update.final_update_id <= local_id:
                continue
            if index > 0 and update.first_update_id > local_id + 1:
                self._clear()
                raise BinanceDataIntegrityError(
                    "Binance Spot buffered depth sequence had a gap.",
                    quality_event="sequence_gap",
                )
            normalized_updates.append({**update.normalized, "sequence_event": "bootstrap_update"})
            local_id = update.final_update_id

        self._has_snapshot = True
        self._last_update_id = local_id
        self._validated_post_snapshot_updates = len(normalized_updates)
        self._buffer.clear()
        return _SpotSnapshotAcceptance(
            retry_required=False,
            normalized_frames=(snapshot, *normalized_updates),
        )

    def _clear(self) -> None:
        self._buffer.clear()
        self._last_update_id = None
        self._has_snapshot = False
        self._validated_post_snapshot_updates = 0


@dataclass(frozen=True, slots=True)
class _StreamProfile:
    name: str
    product: str
    channels: dict[str, str]
    required_streams: frozenset[str]
    optional_streams: frozenset[str]


_SPOT_PROFILE: Final = _StreamProfile(
    "spot",
    BINANCE_SPOT_PRODUCT,
    _SPOT_STREAM_CHANNELS,
    frozenset(_SPOT_STREAM_CHANNELS),
    frozenset(),
)
_USDM_MARKET_PROFILE: Final = _StreamProfile(
    "usdm_market",
    BINANCE_USDM_PRODUCT,
    _USDM_MARKET_STREAM_CHANNELS,
    frozenset({"btcusdt@aggTrade", "btcusdt@markPrice@1s"}),
    frozenset({"btcusdt@forceOrder"}),
)
_USDM_PUBLIC_PROFILE: Final = _StreamProfile(
    "usdm_public",
    BINANCE_USDM_PRODUCT,
    _USDM_PUBLIC_STREAM_CHANNELS,
    frozenset(_USDM_PUBLIC_STREAM_CHANNELS),
    frozenset(),
)
_STREAM_PROFILES: Final = (_SPOT_PROFILE, _USDM_MARKET_PROFILE, _USDM_PUBLIC_PROFILE)


class BinancePublicResearchCollector:
    """Capture fixed public Spot and USDⓈ-M data through one fail-closed run."""

    def __init__(
        self,
        sink: RawResearchSink,
        *,
        config: BinancePublicResearchConfig | None = None,
        spot_connection_factory: ConnectionFactory | None = None,
        usdm_market_connection_factory: ConnectionFactory | None = None,
        usdm_public_connection_factory: ConnectionFactory | None = None,
        spot_depth_fetcher: PayloadFetcher | None = None,
        usdm_open_interest_fetcher: PayloadFetcher | None = None,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        session_id_factory: SessionIdFactory | None = None,
    ) -> None:
        self._sink = sink
        self._config = config if config is not None else BinancePublicResearchConfig()
        self._spot_connection_factory = spot_connection_factory or _connection_factory(
            BINANCE_SPOT_WEBSOCKET_URL,
            self._config,
        )
        self._usdm_market_connection_factory = (
            usdm_market_connection_factory
            or _connection_factory(BINANCE_USDM_MARKET_WEBSOCKET_URL, self._config)
        )
        self._usdm_public_connection_factory = (
            usdm_public_connection_factory
            or _connection_factory(BINANCE_USDM_PUBLIC_WEBSOCKET_URL, self._config)
        )
        self._spot_depth_fetcher = spot_depth_fetcher or _payload_fetcher(
            BINANCE_SPOT_DEPTH_URL,
            self._config,
            utc_ns,
            monotonic_ns,
        )
        self._usdm_open_interest_fetcher = usdm_open_interest_fetcher or _payload_fetcher(
            BINANCE_USDM_OPEN_INTEREST_URL,
            self._config,
            utc_ns,
            monotonic_ns,
        )
        self._utc_ns = utc_ns
        self._monotonic_ns = monotonic_ns
        self._session_id_factory = session_id_factory or (lambda: uuid.uuid4().hex)
        self._ordinal = 0
        self._append_lock = asyncio.Lock()
        self._sink_failed = False
        self._required_last_seen_monotonic: dict[tuple[str, str], float] = {}
        self._profile_watch_started_monotonic: dict[str, float] = {}

    async def capture_for(
        self,
        duration_seconds: float,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        _require_bounded_duration(duration_seconds)
        external_stop = stop_event
        internal_stop = asyncio.Event()
        tasks = (
            asyncio.create_task(
                self._run_stream(
                    _SPOT_PROFILE,
                    self._spot_connection_factory,
                    internal_stop,
                )
            ),
            asyncio.create_task(
                self._run_stream(
                    _USDM_MARKET_PROFILE,
                    self._usdm_market_connection_factory,
                    internal_stop,
                )
            ),
            asyncio.create_task(
                self._run_stream(
                    _USDM_PUBLIC_PROFILE,
                    self._usdm_public_connection_factory,
                    internal_stop,
                )
            ),
            asyncio.create_task(self._capture_open_interest(internal_stop)),
        )
        timer = asyncio.create_task(asyncio.sleep(float(duration_seconds)))
        external_wait = (
            asyncio.create_task(external_stop.wait()) if external_stop is not None else None
        )
        watchers: set[asyncio.Task[object]] = {cast(asyncio.Task[object], task) for task in tasks}
        watchers.add(cast(asyncio.Task[object], timer))
        if external_wait is not None:
            watchers.add(cast(asyncio.Task[object], external_wait))

        failure: BaseException | None = None
        try:
            while not internal_stop.is_set():
                done, _ = await asyncio.wait(watchers, return_when=asyncio.FIRST_COMPLETED)
                if timer in done or (external_wait is not None and external_wait in done):
                    internal_stop.set()
                    break
                for task in tasks:
                    if task not in done:
                        continue
                    task_error = task.exception()
                    if task_error is not None:
                        failure = task_error
                    elif task is not tasks[3]:
                        failure = BinanceTransportError(
                            "Binance required public stream ended unexpectedly."
                        )
                    if failure is not None:
                        internal_stop.set()
                        break
                if failure is not None:
                    break
                watchers.difference_update(done)
        finally:
            internal_stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            timer.cancel()
            if external_wait is not None:
                external_wait.cancel()
            with suppress(asyncio.CancelledError):
                await timer
            if external_wait is not None:
                with suppress(asyncio.CancelledError):
                    await external_wait

        if failure is not None:
            raise failure
        for task in tasks:
            task_error = task.exception()
            if task_error is not None:
                raise task_error

    async def _run_stream(
        self,
        profile: _StreamProfile,
        connection_factory: ConnectionFactory,
        stop_event: asyncio.Event,
    ) -> None:
        reconnects = 0
        while not stop_event.is_set():
            self._mark_profile_watch_start(profile)
            session_id = self._session_id_factory()
            book_state = (
                _SpotBookState(
                    max_buffered_updates=self._config.max_buffered_spot_depth_updates,
                )
                if profile is _SPOT_PROFILE
                else None
            )
            capture_logger().info(
                "binance session_start transport_profile=%s",
                profile.name,
            )
            await self._marker(
                profile.product,
                "session",
                session_id,
                {"event": "session_start", "transport_profile": profile.name},
            )
            await self._marker(
                profile.product,
                "subscription",
                session_id,
                {
                    "event": "subscription_requested",
                    "transport_profile": profile.name,
                    "streams": sorted(profile.channels),
                    "authenticated": False,
                },
            )
            try:
                async with connection_factory() as connection:
                    await self._receive_session(
                        profile,
                        connection,
                        session_id,
                        stop_event,
                        book_state,
                    )
                await self._marker(
                    profile.product,
                    "session",
                    session_id,
                    {"event": "session_end", "reason": "controlled_stop"},
                )
                return
            except (BinanceDataIntegrityError, BinanceSinkError):
                stop_event.set()
                raise
            except asyncio.CancelledError:
                raise
            except _BinanceServerShutdown:
                await self._marker(
                    profile.product,
                    "session",
                    session_id,
                    {
                        "event": "disconnect",
                        "reason": "venue_server_shutdown",
                        "transport_profile": profile.name,
                    },
                )
                await self._quality(
                    profile.product,
                    session_id,
                    "gap",
                    "venue_server_shutdown",
                    None,
                    extra={"transport_profile": profile.name},
                )
                if reconnects >= self._config.max_reconnects:
                    stop_event.set()
                    raise BinanceTransportError(
                        "Binance public reconnect bound was exhausted."
                    ) from None
                reconnects += 1
                capture_logger().info(
                    "binance reconnect transport_profile=%s attempt=%s "
                    "reason=venue_server_shutdown",
                    profile.name,
                    reconnects,
                )
                await self._marker(
                    profile.product,
                    "session",
                    session_id,
                    {
                        "event": "reconnect",
                        "attempt": reconnects,
                        "transport_profile": profile.name,
                    },
                )
                await _wait_or_stop(
                    _reconnect_wait_seconds(
                        self._config.reconnect_delay_seconds,
                        reconnects,
                    ),
                    stop_event,
                )
                await self._raise_if_required_stream_starved(profile, session_id)
            except PayloadTooBig:
                await self._quality(
                    profile.product,
                    session_id,
                    "truncation_error",
                    "transport_payload_truncated",
                    None,
                    extra={"transport_profile": profile.name},
                )
                stop_event.set()
                raise BinanceDataIntegrityError(
                    "Binance WebSocket payload exceeded the transport bound.",
                    quality_event="truncation_error",
                ) from None
            except (WebSocketException, OSError, ConnectionError) as error:
                failure_fields = transport_exception_fields(error)
                del error
                capture_logger().info(
                    "binance disconnect transport_profile=%s exception_class=%s close_code=%s",
                    profile.name,
                    failure_fields.get("exception_class"),
                    failure_fields.get("close_code"),
                )
                await self._marker(
                    profile.product,
                    "session",
                    session_id,
                    {
                        "event": "disconnect",
                        "reason": "transport_failure",
                        "transport_profile": profile.name,
                        **failure_fields,
                    },
                )
                await self._quality(
                    profile.product,
                    session_id,
                    "gap",
                    "transport_disconnect",
                    None,
                    extra={"transport_profile": profile.name, **failure_fields},
                )
                if stop_event.is_set():
                    return
                if reconnects >= self._config.max_reconnects:
                    stop_event.set()
                    raise BinanceTransportError(
                        "Binance public reconnect bound was exhausted."
                    ) from None
                reconnects += 1
                capture_logger().info(
                    "binance reconnect transport_profile=%s attempt=%s",
                    profile.name,
                    reconnects,
                )
                await self._marker(
                    profile.product,
                    "session",
                    session_id,
                    {
                        "event": "reconnect",
                        "attempt": reconnects,
                        "transport_profile": profile.name,
                    },
                )
                await _wait_or_stop(
                    _reconnect_wait_seconds(
                        self._config.reconnect_delay_seconds,
                        reconnects,
                    ),
                    stop_event,
                )
                await self._raise_if_required_stream_starved(profile, session_id)
            except BaseException:
                stop_event.set()
                raise BinanceTransportError("Binance public transport boundary failed.") from None

    async def _receive_session(
        self,
        profile: _StreamProfile,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
        book_state: _SpotBookState | None,
    ) -> None:
        observed: set[str] = set()
        self._mark_profile_watch_start(profile)
        while not stop_event.is_set():
            remaining = self._remaining_required_stream_seconds(profile)
            if remaining <= 0:
                await self._raise_if_required_stream_starved(profile, session_id)
            captured = await _receive_or_stop(
                connection,
                stop_event,
                utc_ns=self._utc_ns,
                monotonic_ns=self._monotonic_ns,
                timeout_seconds=max(remaining, 0.01),
            )
            if captured is None:
                if stop_event.is_set():
                    break
                await self._raise_if_required_stream_starved(profile, session_id)
                continue
            if len(captured.payload_bytes) > self._config.max_application_payload_bytes:
                raw_ordinal = await self._raw(
                    profile.product,
                    "unrouted",
                    session_id,
                    captured,
                )
                await self._quality(
                    profile.product,
                    session_id,
                    "truncation_error",
                    "application_payload_oversize",
                    raw_ordinal,
                )
                raise BinanceDataIntegrityError(
                    "Binance complete application payload exceeded its bound.",
                    quality_event="truncation_error",
                )
            if captured.frame_type is not FrameType.TEXT:
                raw_ordinal = await self._raw(
                    profile.product,
                    "unrouted",
                    session_id,
                    captured,
                )
                await self._quality(
                    profile.product,
                    session_id,
                    "schema_error",
                    "non_text_application_frame",
                    raw_ordinal,
                )
                raise BinanceDataIntegrityError(
                    "Binance JSON stream returned a non-text application frame."
                )
            try:
                document = _decode_json_object(captured.payload_bytes)
                if profile is _SPOT_PROFILE and _is_server_shutdown(document):
                    await self._raw(
                        profile.product,
                        "server_shutdown",
                        session_id,
                        captured,
                    )
                    raise _BinanceServerShutdown(
                        "Binance Spot server announced a controlled shutdown."
                    )
                stream, data = _combined_stream(document, profile.channels)
            except _BinanceServerShutdown:
                raise
            except BinanceDataIntegrityError as error:
                raw_ordinal = await self._raw(
                    profile.product,
                    "unrouted",
                    session_id,
                    captured,
                )
                await self._quality(
                    profile.product,
                    session_id,
                    error.quality_event,
                    "inbound_identity_or_schema",
                    raw_ordinal,
                )
                raise

            source_channel = profile.channels[stream]
            raw_ordinal = await self._raw(
                profile.product,
                source_channel,
                session_id,
                captured,
            )
            try:
                if profile is _SPOT_PROFILE:
                    if book_state is None:
                        raise AssertionError("Spot profile must carry book state")
                    await self._handle_spot(
                        stream,
                        data,
                        raw_ordinal,
                        session_id,
                        book_state,
                    )
                else:
                    normalized = _normalize_usdm(stream, data, raw_ordinal)
                    await self._normalized(
                        profile.product,
                        "normalized_usdm_context",
                        session_id,
                        normalized,
                    )
            except BinanceDataIntegrityError as error:
                if not error.reported:
                    await self._quality(
                        profile.product,
                        session_id,
                        error.quality_event,
                        "inbound_validation",
                        raw_ordinal,
                    )
                    error.reported = True
                raise

            self._note_required_stream(profile, stream)
            if stream not in observed:
                observed.add(stream)
                await self._marker(
                    profile.product,
                    "subscription",
                    session_id,
                    {
                        "event": "subscription_observed",
                        "transport_profile": profile.name,
                        "stream": stream,
                        "authenticated": False,
                    },
                )

        # Idle reconnect + operator/duration stop is allowed only when this
        # profile already observed every required stream earlier in the run
        # and none of those streams are past the documented silence bound.
        if (
            stop_event.is_set()
            and not observed
            and self._profile_required_complete(profile)
            and self._starved_required_stream(profile) is None
        ):
            return
        if not self._profile_required_complete(profile):
            await self._raise_required_stream_liveness(
                profile,
                session_id,
                reason="required_streams_unobserved",
            )
        await self._raise_if_required_stream_starved(profile, session_id)
        if book_state is not None and (
            not book_state.has_snapshot or book_state.validated_post_snapshot_updates < 1
        ):
            await self._quality(
                profile.product,
                session_id,
                "snapshot_error",
                "snapshot_or_post_snapshot_update_missing",
                None,
            )
            missing_snapshot_error = BinanceDataIntegrityError(
                "Binance Spot depth snapshot or validated update was incomplete.",
                quality_event="snapshot_error",
            )
            missing_snapshot_error.reported = True
            raise missing_snapshot_error

    def _mark_profile_watch_start(self, profile: _StreamProfile) -> None:
        self._profile_watch_started_monotonic.setdefault(profile.name, time.monotonic())

    def _note_required_stream(self, profile: _StreamProfile, stream: str) -> None:
        if stream in profile.required_streams:
            self._required_last_seen_monotonic[(profile.name, stream)] = time.monotonic()

    def _observed_required_streams(self, profile: _StreamProfile) -> set[str]:
        return {
            stream for name, stream in self._required_last_seen_monotonic if name == profile.name
        }

    def _profile_required_complete(self, profile: _StreamProfile) -> bool:
        return profile.required_streams <= self._observed_required_streams(profile)

    def _starved_required_stream(self, profile: _StreamProfile) -> tuple[str, float] | None:
        now = time.monotonic()
        started = self._profile_watch_started_monotonic.get(profile.name, now)
        threshold = float(self._config.required_stream_starvation_seconds)
        worst: tuple[str, float] | None = None
        for stream in sorted(profile.required_streams):
            last_seen = self._required_last_seen_monotonic.get((profile.name, stream))
            silence = now - (last_seen if last_seen is not None else started)
            if silence >= threshold and (worst is None or silence > worst[1]):
                worst = (stream, silence)
        return worst

    def _remaining_required_stream_seconds(self, profile: _StreamProfile) -> float:
        now = time.monotonic()
        started = self._profile_watch_started_monotonic.get(profile.name, now)
        threshold = float(self._config.required_stream_starvation_seconds)
        remaining = threshold
        for stream in profile.required_streams:
            last_seen = self._required_last_seen_monotonic.get((profile.name, stream))
            reference = last_seen if last_seen is not None else started
            remaining = min(remaining, threshold - (now - reference))
        return remaining

    async def _raise_if_required_stream_starved(
        self,
        profile: _StreamProfile,
        session_id: str,
    ) -> None:
        starved = self._starved_required_stream(profile)
        if starved is None:
            return
        stream, silence_seconds = starved
        await self._raise_required_stream_liveness(
            profile,
            session_id,
            reason="required_stream_starved",
            stream=stream,
            silence_seconds=silence_seconds,
        )

    async def _raise_required_stream_liveness(
        self,
        profile: _StreamProfile,
        session_id: str,
        *,
        reason: str,
        stream: str | None = None,
        silence_seconds: float | None = None,
    ) -> NoReturn:
        extra: dict[str, object] = {
            "transport_profile": profile.name,
            "threshold_seconds": float(self._config.required_stream_starvation_seconds),
        }
        starved = self._starved_required_stream(profile)
        if stream is not None:
            extra["stream"] = stream
        elif starved is not None:
            extra["stream"] = starved[0]
            extra["silence_seconds"] = round(starved[1], 6)
        if silence_seconds is not None:
            extra["silence_seconds"] = round(silence_seconds, 6)
        missing = sorted(profile.required_streams - self._observed_required_streams(profile))
        if missing:
            extra["missing_streams"] = missing
        capture_logger().info(
            "binance liveness_error transport_profile=%s reason=%s stream=%s",
            profile.name,
            reason,
            extra.get("stream"),
        )
        await self._quality(
            profile.product,
            session_id,
            "liveness_error",
            reason,
            None,
            extra=extra,
        )
        message = (
            "Binance required public stream was starved."
            if reason == "required_stream_starved"
            else "Binance required public streams were not observed."
        )
        error = BinanceDataIntegrityError(message, quality_event="liveness_error")
        error.reported = True
        raise error

    async def _handle_spot(
        self,
        stream: str,
        data: dict[str, object],
        raw_ordinal: int,
        session_id: str,
        book_state: _SpotBookState,
    ) -> None:
        if stream == "btcusdt@trade":
            normalized = _normalize_spot_trade(data, raw_ordinal)
            await self._normalized(
                BINANCE_SPOT_PRODUCT,
                "normalized_spot_trade",
                session_id,
                normalized,
            )
            return
        if stream == "btcusdt@bookTicker":
            normalized = _normalize_spot_bbo(data, raw_ordinal)
            await self._normalized(
                BINANCE_SPOT_PRODUCT,
                "normalized_spot_bbo",
                session_id,
                normalized,
            )
            return
        if stream != "btcusdt@depth@100ms":
            raise BinanceDataIntegrityError("Binance Spot stream routing failed.")

        normalized_update = book_state.ingest_update(data, raw_ordinal)
        if normalized_update is not None:
            await self._normalized(
                BINANCE_SPOT_PRODUCT,
                "normalized_spot_depth",
                session_id,
                normalized_update,
            )
            return
        if book_state.has_snapshot or not book_state.has_buffered_update:
            return

        for attempt in range(1, self._config.max_spot_snapshot_requests + 1):
            try:
                captured = await self._fetch_payload(self._spot_depth_fetcher, "Spot depth")
            except BinanceDataIntegrityError as error:
                await self._quality(
                    BINANCE_SPOT_PRODUCT,
                    session_id,
                    error.quality_event,
                    "snapshot_transport_truncation",
                    None,
                )
                error.reported = True
                raise
            raw_snapshot_ordinal = await self._raw(
                BINANCE_SPOT_PRODUCT,
                "spot_depth_snapshot",
                session_id,
                captured,
            )
            try:
                snapshot_document = _decode_json_object(captured.payload_bytes)
                acceptance = book_state.accept_snapshot(
                    snapshot_document,
                    raw_snapshot_ordinal,
                )
            except BinanceDataIntegrityError as error:
                await self._quality(
                    BINANCE_SPOT_PRODUCT,
                    session_id,
                    error.quality_event,
                    "snapshot_validation",
                    raw_snapshot_ordinal,
                )
                error.reported = True
                raise
            if acceptance.retry_required:
                await self._marker(
                    BINANCE_SPOT_PRODUCT,
                    "subscription",
                    session_id,
                    {
                        "event": "snapshot_retry",
                        "attempt": attempt,
                        "raw_message_ordinal": raw_snapshot_ordinal,
                    },
                )
                continue
            for normalized in acceptance.normalized_frames:
                await self._normalized(
                    BINANCE_SPOT_PRODUCT,
                    "normalized_spot_depth",
                    session_id,
                    normalized,
                )
            await self._marker(
                BINANCE_SPOT_PRODUCT,
                "subscription",
                session_id,
                {
                    "event": "snapshot_installed",
                    "attempt": attempt,
                    "raw_message_ordinal": raw_snapshot_ordinal,
                },
            )
            return
        raise BinanceDataIntegrityError(
            "Binance Spot depth snapshot retry bound was exhausted.",
            quality_event="snapshot_error",
        )

    async def _capture_open_interest(self, stop_event: asyncio.Event) -> None:
        if stop_event.is_set():
            return
        session_id = self._session_id_factory()
        await self._marker(
            BINANCE_USDM_PRODUCT,
            "session",
            session_id,
            {"event": "session_start", "transport_profile": "usdm_rest"},
        )
        try:
            captured = await self._fetch_payload(
                self._usdm_open_interest_fetcher,
                "USD-M open interest",
            )
        except BinanceDataIntegrityError as error:
            await self._quality(
                BINANCE_USDM_PRODUCT,
                session_id,
                error.quality_event,
                "open_interest_transport_truncation",
                None,
            )
            error.reported = True
            stop_event.set()
            raise
        raw_ordinal = await self._raw(
            BINANCE_USDM_PRODUCT,
            "usdm_open_interest",
            session_id,
            captured,
        )
        try:
            normalized = _normalize_open_interest(
                _decode_json_object(captured.payload_bytes),
                raw_ordinal,
            )
        except BinanceDataIntegrityError as error:
            await self._quality(
                BINANCE_USDM_PRODUCT,
                session_id,
                error.quality_event,
                "open_interest_validation",
                raw_ordinal,
            )
            error.reported = True
            stop_event.set()
            raise
        await self._normalized(
            BINANCE_USDM_PRODUCT,
            "normalized_usdm_context",
            session_id,
            normalized,
        )
        await self._marker(
            BINANCE_USDM_PRODUCT,
            "session",
            session_id,
            {"event": "session_end", "reason": "rest_complete"},
        )

    async def _fetch_payload(
        self,
        fetcher: PayloadFetcher,
        boundary_name: str,
    ) -> CapturedApplicationPayload:
        try:
            captured = await fetcher()
        except asyncio.CancelledError:
            raise
        except BaseException:
            raise BinanceTransportError(f"Binance {boundary_name} request failed.") from None
        if type(captured) is not CapturedApplicationPayload:
            raise BinanceTransportError(f"Binance {boundary_name} response boundary failed.")
        if len(captured.payload_bytes) > self._config.max_application_payload_bytes:
            raise BinanceDataIntegrityError(
                f"Binance {boundary_name} response exceeded its bound.",
                quality_event="truncation_error",
            )
        return captured

    async def _raw(
        self,
        product: str,
        channel: str,
        session_id: str,
        captured: CapturedApplicationPayload,
    ) -> int:
        async with self._append_lock:
            self._ordinal += 1
            ordinal = self._ordinal
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=BINANCE_RESEARCH_VENUE,
                product=product,
                channel=channel,
                session_id=session_id,
                message_ordinal=ordinal,
                received_utc_ns=captured.received_utc_ns,
                received_monotonic_ns=captured.received_monotonic_ns,
                direction=MessageDirection.INBOUND,
                frame_type=captured.frame_type,
                payload_encoding=captured.payload_encoding,
                payload_bytes=captured.payload_bytes,
            )
            await self._append(record)
            return ordinal

    async def _normalized(
        self,
        product: str,
        channel: str,
        session_id: str,
        document: dict[str, object],
    ) -> None:
        await self._marker(product, channel, session_id, document)

    async def _quality(
        self,
        product: str,
        session_id: str,
        event: str,
        reason: str,
        raw_message_ordinal: int | None,
        extra: dict[str, object] | None = None,
    ) -> None:
        marker: dict[str, object] = {"event": event, "reason": reason}
        if raw_message_ordinal is not None:
            marker["raw_message_ordinal"] = raw_message_ordinal
        if extra:
            marker.update(extra)
        await self._marker(product, "data_quality", session_id, marker)

    async def _marker(
        self,
        product: str,
        channel: str,
        session_id: str,
        document: dict[str, object],
    ) -> None:
        payload = json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        captured = capture_application_payload(
            payload,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )
        async with self._append_lock:
            self._ordinal += 1
            await self._append(
                RawResearchRecord(
                    schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                    venue=BINANCE_RESEARCH_VENUE,
                    product=product,
                    channel=channel,
                    session_id=session_id,
                    message_ordinal=self._ordinal,
                    received_utc_ns=captured.received_utc_ns,
                    received_monotonic_ns=captured.received_monotonic_ns,
                    direction=MessageDirection.LOCAL,
                    frame_type=FrameType.MARKER,
                    payload_encoding=PayloadEncoding.UTF8_JSON,
                    payload_bytes=captured.payload_bytes,
                )
            )

    async def _append(self, record: RawResearchRecord) -> None:
        if self._sink_failed:
            raise BinanceSinkError("Binance research sink is unavailable after an append failure.")
        try:
            await self._sink.append(record)
        except asyncio.CancelledError:
            raise
        except BaseException:
            self._sink_failed = True
            raise BinanceSinkError("Binance research sink append failed.") from None


def _normalize_spot_trade(document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
    _require_raw_ordinal(raw_ordinal)
    payload = {
        "e": _required_text(document, "e"),
        "E": _required_int(document, "E"),
        "s": _required_text(document, "s"),
        "t": _required_int(document, "t"),
        "p": _required_decimal(document, "p", positive=True),
        "q": _required_decimal(document, "q", positive=True),
        "T": _required_int(document, "T"),
        "m": _required_bool(document, "m"),
        "M": _required_bool(document, "M"),
    }
    if payload["s"] != BINANCE_NATIVE_SYMBOL:
        raise BinanceDataIntegrityError("Binance Spot trade symbol validation failed.")
    try:
        decoded = decode_binance_spot_trade(
            payload,
            timestamp_unit=BinanceTimestampUnit.MICROSECONDS,
        )
    except (TypeError, ValueError):
        raise BinanceDataIntegrityError("Binance Spot trade schema validation failed.") from None
    return {
        "raw_message_ordinal": raw_ordinal,
        "source_channel": "spot_trade",
        "symbol": decoded.symbol,
        "trade_id": str(decoded.trade_id),
        "price": cast(str, payload["p"]),
        "quantity": cast(str, payload["q"]),
        "exchange_event_time": str(decoded.exchange_event_time),
        "trade_time": str(decoded.trade_time),
        "timestamp_unit": decoded.timestamp_unit.value,
        "buyer_was_maker": decoded.buyer_was_maker,
        "ignore_flag": decoded.ignore_flag,
        "aggressor_side": decoded.aggressor_side.value,
    }


def _normalize_spot_bbo(document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
    _require_raw_ordinal(raw_ordinal)
    if _required_text(document, "s") != BINANCE_NATIVE_SYMBOL:
        raise BinanceDataIntegrityError("Binance Spot BBO symbol validation failed.")
    return {
        "raw_message_ordinal": raw_ordinal,
        "source_channel": "spot_book_ticker",
        "symbol": BINANCE_NATIVE_SYMBOL,
        "update_id": str(_required_int(document, "u")),
        "bid_price": _required_decimal(document, "b", positive=True),
        "bid_quantity": _required_decimal(document, "B", non_negative=True),
        "ask_price": _required_decimal(document, "a", positive=True),
        "ask_quantity": _required_decimal(document, "A", non_negative=True),
    }


def _normalize_spot_depth_update(
    document: dict[str, object],
    raw_ordinal: int,
) -> dict[str, object]:
    _require_raw_ordinal(raw_ordinal)
    if _required_text(document, "e") != "depthUpdate":
        raise BinanceDataIntegrityError("Binance Spot depth event validation failed.")
    if _required_text(document, "s") != BINANCE_NATIVE_SYMBOL:
        raise BinanceDataIntegrityError("Binance Spot depth symbol validation failed.")
    if "bids" in document or "asks" in document:
        raise BinanceDataIntegrityError("Binance Spot depth update schema validation failed.")
    first_id = _required_int(document, "U")
    final_id = _required_int(document, "u")
    if first_id > final_id:
        raise BinanceDataIntegrityError(
            "Binance Spot depth update range was inverted.",
            quality_event="sequence_error",
        )
    return {
        "raw_message_ordinal": raw_ordinal,
        "source_channel": "spot_depth",
        "message_type": "update",
        "symbol": BINANCE_NATIVE_SYMBOL,
        "exchange_event_time": str(_required_int(document, "E")),
        "first_update_id": str(first_id),
        "final_update_id": str(final_id),
        "sequence_event": "buffered",
        "events": _book_events(document, allow_zero=True),
    }


def _normalize_spot_depth_snapshot(
    document: dict[str, object],
    raw_ordinal: int,
) -> dict[str, object]:
    _require_raw_ordinal(raw_ordinal)
    if "b" in document or "a" in document:
        raise BinanceDataIntegrityError("Binance Spot depth snapshot schema validation failed.")
    return {
        "raw_message_ordinal": raw_ordinal,
        "source_channel": "spot_depth_snapshot",
        "message_type": "snapshot",
        "symbol": BINANCE_NATIVE_SYMBOL,
        "last_update_id": str(_required_int(document, "lastUpdateId")),
        "sequence_event": "snapshot",
        "events": _book_events(document, allow_zero=False),
    }


def _normalize_usdm(
    stream: str,
    document: dict[str, object],
    raw_ordinal: int,
) -> dict[str, object]:
    if stream == "btcusdt@aggTrade":
        return _normalize_usdm_agg_trade(document, raw_ordinal)
    if stream == "btcusdt@bookTicker":
        return _normalize_usdm_bbo(document, raw_ordinal)
    if stream == "btcusdt@markPrice@1s":
        return _normalize_usdm_mark(document, raw_ordinal)
    if stream == "btcusdt@forceOrder":
        return _normalize_usdm_force_order(document, raw_ordinal)
    raise BinanceDataIntegrityError("Binance USD-M stream routing failed.")


def _normalize_usdm_agg_trade(
    document: dict[str, object],
    raw_ordinal: int,
) -> dict[str, object]:
    _require_usdm_identity(document, event="aggTrade")
    quantity = _required_decimal(document, "q", positive=True)
    normal_quantity = _required_decimal(document, "nq", non_negative=True)
    if Decimal(normal_quantity) > Decimal(quantity):
        raise BinanceDataIntegrityError("Binance USD-M aggregate quantity validation failed.")
    buyer_was_maker = _required_bool(document, "m")
    return {
        "raw_message_ordinal": _require_raw_ordinal(raw_ordinal),
        "source_channel": "usdm_agg_trade",
        "context_type": "aggregate_trade",
        "symbol": BINANCE_NATIVE_SYMBOL,
        "exchange_event_time": str(_required_int(document, "E")),
        "transaction_time": str(_required_int(document, "T")),
        "aggregate_trade_id": str(_required_int(document, "a")),
        "first_trade_id": str(_required_int(document, "f")),
        "last_trade_id": str(_required_int(document, "l")),
        "price": _required_decimal(document, "p", positive=True),
        "quantity": quantity,
        "normal_quantity": normal_quantity,
        "buyer_was_maker": buyer_was_maker,
        "aggressor_side": "sell" if buyer_was_maker else "buy",
        "aggregation_window_ms": "100",
        "individual_trade": False,
        "insurance_and_adl_excluded": True,
    }


def _normalize_usdm_bbo(document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
    _require_usdm_identity(document, event="bookTicker", pair_required=True)
    return {
        "raw_message_ordinal": _require_raw_ordinal(raw_ordinal),
        "source_channel": "usdm_book_ticker",
        "context_type": "book_ticker",
        "symbol": BINANCE_NATIVE_SYMBOL,
        "exchange_event_time": str(_required_int(document, "E")),
        "transaction_time": str(_required_int(document, "T")),
        "update_id": str(_required_int(document, "u")),
        "bid_price": _required_decimal(document, "b", positive=True),
        "bid_quantity": _required_decimal(document, "B", non_negative=True),
        "ask_price": _required_decimal(document, "a", positive=True),
        "ask_quantity": _required_decimal(document, "A", non_negative=True),
        "rpi_excluded": True,
    }


def _normalize_usdm_mark(document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
    _require_usdm_identity(document, event="markPriceUpdate")
    return {
        "raw_message_ordinal": _require_raw_ordinal(raw_ordinal),
        "source_channel": "usdm_mark_price",
        "context_type": "mark_price",
        "symbol": BINANCE_NATIVE_SYMBOL,
        "exchange_event_time": str(_required_int(document, "E")),
        "mark_price": _required_decimal(document, "p", positive=True),
        "index_price": _required_decimal(document, "i", positive=True),
        "estimated_settle_price": _required_decimal(document, "P", non_negative=True),
        "mark_moving_average": _required_decimal(document, "ap", positive=True),
        "funding_rate": _required_decimal(document, "r"),
        "next_funding_time": str(_required_int(document, "T")),
    }


def _normalize_usdm_force_order(
    document: dict[str, object],
    raw_ordinal: int,
) -> dict[str, object]:
    if _required_text(document, "e") != "forceOrder":
        raise BinanceDataIntegrityError("Binance USD-M liquidation event validation failed.")
    _required_int(document, "E")
    if "st" in document and _required_int(document, "st") != 1:
        raise BinanceDataIntegrityError("Binance USD-M liquidation market-type validation failed.")
    if "ps" in document and _required_text(document, "ps") != BINANCE_NATIVE_SYMBOL:
        raise BinanceDataIntegrityError("Binance USD-M liquidation pair validation failed.")
    order = _required_object(document, "o")
    if _required_text(order, "s") != BINANCE_NATIVE_SYMBOL:
        raise BinanceDataIntegrityError("Binance USD-M liquidation symbol validation failed.")
    return {
        "raw_message_ordinal": _require_raw_ordinal(raw_ordinal),
        "source_channel": "usdm_force_order",
        "context_type": "liquidation",
        "symbol": BINANCE_NATIVE_SYMBOL,
        "exchange_event_time": str(_required_int(document, "E")),
        "transaction_time": str(_required_int(order, "T")),
        "side": _required_enum(order, "S", {"BUY", "SELL"}),
        "order_type": _required_text(order, "o"),
        "time_in_force": _required_text(order, "f"),
        "quantity": _required_decimal(order, "q", positive=True),
        "price": _required_decimal(order, "p", non_negative=True),
        "average_price": _required_decimal(order, "ap", non_negative=True),
        "order_status": _required_text(order, "X"),
        "last_filled_quantity": _required_decimal(order, "l", non_negative=True),
        "accumulated_filled_quantity": _required_decimal(order, "z", non_negative=True),
        "incomplete_snapshot": True,
        "absence_is_zero": False,
    }


def _normalize_open_interest(
    document: dict[str, object],
    raw_ordinal: int,
) -> dict[str, object]:
    if _required_text(document, "symbol") != BINANCE_NATIVE_SYMBOL:
        raise BinanceDataIntegrityError("Binance USD-M open-interest symbol validation failed.")
    return {
        "raw_message_ordinal": _require_raw_ordinal(raw_ordinal),
        "source_channel": "usdm_open_interest",
        "context_type": "open_interest",
        "symbol": BINANCE_NATIVE_SYMBOL,
        "open_interest": _required_decimal(document, "openInterest", non_negative=True),
        "transaction_time": str(_required_int(document, "time")),
    }


def _combined_stream(
    document: dict[str, object],
    allowed_channels: dict[str, str],
) -> tuple[str, dict[str, object]]:
    stream = _required_text(document, "stream")
    if stream not in allowed_channels:
        raise BinanceDataIntegrityError("Binance combined-stream identity validation failed.")
    return stream, _required_object(document, "data")


def _book_events(document: dict[str, object], *, allow_zero: bool) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    wire_order = 0
    for source_key, side in (("bids", "bid"), ("asks", "ask"), ("b", "bid"), ("a", "ask")):
        if source_key not in document:
            continue
        levels = document[source_key]
        if type(levels) is not list:
            raise BinanceDataIntegrityError("Binance order-book side schema validation failed.")
        for side_index, level in enumerate(cast(list[object], levels)):
            if type(level) is not list or len(level) != 2:
                raise BinanceDataIntegrityError(
                    "Binance order-book level schema validation failed."
                )
            values = cast(list[object], level)
            price = _decimal_value(values[0], positive=True)
            quantity = _decimal_value(values[1], non_negative=True)
            is_zero = Decimal(quantity) == 0
            if is_zero and not allow_zero:
                raise BinanceDataIntegrityError("Binance snapshot contained an empty book level.")
            events.append(
                {
                    "wire_order": wire_order,
                    "side": side,
                    "side_index": side_index,
                    "action": "delete" if is_zero else "upsert",
                    "price": price,
                    "quantity": quantity,
                }
            )
            wire_order += 1
    if not (("bids" in document and "asks" in document) or ("b" in document and "a" in document)):
        raise BinanceDataIntegrityError("Binance order-book sides were incomplete.")
    return events


def _require_usdm_identity(
    document: dict[str, object],
    *,
    event: str,
    pair_required: bool = False,
) -> None:
    if _required_text(document, "e") != event:
        raise BinanceDataIntegrityError("Binance USD-M event validation failed.")
    if _required_text(document, "s") != BINANCE_NATIVE_SYMBOL:
        raise BinanceDataIntegrityError("Binance USD-M symbol validation failed.")
    if _required_int(document, "st") != 1:
        raise BinanceDataIntegrityError("Binance USD-M market-type validation failed.")
    if pair_required and _required_text(document, "ps") != BINANCE_NATIVE_SYMBOL:
        raise BinanceDataIntegrityError("Binance USD-M pair validation failed.")


def _decode_json_object(payload_bytes: bytes) -> dict[str, object]:
    if type(payload_bytes) is not bytes:
        raise TypeError("payload_bytes must be immutable bytes.")
    try:
        text = payload_bytes.decode("utf-8")
        document = json.loads(
            text,
            parse_int=_IntegerLexeme,
            parse_float=_DecimalLexeme,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BinanceDataIntegrityError("Binance inbound JSON validation failed.") from None
    if type(document) is not dict:
        raise BinanceDataIntegrityError("Binance inbound JSON root validation failed.")
    return cast(dict[str, object], document)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise BinanceDataIntegrityError("Binance JSON contained a duplicate object key.")
        document[key] = value
    return document


def _reject_json_constant(_: str) -> object:
    raise BinanceDataIntegrityError("Binance JSON contained a non-standard numeric constant.")


def _is_server_shutdown(document: dict[str, object]) -> bool:
    if document.get("stream") != "!serverShutdown":
        return False
    data = _required_object(document, "data")
    if _required_text(data, "e") != "serverShutdown":
        raise BinanceDataIntegrityError("Binance server-shutdown event validation failed.")
    _required_int(data, "E")
    return True


def _required_object(document: dict[str, object], key: str) -> dict[str, object]:
    value = document.get(key)
    if type(value) is not dict:
        raise BinanceDataIntegrityError("Binance object field schema validation failed.")
    return cast(dict[str, object], value)


def _required_text(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if type(value) is not str or not value or value != value.strip():
        raise BinanceDataIntegrityError("Binance text field schema validation failed.")
    return value


def _required_enum(document: dict[str, object], key: str, allowed: set[str]) -> str:
    value = _required_text(document, key)
    if value not in allowed:
        raise BinanceDataIntegrityError("Binance enum field validation failed.")
    return value


def _required_bool(document: dict[str, object], key: str) -> bool:
    value = document.get(key)
    if type(value) is not bool:
        raise BinanceDataIntegrityError("Binance boolean field schema validation failed.")
    return value


def _required_int(document: dict[str, object], key: str) -> int:
    value = document.get(key)
    if type(value) is not _IntegerLexeme or not value.isascii() or not value.isdigit():
        raise BinanceDataIntegrityError("Binance integer field schema validation failed.")
    integer = int(value)
    if integer < 0:
        raise BinanceDataIntegrityError("Binance integer field schema validation failed.")
    return integer


def _required_decimal(
    document: dict[str, object],
    key: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> str:
    if positive and non_negative:
        raise AssertionError("decimal validation mode is ambiguous")
    return _decimal_value(document.get(key), positive=positive, non_negative=non_negative)


def _decimal_value(
    value: object,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> str:
    if type(value) is not str or _DECIMAL_TEXT.fullmatch(value) is None:
        raise BinanceDataIntegrityError("Binance decimal field schema validation failed.")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        raise BinanceDataIntegrityError("Binance decimal field schema validation failed.") from None
    if not decimal_value.is_finite():
        raise BinanceDataIntegrityError("Binance decimal field schema validation failed.")
    if positive and decimal_value <= 0:
        raise BinanceDataIntegrityError("Binance decimal field schema validation failed.")
    if non_negative and decimal_value < 0:
        raise BinanceDataIntegrityError("Binance decimal field schema validation failed.")
    return value


def _require_raw_ordinal(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("raw_ordinal must be a positive integer.")
    return value


async def _receive_or_stop(
    connection: WebSocketConnection,
    stop_event: asyncio.Event,
    *,
    utc_ns: NanosecondClock = time.time_ns,
    monotonic_ns: NanosecondClock = time.monotonic_ns,
    timeout_seconds: float | None = None,
) -> CapturedApplicationPayload | None:
    async def receive_captured() -> CapturedApplicationPayload:
        frame = await connection.recv()
        return capture_application_payload(
            frame,
            utc_ns=utc_ns,
            monotonic_ns=monotonic_ns,
        )

    receive_task = asyncio.create_task(receive_captured())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait(
            {receive_task, stop_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if receive_task in done:
            return await receive_task
        receive_task.cancel()
        with suppress(asyncio.CancelledError):
            await receive_task
        return None
    finally:
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task


async def _wait_or_stop(delay_seconds: float, stop_event: asyncio.Event) -> None:
    if delay_seconds <= 0:
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay_seconds)
    except TimeoutError:
        return


def _reconnect_wait_seconds(base_seconds: float, attempt: int) -> float:
    """Mild exponential backoff, capped below the 60s starve bound.

    Three profiles at a flat 3s would be 300 connection attempts / 5 minutes
    if every socket stormed — the official Spot IP connection limit.
    """

    if base_seconds <= 0:
        return 0.0
    if type(attempt) is not int or attempt < 1:
        raise ValueError("reconnect attempt must be a positive integer.")
    exponent = min(attempt - 1, 3)
    delay = float(base_seconds) * float(2**exponent)
    cap = float(BINANCE_RECONNECT_BACKOFF_CAP_SECONDS)
    return delay if delay < cap else cap


def _websocket_incoming_max_queue(websocket_url: str) -> int:
    """Larger incoming queue only for high-frequency Spot and USD-M /public."""

    if websocket_url == BINANCE_USDM_MARKET_WEBSOCKET_URL:
        return BINANCE_WEBSOCKET_MARKET_MAX_QUEUE
    if websocket_url in {BINANCE_SPOT_WEBSOCKET_URL, BINANCE_USDM_PUBLIC_WEBSOCKET_URL}:
        return BINANCE_WEBSOCKET_HIGH_FREQUENCY_MAX_QUEUE
    raise ValueError("unknown Binance public research WebSocket URL.")


def _websocket_connect_kwargs(
    config: BinancePublicResearchConfig,
    websocket_url: str,
) -> dict[str, object]:
    """Shared connect options; max_queue is higher for Spot and /public."""

    return {
        "max_size": config.max_application_payload_bytes,
        "proxy": None,
        "logger": _TRANSPORT_PRIVACY_LOGGER,
        "open_timeout": 10,
        "close_timeout": 5,
        "ping_interval": BINANCE_WEBSOCKET_CLIENT_PING_INTERVAL,
        "ping_timeout": BINANCE_WEBSOCKET_CLIENT_PING_TIMEOUT,
        "max_queue": _websocket_incoming_max_queue(websocket_url),
    }


def _connection_factory(
    websocket_url: str,
    config: BinancePublicResearchConfig,
) -> ConnectionFactory:
    def factory() -> AbstractAsyncContextManager[WebSocketConnection]:
        return cast(
            AbstractAsyncContextManager[WebSocketConnection],
            connect(
                websocket_url,
                max_size=config.max_application_payload_bytes,
                proxy=None,
                logger=_TRANSPORT_PRIVACY_LOGGER,
                open_timeout=10,
                close_timeout=5,
                ping_interval=BINANCE_WEBSOCKET_CLIENT_PING_INTERVAL,
                ping_timeout=BINANCE_WEBSOCKET_CLIENT_PING_TIMEOUT,
                max_queue=_websocket_incoming_max_queue(websocket_url),
            ),
        )

    return factory


def _payload_fetcher(
    url: str,
    config: BinancePublicResearchConfig,
    utc_ns: NanosecondClock,
    monotonic_ns: NanosecondClock,
) -> PayloadFetcher:
    async def fetch() -> CapturedApplicationPayload:
        return await asyncio.to_thread(
            _fetch_payload_sync,
            url,
            config,
            utc_ns,
            monotonic_ns,
        )

    return fetch


def _fetch_payload_sync(
    url: str,
    config: BinancePublicResearchConfig,
    utc_ns: NanosecondClock,
    monotonic_ns: NanosecondClock,
) -> CapturedApplicationPayload:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "hyperliquid-bot-data-research"},
        method="GET",
    )
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=config.http_timeout_seconds) as response:
        payload_bytes = response.read(config.max_application_payload_bytes + 1)
    captured_utc_ns = utc_ns()
    captured_monotonic_ns = monotonic_ns()
    return CapturedApplicationPayload(
        received_utc_ns=captured_utc_ns,
        received_monotonic_ns=captured_monotonic_ns,
        frame_type=FrameType.TEXT,
        payload_encoding=PayloadEncoding.UTF8,
        payload_bytes=bytes(payload_bytes),
    )


def _require_bounded_duration(duration_seconds: object) -> float:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not math.isfinite(duration) or not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
    return duration


def _config_for_duration(duration_seconds: float) -> BinancePublicResearchConfig:
    """Smoke keeps the Phase-1 reconnect cap; retained runs retry until duration ends."""

    duration = _require_bounded_duration(duration_seconds)
    if duration <= SMOKE_CAPTURE_SECONDS:
        return BinancePublicResearchConfig()
    return BinancePublicResearchConfig(max_reconnects=RETAINED_MAX_RECONNECTS)


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
            gap_event="gap",
            reconnect_event="reconnect",
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
    collector_factory: Callable[[RawResearchSink], BinancePublicResearchCollector] | None = None,
) -> dict[str, object]:
    """Run the no-credential capture, close Parquet, and build the DuckDB catalog."""

    duration = _require_bounded_duration(duration_seconds)
    writer = ParquetResearchWriter(output_dir, rotation=ParquetRotation())
    active = (
        collector_factory(writer)
        if collector_factory is not None
        else BinancePublicResearchCollector(
            writer,
            config=_config_for_duration(duration),
        )
    )
    capture_error: BaseException | None = None
    try:
        await active.capture_for(duration, stop_event=stop_event)
    except BaseException as error:
        capture_error = error
    finally:
        await writer.aclose()
    create_research_catalog(output_dir, database_path)
    report = build_capture_report(database_path, output_dir)
    if capture_error is not None:
        raise capture_error
    return report


def data1f_capture_claim(
    *,
    run_id: str,
    duration_seconds: float,
    paths: Data1FRunPaths,
) -> dict[str, object]:
    """Create-only start claim for a reconstructable DATA-1F run."""

    duration = _require_bounded_duration(duration_seconds)
    return {
        "schema": DATA1F_CLAIM_SCHEMA,
        "state": "STARTED_FAIL_CLOSED",
        "path_contract": DATA1F_PATH_CONTRACT_ID,
        "run_id": run_id,
        "venue": BINANCE_RESEARCH_VENUE,
        "product": DATA1F_PRODUCT,
        "products": [BINANCE_SPOT_PRODUCT, BINANCE_USDM_PRODUCT],
        "feed": "binance-public-btcusdt-spot-usdm",
        "spot_websocket_url": BINANCE_SPOT_WEBSOCKET_URL,
        "usdm_market_websocket_url": BINANCE_USDM_MARKET_WEBSOCKET_URL,
        "usdm_public_websocket_url": BINANCE_USDM_PUBLIC_WEBSOCKET_URL,
        "independent_websocket_profiles": ["spot", "usdm_market", "usdm_public"],
        "required_streams": {
            profile.name: sorted(profile.required_streams) for profile in _STREAM_PROFILES
        },
        "optional_streams": {
            profile.name: sorted(profile.optional_streams) for profile in _STREAM_PROFILES
        },
        "required_stream_starvation_seconds": REQUIRED_STREAM_STARVATION_SECONDS,
        "credentialless": True,
        "signing": False,
        "duration_seconds": duration,
        "smoke_duration_seconds": SMOKE_CAPTURE_SECONDS,
        "max_duration_seconds": MAX_CAPTURE_SECONDS,
        "retained": duration > SMOKE_CAPTURE_SECONDS,
        "twenty_four_seven": False,
        "resume_policy": "never resume or overwrite an existing DATA-1F run directory",
        "raw_dir": paths.raw_dir.as_posix(),
        "database_path": paths.database_path.as_posix(),
    }


def data1f_capture_health(
    *,
    run_id: str,
    duration_seconds: float,
    status: str,
    report: dict[str, object],
) -> dict[str, object]:
    """Create-only end health for a reconstructable DATA-1F run."""

    if status not in {"COMPLETED", "OPERATOR_STOP", "FAILED"}:
        raise ValueError("DATA-1F capture-health status is outside the documented bound.")
    duration = _require_bounded_duration(duration_seconds)
    elapsed = elapsed_from_report(report)
    return attach_observability_health(
        {
            "schema": DATA1F_HEALTH_SCHEMA,
            "kind": "capture-health",
            "path_contract": DATA1F_PATH_CONTRACT_ID,
            "run_id": run_id,
            "status": status,
            "duration_seconds": duration,
            "retained": duration > SMOKE_CAPTURE_SECONDS,
            "twenty_four_seven": False,
            "credentialless": True,
            "events": report.get("events"),
            "payload_bytes": report.get("payload_bytes"),
            "parquet_files": report.get("parquet_files"),
            "parquet_bytes": report.get("parquet_bytes"),
            "gaps": report.get("gaps"),
            "reconnects": report.get("reconnects"),
            "required_stream_starvation_seconds": REQUIRED_STREAM_STARVATION_SECONDS,
            "required_streams": {
                profile.name: sorted(profile.required_streams) for profile in _STREAM_PROFILES
            },
            "optional_streams": {
                profile.name: sorted(profile.optional_streams) for profile in _STREAM_PROFILES
            },
            "limitations": [
                "Published Parquet parts are reconstructable; a crash can lose the "
                "in-memory segment.",
                "This is not 24/7 service evidence or a trading edge.",
                "Spot depth@100ms is heavier than DATA-1A; disk growth can reach tens of "
                "GB over 72h.",
                "USD-M open interest is one REST observation at start, not a history.",
                "Public stream only; no API keys, signing, or extra venues.",
                "Transport gaps exclude fail-closed integrity events such as sequence_gap "
                "or liveness_error.",
                "USD-M bookTicker uses a dedicated /public combined socket; "
                "aggTrade/markPrice/forceOrder stay on /market and are not mixed.",
                "Required-stream application silence past "
                f"{REQUIRED_STREAM_STARVATION_SECONDS:g}s fails closed mid-run with "
                "liveness_error; OPERATOR_STOP cannot accept an empty required stream.",
                "USD-M forceOrder is optional; liquidation silence is not starvation.",
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
    collector_factory: Callable[[RawResearchSink], BinancePublicResearchCollector] | None = None,
) -> dict[str, object]:
    """Write DATA-1F Parquet/DuckDB to the documented reconstructable path."""

    paths = data1f_run_paths(artifact_root, run_id)
    if paths.run_dir.exists():
        raise FileExistsError(f"DATA-1F refuses to reuse existing run directory: {paths.run_dir}")
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    paths.raw_dir.mkdir(exist_ok=False)
    log_path = capture_log_path(paths.run_dir, run_id)
    configure_capture_logger(log_path)
    capture_logger().info(
        "data1f start run_id=%s requested_duration_seconds=%s log=%s",
        run_id,
        duration_seconds,
        log_path,
    )
    _write_create_only_json(
        paths.capture_claim_path,
        data1f_capture_claim(run_id=run_id, duration_seconds=duration_seconds, paths=paths),
    )
    report: dict[str, object] = {
        "events": 0,
        "payload_bytes": 0,
        "parquet_files": 0,
        "parquet_bytes": 0,
        "gaps": 0,
        "reconnects": 0,
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
        )
        if operator_stop is not None and operator_stop():
            status = "OPERATOR_STOP"
        else:
            status = "COMPLETED"
    except BaseException:
        if paths.database_path.exists():
            report = build_capture_report(paths.database_path, paths.raw_dir)
        raise
    finally:
        report = {**report, "elapsed_seconds": round(time.monotonic() - started, 6)}
        capture_logger().info(
            "data1f stop run_id=%s status=%s requested_duration_seconds=%s elapsed_seconds=%s",
            run_id,
            status,
            duration_seconds,
            report["elapsed_seconds"],
        )
        if not paths.capture_health_path.exists():
            _write_create_only_json(
                paths.capture_health_path,
                data1f_capture_health(
                    run_id=run_id,
                    duration_seconds=duration_seconds,
                    status=status,
                    report=report,
                ),
            )
    return {
        **report,
        "run_id": run_id,
        "path_contract": DATA1F_PATH_CONTRACT_ID,
        "run_dir": str(paths.run_dir),
        "raw_dir": str(paths.raw_dir),
        "database_path": str(paths.database_path),
        "status": status,
        "twenty_four_seven": False,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Public Binance BTCUSDT exact-raw research capture. "
            "Duration may exceed the historical 180s smoke cap up to 7 days. "
            "This is not a 24/7 service. Do not start a multi-day retain while "
            "the TerraPC DATA-1A hl-capture session is still running unless CoS assigns it."
        )
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--duration-seconds", required=True, type=float)
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

    mode = _resolve_cli_mode(args)
    if mode == "reconstructable":
        return await run_reconstructable_capture(
            artifact_root=cast(Path, args.artifact_root),
            run_id=cast(str, args.run_id),
            duration_seconds=cast(float, args.duration_seconds),
            stop_event=stop_event,
            operator_stop=lambda: operator_stopped,
        )
    return await run_bounded_capture(
        output_dir=cast(Path, args.output_dir),
        database_path=cast(Path, args.database),
        duration_seconds=cast(float, args.duration_seconds),
        stop_event=stop_event,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    report = asyncio.run(_run_from_args(args))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
