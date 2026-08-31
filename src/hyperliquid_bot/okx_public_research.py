"""Bounded public OKX BTC-USDT-SWAP exact-raw research capture."""

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

OKX_EEA_PUBLIC_WEBSOCKET_URL: Final = "wss://wseea.okx.com:8443/ws/v5/public"
OKX_EEA_BUSINESS_WEBSOCKET_URL: Final = "wss://wseea.okx.com:8443/ws/v5/business"
OKX_RESEARCH_VENUE: Final = "okx"
OKX_RESEARCH_PRODUCT: Final = "BTC-USDT-SWAP"
OKX_RESEARCH_INDEX: Final = "BTC-USDT"
MAX_CAPTURE_SECONDS: Final = 600.0

_PING_TEXT: Final = "ping"
_PONG_BYTES: Final = b"pong"
_DECIMAL_TEXT: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")


@dataclass(frozen=True, slots=True)
class _SubscriptionSpec:
    stream: str
    request_id: str
    channel: str
    instrument_id: str

    @property
    def payload_text(self) -> str:
        return json.dumps(
            {
                "id": self.request_id,
                "op": "subscribe",
                "args": [{"channel": self.channel, "instId": self.instrument_id}],
            },
            separators=(",", ":"),
            sort_keys=True,
        )


_PUBLIC_SUBSCRIPTIONS: Final = (
    _SubscriptionSpec("public", "d1cbbo", "bbo-tbt", OKX_RESEARCH_PRODUCT),
    _SubscriptionSpec("public", "d1cbooks", "books", OKX_RESEARCH_PRODUCT),
    _SubscriptionSpec("public", "d1cfunding", "funding-rate", OKX_RESEARCH_PRODUCT),
    _SubscriptionSpec("public", "d1coi", "open-interest", OKX_RESEARCH_PRODUCT),
    _SubscriptionSpec("public", "d1cmark", "mark-price", OKX_RESEARCH_PRODUCT),
    _SubscriptionSpec("public", "d1cindex", "index-tickers", OKX_RESEARCH_INDEX),
)
_BUSINESS_SUBSCRIPTIONS: Final = (
    _SubscriptionSpec("business", "d1ctrades", "trades-all", OKX_RESEARCH_PRODUCT),
)
_PUBLIC_CHANNELS: Final = frozenset(spec.channel for spec in _PUBLIC_SUBSCRIPTIONS)
_BUSINESS_CHANNELS: Final = frozenset(spec.channel for spec in _BUSINESS_SUBSCRIPTIONS)

_TRANSPORT_PRIVACY_LOGGER: Final = logging.Logger(
    "hyperliquid_bot.okx_public_research_transport",
    level=logging.CRITICAL + 1,
)
_TRANSPORT_PRIVACY_LOGGER.disabled = True
_TRANSPORT_PRIVACY_LOGGER.propagate = False
_TRANSPORT_PRIVACY_LOGGER.addHandler(logging.NullHandler())


class OkxCaptureError(RuntimeError):
    """Bounded public error that never includes a venue payload."""


class OkxDataIntegrityError(OkxCaptureError):
    """An inbound schema, identity, snapshot, or sequence rule failed."""

    def __init__(self, message: str, *, quality_event: str = "schema_error") -> None:
        super().__init__(message)
        self.quality_event = quality_event


class OkxTransportError(OkxCaptureError):
    """A required public connection or reconnect failed."""


class OkxSinkError(OkxCaptureError):
    """The shared raw sink failed and capture stopped without retry."""


class WebSocketConnection(Protocol):
    """Small transport surface implemented by websockets and offline fakes."""

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]
type SessionIdFactory = Callable[[str], str]
type MonotonicClock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class OkxPublicResearchConfig:
    """Fixed BTC-USDT-SWAP scope with bounded public transport controls."""

    heartbeat_idle_seconds: float = 20.0
    pong_timeout_seconds: float = 10.0
    reconnect_delay_seconds: float = 3.0
    max_application_payload_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            type(self.heartbeat_idle_seconds) not in (int, float)
            or not 0 < self.heartbeat_idle_seconds < 30
        ):
            raise ValueError("heartbeat_idle_seconds must be between zero and 30 seconds.")
        if type(self.pong_timeout_seconds) not in (int, float) or self.pong_timeout_seconds <= 0:
            raise ValueError("pong_timeout_seconds must be positive.")
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


