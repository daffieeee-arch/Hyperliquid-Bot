"""Bounded public Hyperliquid BTC perpetual exact-raw research capture."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast

import duckdb
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

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

HYPERLIQUID_MAINNET_WEBSOCKET_URL: Final = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_RESEARCH_VENUE: Final = "hyperliquid"
HYPERLIQUID_RESEARCH_PRODUCT: Final = "BTC-PERP"
MAX_CAPTURE_SECONDS: Final = 600.0

_GREETING: Final = b"Websocket connection established."
_PING_TEXT: Final = '{"method":"ping"}'
_SUBSCRIPTION_PAYLOADS: Final = (
    (
        "trades",
        b'{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}',
    ),
    (
        "bbo",
        b'{"method":"subscribe","subscription":{"type":"bbo","coin":"BTC"}}',
    ),
    (
        "l2Book",
        b'{"method":"subscribe","subscription":{"type":"l2Book","coin":"BTC"}}',
    ),
    (
        "activeAssetCtx",
        b'{"method":"subscribe","subscription":{"type":"activeAssetCtx","coin":"BTC"}}',
    ),
)
_EXPECTED_SUBSCRIPTIONS: Final = frozenset(channel for channel, _ in _SUBSCRIPTION_PAYLOADS)
_EXPECTED_INBOUND_CHANNELS: Final = _EXPECTED_SUBSCRIPTIONS | {
    "subscriptionResponse",
    "pong",
}

_TRANSPORT_PRIVACY_LOGGER: Final = logging.Logger(
    "hyperliquid_bot.hyperliquid_raw_research_transport",
    level=logging.CRITICAL + 1,
)
_TRANSPORT_PRIVACY_LOGGER.disabled = True
_TRANSPORT_PRIVACY_LOGGER.propagate = False
_TRANSPORT_PRIVACY_LOGGER.addHandler(logging.NullHandler())


class WebSocketConnection(Protocol):
    """Small transport surface for the real connection and offline fakes."""

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]
type SessionIdFactory = Callable[[], str]
type MonotonicClock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class HyperliquidRawResearchConfig:
    """Fixed public BTC-PERP feed and bounded transport controls."""

    heartbeat_interval_seconds: float = 45.0
    reconnect_delay_seconds: float = 3.0
    max_application_payload_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            type(self.heartbeat_interval_seconds) not in (int, float)
            or self.heartbeat_interval_seconds <= 0
        ):
            raise ValueError("heartbeat_interval_seconds must be positive.")
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


class HyperliquidRawResearchCollector:
    """Capture four public channels without normalizing or executing orders."""

    def __init__(
        self,
        sink: RawResearchSink,
        *,
        config: HyperliquidRawResearchConfig | None = None,
        connection_factory: ConnectionFactory | None = None,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        monotonic: MonotonicClock = time.monotonic,
        session_id_factory: SessionIdFactory | None = None,
    ) -> None:
        self._sink = sink
        self._config = config if config is not None else HyperliquidRawResearchConfig()
        self._connection_factory = (
            connection_factory
            if connection_factory is not None
            else _mainnet_connection_factory(self._config)
        )
        self._utc_ns = utc_ns
        self._monotonic_ns = monotonic_ns
        self._monotonic = monotonic
        self._session_id_factory = (
            session_id_factory if session_id_factory is not None else lambda: uuid.uuid4().hex
        )
        self._message_ordinal = 0

    async def capture_for(
        self,
        duration_seconds: float,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Capture until the duration elapses or an optional earlier stop is set."""

        _require_bounded_duration(duration_seconds)
        capture_stop = stop_event if stop_event is not None else asyncio.Event()
        timer = asyncio.create_task(
            self._stop_after(capture_stop, float(duration_seconds)),
            name="hyperliquid-raw-research-duration",
        )
        try:
            await self._capture_until(capture_stop)
        finally:
            capture_stop.set()
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)

    async def _stop_after(self, stop_event: asyncio.Event, duration_seconds: float) -> None:
        await asyncio.sleep(duration_seconds)
        stop_event.set()

    async def _capture_until(self, stop_event: asyncio.Event) -> None:
        previous_connected_session_id: str | None = None
        while not stop_event.is_set():
            session_id = self._session_id_factory()
            await self._append_marker(
                session_id,
                "session",
                "session_started",
                reason=(
                    "initial_connection"
                    if previous_connected_session_id is None
                    else "reconnect_attempt"
                ),
            )
            connected = False
            try:
                async with self._connection_factory() as connection:
                    connected = True
                    await self._append_marker(session_id, "session", "connected")
                    if previous_connected_session_id is not None:
                        await self._append_marker(
                            session_id,
                            "session",
                            "reconnected",
                            previous_session_id=previous_connected_session_id,
                        )
                    await self._send_subscriptions(connection, session_id)
                    await self._receive_session(connection, session_id, stop_event)
                    await self._append_marker(
                        session_id,
                        "session",
                        "session_stopped",
                        reason="capture_limit_reached",
                    )
                    return
            except asyncio.CancelledError:
                if connected:
                    await self._append_marker(
                        session_id,
                        "session",
                        "session_stopped",
                        reason="capture_cancelled",
                    )
                raise
            except (WebSocketException, OSError):
                if connected:
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
                    previous_connected_session_id = session_id
                else:
                    await self._append_marker(
                        session_id,
                        "session",
                        "connection_failed",
                        reason="transport_error",
                    )

            if stop_event.is_set():
                return
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=float(self._config.reconnect_delay_seconds),
                )
            except TimeoutError:
                pass

    async def _send_subscriptions(
        self,
        connection: WebSocketConnection,
        session_id: str,
    ) -> None:
        for subscription_type, payload_bytes in _SUBSCRIPTION_PAYLOADS:
            captured = capture_application_payload(
                payload_bytes.decode("utf-8"),
                utc_ns=self._utc_ns,
                monotonic_ns=self._monotonic_ns,
            )
            await connection.send(payload_bytes.decode("utf-8"))
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
                subscription_type=subscription_type,
            )

    async def _receive_session(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        acknowledged_subscriptions: set[str] = set()
        subscriptions_active_marked = False
        stop_task = asyncio.create_task(stop_event.wait(), name="raw-research-stop-wait")
        receive_task = asyncio.create_task(
            self._receive_captured(connection),
            name="raw-research-receive",
        )
        next_heartbeat = self._monotonic() + float(self._config.heartbeat_interval_seconds)
        try:
            while True:
                heartbeat_wait = max(0.0, next_heartbeat - self._monotonic())
                done, _ = await asyncio.wait(
                    (receive_task, stop_task),
                    timeout=heartbeat_wait,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if receive_task in done:
                    captured = receive_task.result()
                    _, channel, document = await self._record_inbound(captured, session_id)
                    if channel == "subscriptionResponse" and document is not None:
                        subscription_type = _subscription_type_from_response(document)
                        if subscription_type in _EXPECTED_SUBSCRIPTIONS:
                            acknowledged_subscriptions.add(subscription_type)
                            await self._append_marker(
                                session_id,
                                "subscription",
                                "subscription_acknowledged",
                                subscription_type=subscription_type,
                            )
                            if (
                                acknowledged_subscriptions == _EXPECTED_SUBSCRIPTIONS
                                and not subscriptions_active_marked
                            ):
                                await self._append_marker(
                                    session_id,
                                    "subscription",
                                    "subscriptions_active",
                                )
                                subscriptions_active_marked = True
                        else:
                            await self._append_marker(
                                session_id,
                                "data_quality",
                                "unexpected_subscription_response",
                                reason="response did not identify one requested subscription",
                            )
                    receive_task = asyncio.create_task(
                        self._receive_captured(connection),
                        name="raw-research-receive",
                    )
                    if stop_task.done():
                        return
                    continue
                if stop_task in done:
                    return

                await self._send_heartbeat(connection, session_id)
                next_heartbeat = self._monotonic() + float(self._config.heartbeat_interval_seconds)
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

    async def _record_inbound(
        self,
        captured: CapturedApplicationPayload,
        session_id: str,
    ) -> tuple[int, str, dict[str, object] | None]:
        channel, document, quality_reason = _classify_inbound(captured.payload_bytes)
        raw_ordinal = await self._append_captured(
            captured,
            session_id=session_id,
            channel=channel,
            direction=MessageDirection.INBOUND,
        )
        if quality_reason is not None:
            await self._append_marker(
                session_id,
                "data_quality",
                "unclassified_payload",
                reason=quality_reason,
                raw_message_ordinal=raw_ordinal,
            )
        elif channel not in _EXPECTED_INBOUND_CHANNELS and channel != "session":
            await self._append_marker(
                session_id,
                "data_quality",
                "unexpected_channel",
                reason="received channel was not requested by DATA-1A",
                raw_message_ordinal=raw_ordinal,
            )
        return raw_ordinal, channel, document

    async def _append_captured(
        self,
        captured: CapturedApplicationPayload,
        *,
        session_id: str,
        channel: str,
        direction: MessageDirection,
    ) -> int:
        if type(captured) is not CapturedApplicationPayload:
            raise TypeError("captured must be a CapturedApplicationPayload.")
        self._message_ordinal += 1
        record = RawResearchRecord(
            schema_version=RAW_RESEARCH_SCHEMA_VERSION,
            venue=HYPERLIQUID_RESEARCH_VENUE,
            product=HYPERLIQUID_RESEARCH_PRODUCT,
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
        await self._sink.append(record)
        return record.message_ordinal

    async def _append_marker(
        self,
        session_id: str,
        channel: str,
        event: str,
        **fields: str | int,
    ) -> int:
        payload: dict[str, str | int] = {"event": event, **fields}
        payload_bytes = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self._message_ordinal += 1
        record = RawResearchRecord(
            schema_version=RAW_RESEARCH_SCHEMA_VERSION,
            venue=HYPERLIQUID_RESEARCH_VENUE,
            product=HYPERLIQUID_RESEARCH_PRODUCT,
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
        await self._sink.append(record)
        return record.message_ordinal


@asynccontextmanager
async def _mainnet_connection(
    config: HyperliquidRawResearchConfig,
) -> AsyncIterator[WebSocketConnection]:
    async with connect(
        HYPERLIQUID_MAINNET_WEBSOCKET_URL,
        open_timeout=10.0,
        close_timeout=5.0,
        ping_interval=None,
        max_size=config.max_application_payload_bytes,
        max_queue=1024,
        logger=_TRANSPORT_PRIVACY_LOGGER,
    ) as connection:
        yield cast(WebSocketConnection, connection)


def _mainnet_connection_factory(config: HyperliquidRawResearchConfig) -> ConnectionFactory:
    return lambda: _mainnet_connection(config)


def _classify_inbound(
    payload_bytes: bytes,
) -> tuple[str, dict[str, object] | None, str | None]:
    if payload_bytes == _GREETING:
        return "session", None, None
    try:
        loaded = json.loads(payload_bytes, parse_float=str, parse_int=str)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unknown", None, "payload was not a valid UTF-8 JSON object"
    if type(loaded) is not dict:
        return "unknown", None, "JSON root was not an object"
    document = cast(dict[str, object], loaded)
    channel = document.get("channel")
    if type(channel) is not str or not channel:
        return "unknown", document, "JSON object had no non-empty string channel"
    return channel, document, None


def _subscription_type_from_response(document: dict[str, object]) -> str | None:
    data = document.get("data")
    if type(data) is not dict:
        return None
    subscription = data.get("subscription")
    if type(subscription) is not dict:
        return None
    subscription_type = subscription.get("type")
    return subscription_type if type(subscription_type) is str else None


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
        gap_row = connection.execute(
            "SELECT count(*) FROM data_quality_events WHERE event = 'gap_detected'"
        ).fetchone()
        reconnect_row = connection.execute(
            "SELECT count(*) FROM sessions WHERE event = 'reconnected'"
        ).fetchone()
        if totals is None or gap_row is None or reconnect_row is None:
            raise RuntimeError("DuckDB did not return the requested capture aggregates.")
        total_events, total_payload_bytes = totals
        gaps = gap_row[0]
        reconnects = reconnect_row[0]
    finally:
        connection.close()

    parquet_files = tuple(sorted(parquet_dir.resolve().glob("*.parquet")))
    parquet_bytes = sum(path.stat().st_size for path in parquet_files)
    raw_bytes = int(total_payload_bytes)
    return {
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
        "payload_bytes": raw_bytes,
        "parquet_files": len(parquet_files),
        "parquet_bytes": parquet_bytes,
        "raw_payload_to_parquet_ratio": (raw_bytes / parquet_bytes if parquet_bytes else None),
        "gaps": int(gaps),
        "reconnects": int(reconnects),
    }


async def run_bounded_capture(
    *,
    output_dir: Path,
    database_path: Path,
    duration_seconds: float,
) -> dict[str, object]:
    """Run the no-credential capture, close Parquet, and build the DuckDB catalog."""

    _require_bounded_duration(duration_seconds)
    writer = ParquetResearchWriter(output_dir, rotation=ParquetRotation())
    collector = HyperliquidRawResearchCollector(writer)
    try:
        await collector.capture_for(duration_seconds)
    finally:
        await writer.aclose()
    create_research_catalog(output_dir, database_path)
    return build_capture_report(database_path, output_dir)


def _require_bounded_duration(duration_seconds: object) -> None:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bounded public Hyperliquid BTC-PERP exact-raw research capture."
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--duration-seconds", required=True, type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    report = asyncio.run(
        run_bounded_capture(
            output_dir=cast(Path, args.output_dir),
            database_path=cast(Path, args.database),
            duration_seconds=cast(float, args.duration_seconds),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
