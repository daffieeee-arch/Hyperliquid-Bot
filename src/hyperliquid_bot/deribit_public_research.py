"""Bounded, transport-injected public Deribit BTC derivatives research capture."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final, Protocol, cast

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

DERIBIT_PUBLIC_WEBSOCKET_URL: Final = "wss://www.deribit.com/ws/api/v2"
DERIBIT_RESEARCH_VENUE: Final = "deribit"
DERIBIT_RESEARCH_PRODUCT: Final = "BTC-DERIVATIVES"
DERIBIT_PERPETUAL: Final = "BTC-PERPETUAL"
DERIBIT_INDEX_NAME: Final = "btc_usd"
MAX_CAPTURE_SECONDS: Final = 600.0

_REQUEST_ID: Final = "data-1h-public-subscribe"
_DECIMAL_TEXT: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_FUTURE_NAME: Final = re.compile(r"BTC-([0-9]{1,2}[A-Z]{3}[0-9]{2})\Z")
_OPTION_NAME: Final = re.compile(
    r"BTC-([0-9]{1,2}[A-Z]{3}[0-9]{2})-([1-9][0-9]*(?:\.[0-9]+)?)-([CP])\Z"
)


class _JsonIntegerLexeme(str):
    """A JSON number token parsed without losing its integer spelling."""


class _JsonFloatLexeme(str):
    """A JSON number token parsed without binary floating-point conversion."""


class DeribitCaptureError(RuntimeError):
    """Bounded error that never includes a venue payload or transport detail."""


class DeribitDataIntegrityError(DeribitCaptureError):
    """An inbound schema, identity, subscription, snapshot, or sequence rule failed."""

    def __init__(self, message: str, *, quality_event: str = "schema_error") -> None:
        super().__init__(message)
        self.quality_event = quality_event


class DeribitTransportError(DeribitCaptureError):
    """The injected public transport failed or exceeded its reconnect boundary."""


class DeribitTransportTruncation(DeribitCaptureError):
    """Signal from an injected transport that a WebSocket message was truncated."""


class DeribitSinkError(DeribitCaptureError):
    """The shared raw sink failed and remains unusable for this collector."""


class _CaptureStopBoundary:
    """Record the first bounded stop cause without mutating a caller-owned event."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.reason: str | None = None

    def request(self, reason: str) -> None:
        if not self.event.is_set():
            self.reason = reason
            self.event.set()


class WebSocketConnection(Protocol):
    """Minimal public transport surface supplied by a caller or offline fake."""

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]
type SessionIdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class DeribitInstrumentSelection:
    """Deterministic bounded selection derived from a caller's discovery response."""

    dated_future: str
    option_instruments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _OptionIdentity:
    expiry_code: str
    strike: str
    option_type: str


@dataclass(frozen=True, slots=True)
class DeribitPublicResearchConfig:
    """Fixed public scope with one dated future and a small paired option sample."""

    dated_future: str
    option_instruments: tuple[str, ...]
    reconnect_delay_seconds: float = 1.0
    send_timeout_seconds: float = 5.0
    max_application_payload_bytes: int = 8 * 1024 * 1024
    max_reconnects: int = 1

    def __post_init__(self) -> None:
        if type(self.dated_future) is not str or _FUTURE_NAME.fullmatch(self.dated_future) is None:
            raise ValueError("dated_future must be one active-style BTC dated-future name.")
        if type(self.option_instruments) is not tuple:
            raise TypeError("option_instruments must be a tuple.")
        _validate_option_sample(self.option_instruments)
        if (
            type(self.reconnect_delay_seconds) not in (int, float)
            or not math.isfinite(float(self.reconnect_delay_seconds))
            or not 0 <= self.reconnect_delay_seconds <= 30
        ):
            raise ValueError("reconnect_delay_seconds must be finite and between zero and 30.")
        if (
            type(self.send_timeout_seconds) not in (int, float)
            or not math.isfinite(float(self.send_timeout_seconds))
            or not 0 < self.send_timeout_seconds <= 30
        ):
            raise ValueError("send_timeout_seconds must be finite and between zero and 30.")
        if (
            type(self.max_application_payload_bytes) is not int
            or self.max_application_payload_bytes <= 0
        ):
            raise ValueError("max_application_payload_bytes must be a positive integer.")
        if type(self.max_reconnects) is not int or not 0 <= self.max_reconnects <= 1:
            raise ValueError("max_reconnects must be zero or one.")

    @property
    def channels(self) -> tuple[str, ...]:
        """Exact public 100 ms subscription scope, ordered deterministically."""

        base = (
            f"book.{DERIBIT_PERPETUAL}.100ms",
            f"trades.{DERIBIT_PERPETUAL}.100ms",
            f"ticker.{DERIBIT_PERPETUAL}.100ms",
            f"ticker.{self.dated_future}.100ms",
            f"deribit_price_index.{DERIBIT_INDEX_NAME}",
            f"deribit_volatility_index.{DERIBIT_INDEX_NAME}",
        )
        return (*base, *(f"ticker.{name}.100ms" for name in self.option_instruments))


