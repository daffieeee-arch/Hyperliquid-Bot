"""Bounded public Bitvavo Standard BTC-EUR exact-raw research capture."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import PayloadTooBig, WebSocketException

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

BITVAVO_STANDARD_WEBSOCKET_URL: Final = "wss://ws.bitvavo.com/v2/"
BITVAVO_RESEARCH_VENUE: Final = "bitvavo"
BITVAVO_RESEARCH_PRODUCT: Final = "BTC-EUR"
BITVAVO_FEED_PRODUCT: Final = "standard"
MAX_CAPTURE_SECONDS: Final = 600.0

_DECIMAL_TEXT: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_SUBSCRIPTION_CHANNELS: Final = ("trades", "ticker", "book")
_SUBSCRIPTION_PAYLOAD_TEXT: Final = json.dumps(
    {
        "action": "subscribe",
        "channels": [
            {"name": channel, "markets": [BITVAVO_RESEARCH_PRODUCT]}
            for channel in _SUBSCRIPTION_CHANNELS
        ],
    },
    separators=(",", ":"),
    sort_keys=True,
)

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
        captured = capture_application_payload(
            _SUBSCRIPTION_PAYLOAD_TEXT,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )
        await connection.send(_SUBSCRIPTION_PAYLOAD_TEXT)
        await self._append_captured(
            captured,
            session_id=session_id,
            channel="subscription",
            direction=MessageDirection.OUTBOUND,
        )
        for channel in _SUBSCRIPTION_CHANNELS:
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
        expected_channels = frozenset(_SUBSCRIPTION_CHANNELS)
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
            if channel in {"trades", "ticker", "book"} and channel not in acknowledged:
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
            acknowledged == set(_SUBSCRIPTION_CHANNELS)
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
            acknowledged = _subscription_channels(document)
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


def _subscription_channels(document: dict[str, object]) -> frozenset[str]:
    event = document.get("event")
    subscriptions = _object(document.get("subscriptions"))
    if event not in {"subscribed", "book"}:
        raise BitvavoDataIntegrityError("Bitvavo subscription response schema validation failed.")
    if not subscriptions or set(subscriptions) - set(_SUBSCRIPTION_CHANNELS):
        raise BitvavoDataIntegrityError("Bitvavo subscription response scope validation failed.")
    acknowledged: set[str] = set()
    for channel, markets in subscriptions.items():
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
    channel_by_event = {"trade": "trades", "ticker": "ticker", "book": "book"}
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


def _require_bounded_duration(duration_seconds: object) -> None:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
