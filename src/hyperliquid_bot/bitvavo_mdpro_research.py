"""Authenticated Bitvavo Market Data Pro BTC-EUR capture.

The same Pro socket carries book (depth 1000) plus trades. Ticker is optional
and flagged. Duration may be a short smoke or a retained multi-day run. The
process still stops at an explicit duration or operator signal; this is not a
24/7 service. Read-only MD Pro keys enter only through documented environment
names. This path never falls back to DATA-1D Standard.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
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
from enum import Enum
from pathlib import Path
from typing import Final, Protocol, cast

import duckdb
from websockets.asyncio.client import connect
from websockets.exceptions import PayloadTooBig, WebSocketException

from .capture_observability import (
    DISCONNECT_LOG_SUFFIX,
    add_transport_counts,
    attach_observability_health,
    capture_log_path,
    capture_logger,
    configure_capture_logger,
    disconnect_log_values,
    elapsed_from_report,
    transport_exception_fields,
)
from .capture_operator_alert import emit_capture_operator_alert
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
    DATA1E_PATH_CONTRACT_ID,
    DATA1E_PRODUCT,
    Data1ERunPaths,
    data1e_run_paths,
)

BITVAVO_MDPRO_WEBSOCKET_URL: Final = "wss://ws-mdpro.bitvavo.com/v2/"
BITVAVO_MDPRO_VENUE: Final = "bitvavo"
BITVAVO_MDPRO_PRODUCT: Final = "BTC-EUR"
BITVAVO_MDPRO_FEED_PRODUCT: Final = "market_data_pro"
BITVAVO_MDPRO_BOOK_DEPTH: Final = 1000
BITVAVO_MDPRO_REQUIRED_CHANNELS: Final = ("book", "trades")
BITVAVO_MDPRO_OPTIONAL_TICKER_CHANNEL: Final = "ticker"
BITVAVO_MDPRO_SIGNATURE_PATH: Final = "/v2/websocket"
# Official MD Pro docs require authenticate-then-subscribe and do not define an
# application-level ping
# (https://docs.bitvavo.com/docs/ws-market-data-pro-api/introduction/).
# Bitvavo still closes long sockets with code 1000 reason "Ping timeout" when a
# protocol Pong is late (server pings about every 50s; python-bitvavo-api#58).
# The official Python SDK enables protocol ping_interval. A ping_timeout would
# self-close with 1011 "keepalive ping timeout" when Bitvavo is slow to Pong,
# so the timeout stays disabled. A full receive queue pauses socket reads and
# delays those Pongs across a Parquet flush; keep the queue high.
BITVAVO_MDPRO_WEBSOCKET_CLIENT_PING_INTERVAL: Final[float | None] = 20.0
BITVAVO_MDPRO_WEBSOCKET_CLIENT_PING_TIMEOUT: Final[float | None] = None
BITVAVO_MDPRO_WEBSOCKET_MAX_QUEUE: Final[tuple[int, int]] = (16_384, 4_096)
# Docs do not require client ping; soft-reconnect when market frames go silent.
BITVAVO_MDPRO_APPLICATION_IDLE_RECONNECT_SECONDS: Final = 180.0
# Client bounds. Official MD Pro docs require authenticate-then-subscribe and
# do not publish an acknowledgement deadline. The signature `window` (default
# 10_000 ms, max 60_000 ms) is the request execution window, not this timeout.
BITVAVO_MDPRO_AUTH_ACK_TIMEOUT_SECONDS: Final = 10.0
BITVAVO_MDPRO_SUBSCRIBE_ACK_TIMEOUT_SECONDS: Final = 10.0
BITVAVO_MDPRO_SNAPSHOT_TIMEOUT_SECONDS: Final = 15.0
_MDPRO_IDLE_RECONNECTS: Final = {
    "application idle reconnect": "application_idle",
    "book idle reconnect": "book_idle",
    "trades idle reconnect": "trades_idle",
    "snapshot bootstrap timeout": "snapshot_bootstrap_timeout",
    "authentication ack timeout": "authentication_ack_timeout",
}
SMOKE_CAPTURE_SECONDS: Final = 600.0
MAX_CAPTURE_SECONDS: Final = 7 * 24 * 60 * 60
RETAINED_MAX_RECONNECTS: Final = 10_080
DATA1E_CLAIM_SCHEMA: Final = "data-1e-retained-capture-claim-v1"
DATA1E_HEALTH_SCHEMA: Final = "data-1e-retained-capture-health-v1"
BITVAVO_MDPRO_API_KEY_ENV: Final = "BITVAVO_MDPRO_API_KEY"
BITVAVO_MDPRO_API_SECRET_ENV: Final = "BITVAVO_MDPRO_API_SECRET"
BITVAVO_MDPRO_REQUIRED_ENV: Final = (BITVAVO_MDPRO_API_KEY_ENV, BITVAVO_MDPRO_API_SECRET_ENV)
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
_MARKET_CHANNEL_BY_EVENT: Final = {
    "book": "mdpro_book",
    "trade": "mdpro_trades",
    "ticker": "mdpro_ticker",
}
_SENSITIVE_KEY_NAMES: Final = frozenset(
    {
        "apikey",
        "apisecret",
        "accesskey",
        "accesstoken",
        "authorization",
        "bearertoken",
        "clientsecret",
        "key",
        "password",
        "refreshtoken",
        "secret",
        "signature",
        "token",
        "websockettoken",
    }
)

_TRANSPORT_PRIVACY_LOGGER: Final = logging.Logger(
    "hyperliquid_bot.bitvavo_mdpro_research_transport",
    level=logging.CRITICAL + 1,
)
_TRANSPORT_PRIVACY_LOGGER.disabled = True
_TRANSPORT_PRIVACY_LOGGER.propagate = False
_TRANSPORT_PRIVACY_LOGGER.addHandler(logging.NullHandler())


class BitvavoMdProCaptureError(RuntimeError):
    """Bounded public error whose message never includes venue or secret text."""


class BitvavoMdProAuthenticationError(BitvavoMdProCaptureError):
    """Credential or authenticate-ack failure. Not reconnectable."""


class BitvavoMdProDataIntegrityError(BitvavoMdProCaptureError):
    """An inbound identity, schema, snapshot, or sequence rule failed."""

    def __init__(self, message: str, *, quality_event: str = "schema_error") -> None:
        super().__init__(message)
        self.quality_event = quality_event


class BitvavoMdProTransportError(BitvavoMdProCaptureError):
    """A required connection or bounded reconnect failed."""


class BitvavoMdProSubscriptionError(BitvavoMdProTransportError):
    """Subscribe/ack race or protocol rejection. Retained runs may reconnect."""


class BitvavoMdProSinkError(BitvavoMdProCaptureError):
    """The shared raw sink failed and capture stopped without retry."""


class _IntegerLexeme(str):
    """Distinguish a JSON integer token from a quoted string."""


class _DecimalLexeme(str):
    """Distinguish an unquoted JSON decimal from an official decimal string."""


class _ReceiveFailure(Enum):
    """Secret-free result used to detach payload-size failures from recv()."""

    PAYLOAD_TOO_BIG = "payload_too_big"


class WebSocketConnection(Protocol):
    """Small transport surface implemented by websockets and offline fakes."""

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]
type SessionIdFactory = Callable[[], str]
type TimestampMsFactory = Callable[[], int]


class BitvavoMdProCredentials:
    """In-memory View-only key material with redacted representations."""

    __slots__ = ("_api_key", "_api_secret", "_signatures")

    def __init__(self, *, api_key: str, api_secret: str) -> None:
        if (
            type(api_key) is not str
            or len(api_key) < 8
            or api_key != api_key.strip()
            or type(api_secret) is not str
            or len(api_secret) < 8
            or api_secret != api_secret.strip()
        ):
            raise BitvavoMdProAuthenticationError(
                "Bitvavo Market Data Pro credentials are invalid."
            )
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._signatures: list[bytes] = []

    def __repr__(self) -> str:
        return "BitvavoMdProCredentials(<redacted>)"

    __str__ = __repr__

    def _authentication_message(self, *, timestamp_ms: int, window_ms: int) -> str:
        if type(timestamp_ms) is not int or timestamp_ms <= 0:
            raise BitvavoMdProAuthenticationError("Bitvavo authentication timestamp is invalid.")
        message = f"{timestamp_ms}GET{BITVAVO_MDPRO_SIGNATURE_PATH}".encode("ascii")
        signature = hmac.new(self._api_secret, message, hashlib.sha256).hexdigest()
        self._signatures.append(signature.encode("ascii"))
        return json.dumps(
            {
                "action": "authenticate",
                "key": self._api_key,
                "signature": signature,
                "timestamp": timestamp_ms,
                "window": window_ms,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def _contains_sensitive_material(self, payload: bytes) -> bool:
        materials = (
            self._api_key.encode("utf-8"),
            self._api_secret,
            *self._signatures,
        )
        return any(material in payload for material in materials)

    def _contains_sensitive_decoded_material(self, value: object) -> bool:
        if isinstance(value, str):
            return self._contains_sensitive_material(value.encode("utf-8"))
        if type(value) is dict:
            document = cast(dict[object, object], value)
            return any(
                self._contains_sensitive_decoded_material(key)
                or self._contains_sensitive_decoded_material(child)
                for key, child in document.items()
            )
        if type(value) is list:
            return any(
                self._contains_sensitive_decoded_material(item)
                for item in cast(list[object], value)
            )
        return False


@dataclass(frozen=True, slots=True)
class BitvavoMdProResearchConfig:
    """Fixed BTC-EUR Pro book+trades scope with optional ticker and bounded transport."""

    authentication_window_ms: int = 10_000
    reconnect_delay_seconds: float = 3.0
    max_application_payload_bytes: int = 8 * 1024 * 1024
    max_buffered_book_updates: int = 10_000
    max_reconnects: int = 1
    include_ticker: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.authentication_window_ms) is not int
            or not 100 <= self.authentication_window_ms <= 60_000
        ):
            raise ValueError("authentication_window_ms must be between 100 and 60000.")
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
        if type(self.max_reconnects) is not int or self.max_reconnects < 0:
            raise ValueError("max_reconnects must be a non-negative integer.")
        if type(self.include_ticker) is not bool:
            raise ValueError("include_ticker must be a bool.")

    @property
    def subscription_channels(self) -> tuple[str, ...]:
        return mdpro_subscription_channels(include_ticker=self.include_ticker)


@dataclass(frozen=True, slots=True)
class _BufferedBookUpdate:
    start_sequence: int
    end_sequence: int
    normalized: dict[str, object]


@dataclass(frozen=True, slots=True)
class _SnapshotAcceptance:
    normalized_frames: tuple[dict[str, object], ...]
    retained_updates: int


class _BookState:
    """Snapshot-first Pro L2 state with exact documented range chaining."""

    def __init__(self, *, max_buffered_updates: int = 10_000) -> None:
        if type(max_buffered_updates) is not int or max_buffered_updates <= 0:
            raise ValueError("max_buffered_updates must be a positive integer.")
        self._max_buffered_updates = max_buffered_updates
        self._buffer: list[_BufferedBookUpdate] = []
        self._last_sequence: int | None = None
        self._has_snapshot = False
        self._post_snapshot_updates = 0

    @property
    def has_snapshot(self) -> bool:
        return self._has_snapshot

    @property
    def last_sequence(self) -> int | None:
        return self._last_sequence

    @property
    def post_snapshot_updates(self) -> int:
        return self._post_snapshot_updates

    def ingest_update(
        self,
        document: dict[str, object],
        raw_ordinal: int,
    ) -> dict[str, object] | None:
        normalized = _normalize_book_update(document, raw_ordinal)
        start = int(cast(str, normalized["sequence_start"]))
        end = int(cast(str, normalized["sequence_end"]))
        previous = (
            self._last_sequence
            if self._has_snapshot
            else self._buffer[-1].end_sequence
            if self._buffer
            else None
        )
        if previous is not None:
            self._require_next(previous, start)
        if self._has_snapshot:
            self._last_sequence = end
            self._post_snapshot_updates += 1
            return normalized
        if len(self._buffer) >= self._max_buffered_updates:
            self._clear()
            raise BitvavoMdProDataIntegrityError(
                "Bitvavo Market Data Pro bootstrap buffer exceeded its bound.",
                quality_event="buffer_overflow",
            )
        self._buffer.append(
            _BufferedBookUpdate(
                start_sequence=start,
                end_sequence=end,
                normalized=normalized,
            )
        )
        return None

    def accept_snapshot(
        self,
        document: dict[str, object],
        raw_ordinal: int,
        *,
        expected_request_id: int,
    ) -> _SnapshotAcceptance:
        if self._has_snapshot:
            self._clear()
            raise BitvavoMdProDataIntegrityError(
                "Bitvavo Market Data Pro snapshot arrived outside bootstrap state.",
                quality_event="snapshot_error",
            )
        snapshot = _normalize_book_snapshot(
            document,
            raw_ordinal,
            expected_request_id=expected_request_id,
        )
        snapshot_sequence = int(cast(str, snapshot["sequence_end"]))
        retained: list[_BufferedBookUpdate] = []
        previous = snapshot_sequence
        for update in self._buffer:
            if update.end_sequence <= snapshot_sequence:
                continue
            if update.start_sequence <= snapshot_sequence < update.end_sequence:
                self._clear()
                raise BitvavoMdProDataIntegrityError(
                    "Bitvavo Market Data Pro buffered sequence range straddled the snapshot.",
                    quality_event="sequence_overlap",
                )
            self._require_next(previous, update.start_sequence)
            retained.append(update)
            previous = update.end_sequence
        self._has_snapshot = True
        self._last_sequence = previous
        self._post_snapshot_updates = len(retained)
        self._buffer.clear()
        return _SnapshotAcceptance(
            normalized_frames=(snapshot, *(update.normalized for update in retained)),
            retained_updates=len(retained),
        )

    def _require_next(self, previous: int, start: int) -> None:
        if start <= previous:
            self._clear()
            raise BitvavoMdProDataIntegrityError(
                "Bitvavo Market Data Pro sequence was duplicate, stale, or out of order.",
                quality_event="sequence_error",
            )
        if start != previous + 1:
            self._clear()
            raise BitvavoMdProDataIntegrityError(
                "Bitvavo Market Data Pro sequence chain had a gap.",
                quality_event="sequence_gap",
            )

    def _clear(self) -> None:
        self._buffer.clear()
        self._last_sequence = None
        self._has_snapshot = False
        self._post_snapshot_updates = 0


class BitvavoMdProResearchCollector:
    """Capture authenticated non-conflated BTC-EUR Pro book, trades, and optional ticker."""

    def __init__(
        self,
        sink: RawResearchSink,
        credentials: BitvavoMdProCredentials,
        *,
        config: BitvavoMdProResearchConfig | None = None,
        connection_factory: ConnectionFactory | None = None,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        timestamp_ms: TimestampMsFactory | None = None,
        session_id_factory: SessionIdFactory | None = None,
    ) -> None:
        if type(credentials) is not BitvavoMdProCredentials:
            raise TypeError("credentials must be BitvavoMdProCredentials.")
        self._sink = sink
        self._credentials = credentials
        self._config = config if config is not None else BitvavoMdProResearchConfig()
        self._connection_factory = (
            connection_factory
            if connection_factory is not None
            else _connection_factory(self._config)
        )
        self._utc_ns = utc_ns
        self._monotonic_ns = monotonic_ns
        self._timestamp_ms = (
            timestamp_ms if timestamp_ms is not None else lambda: time.time_ns() // 1_000_000
        )
        self._session_id_factory = (
            session_id_factory
            if session_id_factory is not None
            else lambda: f"mdpro-{uuid.uuid4().hex}"
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
        """Run the fixed authenticated slice for a strictly bounded duration."""

        _require_bounded_duration(duration_seconds)
        capture_stop = stop_event if stop_event is not None else asyncio.Event()
        timer = asyncio.create_task(
            self._stop_after(capture_stop, float(duration_seconds)),
            name="bitvavo-mdpro-research-duration",
        )
        stream = asyncio.create_task(
            self._run_stream(capture_stop),
            name="bitvavo-mdpro-research-stream",
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
            failure: str | None = None
            disconnect_fields: dict[str, int | str] = {}
            idle_reconnect_reason: str | None = None
            venue_ping_timeout = False
            subscription_disconnect_reason = "subscription_ack_failed"
            try:
                async with self._connection_factory() as connection:
                    connected = True
                    await self._connected(session_id, previous_session_id)
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
                        stream=BITVAVO_MDPRO_FEED_PRODUCT,
                        reason="capture_limit_reached",
                    )
                    return
            except asyncio.CancelledError:
                raise
            except PayloadTooBig:
                failure = "truncation"
            except BitvavoMdProSubscriptionError as error:
                failure = "subscription"
                if "timed out" in str(error):
                    subscription_disconnect_reason = "subscription_ack_timeout"
                disconnect_fields = {"exception_class": "BitvavoMdProSubscriptionError"}
            except (
                BitvavoMdProAuthenticationError,
                BitvavoMdProDataIntegrityError,
                BitvavoMdProSinkError,
            ):
                raise
            except (WebSocketException, OSError) as error:
                failure = "transport"
                idle_reconnect_reason = _mdpro_idle_reconnect_reason(error)
                venue_ping_timeout = False
                if idle_reconnect_reason is None:
                    disconnect_fields = transport_exception_fields(error)
                    venue_ping_timeout = _is_venue_ping_timeout(disconnect_fields)
                del error
            except Exception:
                failure = "boundary"

            if failure == "truncation":
                await self._terminal_quality(session_id, "truncation_error")
                raise BitvavoMdProDataIntegrityError(
                    "Bitvavo Market Data Pro payload exceeded the transport bound.",
                    quality_event="truncation_error",
                ) from None
            if failure == "boundary":
                raise BitvavoMdProTransportError(
                    "Bitvavo Market Data Pro transport boundary failed."
                ) from None
            if failure in {"transport", "subscription"}:
                if not connected:
                    await self._connection_failed(session_id, previous_session_id)
                elif failure == "subscription":
                    await self._subscription_failed_reconnectable(
                        session_id,
                        reason=subscription_disconnect_reason,
                    )
                elif idle_reconnect_reason is not None:
                    await self._append_marker(
                        session_id,
                        "session",
                        "disconnected",
                        stream=BITVAVO_MDPRO_FEED_PRODUCT,
                        transport_profile=BITVAVO_MDPRO_FEED_PRODUCT,
                        reason=idle_reconnect_reason,
                    )
                elif venue_ping_timeout:
                    await self._disconnected(
                        session_id,
                        disconnect_fields,
                        reason="venue_ping_timeout",
                    )
                else:
                    await self._disconnected(session_id, disconnect_fields)
                if reconnects >= self._config.max_reconnects:
                    if failure == "subscription":
                        raise BitvavoMdProSubscriptionError(
                            "Bitvavo Market Data Pro subscription reconnect bound was exhausted."
                        ) from None
                    raise BitvavoMdProTransportError(
                        "Bitvavo Market Data Pro reconnect bound was exhausted."
                    ) from None
                previous_session_id = session_id
                reconnects += 1
            await self._wait_to_reconnect(stop_event)

    async def _receive_session(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
        *,
        is_reconnect: bool,
    ) -> None:
        await self._send_authentication(connection, session_id)
        if stop_event.is_set():
            return
        await self._require_authentication_ack(connection, session_id, stop_event)
        if stop_event.is_set():
            return
        await self._send_subscription(connection, session_id)
        if stop_event.is_set():
            return
        await self._require_subscription_ack(connection, session_id, stop_event)
        if stop_event.is_set():
            return
        await self._send_snapshot_request(connection, session_id, request_id=1)

        book_state = _BookState(
            max_buffered_updates=self._config.max_buffered_book_updates,
        )
        snapshot_received = False
        expected_channels = frozenset(self._config.subscription_channels)
        session_healthy = False
        bootstrap_started_ns = self._monotonic_ns()
        last_book_ns = bootstrap_started_ns
        last_trade_ns = bootstrap_started_ns
        while not stop_event.is_set():
            now_ns = self._monotonic_ns()
            timeout_reason: str | None = None
            if not book_state.has_snapshot:
                remaining = BITVAVO_MDPRO_SNAPSHOT_TIMEOUT_SECONDS - (
                    (now_ns - bootstrap_started_ns) / 1_000_000_000
                )
                if remaining <= 0:
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "snapshot_missing",
                        stream=BITVAVO_MDPRO_FEED_PRODUCT,
                        reason="snapshot_bootstrap_timeout",
                    )
                    raise ConnectionError("snapshot bootstrap timeout")
                timeout_seconds: float | None = max(remaining, 0.01)
                timeout_reason = "snapshot_bootstrap_timeout"
            else:
                idle_budget = BITVAVO_MDPRO_APPLICATION_IDLE_RECONNECT_SECONDS
                book_remaining = idle_budget - ((now_ns - last_book_ns) / 1_000_000_000)
                trade_remaining = idle_budget - ((now_ns - last_trade_ns) / 1_000_000_000)
                if book_remaining <= 0:
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "gap_detected",
                        stream=BITVAVO_MDPRO_FEED_PRODUCT,
                        transport_profile=BITVAVO_MDPRO_FEED_PRODUCT,
                        reason="book_idle",
                    )
                    raise ConnectionError("book idle reconnect")
                if trade_remaining <= 0:
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "gap_detected",
                        stream=BITVAVO_MDPRO_FEED_PRODUCT,
                        transport_profile=BITVAVO_MDPRO_FEED_PRODUCT,
                        reason="trades_idle",
                    )
                    raise ConnectionError("trades idle reconnect")
                timeout_seconds = max(min(book_remaining, trade_remaining), 0.01)
                timeout_reason = "book_idle" if book_remaining <= trade_remaining else "trades_idle"
            captured = await self._receive_or_stop(
                connection,
                stop_event,
                timeout_seconds=timeout_seconds,
            )
            if captured is None:
                if stop_event.is_set():
                    break
                if timeout_reason == "snapshot_bootstrap_timeout":
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "snapshot_missing",
                        stream=BITVAVO_MDPRO_FEED_PRODUCT,
                        reason="snapshot_bootstrap_timeout",
                    )
                    raise ConnectionError("snapshot bootstrap timeout")
                await self._append_marker(
                    session_id,
                    "data_quality",
                    "gap_detected",
                    stream=BITVAVO_MDPRO_FEED_PRODUCT,
                    transport_profile=BITVAVO_MDPRO_FEED_PRODUCT,
                    reason=timeout_reason or "book_idle",
                )
                if timeout_reason == "trades_idle":
                    raise ConnectionError("trades idle reconnect")
                raise ConnectionError("book idle reconnect")
            result, unpersisted_failure = await self._record_market_inbound(
                captured,
                session_id,
                expected_channels=expected_channels,
            )
            captured = None
            if unpersisted_failure is not None:
                await self._unpersisted_quality_failure(session_id, unpersisted_failure)
            if result is None:
                continue
            raw_ordinal, channel, document = result
            try:
                normalized_frames: tuple[dict[str, object], ...]
                normalized_channel: str
                if channel == "mdpro_book":
                    normalized = book_state.ingest_update(document, raw_ordinal)
                    normalized_frames = () if normalized is None else (normalized,)
                    normalized_channel = "normalized_mdpro_book"
                elif channel == "mdpro_book_snapshot":
                    if snapshot_received:
                        raise BitvavoMdProDataIntegrityError(
                            "Bitvavo Market Data Pro returned an unsolicited snapshot.",
                            quality_event="snapshot_error",
                        )
                    acceptance = book_state.accept_snapshot(
                        document,
                        raw_ordinal,
                        expected_request_id=1,
                    )
                    snapshot_received = True
                    normalized_frames = acceptance.normalized_frames
                    normalized_channel = "normalized_mdpro_book"
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "resnapshot_received" if is_reconnect else "snapshot_received",
                        stream=BITVAVO_MDPRO_FEED_PRODUCT,
                        raw_message_ordinal=raw_ordinal,
                        retained_updates=acceptance.retained_updates,
                        reason="fresh_market_data_pro_depth_1000_book_state",
                    )
                elif channel == "mdpro_trades":
                    normalized_frames = (_normalize_trade(document, raw_ordinal),)
                    normalized_channel = "normalized_mdpro_trades"
                elif channel == "mdpro_ticker":
                    if BITVAVO_MDPRO_OPTIONAL_TICKER_CHANNEL not in expected_channels:
                        raise BitvavoMdProDataIntegrityError(
                            "Bitvavo Market Data Pro ticker arrived without a ticker subscription.",
                            quality_event="subscription_error",
                        )
                    normalized_frames = (_normalize_ticker(document, raw_ordinal),)
                    normalized_channel = "normalized_mdpro_ticker"
                else:
                    raise AssertionError("unreachable Market Data Pro channel")
            except BitvavoMdProDataIntegrityError as error:
                await self._quality_failure(session_id, raw_ordinal, error)
                raise AssertionError("unreachable Market Data Pro normalization failure") from None

            for normalized in normalized_frames:
                await self._append_local_payload(
                    session_id,
                    normalized_channel,
                    normalized,
                )
            # Ticker must not refresh book or trades. One feed cannot mask the other.
            if channel in {"mdpro_book", "mdpro_book_snapshot"}:
                last_book_ns = self._monotonic_ns()
            elif channel == "mdpro_trades":
                last_trade_ns = self._monotonic_ns()
            if book_state.has_snapshot and not session_healthy:
                session_healthy = True
                recovered_ns = self._monotonic_ns()
                last_book_ns = recovered_ns
                last_trade_ns = recovered_ns

        if not book_state.has_snapshot:
            await self._append_marker(
                session_id,
                "data_quality",
                "snapshot_missing",
                stream=BITVAVO_MDPRO_FEED_PRODUCT,
                reason="capture_stopped_before_valid_book_snapshot",
            )
            raise BitvavoMdProDataIntegrityError(
                "Bitvavo Market Data Pro snapshot was incomplete.",
                quality_event="snapshot_missing",
            )
        if book_state.post_snapshot_updates == 0:
            await self._append_marker(
                session_id,
                "data_quality",
                "post_snapshot_update_missing",
                stream=BITVAVO_MDPRO_FEED_PRODUCT,
                reason="capture_stopped_before_validated_post_snapshot_update",
            )
            raise BitvavoMdProDataIntegrityError(
                "Bitvavo Market Data Pro post-snapshot update was missing.",
                quality_event="post_snapshot_update_missing",
            )

    async def _send_authentication(
        self,
        connection: WebSocketConnection,
        session_id: str,
    ) -> None:
        failed = False
        payload: str | None = None
        try:
            payload = self._credentials._authentication_message(
                timestamp_ms=self._timestamp_ms(),
                window_ms=self._config.authentication_window_ms,
            )
            await connection.send(payload)
        except asyncio.CancelledError:
            raise
        except (WebSocketException, OSError):
            # A socket close while sending authenticate is transport, not a bad key.
            raise
        except Exception:
            failed = True
        finally:
            payload = None
        if failed:
            raise BitvavoMdProAuthenticationError(
                "Bitvavo Market Data Pro authentication send failed."
            ) from None
        await self._append_marker(
            session_id,
            "authentication",
            "authentication_sent",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            authenticated=False,
        )

    async def _require_authentication_ack(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        failed = False
        captured: CapturedApplicationPayload | None = None
        document: dict[str, object] | None = None
        try:
            captured = await self._receive_or_stop(
                connection,
                stop_event,
                timeout_seconds=BITVAVO_MDPRO_AUTH_ACK_TIMEOUT_SECONDS,
            )
            if captured is None:
                if stop_event.is_set():
                    return
                await self._append_marker(
                    session_id,
                    "data_quality",
                    "authentication_ack_timeout",
                    stream=BITVAVO_MDPRO_FEED_PRODUCT,
                    reason="authentication_acknowledgement_timed_out",
                )
                raise ConnectionError("authentication ack timeout") from None
            if self._credentials._contains_sensitive_material(captured.payload_bytes):
                failed = True
            else:
                document = _decode_json_object(captured.payload_bytes)
                failed = self._credentials._contains_sensitive_decoded_material(
                    document
                ) or not _authentication_acknowledged(document)
        except asyncio.CancelledError:
            raise
        except (WebSocketException, OSError):
            # Code 1000 "Ping timeout" during the auth window must reconnect
            # with a fresh signature. It is not a credential rejection.
            raise
        except Exception:
            failed = True
        finally:
            captured = None
            document = None
        if failed:
            await self._append_marker(
                session_id,
                "data_quality",
                "authentication_failed",
                stream=BITVAVO_MDPRO_FEED_PRODUCT,
                reason="authentication_rejected_or_invalid",
            )
            raise BitvavoMdProAuthenticationError(
                "Bitvavo Market Data Pro authentication failed."
            ) from None
        await self._append_marker(
            session_id,
            "authentication",
            "authentication_acknowledged",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            authenticated=True,
        )

    async def _send_subscription(
        self,
        connection: WebSocketConnection,
        session_id: str,
    ) -> None:
        channels = self._config.subscription_channels
        failed = False
        try:
            await connection.send(
                mdpro_subscription_payload_text(include_ticker=self._config.include_ticker)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        if failed:
            raise BitvavoMdProTransportError(
                "Bitvavo Market Data Pro subscription send failed."
            ) from None
        await self._append_marker(
            session_id,
            "subscription",
            "subscription_sent",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            subscription_type="+".join(channels),
            channels=list(channels),
            product=BITVAVO_MDPRO_PRODUCT,
            authenticated=True,
        )

    async def _require_subscription_ack(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        expected = frozenset(self._config.subscription_channels)
        acknowledged: set[str] = set()
        while acknowledged != expected:
            failed = False
            retryable = False
            captured: CapturedApplicationPayload | None = None
            document: dict[str, object] | None = None
            newly: frozenset[str] = frozenset()
            try:
                captured = await self._receive_or_stop(
                    connection,
                    stop_event,
                    timeout_seconds=BITVAVO_MDPRO_SUBSCRIBE_ACK_TIMEOUT_SECONDS,
                )
                if captured is None:
                    if stop_event.is_set():
                        return
                    raise BitvavoMdProSubscriptionError(
                        "Bitvavo Market Data Pro subscription acknowledgement timed out."
                    ) from None
                if self._credentials._contains_sensitive_material(captured.payload_bytes):
                    failed = True
                else:
                    document = _decode_json_object(captured.payload_bytes)
                    if self._credentials._contains_sensitive_decoded_material(document):
                        failed = True
                    else:
                        newly = _subscription_acknowledged_channels(document, expected=expected)
                        if newly:
                            pass
                        elif _subscription_ack_should_ignore(document):
                            continue
                        elif _subscription_ack_is_venue_error(document):
                            retryable = True
                            failed = True
                        else:
                            # Unexpected control shape after subscribe — treat as
                            # reconnectable protocol race, not a credential failure.
                            retryable = True
                            failed = True
            except asyncio.CancelledError:
                raise
            except BitvavoMdProSubscriptionError:
                raise
            except Exception:
                failed = True
                retryable = True
            finally:
                captured = None
                document = None
            if failed:
                await self._append_marker(
                    session_id,
                    "data_quality",
                    "subscription_failed",
                    stream=BITVAVO_MDPRO_FEED_PRODUCT,
                    reason=(
                        "authenticated_pro_subscription_rejected_or_invalid"
                        if retryable
                        else "authenticated_pro_subscription_secret_rejected"
                    ),
                )
                if retryable:
                    raise BitvavoMdProSubscriptionError(
                        "Bitvavo Market Data Pro subscription failed."
                    ) from None
                raise BitvavoMdProAuthenticationError(
                    "Bitvavo Market Data Pro subscription failed."
                ) from None
            acknowledged.update(newly)
            await self._append_marker(
                session_id,
                "subscription",
                "subscription_acknowledged",
                stream=BITVAVO_MDPRO_FEED_PRODUCT,
                subscription_type="+".join(channel for channel in expected if channel in newly),
                channels=sorted(newly),
                product=BITVAVO_MDPRO_PRODUCT,
                authenticated=True,
            )

    async def _send_snapshot_request(
        self,
        connection: WebSocketConnection,
        session_id: str,
        *,
        request_id: int,
    ) -> None:
        payload = json.dumps(
            {
                "action": "getBook",
                "depth": BITVAVO_MDPRO_BOOK_DEPTH,
                "market": BITVAVO_MDPRO_PRODUCT,
                "requestId": request_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        failed = False
        try:
            await connection.send(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        finally:
            payload = ""
        if failed:
            raise BitvavoMdProTransportError(
                "Bitvavo Market Data Pro snapshot request failed."
            ) from None
        await self._append_marker(
            session_id,
            "subscription",
            "snapshot_requested",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            request_id=request_id,
            product=BITVAVO_MDPRO_PRODUCT,
            depth=BITVAVO_MDPRO_BOOK_DEPTH,
            authenticated=True,
        )

    async def _record_market_inbound(
        self,
        captured: CapturedApplicationPayload,
        session_id: str,
        *,
        expected_channels: frozenset[str],
    ) -> tuple[tuple[int, str, dict[str, object]] | None, str | None]:
        if len(captured.payload_bytes) > self._config.max_application_payload_bytes:
            return None, "payload_oversize"
        if self._credentials._contains_sensitive_material(captured.payload_bytes):
            return None, "secret_guard_error"
        try:
            document = _decode_json_object(captured.payload_bytes)
        except BitvavoMdProDataIntegrityError:
            return None, "schema_error"
        if _contains_sensitive_key(
            document
        ) or self._credentials._contains_sensitive_decoded_material(document):
            return None, "secret_guard_error"
        try:
            channel = _classify_market_document(document)
        except BitvavoMdProDataIntegrityError:
            return None, "schema_error"
        if channel == "control":
            newly = _subscription_acknowledged_channels(document, expected=expected_channels)
            if newly:
                await self._append_marker(
                    session_id,
                    "subscription",
                    "subscription_acknowledged",
                    stream=BITVAVO_MDPRO_FEED_PRODUCT,
                    subscription_type="+".join(name for name in expected_channels if name in newly),
                    channels=sorted(newly),
                    product=BITVAVO_MDPRO_PRODUCT,
                    authenticated=True,
                    reason="repeated_control_confirmation",
                )
                return None, None
            return None, "subscription_failed"
        raw_ordinal = await self._append_captured(
            captured,
            session_id=session_id,
            channel=channel,
            direction=MessageDirection.INBOUND,
        )
        return (raw_ordinal, channel, document), None

    async def _receive_or_stop(
        self,
        connection: WebSocketConnection,
        stop_event: asyncio.Event,
        *,
        timeout_seconds: float | None = None,
    ) -> CapturedApplicationPayload | None:
        receive_task = asyncio.create_task(
            self._receive_captured(connection),
            name="bitvavo-mdpro-research-receive",
        )
        stop_task = asyncio.create_task(stop_event.wait(), name="bitvavo-mdpro-stop-wait")
        received: CapturedApplicationPayload | _ReceiveFailure | None = None
        try:
            done, _ = await asyncio.wait(
                (receive_task, stop_task),
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                received = receive_task.result()
        finally:
            for task in (receive_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receive_task, stop_task, return_exceptions=True)

        if received is _ReceiveFailure.PAYLOAD_TOO_BIG:
            raise PayloadTooBig(
                None,
                self._config.max_application_payload_bytes,
            ) from None
        return received

    async def _receive_captured(
        self,
        connection: WebSocketConnection,
    ) -> CapturedApplicationPayload | _ReceiveFailure:
        try:
            frame = await connection.recv()
        except asyncio.CancelledError:
            raise
        except PayloadTooBig:
            return _ReceiveFailure.PAYLOAD_TOO_BIG
        except (WebSocketException, OSError):
            raise
        except Exception as error:
            raise OSError("Bitvavo Market Data Pro receive failed.") from error
        try:
            return capture_application_payload(
                frame,
                utc_ns=self._utc_ns,
                monotonic_ns=self._monotonic_ns,
            )
        except asyncio.CancelledError:
            raise
        except PayloadTooBig:
            return _ReceiveFailure.PAYLOAD_TOO_BIG
        except (WebSocketException, OSError):
            raise
        except Exception as error:
            raise OSError("Bitvavo Market Data Pro receive failed.") from error

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
                raise BitvavoMdProSinkError(
                    "Bitvavo Market Data Pro sink is unavailable after an append failure."
                )
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=BITVAVO_MDPRO_VENUE,
                product=BITVAVO_MDPRO_PRODUCT,
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
            if self._sink_failed:
                raise BitvavoMdProSinkError("Bitvavo Market Data Pro sink append failed.") from None
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
                raise BitvavoMdProSinkError(
                    "Bitvavo Market Data Pro sink is unavailable after an append failure."
                )
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=BITVAVO_MDPRO_VENUE,
                product=BITVAVO_MDPRO_PRODUCT,
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
            if self._sink_failed:
                raise BitvavoMdProSinkError("Bitvavo Market Data Pro sink append failed.") from None
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
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
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
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
        )
        if previous_session_id is not None:
            await self._append_marker(
                session_id,
                "session",
                "reconnected",
                stream=BITVAVO_MDPRO_FEED_PRODUCT,
                transport_profile=BITVAVO_MDPRO_FEED_PRODUCT,
                previous_session_id=previous_session_id,
            )

    async def _disconnected(
        self,
        session_id: str,
        failure_fields: dict[str, int | str] | None = None,
        *,
        reason: str = "transport_error",
    ) -> None:
        fields = dict(failure_fields or {})
        capture_logger().info(
            "bitvavo disconnect transport_profile=%s reason=%s " + DISCONNECT_LOG_SUFFIX,
            BITVAVO_MDPRO_FEED_PRODUCT,
            reason,
            *disconnect_log_values(fields),
        )
        gap_reason = (
            "venue_ping_timeout; reconnect will re-auth and re-subscribe"
            if reason == "venue_ping_timeout"
            else "transport_disconnect; missed Pro L2 history is not reconstructable"
        )
        await self._append_marker(
            session_id,
            "session",
            "disconnected",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            transport_profile=BITVAVO_MDPRO_FEED_PRODUCT,
            reason=reason,
            **fields,
        )
        await self._append_marker(
            session_id,
            "data_quality",
            "gap_detected",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            transport_profile=BITVAVO_MDPRO_FEED_PRODUCT,
            reason=gap_reason,
            **fields,
        )

    async def _subscription_failed_reconnectable(
        self,
        session_id: str,
        *,
        reason: str = "subscription_ack_failed",
    ) -> None:
        capture_logger().info(
            "bitvavo subscription_gap transport_profile=%s reason=%s",
            BITVAVO_MDPRO_FEED_PRODUCT,
            reason,
        )
        await self._append_marker(
            session_id,
            "session",
            "disconnected",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            transport_profile=BITVAVO_MDPRO_FEED_PRODUCT,
            reason=reason,
            exception_class="BitvavoMdProSubscriptionError",
        )
        await self._append_marker(
            session_id,
            "data_quality",
            "gap_detected",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            transport_profile=BITVAVO_MDPRO_FEED_PRODUCT,
            reason=f"{reason}; reconnect will re-auth and re-subscribe",
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
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            reason="transport_error",
        )

    async def _terminal_quality(self, session_id: str, event: str) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            event,
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            reason="capture_stopped_fail_closed",
        )

    async def _quality_failure(
        self,
        session_id: str,
        raw_ordinal: int,
        error: BitvavoMdProDataIntegrityError,
    ) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            error.quality_event,
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            reason="market_data_integrity_failure",
            raw_message_ordinal=raw_ordinal,
        )
        raise error

    async def _unpersisted_quality_failure(self, session_id: str, event: str) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            event,
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            reason="untrusted_or_sensitive_frame_not_persisted",
        )
        if event == "secret_guard_error":
            raise BitvavoMdProAuthenticationError(
                "Bitvavo Market Data Pro secret guard stopped capture."
            )
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro inbound frame failed before persistence.",
            quality_event=event,
        )

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


def _normalize_book_update(
    document: dict[str, object],
    raw_ordinal: int,
) -> dict[str, object]:
    allowed = {
        "asks",
        "bids",
        "endMdSeqNo",
        "event",
        "market",
        "nonce",
        "startMdSeqNo",
        "timestamp",
        "type",
    }
    required = allowed - {"nonce"}
    if (
        set(document) - allowed
        or not required <= set(document)
        or document.get("event") != "book"
        or document.get("market") != BITVAVO_MDPRO_PRODUCT
        or document.get("type") != "update"
    ):
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro book update identity validation failed."
        )
    start = _unsigned_integer_text(document, "startMdSeqNo")
    end = _unsigned_integer_text(document, "endMdSeqNo")
    if int(start) > int(end):
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro sequence range validation failed."
        )
    return {
        "event": "normalized_mdpro_book_frame",
        "source_channel": "mdpro_book",
        "raw_message_ordinal": raw_ordinal,
        "message_type": "update",
        "market": BITVAVO_MDPRO_PRODUCT,
        "deprecated_nonce": _optional_unsigned_integer_text(document, "nonce"),
        "venue_timestamp_ns": _unsigned_integer_text(document, "timestamp"),
        "sequence_start": start,
        "sequence_end": end,
        "sequence_event": "update_range",
        "events": _book_events(document, snapshot=False),
    }


def _normalize_book_snapshot(
    document: dict[str, object],
    raw_ordinal: int,
    *,
    expected_request_id: int,
) -> dict[str, object]:
    if set(document) != {"action", "requestId", "response"} or document.get("action") != "getBook":
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro snapshot action validation failed."
        )
    request_id = _unsigned_integer_text(document, "requestId")
    if request_id != str(expected_request_id):
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro snapshot request identity validation failed."
        )
    response = _object(document.get("response"))
    allowed = {"asks", "bids", "market", "mdSeqNo", "nonce", "timestamp"}
    required = allowed - {"nonce"}
    if (
        set(response) - allowed
        or not required <= set(response)
        or response.get("market") != BITVAVO_MDPRO_PRODUCT
    ):
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro snapshot identity validation failed."
        )
    sequence = _unsigned_integer_text(response, "mdSeqNo")
    return {
        "event": "normalized_mdpro_book_frame",
        "source_channel": "mdpro_book_snapshot",
        "raw_message_ordinal": raw_ordinal,
        "message_type": "snapshot",
        "market": BITVAVO_MDPRO_PRODUCT,
        "deprecated_nonce": _optional_unsigned_integer_text(response, "nonce"),
        "venue_timestamp_ns": _unsigned_integer_text(response, "timestamp"),
        "sequence_start": sequence,
        "sequence_end": sequence,
        "sequence_event": "snapshot",
        "events": _book_events(response, snapshot=True),
    }


def _book_events(
    document: dict[str, object],
    *,
    snapshot: bool,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    wire_order = 0
    for key, side in (("bids", "bid"), ("asks", "ask")):
        values = document.get(key)
        if type(values) is not list or (snapshot and len(values) > 1000):
            raise BitvavoMdProDataIntegrityError(
                "Bitvavo Market Data Pro order-book side validation failed."
            )
        for side_index, raw_level in enumerate(values):
            if type(raw_level) is not list or len(raw_level) != 2:
                raise BitvavoMdProDataIntegrityError(
                    "Bitvavo Market Data Pro order-book level validation failed."
                )
            level = cast(list[object], raw_level)
            price = _decimal_value(level[0], allow_zero=False)
            quantity = _decimal_value(level[1], allow_zero=not snapshot)
            if snapshot and Decimal(quantity) == 0:
                raise BitvavoMdProDataIntegrityError(
                    "Bitvavo Market Data Pro snapshot contained an empty level."
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
    return events


def _normalize_trade(document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
    allowed = {"amount", "event", "id", "market", "price", "side", "timestamp", "timestampNs"}
    if (
        set(document) - allowed
        or not allowed <= set(document)
        or document.get("event") != "trade"
        or document.get("market") != BITVAVO_MDPRO_PRODUCT
    ):
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro trade identity validation failed."
        )
    taker_side = _required_text(document, "side")
    if taker_side not in {"buy", "sell"}:
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro trade side validation failed."
        )
    return {
        "event": "normalized_mdpro_trade_frame",
        "source_channel": "mdpro_trades",
        "raw_message_ordinal": raw_ordinal,
        "events": [
            {
                "event_index": 0,
                "market": BITVAVO_MDPRO_PRODUCT,
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
    allowed = {
        "bestAsk",
        "bestAskSize",
        "bestBid",
        "bestBidSize",
        "event",
        "lastPrice",
        "market",
    }
    required = {"event", "market"}
    if (
        set(document) - allowed
        or not required <= set(document)
        or document.get("event") != "ticker"
        or document.get("market") != BITVAVO_MDPRO_PRODUCT
    ):
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro ticker identity validation failed."
        )
    bid_price = _optional_decimal(document, "bestBid", allow_zero=False)
    bid_quantity = _optional_decimal(document, "bestBidSize", allow_zero=False)
    ask_price = _optional_decimal(document, "bestAsk", allow_zero=False)
    ask_quantity = _optional_decimal(document, "bestAskSize", allow_zero=False)
    last_price = _optional_decimal(document, "lastPrice", allow_zero=False)
    if (bid_price is None) != (bid_quantity is None) or (ask_price is None) != (
        ask_quantity is None
    ):
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro ticker price/size pair validation failed."
        )
    if bid_price is None and ask_price is None and last_price is None:
        raise BitvavoMdProDataIntegrityError("Bitvavo Market Data Pro ticker update was empty.")
    return {
        "event": "normalized_mdpro_ticker_frame",
        "source_channel": "mdpro_ticker",
        "raw_message_ordinal": raw_ordinal,
        "market": BITVAVO_MDPRO_PRODUCT,
        "bid_price": bid_price,
        "bid_quantity": bid_quantity,
        "ask_price": ask_price,
        "ask_quantity": ask_quantity,
        "last_price": last_price,
    }


def _authentication_acknowledged(document: dict[str, object]) -> bool:
    if "error" in document or "errorCode" in document or document.get("event") == "error":
        return False
    if document.get("event") != "authenticate":
        return False
    if "authenticated" not in document:
        return True
    return document["authenticated"] is True


def _book_subscription_acknowledged(document: dict[str, object]) -> bool:
    return _subscription_acknowledged_channels(
        document,
        expected=frozenset({"book"}),
    ) == frozenset({"book"})


def _subscription_acknowledged_channels(
    document: dict[str, object],
    *,
    expected: frozenset[str],
) -> frozenset[str]:
    if (
        "error" in document
        or "errorCode" in document
        or document.get("event") == "error"
        or set(document) != {"event", "subscriptions"}
        or document.get("event") not in {"book", "subscribed"}
    ):
        return frozenset()
    subscriptions = document.get("subscriptions")
    if type(subscriptions) is not dict or not subscriptions:
        return frozenset()
    acknowledged: set[str] = set()
    for channel, markets in cast(dict[object, object], subscriptions).items():
        if type(channel) is not str or channel not in expected:
            return frozenset()
        if type(markets) is not list or markets != [BITVAVO_MDPRO_PRODUCT]:
            return frozenset()
        acknowledged.add(channel)
    return frozenset(acknowledged)


def _subscription_ack_is_venue_error(document: dict[str, object]) -> bool:
    return "error" in document or "errorCode" in document or document.get("event") == "error"


def _subscription_ack_should_ignore(document: dict[str, object]) -> bool:
    """Ignore market/snapshot frames that can race ahead of the subscribe ack."""

    if _subscription_ack_is_venue_error(document):
        return False
    if set(document) == {"event", "subscriptions"} and document.get("event") in {
        "book",
        "subscribed",
    }:
        return False
    event = document.get("event")
    if type(event) is str and event in _MARKET_CHANNEL_BY_EVENT:
        return True
    if document.get("action") == "getBook" and "response" in document:
        return True
    return False


def _classify_market_document(document: dict[str, object]) -> str:
    if (
        "error" in document
        or "errorCode" in document
        or document.get("event") == "error"
        or "subscriptions" in document
    ):
        return "control"
    if document.get("action") == "getBook" and "response" in document:
        return "mdpro_book_snapshot"
    event = document.get("event")
    if type(event) is str and event in _MARKET_CHANNEL_BY_EVENT and "market" in document:
        return _MARKET_CHANNEL_BY_EVENT[event]
    raise BitvavoMdProDataIntegrityError(
        "Bitvavo Market Data Pro frame had no allowed market identity."
    )


def _contains_sensitive_key(value: object) -> bool:
    if type(value) is dict:
        document = cast(dict[object, object], value)
        for key, child in document.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if normalized in _SENSITIVE_KEY_NAMES or _contains_sensitive_key(child):
                return True
    elif type(value) is list:
        return any(_contains_sensitive_key(item) for item in cast(list[object], value))
    return False


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
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro inbound JSON validation failed."
        ) from None
    if type(loaded) is not dict:
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro inbound JSON root validation failed."
        )
    return cast(dict[str, object], loaded)


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise BitvavoMdProDataIntegrityError("Bitvavo Market Data Pro object validation failed.")
    return cast(dict[str, object], value)


def _required_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not str or not value:
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro text field validation failed."
        )
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
        raise BitvavoMdProDataIntegrityError("Bitvavo Market Data Pro decimal validation failed.")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise BitvavoMdProDataIntegrityError(
            "Bitvavo Market Data Pro decimal validation failed."
        ) from None
    if decimal < 0 or (not allow_zero and decimal == 0):
        raise BitvavoMdProDataIntegrityError("Bitvavo Market Data Pro decimal validation failed.")
    return value


def _unsigned_integer_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not _IntegerLexeme or not value.isascii() or not value.isdigit():
        raise BitvavoMdProDataIntegrityError("Bitvavo Market Data Pro integer validation failed.")
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


def _mdpro_idle_reconnect_reason(error: BaseException) -> str | None:
    if isinstance(error, ConnectionError):
        return _MDPRO_IDLE_RECONNECTS.get(str(error))
    return None


def _is_venue_ping_timeout(fields: Mapping[str, int | str]) -> bool:
    """True for Bitvavo's close reason, not the library's keepalive ping timeout."""

    for key in ("close_reason_rcvd", "close_reason_sent"):
        reason = fields.get(key)
        if type(reason) is str and reason.casefold() == "ping timeout":
            return True
    return False


@asynccontextmanager
async def _websocket_connection(
    config: BitvavoMdProResearchConfig,
) -> AsyncIterator[WebSocketConnection]:
    async with connect(
        BITVAVO_MDPRO_WEBSOCKET_URL,
        open_timeout=10.0,
        close_timeout=5.0,
        ping_interval=BITVAVO_MDPRO_WEBSOCKET_CLIENT_PING_INTERVAL,
        ping_timeout=BITVAVO_MDPRO_WEBSOCKET_CLIENT_PING_TIMEOUT,
        max_queue=BITVAVO_MDPRO_WEBSOCKET_MAX_QUEUE,
        max_size=config.max_application_payload_bytes,
        logger=_TRANSPORT_PRIVACY_LOGGER,
    ) as connection:
        yield cast(WebSocketConnection, connection)


def _connection_factory(config: BitvavoMdProResearchConfig) -> ConnectionFactory:
    return lambda: _websocket_connection(config)


def _require_bounded_duration(duration_seconds: object) -> float:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not math.isfinite(duration) or not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
    return duration


def mdpro_subscription_channels(*, include_ticker: bool = False) -> tuple[str, ...]:
    """Return the Pro socket subscribe set. Trades are required; ticker is flagged."""

    if type(include_ticker) is not bool:
        raise ValueError("include_ticker must be a bool.")
    if include_ticker:
        return (*BITVAVO_MDPRO_REQUIRED_CHANNELS, BITVAVO_MDPRO_OPTIONAL_TICKER_CHANNEL)
    return BITVAVO_MDPRO_REQUIRED_CHANNELS


def mdpro_subscription_payload_text(*, include_ticker: bool = False) -> str:
    """Deterministic Pro subscribe payload for BTC-EUR on the MD Pro socket."""

    return json.dumps(
        {
            "action": "subscribe",
            "channels": [
                {"markets": [BITVAVO_MDPRO_PRODUCT], "name": name}
                for name in mdpro_subscription_channels(include_ticker=include_ticker)
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def data1e_feed_name(*, include_ticker: bool = False) -> str:
    """Fail-closed Pro feed identity. Never a DATA-1D Standard feed string."""

    if include_ticker:
        return "bitvavo-mdpro-btc-eur-book-trades-ticker"
    return "bitvavo-mdpro-btc-eur-book-trades"


def _config_for_duration(
    duration_seconds: float,
    *,
    include_ticker: bool = False,
) -> BitvavoMdProResearchConfig:
    """Smoke keeps the Phase-1 reconnect cap; retained runs retry until duration ends."""

    duration = _require_bounded_duration(duration_seconds)
    if duration <= SMOKE_CAPTURE_SECONDS:
        return BitvavoMdProResearchConfig(include_ticker=include_ticker)
    return BitvavoMdProResearchConfig(
        max_reconnects=RETAINED_MAX_RECONNECTS,
        include_ticker=include_ticker,
    )


def refuse_protected_trade_keys(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed when trade/signing key names are set. Values are never included."""

    source = os.environ if environ is None else environ
    for name in PROTECTED_TRADE_KEY_ENV:
        if source.get(name):
            raise BitvavoMdProAuthenticationError(
                f"Refuse: protected environment name {name} is set. Value not printed."
            )


