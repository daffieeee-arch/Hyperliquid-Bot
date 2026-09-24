"""Public Hyperliquid BTC perpetual exact-raw research capture.

Duration may be a short smoke or a retained multi-day run. The process still
stops at an explicit duration or operator signal; this is not a 24/7 service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import signal
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

from .capture_live_status import (
    CAPTURE_LIVE_NAME,
    stamp_start_metadata,
    utc_now_text,
    write_capture_live,
)
from .capture_observability import (
    add_transport_counts,
    attach_observability_health,
    capture_log_path,
    capture_logger,
    configure_capture_logger,
    elapsed_from_report,
    transport_exception_fields,
)
from .capture_operator_alert import emit_capture_operator_alert
from .hyperliquid_retained_instruments import (
    DEFAULT_CANDLE_INTERVAL,
    HYPERLIQUID_CANDLE_INTERVALS,
    HYPERLIQUID_REQUIRED_COIN,
    HyperliquidRetainedInstrumentPlan,
    build_hyperliquid_retained_plan,
)
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
from .reconstructable_paths import (
    DATA1A_PATH_CONTRACT_ID,
    Data1ARunPaths,
    data1a_run_paths,
)
from .retained_capture_profile import claim_profile_fields

HYPERLIQUID_MAINNET_WEBSOCKET_URL: Final = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_RESEARCH_VENUE: Final = "hyperliquid"
HYPERLIQUID_RESEARCH_PRODUCT: Final = "BTC-PERP"
SMOKE_CAPTURE_SECONDS: Final = 600.0
MAX_CAPTURE_SECONDS: Final = 7 * 24 * 60 * 60
DATA1A_CLAIM_SCHEMA: Final = "data-1a-retained-capture-claim-v1"
DATA1A_HEALTH_SCHEMA: Final = "data-1a-retained-capture-health-v1"
HYPERLIQUID_TRANSPORT_PROFILE: Final = "hyperliquid_public"
_SERVER_IDLE_TIMEOUT_SECONDS: Final = 60.0
_MIN_HEARTBEAT_SECONDS: Final = 5.0
# Official HL websocket (timeouts and heartbeats, fetched 2026-09-24): the server
# closes a connection it has not written for 60s. Client {"method":"ping"} /
# {"channel":"pong"} keeps that socket up and is not market data. 90s is longer
# than that server idle and the 45s client heartbeat, so one heartbeat gap does
# not reconnect a quiet channel, and one live channel still cannot hide a sibling.
_MARKET_DATA_STALE_SECONDS: Final = 90.0
_MARKET_DATA_CHANNELS: Final = frozenset({"trades", "bbo", "l2Book", "activeAssetCtx"})

_GREETING: Final = b"Websocket connection established."
_PING_TEXT: Final = '{"method":"ping"}'
_DEFAULT_BTC_PLAN: Final = build_hyperliquid_retained_plan()
_CONTROL_INBOUND_CHANNELS: Final = frozenset({"subscriptionResponse", "pong"})
_SUBSCRIPTION_PAYLOADS: Final = tuple(
    (item.channel, item.payload_bytes) for item in _DEFAULT_BTC_PLAN.subscriptions
)
_EXPECTED_SUBSCRIPTIONS: Final = _DEFAULT_BTC_PLAN.expected_channels
_EXPECTED_INBOUND_CHANNELS: Final = _EXPECTED_SUBSCRIPTIONS | _CONTROL_INBOUND_CHANNELS

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
    receive_timeout_seconds: float = 60.0
    reconnect_delay_seconds: float = 3.0
    max_application_payload_bytes: int = 8 * 1024 * 1024
    market_data_stale_seconds: float = _MARKET_DATA_STALE_SECONDS

    def __post_init__(self) -> None:
        if type(self.heartbeat_interval_seconds) not in (int, float) or not math.isfinite(
            float(self.heartbeat_interval_seconds)
        ):
            raise ValueError("heartbeat_interval_seconds must be a finite number.")
        heartbeat = float(self.heartbeat_interval_seconds)
        if heartbeat < _MIN_HEARTBEAT_SECONDS or heartbeat >= _SERVER_IDLE_TIMEOUT_SECONDS:
            raise ValueError("heartbeat_interval_seconds must be in [5, 60).")
        if type(self.receive_timeout_seconds) not in (int, float) or not math.isfinite(
            float(self.receive_timeout_seconds)
        ):
            raise ValueError("receive_timeout_seconds must be a finite number.")
        receive_timeout = float(self.receive_timeout_seconds)
        if receive_timeout < heartbeat:
            raise ValueError("receive_timeout_seconds must cover heartbeat_interval_seconds.")
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
        if type(self.market_data_stale_seconds) not in (int, float) or not math.isfinite(
            float(self.market_data_stale_seconds)
        ):
            raise ValueError("market_data_stale_seconds must be a finite number.")
        market_stale = float(self.market_data_stale_seconds)
        if market_stale <= 0:
            raise ValueError("market_data_stale_seconds must be positive.")
        object.__setattr__(self, "heartbeat_interval_seconds", heartbeat)
        object.__setattr__(self, "receive_timeout_seconds", receive_timeout)
        object.__setattr__(self, "market_data_stale_seconds", market_stale)


class HyperliquidRawResearchCollector:
    """Capture four public channels without normalizing or executing orders."""

    def __init__(
        self,
        sink: RawResearchSink,
        *,
        config: HyperliquidRawResearchConfig | None = None,
        instrument_plan: HyperliquidRetainedInstrumentPlan | None = None,
        connection_factory: ConnectionFactory | None = None,
        utc_ns: NanosecondClock = time.time_ns,
        monotonic_ns: NanosecondClock = time.monotonic_ns,
        monotonic: MonotonicClock = time.monotonic,
        session_id_factory: SessionIdFactory | None = None,
    ) -> None:
        self._sink = sink
        self._config = config if config is not None else HyperliquidRawResearchConfig()
        self._instrument_plan = (
            instrument_plan if instrument_plan is not None else build_hyperliquid_retained_plan()
        )
        if HYPERLIQUID_RESEARCH_PRODUCT not in {
            self._instrument_plan.required.product,
        }:
            raise ValueError("DATA-1A required product must remain BTC-PERP")
        if "BTC" not in self._instrument_plan.started_coins:
            raise ValueError("DATA-1A must not drop required BTC channels")
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
        self._awaiting_market_recovery: set[tuple[str, str]] = set()
        self._live_status_path: Path | None = None
        self._live_run_id: str | None = None
        self._feed_last_market_utc: dict[tuple[str, str], str] = {}
        self._feed_state: dict[tuple[str, str], str] = {}

    def _expected_inbound_channels(self) -> frozenset[str]:
        return self._instrument_plan.expected_channels | _CONTROL_INBOUND_CHANNELS

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
        live_stop = asyncio.Event()
        live_task = asyncio.create_task(
            self._publish_live_until(live_stop),
            name="hyperliquid-live-status",
        )
        try:
            await self._capture_until(capture_stop)
        finally:
            capture_stop.set()
            live_stop.set()
            timer.cancel()
            await asyncio.gather(timer, live_task, return_exceptions=True)

    async def _stop_after(self, stop_event: asyncio.Event, duration_seconds: float) -> None:
        await asyncio.sleep(duration_seconds)
        stop_event.set()

    async def _capture_until(self, stop_event: asyncio.Event) -> None:
        previous_connected_session_id: str | None = None
        while not stop_event.is_set():
            session_id = self._session_id_factory()
            capture_logger().info(
                "hyperliquid session_start transport_profile=%s reason=%s",
                HYPERLIQUID_TRANSPORT_PROFILE,
                "initial_connection"
                if previous_connected_session_id is None
                else "reconnect_attempt",
            )
            await self._append_marker(
                session_id,
                "session",
                "session_started",
                transport_profile=HYPERLIQUID_TRANSPORT_PROFILE,
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
                    await self._append_marker(
                        session_id,
                        "session",
                        "connected",
                        transport_profile=HYPERLIQUID_TRANSPORT_PROFILE,
                    )
                    if previous_connected_session_id is not None:
                        await self._append_marker(
                            session_id,
                            "session",
                            "reconnected",
                            previous_session_id=previous_connected_session_id,
                            transport_profile=HYPERLIQUID_TRANSPORT_PROFILE,
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
            except (WebSocketException, OSError, TimeoutError) as error:
                failure_fields = transport_exception_fields(error)
                del error
                capture_logger().info(
                    "hyperliquid disconnect transport_profile=%s connected=%s "
                    "exception_class=%s close_code=%s",
                    HYPERLIQUID_TRANSPORT_PROFILE,
                    connected,
                    failure_fields.get("exception_class"),
                    failure_fields.get("close_code"),
                )
                if connected:
                    self._mark_feeds_recovering()
                    await self._append_marker(
                        session_id,
                        "session",
                        "disconnected",
                        reason="transport_error",
                        transport_profile=HYPERLIQUID_TRANSPORT_PROFILE,
                        **failure_fields,
                    )
                    await self._append_marker(
                        session_id,
                        "data_quality",
                        "gap_detected",
                        reason="transport_disconnect; missed stream history is not reconstructable",
                        transport_profile=HYPERLIQUID_TRANSPORT_PROFILE,
                        **failure_fields,
                    )
                    previous_connected_session_id = session_id
                else:
                    await self._append_marker(
                        session_id,
                        "session",
                        "connection_failed",
                        reason="transport_error",
                        transport_profile=HYPERLIQUID_TRANSPORT_PROFILE,
                        **failure_fields,
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
        for subscription in self._instrument_plan.subscriptions:
            payload_bytes = subscription.payload_bytes
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
                subscription_type=subscription.channel,
                coin=subscription.coin,
                **(
                    {"interval": subscription.interval} if subscription.interval is not None else {}
                ),
            )

    def set_live_status_path(self, path: Path, run_id: str) -> None:
        self._live_status_path = path
        self._live_run_id = run_id

    def _required_market_identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            identity
            for identity in sorted(self._instrument_plan.expected_subscription_identities)
            if identity[0] in _MARKET_DATA_CHANNELS and identity[1] == HYPERLIQUID_REQUIRED_COIN
        )

    def _note_required_market(self, identity: tuple[str, str]) -> None:
        self._feed_last_market_utc[identity] = utc_now_text()
        self._feed_state[identity] = "fresh"

    def _mark_feeds_recovering(self) -> None:
        for identity in self._required_market_identities():
            self._feed_state[identity] = "recovering"

    def publish_live_status(self) -> None:
        path = self._live_status_path
        run_id = self._live_run_id
        if path is None or run_id is None:
            return
        pending: int | None = None
        published: int | None = None
        if isinstance(self._sink, ParquetResearchWriter):
            pending = self._sink.pending_record_count
            published = self._sink.published_part_count
        feeds: list[dict[str, object]] = []
        for channel, coin in self._required_market_identities():
            identity = (channel, coin)
            feeds.append(
                {
                    "name": f"{channel}/{coin}",
                    "role": "required",
                    "state": self._feed_state.get(identity, "unknown"),
                    "last_market_utc": self._feed_last_market_utc.get(identity),
                    "silence_bound_seconds": float(self._config.market_data_stale_seconds),
                }
            )
        try:
            write_capture_live(
                path,
                run_id=run_id,
                writer_pending_records=pending,
                writer_published_parts=published,
                feeds=feeds,
                definitive_outage=None,
            )
        except OSError:
            capture_logger().info("hyperliquid live_status_write_failed")

    async def _publish_live_until(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            self.publish_live_status()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5)
            except TimeoutError:
                continue
        self.publish_live_status()

    async def _raise_if_required_market_silent(
        self,
        session_id: str,
        last_market: dict[tuple[str, str], float],
    ) -> None:
        now = self._monotonic()
        silent_identities = sorted(
            identity
            for identity, seen_at in last_market.items()
            if now >= seen_at + self._config.market_data_stale_seconds
        )
        if not silent_identities:
            return
        for market_channel, coin in silent_identities:
            self._awaiting_market_recovery.add((market_channel, coin))
            await self._append_marker(
                session_id,
                "data_quality",
                "market_data_stale_despite_heartbeat",
                reason=(
                    "heartbeat/pong or another channel kept the socket alive but "
                    f"{market_channel}/{coin} was silent; heartbeat is not "
                    "market-data validity"
                ),
                market_channel=market_channel,
                coin=coin,
            )
        market_channel, coin = silent_identities[0]
        raise TimeoutError(f"Hyperliquid {market_channel}/{coin} was silent despite heartbeat.")

    async def _receive_session(
        self,
        connection: WebSocketConnection,
        session_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        acknowledged_subscriptions: set[tuple[str, str]] = set()
        expected_identities = self._instrument_plan.expected_subscription_identities
        subscriptions_active_marked = False
        # Required BTC market channels each have a clock. Addon silence is not
        # a fault: one active channel must not hide a quiet required channel.
        # Official HL websocket: the server closes a connection it has not
        # written in 60s; client {"method":"ping"} / {"channel":"pong"} is
        # keepalive, not market data.
        required_market = tuple(
            identity
            for identity in sorted(expected_identities)
            if identity[0] in _MARKET_DATA_CHANNELS and identity[1] == HYPERLIQUID_REQUIRED_COIN
        )
        session_started = self._monotonic()
        last_market: dict[tuple[str, str], float] = {
            identity: session_started for identity in required_market
        }
        stop_task = asyncio.create_task(stop_event.wait(), name="raw-research-stop-wait")
        receive_task = asyncio.create_task(
            self._receive_captured(connection),
            name="raw-research-receive",
        )
        next_heartbeat = self._monotonic() + float(self._config.heartbeat_interval_seconds)
        last_inbound = self._monotonic()
        try:
            while True:
                now = self._monotonic()
                heartbeat_wait = max(0.0, next_heartbeat - now)
                receive_wait = max(
                    0.0,
                    last_inbound + float(self._config.receive_timeout_seconds) - now,
                )
                stale_identity = _oldest_silent_identity(last_market, now)
                if stale_identity is None:
                    stale_wait = heartbeat_wait
                else:
                    stale_wait = max(
                        0.0,
                        last_market[stale_identity] + self._config.market_data_stale_seconds - now,
                    )
                done, _ = await asyncio.wait(
                    (receive_task, stop_task),
                    timeout=min(heartbeat_wait, receive_wait, stale_wait),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if receive_task in done:
                    captured = receive_task.result()
                    last_inbound = self._monotonic()
                    _, channel, document = await self._record_inbound(captured, session_id)
                    for coin in _market_coins(channel, document):
                        market_identity = (channel, coin)
                        if market_identity not in last_market:
                            continue
                        last_market[market_identity] = last_inbound
                        self._note_required_market(market_identity)
                        if market_identity in self._awaiting_market_recovery:
                            self._awaiting_market_recovery.discard(market_identity)
                            await self._append_marker(
                                session_id,
                                "data_quality",
                                "market_data_recovered",
                                reason=(
                                    "required market channel delivered after reconnect; "
                                    "heartbeat is not market-data validity"
                                ),
                                market_channel=channel,
                                coin=coin,
                            )
                    if channel == "subscriptionResponse" and document is not None:
                        identity = _subscription_identity_from_response(document)
                        if identity is not None and identity in expected_identities:
                            acknowledged_subscriptions.add(identity)
                            await self._append_marker(
                                session_id,
                                "subscription",
                                "subscription_acknowledged",
                                subscription_type=identity[0],
                                coin=identity[1],
                            )
                            if (
                                acknowledged_subscriptions == expected_identities
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
                    await self._raise_if_required_market_silent(session_id, last_market)
                    continue
                if stop_task in done:
                    return
                if self._monotonic() >= last_inbound + float(self._config.receive_timeout_seconds):
                    raise TimeoutError("Hyperliquid public receive timed out.")
                await self._raise_if_required_market_silent(session_id, last_market)

                if self._monotonic() >= next_heartbeat:
                    await self._send_heartbeat(connection, session_id)
                    next_heartbeat = self._monotonic() + float(
                        self._config.heartbeat_interval_seconds
                    )
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
        elif channel not in self._expected_inbound_channels() and channel != "session":
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
        payload: dict[str, str | int] = {
            "event": event,
            "transport_profile": HYPERLIQUID_TRANSPORT_PROFILE,
            **fields,
        }
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


def _oldest_silent_identity(
    last_market: dict[tuple[str, str], float],
    now: float,
) -> tuple[str, str] | None:
    if not last_market:
        return None
    return min(last_market, key=lambda identity: (last_market[identity], identity))


def _market_coins(channel: str, document: dict[str, object] | None) -> tuple[str, ...]:
    """Coins carried by one market frame. Control frames and addons return empty."""

    if document is None or channel not in _MARKET_DATA_CHANNELS:
        return ()
    data = document.get("data")
    if channel == "trades":
        if type(data) is not list:
            return ()
        coins: list[str] = []
        for item in data:
            if type(item) is not dict:
                continue
            coin = item.get("coin")
            if type(coin) is str and coin and coin not in coins:
                coins.append(coin)
        return tuple(coins)
    if type(data) is not dict:
        return ()
    coin = data.get("coin")
    if type(coin) is not str or not coin:
        return ()
    return (coin,)


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
    identity = _subscription_identity_from_response(document)
    return None if identity is None else identity[0]


def _subscription_identity_from_response(
    document: dict[str, object],
) -> tuple[str, str] | None:
    data = document.get("data")
    if type(data) is not dict:
        return None
    subscription = data.get("subscription")
    if type(subscription) is not dict:
        return None
    subscription_type = subscription.get("type")
    coin = subscription.get("coin")
    if type(subscription_type) is not str or type(coin) is not str:
        return None
    if subscription_type == "candle":
        interval = subscription.get("interval")
        if type(interval) is not str or not interval:
            return None
        return (subscription_type, f"{coin}:{interval}")
    return (subscription_type, coin)


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
    connection_factory: ConnectionFactory | None = None,
    instrument_plan: HyperliquidRetainedInstrumentPlan | None = None,
    live_status_path: Path | None = None,
    live_run_id: str | None = None,
) -> dict[str, object]:
    """Run the no-credential capture, close Parquet, and build the DuckDB catalog."""

    _require_bounded_duration(duration_seconds)
    writer = ParquetResearchWriter(output_dir, rotation=ParquetRotation())
    collector = HyperliquidRawResearchCollector(
        writer,
        connection_factory=connection_factory,
        instrument_plan=instrument_plan,
    )
    if live_status_path is not None and live_run_id is not None:
        collector.set_live_status_path(live_status_path, live_run_id)
    try:
        await collector.capture_for(duration_seconds, stop_event=stop_event)
    finally:
        await writer.aclose()
    create_research_catalog(output_dir, database_path)
    return build_capture_report(database_path, output_dir)


def data1a_capture_claim(
    *,
    run_id: str,
    duration_seconds: float,
    paths: Data1ARunPaths,
    instrument_plan: HyperliquidRetainedInstrumentPlan | None = None,
) -> dict[str, object]:
    """Create-only start claim for a reconstructable DATA-1A run."""

    duration = _require_bounded_duration(duration_seconds)
    plan = instrument_plan if instrument_plan is not None else build_hyperliquid_retained_plan()
    claim: dict[str, object] = {
        "schema": DATA1A_CLAIM_SCHEMA,
        "state": "STARTED_FAIL_CLOSED",
        "path_contract": DATA1A_PATH_CONTRACT_ID,
        "run_id": run_id,
        "venue": HYPERLIQUID_RESEARCH_VENUE,
        "product": HYPERLIQUID_RESEARCH_PRODUCT,
        "feed": plan.feed_name,
        "websocket_url": HYPERLIQUID_MAINNET_WEBSOCKET_URL,
        "heartbeat_interval_seconds": 45.0,
        "receive_timeout_seconds": 60.0,
        "application_ping": True,
        "heartbeat_is_not_market_data": True,
        "credentialless": True,
        "signing": False,
        "duration_seconds": duration,
        "smoke_duration_seconds": SMOKE_CAPTURE_SECONDS,
        "max_duration_seconds": MAX_CAPTURE_SECONDS,
        "retained": duration > SMOKE_CAPTURE_SECONDS,
        "twenty_four_seven": False,
        "started_coins": list(plan.started_coins),
        "addon_coins": list(item.coin for item in plan.addons),
        "addon_start_policy": plan.addon_start_policy,
        "deferred_addon_coins": list(plan.deferred_addon_coins),
        "include_candles": plan.include_candles,
        "candle_interval": plan.candle_interval if plan.include_candles else None,
        "resume_policy": "never resume or overwrite an existing DATA-1A run directory",
        "raw_dir": paths.raw_dir.as_posix(),
        "database_path": paths.database_path.as_posix(),
    }
    claim.update(claim_profile_fields(duration))
    return stamp_start_metadata(
        claim,
        config_fields={
            "feed": claim["feed"],
            "websocket_url": claim["websocket_url"],
            "heartbeat_interval_seconds": claim["heartbeat_interval_seconds"],
            "receive_timeout_seconds": claim["receive_timeout_seconds"],
            "market_data_stale_seconds": _MARKET_DATA_STALE_SECONDS,
            "started_coins": claim["started_coins"],
        },
    )


def data1a_capture_health(
    *,
    run_id: str,
    duration_seconds: float,
    status: str,
    report: dict[str, object],
) -> dict[str, object]:
    """Create-only end health for a reconstructable DATA-1A run."""

    if status not in {"COMPLETED", "OPERATOR_STOP", "FAILED"}:
        raise ValueError("DATA-1A capture-health status is outside the documented bound.")
    duration = _require_bounded_duration(duration_seconds)
    elapsed = elapsed_from_report(report)
    return attach_observability_health(
        {
            "schema": DATA1A_HEALTH_SCHEMA,
            "kind": "capture-health",
            "path_contract": DATA1A_PATH_CONTRACT_ID,
            "run_id": run_id,
            "status": status,
            "duration_seconds": duration,
            "retained": duration > SMOKE_CAPTURE_SECONDS,
            "twenty_four_seven": False,
            "heartbeat_is_not_market_data": True,
            "credentialless": True,
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
                "Hyperliquid supplies no sequence IDs on these feeds; gaps are "
                "conservative markers.",
                "Public stream only; no API keys, signing, or extra venues.",
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
    connection_factory: ConnectionFactory | None = None,
    instrument_plan: HyperliquidRetainedInstrumentPlan | None = None,
) -> dict[str, object]:
    """Write DATA-1A Parquet/DuckDB to the documented reconstructable path."""

    plan = instrument_plan if instrument_plan is not None else build_hyperliquid_retained_plan()
    paths = data1a_run_paths(artifact_root, run_id)
    if paths.run_dir.exists():
        raise FileExistsError(f"DATA-1A refuses to reuse existing run directory: {paths.run_dir}")
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    paths.raw_dir.mkdir(exist_ok=False)
    log_path = capture_log_path(paths.run_dir, run_id)
    configure_capture_logger(log_path)
    capture_logger().info(
        "data1a start run_id=%s requested_duration_seconds=%s heartbeat_interval_seconds=45 "
        "receive_timeout_seconds=60 log=%s",
        run_id,
        duration_seconds,
        log_path,
    )
    _write_create_only_json(
        paths.capture_claim_path,
        data1a_capture_claim(
            run_id=run_id,
            duration_seconds=duration_seconds,
            paths=paths,
            instrument_plan=plan,
        ),
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
    terminal_error: BaseException | None = None
    try:
        report = await run_bounded_capture(
            output_dir=paths.raw_dir,
            database_path=paths.database_path,
            duration_seconds=duration_seconds,
            stop_event=stop_event,
            connection_factory=connection_factory,
            instrument_plan=plan,
            live_status_path=paths.run_dir / CAPTURE_LIVE_NAME,
            live_run_id=run_id,
        )
        if operator_stop is not None and operator_stop():
            status = "OPERATOR_STOP"
        else:
            status = "COMPLETED"
    except BaseException as error:
        terminal_error = error
        raise
    finally:
        report = {**report, "elapsed_seconds": round(time.monotonic() - started, 6)}
        capture_logger().info(
            "data1a stop run_id=%s status=%s requested_duration_seconds=%s elapsed_seconds=%s",
            run_id,
            status,
            duration_seconds,
            report["elapsed_seconds"],
        )
        if not paths.capture_health_path.exists():
            _write_create_only_json(
                paths.capture_health_path,
                data1a_capture_health(
                    run_id=run_id,
                    duration_seconds=duration_seconds,
                    status=status,
                    report=report,
                ),
            )
        if status == "FAILED":
            emit_capture_operator_alert(
                venue=HYPERLIQUID_RESEARCH_VENUE,
                run_id=run_id,
                status=status,
                error=terminal_error,
            )
    return {
        **report,
        "run_id": run_id,
        "path_contract": DATA1A_PATH_CONTRACT_ID,
        "run_dir": str(paths.run_dir),
        "raw_dir": str(paths.raw_dir),
        "database_path": str(paths.database_path),
        "status": status,
        "twenty_four_seven": False,
    }


def _require_bounded_duration(duration_seconds: object) -> float:
    if type(duration_seconds) not in (int, float):
        raise TypeError("duration_seconds must be a built-in number.")
    duration = float(cast(int | float, duration_seconds))
    if not 1.0 <= duration <= MAX_CAPTURE_SECONDS:
        raise ValueError(f"duration_seconds must be between 1 and {MAX_CAPTURE_SECONDS:g}.")
    return duration


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Public Hyperliquid BTC-PERP exact-raw research capture. "
            "Duration may exceed the historical 600s smoke cap up to 7 days. "
            "Optional candles via --include-candles (official WS candle intervals; "
            "off by default). This is not a 24/7 service."
        )
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--duration-seconds", required=True, type=float)
    parser.add_argument(
        "--addon-coins",
        default="",
        help="Optional Hyperliquid add-on coins (ETH,SOL). Never replaces BTC.",
    )
    parser.add_argument(
        "--enable-addons",
        action="store_true",
        help="Subscribe to --addon-coins. Default Phase A policy is deferred_at_start.",
    )
    parser.add_argument(
        "--include-candles",
        action="store_true",
        help=(
            "Also subscribe the optional Hyperliquid candle channel "
            "(https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/"
            "api/websocket/subscriptions). "
            "Off by default; never implied by trades/bbo/l2Book/activeAssetCtx."
        ),
    )
    parser.add_argument(
        "--candle-interval",
        default=DEFAULT_CANDLE_INTERVAL,
        choices=list(HYPERLIQUID_CANDLE_INTERVALS),
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

    include_candles = bool(args.include_candles)
    candle_interval = cast(str, args.candle_interval)
    if not include_candles and candle_interval != DEFAULT_CANDLE_INTERVAL:
        raise ValueError("--candle-interval requires --include-candles.")
    instrument_plan = build_hyperliquid_retained_plan(
        addon_coins=cast(str, args.addon_coins),
        enable_addons=bool(args.enable_addons),
        include_candles=include_candles,
        candle_interval=candle_interval,
    )
    mode = _resolve_cli_mode(args)
    if mode == "reconstructable":
        return await run_reconstructable_capture(
            artifact_root=cast(Path, args.artifact_root),
            run_id=cast(str, args.run_id),
            duration_seconds=cast(float, args.duration_seconds),
            stop_event=stop_event,
            operator_stop=lambda: operator_stopped,
            instrument_plan=instrument_plan,
        )
    return await run_bounded_capture(
        output_dir=cast(Path, args.output_dir),
        database_path=cast(Path, args.database),
        duration_seconds=cast(float, args.duration_seconds),
        stop_event=stop_event,
        instrument_plan=instrument_plan,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    report = asyncio.run(_run_from_args(args))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