class _BookState:
    """Validate Deribit's documented snapshot/change linkage without inventing gaps."""

    def __init__(self) -> None:
        self._has_snapshot = False
        self._last_change_id: int | None = None

    @property
    def has_snapshot(self) -> bool:
        return self._has_snapshot

    @property
    def last_change_id(self) -> int | None:
        return self._last_change_id

    def normalize(
        self,
        data: dict[str, object],
        *,
        source_channel: str,
        raw_ordinal: int,
    ) -> dict[str, object]:
        try:
            return self._normalize(data, source_channel=source_channel, raw_ordinal=raw_ordinal)
        except DeribitDataIntegrityError:
            self._has_snapshot = False
            self._last_change_id = None
            raise

    def _normalize(
        self,
        data: dict[str, object],
        *,
        source_channel: str,
        raw_ordinal: int,
    ) -> dict[str, object]:
        if _required_text(data, "instrument_name") != DERIBIT_PERPETUAL:
            raise DeribitDataIntegrityError("Deribit book instrument identity failed.")
        message_type = _required_text(data, "type")
        if message_type not in {"snapshot", "change"}:
            raise DeribitDataIntegrityError("Deribit book message type failed.")
        change_id_text = _unsigned_integer(data, "change_id")
        change_id = int(change_id_text)
        timestamp = _unsigned_integer(data, "timestamp")
        events = _book_events(data, snapshot=message_type == "snapshot")

        if message_type == "snapshot":
            if self._has_snapshot or "prev_change_id" in data:
                raise DeribitDataIntegrityError(
                    "Deribit book snapshot boundary failed.",
                    quality_event="sequence_error",
                )
            previous: str | None = None
        else:
            if not self._has_snapshot or self._last_change_id is None:
                raise DeribitDataIntegrityError(
                    "Deribit book change preceded its snapshot.",
                    quality_event="snapshot_missing",
                )
            previous = _unsigned_integer(data, "prev_change_id")
            if int(previous) != self._last_change_id:
                raise DeribitDataIntegrityError(
                    "Deribit book prev_change_id linkage failed.",
                    quality_event="sequence_gap",
                )
            if change_id <= self._last_change_id:
                raise DeribitDataIntegrityError(
                    "Deribit book duplicate or out-of-order change failed.",
                    quality_event="sequence_error",
                )

        self._has_snapshot = True
        self._last_change_id = change_id
        return {
            "event": "normalized_book_frame",
            "source_channel": source_channel,
            "raw_message_ordinal": raw_ordinal,
            "message_type": message_type,
            "instrument_name": DERIBIT_PERPETUAL,
            "event_time_ms": timestamp,
            "change_id": change_id_text,
            "prev_change_id": previous,
            "events": events,
        }


class _TradeState:
    """Validate the documented per-instrument trade sequence within one session."""

    def __init__(self) -> None:
        self._last_trade_seq: int | None = None

    def normalize(
        self,
        value: object,
        *,
        source_channel: str,
        raw_ordinal: int,
    ) -> dict[str, object]:
        try:
            normalized = _normalize_trades(
                value,
                source_channel=source_channel,
                raw_ordinal=raw_ordinal,
            )
            next_sequence = self._last_trade_seq
            for event in cast(list[dict[str, object]], normalized["events"]):
                sequence = int(cast(str, event["trade_seq"]))
                if next_sequence is not None and sequence != next_sequence + 1:
                    quality_event = (
                        "trade_sequence_gap"
                        if sequence > next_sequence + 1
                        else "trade_sequence_error"
                    )
                    raise DeribitDataIntegrityError(
                        "Deribit trade sequence continuity failed.",
                        quality_event=quality_event,
                    )
                next_sequence = sequence
            self._last_trade_seq = next_sequence
            return normalized
        except DeribitDataIntegrityError:
            self._last_trade_seq = None
            raise