class _BooksState:
    """Validate only the officially documented JSON ``books`` sequence rules."""

    def __init__(self) -> None:
        self._has_snapshot = False
        self._last_seq_id: int | None = None

    @property
    def has_snapshot(self) -> bool:
        return self._has_snapshot

    @property
    def last_seq_id(self) -> int | None:
        return self._last_seq_id

    def normalize(self, document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
        try:
            return self._normalize(document, raw_ordinal)
        except OkxDataIntegrityError:
            self._has_snapshot = False
            self._last_seq_id = None
            raise

    def _normalize(self, document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
        action = document.get("action")
        if action not in {"snapshot", "update"}:
            raise OkxDataIntegrityError("OKX books action validation failed.")
        data = _market_data(document, "books", OKX_RESEARCH_PRODUCT)
        if len(data) != 1:
            raise OkxDataIntegrityError("OKX books schema validation failed.")
        item = _object(data[0])
        checksum_wire = _integer_text(item, "checksum")
        if checksum_wire != "0":
            raise OkxDataIntegrityError("OKX deprecated checksum field was not zero.")
        prev_seq_text = _integer_text(item, "prevSeqId")
        seq_text = _integer_text(item, "seqId")
        prev_seq_id = int(prev_seq_text)
        seq_id = int(seq_text)
        if seq_id < 0:
            raise OkxDataIntegrityError("OKX books sequence validation failed.")

        events = _book_events(
            item,
            snapshot=action == "snapshot",
            max_levels=400 if action == "snapshot" else None,
        )
        if action == "snapshot":
            if self._has_snapshot or prev_seq_id != -1:
                raise OkxDataIntegrityError(
                    "OKX books snapshot boundary validation failed.",
                    quality_event="sequence_error",
                )
            sequence_event = "snapshot"
        else:
            if not self._has_snapshot or self._last_seq_id is None:
                raise OkxDataIntegrityError(
                    "OKX books update preceded its snapshot.",
                    quality_event="snapshot_missing",
                )
            if prev_seq_id != self._last_seq_id:
                raise OkxDataIntegrityError(
                    "OKX books prevSeqId chain validation failed.",
                    quality_event="sequence_gap",
                )
            if seq_id > prev_seq_id:
                sequence_event = "update"
            elif seq_id == prev_seq_id and not events:
                sequence_event = "no_update"
            elif seq_id < prev_seq_id:
                sequence_event = "maintenance_reset"
            else:
                raise OkxDataIntegrityError(
                    "OKX books duplicate or out-of-order update failed.",
                    quality_event="sequence_error",
                )

        self._has_snapshot = True
        self._last_seq_id = seq_id
        return {
            "event": "normalized_books_frame",
            "raw_message_ordinal": raw_ordinal,
            "message_type": action,
            "instrument_id": OKX_RESEARCH_PRODUCT,
            "event_time_ms": _timestamp_text(item, "ts"),
            "checksum_wire": checksum_wire,
            "prev_seq_id": prev_seq_text,
            "seq_id": seq_text,
            "sequence_event": sequence_event,
            "events": events,
        }


class OkxPublicResearchCollector:
    """Capture the required EEA public and business feeds without credentials."""

    def __init__(
        self,
        sink: RawResearchSink,
        *,
        config: OkxPublicResearchConfig | None = None,
        public_connection_factory: ConnectionFactory | None = None,
        business_connection_factory: ConnectionFactory | None = None,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        monotonic: MonotonicClock = time.monotonic,
        session_id_factory: SessionIdFactory | None = None,
    ) -> None:
        self._sink = sink
        self._config = config if config is not None else OkxPublicResearchConfig()
        self._public_connection_factory = (
            public_connection_factory
            if public_connection_factory is not None
            else _connection_factory(OKX_EEA_PUBLIC_WEBSOCKET_URL, self._config)
        )
        self._business_connection_factory = (
            business_connection_factory
            if business_connection_factory is not None
            else _connection_factory(OKX_EEA_BUSINESS_WEBSOCKET_URL, self._config)
        )
        self._utc_ns = utc_ns
        self._monotonic_ns = monotonic_ns
        self._monotonic = monotonic
        self._session_id_factory = (
            session_id_factory
            if session_id_factory is not None
            else lambda stream: f"{stream}-{uuid.uuid4().hex}"
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
        """Run both required sockets; any terminal failure stops the full slice."""

        _require_bounded_duration(duration_seconds)
        capture_stop = stop_event if stop_event is not None else asyncio.Event()
        timer = asyncio.create_task(
            self._stop_after(capture_stop, float(duration_seconds)),
            name="okx-public-research-duration",
        )
        public_task = asyncio.create_task(
            self._run_stream("public", _PUBLIC_SUBSCRIPTIONS, capture_stop),
            name="okx-public-research-stream",
        )
        business_task = asyncio.create_task(
            self._run_stream("business", _BUSINESS_SUBSCRIPTIONS, capture_stop),
            name="okx-business-research-stream",
        )
        try:
            done, pending = await asyncio.wait(
                (public_task, business_task),
                return_when=asyncio.FIRST_EXCEPTION,
            )
            terminal = next(
                (task.exception() for task in done if task.exception() is not None),
                None,
            )
            if terminal is not None:
                capture_stop.set()
                await asyncio.gather(*pending, return_exceptions=True)
                raise terminal
            await asyncio.gather(*pending)
        finally:
            capture_stop.set()
            timer.cancel()
            for task in (public_task, business_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(timer, public_task, business_task, return_exceptions=True)

    async def _stop_after(self, stop_event: asyncio.Event, duration_seconds: float) -> None:
        await asyncio.sleep(duration_seconds)
        stop_event.set()

    async def _run_stream(
        self,
        stream: str,
        subscriptions: tuple[_SubscriptionSpec, ...],
        stop_event: asyncio.Event,
    ) -> None:
        previous_session_id: str | None = None
        connection_factory = (
            self._public_connection_factory
            if stream == "public"
            else self._business_connection_factory
        )
        while not stop_event.is_set():
            session_id = self._session_id_factory(stream)
            await self._session_started(session_id, stream, previous_session_id)
            connected = False
            try:
                async with connection_factory() as connection:
                    connected = True
                    await self._connected(session_id, stream, previous_session_id)
                    await self._send_subscriptions(connection, session_id, subscriptions)
                    await self._receive_session(
                        connection,
                        session_id,
                        stream,
                        subscriptions,
                        stop_event,
                        is_reconnect=previous_session_id is not None,
                    )
                    await self._append_marker(
                        session_id,
                        "session",
                        "session_stopped",
                        stream=stream,
                        reason="capture_limit_reached",
                    )
                    return
            except asyncio.CancelledError:
                raise
            except PayloadTooBig:
                await self._terminal_quality(session_id, stream, "truncation_error")
                raise OkxDataIntegrityError(
                    "OKX application payload exceeded the transport bound.",
                    quality_event="truncation_error",
                ) from None
            except OkxSinkError:
                raise
            except OkxDataIntegrityError:
                raise
            except (WebSocketException, OSError):
                if not connected:
                    await self._connection_failed(session_id, stream, previous_session_id)
                    raise OkxTransportError("OKX public connection failed.") from None
                await self._disconnected(session_id, stream)
                previous_session_id = session_id
            except Exception:
                raise OkxTransportError("OKX public transport boundary failed.") from None
            await self._wait_to_reconnect(stop_event)

    async def _send_subscriptions(
        self,
        connection: WebSocketConnection,
        session_id: str,
        subscriptions: tuple[_SubscriptionSpec, ...],
    ) -> None:
        for spec in subscriptions:
            payload_text = spec.payload_text
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
            await self._append_marker(
                session_id,
                "subscription",
                "subscription_sent",
                stream=spec.stream,
                request_id=spec.request_id,
                subscription_type=spec.channel,
                instrument_id=spec.instrument_id,
                authenticated=False,
            )

    async def _receive_session(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stream: str,
        subscriptions: tuple[_SubscriptionSpec, ...],
        stop_event: asyncio.Event,
        *,
        is_reconnect: bool,
    ) -> None:
        expected_by_id = {spec.request_id: spec for spec in subscriptions}
        expected_channels = frozenset(spec.channel for spec in subscriptions)
        acknowledged: set[str] = set()
        subscriptions_active_marked = False
        books_state = _BooksState() if stream == "public" else None
        pong_deadline: float | None = None

        while not stop_event.is_set():
            timeout = (
                float(self._config.heartbeat_idle_seconds)
                if pong_deadline is None
                else max(0.0, pong_deadline - self._monotonic())
            )
            try:
                captured = await self._receive_or_stop(connection, stop_event, timeout)
            except TimeoutError:
                if pong_deadline is not None:
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "heartbeat_timeout",
                        stream=stream,
                        reason="application_pong_deadline_expired; reconnect_required",
                    )
                    raise OSError("OKX application heartbeat timed out.") from None
                await self._send_heartbeat(connection, session_id, stream)
                pong_deadline = self._monotonic() + float(self._config.pong_timeout_seconds)
                continue
            if captured is None:
                break
            if captured.payload_bytes == _PONG_BYTES:
                await self._append_captured(
                    captured,
                    session_id=session_id,
                    channel="heartbeat",
                    direction=MessageDirection.INBOUND,
                )
                pong_deadline = None
                continue

            raw_ordinal, channel, document = await self._record_inbound(
                captured, session_id, stream
            )
            if document.get("event") is not None:
                spec = await self._record_subscription_response(
                    document,
                    session_id,
                    stream,
                    raw_ordinal,
                    expected_by_id,
                )
                acknowledged.add(spec.channel)
                if acknowledged == expected_channels and not subscriptions_active_marked:
                    await self._append_marker(
                        session_id,
                        "subscription",
                        "subscriptions_active",
                        stream=stream,
                        product=OKX_RESEARCH_PRODUCT,
                        authenticated=False,
                    )
                    subscriptions_active_marked = True
                continue
            if channel not in expected_channels or channel not in acknowledged:
                await self._quality_failure(
                    session_id,
                    stream,
                    raw_ordinal,
                    OkxDataIntegrityError("OKX channel was not acknowledged or requested."),
                )
            try:
                normalized = _normalize_market_frame(channel, document, raw_ordinal, books_state)
            except OkxDataIntegrityError as error:
                await self._quality_failure(session_id, stream, raw_ordinal, error)
                raise
            await self._append_local_payload(session_id, f"normalized_{channel}", normalized)

            if channel == "books":
                sequence_event = normalized["sequence_event"]
                if sequence_event == "snapshot":
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "resnapshot_received" if is_reconnect else "snapshot_received",
                        stream=stream,
                        raw_message_ordinal=raw_ordinal,
                        reason="fresh_400_level_organic_book_state",
                    )
                elif sequence_event == "maintenance_reset":
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "sequence_reset",
                        stream=stream,
                        raw_message_ordinal=raw_ordinal,
                        reason="officially_documented_maintenance_reset",
                    )

        if acknowledged != expected_channels:
            await self._append_marker(
                session_id,
                "data_quality",
                "subscription_failed",
                stream=stream,
                reason="capture_stopped_before_required_acknowledgements",
            )
            raise OkxDataIntegrityError("OKX subscriptions were incomplete.")
        if books_state is not None and not books_state.has_snapshot:
            await self._append_marker(
                session_id,
                "data_quality",
                "snapshot_missing",
                stream=stream,
                reason="capture_stopped_before_books_snapshot",
            )
            raise OkxDataIntegrityError(
                "OKX books snapshot was incomplete.",
                quality_event="snapshot_missing",
            )

    async def _receive_or_stop(
        self,
        connection: WebSocketConnection,
        stop_event: asyncio.Event,
        timeout_seconds: float,
    ) -> CapturedApplicationPayload | None:
        receive_task = asyncio.create_task(
            self._receive_captured(connection),
            name="okx-research-receive",
        )
        stop_task = asyncio.create_task(stop_event.wait(), name="okx-research-stop-wait")
        try:
            done, _ = await asyncio.wait(
                (receive_task, stop_task),
                timeout=timeout_seconds,
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
                    raise OSError("OKX WebSocket receive failed.") from None
            if stop_task in done:
                return None
            raise TimeoutError
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

    async def _send_heartbeat(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stream: str,
    ) -> None:
        captured = capture_application_payload(
            _PING_TEXT,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )
        await connection.send(_PING_TEXT)
        await self._append_captured(
            captured,
            session_id=session_id,
            channel="heartbeat",
            direction=MessageDirection.OUTBOUND,
        )
        await self._append_marker(
            session_id,
            "session",
            "heartbeat_sent",
            stream=stream,
        )

    async def _record_inbound(
        self,
        captured: CapturedApplicationPayload,
        session_id: str,
        stream: str,
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
                stream,
                raw_ordinal,
                OkxDataIntegrityError(
                    "OKX complete application payload exceeded its bound.",
                    quality_event="payload_oversize",
                ),
            )
            raise AssertionError("unreachable payload oversize failure")
        try:
            document = _decode_json_object(captured.payload_bytes)
            channel = _classify_document(document)
        except OkxDataIntegrityError as error:
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="unknown",
                direction=MessageDirection.INBOUND,
            )
            await self._quality_failure(session_id, stream, raw_ordinal, error)
            raise
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
        stream: str,
        raw_ordinal: int,
        expected_by_id: dict[str, _SubscriptionSpec],
    ) -> _SubscriptionSpec:
        if document.get("event") == "error":
            await self._append_marker(
                session_id,
                "data_quality",
                "subscription_failed",
                stream=stream,
                reason="venue_rejected_subscription",
                raw_message_ordinal=raw_ordinal,
            )
            raise OkxDataIntegrityError("OKX rejected a public subscription.")
        if document.get("event") != "subscribe":
            error = OkxDataIntegrityError("OKX subscription response schema validation failed.")
            await self._quality_failure(session_id, stream, raw_ordinal, error)
        try:
            request_id = _required_text(document, "id")
            spec = expected_by_id.get(request_id)
            arg = _object(document.get("arg"))
            connection_id = _required_text(document, "connId")
        except OkxDataIntegrityError as schema_error:
            await self._quality_failure(session_id, stream, raw_ordinal, schema_error)
            raise AssertionError("unreachable subscription response failure") from None
        if (
            spec is None
            or arg.get("channel") != spec.channel
            or arg.get("instId") != spec.instrument_id
            or not connection_id
        ):
            error = OkxDataIntegrityError("OKX subscription response identity validation failed.")
            await self._quality_failure(session_id, stream, raw_ordinal, error)
        if spec is None:
            raise AssertionError("unreachable subscription spec")
        await self._append_marker(
            session_id,
            "subscription",
            "subscription_acknowledged",
            stream=stream,
            request_id=spec.request_id,
            subscription_type=spec.channel,
            instrument_id=spec.instrument_id,
            authenticated=False,
            raw_message_ordinal=raw_ordinal,
        )
        return spec

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
                raise OkxSinkError("OKX research sink is unavailable after an append failure.")
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=OKX_RESEARCH_VENUE,
                product=OKX_RESEARCH_PRODUCT,
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
                raise OkxSinkError("OKX research sink append failed.") from None
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
                raise OkxSinkError("OKX research sink is unavailable after an append failure.")
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=OKX_RESEARCH_VENUE,
                product=OKX_RESEARCH_PRODUCT,
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
                raise OkxSinkError("OKX research sink append failed.") from None
            return record.message_ordinal

    async def _session_started(
        self,
        session_id: str,
        stream: str,
        previous_session_id: str | None,
    ) -> None:
        await self._append_marker(
            session_id,
            "session",
            "session_started",
            stream=stream,
            reason="initial_connection" if previous_session_id is None else "reconnect_attempt",
        )

    async def _connected(
        self,
        session_id: str,
        stream: str,
        previous_session_id: str | None,
    ) -> None:
        await self._append_marker(session_id, "session", "connected", stream=stream)
        if previous_session_id is not None:
            await self._append_marker(
                session_id,
                "session",
                "reconnected",
                stream=stream,
                previous_session_id=previous_session_id,
            )

    async def _disconnected(self, session_id: str, stream: str) -> None:
        await self._append_marker(
            session_id,
            "session",
            "disconnected",
            stream=stream,
            reason="transport_error",
        )
        await self._append_marker(
            session_id,
            "data_quality",
            "gap_detected",
            stream=stream,
            reason="transport_disconnect; missed stream history is not reconstructable",
        )

    async def _connection_failed(
        self,
        session_id: str,
        stream: str,
        previous_session_id: str | None,
    ) -> None:
        await self._append_marker(
            session_id,
            "session",
            "reconnect_failed" if previous_session_id is not None else "connection_failed",
            stream=stream,
            reason="transport_error",
        )

    async def _terminal_quality(self, session_id: str, stream: str, event: str) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            event,
            stream=stream,
            reason="capture_stopped_fail_closed",
        )

    async def _quality_failure(
        self,
        session_id: str,
        stream: str,
        raw_ordinal: int,
        error: OkxDataIntegrityError,
    ) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            error.quality_event,
            stream=stream,
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