def load_mdpro_credentials_from_env(
    environ: Mapping[str, str] | None = None,
) -> BitvavoMdProCredentials:
    """Load View-only MD Pro keys from documented env names. Never echo values."""

    source = os.environ if environ is None else environ
    refuse_protected_trade_keys(source)
    api_key = source.get(BITVAVO_MDPRO_API_KEY_ENV)
    api_secret = source.get(BITVAVO_MDPRO_API_SECRET_ENV)
    if api_key is None or api_secret is None:
        raise BitvavoMdProAuthenticationError(
            "Bitvavo Market Data Pro requires BITVAVO_MDPRO_API_KEY and "
            "BITVAVO_MDPRO_API_SECRET (View-only). Values are not printed."
        )
    return BitvavoMdProCredentials(api_key=api_key, api_secret=api_secret)


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
    credentials: BitvavoMdProCredentials,
    stop_event: asyncio.Event | None = None,
    collector_factory: Callable[[RawResearchSink], BitvavoMdProResearchCollector] | None = None,
    include_ticker: bool = False,
) -> dict[str, object]:
    """Run the authenticated capture, close Parquet, and build the DuckDB catalog."""

    duration = _require_bounded_duration(duration_seconds)
    writer = ParquetResearchWriter(output_dir, rotation=ParquetRotation())
    active = (
        collector_factory(writer)
        if collector_factory is not None
        else BitvavoMdProResearchCollector(
            writer,
            credentials,
            config=_config_for_duration(duration, include_ticker=include_ticker),
        )
    )
    try:
        await active.capture_for(duration, stop_event=stop_event)
    finally:
        await writer.aclose()
    create_research_catalog(output_dir, database_path)
    return build_capture_report(database_path, output_dir)


