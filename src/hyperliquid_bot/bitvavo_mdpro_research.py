"""Bounded authenticated Bitvavo Market Data Pro BTC-EUR L2 capture."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
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

BITVAVO_MDPRO_WEBSOCKET_URL: Final = "wss://ws-mdpro.bitvavo.com/v2/"
BITVAVO_MDPRO_VENUE: Final = "bitvavo"
BITVAVO_MDPRO_PRODUCT: Final = "BTC-EUR"
BITVAVO_MDPRO_FEED_PRODUCT: Final = "market_data_pro"
BITVAVO_MDPRO_SIGNATURE_PATH: Final = "/v2/websocket"
MAX_CAPTURE_SECONDS: Final = 600.0

_BOOK_SUBSCRIPTION_TEXT: Final = (
    '{"action":"subscribe","channels":[{"markets":["BTC-EUR"],"name":"book"}]}'
)
_DECIMAL_TEXT: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
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
    """Authentication or authenticated feed access failed closed."""


class BitvavoMdProDataIntegrityError(BitvavoMdProCaptureError):
    """An inbound identity, schema, snapshot, or sequence rule failed."""

    def __init__(self, message: str, *, quality_event: str = "schema_error") -> None:
        super().__init__(message)
        self.quality_event = quality_event


class BitvavoMdProTransportError(BitvavoMdProCaptureError):
    """A required connection or bounded reconnect failed."""


class BitvavoMdProSinkError(BitvavoMdProCaptureError):
    """The shared raw sink failed and capture stopped without retry."""


class _IntegerLexeme(str):
    """Distinguish a JSON integer token from a quoted string."""


class _DecimalLexeme(str):
    """Distinguish an unquoted JSON decimal from an official decimal string."""


class _ReceiveFailure(Enum):
    """Secret-free result used to detach lower transport exceptions."""

    PAYLOAD_TOO_BIG = "payload_too_big"
    TRANSPORT = "transport"


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
    """Fixed BTC-EUR Pro-book scope with bounded transport controls."""

    authentication_window_ms: int = 10_000
    reconnect_delay_seconds: float = 3.0
    max_application_payload_bytes: int = 8 * 1024 * 1024
    max_buffered_book_updates: int = 10_000
    max_reconnects: int = 1

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
    """Capture authenticated non-conflated BTC-EUR Pro price-level L2."""

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
            except (
                BitvavoMdProAuthenticationError,
                BitvavoMdProDataIntegrityError,
                BitvavoMdProSinkError,
            ):
                raise
            except (WebSocketException, OSError):
                failure = "transport"
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
            if failure == "transport":
                if not connected:
                    await self._connection_failed(session_id, previous_session_id)
                    raise BitvavoMdProTransportError(
                        "Bitvavo Market Data Pro connection failed."
                    ) from None
                await self._disconnected(session_id)
                if reconnects >= self._config.max_reconnects:
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
        await self._require_authentication_ack(connection, session_id, stop_event)
        await self._send_subscription(connection, session_id)
        await self._require_subscription_ack(connection, session_id, stop_event)
        await self._send_snapshot_request(connection, session_id, request_id=1)

        book_state = _BookState(
            max_buffered_updates=self._config.max_buffered_book_updates,
        )
        snapshot_received = False
        while not stop_event.is_set():
            captured = await self._receive_or_stop(connection, stop_event)
            if captured is None:
                break
            result, unpersisted_failure = await self._record_market_inbound(
                captured,
                session_id,
            )
            captured = None
            if unpersisted_failure is not None:
                await self._unpersisted_quality_failure(session_id, unpersisted_failure)
            if result is None:
                continue
            raw_ordinal, channel, document = result
            try:
                normalized_frames: tuple[dict[str, object], ...]
                if channel == "mdpro_book":
                    normalized = book_state.ingest_update(document, raw_ordinal)
                    normalized_frames = () if normalized is None else (normalized,)
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
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "resnapshot_received" if is_reconnect else "snapshot_received",
                        stream=BITVAVO_MDPRO_FEED_PRODUCT,
                        raw_message_ordinal=raw_ordinal,
                        retained_updates=acceptance.retained_updates,
                        reason="fresh_market_data_pro_depth_1000_book_state",
                    )
                else:
                    raise AssertionError("unreachable Market Data Pro channel")
            except BitvavoMdProDataIntegrityError as error:
                await self._quality_failure(session_id, raw_ordinal, error)
                raise AssertionError("unreachable Market Data Pro normalization failure") from None

            for normalized in normalized_frames:
                await self._append_local_payload(
                    session_id,
                    "normalized_mdpro_book",
                    normalized,
                )

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
            captured = await self._receive_or_stop(connection, stop_event)
            if captured is None or self._credentials._contains_sensitive_material(
                captured.payload_bytes
            ):
                failed = True
            else:
                document = _decode_json_object(captured.payload_bytes)
                failed = self._credentials._contains_sensitive_decoded_material(
                    document
                ) or not _authentication_acknowledged(document)
        except asyncio.CancelledError:
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
        failed = False
        try:
            await connection.send(_BOOK_SUBSCRIPTION_TEXT)
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
            subscription_type="book",
            product=BITVAVO_MDPRO_PRODUCT,
            authenticated=True,
        )

    async def _require_subscription_ack(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        failed = False
        captured: CapturedApplicationPayload | None = None
        document: dict[str, object] | None = None
        try:
            captured = await self._receive_or_stop(connection, stop_event)
            if captured is None or self._credentials._contains_sensitive_material(
                captured.payload_bytes
            ):
                failed = True
            else:
                document = _decode_json_object(captured.payload_bytes)
                failed = self._credentials._contains_sensitive_decoded_material(
                    document
                ) or not _book_subscription_acknowledged(document)
        except asyncio.CancelledError:
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
                "subscription_failed",
                stream=BITVAVO_MDPRO_FEED_PRODUCT,
                reason="authenticated_book_subscription_rejected_or_invalid",
            )
            raise BitvavoMdProAuthenticationError(
                "Bitvavo Market Data Pro book subscription failed."
            ) from None
        await self._append_marker(
            session_id,
            "subscription",
            "subscription_acknowledged",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            subscription_type="book",
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
                "depth": 1000,
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
            depth=1000,
            authenticated=True,
        )

    async def _record_market_inbound(
        self,
        captured: CapturedApplicationPayload,
        session_id: str,
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
            if _book_subscription_acknowledged(document):
                await self._append_marker(
                    session_id,
                    "subscription",
                    "subscription_acknowledged",
                    stream=BITVAVO_MDPRO_FEED_PRODUCT,
                    subscription_type="book",
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
        if received is _ReceiveFailure.TRANSPORT:
            raise OSError("Bitvavo Market Data Pro receive failed.") from None
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
        except Exception:
            return _ReceiveFailure.TRANSPORT
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
        except Exception:
            return _ReceiveFailure.TRANSPORT

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
                previous_session_id=previous_session_id,
            )

    async def _disconnected(self, session_id: str) -> None:
        await self._append_marker(
            session_id,
            "session",
            "disconnected",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            reason="transport_error",
        )
        await self._append_marker(
            session_id,
            "data_quality",
            "gap_detected",
            stream=BITVAVO_MDPRO_FEED_PRODUCT,
            reason="transport_disconnect; missed Pro L2 history is not reconstructable",
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


def _authentication_acknowledged(document: dict[str, object]) -> bool:
    if "error" in document or "errorCode" in document or document.get("event") == "error":
        return False
    if document.get("event") != "authenticate":
        return False
    if "authenticated" not in document:
        return True
    return document["authenticated"] is True


def _book_subscription_acknowledged(document: dict[str, object]) -> bool:
    if set(document) != {"event", "subscriptions"} or document.get("event") not in {
        "book",
        "subscribed",
    }:
        return False
    subscriptions = document.get("subscriptions")
    return type(subscriptions) is dict and subscriptions == {"book": [BITVAVO_MDPRO_PRODUCT]}


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
    if document.get("event") == "book" and "market" in document:
        return "mdpro_book"
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


@asynccontextmanager
async def _websocket_connection(
    config: BitvavoMdProResearchConfig,
) -> AsyncIterator[WebSocketConnection]:
    async with connect(
        BITVAVO_MDPRO_WEBSOCKET_URL,
        open_timeout=10.0,
        close_timeout=5.0,
        ping_interval=20.0,
        ping_timeout=10.0,
        max_size=config.max_application_payload_bytes,
        max_queue=1024,
        logger=_TRANSPORT_PRIVACY_LOGGER,
    ) as connection:
        yield cast(WebSocketConnection, connection)


def _connection_factory(config: BitvavoMdProResearchConfig) -> ConnectionFactory:
    return lambda: _websocket_connection(config)


def _require_bounded_duration(duration_seconds: object) -> None:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