class DeribitPublicResearchCollector:
    """Capture the bounded public corpus through a mandatory injected transport."""

    def __init__(
        self,
        sink: RawResearchSink,
        *,
        config: DeribitPublicResearchConfig,
        connection_factory: ConnectionFactory,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        session_id_factory: SessionIdFactory | None = None,
    ) -> None:
        if connection_factory is None:
            raise TypeError("connection_factory must be explicitly injected.")
        self._sink = sink
        self._config = config
        self._connection_factory = connection_factory
        self._utc_ns = utc_ns
        self._monotonic_ns = monotonic_ns
        self._session_id_factory = (
            session_id_factory
            if session_id_factory is not None
            else lambda: f"public-{uuid.uuid4().hex}"
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
        """Run one public socket with at most one bounded reconnect."""

        _require_bounded_duration(duration_seconds)
        if stop_event is not None and stop_event.is_set():
            raise DeribitDataIntegrityError("Deribit capture stop was already requested.")
        boundary = _CaptureStopBoundary()
        timer = asyncio.create_task(
            self._stop_after(boundary, float(duration_seconds)),
            name="deribit-public-research-duration",
        )
        external_watcher = (
            asyncio.create_task(
                self._watch_external_stop(boundary, stop_event),
                name="deribit-public-research-external-stop",
            )
            if stop_event is not None
            else None
        )
        try:
            try:
                async with asyncio.timeout(float(duration_seconds) + 0.1):
                    await self._run(boundary)
            except TimeoutError:
                boundary.request("capture_limit_reached")
                raise DeribitTransportError(
                    "Deribit capture exceeded its hard duration boundary."
                ) from None
        finally:
            boundary.request("capture_cancelled")
            tasks = [timer]
            if external_watcher is not None:
                tasks.append(external_watcher)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _stop_after(
        self,
        boundary: _CaptureStopBoundary,
        duration_seconds: float,
    ) -> None:
        await asyncio.sleep(duration_seconds)
        boundary.request("capture_limit_reached")

    async def _watch_external_stop(
        self,
        boundary: _CaptureStopBoundary,
        stop_event: asyncio.Event,
    ) -> None:
        await stop_event.wait()
        boundary.request("external_stop_requested")

    async def _run(self, boundary: _CaptureStopBoundary) -> None:
        previous_session_id: str | None = None
        reconnects = 0
        while not boundary.event.is_set():
            session_id = self._session_id_factory()
            await self._append_marker(
                session_id,
                "session",
                "session_started",
                reason="initial_connection" if previous_session_id is None else "reconnect_attempt",
            )
            connected = False
            try:
                async with self._connection_factory() as connection:
                    connected = True
                    await self._append_marker(session_id, "session", "connected")
                    if previous_session_id is not None:
                        await self._append_marker(
                            session_id,
                            "session",
                            "reconnected",
                            previous_session_id=previous_session_id,
                        )
                    await self._send_subscription(connection, session_id)
                    await self._receive_session(
                        connection,
                        session_id,
                        boundary.event,
                        is_reconnect=previous_session_id is not None,
                    )
                    await self._append_marker(
                        session_id,
                        "session",
                        "session_stopped",
                        reason=boundary.reason or "capture_stopped",
                    )
                    return
            except asyncio.CancelledError:
                raise
            except DeribitTransportTruncation:
                await self._terminal_quality(session_id, "truncation_error")
                raise DeribitDataIntegrityError(
                    "Deribit transport reported a truncated application payload.",
                    quality_event="truncation_error",
                ) from None
            except (DeribitSinkError, DeribitDataIntegrityError):
                raise
            except OSError:
                if not connected:
                    await self._append_marker(
                        session_id,
                        "session",
                        "reconnect_failed" if previous_session_id else "connection_failed",
                        reason="transport_error",
                    )
                else:
                    await self._append_marker(
                        session_id,
                        "session",
                        "disconnected",
                        reason="transport_error",
                    )
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "gap_detected",
                        reason="transport_disconnect; missed stream history is not reconstructable",
                    )
                if reconnects >= self._config.max_reconnects:
                    raise DeribitTransportError(
                        "Deribit public reconnect boundary exhausted."
                    ) from None
                reconnects += 1
                previous_session_id = session_id
            except Exception:
                raise DeribitTransportError("Deribit public transport boundary failed.") from None
            await self._wait_to_reconnect(boundary.event)
            if boundary.event.is_set():
                await self._append_marker(
                    session_id,
                    "data_quality",
                    "reconnect_incomplete",
                    reason="capture_stopped_before_a_fresh_reconnect_snapshot",
                )
                raise DeribitTransportError("Deribit reconnect did not establish fresh state.")

    async def _send_subscription(
        self,
        connection: WebSocketConnection,
        session_id: str,
    ) -> None:
        payload_text = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": _REQUEST_ID,
                "method": "public/subscribe",
                "params": {"channels": list(self._config.channels)},
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        captured = capture_application_payload(
            payload_text,
            utc_ns=self._utc_ns,
            monotonic_ns=self._monotonic_ns,
        )
        try:
            await asyncio.wait_for(
                connection.send(payload_text),
                timeout=float(self._config.send_timeout_seconds),
            )
        except TimeoutError:
            raise OSError("Deribit public subscribe send timed out.") from None
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
            authenticated=False,
            channels=list(self._config.channels),
        )

    async def _receive_session(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
        *,
        is_reconnect: bool,
    ) -> None:
        acknowledged = False
        book_state = _BookState()
        trade_state = _TradeState()
        observed_channels: set[str] = set()
        book_change_observed = False
        while not stop_event.is_set():
            try:
                captured = await self._receive_or_stop(connection, stop_event)
            except DeribitDataIntegrityError as error:
                await self._terminal_quality(session_id, error.quality_event)
                raise
            if captured is None:
                break
            raw_ordinal, channel, document = await self._record_inbound(captured, session_id)
            if channel == "subscription":
                if acknowledged:
                    await self._quality_failure(
                        session_id,
                        raw_ordinal,
                        DeribitDataIntegrityError(
                            "Deribit duplicate subscription response failed."
                        ),
                    )
                await self._validate_ack(document, session_id, raw_ordinal)
                acknowledged = True
                continue
            if not acknowledged or channel not in self._config.channels:
                await self._quality_failure(
                    session_id,
                    raw_ordinal,
                    DeribitDataIntegrityError(
                        "Deribit data preceded its exact batch subscription acknowledgement."
                    ),
                )
            try:
                normalized_channel, normalized = _normalize_notification(
                    document,
                    source_channel=channel,
                    raw_ordinal=raw_ordinal,
                    config=self._config,
                    book_state=book_state,
                    trade_state=trade_state,
                )
            except DeribitDataIntegrityError as error:
                await self._quality_failure(session_id, raw_ordinal, error)
                raise AssertionError("unreachable normalization failure") from None
            await self._append_local_payload(session_id, normalized_channel, normalized)
            observed_channels.add(channel)
            if channel == f"book.{DERIBIT_PERPETUAL}.100ms":
                message_type = normalized["message_type"]
                if message_type == "snapshot":
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "resnapshot_received" if is_reconnect else "snapshot_received",
                        raw_message_ordinal=raw_ordinal,
                        reason="fresh_public_100ms_book_state",
                    )
                else:
                    book_change_observed = True
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "book_change_validated",
                        raw_message_ordinal=raw_ordinal,
                        reason="prev_change_id_link_valid",
                    )

        if not acknowledged:
            await self._terminal_quality(session_id, "subscription_failed")
            raise DeribitDataIntegrityError("Deribit subscription acknowledgement was incomplete.")
        if not book_state.has_snapshot:
            await self._terminal_quality(session_id, "snapshot_missing")
            raise DeribitDataIntegrityError(
                "Deribit book snapshot was incomplete.",
                quality_event="snapshot_missing",
            )
        trade_channel = f"trades.{DERIBIT_PERPETUAL}.100ms"
        required_channels = set(self._config.channels) - {trade_channel}
        missing_channel_count = len(required_channels - observed_channels)
        if missing_channel_count or not book_change_observed:
            await self._append_marker(
                session_id,
                "data_quality",
                "coverage_incomplete",
                reason="capture_stopped_before_required_public_session_evidence",
                missing_channel_count=missing_channel_count,
                book_change_observed=book_change_observed,
                public_trades_required=False,
            )
            raise DeribitDataIntegrityError(
                "Deribit required public session evidence was incomplete.",
                quality_event="coverage_incomplete",
            )

    async def _receive_or_stop(
        self,
        connection: WebSocketConnection,
        stop_event: asyncio.Event,
    ) -> CapturedApplicationPayload | None:
        receive_task = asyncio.create_task(
            self._receive_captured(connection),
            name="deribit-public-research-receive",
        )
        stop_task = asyncio.create_task(stop_event.wait(), name="deribit-public-stop-wait")
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
                except (DeribitDataIntegrityError, DeribitTransportTruncation, OSError):
                    raise
                except Exception:
                    raise OSError("Deribit WebSocket receive failed.") from None
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
        try:
            return capture_application_payload(
                frame,
                utc_ns=self._utc_ns,
                monotonic_ns=self._monotonic_ns,
            )
        except TypeError:
            raise DeribitDataIntegrityError("Deribit application frame type failed.") from None

    async def _record_inbound(
        self,
        captured: CapturedApplicationPayload,
        session_id: str,
    ) -> tuple[int, str, dict[str, object]]:
        if captured.frame_type is not FrameType.TEXT:
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="unknown",
                direction=MessageDirection.INBOUND,
            )
            await self._quality_failure(
                session_id,
                raw_ordinal,
                DeribitDataIntegrityError(
                    "Deribit JSON-RPC application frame was not text.",
                    quality_event="schema_error",
                ),
            )
            raise AssertionError("unreachable binary frame failure")
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
                DeribitDataIntegrityError(
                    "Deribit complete application payload exceeded its bound.",
                    quality_event="payload_oversize",
                ),
            )
            raise AssertionError("unreachable payload failure")
        try:
            document = _decode_json_object(captured.payload_bytes)
            channel = _classify_document(document)
        except DeribitDataIntegrityError as error:
            raw_ordinal = await self._append_captured(
                captured,
                session_id=session_id,
                channel="unknown",
                direction=MessageDirection.INBOUND,
            )
            await self._quality_failure(session_id, raw_ordinal, error)
            raise AssertionError("unreachable decode failure") from None
        raw_ordinal = await self._append_captured(
            captured,
            session_id=session_id,
            channel=channel,
            direction=MessageDirection.INBOUND,
        )
        return raw_ordinal, channel, document

    async def _validate_ack(
        self,
        document: dict[str, object],
        session_id: str,
        raw_ordinal: int,
    ) -> None:
        if document.get("jsonrpc") != "2.0" or document.get("id") != _REQUEST_ID:
            await self._quality_failure(
                session_id,
                raw_ordinal,
                DeribitDataIntegrityError("Deribit subscription response identity failed."),
            )
        if "error" in document:
            await self._append_marker(
                session_id,
                "subscription",
                "subscription_failed",
                authenticated=False,
                reason="venue_rejected_public_subscription",
                raw_message_ordinal=raw_ordinal,
            )
            raise DeribitDataIntegrityError("Deribit rejected the public subscription.")
        result = document.get("result")
        if type(result) is not list or any(type(value) is not str for value in result):
            await self._quality_failure(
                session_id,
                raw_ordinal,
                DeribitDataIntegrityError("Deribit subscription result schema failed."),
            )
        result_channels = cast(list[str], result)
        expected = frozenset(self._config.channels)
        if len(result_channels) != len(expected) or frozenset(result_channels) != expected:
            await self._quality_failure(
                session_id,
                raw_ordinal,
                DeribitDataIntegrityError("Deribit batch subscription result did not match."),
            )
        await self._append_marker(
            session_id,
            "subscription",
            "subscription_acknowledged",
            authenticated=False,
            channels=list(self._config.channels),
            raw_message_ordinal=raw_ordinal,
        )

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
                raise DeribitSinkError("Deribit research sink is unavailable after failure.")
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=DERIBIT_RESEARCH_VENUE,
                product=DERIBIT_RESEARCH_PRODUCT,
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
                raise DeribitSinkError("Deribit research sink append failed.") from None
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
                raise DeribitSinkError("Deribit research sink is unavailable after failure.")
            self._message_ordinal += 1
            record = RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue=DERIBIT_RESEARCH_VENUE,
                product=DERIBIT_RESEARCH_PRODUCT,
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
                raise DeribitSinkError("Deribit research sink append failed.") from None
            return record.message_ordinal

    async def _quality_failure(
        self,
        session_id: str,
        raw_ordinal: int,
        error: DeribitDataIntegrityError,
    ) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            error.quality_event,
            reason="market_data_integrity_failure",
            raw_message_ordinal=raw_ordinal,
        )
        raise error

    async def _terminal_quality(self, session_id: str, event: str) -> None:
        await self._append_marker(
            session_id,
            "data_quality",
            event,
            reason="capture_stopped_fail_closed",
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


def select_research_instruments(
    instruments: Sequence[object],
    *,
    index_price: str,
    now_ms: int,
) -> DeribitInstrumentSelection:
    """Select one nearest future and 12 paired options without retaining discovery data."""

    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer.")
    reference_price = _plain_decimal(index_price, allow_zero=False, allow_negative=False)
    future_candidates: list[tuple[int, str]] = []
    option_pairs: dict[tuple[int, str], dict[str, str]] = {}

    for value in instruments:
        item = _object(value)
        if item.get("is_active") is not True or item.get("base_currency") != "BTC":
            continue
        instrument_name = _required_text(item, "instrument_name")
        kind = _required_text(item, "kind")
        expiration = _metadata_integer(item.get("expiration_timestamp"))
        if expiration <= now_ms:
            continue
        if kind == "future" and instrument_name != DERIBIT_PERPETUAL:
            if _FUTURE_NAME.fullmatch(instrument_name) is not None:
                if not _expiry_matches(instrument_name.split("-", maxsplit=1)[1], expiration):
                    raise DeribitDataIntegrityError("Deribit future expiry metadata failed.")
                future_candidates.append((expiration, instrument_name))
            continue
        if kind != "option":
            continue
        identity = _parse_option_identity(instrument_name)
        if not _expiry_matches(identity.expiry_code, expiration):
            raise DeribitDataIntegrityError("Deribit option expiry metadata failed.")
        option_type = _required_text(item, "option_type")
        if option_type != identity.option_type:
            raise DeribitDataIntegrityError("Deribit option metadata identity failed.")
        strike = _metadata_decimal(item.get("strike"))
        if Decimal(strike) != Decimal(identity.strike):
            raise DeribitDataIntegrityError("Deribit option strike metadata failed.")
        pair = option_pairs.setdefault((expiration, identity.strike), {})
        if option_type in pair:
            raise DeribitDataIntegrityError("Deribit option metadata contained a duplicate leg.")
        pair[option_type] = instrument_name

    if not future_candidates:
        raise DeribitDataIntegrityError("Deribit discovery had no active dated BTC future.")
    dated_future = min(future_candidates)[1]
    expiries = sorted({expiry for expiry, _ in option_pairs})
    selected: list[tuple[int, Decimal, str, str]] = []
    selected_expiries = 0
    for expiry in expiries:
        paired_strikes = [
            (Decimal(strike), strike, legs)
            for (pair_expiry, strike), legs in option_pairs.items()
            if pair_expiry == expiry and set(legs) == {"call", "put"}
        ]
        if len(paired_strikes) < 3:
            continue
        closest = sorted(
            paired_strikes,
            key=lambda item: (abs(item[0] - Decimal(reference_price)), item[0]),
        )[:3]
        for strike_decimal, _, legs in sorted(closest, key=lambda item: item[0]):
            selected.append((expiry, strike_decimal, "call", legs["call"]))
            selected.append((expiry, strike_decimal, "put", legs["put"]))
        selected_expiries += 1
        if selected_expiries == 2:
            break
    if len({expiry for expiry, *_ in selected}) != 2 or len(selected) != 12:
        raise DeribitDataIntegrityError("Deribit discovery lacked the two-expiry option sample.")
    options = tuple(item[3] for item in sorted(selected))
    _validate_option_sample(options)
    return DeribitInstrumentSelection(dated_future=dated_future, option_instruments=options)


def _normalize_notification(
    document: dict[str, object],
    *,
    source_channel: str,
    raw_ordinal: int,
    config: DeribitPublicResearchConfig,
    book_state: _BookState,
    trade_state: _TradeState | None = None,
) -> tuple[str, dict[str, object]]:
    if document.get("jsonrpc") != "2.0" or document.get("method") != "subscription":
        raise DeribitDataIntegrityError("Deribit subscription notification envelope failed.")
    params = _object(document.get("params"))
    if params.get("channel") != source_channel:
        raise DeribitDataIntegrityError("Deribit subscription channel identity failed.")
    data = params.get("data")
    book_channel = f"book.{DERIBIT_PERPETUAL}.100ms"
    trade_channel = f"trades.{DERIBIT_PERPETUAL}.100ms"
    if source_channel == book_channel:
        return (
            "normalized_book",
            book_state.normalize(
                _object(data), source_channel=source_channel, raw_ordinal=raw_ordinal
            ),
        )
    if source_channel == trade_channel:
        return (
            "normalized_trades",
            (_TradeState() if trade_state is None else trade_state).normalize(
                data,
                source_channel=source_channel,
                raw_ordinal=raw_ordinal,
            ),
        )
    if source_channel == f"deribit_price_index.{DERIBIT_INDEX_NAME}":
        return (
            "normalized_index",
            _normalize_index(_object(data), source_channel=source_channel, raw_ordinal=raw_ordinal),
        )
    if source_channel == f"deribit_volatility_index.{DERIBIT_INDEX_NAME}":
        return (
            "normalized_dvol",
            _normalize_dvol(_object(data), source_channel=source_channel, raw_ordinal=raw_ordinal),
        )
    ticker_prefix = "ticker."
    ticker_suffix = ".100ms"
    if not source_channel.startswith(ticker_prefix) or not source_channel.endswith(ticker_suffix):
        raise DeribitDataIntegrityError("Deribit public channel was outside the fixed scope.")
    instrument = source_channel[len(ticker_prefix) : -len(ticker_suffix)]
    item = _object(data)
    if instrument in config.option_instruments:
        return (
            "normalized_option_ticker",
            _normalize_option_ticker(
                item,
                source_channel=source_channel,
                raw_ordinal=raw_ordinal,
                instrument_name=instrument,
            ),
        )
    if instrument in {DERIBIT_PERPETUAL, config.dated_future}:
        return (
            "normalized_derivative_ticker",
            _normalize_derivative_ticker(
                item,
                source_channel=source_channel,
                raw_ordinal=raw_ordinal,
                instrument_name=instrument,
            ),
        )
    raise DeribitDataIntegrityError("Deribit ticker instrument was outside the fixed scope.")


def _normalize_trades(
    value: object,
    *,
    source_channel: str,
    raw_ordinal: int,
) -> dict[str, object]:
    if type(value) is not list or not value:
        raise DeribitDataIntegrityError("Deribit trades payload schema failed.")
    events: list[dict[str, object]] = []
    for wire_order, raw_trade in enumerate(cast(list[object], value)):
        trade = _object(raw_trade)
        if _required_text(trade, "instrument_name") != DERIBIT_PERPETUAL:
            raise DeribitDataIntegrityError("Deribit trade instrument identity failed.")
        direction = _required_text(trade, "direction")
        if direction not in {"buy", "sell"}:
            raise DeribitDataIntegrityError("Deribit trade direction failed.")
        events.append(
            {
                "wire_order": wire_order,
                "trade_seq": _unsigned_integer(trade, "trade_seq"),
                "trade_id": _required_text(trade, "trade_id"),
                "direction": direction,
                "price": _decimal(trade, "price", allow_zero=False, allow_negative=False),
                "amount": _decimal(trade, "amount", allow_zero=False, allow_negative=False),
                "event_time_ms": _unsigned_integer(trade, "timestamp"),
                "tick_direction": _tick_direction(trade),
                "index_price": _decimal(
                    trade, "index_price", allow_zero=False, allow_negative=False
                ),
                "mark_price": _decimal(trade, "mark_price", allow_zero=False, allow_negative=False),
                "contracts": _optional_decimal(
                    trade, "contracts", allow_zero=True, allow_negative=False
                ),
                "liquidation": _optional_liquidation(trade),
                "iv": _optional_decimal(trade, "iv", allow_zero=True, allow_negative=False),
            }
        )
    return {
        "event": "normalized_trade_frame",
        "source_channel": source_channel,
        "raw_message_ordinal": raw_ordinal,
        "instrument_name": DERIBIT_PERPETUAL,
        "events": events,
    }


def _normalize_derivative_ticker(
    item: dict[str, object],
    *,
    source_channel: str,
    raw_ordinal: int,
    instrument_name: str,
) -> dict[str, object]:
    if _required_text(item, "instrument_name") != instrument_name:
        raise DeribitDataIntegrityError("Deribit derivative ticker identity failed.")
    return {
        "event": "normalized_derivative_ticker_frame",
        "source_channel": source_channel,
        "raw_message_ordinal": raw_ordinal,
        "instrument_name": instrument_name,
        "instrument_kind": "perpetual" if instrument_name == DERIBIT_PERPETUAL else "future",
        "event_time_ms": _unsigned_integer(item, "timestamp"),
        "state": _ticker_state(item),
        "mark_price": _decimal(item, "mark_price", allow_zero=False, allow_negative=False),
        "index_price": _decimal(item, "index_price", allow_zero=False, allow_negative=False),
        "open_interest": _decimal(item, "open_interest", allow_zero=True, allow_negative=False),
        "current_funding": _optional_decimal(
            item, "current_funding", allow_zero=True, allow_negative=True
        ),
        "funding_8h": _optional_decimal(item, "funding_8h", allow_zero=True, allow_negative=True),
        "best_bid_price": _optional_decimal(
            item, "best_bid_price", allow_zero=False, allow_negative=False
        ),
        "best_bid_amount": _optional_decimal(
            item, "best_bid_amount", allow_zero=True, allow_negative=False
        ),
        "best_ask_price": _optional_decimal(
            item, "best_ask_price", allow_zero=False, allow_negative=False
        ),
        "best_ask_amount": _optional_decimal(
            item, "best_ask_amount", allow_zero=True, allow_negative=False
        ),
    }


def _normalize_option_ticker(
    item: dict[str, object],
    *,
    source_channel: str,
    raw_ordinal: int,
    instrument_name: str,
) -> dict[str, object]:
    if _required_text(item, "instrument_name") != instrument_name:
        raise DeribitDataIntegrityError("Deribit option ticker identity failed.")
    identity = _parse_option_identity(instrument_name)
    greeks_value = item.get("greeks")
    greeks: dict[str, str | None]
    if greeks_value is None:
        greeks = {name: None for name in ("delta", "gamma", "vega", "theta", "rho")}
    else:
        source_greeks = _object(greeks_value)
        greeks = {
            name: _optional_decimal(
                source_greeks,
                name,
                allow_zero=True,
                allow_negative=True,
            )
            for name in ("delta", "gamma", "vega", "theta", "rho")
        }
    return {
        "event": "normalized_option_ticker_frame",
        "source_channel": source_channel,
        "raw_message_ordinal": raw_ordinal,
        "instrument_name": instrument_name,
        "expiry_code": identity.expiry_code,
        "strike": identity.strike,
        "option_type": identity.option_type,
        "event_time_ms": _unsigned_integer(item, "timestamp"),
        "state": _ticker_state(item),
        "underlying_index": _optional_opaque_text(item, "underlying_index"),
        "underlying_price": _optional_decimal(
            item, "underlying_price", allow_zero=False, allow_negative=False
        ),
        "mark_price": _optional_decimal(item, "mark_price", allow_zero=True, allow_negative=False),
        "mark_iv": _optional_decimal(item, "mark_iv", allow_zero=True, allow_negative=False),
        "bid_iv": _optional_decimal(item, "bid_iv", allow_zero=True, allow_negative=False),
        "ask_iv": _optional_decimal(item, "ask_iv", allow_zero=True, allow_negative=False),
        "best_bid_price": _optional_decimal(
            item, "best_bid_price", allow_zero=True, allow_negative=False
        ),
        "best_bid_amount": _optional_decimal(
            item, "best_bid_amount", allow_zero=True, allow_negative=False
        ),
        "best_ask_price": _optional_decimal(
            item, "best_ask_price", allow_zero=True, allow_negative=False
        ),
        "best_ask_amount": _optional_decimal(
            item, "best_ask_amount", allow_zero=True, allow_negative=False
        ),
        "open_interest": _optional_decimal(
            item, "open_interest", allow_zero=True, allow_negative=False
        ),
        "greeks": greeks,
    }


def _normalize_index(
    item: dict[str, object],
    *,
    source_channel: str,
    raw_ordinal: int,
) -> dict[str, object]:
    if _required_text(item, "index_name") != DERIBIT_INDEX_NAME:
        raise DeribitDataIntegrityError("Deribit price-index identity failed.")
    return {
        "event": "normalized_index_frame",
        "source_channel": source_channel,
        "raw_message_ordinal": raw_ordinal,
        "index_name": DERIBIT_INDEX_NAME,
        "event_time_ms": _unsigned_integer(item, "timestamp"),
        "index_price": _decimal(item, "price", allow_zero=False, allow_negative=False),
    }


def _normalize_dvol(
    item: dict[str, object],
    *,
    source_channel: str,
    raw_ordinal: int,
) -> dict[str, object]:
    if _required_text(item, "index_name") != DERIBIT_INDEX_NAME:
        raise DeribitDataIntegrityError("Deribit volatility-index identity failed.")
    return {
        "event": "normalized_dvol_frame",
        "source_channel": source_channel,
        "raw_message_ordinal": raw_ordinal,
        "index_name": DERIBIT_INDEX_NAME,
        "event_time_ms": _unsigned_integer(item, "timestamp"),
        "volatility": _decimal(item, "volatility", allow_zero=True, allow_negative=False),
    }


def _book_events(item: dict[str, object], *, snapshot: bool) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    wire_order = 0
    for field, side in (("bids", "bid"), ("asks", "ask")):
        levels = item.get(field)
        if type(levels) is not list:
            raise DeribitDataIntegrityError("Deribit order-book side schema failed.")
        for side_index, raw_level in enumerate(cast(list[object], levels)):
            if type(raw_level) is not list or len(raw_level) != 3:
                raise DeribitDataIntegrityError("Deribit order-book level schema failed.")
            level = cast(list[object], raw_level)
            action = level[0]
            if type(action) is not str or action not in {"new", "change", "delete"}:
                raise DeribitDataIntegrityError("Deribit order-book action failed.")
            if snapshot and action != "new":
                raise DeribitDataIntegrityError("Deribit snapshot contained a non-new action.")
            price = _decimal_value(level[1], allow_zero=False, allow_negative=False)
            amount = _decimal_value(
                level[2],
                allow_zero=action == "delete",
                allow_negative=False,
            )
            events.append(
                {
                    "wire_order": wire_order,
                    "side": side,
                    "side_index": side_index,
                    "action": action,
                    "price": price,
                    "quantity": amount,
                }
            )
            wire_order += 1
    return events


def _decode_json_object(payload_bytes: bytes) -> dict[str, object]:
    try:
        loaded = json.loads(
            payload_bytes,
            parse_float=_JsonFloatLexeme,
            parse_int=_JsonIntegerLexeme,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise DeribitDataIntegrityError("Deribit inbound JSON validation failed.") from None
    if type(loaded) is not dict:
        raise DeribitDataIntegrityError("Deribit inbound JSON root failed.")
    return cast(dict[str, object], loaded)


def _classify_document(document: dict[str, object]) -> str:
    if "id" in document or "error" in document:
        return "subscription"
    if document.get("method") != "subscription":
        raise DeribitDataIntegrityError("Deribit inbound message identity failed.")
    params = _object(document.get("params"))
    return _required_text(params, "channel")


def _validate_option_sample(options: tuple[str, ...]) -> None:
    if not 2 <= len(options) <= 12 or len(options) % 2:
        raise ValueError("option_instruments must contain one to six call/put pairs.")
    if len(set(options)) != len(options):
        raise ValueError("option_instruments must not contain duplicates.")
    pairs: dict[tuple[str, str], set[str]] = {}
    for name in options:
        if type(name) is not str:
            raise TypeError("option instrument names must be strings.")
        identity = _parse_option_identity(name)
        pairs.setdefault((identity.expiry_code, identity.strike), set()).add(identity.option_type)
    if any(legs != {"call", "put"} for legs in pairs.values()):
        raise ValueError("option_instruments must contain paired calls and puts.")
    expiries = {expiry for expiry, _ in pairs}
    if len(expiries) > 2:
        raise ValueError("option_instruments may span at most two expiries.")
    if any(sum(expiry == candidate for candidate, _ in pairs) > 3 for expiry in expiries):
        raise ValueError("option_instruments may contain at most three strikes per expiry.")


def _parse_option_identity(instrument_name: str) -> _OptionIdentity:
    match = _OPTION_NAME.fullmatch(instrument_name)
    if match is None:
        raise ValueError("option instrument name must use the BTC expiry-strike-C/P form.")
    expiry, strike, leg = match.groups()
    return _OptionIdentity(
        expiry_code=expiry,
        strike=strike,
        option_type="call" if leg == "C" else "put",
    )


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise DeribitDataIntegrityError("Deribit object schema failed.")
    return cast(dict[str, object], value)


def _required_text(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not str or not value:
        raise DeribitDataIntegrityError("Deribit text field schema failed.")
    return value


def _optional_text(document: dict[str, object], field: str) -> str | None:
    value = document.get(field)
    if value is None:
        return None
    if type(value) is not str or not value:
        raise DeribitDataIntegrityError("Deribit optional text field schema failed.")
    return value


def _optional_opaque_text(document: dict[str, object], field: str) -> str | None:
    value = document.get(field)
    if value is None:
        return None
    if type(value) in {str, _JsonIntegerLexeme, _JsonFloatLexeme} and value:
        return cast(str, value)
    raise DeribitDataIntegrityError("Deribit optional opaque field schema failed.")


def _decimal(
    document: dict[str, object],
    field: str,
    *,
    allow_zero: bool,
    allow_negative: bool,
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
    allow_zero: bool,
    allow_negative: bool,
) -> str | None:
    value = document.get(field)
    if value is None:
        return None
    return _decimal_value(value, allow_zero=allow_zero, allow_negative=allow_negative)


def _decimal_value(value: object, *, allow_zero: bool, allow_negative: bool) -> str:
    if type(value) not in {_JsonIntegerLexeme, _JsonFloatLexeme}:
        raise DeribitDataIntegrityError("Deribit decimal field schema failed.")
    return _validated_decimal_text(
        cast(str, value),
        allow_zero=allow_zero,
        allow_negative=allow_negative,
    )


def _plain_decimal(value: object, *, allow_zero: bool, allow_negative: bool) -> str:
    if type(value) is not str:
        raise DeribitDataIntegrityError("Deribit decimal field schema failed.")
    return _validated_decimal_text(
        value,
        allow_zero=allow_zero,
        allow_negative=allow_negative,
    )


def _validated_decimal_text(value: str, *, allow_zero: bool, allow_negative: bool) -> str:
    if _DECIMAL_TEXT.fullmatch(value) is None:
        raise DeribitDataIntegrityError("Deribit decimal field schema failed.")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise DeribitDataIntegrityError("Deribit decimal field schema failed.") from None
    if decimal < 0 and not allow_negative:
        raise DeribitDataIntegrityError("Deribit decimal field schema failed.")
    if decimal == 0 and not allow_zero:
        raise DeribitDataIntegrityError("Deribit decimal field schema failed.")
    return value


def _unsigned_integer(document: dict[str, object], field: str) -> str:
    value = document.get(field)
    if type(value) is not _JsonIntegerLexeme or not value.isascii() or not value.isdigit():
        raise DeribitDataIntegrityError("Deribit integer field schema failed.")
    return cast(str, value)


def _metadata_integer(value: object) -> int:
    if type(value) is int:
        integer = value
    elif type(value) is str and value.isascii() and value.isdigit():
        integer = int(value)
    else:
        raise DeribitDataIntegrityError("Deribit metadata integer schema failed.")
    if integer < 0:
        raise DeribitDataIntegrityError("Deribit metadata integer schema failed.")
    return integer


def _metadata_decimal(value: object) -> str:
    if type(value) is int:
        text = str(value)
    elif type(value) is Decimal:
        text = str(value)
    elif type(value) is str:
        text = value
    else:
        raise DeribitDataIntegrityError("Deribit metadata decimal schema failed.")
    return _plain_decimal(text, allow_zero=False, allow_negative=False)


def _tick_direction(document: dict[str, object]) -> str:
    value = _unsigned_integer(document, "tick_direction")
    if value not in {"0", "1", "2", "3"}:
        raise DeribitDataIntegrityError("Deribit tick direction failed.")
    return value


def _ticker_state(document: dict[str, object]) -> str:
    state = _required_text(document, "state")
    if state not in {
        "open",
        "settlement",
        "delivered",
        "inactive",
        "locked",
        "halted",
        "archivized",
    }:
        raise DeribitDataIntegrityError("Deribit ticker state failed.")
    return state


def _optional_liquidation(document: dict[str, object]) -> str | None:
    liquidation = _optional_text(document, "liquidation")
    if liquidation not in {None, "M", "T", "MT"}:
        raise DeribitDataIntegrityError("Deribit liquidation enum failed.")
    return liquidation


def _expiry_matches(expiry_code: str, expiration_timestamp_ms: int) -> bool:
    try:
        parsed = datetime.strptime(expiry_code, "%d%b%y").replace(tzinfo=UTC)
    except ValueError:
        return False
    actual = datetime.fromtimestamp(expiration_timestamp_ms // 1000, UTC)
    return actual.date() == parsed.date()


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


def _require_bounded_duration(duration_seconds: object) -> None:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