def data1e_capture_claim(
    *,
    run_id: str,
    duration_seconds: float,
    paths: Data1ERunPaths,
    include_ticker: bool = False,
) -> dict[str, object]:
    """Create-only start claim for a reconstructable DATA-1E run."""

    duration = _require_bounded_duration(duration_seconds)
    channels = list(mdpro_subscription_channels(include_ticker=include_ticker))
    return {
        "schema": DATA1E_CLAIM_SCHEMA,
        "state": "STARTED_FAIL_CLOSED",
        "path_contract": DATA1E_PATH_CONTRACT_ID,
        "run_id": run_id,
        "venue": BITVAVO_MDPRO_VENUE,
        "product": DATA1E_PRODUCT,
        "feed": data1e_feed_name(include_ticker=include_ticker),
        "feed_product": BITVAVO_MDPRO_FEED_PRODUCT,
        "channels": channels,
        "book_depth": BITVAVO_MDPRO_BOOK_DEPTH,
        "include_ticker": include_ticker,
        "standard_fallback": False,
        "websocket_url": BITVAVO_MDPRO_WEBSOCKET_URL,
        "credentialless": False,
        "signing": False,
        "authenticated_read_only": True,
        "duration_seconds": duration,
        "smoke_duration_seconds": SMOKE_CAPTURE_SECONDS,
        "max_duration_seconds": MAX_CAPTURE_SECONDS,
        "retained": duration > SMOKE_CAPTURE_SECONDS,
        "twenty_four_seven": False,
        "resume_policy": "never resume or overwrite an existing DATA-1E run directory",
        "raw_dir": paths.raw_dir.as_posix(),
        "database_path": paths.database_path.as_posix(),
    }


