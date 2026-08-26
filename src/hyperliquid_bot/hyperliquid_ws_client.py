"""Bounded, unauthenticated Hyperliquid public-trades WebSocket collector."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import ssl
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from typing import Final, NoReturn, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidMessage, InvalidStatus

from .contracts import Instrument, MarketEventEnvelope, Venue
from .data_provenance import (
    MAX_RAW_APPLICATION_MESSAGE_BYTES,
    CollectorRunId,
    NormalizationRunId,
    RawMarketDataRecord,
    SanitizedValidationFailure,
    SourceEventId,
    SubscriptionAttemptSnapshot,
    SubscriptionAttemptStatus,
    SubscriptionAttemptTransition,
    ValidationFailureCategory,
)
from .hyperliquid_capture import (
    HYPERLIQUID_MAX_SUBSCRIPTIONS as _HYPERLIQUID_MAX_SUBSCRIPTIONS,
)
from .hyperliquid_capture import (
    HyperliquidSessionAttempts,
    application_message_bytes,
    attempt_snapshot_for_coin,
    build_hyperliquid_capture_plan,
    build_raw_market_data_record,
    control_normalization_outcome,
    decoded_wire_payload_context,
    empty_trade_normalization_outcome,
    frame_normalization_outcome,
    new_hyperliquid_session_attempts,
    preindex_rejection_normalization_outcome,
    raw_event_outcome,
    raw_event_scope_binding,
    subscription_spec_for_coin,
    transition_hyperliquid_attempt,
)
from .hyperliquid_trades import (
    HyperliquidWsTrade,
    hyperliquid_trade_source_event_id,
    normalize_hyperliquid_trade,
)
from .market_data_sinks import (
    NormalizationOutcomeAcceptance,
    NormalizationOutcomeRejected,
    NormalizationOutcomeSink,
    RawRecordAcceptance,
    RawRecordRejected,
    RawRecordSink,
    SinkDestinationId,
    SinkFailureCategory,
)
from .market_event_v3 import (
    FrameNormalizationStatus,
    NormalizationEvidence,
    NormalizationOutcome,
    RawEventDisposition,
    RawEventNormalizationOutcome,
    RawEventNormalizationScopeBinding,
)

HYPERLIQUID_MAINNET_WEBSOCKET_URL: Final = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_MAX_SUBSCRIPTIONS: Final = _HYPERLIQUID_MAX_SUBSCRIPTIONS

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
_MAX_BUILD_TEXT_LENGTH: Final = 256
_RETRYABLE_HTTP_STATUSES: Final = frozenset({500, 502, 503, 504})
_RETRYABLE_CLOSE_CODES: Final = frozenset({1000, 1001, 1005, 1006, 1011, 1012, 1013, 1014})
_TERMINAL_CLOSE_CODES: Final = frozenset({1002, 1003, 1007, 1008, 1009, 1010})
_MAX_QUARANTINED_SINK_OPERATIONS: Final = 3


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


class HyperliquidSinkBoundaryError(HyperliquidCollectorError):
    """A mandatory sink failed with one sanitized bounded category."""

    def __init__(self, category: SinkFailureCategory) -> None:
        if type(category) is not SinkFailureCategory:
            raise TypeError("category must be a SinkFailureCategory.")
        self.category = category
        super().__init__(f"Market-data sink boundary failed ({category.value}).")


class HyperliquidCaptureValidationError(HyperliquidCollectorError):
    """Private capture construction failed and exposed only a bounded category."""

    def __init__(self, failure: SanitizedValidationFailure) -> None:
        if type(failure) is not SanitizedValidationFailure:
            raise TypeError("failure must be a SanitizedValidationFailure.")
        self.failure = failure
        super().__init__(f"Market-data capture validation failed ({failure.category.value}).")


class _RetryableTransportError(HyperliquidCollectorError):
    """An error known to originate at an active WebSocket transport operation."""


class _SinkHardDeadlineExpired(Exception):
    """An argumentless private signal for the collector's sink deadline."""

    def __init__(self) -> None:
        super().__init__()


