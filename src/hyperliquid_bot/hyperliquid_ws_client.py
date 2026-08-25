"""Bounded, unauthenticated Hyperliquid public-trades WebSocket collector."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import ssl
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, NoReturn, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidMessage, InvalidStatus

from .contracts import AggressorSide, Instrument, MarketEventEnvelope, Venue
from .hyperliquid_trades import (
    HyperliquidWsTrade,
    decode_hyperliquid_trades_frame,
    normalize_hyperliquid_trade,
)

HYPERLIQUID_MAINNET_WEBSOCKET_URL: Final = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_MAX_SUBSCRIPTIONS: Final = 1_000

_TRANSPORT_PRIVACY_LOGGER: Final = logging.Logger(
    "hyperliquid_bot.hyperliquid_ws_transport_privacy",
    level=logging.CRITICAL + 1,
)
_TRANSPORT_PRIVACY_LOGGER.disabled = True
_TRANSPORT_PRIVACY_LOGGER.propagate = False
_TRANSPORT_PRIVACY_LOGGER.addHandler(logging.NullHandler())

_GREETING: Final = "Websocket connection established."
_PING_MESSAGE: Final = '{"method":"ping"}'
_SERVER_IDLE_TIMEOUT_SECONDS: Final = 60.0
_CONNECTIONS_PER_MINUTE_BUDGET: Final = 20.0
_OUTBOUND_MESSAGES_PER_MINUTE_BUDGET: Final = 900.0
_RETRYABLE_HTTP_STATUSES: Final = frozenset({500, 502, 503, 504})
_RETRYABLE_CLOSE_CODES: Final = frozenset({1000, 1001, 1005, 1006, 1011, 1012, 1013, 1014})
_TERMINAL_CLOSE_CODES: Final = frozenset({1002, 1003, 1007, 1008, 1009, 1010})


class HyperliquidCollectorError(RuntimeError):
    """Base class for terminal or transient collector failures."""


class HyperliquidProtocolError(HyperliquidCollectorError):
    """A WebSocket application message violated the strict protocol boundary."""


class HyperliquidTerminalCloseError(HyperliquidProtocolError):
    """A sanitized terminal WebSocket close condition stopped collection."""


class HyperliquidSourceEventConflictError(HyperliquidProtocolError):
    """One source-event ID was reused for different trade semantics."""


class HyperliquidBackpressureError(HyperliquidCollectorError):
    """A complete event batch couldn't be published within the configured limit."""


class HyperliquidHeartbeatTimeoutError(HyperliquidCollectorError):
    """A sent application heartbeat didn't receive a timely application pong."""


class HyperliquidReceiveTimeoutError(HyperliquidCollectorError):
    """No WebSocket application message arrived within the receive limit."""


class HyperliquidSubscriptionTimeoutError(HyperliquidCollectorError):
    """A session didn't acknowledge every configured subscription in time."""


class HyperliquidCollectorStateError(HyperliquidCollectorError):
    """The collector was used in an invalid local lifecycle state."""


class _RetryableTransportError(HyperliquidCollectorError):
    """An error known to originate at an active WebSocket transport operation."""


class SessionState(StrEnum):
    """Externally visible state of the collector lifecycle."""

    IDLE = "idle"
    CONNECTING = "connecting"
    SUBSCRIBING = "subscribing"
    ACTIVE = "active"
    BACKING_OFF = "backing_off"
    STOPPED = "stopped"
    FAILED = "failed"


class FailureCategory(StrEnum):
    """Bounded failure information safe for health and consumer outcomes."""

    TRANSPORT_RECONNECT = "transport_reconnect"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    RECEIVE_TIMEOUT = "receive_timeout"
    SUBSCRIPTION_TIMEOUT = "subscription_timeout"
    TERMINAL_CLOSE_OR_PROTOCOL_VIOLATION = "terminal_close_or_protocol_violation"
    SOURCE_EVENT_CONFLICT = "source_event_conflict"
    BACKPRESSURE = "backpressure"
    LOCAL_LIFECYCLE_FAILURE = "local_lifecycle_failure"


class HyperliquidCollectorTerminatedError(HyperliquidCollectorError):
    """A consumer observed sanitized permanent producer termination."""

    def __init__(
        self,
        *,
        session_state: SessionState,
        failure_category: FailureCategory | None,
    ) -> None:
        self.session_state = session_state
        self.failure_category = failure_category
        if session_state is SessionState.STOPPED:
            message = "Hyperliquid collector stopped."
        else:
            category = (
                failure_category.value
                if failure_category is not None
                else FailureCategory.LOCAL_LIFECYCLE_FAILURE.value
            )
            message = f"Hyperliquid collector terminated ({category})."
        super().__init__(message)


class _WireFailureDisposition(StrEnum):
    RETRYABLE = "retryable"
    TERMINAL_CLOSE = "terminal_close"


@dataclass(frozen=True, slots=True)
class GreetingMessage:
    """The exact harmless greeting recognized by the official Python SDK."""


@dataclass(frozen=True, slots=True)
class SubscriptionAcknowledgement:
    """A validated trades-subscription acknowledgement for one exact coin."""

    coin: str


@dataclass(frozen=True, slots=True)
class PongMessage:
    """A validated Hyperliquid application pong."""


@dataclass(frozen=True, slots=True)
class TradesMessage:
    """A trades frame routed to the existing strict decoder boundary."""

    frame: dict[str, object]