def _normalize_market_frame(
    channel: str,
    document: dict[str, object],
    raw_ordinal: int,
    books_state: _BooksState | None,
) -> dict[str, object]:
    if channel == "trades-all":
        return _normalize_trade(document, raw_ordinal)
    if channel == "bbo-tbt":
        return _normalize_bbo(document, raw_ordinal)
    if channel == "books":
        if books_state is None:
            raise OkxDataIntegrityError("OKX books arrived on the wrong stream.")
        return books_state.normalize(document, raw_ordinal)
    if channel in {"funding-rate", "open-interest", "mark-price", "index-tickers"}:
        return _normalize_derivative_context(channel, document, raw_ordinal)
    raise OkxDataIntegrityError("OKX market channel validation failed.")


def _normalize_trade(document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
    data = _market_data(document, "trades-all", OKX_RESEARCH_PRODUCT)
    if len(data) != 1:
        raise OkxDataIntegrityError("OKX trades-all must contain exactly one trade.")
    item = _object(data[0])
    if _required_text(item, "instId") != OKX_RESEARCH_PRODUCT:
        raise OkxDataIntegrityError("OKX trade instrument validation failed.")
    side = _required_text(item, "side")
    source = _required_text(item, "source")
    if side not in {"buy", "sell"} or source not in {"0", "1"}:
        raise OkxDataIntegrityError("OKX trade enum validation failed.")
    event = {
        "event_index": 0,
        "instrument_id": OKX_RESEARCH_PRODUCT,
        "trade_id": _unsigned_integer_text(item, "tradeId"),
        "price": _decimal(item, "px", allow_zero=False),
        "quantity": _decimal(item, "sz", allow_zero=False),
        "side": side,
        "source": source,
        "event_time_ms": _timestamp_text(item, "ts"),
    }
    return {
        "event": "normalized_trade_frame",
        "raw_message_ordinal": raw_ordinal,
        "message_type": "update",
        "events": [event],
    }


def _normalize_bbo(document: dict[str, object], raw_ordinal: int) -> dict[str, object]:
    if "action" in document:
        raise OkxDataIntegrityError("OKX bbo-tbt unexpectedly carried an action field.")
    data = _market_data(document, "bbo-tbt", OKX_RESEARCH_PRODUCT)
    if len(data) != 1:
        raise OkxDataIntegrityError("OKX bbo-tbt schema validation failed.")
    item = _object(data[0])
    events = _book_events(item, snapshot=True, max_levels=1)
    if not events or "prevSeqId" in item or "checksum" in item:
        raise OkxDataIntegrityError("OKX bbo-tbt schema validation failed.")
    return {
        "event": "normalized_bbo_frame",
        "raw_message_ordinal": raw_ordinal,
        "instrument_id": OKX_RESEARCH_PRODUCT,
        "event_time_ms": _timestamp_text(item, "ts"),
        "seq_id": _unsigned_integer_text(item, "seqId"),
        "events": events,
    }


def _normalize_derivative_context(
    channel: str,
    document: dict[str, object],
    raw_ordinal: int,
) -> dict[str, object]:
    instrument_id = OKX_RESEARCH_INDEX if channel == "index-tickers" else OKX_RESEARCH_PRODUCT
    data = _market_data(document, channel, instrument_id)
    if len(data) != 1:
        raise OkxDataIntegrityError("OKX derivative context schema validation failed.")
    item = _object(data[0])
    if _required_text(item, "instId") != instrument_id:
        raise OkxDataIntegrityError("OKX derivative context instrument validation failed.")
    normalized: dict[str, object] = {
        "event": "normalized_derivative_context_frame",
        "raw_message_ordinal": raw_ordinal,
        "source_channel": channel,
        "instrument_id": instrument_id,
    }
    if channel == "open-interest":
        if _required_text(item, "instType") != "SWAP":
            raise OkxDataIntegrityError("OKX open-interest instrument type validation failed.")
        normalized.update(
            {
                "context_type": "open_interest",
                "open_interest_contracts": _decimal(item, "oi", allow_zero=True),
                "open_interest_currency": _decimal(item, "oiCcy", allow_zero=True),
                "open_interest_usd": _decimal(item, "oiUsd", allow_zero=True),
                "event_time_ms": _timestamp_text(item, "ts"),
            }
        )
    elif channel == "mark-price":
        if _required_text(item, "instType") != "SWAP":
            raise OkxDataIntegrityError("OKX mark-price instrument type validation failed.")
        normalized.update(
            {
                "context_type": "mark_price",
                "mark_price": _decimal(item, "markPx", allow_zero=False),
                "event_time_ms": _timestamp_text(item, "ts"),
            }
        )
    elif channel == "index-tickers":
        normalized.update(
            {
                "context_type": "index_price",
                "index_price": _decimal(item, "idxPx", allow_zero=False),
                "open_24h": _decimal(item, "open24h", allow_zero=False),
                "high_24h": _decimal(item, "high24h", allow_zero=False),
                "low_24h": _decimal(item, "low24h", allow_zero=False),
                "sod_utc0": _decimal(item, "sodUtc0", allow_zero=False),
                "sod_utc8": _decimal(item, "sodUtc8", allow_zero=False),
                "event_time_ms": _timestamp_text(item, "ts"),
            }
        )
    else:
        if _required_text(item, "instType") != "SWAP":
            raise OkxDataIntegrityError("OKX funding-rate instrument type validation failed.")
        method = _required_text(item, "method")
        formula_type = _required_text(item, "formulaType")
        settlement_state = _required_text(item, "settState")
        if (
            method != "current_period"
            or formula_type not in {"noRate", "withRate"}
            or settlement_state not in {"processing", "settled"}
        ):
            raise OkxDataIntegrityError("OKX funding-rate enum validation failed.")
        normalized.update(
            {
                "context_type": "funding_rate",
                "method": method,
                "formula_type": formula_type,
                "funding_rate": _decimal(item, "fundingRate", allow_zero=True, allow_negative=True),
                "next_funding_rate": _optional_decimal(
                    item, "nextFundingRate", allow_negative=True
                ),
                "funding_time_ms": _timestamp_text(item, "fundingTime"),
                "next_funding_time_ms": _timestamp_text(item, "nextFundingTime"),
                "min_funding_rate": _decimal(
                    item, "minFundingRate", allow_zero=True, allow_negative=True
                ),
                "max_funding_rate": _decimal(
                    item, "maxFundingRate", allow_zero=True, allow_negative=True
                ),
                "interest_rate": _optional_decimal(item, "interestRate", allow_negative=True),
                "impact_value": _optional_decimal(item, "impactValue"),
                "settlement_state": settlement_state,
                "settlement_funding_rate": _optional_decimal(
                    item, "settFundingRate", allow_negative=True
                ),
                "premium": _decimal(item, "premium", allow_zero=True, allow_negative=True),
                "event_time_ms": _timestamp_text(item, "ts"),
            }
        )
    return normalized


def _market_data(
    document: dict[str, object],
    expected_channel: str,
    expected_instrument: str,
) -> list[object]:
    arg = _object(document.get("arg"))
    data = document.get("data")
    if (
        arg.get("channel") != expected_channel
        or arg.get("instId") != expected_instrument
        or type(data) is not list
        or not data
    ):
        raise OkxDataIntegrityError("OKX market frame identity validation failed.")
    return cast(list[object], data)


def _book_events(
    item: dict[str, object],
    *,
    snapshot: bool,
    max_levels: int | None,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    saw_sides: set[str] = set()
    wire_order = 0
    for key, value in item.items():
        if key not in {"asks", "bids"}:
            continue
        if type(value) is not list or (max_levels is not None and len(value) > max_levels):
            raise OkxDataIntegrityError("OKX order-book side schema validation failed.")
        saw_sides.add(key)
        side = "ask" if key == "asks" else "bid"
        for side_index, raw_level in enumerate(value):
            if type(raw_level) is not list or len(raw_level) != 4:
                raise OkxDataIntegrityError("OKX order-book level schema validation failed.")
            level = cast(list[object], raw_level)
            if level[2] != "0":
                raise OkxDataIntegrityError("OKX deprecated level field was not zero.")
            price = _decimal_value(level[0], allow_zero=False)
            quantity = _decimal_value(level[1], allow_zero=not snapshot)
            order_count = _unsigned_integer_value(level[3])
            if snapshot and Decimal(quantity) == 0:
                raise OkxDataIntegrityError("OKX snapshot contained an empty order-book level.")
            events.append(
                {
                    "wire_order": wire_order,
                    "side": side,
                    "side_index": side_index,
                    "price": price,
                    "quantity": quantity,
                    "order_count": order_count,
                    "action": "snapshot"
                    if snapshot
                    else ("delete" if Decimal(quantity) == 0 else "update"),
                }
            )
            wire_order += 1
    if saw_sides != {"asks", "bids"}:
        raise OkxDataIntegrityError("OKX order-book sides were incomplete.")
    return events


def _decode_json_object(payload_bytes: bytes) -> dict[str, object]:
    try:
        loaded = json.loads(
            payload_bytes,
            parse_float=str,
            parse_int=str,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise OkxDataIntegrityError("OKX inbound JSON validation failed.") from None
    if type(loaded) is not dict:
        raise OkxDataIntegrityError("OKX inbound JSON root validation failed.")
    return cast(dict[str, object], loaded)


def _classify_document(document: dict[str, object]) -> str:
    if document.get("event") is not None:
        return "subscription"
    arg = document.get("arg")
    if type(arg) is not dict:
        raise OkxDataIntegrityError("OKX inbound frame had no channel identity.")
    channel = arg.get("channel")
    if type(channel) is not str or not channel:
        raise OkxDataIntegrityError("OKX inbound frame had no channel identity.")
    return channel


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise OkxDataIntegrityError("OKX object schema validation failed.")
    return cast(dict[str, object], value)


def _required_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not str or not value:
        raise OkxDataIntegrityError("OKX text field schema validation failed.")
    return value


def _decimal(
    document: dict[str, object],
    field: str,
    *,
    allow_zero: bool,
    allow_negative: bool = False,
) -> str:
    return _decimal_value(
        document.get(field),
        allow_zero=allow_zero,
        allow_negative=allow_negative,
    )


def _optional_decimal(
    document: dict[str, object],
    field: str,
    *,
    allow_negative: bool = False,
) -> str:
    value = document.get(field)
    if value == "":
        return ""
    return _decimal_value(value, allow_zero=True, allow_negative=allow_negative)


def _decimal_value(
    value: object,
    *,
    allow_zero: bool,
    allow_negative: bool = False,
) -> str:
    if type(value) is not str or _DECIMAL_TEXT.fullmatch(value) is None:
        raise OkxDataIntegrityError("OKX decimal field schema validation failed.")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise OkxDataIntegrityError("OKX decimal field schema validation failed.") from None
    if decimal < 0 and not allow_negative:
        raise OkxDataIntegrityError("OKX decimal field schema validation failed.")
    if not allow_zero and decimal == 0:
        raise OkxDataIntegrityError("OKX decimal field schema validation failed.")
    return value


def _integer_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not str or not value.isascii():
        raise OkxDataIntegrityError("OKX integer field schema validation failed.")
    digits = value[1:] if value.startswith("-") else value
    if not digits or not digits.isdigit():
        raise OkxDataIntegrityError("OKX integer field schema validation failed.")
    return value


def _unsigned_integer_text(document: dict[str, object], field: str) -> str:
    return _unsigned_integer_value(document.get(field))


def _unsigned_integer_value(value: object) -> str:
    if type(value) is not str or not value.isascii() or not value.isdigit():
        raise OkxDataIntegrityError("OKX unsigned integer field schema validation failed.")
    return value


def _timestamp_text(document: dict[str, object], field: str) -> str:
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
    url: str,
    config: OkxPublicResearchConfig,
) -> AsyncIterator[WebSocketConnection]:
    async with connect(
        url,
        open_timeout=10.0,
        close_timeout=5.0,
        ping_interval=None,
        max_size=config.max_application_payload_bytes,
        max_queue=1024,
        logger=_TRANSPORT_PRIVACY_LOGGER,
    ) as connection:
        yield cast(WebSocketConnection, connection)


def _connection_factory(
    url: str,
    config: OkxPublicResearchConfig,
) -> ConnectionFactory:
    return lambda: _websocket_connection(url, config)


def _require_bounded_duration(duration_seconds: object) -> None:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