def data1e_capture_health(
    *,
    run_id: str,
    duration_seconds: float,
    status: str,
    report: dict[str, object],
    include_ticker: bool = False,
) -> dict[str, object]:
    """Create-only end health for a reconstructable DATA-1E run."""

    if status not in {"COMPLETED", "OPERATOR_STOP", "FAILED"}:
        raise ValueError("DATA-1E capture-health status is outside the documented bound.")
    duration = _require_bounded_duration(duration_seconds)
    channels = list(mdpro_subscription_channels(include_ticker=include_ticker))
    elapsed = elapsed_from_report(report)
    return attach_observability_health(
        {
            "schema": DATA1E_HEALTH_SCHEMA,
            "kind": "capture-health",
            "path_contract": DATA1E_PATH_CONTRACT_ID,
            "run_id": run_id,
            "status": status,
            "duration_seconds": duration,
            "retained": duration > SMOKE_CAPTURE_SECONDS,
            "twenty_four_seven": False,
            "credentialless": False,
            "authenticated_read_only": True,
            "signing": False,
            "feed": data1e_feed_name(include_ticker=include_ticker),
            "feed_product": BITVAVO_MDPRO_FEED_PRODUCT,
            "channels": channels,
            "book_depth": BITVAVO_MDPRO_BOOK_DEPTH,
            "include_ticker": include_ticker,
            "standard_fallback": False,
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
                "Market Data Pro is authenticated View-only L2 plus trades on the same Pro socket, "
                "never L3/MBO.",
                "Ticker is optional and flagged; it is never implied by the default book+trades "
                "subscribe set.",
                "This capture never falls back to DATA-1D Standard.",
                "Keys enter only through BITVAVO_MDPRO_API_KEY and BITVAVO_MDPRO_API_SECRET.",
                "Trade, withdrawal, transfer, or signing key names fail closed.",
                "Client protocol pings use ping_interval=20s with ping_timeout disabled "
                "so a late Pong cannot self-close as 1011. Bitvavo code 1000 "
                "'Ping timeout' reconnects with re-auth, including during the "
                "authenticate window. Credential authenticate rejections stay "
                "fail-closed.",
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
    credentials: BitvavoMdProCredentials,
    stop_event: asyncio.Event | None = None,
    operator_stop: Callable[[], bool] | None = None,
    collector_factory: Callable[[RawResearchSink], BitvavoMdProResearchCollector] | None = None,
    include_ticker: bool = False,
) -> dict[str, object]:
    """Write DATA-1E Parquet/DuckDB to the documented reconstructable path."""

    refuse_protected_trade_keys()
    paths = data1e_run_paths(artifact_root, run_id)
    if paths.run_dir.exists():
        raise FileExistsError(f"DATA-1E refuses to reuse existing run directory: {paths.run_dir}")
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    paths.raw_dir.mkdir(exist_ok=False)
    log_path = capture_log_path(paths.run_dir, run_id)
    configure_capture_logger(log_path)
    capture_logger().info(
        "data1e start run_id=%s requested_duration_seconds=%s log=%s",
        run_id,
        duration_seconds,
        log_path,
    )
    _write_create_only_json(
        paths.capture_claim_path,
        data1e_capture_claim(
            run_id=run_id,
            duration_seconds=duration_seconds,
            paths=paths,
            include_ticker=include_ticker,
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
    terminal_error: BaseException | None = None
    try:
        report = await run_bounded_capture(
            output_dir=paths.raw_dir,
            database_path=paths.database_path,
            duration_seconds=duration_seconds,
            credentials=credentials,
            stop_event=stop_event,
            collector_factory=collector_factory,
            include_ticker=include_ticker,
        )
        if operator_stop is not None and operator_stop():
            status = "OPERATOR_STOP"
        else:
            status = "COMPLETED"
    except BaseException as error:
        terminal_error = error
        raise
    finally:
        elapsed = round(time.monotonic() - started, 6)
        # Fail-closed stops can skip the normal DuckDB rebuild; reconstruct counts
        # from published Parquet so health matches disk (events/reconnects/gaps).
        events_raw = report.get("events", 0)
        events_count = events_raw if isinstance(events_raw, int) else 0
        if events_count == 0 and any(paths.raw_dir.glob("*.parquet")):
            try:
                if not paths.database_path.exists():
                    create_research_catalog(paths.raw_dir, paths.database_path)
                if paths.database_path.exists():
                    rebuilt = build_capture_report(paths.database_path, paths.raw_dir)
                    report = {**rebuilt, "elapsed_seconds": elapsed}
            except Exception:
                report = {**report, "elapsed_seconds": elapsed}
        else:
            report = {**report, "elapsed_seconds": elapsed}
        capture_logger().info(
            "data1e stop run_id=%s status=%s requested_duration_seconds=%s elapsed_seconds=%s "
            "events=%s reconnects=%s gaps=%s parquet_files=%s",
            run_id,
            status,
            duration_seconds,
            report["elapsed_seconds"],
            report.get("events"),
            report.get("reconnects"),
            report.get("gaps"),
            report.get("parquet_files"),
        )
        if not paths.capture_health_path.exists():
            _write_create_only_json(
                paths.capture_health_path,
                data1e_capture_health(
                    run_id=run_id,
                    duration_seconds=duration_seconds,
                    status=status,
                    report=report,
                    include_ticker=include_ticker,
                ),
            )
        if status == "FAILED":
            emit_capture_operator_alert(
                venue=BITVAVO_MDPRO_VENUE,
                run_id=run_id,
                status=status,
                error=terminal_error,
            )
    return {
        **report,
        "run_id": run_id,
        "path_contract": DATA1E_PATH_CONTRACT_ID,
        "run_dir": str(paths.run_dir),
        "raw_dir": str(paths.raw_dir),
        "database_path": str(paths.database_path),
        "status": status,
        "twenty_four_seven": False,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Authenticated Bitvavo Market Data Pro BTC-EUR exact-raw research capture. "
            "Same Pro socket: book depth 1000 plus trades; ticker is optional via "
            "--include-ticker. Duration may exceed the historical 600s smoke cap up to "
            "7 days. Read-only MD Pro keys via BITVAVO_MDPRO_API_KEY and "
            "BITVAVO_MDPRO_API_SECRET. Never falls back to DATA-1D Standard. "
            "This is not a 24/7 service. Do not start a multi-day retain from a Cloud Agent."
        )
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--duration-seconds", required=True, type=float)
    parser.add_argument(
        "--include-ticker",
        action="store_true",
        default=False,
        help="Also subscribe the optional Pro ticker channel on the same MD Pro socket.",
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

    credentials = load_mdpro_credentials_from_env()
    mode = _resolve_cli_mode(args)
    if mode == "reconstructable":
        return await run_reconstructable_capture(
            artifact_root=cast(Path, args.artifact_root),
            run_id=cast(str, args.run_id),
            duration_seconds=cast(float, args.duration_seconds),
            credentials=credentials,
            stop_event=stop_event,
            operator_stop=lambda: operator_stopped,
            include_ticker=bool(args.include_ticker),
        )
    return await run_bounded_capture(
        output_dir=cast(Path, args.output_dir),
        database_path=cast(Path, args.database),
        duration_seconds=cast(float, args.duration_seconds),
        credentials=credentials,
        stop_event=stop_event,
        include_ticker=bool(args.include_ticker),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    report = asyncio.run(_run_from_args(args))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