class _SinkOperationCancelled(Exception):
    """An argumentless private signal for a sink that cancelled itself."""

    def __init__(self) -> None:
        super().__init__()


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
    RAW_SINK_FAILURE = "raw_sink_failure"
    NORMALIZATION_OUTCOME_SINK_FAILURE = "normalization_outcome_sink_failure"
    SINK_CLOSE_FAILURE = "sink_close_failure"
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
    if len(text) > _MAX_BUILD_TEXT_LENGTH:
        raise ValueError(f"{field_name} exceeds the provenance text bound.")
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
    raw_sink_timeout_seconds: float = 1.0
    outcome_sink_timeout_seconds: float = 1.0
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
        if self.websocket_max_size_bytes > MAX_RAW_APPLICATION_MESSAGE_BYTES:
            raise ValueError("websocket_max_size_bytes cannot exceed the Bronze raw-message limit.")
        _require_positive_int(
            self.websocket_max_queue_frames,
            field_name="websocket_max_queue_frames",
        )

        raw_sink_timeout = _require_finite_number(
            self.raw_sink_timeout_seconds,
            field_name="raw_sink_timeout_seconds",
        )
        outcome_sink_timeout = _require_finite_number(
            self.outcome_sink_timeout_seconds,
            field_name="outcome_sink_timeout_seconds",
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
            max(
                heartbeat_interval,
                pong_timeout + 2 * (raw_sink_timeout + outcome_sink_timeout) + publish_timeout,
            )
            + (raw_sink_timeout + outcome_sink_timeout + publish_timeout)
            + send_timeout
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

        object.__setattr__(self, "raw_sink_timeout_seconds", raw_sink_timeout)
        object.__setattr__(self, "outcome_sink_timeout_seconds", outcome_sink_timeout)
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
    accepted_raw_record_count: int
    accepted_normalization_outcome_count: int
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
    last_sink_failure_category: SinkFailureCategory | None
    last_received_time: datetime | None
    last_received_monotonic_ns: int | None


@dataclass(frozen=True, slots=True)
class _SourceEventFingerprint:
    """Bounded digest of replay semantics without retaining source-private values."""

    sha256: str = field(repr=False)


def _source_event_fingerprint(
    trade: HyperliquidWsTrade,
    event: MarketEventEnvelope,
) -> _SourceEventFingerprint:
    """Hash exact semantic replay fields without retaining the canonical preimage."""

    price_numerator, price_denominator = event.event.price.as_integer_ratio()
    quantity_numerator, quantity_denominator = event.event.quantity.as_integer_ratio()
    preimage = json.dumps(
        (
            "hyperliquid-trade-semantic-fingerprint-v1",
            trade.coin,
            trade.side,
            event.event.aggressor_side.value,
            str(price_numerator),
            str(price_denominator),
            str(quantity_numerator),
            str(quantity_denominator),
            event.event_time.isoformat(timespec="microseconds"),
            trade.tid,
            trade.hash,
            trade.users,
            event.instrument.canonical_instrument_id,
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(preimage).hexdigest()
    return _SourceEventFingerprint(digest)


@dataclass(frozen=True, slots=True)
class _TerminalOutcome:
    session_state: SessionState
    failure_category: FailureCategory | None


class _ExportedFailureKind(StrEnum):
    PROTOCOL = "protocol"
    TERMINAL_CLOSE = "terminal_close"
    SOURCE_EVENT_CONFLICT = "source_event_conflict"
    BACKPRESSURE = "backpressure"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    RECEIVE_TIMEOUT = "receive_timeout"
    SUBSCRIPTION_TIMEOUT = "subscription_timeout"
    RUN_STATE = "run_state"
    CONSUMER_STATE = "consumer_state"
    SINK = "sink"
    CAPTURE_VALIDATION = "capture_validation"
    TERMINATED = "terminated"


@dataclass(frozen=True, slots=True)
class _ExportedFailure:
    """Bounded standalone information allowed to cross a public async boundary."""

    kind: _ExportedFailureKind
    sink_category: SinkFailureCategory | None = None
    validation_category: ValidationFailureCategory | None = None
    session_state: SessionState | None = None
    failure_category: FailureCategory | None = None


_MISSING_EXCEPTION_ATTRIBUTE: Final = object()


def _private_exception_attribute(error: Exception, name: str) -> object:
    """Read one exact exception attribute without invoking user-defined accessors."""

    value: object = _MISSING_EXCEPTION_ATTRIBUTE
    try:
        attributes = object.__getattribute__(error, "__dict__")
        if isinstance(attributes, dict):
            value = dict.get(attributes, name, _MISSING_EXCEPTION_ATTRIBUTE)
    except BaseException:
        pass
    return value


def _validation_failure_category(value: object) -> ValidationFailureCategory | None:
    if type(value) is not SanitizedValidationFailure:
        return None
    category: object = _MISSING_EXCEPTION_ATTRIBUTE
    try:
        category = object.__getattribute__(value, "category")
    except BaseException:
        pass
    if type(category) is not ValidationFailureCategory:
        return None
    return category


def _classify_run_failure(error: Exception) -> _ExportedFailure:
    if type(error) is HyperliquidSinkBoundaryError:
        category = _private_exception_attribute(error, "category")
        if type(category) is not SinkFailureCategory:
            return _ExportedFailure(_ExportedFailureKind.RUN_STATE)
        return _ExportedFailure(
            _ExportedFailureKind.SINK,
            sink_category=category,
        )
    if type(error) is HyperliquidCaptureValidationError:
        category = _validation_failure_category(_private_exception_attribute(error, "failure"))
        if category is None:
            return _ExportedFailure(_ExportedFailureKind.RUN_STATE)
        return _ExportedFailure(
            _ExportedFailureKind.CAPTURE_VALIDATION,
            validation_category=category,
        )
    if isinstance(error, HyperliquidSourceEventConflictError):
        return _ExportedFailure(_ExportedFailureKind.SOURCE_EVENT_CONFLICT)
    if isinstance(error, HyperliquidTerminalCloseError):
        return _ExportedFailure(_ExportedFailureKind.TERMINAL_CLOSE)
    if isinstance(error, HyperliquidProtocolError):
        return _ExportedFailure(_ExportedFailureKind.PROTOCOL)
    if isinstance(error, HyperliquidBackpressureError):
        return _ExportedFailure(_ExportedFailureKind.BACKPRESSURE)
    if isinstance(error, HyperliquidHeartbeatTimeoutError):
        return _ExportedFailure(_ExportedFailureKind.HEARTBEAT_TIMEOUT)
    if isinstance(error, HyperliquidReceiveTimeoutError):
        return _ExportedFailure(_ExportedFailureKind.RECEIVE_TIMEOUT)
    if isinstance(error, HyperliquidSubscriptionTimeoutError):
        return _ExportedFailure(_ExportedFailureKind.SUBSCRIPTION_TIMEOUT)
    return _ExportedFailure(_ExportedFailureKind.RUN_STATE)


def _raise_exported_failure(failure: _ExportedFailure) -> NoReturn:
    """Create a fresh public error from bounded values outside a catch block."""

    kind = (
        failure.kind
        if type(failure.kind) is _ExportedFailureKind
        else _ExportedFailureKind.RUN_STATE
    )
    if kind is _ExportedFailureKind.SINK:
        sink_category = (
            failure.sink_category
            if type(failure.sink_category) is SinkFailureCategory
            else SinkFailureCategory.RAW_ACCEPTANCE_INVALID
        )
        raise HyperliquidSinkBoundaryError(sink_category) from None
    if kind is _ExportedFailureKind.CAPTURE_VALIDATION:
        validation_category = (
            failure.validation_category
            if type(failure.validation_category) is ValidationFailureCategory
            else ValidationFailureCategory.LOCAL_VALIDATION_FAILURE
        )
        raise HyperliquidCaptureValidationError(
            SanitizedValidationFailure(validation_category)
        ) from None
    if kind is _ExportedFailureKind.SOURCE_EVENT_CONFLICT:
        raise HyperliquidSourceEventConflictError(
            "A source-event ID was reused for conflicting trade semantics."
        ) from None
    if kind is _ExportedFailureKind.TERMINAL_CLOSE:
        raise HyperliquidTerminalCloseError(
            "WebSocket closed with a terminal protocol condition."
        ) from None
    if kind is _ExportedFailureKind.PROTOCOL:
        raise HyperliquidProtocolError("WebSocket application message was rejected.") from None
    if kind is _ExportedFailureKind.BACKPRESSURE:
        raise HyperliquidBackpressureError(
            "The normalized event queue remained full beyond its limit."
        ) from None
    if kind is _ExportedFailureKind.HEARTBEAT_TIMEOUT:
        raise HyperliquidHeartbeatTimeoutError(
            "Hyperliquid application heartbeat timed out."
        ) from None
    if kind is _ExportedFailureKind.RECEIVE_TIMEOUT:
        raise HyperliquidReceiveTimeoutError(
            "No WebSocket application message was received in time."
        ) from None
    if kind is _ExportedFailureKind.SUBSCRIPTION_TIMEOUT:
        raise HyperliquidSubscriptionTimeoutError(
            "Not all trades subscriptions were acknowledged in time."
        ) from None
    if kind is _ExportedFailureKind.TERMINATED:
        state = (
            failure.session_state
            if type(failure.session_state) is SessionState
            else SessionState.FAILED
        )
        terminal_category = (
            failure.failure_category if type(failure.failure_category) is FailureCategory else None
        )
        raise HyperliquidCollectorTerminatedError(
            session_state=state,
            failure_category=terminal_category,
        ) from None
    if kind is _ExportedFailureKind.CONSUMER_STATE:
        raise HyperliquidCollectorStateError(
            "Only one logical event-batch consumer is supported."
        ) from None
    raise HyperliquidCollectorStateError("A collector instance may only be run once.") from None


async def _export_run_boundary(
    private_operation_factory: Callable[[], Coroutine[object, object, NoReturn]],
) -> NoReturn:
    """Export only a fresh bounded error, never the private collector traceback."""

    failure: _ExportedFailure | None = None
    cancelled = False
    private_operation = private_operation_factory()
    del private_operation_factory
    try:
        await private_operation
    except asyncio.CancelledError:
        cancelled = True
    except Exception as error:
        failure = _classify_run_failure(error)
    del private_operation
    if cancelled:
        raise asyncio.CancelledError from None
    if failure is None:
        failure = _ExportedFailure(_ExportedFailureKind.RUN_STATE)
    _raise_exported_failure(failure)


async def _export_receive_boundary(
    private_operation_factory: Callable[[], Coroutine[object, object, EventBatch]],
) -> EventBatch:
    """Return one batch or export a fresh bounded consumer error."""

    result: EventBatch | None = None
    failure: _ExportedFailure | None = None
    cancelled = False
    private_operation = private_operation_factory()
    del private_operation_factory
    try:
        result = await private_operation
    except asyncio.CancelledError:
        cancelled = True
    except HyperliquidCollectorTerminatedError as error:
        exported_state = _private_exception_attribute(error, "session_state")
        exported_category = _private_exception_attribute(error, "failure_category")
        session_state = (
            exported_state if type(exported_state) is SessionState else SessionState.FAILED
        )
        terminal_category = (
            exported_category if type(exported_category) is FailureCategory else None
        )
        failure = _ExportedFailure(
            _ExportedFailureKind.TERMINATED,
            session_state=session_state,
            failure_category=terminal_category,
        )
    except Exception:
        failure = _ExportedFailure(_ExportedFailureKind.CONSUMER_STATE)
    del private_operation
    if cancelled:
        raise asyncio.CancelledError from None
    if failure is not None:
        _raise_exported_failure(failure)
    if result is None:
        _raise_exported_failure(_ExportedFailure(_ExportedFailureKind.CONSUMER_STATE))
    return result


@dataclass(slots=True)
class _ReceiverFlowState:
    available: asyncio.Event
    block_generation: int = 0


class _PreparedCommitKind(StrEnum):
    GREETING = "greeting"
    PONG = "pong"
    ACKNOWLEDGEMENT = "acknowledgement"
    TRADES = "trades"
    REJECTION = "rejection"


class _PreparedTerminalFailure(StrEnum):
    PROTOCOL = "protocol"
    SOURCE_EVENT_CONFLICT = "source_event_conflict"


class _ReceivedMessageResult(StrEnum):
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    RAW_SINK_FAILURE = "raw_sink_failure"
    OUTCOME_SINK_FAILURE = "outcome_sink_failure"
    PROTOCOL_FAILURE = "protocol_failure"
    SOURCE_EVENT_CONFLICT = "source_event_conflict"
    BACKPRESSURE = "backpressure"
    LOCAL_FAILURE = "local_failure"


@dataclass(frozen=True, slots=True)
class _PreparedMessage:
    outcome: NormalizationOutcome
    commit_kind: _PreparedCommitKind
    acknowledgement_coin: str | None = None
    candidate_cache: OrderedDict[str, _SourceEventFingerprint] | None = field(
        default=None,
        repr=False,
    )
    batch: EventBatch = ()
    duplicate_count: int = 0
    terminal_failure: _PreparedTerminalFailure | None = None


def _raise_received_message_failure(
    result: _ReceivedMessageResult,
    sink_failure: SinkFailureCategory | None,
) -> NoReturn:
    """Raise one bounded error after all payload-bearing worker locals are gone."""

    if result is _ReceivedMessageResult.CANCELLED:
        raise asyncio.CancelledError
    if result is _ReceivedMessageResult.RAW_SINK_FAILURE:
        category = (
            sink_failure
            if sink_failure is not None and sink_failure.value.startswith("raw-")
            else SinkFailureCategory.RAW_ACCEPTANCE_INVALID
        )
        raise HyperliquidSinkBoundaryError(category)
    if result is _ReceivedMessageResult.OUTCOME_SINK_FAILURE:
        category = (
            sink_failure
            if sink_failure is not None and sink_failure.value.startswith("outcome-")
            else SinkFailureCategory.OUTCOME_ACCEPTANCE_INVALID
        )
        raise HyperliquidSinkBoundaryError(category)
    if result is _ReceivedMessageResult.SOURCE_EVENT_CONFLICT:
        raise HyperliquidSourceEventConflictError(
            "A source-event ID was reused for conflicting trade semantics."
        )
    if result is _ReceivedMessageResult.BACKPRESSURE:
        raise HyperliquidBackpressureError(
            "The normalized event queue remained full beyond its limit."
        )
    if result is _ReceivedMessageResult.PROTOCOL_FAILURE:
        raise HyperliquidProtocolError("WebSocket application message was rejected.")
    raise HyperliquidCaptureValidationError(
        SanitizedValidationFailure(ValidationFailureCategory.LOCAL_VALIDATION_FAILURE)
    )


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
    except (ValueError, UnicodeError, RecursionError, OverflowError):
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


def _consume_quarantined_sink_operation(
    registry: set[asyncio.Future[object]] | None,
    operation: asyncio.Future[object],
) -> None:
    """Consume one late sink result privately and release its bounded registry slot."""

    if registry is not None:
        registry.discard(operation)
    if operation.cancelled():
        return
    try:
        operation.exception()
    except BaseException:
        pass


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
    if type(error) is HyperliquidSinkBoundaryError:
        category = _private_exception_attribute(error, "category")
        if type(category) is not SinkFailureCategory:
            return FailureCategory.LOCAL_LIFECYCLE_FAILURE
        if category.value.startswith("raw-"):
            return FailureCategory.RAW_SINK_FAILURE
        return FailureCategory.NORMALIZATION_OUTCOME_SINK_FAILURE
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
            failure = _WireFailureDisposition.TERMINAL_CLOSE
    if failure is _WireFailureDisposition.RETRYABLE:
        raise _RetryableTransportError("WebSocket send failed transiently.")
    if failure is _WireFailureDisposition.TERMINAL_CLOSE:
        raise HyperliquidTerminalCloseError("WebSocket closed with a terminal protocol condition.")


def _declared_sink_destination(sink: object) -> SinkDestinationId | None:
    """Read a sink's public destination without exporting a getter failure."""

    destination: object | None = None
    valid = True
    try:
        destination = sink.destination_id  # type: ignore[attr-defined]
    except Exception:
        valid = False
    if not valid or type(destination) is not SinkDestinationId:
        return None
    return destination


class HyperliquidTradesCollector:
    """Collect public trades into a bounded queue without authentication or storage."""

    def __init__(
        self,
        config: HyperliquidTradesCollectorConfig,
        *,
        collector_run_id: CollectorRunId,
        normalization_run_id: NormalizationRunId,
        normalizer_version: str,
        normalizer_commit: str,
        raw_record_sink: RawRecordSink,
        normalization_outcome_sink: NormalizationOutcomeSink,
        connection_factory: ConnectionFactory = _open_mainnet_connection,
        utc_now: UtcNow = _system_utc_now,
        monotonic_now: MonotonicNow = _system_monotonic_ns,
        sleeper: Sleeper = asyncio.sleep,
        jitter: Jitter = _system_jitter,
        timeout_runner: TimeoutRunner = _asyncio_timeout,
    ) -> None:
        if type(config) is not HyperliquidTradesCollectorConfig:
            raise TypeError("config must be a HyperliquidTradesCollectorConfig.")
        if type(collector_run_id) is not CollectorRunId:
            raise TypeError("collector_run_id must be a CollectorRunId.")
        if type(normalization_run_id) is not NormalizationRunId:
            raise TypeError("normalization_run_id must be a NormalizationRunId.")
        _require_text(normalizer_version, field_name="normalizer_version")
        _require_text(normalizer_commit, field_name="normalizer_commit")
        if cast(object, raw_record_sink) is cast(object, normalization_outcome_sink):
            raise ValueError("raw and normalization-outcome sinks must be distinct objects.")
        if not isinstance(raw_record_sink, RawRecordSink):
            raise TypeError("raw_record_sink must satisfy RawRecordSink.")
        if not isinstance(normalization_outcome_sink, NormalizationOutcomeSink):
            raise TypeError("normalization_outcome_sink must satisfy NormalizationOutcomeSink.")
        raw_destination = _declared_sink_destination(raw_record_sink)
        outcome_destination = _declared_sink_destination(normalization_outcome_sink)
        if raw_destination is None or outcome_destination is None:
            raise TypeError("each sink must declare a valid SinkDestinationId.")
        self._config = config
        self._collector_run_id = collector_run_id
        self._normalization_run_id = normalization_run_id
        self._normalizer_version = normalizer_version
        self._normalizer_commit = normalizer_commit
        self._raw_record_sink = raw_record_sink
        self._normalization_outcome_sink = normalization_outcome_sink
        self._raw_sink_destination_id = raw_destination
        self._outcome_sink_destination_id = outcome_destination
        self._capture_plan = build_hyperliquid_capture_plan(config.instruments)
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
        self._acknowledged_coins: set[str] = set()
        self._session_attempts: HyperliquidSessionAttempts | None = None
        self._attempt_transition_journal: list[SubscriptionAttemptTransition] = []
        self._connection_ordinal = 0
        self._next_ingress_ordinal = 0
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
        self._accepted_raw_record_count = 0
        self._accepted_normalization_outcome_count = 0
        self._emitted_event_count = 0
        self._duplicate_event_count = 0
        self._duplicate_acknowledgement_count = 0
        self._ping_count = 0
        self._pong_count = 0
        self._protocol_error_count = 0
        self._backpressure_error_count = 0
        self._queue_high_water_mark = 0
        self._last_failure_category: FailureCategory | None = None
        self._last_sink_failure_category: SinkFailureCategory | None = None
        self._last_received_time: datetime | None = None
        self._last_received_monotonic_ns: int | None = None
        self._outcome_sink_close_started = False
        self._raw_sink_close_started = False
        self._quarantined_sink_operations: set[asyncio.Future[object]] = set()

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
            accepted_raw_record_count=self._accepted_raw_record_count,
            accepted_normalization_outcome_count=(self._accepted_normalization_outcome_count),
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
            last_sink_failure_category=self._last_sink_failure_category,
            last_received_time=self._last_received_time,
            last_received_monotonic_ns=self._last_received_monotonic_ns,
        )

    def receive_batch(self) -> Coroutine[object, object, EventBatch]:
        """Return a standalone consumer boundary without retaining collector state."""

        return _export_receive_boundary(self._receive_batch_private)

    async def _receive_batch_private(self) -> EventBatch:
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

    def run(self) -> Coroutine[object, object, NoReturn]:
        """Return a standalone producer boundary without exporting private state."""

        return _export_run_boundary(self._run_private)

    async def _run_private(self) -> NoReturn:
        """Run until cancellation or a fatal local/protocol failure."""

        if self._running or self._has_run:
            self._last_failure_category = FailureCategory.LOCAL_LIFECYCLE_FAILURE
            raise HyperliquidCollectorStateError("A collector instance may only be run once.")
        self._running = True
        self._has_run = True
        next_backoff = self._config.backoff_initial_seconds
        terminal_outcome: _TerminalOutcome | None = None

        try:
            while True:
                self._state = SessionState.CONNECTING
                self._session_fully_active = False
                self._session_subscription_delivery_uncertain = False
                self._connection_attempts += 1
                session_attempts = new_hyperliquid_session_attempts(
                    self._capture_plan,
                    collector_run_id=self._collector_run_id,
                    connection_ordinal=self._connection_ordinal,
                )
                self._connection_ordinal += 1
                try:
                    await self._run_connection_attempt(session_attempts)
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
            terminal_outcome = _TerminalOutcome(
                session_state=SessionState.STOPPED,
                failure_category=None,
            )
            raise
        except Exception as exc:
            self._state = SessionState.FAILED
            category = _failure_category(exc)
            self._last_failure_category = category
            terminal_outcome = _TerminalOutcome(
                session_state=SessionState.FAILED,
                failure_category=category,
            )
            raise
        finally:
            try:
                await self._close_owned_sinks()
            finally:
                self._running = False
                if terminal_outcome is not None:
                    self._signal_terminal_outcome(terminal_outcome)

    def _signal_terminal_outcome(self, outcome: _TerminalOutcome) -> None:
        if self._terminal_outcome is not None:
            return
        self._terminal_outcome = outcome
        self._terminal_event.set()
        self._consumer_wakeup.set()

    async def _run_connection_attempt(
        self,
        session_attempts: HyperliquidSessionAttempts,
    ) -> None:
        failure: _WireFailureDisposition | None = None
        bounded_error: HyperliquidCollectorError | None = None
        cancelled = False
        try:
            async with self._connection_factory(self._config) as connection:
                self._successful_connections += 1
                try:
                    await self._run_session(connection, session_attempts)
                except asyncio.CancelledError:
                    cancelled = True
                except HyperliquidCollectorError as exc:
                    bounded_error = exc
        except asyncio.CancelledError:
            cancelled = True
        except Exception as exc:
            if not cancelled:
                failure = _wire_failure_disposition(exc)
                if failure is None:
                    failure = _WireFailureDisposition.TERMINAL_CLOSE
        if cancelled:
            raise asyncio.CancelledError
        if bounded_error is not None:
            raise bounded_error
        if failure is _WireFailureDisposition.RETRYABLE:
            raise _RetryableTransportError("WebSocket connection failed transiently.")
        if failure is _WireFailureDisposition.TERMINAL_CLOSE:
            raise HyperliquidTerminalCloseError(
                "WebSocket closed with a terminal protocol condition."
            )

    async def _run_session(
        self,
        connection: WebSocketConnection,
        session_attempts: HyperliquidSessionAttempts,
    ) -> None:
        self._session_attempts = session_attempts
        self._attempt_transition_journal.clear()
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
            self._acknowledged_coins.clear()
            self._session_attempts = None

    async def _subscribe_and_await_activation(
        self,
        connection: WebSocketConnection,
        activation_event: asyncio.Event,
    ) -> None:
        async def subscribe_and_wait() -> None:
            for coin in self._config.configured_coins:
                self._transition_attempt(coin, SubscriptionAttemptStatus.SEND_STARTED)
                self._session_subscription_delivery_uncertain = True
                await _send_transport_message(connection, _subscription_message(coin))
                snapshot = self._current_attempt_snapshot(coin)
                if snapshot.attempt_status is SubscriptionAttemptStatus.SEND_STARTED:
                    self._transition_attempt(coin, SubscriptionAttemptStatus.SENT)
            await activation_event.wait()

        timed_out = False
        try:
            await self._timeout_runner(
                subscribe_and_wait(),
                self._config.subscription_timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
        if timed_out:
            raise HyperliquidSubscriptionTimeoutError(
                "Not all trades subscriptions were acknowledged in time."
            )

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
                send_timed_out = False
                try:
                    await self._timeout_runner(
                        _send_transport_message(connection, _PING_MESSAGE),
                        self._config.send_timeout_seconds,
                    )
                except TimeoutError:
                    send_timed_out = True
                if send_timed_out:
                    raise HyperliquidHeartbeatTimeoutError(
                        "Hyperliquid application ping could not be sent in time."
                    )
                self._ping_count += 1
                interval_task = asyncio.create_task(
                    self._wait_heartbeat_interval(),
                    name="hyperliquid-heartbeat-interval",
                )
                pong_timed_out = False
                try:
                    await self._timeout_runner(
                        self._await_pong(pong_event, receiver_flow, block_generation),
                        self._config.pong_timeout_seconds
                        + 2
                        * (
                            self._config.raw_sink_timeout_seconds
                            + self._config.outcome_sink_timeout_seconds
                        )
                        + self._config.publish_timeout_seconds,
                    )
                except TimeoutError:
                    pong_timed_out = True
                finally:
                    self._awaiting_pong = False
                if pong_timed_out:
                    raise HyperliquidHeartbeatTimeoutError(
                        "Hyperliquid application pong was not received in time."
                    )
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
            receive_timed_out = False
            try:
                message = await self._timeout_runner(
                    connection.recv(),
                    self._config.receive_timeout_seconds,
                )
            except TimeoutError:
                receive_timed_out = True
            except asyncio.CancelledError:
                raise
            except UnicodeDecodeError:
                failure = _WireFailureDisposition.TERMINAL_CLOSE
            except Exception as exc:
                failure = _wire_failure_disposition(exc)
                if failure is None:
                    failure = _WireFailureDisposition.TERMINAL_CLOSE
            if receive_timed_out:
                raise HyperliquidReceiveTimeoutError(
                    "No WebSocket application message was received in time."
                )
            if failure is _WireFailureDisposition.RETRYABLE:
                raise _RetryableTransportError("WebSocket receive failed transiently.")
            if failure is _WireFailureDisposition.TERMINAL_CLOSE:
                raise HyperliquidTerminalCloseError(
                    "WebSocket closed with a terminal protocol condition."
                )

            receiver_flow.block_generation += 1
            receiver_flow.available.clear()
            try:
                result = await self._process_received_message_privately(
                    message,
                    pong_event=pong_event,
                    activation_event=activation_event,
                )
            finally:
                receiver_flow.available.set()
            del message
            if result is not _ReceivedMessageResult.COMMITTED:
                _raise_received_message_failure(result, self._last_sink_failure_category)
            await asyncio.sleep(0)

    async def _process_received_message_privately(
        self,
        message: object,
        *,
        pong_event: asyncio.Event,
        activation_event: asyncio.Event,
    ) -> _ReceivedMessageResult:
        phase = _ReceivedMessageResult.LOCAL_FAILURE
        result = _ReceivedMessageResult.COMMITTED
        try:
            received_time, received_monotonic_ns = self._capture_receive_clock()
            raw_record = self._construct_raw_record_privately(
                message,
                received_time=received_time,
                received_monotonic_ns=received_monotonic_ns,
            )
            phase = _ReceivedMessageResult.RAW_SINK_FAILURE
            await self._accept_raw_record(raw_record)
            prepared = self._prepare_message_privately(message, raw_record)
            phase = _ReceivedMessageResult.OUTCOME_SINK_FAILURE
            await self._accept_normalization_outcome(prepared.outcome)
            phase = _ReceivedMessageResult.LOCAL_FAILURE
            await self._commit_prepared_message(
                prepared,
                received_time=received_time,
                received_monotonic_ns=received_monotonic_ns,
                pong_event=pong_event,
                activation_event=activation_event,
            )
        except asyncio.CancelledError:
            result = _ReceivedMessageResult.CANCELLED
        except HyperliquidSinkBoundaryError:
            result = phase
        except HyperliquidSourceEventConflictError:
            result = _ReceivedMessageResult.SOURCE_EVENT_CONFLICT
        except HyperliquidBackpressureError:
            result = _ReceivedMessageResult.BACKPRESSURE
        except HyperliquidProtocolError:
            result = _ReceivedMessageResult.PROTOCOL_FAILURE
        except Exception:
            result = _ReceivedMessageResult.LOCAL_FAILURE
        return result

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

    def _construct_raw_record_privately(
        self,
        message: object,
        *,
        received_time: datetime,
        received_monotonic_ns: int,
    ) -> RawMarketDataRecord:
        session_attempts = self._session_attempts
        if session_attempts is None:
            raise HyperliquidCollectorStateError("Capture session state is unavailable.")
        ingress_ordinal = self._next_ingress_ordinal
        self._next_ingress_ordinal += 1
        record: RawMarketDataRecord | None = None
        failure: SanitizedValidationFailure | None = None
        try:
            received_message = application_message_bytes(message)
            record = build_raw_market_data_record(
                self._capture_plan,
                session_attempts,
                collector_run_id=self._collector_run_id,
                ingress_ordinal=ingress_ordinal,
                message=received_message,
                received_time=received_time,
                received_monotonic_ns=received_monotonic_ns,
                collector_version=self._config.collector_version,
                collector_commit=self._config.collector_commit,
            )
        except TypeError:
            failure = SanitizedValidationFailure(ValidationFailureCategory.INVALID_RUNTIME_TYPE)
        except ValueError:
            failure = SanitizedValidationFailure(ValidationFailureCategory.INVALID_VALUE)
        if failure is not None:
            raise HyperliquidCaptureValidationError(failure)
        if record is None:
            raise HyperliquidCaptureValidationError(
                SanitizedValidationFailure(ValidationFailureCategory.LOCAL_VALIDATION_FAILURE)
            )
        return record

    def _quarantine_sink_operation(self, operation: asyncio.Future[object]) -> None:
        """Bound and privately reap an operation that outlived its collector deadline."""

        registry = self._quarantined_sink_operations
        owned_registry: set[asyncio.Future[object]] | None
        if len(registry) >= _MAX_QUARANTINED_SINK_OPERATIONS:
            owned_registry = None
        else:
            registry.add(operation)
            owned_registry = registry
        operation.add_done_callback(partial(_consume_quarantined_sink_operation, owned_registry))

    async def _await_sink_deadline[ResultT](
        self,
        awaitable: Awaitable[ResultT],
        timeout_seconds: float,
        *,
        task_name: str,
    ) -> ResultT:
        """Make a hard fail-stop decision without awaiting child cancellation completion."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        operation = asyncio.ensure_future(awaitable)
        if isinstance(operation, asyncio.Task):
            operation.set_name(task_name)
        try:
            done, _ = await asyncio.wait({operation}, timeout=timeout_seconds)
        except asyncio.CancelledError:
            self._quarantine_sink_operation(cast(asyncio.Future[object], operation))
            operation.cancel()
            raise
        if operation not in done or loop.time() >= deadline:
            self._quarantine_sink_operation(cast(asyncio.Future[object], operation))
            operation.cancel()
            raise _SinkHardDeadlineExpired
        try:
            return operation.result()
        except asyncio.CancelledError:
            raise _SinkOperationCancelled from None

    async def _accept_raw_record(self, raw_record: RawMarketDataRecord) -> None:
        if type(raw_record) is not RawMarketDataRecord:
            raise HyperliquidCaptureValidationError(
                SanitizedValidationFailure(ValidationFailureCategory.INVALID_RUNTIME_TYPE)
            )
        result: object | None = None
        failure: SinkFailureCategory | None = None
        try:
            result = await self._await_sink_deadline(
                self._raw_record_sink.accept(raw_record),
                self._config.raw_sink_timeout_seconds,
                task_name="hyperliquid-raw-sink-accept",
            )
        except asyncio.CancelledError:
            raise
        except RawRecordRejected:
            failure = SinkFailureCategory.RAW_EXPLICIT_REJECTION
        except _SinkHardDeadlineExpired:
            failure = SinkFailureCategory.RAW_ACCEPTANCE_TIMEOUT
        except Exception:
            failure = SinkFailureCategory.RAW_ACCEPTANCE_AMBIGUOUS
        if failure is None and (
            type(result) is not RawRecordAcceptance
            or result.raw_record_id != raw_record.raw_record_id
            or result.full_record_integrity_sha256 != raw_record.full_record_integrity_sha256
            or result.destination_id != self._raw_sink_destination_id
        ):
            failure = SinkFailureCategory.RAW_ACCEPTANCE_INVALID
        if failure is not None:
            self._last_sink_failure_category = failure
            raise HyperliquidSinkBoundaryError(failure)
        self._accepted_raw_record_count += 1
        if self._accepted_normalization_outcome_count > self._accepted_raw_record_count:
            raise HyperliquidCollectorStateError(
                "Normalization-outcome acceptance cannot exceed raw-record acceptance."
            )

    async def _accept_normalization_outcome(self, outcome: NormalizationOutcome) -> None:
        result: object | None = None
        failure: SinkFailureCategory | None = None
        try:
            result = await self._await_sink_deadline(
                self._normalization_outcome_sink.accept(outcome),
                self._config.outcome_sink_timeout_seconds,
                task_name="hyperliquid-normalization-outcome-sink-accept",
            )
        except asyncio.CancelledError:
            raise
        except NormalizationOutcomeRejected:
            failure = SinkFailureCategory.OUTCOME_EXPLICIT_REJECTION
        except _SinkHardDeadlineExpired:
            failure = SinkFailureCategory.OUTCOME_ACCEPTANCE_TIMEOUT
        except Exception:
            failure = SinkFailureCategory.OUTCOME_ACCEPTANCE_AMBIGUOUS
        if failure is None and (
            type(result) is not NormalizationOutcomeAcceptance
            or result.normalization_outcome_id != outcome.normalization_outcome_id
            or result.destination_id != self._outcome_sink_destination_id
        ):
            failure = SinkFailureCategory.OUTCOME_ACCEPTANCE_INVALID
        if failure is not None:
            self._last_sink_failure_category = failure
            raise HyperliquidSinkBoundaryError(failure)
        if self._accepted_normalization_outcome_count >= self._accepted_raw_record_count:
            raise HyperliquidCollectorStateError(
                "Normalization-outcome acceptance requires a prior raw-record acceptance."
            )
        self._accepted_normalization_outcome_count += 1

    def _prepare_message_privately(
        self,
        message: object,
        raw_record: RawMarketDataRecord,
    ) -> _PreparedMessage:
        prepared: _PreparedMessage | None = None
        failure: SanitizedValidationFailure | None = None
        try:
            prepared = self._prepare_message(message, raw_record)
        except TypeError:
            failure = SanitizedValidationFailure(ValidationFailureCategory.INVALID_RUNTIME_TYPE)
        except (LookupError, ValueError):
            failure = SanitizedValidationFailure(ValidationFailureCategory.INVARIANT_VIOLATION)
        except Exception:
            failure = SanitizedValidationFailure(ValidationFailureCategory.LOCAL_VALIDATION_FAILURE)
        if failure is not None:
            fallback: _PreparedMessage | None = None
            fallback_failed = False
            try:
                fallback = self._prepared_preindex_rejection(
                    raw_record,
                    evidence=NormalizationEvidence.LOCAL_CONTRACT_FAILURE,
                )
            except (LookupError, TypeError, ValueError):
                fallback_failed = True
            if not fallback_failed and fallback is not None:
                return fallback
            raise HyperliquidCaptureValidationError(failure)
        if prepared is None:
            fallback = self._prepared_preindex_rejection(
                raw_record,
                evidence=NormalizationEvidence.LOCAL_CONTRACT_FAILURE,
            )
            return fallback
        return prepared

    def _prepare_message(
        self,
        message: object,
        raw_record: RawMarketDataRecord,
    ) -> _PreparedMessage:
        if type(raw_record) is not RawMarketDataRecord:
            raise TypeError("raw_record must be a RawMarketDataRecord.")
        route_failed = False
        routed: RoutedMessage | None = None
        try:
            routed = route_hyperliquid_websocket_message(message)
        except HyperliquidProtocolError:
            route_failed = True
        if route_failed:
            return self._prepared_preindex_rejection(
                raw_record,
                evidence=NormalizationEvidence.PROTOCOL_REJECTION,
            )
        if routed is None:
            raise ValueError("route decision is unavailable.")
        if isinstance(routed, GreetingMessage):
            return _PreparedMessage(
                outcome=self._control_outcome(raw_record),
                commit_kind=_PreparedCommitKind.GREETING,
            )
        if isinstance(routed, PongMessage):
            return _PreparedMessage(
                outcome=self._control_outcome(raw_record),
                commit_kind=_PreparedCommitKind.PONG,
            )
        if isinstance(routed, SubscriptionAcknowledgement):
            if not self._acknowledgement_is_valid(routed.coin):
                return self._prepared_preindex_rejection(
                    raw_record,
                    evidence=NormalizationEvidence.PROVENANCE_MISMATCH,
                )
            return _PreparedMessage(
                outcome=self._control_outcome(raw_record),
                commit_kind=_PreparedCommitKind.ACKNOWLEDGEMENT,
                acknowledgement_coin=routed.coin,
            )
        return self._prepare_trades(routed.frame, raw_record)

    def _control_outcome(self, raw_record: RawMarketDataRecord) -> NormalizationOutcome:
        if type(raw_record) is not RawMarketDataRecord:
            raise TypeError("raw_record must be a RawMarketDataRecord.")
        return control_normalization_outcome(
            raw_record,
            normalization_run_id=self._normalization_run_id,
            normalizer_version=self._normalizer_version,
            normalizer_commit=self._normalizer_commit,
        )

    def _prepared_preindex_rejection(
        self,
        raw_record: RawMarketDataRecord,
        *,
        evidence: NormalizationEvidence,
        is_trade_message: bool = False,
    ) -> _PreparedMessage:
        if type(raw_record) is not RawMarketDataRecord:
            raise TypeError("raw_record must be a RawMarketDataRecord.")
        return _PreparedMessage(
            outcome=preindex_rejection_normalization_outcome(
                self._capture_plan,
                raw_record,
                normalization_run_id=self._normalization_run_id,
                normalizer_version=self._normalizer_version,
                normalizer_commit=self._normalizer_commit,
                evidence=evidence,
            ),
            commit_kind=(
                _PreparedCommitKind.TRADES if is_trade_message else _PreparedCommitKind.REJECTION
            ),
            terminal_failure=_PreparedTerminalFailure.PROTOCOL,
        )

    def _prepare_trades(
        self,
        frame: dict[str, object],
        raw_record: RawMarketDataRecord,
    ) -> _PreparedMessage:
        if type(raw_record) is not RawMarketDataRecord:
            raise TypeError("raw_record must be a RawMarketDataRecord.")
        data = frame.get("data")
        if type(data) is not list:
            return self._prepared_preindex_rejection(
                raw_record,
                evidence=NormalizationEvidence.DECODER_REJECTION,
                is_trade_message=True,
            )
        raw_items = cast(list[object], data)
        if not raw_items:
            return _PreparedMessage(
                outcome=empty_trade_normalization_outcome(
                    raw_record,
                    normalization_run_id=self._normalization_run_id,
                    normalizer_version=self._normalizer_version,
                    normalizer_commit=self._normalizer_commit,
                ),
                commit_kind=_PreparedCommitKind.TRADES,
            )

        coins: list[str] = []
        for item in raw_items:
            if type(item) is not dict:
                return self._prepared_preindex_rejection(
                    raw_record,
                    evidence=NormalizationEvidence.DECODER_REJECTION,
                    is_trade_message=True,
                )
            item_object = cast(dict[object, object], item)
            coin = item_object.get("coin")
            if type(coin) is not str:
                return self._prepared_preindex_rejection(
                    raw_record,
                    evidence=NormalizationEvidence.DECODER_REJECTION,
                    is_trade_message=True,
                )
            if coin not in self._configured_by_coin:
                return self._prepared_preindex_rejection(
                    raw_record,
                    evidence=NormalizationEvidence.UNKNOWN_INSTRUMENT,
                    is_trade_message=True,
                )
            coins.append(coin)

        decoded: list[HyperliquidWsTrade | None] = []
        events: list[MarketEventEnvelope | None] = []
        failures: list[NormalizationEvidence | None] = []
        for item, coin in zip(raw_items, coins, strict=True):
            trade: HyperliquidWsTrade | None = None
            decode_failed = False
            try:
                trade = HyperliquidWsTrade.from_payload(item)
            except (TypeError, ValueError):
                decode_failed = True
            if decode_failed or trade is None:
                decoded.append(None)
                events.append(None)
                failures.append(NormalizationEvidence.DECODER_REJECTION)
                continue

            decoded.append(trade)
            snapshot = attempt_snapshot_for_coin(
                self._capture_plan,
                self._require_session_attempts(),
                coin,
            )
            if snapshot.attempt_status is not SubscriptionAttemptStatus.ACKNOWLEDGED:
                events.append(None)
                failures.append(NormalizationEvidence.PROVENANCE_MISMATCH)
                continue

            event: MarketEventEnvelope | None = None
            normalization_failed = False
            try:
                event = normalize_hyperliquid_trade(
                    trade,
                    instrument_registry=self._config.instruments,
                    received_time=raw_record.received_time,
                    received_monotonic_ns=raw_record.received_monotonic_ns,
                    collector_version=self._config.collector_version,
                    collector_commit=self._config.collector_commit,
                    is_gap=self._sticky_gap,
                )
            except (LookupError, TypeError, ValueError):
                normalization_failed = True
            if normalization_failed or event is None:
                events.append(None)
                failures.append(NormalizationEvidence.LOCAL_CONTRACT_FAILURE)
            else:
                events.append(event)
                failures.append(None)

        decoded_context = decoded_wire_payload_context(
            self._capture_plan,
            raw_record,
            tuple(coins),
        )
        scope_bindings = tuple(
            raw_event_scope_binding(
                self._capture_plan,
                raw_record,
                decoded_context,
                raw_event_index=index,
                coin=coin,
            )
            for index, coin in enumerate(coins)
        )
        source_ids = tuple(
            SourceEventId(hyperliquid_trade_source_event_id(trade)) if trade is not None else None
            for trade in decoded
        )

        if any(failure is not None for failure in failures):
            item_outcomes: list[RawEventNormalizationOutcome] = []
            for index, failure in enumerate(failures):
                disposition = (
                    RawEventDisposition.REJECTED
                    if failure is not None
                    else RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED
                )
                item_outcomes.append(
                    raw_event_outcome(
                        raw_record=raw_record,
                        normalization_run_id=self._normalization_run_id,
                        scope_binding=scope_bindings[index],
                        source_event_id=source_ids[index],
                        disposition=disposition,
                        evidence=(
                            failure
                            if failure is not None
                            else NormalizationEvidence.FRAME_ATOMIC_ABORT
                        ),
                    )
                )
            evidence = tuple(
                sorted(
                    {item.evidence for item in item_outcomes if item.evidence is not None},
                    key=lambda item: item.value,
                )
            )
            return _PreparedMessage(
                outcome=frame_normalization_outcome(
                    raw_record=raw_record,
                    normalization_run_id=self._normalization_run_id,
                    normalizer_version=self._normalizer_version,
                    normalizer_commit=self._normalizer_commit,
                    frame_status=FrameNormalizationStatus.REJECTED_AFTER_INDEXING,
                    decoded_event_count=len(raw_items),
                    raw_event_outcomes=tuple(item_outcomes),
                    evidence=evidence,
                ),
                commit_kind=_PreparedCommitKind.TRADES,
                terminal_failure=_PreparedTerminalFailure.PROTOCOL,
            )

        valid_trades = tuple(cast(HyperliquidWsTrade, trade) for trade in decoded)
        valid_events = tuple(cast(MarketEventEnvelope, event) for event in events)
        return self._prepare_valid_trades(
            raw_record,
            valid_trades,
            valid_events,
            source_ids=tuple(cast(SourceEventId, item) for item in source_ids),
            scope_bindings=scope_bindings,
        )

    def _prepare_valid_trades(
        self,
        raw_record: RawMarketDataRecord,
        trades: tuple[HyperliquidWsTrade, ...],
        events: EventBatch,
        *,
        source_ids: tuple[SourceEventId, ...],
        scope_bindings: tuple[RawEventNormalizationScopeBinding, ...],
    ) -> _PreparedMessage:
        if type(raw_record) is not RawMarketDataRecord:
            raise TypeError("raw_record must be a RawMarketDataRecord.")
        if any(type(item) is not RawEventNormalizationScopeBinding for item in scope_bindings):
            raise TypeError("scope_bindings contain an invalid value.")
        bindings = scope_bindings
        candidate_cache = self._dedup_cache.copy()
        frame_fingerprints: dict[str, _SourceEventFingerprint] = {}
        classifications: list[RawEventDisposition] = []
        new_events: list[MarketEventEnvelope] = []
        duplicate_count = 0
        conflict_found = False

        for trade, event in zip(trades, events, strict=True):
            event_id = event.source_event_id
            fingerprint = _source_event_fingerprint(trade, event)
            known = frame_fingerprints.get(event_id)
            if known is None:
                known = candidate_cache.get(event_id)
                if known is not None:
                    frame_fingerprints[event_id] = known
            if known is not None and known != fingerprint:
                classifications.append(RawEventDisposition.SOURCE_EVENT_CONFLICT)
                conflict_found = True
                continue
            if known is not None:
                classifications.append(RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED)
                duplicate_count += 1
            else:
                classifications.append(RawEventDisposition.MATERIALIZED_NEW)
                new_events.append(event)
                frame_fingerprints[event_id] = fingerprint
            if event_id in candidate_cache:
                candidate_cache.move_to_end(event_id)
            else:
                candidate_cache[event_id] = fingerprint
            while len(candidate_cache) > self._config.dedup_capacity:
                candidate_cache.popitem(last=False)

        if conflict_found:
            item_outcomes = tuple(
                raw_event_outcome(
                    raw_record=raw_record,
                    normalization_run_id=self._normalization_run_id,
                    scope_binding=bindings[index],
                    source_event_id=source_ids[index],
                    disposition=(
                        RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED
                        if disposition is RawEventDisposition.MATERIALIZED_NEW
                        else disposition
                    ),
                    evidence=(
                        NormalizationEvidence.SOURCE_EVENT_CONFLICT
                        if disposition is RawEventDisposition.SOURCE_EVENT_CONFLICT
                        else (
                            NormalizationEvidence.FRAME_ATOMIC_ABORT
                            if disposition is RawEventDisposition.MATERIALIZED_NEW
                            else None
                        )
                    ),
                )
                for index, disposition in enumerate(classifications)
            )
            evidence = tuple(
                sorted(
                    {item.evidence for item in item_outcomes if item.evidence is not None},
                    key=lambda item: item.value,
                )
            )
            return _PreparedMessage(
                outcome=frame_normalization_outcome(
                    raw_record=raw_record,
                    normalization_run_id=self._normalization_run_id,
                    normalizer_version=self._normalizer_version,
                    normalizer_commit=self._normalizer_commit,
                    frame_status=FrameNormalizationStatus.SOURCE_EVENT_CONFLICT,
                    decoded_event_count=len(events),
                    raw_event_outcomes=item_outcomes,
                    evidence=evidence,
                ),
                commit_kind=_PreparedCommitKind.TRADES,
                terminal_failure=_PreparedTerminalFailure.SOURCE_EVENT_CONFLICT,
            )

        item_outcomes = tuple(
            raw_event_outcome(
                raw_record=raw_record,
                normalization_run_id=self._normalization_run_id,
                scope_binding=bindings[index],
                source_event_id=source_ids[index],
                disposition=disposition,
            )
            for index, disposition in enumerate(classifications)
        )
        dispositions = set(classifications)
        if dispositions == {RawEventDisposition.MATERIALIZED_NEW}:
            frame_status = FrameNormalizationStatus.MATERIALIZED
        elif dispositions == {RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED}:
            frame_status = FrameNormalizationStatus.DUPLICATES_ONLY
        else:
            frame_status = FrameNormalizationStatus.MIXED_SUCCESS
        return _PreparedMessage(
            outcome=frame_normalization_outcome(
                raw_record=raw_record,
                normalization_run_id=self._normalization_run_id,
                normalizer_version=self._normalizer_version,
                normalizer_commit=self._normalizer_commit,
                frame_status=frame_status,
                decoded_event_count=len(events),
                raw_event_outcomes=item_outcomes,
            ),
            commit_kind=_PreparedCommitKind.TRADES,
            candidate_cache=candidate_cache,
            batch=tuple(new_events),
            duplicate_count=duplicate_count,
        )

    async def _commit_prepared_message(
        self,
        prepared: _PreparedMessage,
        *,
        received_time: datetime,
        received_monotonic_ns: int,
        pong_event: asyncio.Event,
        activation_event: asyncio.Event,
    ) -> None:
        self._last_received_time = received_time
        self._last_received_monotonic_ns = received_monotonic_ns

        if prepared.commit_kind is _PreparedCommitKind.TRADES:
            self._received_trade_message_count += 1
        elif prepared.commit_kind is not _PreparedCommitKind.REJECTION:
            self._received_control_message_count += 1

        if prepared.terminal_failure is not None:
            self._protocol_error_count += 1
            if prepared.terminal_failure is _PreparedTerminalFailure.SOURCE_EVENT_CONFLICT:
                raise HyperliquidSourceEventConflictError(
                    "A source-event ID was reused for conflicting trade semantics."
                )
            raise HyperliquidProtocolError("WebSocket application message was rejected.")

        if prepared.commit_kind is _PreparedCommitKind.GREETING:
            return
        if prepared.commit_kind is _PreparedCommitKind.PONG:
            self._pong_count += 1
            if self._awaiting_pong:
                pong_event.set()
            return
        if prepared.commit_kind is _PreparedCommitKind.ACKNOWLEDGEMENT:
            coin = prepared.acknowledgement_coin
            if coin is None:
                raise HyperliquidCollectorStateError(
                    "Prepared acknowledgement coin is unavailable."
                )
            self._acknowledge(coin, activation_event)
            return

        if prepared.batch:
            publish_timed_out = False
            try:
                await self._timeout_runner(
                    self._queue.put(prepared.batch),
                    self._config.publish_timeout_seconds,
                )
            except TimeoutError:
                publish_timed_out = True
            if publish_timed_out:
                self._backpressure_error_count += 1
                self._sticky_gap = True
                raise HyperliquidBackpressureError(
                    "The normalized event queue remained full beyond its limit."
                )
            self._queue_high_water_mark = max(self._queue_high_water_mark, self._queue.qsize())
            self._emitted_event_count += len(prepared.batch)
            self._consumer_wakeup.set()

        if prepared.candidate_cache is not None:
            self._dedup_cache = prepared.candidate_cache
        self._duplicate_event_count += prepared.duplicate_count

    def _require_session_attempts(self) -> HyperliquidSessionAttempts:
        if self._session_attempts is None:
            raise HyperliquidCollectorStateError("Subscription attempt state is unavailable.")
        return self._session_attempts

    def _current_attempt_snapshot(self, coin: str) -> SubscriptionAttemptSnapshot:
        return attempt_snapshot_for_coin(
            self._capture_plan,
            self._require_session_attempts(),
            coin,
        )

    def _transition_attempt(
        self,
        coin: str,
        status: SubscriptionAttemptStatus,
    ) -> None:
        spec = subscription_spec_for_coin(self._capture_plan, coin)
        updated_attempts, transition = transition_hyperliquid_attempt(
            self._require_session_attempts(),
            subscription_spec=spec,
            requested_status=status,
        )
        self._session_attempts = updated_attempts
        if transition.previous_status is not transition.new_status:
            self._attempt_transition_journal.append(transition)
            if len(self._attempt_transition_journal) > 3 * len(
                self._capture_plan.subscription_plan.subscription_specs
            ):
                raise HyperliquidCollectorStateError(
                    "Subscription attempt transition journal exceeded its finite bound."
                )

    def _acknowledgement_is_valid(self, coin: str) -> bool:
        if coin not in self._configured_by_coin:
            return False
        snapshot = attempt_snapshot_for_coin(
            self._capture_plan,
            self._require_session_attempts(),
            coin,
        )
        return snapshot.attempt_status in {
            SubscriptionAttemptStatus.SEND_STARTED,
            SubscriptionAttemptStatus.SENT,
            SubscriptionAttemptStatus.ACKNOWLEDGED,
        }

    def _acknowledge(self, coin: str, activation_event: asyncio.Event) -> None:
        snapshot = attempt_snapshot_for_coin(
            self._capture_plan,
            self._require_session_attempts(),
            coin,
        )
        if snapshot.attempt_status is SubscriptionAttemptStatus.ACKNOWLEDGED:
            self._duplicate_acknowledgement_count += 1
            return
        self._transition_attempt(coin, SubscriptionAttemptStatus.ACKNOWLEDGED)
        self._acknowledged_coins.add(coin)
        if len(self._acknowledged_coins) == len(self._configured_by_coin):
            self._session_fully_active = True
            self._state = SessionState.ACTIVE
            activation_event.set()

    async def _close_owned_sinks(self) -> None:
        close_failure: SinkFailureCategory | None = None
        cancelled = False
        if not self._outcome_sink_close_started:
            self._outcome_sink_close_started = True
            close_failure, outcome_cancelled = await self._close_one_sink(
                self._normalization_outcome_sink,
                timeout_seconds=self._config.outcome_sink_timeout_seconds,
                timeout_category=SinkFailureCategory.OUTCOME_CLOSE_TIMEOUT,
                failure_category=SinkFailureCategory.OUTCOME_CLOSE_FAILURE,
            )
            cancelled = cancelled or outcome_cancelled
        if not self._raw_sink_close_started:
            self._raw_sink_close_started = True
            raw_close_failure, raw_cancelled = await self._close_one_sink(
                self._raw_record_sink,
                timeout_seconds=self._config.raw_sink_timeout_seconds,
                timeout_category=SinkFailureCategory.RAW_CLOSE_TIMEOUT,
                failure_category=SinkFailureCategory.RAW_CLOSE_FAILURE,
            )
            cancelled = cancelled or raw_cancelled
            if close_failure is None:
                close_failure = raw_close_failure
        if close_failure is not None:
            if self._last_sink_failure_category is None:
                self._last_sink_failure_category = close_failure
            if self._last_failure_category is None and self._state is not SessionState.STOPPED:
                self._last_failure_category = FailureCategory.SINK_CLOSE_FAILURE
        if cancelled:
            raise asyncio.CancelledError

    async def _close_one_sink(
        self,
        sink: RawRecordSink | NormalizationOutcomeSink,
        *,
        timeout_seconds: float,
        timeout_category: SinkFailureCategory,
        failure_category: SinkFailureCategory,
    ) -> tuple[SinkFailureCategory | None, bool]:
        result: SinkFailureCategory | None = None
        cancelled = False
        try:
            await self._await_sink_deadline(
                sink.aclose(),
                timeout_seconds,
                task_name="hyperliquid-market-data-sink-close",
            )
        except asyncio.CancelledError:
            cancelled = True
        except _SinkHardDeadlineExpired:
            result = timeout_category
        except Exception:
            result = failure_category
        return result, cancelled