type RoutedMessage = GreetingMessage | SubscriptionAcknowledgement | PongMessage | TradesMessage
type EventBatch = tuple[MarketEventEnvelope, ...]


class WebSocketConnection(Protocol):
    """Small transport surface used by the collector and deterministic fakes."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class TimeoutRunner(Protocol):
    """Injectable bounded wait operation."""

    async def __call__[ResultT](
        self,
        awaitable: Awaitable[ResultT],
        timeout_seconds: float,
    ) -> ResultT: ...


type ConnectionFactory = Callable[
    [HyperliquidTradesCollectorConfig],
    AbstractAsyncContextManager[WebSocketConnection],
]
type UtcNow = Callable[[], datetime]
type MonotonicNow = Callable[[], int]
type Sleeper = Callable[[float], Awaitable[None]]
type Jitter = Callable[[float], float]


def _require_text(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a built-in string.")
    text = value
    if not text or text != text.strip() or not text.isprintable():
        raise ValueError(f"{field_name} must be non-empty printable text without outer whitespace.")
    return text


def _require_positive_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be a built-in integer.")
    integer = value
    if integer <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    return integer


def _require_finite_number(
    value: object,
    *,
    field_name: str,
    allow_zero: bool = False,
) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{field_name} must be a built-in integer or float.")
    number = float(cast(int | float, value))
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"{field_name} must be finite and {qualifier}.")
    return number


@dataclass(frozen=True, slots=True)
class HyperliquidTradesCollectorConfig:
    """Validated immutable configuration for one public mainnet connection."""

    instruments: tuple[Instrument, ...]
    collector_version: str
    collector_commit: str
    queue_capacity: int = 16
    dedup_capacity: int = 10_000
    publish_timeout_seconds: float = 5.0
    subscription_timeout_seconds: float = 20.0
    heartbeat_interval_seconds: float = 45.0
    pong_timeout_seconds: float = 10.0
    receive_timeout_seconds: float = 60.0
    open_timeout_seconds: float = 10.0
    close_timeout_seconds: float = 10.0
    send_timeout_seconds: float = 4.0
    websocket_max_size_bytes: int = 1_048_576
    websocket_max_queue_frames: int = 16
    backoff_initial_seconds: float = 3.0
    backoff_max_seconds: float = 120.0
    backoff_multiplier: float = 2.0
    backoff_jitter_seconds: float = 1.0
    minimum_reconnect_delay_seconds: float = field(init=False)

    def __post_init__(self) -> None:
        if type(self.instruments) is not tuple:
            raise TypeError("instruments must be an immutable tuple.")
        if not self.instruments:
            raise ValueError("instruments must not be empty.")
        if len(self.instruments) > HYPERLIQUID_MAX_SUBSCRIPTIONS:
            raise ValueError("instruments exceeds the official Hyperliquid subscription limit.")

        symbols: set[str] = set()
        ordered_instruments: list[Instrument] = []
        for instrument in self.instruments:
            if type(instrument) is not Instrument:
                raise TypeError("instruments must contain only Instrument values.")
            if instrument.venue is not Venue.HYPERLIQUID:
                raise ValueError("instruments must contain only Hyperliquid markets.")
            symbol = instrument.native_symbol
            if symbol in symbols:
                raise ValueError("instrument native symbols must be unique.")
            symbols.add(symbol)
            ordered_instruments.append(instrument)
        ordered_instruments.sort(key=lambda instrument: instrument.native_symbol)
        object.__setattr__(self, "instruments", tuple(ordered_instruments))

        _require_text(self.collector_version, field_name="collector_version")
        _require_text(self.collector_commit, field_name="collector_commit")
        _require_positive_int(self.queue_capacity, field_name="queue_capacity")
        _require_positive_int(self.dedup_capacity, field_name="dedup_capacity")
        _require_positive_int(
            self.websocket_max_size_bytes,
            field_name="websocket_max_size_bytes",
        )
        _require_positive_int(
            self.websocket_max_queue_frames,
            field_name="websocket_max_queue_frames",
        )

        publish_timeout = _require_finite_number(
            self.publish_timeout_seconds,
            field_name="publish_timeout_seconds",
        )
        subscription_timeout = _require_finite_number(
            self.subscription_timeout_seconds,
            field_name="subscription_timeout_seconds",
        )
        heartbeat_interval = _require_finite_number(
            self.heartbeat_interval_seconds,
            field_name="heartbeat_interval_seconds",
        )
        pong_timeout = _require_finite_number(
            self.pong_timeout_seconds,
            field_name="pong_timeout_seconds",
        )
        receive_timeout = _require_finite_number(
            self.receive_timeout_seconds,
            field_name="receive_timeout_seconds",
        )
        open_timeout = _require_finite_number(
            self.open_timeout_seconds,
            field_name="open_timeout_seconds",
        )
        close_timeout = _require_finite_number(
            self.close_timeout_seconds,
            field_name="close_timeout_seconds",
        )
        send_timeout = _require_finite_number(
            self.send_timeout_seconds,
            field_name="send_timeout_seconds",
        )
        backoff_initial = _require_finite_number(
            self.backoff_initial_seconds,
            field_name="backoff_initial_seconds",
        )
        backoff_max = _require_finite_number(
            self.backoff_max_seconds,
            field_name="backoff_max_seconds",
        )
        backoff_multiplier = _require_finite_number(
            self.backoff_multiplier,
            field_name="backoff_multiplier",
        )
        backoff_jitter = _require_finite_number(
            self.backoff_jitter_seconds,
            field_name="backoff_jitter_seconds",
            allow_zero=True,
        )

        if heartbeat_interval >= _SERVER_IDLE_TIMEOUT_SECONDS:
            raise ValueError("heartbeat_interval_seconds must be less than 60 seconds.")
        if heartbeat_interval < 5.0:
            raise ValueError("heartbeat_interval_seconds would exceed a safe message rate.")
        recurrent_ping_deadline = (
            max(heartbeat_interval, pong_timeout + publish_timeout) + publish_timeout + send_timeout
        )
        if recurrent_ping_deadline >= _SERVER_IDLE_TIMEOUT_SECONDS:
            raise ValueError(
                "the conservative recurrent ping-completion bound must be below 60 seconds."
            )
        if receive_timeout < recurrent_ping_deadline:
            raise ValueError(
                "receive_timeout_seconds must cover the recurrent ping-completion bound."
            )
        if backoff_initial < _SERVER_IDLE_TIMEOUT_SECONDS / _CONNECTIONS_PER_MINUTE_BUDGET:
            raise ValueError("backoff_initial_seconds would create a rapid reconnect loop.")
        if backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be at least one.")

        connection_floor = _SERVER_IDLE_TIMEOUT_SECONDS / _CONNECTIONS_PER_MINUTE_BUDGET
        outbound_floor = (
            len(self.instruments)
            * _SERVER_IDLE_TIMEOUT_SECONDS
            / _OUTBOUND_MESSAGES_PER_MINUTE_BUDGET
        )
        minimum_reconnect_delay = max(connection_floor, outbound_floor)
        if backoff_max < max(backoff_initial, minimum_reconnect_delay):
            raise ValueError("backoff_max_seconds is below the safe initial reconnect delay.")

        object.__setattr__(self, "publish_timeout_seconds", publish_timeout)
        object.__setattr__(self, "subscription_timeout_seconds", subscription_timeout)
        object.__setattr__(self, "heartbeat_interval_seconds", heartbeat_interval)
        object.__setattr__(self, "pong_timeout_seconds", pong_timeout)
        object.__setattr__(self, "receive_timeout_seconds", receive_timeout)
        object.__setattr__(self, "open_timeout_seconds", open_timeout)
        object.__setattr__(self, "close_timeout_seconds", close_timeout)
        object.__setattr__(self, "send_timeout_seconds", send_timeout)
        object.__setattr__(self, "backoff_initial_seconds", backoff_initial)
        object.__setattr__(self, "backoff_max_seconds", backoff_max)
        object.__setattr__(self, "backoff_multiplier", backoff_multiplier)
        object.__setattr__(self, "backoff_jitter_seconds", backoff_jitter)
        object.__setattr__(self, "minimum_reconnect_delay_seconds", minimum_reconnect_delay)

    @property
    def configured_coins(self) -> tuple[str, ...]:
        """Return the stable subscription order derived from exact native symbols."""

        return tuple(instrument.native_symbol for instrument in self.instruments)


@dataclass(frozen=True, slots=True)
class HyperliquidCollectorHealth:
    """Immutable point-in-time view of collector state and bounded resources."""

    session_state: SessionState
    configured_coins: tuple[str, ...]
    acknowledged_coins: tuple[str, ...]
    connection_attempts: int
    successful_connections: int
    reconnect_count: int
    received_control_message_count: int
    received_trade_message_count: int
    emitted_event_count: int
    duplicate_event_count: int
    duplicate_acknowledgement_count: int
    ping_count: int
    pong_count: int
    protocol_error_count: int
    backpressure_error_count: int
    queue_depth: int
    queue_high_water_mark: int
    dedup_cache_size: int
    sticky_gap: bool
    last_failure_category: FailureCategory | None
    last_received_time: datetime | None
    last_received_monotonic_ns: int | None


@dataclass(frozen=True, slots=True)
class _SourceEventFingerprint:
    coin: str
    venue_side: str
    aggressor_side: AggressorSide
    price: Decimal
    quantity: Decimal
    event_time: datetime
    tid: int
    source_transaction_id: str
    users: tuple[str, str]
    canonical_instrument_id: str


@dataclass(frozen=True, slots=True)
class _TerminalOutcome:
    session_state: SessionState
    failure_category: FailureCategory | None


@dataclass(slots=True)
class _ReceiverFlowState:
    available: asyncio.Event
    block_generation: int = 0


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise HyperliquidProtocolError("WebSocket JSON contains duplicate object keys.")
        parsed[key] = value
    return parsed


def _reject_non_finite_constant(_value: str) -> NoReturn:
    raise HyperliquidProtocolError("WebSocket JSON contains a non-finite numeric constant.")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise HyperliquidProtocolError("WebSocket JSON contains a non-finite number.")
    return parsed


def _require_json_object(value: object, *, field_name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise HyperliquidProtocolError(f"{field_name} must be a JSON object.")
    return cast(dict[str, object], value)


def _require_json_text(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise HyperliquidProtocolError(f"{field_name} must be a JSON string.")
    text = value
    if not text or text != text.strip() or not text.isprintable():
        raise HyperliquidProtocolError(
            f"{field_name} must be non-empty printable text without outer whitespace."
        )
    return text


def route_hyperliquid_websocket_message(message: object) -> RoutedMessage:
    """Parse and route one text application message without exposing its raw contents."""

    if type(message) is not str:
        raise HyperliquidProtocolError("WebSocket application messages must be text frames.")
    raw_message = message
    if raw_message == _GREETING:
        return GreetingMessage()

    parse_error: str | None = None
    try:
        decoded = json.loads(
            raw_message,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_non_finite_constant,
            parse_float=_parse_finite_float,
        )
    except HyperliquidProtocolError as exc:
        parse_error = str(exc)
    except (json.JSONDecodeError, UnicodeError):
        parse_error = "WebSocket application message is not valid JSON."
    if parse_error is not None:
        raise HyperliquidProtocolError(parse_error)

    message_object = _require_json_object(decoded, field_name="WebSocket message")
    if "channel" not in message_object:
        raise HyperliquidProtocolError("WebSocket message is missing channel.")
    channel = _require_json_text(message_object["channel"], field_name="channel")

    if channel == "pong":
        return PongMessage()
    if channel == "trades":
        return TradesMessage(frame=message_object)
    if channel != "subscriptionResponse":
        raise HyperliquidProtocolError("WebSocket message has an unsupported channel.")

    if "data" not in message_object:
        raise HyperliquidProtocolError("Subscription acknowledgement is missing data.")
    data = _require_json_object(message_object["data"], field_name="subscription data")
    if data.get("method") != "subscribe" or type(data.get("method")) is not str:
        raise HyperliquidProtocolError("Subscription acknowledgement has an invalid method.")
    if "subscription" not in data:
        raise HyperliquidProtocolError("Subscription acknowledgement is missing subscription.")
    subscription = _require_json_object(
        data["subscription"],
        field_name="subscription acknowledgement subscription",
    )
    if subscription.get("type") != "trades" or type(subscription.get("type")) is not str:
        raise HyperliquidProtocolError("Subscription acknowledgement has an invalid type.")
    if "coin" not in subscription:
        raise HyperliquidProtocolError("Subscription acknowledgement is missing coin.")
    coin = _require_json_text(subscription["coin"], field_name="subscription coin")
    return SubscriptionAcknowledgement(coin=coin)


def _subscription_message(coin: str) -> str:
    return json.dumps(
        {
            "method": "subscribe",
            "subscription": {"type": "trades", "coin": coin},
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


async def _asyncio_timeout[ResultT](
    awaitable: Awaitable[ResultT],
    timeout_seconds: float,
) -> ResultT:
    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


def _system_utc_now() -> datetime:
    return datetime.now(UTC)


def _system_monotonic_ns() -> int:
    return time.monotonic_ns()


def _system_jitter(maximum_seconds: float) -> float:
    fraction = (time.monotonic_ns() % 1_000_003) / 1_000_003
    return fraction * maximum_seconds


@asynccontextmanager
async def _open_mainnet_connection(
    config: HyperliquidTradesCollectorConfig,
) -> AsyncIterator[WebSocketConnection]:
    async with connect(
        HYPERLIQUID_MAINNET_WEBSOCKET_URL,
        logger=_TRANSPORT_PRIVACY_LOGGER,
        proxy=None,
        open_timeout=config.open_timeout_seconds,
        ping_interval=None,
        ping_timeout=None,
        close_timeout=config.close_timeout_seconds,
        max_size=config.websocket_max_size_bytes,
        max_queue=config.websocket_max_queue_frames,
    ) as connection:
        yield connection


def _classify_connection_close(error: ConnectionClosed) -> _WireFailureDisposition:
    closes = tuple(close for close in (error.rcvd, error.sent) if close is not None)
    if not closes:
        return _WireFailureDisposition.RETRYABLE
    codes = tuple(int(close.code) for close in closes)
    if any(code in _TERMINAL_CLOSE_CODES for code in codes):
        return _WireFailureDisposition.TERMINAL_CLOSE
    if all(code in _RETRYABLE_CLOSE_CODES for code in codes):
        return _WireFailureDisposition.RETRYABLE
    return _WireFailureDisposition.TERMINAL_CLOSE


def _wire_failure_disposition(error: Exception) -> _WireFailureDisposition | None:
    if isinstance(error, ssl.SSLCertVerificationError):
        return None
    if isinstance(error, ConnectionClosed):
        return _classify_connection_close(error)
    if isinstance(
        error,
        (
            EOFError,
            OSError,
            TimeoutError,
        ),
    ):
        return _WireFailureDisposition.RETRYABLE
    if isinstance(error, InvalidMessage) and isinstance(error.__cause__, EOFError):
        return _WireFailureDisposition.RETRYABLE
    if isinstance(error, InvalidStatus) and error.response.status_code in _RETRYABLE_HTTP_STATUSES:
        return _WireFailureDisposition.RETRYABLE
    return None


def _is_retryable_wire_error(error: Exception) -> bool:
    return _wire_failure_disposition(error) is _WireFailureDisposition.RETRYABLE


def _is_retryable_transport_error(error: Exception) -> bool:
    return isinstance(
        error,
        (
            _RetryableTransportError,
            HyperliquidHeartbeatTimeoutError,
            HyperliquidReceiveTimeoutError,
            HyperliquidSubscriptionTimeoutError,
        ),
    )


def _failure_category(error: Exception) -> FailureCategory:
    if isinstance(error, HyperliquidSourceEventConflictError):
        return FailureCategory.SOURCE_EVENT_CONFLICT
    if isinstance(error, HyperliquidBackpressureError):
        return FailureCategory.BACKPRESSURE
    if isinstance(error, HyperliquidHeartbeatTimeoutError):
        return FailureCategory.HEARTBEAT_TIMEOUT
    if isinstance(error, HyperliquidReceiveTimeoutError):
        return FailureCategory.RECEIVE_TIMEOUT
    if isinstance(error, HyperliquidSubscriptionTimeoutError):
        return FailureCategory.SUBSCRIPTION_TIMEOUT
    if isinstance(error, (HyperliquidTerminalCloseError, HyperliquidProtocolError)):
        return FailureCategory.TERMINAL_CLOSE_OR_PROTOCOL_VIOLATION
    if isinstance(error, _RetryableTransportError):
        return FailureCategory.TRANSPORT_RECONNECT
    return FailureCategory.LOCAL_LIFECYCLE_FAILURE


async def _send_transport_message(
    connection: WebSocketConnection,
    message: str,
) -> None:
    failure: _WireFailureDisposition | None = None
    try:
        await connection.send(message)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        failure = _wire_failure_disposition(exc)
        if failure is None:
            raise
    if failure is _WireFailureDisposition.RETRYABLE:
        raise _RetryableTransportError("WebSocket send failed transiently.")
    if failure is _WireFailureDisposition.TERMINAL_CLOSE:
        raise HyperliquidTerminalCloseError("WebSocket closed with a terminal protocol condition.")


class HyperliquidTradesCollector:
    """Collect public trades into a bounded queue without authentication or storage."""

    def __init__(
        self,
        config: HyperliquidTradesCollectorConfig,
        *,
        connection_factory: ConnectionFactory = _open_mainnet_connection,
        utc_now: UtcNow = _system_utc_now,
        monotonic_now: MonotonicNow = _system_monotonic_ns,
        sleeper: Sleeper = asyncio.sleep,
        jitter: Jitter = _system_jitter,
        timeout_runner: TimeoutRunner = _asyncio_timeout,
    ) -> None:
        if type(config) is not HyperliquidTradesCollectorConfig:
            raise TypeError("config must be a HyperliquidTradesCollectorConfig.")
        self._config = config
        self._connection_factory = connection_factory
        self._utc_now = utc_now
        self._monotonic_now = monotonic_now
        self._sleeper = sleeper
        self._jitter = jitter
        self._timeout_runner = timeout_runner

        self._queue: asyncio.Queue[EventBatch] = asyncio.Queue(maxsize=config.queue_capacity)
        self._dedup_cache: OrderedDict[str, _SourceEventFingerprint] = OrderedDict()
        self._terminal_event = asyncio.Event()
        self._consumer_wakeup = asyncio.Event()
        self._terminal_outcome: _TerminalOutcome | None = None
        self._consumer_active = False
        self._configured_by_coin = {
            instrument.native_symbol: instrument for instrument in config.instruments
        }
        self._sent_coins: set[str] = set()
        self._acknowledged_coins: set[str] = set()
        self._state = SessionState.IDLE
        self._running = False
        self._has_run = False
        self._sticky_gap = False
        self._session_fully_active = False
        self._session_subscription_delivery_uncertain = False
        self._awaiting_pong = False

        self._connection_attempts = 0
        self._successful_connections = 0
        self._reconnect_count = 0
        self._received_control_message_count = 0
        self._received_trade_message_count = 0
        self._emitted_event_count = 0
        self._duplicate_event_count = 0
        self._duplicate_acknowledgement_count = 0
        self._ping_count = 0
        self._pong_count = 0
        self._protocol_error_count = 0
        self._backpressure_error_count = 0
        self._queue_high_water_mark = 0
        self._last_failure_category: FailureCategory | None = None
        self._last_received_time: datetime | None = None
        self._last_received_monotonic_ns: int | None = None

    @property
    def health(self) -> HyperliquidCollectorHealth:
        """Return an immutable snapshot without performing any I/O."""

        acknowledged = tuple(
            coin for coin in self._config.configured_coins if coin in self._acknowledged_coins
        )
        return HyperliquidCollectorHealth(
            session_state=self._state,
            configured_coins=self._config.configured_coins,
            acknowledged_coins=acknowledged,
            connection_attempts=self._connection_attempts,
            successful_connections=self._successful_connections,
            reconnect_count=self._reconnect_count,
            received_control_message_count=self._received_control_message_count,
            received_trade_message_count=self._received_trade_message_count,
            emitted_event_count=self._emitted_event_count,
            duplicate_event_count=self._duplicate_event_count,
            duplicate_acknowledgement_count=self._duplicate_acknowledgement_count,
            ping_count=self._ping_count,
            pong_count=self._pong_count,
            protocol_error_count=self._protocol_error_count,
            backpressure_error_count=self._backpressure_error_count,
            queue_depth=self._queue.qsize(),
            queue_high_water_mark=self._queue_high_water_mark,
            dedup_cache_size=len(self._dedup_cache),
            sticky_gap=self._sticky_gap,
            last_failure_category=self._last_failure_category,
            last_received_time=self._last_received_time,
            last_received_monotonic_ns=self._last_received_monotonic_ns,
        )

    async def receive_batch(self) -> EventBatch:
        """Wait for the next batch or sanitized producer termination.

        Phase 1A-2B supports one logical consumer at a time.
        """

        if self._consumer_active:
            self._last_failure_category = FailureCategory.LOCAL_LIFECYCLE_FAILURE
            raise HyperliquidCollectorStateError(
                "Only one logical event-batch consumer is supported."
            )
        self._consumer_active = True
        try:
            while True:
                self._consumer_wakeup.clear()
                try:
                    return self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

                if self._terminal_event.is_set():
                    outcome = self._terminal_outcome
                    if outcome is None:
                        raise HyperliquidCollectorStateError(
                            "Collector termination outcome is unavailable."
                        )
                    raise HyperliquidCollectorTerminatedError(
                        session_state=outcome.session_state,
                        failure_category=outcome.failure_category,
                    )
                await self._consumer_wakeup.wait()
        finally:
            self._consumer_active = False

    async def run(self) -> NoReturn:
        """Run until cancellation or a fatal local/protocol failure."""

        if self._running or self._has_run:
            self._last_failure_category = FailureCategory.LOCAL_LIFECYCLE_FAILURE
            raise HyperliquidCollectorStateError("A collector instance may only be run once.")
        self._running = True
        self._has_run = True
        next_backoff = self._config.backoff_initial_seconds

        try:
            while True:
                self._state = SessionState.CONNECTING
                self._session_fully_active = False
                self._session_subscription_delivery_uncertain = False
                self._connection_attempts += 1
                try:
                    await self._run_connection_attempt()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_failure_category = _failure_category(exc)
                    if isinstance(exc, HyperliquidBackpressureError):
                        raise
                    if not _is_retryable_transport_error(exc):
                        raise

                    if self._session_subscription_delivery_uncertain:
                        self._sticky_gap = True
                    if self._session_fully_active:
                        next_backoff = self._config.backoff_initial_seconds

                    self._reconnect_count += 1
                    self._state = SessionState.BACKING_OFF
                    base_delay = max(
                        next_backoff,
                        self._config.minimum_reconnect_delay_seconds,
                    )
                    base_delay = min(base_delay, self._config.backoff_max_seconds)
                    jitter_limit = min(
                        self._config.backoff_jitter_seconds,
                        self._config.backoff_max_seconds - base_delay,
                    )
                    jitter = 0.0 if jitter_limit == 0 else self._jitter(jitter_limit)
                    if type(jitter) not in (int, float):
                        raise TypeError("jitter must return a built-in integer or float.") from None
                    jitter_value = float(cast(int | float, jitter))
                    if (
                        not math.isfinite(jitter_value)
                        or jitter_value < 0
                        or jitter_value > jitter_limit
                    ):
                        raise ValueError("jitter must remain within its requested bound.") from None
                    await self._sleeper(base_delay + jitter_value)
                    next_backoff = min(
                        base_delay * self._config.backoff_multiplier,
                        self._config.backoff_max_seconds,
                    )
        except asyncio.CancelledError:
            self._state = SessionState.STOPPED
            self._signal_terminal_outcome(
                _TerminalOutcome(
                    session_state=SessionState.STOPPED,
                    failure_category=None,
                )
            )
            raise
        except Exception as exc:
            self._state = SessionState.FAILED
            category = _failure_category(exc)
            self._last_failure_category = category
            self._signal_terminal_outcome(
                _TerminalOutcome(
                    session_state=SessionState.FAILED,
                    failure_category=category,
                )
            )
            raise
        finally:
            self._running = False

    def _signal_terminal_outcome(self, outcome: _TerminalOutcome) -> None:
        if self._terminal_outcome is not None:
            return
        self._terminal_outcome = outcome
        self._terminal_event.set()
        self._consumer_wakeup.set()

    async def _run_connection_attempt(self) -> None:
        connection_established = False
        failure: _WireFailureDisposition | None = None
        try:
            async with self._connection_factory(self._config) as connection:
                connection_established = True
                self._successful_connections += 1
                await self._run_session(connection)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = _wire_failure_disposition(exc)
            raw_close = isinstance(exc, ConnectionClosed)
            if failure is None or (connection_established and not raw_close):
                raise
        if failure is _WireFailureDisposition.RETRYABLE:
            raise _RetryableTransportError("WebSocket connection failed transiently.")
        if failure is _WireFailureDisposition.TERMINAL_CLOSE:
            raise HyperliquidTerminalCloseError(
                "WebSocket closed with a terminal protocol condition."
            )

    async def _run_session(self, connection: WebSocketConnection) -> None:
        self._sent_coins.clear()
        self._acknowledged_coins.clear()
        self._awaiting_pong = False
        self._state = SessionState.SUBSCRIBING
        pong_event = asyncio.Event()
        activation_event = asyncio.Event()
        receiver_available = asyncio.Event()
        receiver_available.set()
        receiver_flow = _ReceiverFlowState(available=receiver_available)
        receiver_task = asyncio.create_task(
            self._receive_messages(connection, pong_event, activation_event, receiver_flow),
            name="hyperliquid-trades-receiver",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat(connection, pong_event, receiver_flow),
            name="hyperliquid-application-heartbeat",
        )
        activation_task = asyncio.create_task(
            self._subscribe_and_await_activation(connection, activation_event),
            name="hyperliquid-subscription-activation",
        )
        tasks: set[asyncio.Task[None]] = {
            receiver_task,
            heartbeat_task,
            activation_task,
        }

        try:
            while tasks:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                ordered_tasks = (receiver_task, heartbeat_task, activation_task)
                errors: list[Exception] = []
                permanent_task_ended = False
                for task in ordered_tasks:
                    if task not in done:
                        continue
                    if task.cancelled():
                        raise asyncio.CancelledError
                    error = task.exception()
                    if error is not None:
                        if not isinstance(error, Exception):
                            raise error
                        errors.append(error)
                    elif task is receiver_task or task is heartbeat_task:
                        permanent_task_ended = True
                if errors:
                    fatal_errors = [
                        error for error in errors if not _is_retryable_transport_error(error)
                    ]
                    raise fatal_errors[0] if fatal_errors else errors[0]
                if permanent_task_ended:
                    raise _RetryableTransportError(
                        "A permanent WebSocket session task ended unexpectedly."
                    )
                tasks.difference_update(done)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._awaiting_pong = False
            self._sent_coins.clear()
            self._acknowledged_coins.clear()

    async def _subscribe_and_await_activation(
        self,
        connection: WebSocketConnection,
        activation_event: asyncio.Event,
    ) -> None:
        async def subscribe_and_wait() -> None:
            for coin in self._config.configured_coins:
                self._sent_coins.add(coin)
                self._session_subscription_delivery_uncertain = True
                await _send_transport_message(connection, _subscription_message(coin))
            await activation_event.wait()

        try:
            await self._timeout_runner(
                subscribe_and_wait(),
                self._config.subscription_timeout_seconds,
            )
        except TimeoutError as exc:
            raise HyperliquidSubscriptionTimeoutError(
                "Not all trades subscriptions were acknowledged in time."
            ) from exc

    async def _heartbeat(
        self,
        connection: WebSocketConnection,
        pong_event: asyncio.Event,
        receiver_flow: _ReceiverFlowState,
    ) -> None:
        interval_task: asyncio.Task[None] | None = None
        try:
            await self._sleeper(self._config.heartbeat_interval_seconds)
            while True:
                await receiver_flow.available.wait()
                pong_event.clear()
                self._awaiting_pong = True
                block_generation = receiver_flow.block_generation
                try:
                    await self._timeout_runner(
                        _send_transport_message(connection, _PING_MESSAGE),
                        self._config.send_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise HyperliquidHeartbeatTimeoutError(
                        "Hyperliquid application ping could not be sent in time."
                    ) from exc
                self._ping_count += 1
                interval_task = asyncio.create_task(
                    self._wait_heartbeat_interval(),
                    name="hyperliquid-heartbeat-interval",
                )
                try:
                    await self._timeout_runner(
                        self._await_pong(pong_event, receiver_flow, block_generation),
                        self._config.pong_timeout_seconds + self._config.publish_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise HyperliquidHeartbeatTimeoutError(
                        "Hyperliquid application pong was not received in time."
                    ) from exc
                finally:
                    self._awaiting_pong = False
                await interval_task
                interval_task = None
        finally:
            self._awaiting_pong = False
            if interval_task is not None:
                if not interval_task.done():
                    interval_task.cancel()
                await asyncio.gather(interval_task, return_exceptions=True)

    async def _wait_heartbeat_interval(self) -> None:
        await self._sleeper(self._config.heartbeat_interval_seconds)

    async def _await_pong(
        self,
        pong_event: asyncio.Event,
        receiver_flow: _ReceiverFlowState,
        block_generation: int,
    ) -> None:
        while True:
            try:
                await self._timeout_runner(
                    pong_event.wait(),
                    self._config.pong_timeout_seconds,
                )
                return
            except TimeoutError:
                if (
                    receiver_flow.block_generation == block_generation
                    and receiver_flow.available.is_set()
                ):
                    raise
                await receiver_flow.available.wait()
                block_generation = receiver_flow.block_generation

    async def _receive_messages(
        self,
        connection: WebSocketConnection,
        pong_event: asyncio.Event,
        activation_event: asyncio.Event,
        receiver_flow: _ReceiverFlowState,
    ) -> None:
        while True:
            failure: _WireFailureDisposition | None = None
            try:
                message = await self._timeout_runner(
                    connection.recv(),
                    self._config.receive_timeout_seconds,
                )
            except TimeoutError as exc:
                raise HyperliquidReceiveTimeoutError(
                    "No WebSocket application message was received in time."
                ) from exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = _wire_failure_disposition(exc)
                if failure is None:
                    raise
            if failure is _WireFailureDisposition.RETRYABLE:
                raise _RetryableTransportError("WebSocket receive failed transiently.")
            if failure is _WireFailureDisposition.TERMINAL_CLOSE:
                raise HyperliquidTerminalCloseError(
                    "WebSocket closed with a terminal protocol condition."
                )

            received_time, received_monotonic_ns = self._capture_receive_clock()
            self._last_received_time = received_time
            self._last_received_monotonic_ns = received_monotonic_ns
            try:
                routed = route_hyperliquid_websocket_message(message)
            except HyperliquidProtocolError:
                self._protocol_error_count += 1
                raise

            if isinstance(routed, TradesMessage):
                self._received_trade_message_count += 1
                await self._process_trades(
                    routed.frame,
                    received_time=received_time,
                    received_monotonic_ns=received_monotonic_ns,
                    receiver_flow=receiver_flow,
                )
                continue

            self._received_control_message_count += 1
            if isinstance(routed, GreetingMessage):
                continue
            if isinstance(routed, PongMessage):
                self._pong_count += 1
                if self._awaiting_pong:
                    pong_event.set()
                continue
            self._acknowledge(routed.coin, activation_event)

    def _capture_receive_clock(self) -> tuple[datetime, int]:
        received_time = self._utc_now()
        if type(received_time) is not datetime:
            raise TypeError("utc_now must return a datetime.")
        if received_time.tzinfo is None or received_time.utcoffset() != timedelta(0):
            raise ValueError("utc_now must return a timezone-aware UTC datetime.")
        received_time = received_time.astimezone(UTC)

        received_monotonic_ns = self._monotonic_now()
        if type(received_monotonic_ns) is not int:
            raise TypeError("monotonic_now must return a built-in integer.")
        if received_monotonic_ns < 0:
            raise ValueError("monotonic_now must return a non-negative integer.")
        return received_time, received_monotonic_ns

    def _acknowledge(self, coin: str, activation_event: asyncio.Event) -> None:
        if coin not in self._configured_by_coin:
            self._protocol_error_count += 1
            raise HyperliquidProtocolError(
                "Subscription acknowledgement does not match a configured coin."
            )
        if coin not in self._sent_coins:
            self._protocol_error_count += 1
            raise HyperliquidProtocolError(
                "Subscription acknowledgement arrived before its subscription was sent."
            )
        if coin in self._acknowledged_coins:
            self._duplicate_acknowledgement_count += 1
            return

        self._acknowledged_coins.add(coin)
        if len(self._acknowledged_coins) == len(self._configured_by_coin):
            self._session_fully_active = True
            self._state = SessionState.ACTIVE
            activation_event.set()

    async def _process_trades(
        self,
        frame: dict[str, object],
        *,
        received_time: datetime,
        received_monotonic_ns: int,
        receiver_flow: _ReceiverFlowState,
    ) -> None:
        decode_failed = False
        try:
            trades = decode_hyperliquid_trades_frame(frame)
        except (TypeError, ValueError):
            decode_failed = True
        if decode_failed:
            self._protocol_error_count += 1
            raise HyperliquidProtocolError("Hyperliquid trades frame is invalid.")

        for trade in trades:
            if trade.coin not in self._configured_by_coin:
                self._protocol_error_count += 1
                raise HyperliquidProtocolError("Hyperliquid trade references an unconfigured coin.")
            if trade.coin not in self._acknowledged_coins:
                self._protocol_error_count += 1
                raise HyperliquidProtocolError(
                    "Hyperliquid trade arrived before its subscription acknowledgement."
                )

        normalization_failed = False
        try:
            events = tuple(
                normalize_hyperliquid_trade(
                    trade,
                    instrument_registry=self._config.instruments,
                    received_time=received_time,
                    received_monotonic_ns=received_monotonic_ns,
                    collector_version=self._config.collector_version,
                    collector_commit=self._config.collector_commit,
                    is_gap=self._sticky_gap,
                )
                for trade in trades
            )
        except (LookupError, TypeError, ValueError):
            normalization_failed = True
        if normalization_failed:
            self._protocol_error_count += 1
            raise HyperliquidProtocolError(
                "Hyperliquid trade normalization violated the schema boundary."
            )
        await self._publish_new_events(trades, events, receiver_flow)

    async def _publish_new_events(
        self,
        trades: tuple[HyperliquidWsTrade, ...],
        events: EventBatch,
        receiver_flow: _ReceiverFlowState,
    ) -> None:
        if not events:
            return

        candidate_cache = self._dedup_cache.copy()
        frame_fingerprints: dict[str, _SourceEventFingerprint] = {}
        new_events: list[MarketEventEnvelope] = []
        duplicate_count = 0
        for trade, event in zip(trades, events, strict=True):
            event_id = event.source_event_id
            fingerprint = _SourceEventFingerprint(
                coin=trade.coin,
                venue_side=trade.side,
                aggressor_side=trade.aggressor_side,
                price=event.event.price,
                quantity=event.event.quantity,
                event_time=event.event_time,
                tid=trade.tid,
                source_transaction_id=trade.hash,
                users=trade.users,
                canonical_instrument_id=event.instrument.canonical_instrument_id,
            )
            known_fingerprint = frame_fingerprints.get(event_id)
            if known_fingerprint is None:
                known_fingerprint = candidate_cache.get(event_id)
            if known_fingerprint is not None and known_fingerprint != fingerprint:
                raise HyperliquidSourceEventConflictError(
                    "A source-event ID was reused for conflicting trade semantics."
                )
            if known_fingerprint is not None:
                duplicate_count += 1
            else:
                new_events.append(event)

            frame_fingerprints[event_id] = fingerprint
            if event_id in candidate_cache:
                candidate_cache.move_to_end(event_id)
            else:
                candidate_cache[event_id] = fingerprint
            while len(candidate_cache) > self._config.dedup_capacity:
                candidate_cache.popitem(last=False)

        if new_events:
            batch = tuple(new_events)
            receiver_will_block = self._queue.full()
            if receiver_will_block:
                receiver_flow.block_generation += 1
                receiver_flow.available.clear()
            try:
                try:
                    await self._timeout_runner(
                        self._queue.put(batch),
                        self._config.publish_timeout_seconds,
                    )
                except TimeoutError as exc:
                    self._backpressure_error_count += 1
                    self._sticky_gap = True
                    raise HyperliquidBackpressureError(
                        "The normalized event queue remained full beyond its limit."
                    ) from exc
            finally:
                if receiver_will_block:
                    receiver_flow.available.set()
            self._queue_high_water_mark = max(self._queue_high_water_mark, self._queue.qsize())
            self._emitted_event_count += len(batch)
            self._consumer_wakeup.set()

        self._dedup_cache = candidate_cache
        self._duplicate_event_count += duplicate_count
