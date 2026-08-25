"""Deterministic tests for the bounded Hyperliquid public-trades collector."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import ssl
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

import hyperliquid_bot.hyperliquid_ws_client as websocket_module
from hyperliquid_bot.contracts import Instrument, InstrumentType, Venue
from hyperliquid_bot.hyperliquid_ws_client import (
    HYPERLIQUID_MAINNET_WEBSOCKET_URL,
    HYPERLIQUID_MAX_SUBSCRIPTIONS,
    FailureCategory,
    GreetingMessage,
    HyperliquidBackpressureError,
    HyperliquidCollectorHealth,
    HyperliquidCollectorStateError,
    HyperliquidCollectorTerminatedError,
    HyperliquidProtocolError,
    HyperliquidSourceEventConflictError,
    HyperliquidTerminalCloseError,
    HyperliquidTradesCollector,
    HyperliquidTradesCollectorConfig,
    PongMessage,
    SessionState,
    SubscriptionAcknowledgement,
    TradesMessage,
    WebSocketConnection,
    route_hyperliquid_websocket_message,
)

_BASE_TIME = datetime(2026, 8, 25, 16, 0, tzinfo=UTC)


def _fixed_utc_now() -> datetime:
    return _BASE_TIME


def _fixed_monotonic_now() -> int:
    return 1_000_000


def _failing_utc_now() -> datetime:
    raise OSError("synthetic local clock failure")


def _instrument(symbol: str, *, venue: Venue = Venue.HYPERLIQUID) -> Instrument:
    if symbol == "xyz:XYZ100":
        base_asset = "XYZ100"
    elif symbol == "@107":
        base_asset = "PURR"
    else:
        base_asset = symbol
    return Instrument(
        venue=venue,
        instrument_type=(InstrumentType.SPOT if symbol == "@107" else InstrumentType.PERPETUAL),
        base_asset=base_asset,
        quote_asset="USDC",
        venue_market_id=symbol,
        native_symbol=symbol,
    )


def _config(
    *symbols: str,
    queue_capacity: int = 16,
    dedup_capacity: int = 10_000,
    publish_timeout_seconds: float = 5.0,
    heartbeat_interval_seconds: float = 45.0,
    pong_timeout_seconds: float = 10.0,
    receive_timeout_seconds: float = 60.0,
    send_timeout_seconds: float = 4.0,
    backoff_initial_seconds: float = 3.0,
    backoff_max_seconds: float = 60.0,
    backoff_multiplier: float = 2.0,
    backoff_jitter_seconds: float = 0.0,
) -> HyperliquidTradesCollectorConfig:
    selected_symbols = symbols or ("BTC",)
    return HyperliquidTradesCollectorConfig(
        instruments=tuple(_instrument(symbol) for symbol in selected_symbols),
        collector_version="collector-v2",
        collector_commit="89339aa",
        queue_capacity=queue_capacity,
        dedup_capacity=dedup_capacity,
        publish_timeout_seconds=publish_timeout_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        pong_timeout_seconds=pong_timeout_seconds,
        receive_timeout_seconds=receive_timeout_seconds,
        send_timeout_seconds=send_timeout_seconds,
        backoff_initial_seconds=backoff_initial_seconds,
        backoff_max_seconds=backoff_max_seconds,
        backoff_multiplier=backoff_multiplier,
        backoff_jitter_seconds=backoff_jitter_seconds,
    )


def _trade(
    *,
    coin: str = "BTC",
    side: str = "B",
    price: str = "12345.678900000000000001",
    size: str = "0.000000010000000000",
    time_ms: int = 1_720_000_000_123,
    tid: int = 42,
) -> dict[str, object]:
    return {
        "coin": coin,
        "side": side,
        "px": price,
        "sz": size,
        "hash": f"synthetic-transaction-{coin}-{time_ms}-{tid}",
        "time": time_ms,
        "tid": tid,
        "users": ["synthetic-buyer", "synthetic-seller"],
    }


def _conflicting_trade(trade: dict[str, object], field_name: str) -> dict[str, object]:
    conflicting = dict(trade)
    replacements: dict[str, object] = {
        "px": "99999.000000000000000001",
        "sz": "9.000000000000000001",
        "side": "A" if trade["side"] == "B" else "B",
        "hash": "synthetic-conflicting-transaction",
        "users": ["synthetic-changed-buyer", "synthetic-seller"],
    }
    conflicting[field_name] = replacements[field_name]
    return conflicting


def _trades_message(*trades: dict[str, object], extra: bool = False) -> str:
    frame: dict[str, object] = {"channel": "trades", "data": list(trades)}
    if extra:
        frame["future_addition"] = {"accepted": True}
    return json.dumps(frame, separators=(",", ":"))


def _acknowledgement(coin: str, *, extra: bool = False) -> str:
    subscription: dict[str, object] = {"type": "trades", "coin": coin}
    data: dict[str, object] = {"method": "subscribe", "subscription": subscription}
    frame: dict[str, object] = {"channel": "subscriptionResponse", "data": data}
    if extra:
        subscription["future_subscription_field"] = True
        data["future_data_field"] = "accepted"
        frame["future_top_level_field"] = 1
    return json.dumps(frame, separators=(",", ":"))


def _expected_subscription(coin: str) -> str:
    return json.dumps(
        {"method": "subscribe", "subscription": {"type": "trades", "coin": coin}},
        separators=(",", ":"),
    )


def _closed_connection_error(
    code: int | None,
    *,
    reason: str = "sensitive-close-reason",
) -> ConnectionClosedError | ConnectionClosedOK:
    if code is None:
        return ConnectionClosedError(None, None)
    close = Close(code, reason)
    if code in {1000, 1001, 1005}:
        return ConnectionClosedOK(close, close, True)
    return ConnectionClosedError(close, close, True)


class ScriptedConnection:
    def __init__(self) -> None:
        self.inbound: asyncio.Queue[object] = asyncio.Queue(maxsize=100)
        self.sent: list[str] = []
        self.close_count = 0
        self.recv_calls = 0
        self.concurrent_recv = 0
        self.max_concurrent_recv = 0

    def feed(self, message: str | bytes | BaseException) -> None:
        self.inbound.put_nowait(message)

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        self.recv_calls += 1
        self.concurrent_recv += 1
        self.max_concurrent_recv = max(self.max_concurrent_recv, self.concurrent_recv)
        try:
            item = await self.inbound.get()
        finally:
            self.concurrent_recv -= 1
        if isinstance(item, BaseException):
            raise item
        if type(item) not in (str, bytes):
            raise AssertionError("scripted WebSocket item has an invalid test type")
        return cast(str | bytes, item)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason
        self.close_count += 1


class AckDuringSendDrainConnection(ScriptedConnection):
    async def send(self, message: str) -> None:
        self.sent.append(message)
        self.feed(_acknowledgement("BTC"))
        await asyncio.sleep(0)


class BlockingSendConnection(ScriptedConnection):
    def __init__(self) -> None:
        super().__init__()
        self.send_started = asyncio.Event()

    async def send(self, message: str) -> None:
        self.sent.append(message)
        self.send_started.set()
        await asyncio.Event().wait()


class FailingSendConnection(ScriptedConnection):
    async def send(self, message: str) -> None:
        self.sent.append(message)
        raise EOFError("synthetic ambiguous send failure")


class VirtualClock:
    def __init__(self) -> None:
        self.now = 0.0
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    async def sleep(self, delay: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        sleeper = (self.now + delay, future)
        self._sleepers.append(sleeper)
        try:
            await future
        finally:
            if sleeper in self._sleepers:
                self._sleepers.remove(sleeper)

    def advance_to(self, value: float) -> None:
        if value < self.now:
            raise ValueError("virtual time cannot move backwards")
        self.now = value
        for deadline, future in tuple(self._sleepers):
            if deadline <= self.now and not future.done():
                future.set_result(None)

    @property
    def pending_sleep_count(self) -> int:
        return sum(not future.done() for _, future in self._sleepers)


class VirtualTimeoutRunner:
    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock

    async def __call__[ResultT](
        self,
        awaitable: Awaitable[ResultT],
        timeout_seconds: float,
    ) -> ResultT:
        operation = asyncio.ensure_future(awaitable)
        timeout = asyncio.create_task(self.clock.sleep(timeout_seconds))
        try:
            done, _ = await asyncio.wait(
                {operation, timeout},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation in done:
                return operation.result()
            raise TimeoutError
        finally:
            for task in (operation, timeout):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, timeout, return_exceptions=True)


class TimedPingConnection(ScriptedConnection):
    def __init__(self, clock: VirtualClock, *, ping_send_seconds: float) -> None:
        super().__init__()
        self.clock = clock
        self.ping_send_seconds = ping_send_seconds
        self.ping_start_times: list[float] = []
        self.ping_completion_times: list[float] = []

    async def send(self, message: str) -> None:
        if message == '{"method":"ping"}':
            self.ping_start_times.append(self.clock.now)
            await self.clock.sleep(self.ping_send_seconds)
            self.ping_completion_times.append(self.clock.now)
        self.sent.append(message)


class ScriptedConnectionContext(AbstractAsyncContextManager[WebSocketConnection]):
    def __init__(self, outcome: ScriptedConnection | Exception) -> None:
        self.outcome = outcome

    async def __aenter__(self) -> WebSocketConnection:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None:
        del exc_type, exc_value, traceback
        if isinstance(self.outcome, ScriptedConnection):
            await self.outcome.close()
        return None


class ScriptedConnectionFactory:
    def __init__(self, *outcomes: ScriptedConnection | Exception) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[HyperliquidTradesCollectorConfig] = []

    def __call__(
        self,
        config: HyperliquidTradesCollectorConfig,
    ) -> AbstractAsyncContextManager[WebSocketConnection]:
        self.calls.append(config)
        if not self.outcomes:
            raise AssertionError("unexpected extra connection attempt")
        return ScriptedConnectionContext(self.outcomes.popleft())


class ControlledSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self.waiters: list[asyncio.Event] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        waiter = asyncio.Event()
        self.waiters.append(waiter)
        await waiter.wait()

    def release(self, index: int) -> None:
        self.waiters[index].set()


class CountingClock:
    def __init__(self) -> None:
        self.utc_calls = 0
        self.monotonic_calls = 0

    def utc_now(self) -> datetime:
        value = _BASE_TIME + timedelta(microseconds=self.utc_calls)
        self.utc_calls += 1
        return value

    def monotonic_now(self) -> int:
        value = 1_000_000 + self.monotonic_calls
        self.monotonic_calls += 1
        return value


class FailSelectedTimeout:
    def __init__(self, timeout: float, *, occurrence: int = 1) -> None:
        self.timeout = timeout
        self.occurrence = occurrence
        self.calls = 0

    async def __call__[ResultT](
        self,
        awaitable: Awaitable[ResultT],
        timeout_seconds: float,
    ) -> ResultT:
        if timeout_seconds == self.timeout:
            self.calls += 1
            if self.calls == self.occurrence:
                if inspect.iscoroutine(awaitable):
                    awaitable.close()
                raise TimeoutError
        return await awaitable


class ControlledPongTimeout:
    def __init__(self) -> None:
        self.first_pong_wait_started = asyncio.Event()
        self.expire_first_pong_wait = asyncio.Event()
        self.pong_wait_count = 0

    async def __call__[ResultT](
        self,
        awaitable: Awaitable[ResultT],
        timeout_seconds: float,
    ) -> ResultT:
        if timeout_seconds == 10.0:
            self.pong_wait_count += 1
            if self.pong_wait_count == 1:
                self.first_pong_wait_started.set()
                await self.expire_first_pong_wait.wait()
                if inspect.iscoroutine(awaitable):
                    awaitable.close()
                raise TimeoutError
        return await awaitable


class TimeoutAfterSignal:
    def __init__(self, timeout: float, signal: asyncio.Event) -> None:
        self.timeout = timeout
        self.signal = signal

    async def __call__[ResultT](
        self,
        awaitable: Awaitable[ResultT],
        timeout_seconds: float,
    ) -> ResultT:
        if timeout_seconds != self.timeout:
            return await awaitable
        operation = asyncio.ensure_future(awaitable)
        await self.signal.wait()
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        raise TimeoutError


async def _spin_until(predicate: Callable[[], bool], *, description: str) -> None:
    for _ in range(2_000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(f"condition was not reached: {description}")


async def _start(
    collector: HyperliquidTradesCollector,
    connection: ScriptedConnection,
    expected_subscriptions: int,
) -> asyncio.Task[None]:
    task = asyncio.create_task(collector.run())
    await _spin_until(
        lambda: len(connection.sent) >= expected_subscriptions,
        description="subscription messages",
    )
    return task


async def _activate(
    collector: HyperliquidTradesCollector,
    connection: ScriptedConnection,
) -> None:
    for coin in reversed(collector.health.configured_coins):
        connection.feed(_acknowledgement(coin))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.ACTIVE,
        description="all subscriptions acknowledged",
    )


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_config_is_immutable_non_empty_hyperliquid_only_and_deterministically_ordered() -> None:
    config = _config("xyz:XYZ100", "BTC", "@107")

    assert config.configured_coins == ("@107", "BTC", "xyz:XYZ100")
    assert isinstance(config.instruments, tuple)
    recurrent_bound = (
        max(
            config.heartbeat_interval_seconds,
            config.pong_timeout_seconds + config.publish_timeout_seconds,
        )
        + config.publish_timeout_seconds
        + config.send_timeout_seconds
    )
    assert recurrent_bound == 54.0
    with pytest.raises(FrozenInstanceError):
        config.queue_capacity = 1  # type: ignore[misc]

    with pytest.raises(ValueError, match="must not be empty"):
        HyperliquidTradesCollectorConfig(
            instruments=(),
            collector_version="collector-v2",
            collector_commit="89339aa",
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        HyperliquidTradesCollectorConfig(
            instruments=cast(tuple[Instrument, ...], [_instrument("BTC")]),
            collector_version="collector-v2",
            collector_commit="89339aa",
        )
    with pytest.raises(ValueError, match="only Hyperliquid"):
        HyperliquidTradesCollectorConfig(
            instruments=(_instrument("BTC", venue=Venue.BINANCE),),
            collector_version="collector-v2",
            collector_commit="89339aa",
        )
    with pytest.raises(ValueError, match="unique"):
        HyperliquidTradesCollectorConfig(
            instruments=(_instrument("BTC"), _instrument("BTC")),
            collector_version="collector-v2",
            collector_commit="89339aa",
        )


def test_config_enforces_official_subscription_limit_before_networking() -> None:
    maximum = tuple(_instrument(f"C{index}") for index in range(HYPERLIQUID_MAX_SUBSCRIPTIONS))
    config = HyperliquidTradesCollectorConfig(
        instruments=maximum,
        collector_version="collector-v2",
        collector_commit="89339aa",
    )
    assert len(config.instruments) == 1_000
    assert config.minimum_reconnect_delay_seconds > 66.0

    too_many = (*maximum, _instrument("OVER1000"))
    with pytest.raises(ValueError, match="subscription limit"):
        HyperliquidTradesCollectorConfig(
            instruments=too_many,
            collector_version="collector-v2",
            collector_commit="89339aa",
        )


@pytest.mark.parametrize(
    "field_name,value,expected_error",
    [
        pytest.param("queue_capacity", 0, ValueError, id="zero-queue"),
        pytest.param("queue_capacity", True, TypeError, id="boolean-queue"),
        pytest.param("dedup_capacity", -1, ValueError, id="negative-dedup"),
        pytest.param("publish_timeout_seconds", float("inf"), ValueError, id="infinite-put"),
        pytest.param("publish_timeout_seconds", 11.0, ValueError, id="ping-too-late"),
        pytest.param("heartbeat_interval_seconds", 60.0, ValueError, id="late-heartbeat"),
        pytest.param("heartbeat_interval_seconds", 0.1, ValueError, id="chatty-heartbeat"),
        pytest.param("pong_timeout_seconds", 0.0, ValueError, id="zero-pong"),
        pytest.param("send_timeout_seconds", 0.0, ValueError, id="zero-send"),
        pytest.param("receive_timeout_seconds", 53.99, ValueError, id="short-receive"),
        pytest.param("websocket_max_size_bytes", 0, ValueError, id="zero-max-size"),
        pytest.param("websocket_max_queue_frames", True, TypeError, id="boolean-max-queue"),
        pytest.param("backoff_initial_seconds", 2.99, ValueError, id="rapid-reconnect"),
        pytest.param("backoff_max_seconds", 2.0, ValueError, id="cap-below-initial"),
        pytest.param("backoff_multiplier", 0.5, ValueError, id="shrinking-backoff"),
        pytest.param("backoff_jitter_seconds", -0.1, ValueError, id="negative-jitter"),
    ],
)
def test_config_rejects_invalid_bounds(
    field_name: str,
    value: object,
    expected_error: type[Exception],
) -> None:
    arguments: dict[str, object] = {
        "instruments": (_instrument("BTC"),),
        "collector_version": "collector-v2",
        "collector_commit": "89339aa",
        field_name: value,
    }
    with pytest.raises(expected_error):
        HyperliquidTradesCollectorConfig(**arguments)  # type: ignore[arg-type]


def test_router_accepts_exact_known_messages_and_additive_fields() -> None:
    assert (
        route_hyperliquid_websocket_message("Websocket connection established.")
        == GreetingMessage()
    )
    assert route_hyperliquid_websocket_message(_acknowledgement("xyz:XYZ100", extra=True)) == (
        SubscriptionAcknowledgement("xyz:XYZ100")
    )
    assert (
        route_hyperliquid_websocket_message('{"channel":"pong","future":{"accepted":true}}')
        == PongMessage()
    )
    routed = route_hyperliquid_websocket_message(_trades_message(_trade(), extra=True))
    assert isinstance(routed, TradesMessage)
    assert routed.frame["channel"] == "trades"


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(b'{"channel":"pong"}', id="binary"),
        pytest.param("", id="empty"),
        pytest.param(" Websocket connection established.", id="altered-greeting"),
        pytest.param("[]", id="array-root"),
        pytest.param("null", id="null-root"),
        pytest.param("{}", id="missing-channel"),
        pytest.param('{"channel":1}', id="integer-channel"),
        pytest.param('{"channel":"Pong"}', id="wrong-case-channel"),
        pytest.param('{"channel":"l2Book","data":{}}', id="unknown-channel"),
        pytest.param('{"channel":"pong","channel":"trades"}', id="duplicate-top-key"),
        pytest.param(
            '{"channel":"subscriptionResponse","data":{"method":"subscribe",'
            '"subscription":{"type":"trades","coin":"BTC","coin":"ETH"}}}',
            id="duplicate-nested-key",
        ),
        pytest.param('{"channel":"pong","future":NaN}', id="nan"),
        pytest.param('{"channel":"pong","future":Infinity}', id="infinity"),
        pytest.param('{"channel":"pong","future":1e999}', id="overflowing-number"),
    ],
)
def test_router_fails_closed_on_non_text_malformed_or_unknown_messages(message: object) -> None:
    with pytest.raises(HyperliquidProtocolError):
        route_hyperliquid_websocket_message(message)


def test_malformed_json_error_retains_no_raw_frame_or_user_address() -> None:
    private_marker = "synthetic-user-address-must-not-leak"
    malformed = f'{{"channel":"trades","data":[{{"users":["{private_marker}"]}}]'

    with pytest.raises(HyperliquidProtocolError) as captured:
        route_hyperliquid_websocket_message(malformed)

    assert private_marker not in str(captured.value)
    assert private_marker not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "message",
    [
        pytest.param('{"channel":"subscriptionResponse"}', id="missing-data"),
        pytest.param('{"channel":"subscriptionResponse","data":[]}', id="array-data"),
        pytest.param(
            '{"channel":"subscriptionResponse","data":{"method":"unsubscribe",'
            '"subscription":{"type":"trades","coin":"BTC"}}}',
            id="wrong-method",
        ),
        pytest.param(
            '{"channel":"subscriptionResponse","data":{"method":"subscribe"}}',
            id="missing-subscription",
        ),
        pytest.param(
            '{"channel":"subscriptionResponse","data":{"method":"subscribe","subscription":[]}}',
            id="array-subscription",
        ),
        pytest.param(
            '{"channel":"subscriptionResponse","data":{"method":"subscribe",'
            '"subscription":{"type":"l2Book","coin":"BTC"}}}',
            id="wrong-type",
        ),
        pytest.param(
            '{"channel":"subscriptionResponse","data":{"method":"subscribe",'
            '"subscription":{"type":"trades"}}}',
            id="missing-coin",
        ),
        pytest.param(
            '{"channel":"subscriptionResponse","data":{"method":"subscribe",'
            '"subscription":{"type":"trades","coin":1}}}',
            id="integer-coin",
        ),
    ],
)
def test_router_rejects_malformed_acknowledgements(message: str) -> None:
    with pytest.raises(HyperliquidProtocolError):
        route_hyperliquid_websocket_message(message)


@pytest.mark.asyncio
async def test_one_connection_sends_exact_sorted_subscriptions_and_tracks_unordered_acks() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    config = _config("xyz:XYZ100", "BTC", "@107")
    collector = HyperliquidTradesCollector(
        config,
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 3)

    assert connection.sent == [
        _expected_subscription("@107"),
        _expected_subscription("BTC"),
        _expected_subscription("xyz:XYZ100"),
    ]
    connection.feed("Websocket connection established.")
    connection.feed(_acknowledgement("xyz:XYZ100", extra=True))
    connection.feed(_acknowledgement("@107"))
    connection.feed(_acknowledgement("@107"))
    connection.feed(_acknowledgement("BTC"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.ACTIVE,
        description="unordered acknowledgements",
    )

    health = collector.health
    assert health.configured_coins == ("@107", "BTC", "xyz:XYZ100")
    assert health.acknowledged_coins == health.configured_coins
    assert health.duplicate_acknowledgement_count == 1
    assert health.received_control_message_count == 5
    assert health.connection_attempts == health.successful_connections == 1
    assert connection.max_concurrent_recv == 1

    await _cancel(task)
    assert connection.close_count == 1


@pytest.mark.asyncio
async def test_foreign_ack_is_fatal_and_never_retried() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    factory = ScriptedConnectionFactory(connection)
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=factory,
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed(_acknowledgement("ETH"))

    with pytest.raises(HyperliquidProtocolError, match="configured coin"):
        await task
    assert collector.health.session_state is SessionState.FAILED
    assert collector.health.protocol_error_count == 1
    assert collector.health.reconnect_count == 0
    assert len(factory.calls) == 1
    assert sleeper.delays == [45.0]


@pytest.mark.asyncio
async def test_ack_received_while_subscription_send_drains_is_accepted() -> None:
    connection = AckDuringSendDrainConnection()
    factory = ScriptedConnectionFactory(connection)
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=factory,
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = asyncio.create_task(collector.run())

    await _spin_until(
        lambda: collector.health.session_state is SessionState.ACTIVE,
        description="acknowledgement during send drain",
    )
    assert collector.health.protocol_error_count == 0
    assert collector.health.reconnect_count == 0
    assert collector.health.acknowledged_coins == ("BTC",)
    assert len(factory.calls) == 1
    await _cancel(task)


@pytest.mark.asyncio
async def test_ambiguous_subscription_timeout_is_bounded_and_marks_gap() -> None:
    connection = BlockingSendConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection, ScriptedConnection()),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
        timeout_runner=TimeoutAfterSignal(20.0, connection.send_started),
    )
    task = asyncio.create_task(collector.run())
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="bounded subscription timeout",
    )

    assert connection.sent == [_expected_subscription("BTC")]
    assert collector.health.reconnect_count == 1
    assert collector.health.sticky_gap is True
    assert collector.health.acknowledged_coins == ()
    assert collector.health.last_failure_category is FailureCategory.SUBSCRIPTION_TIMEOUT
    assert sleeper.delays[-1] == 3.0
    await _cancel(task)


@pytest.mark.asyncio
async def test_successful_subscription_send_then_eof_before_ack_marks_gap() -> None:
    first = ScriptedConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(first, ScriptedConnection()),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, first, 1)
    first.feed(EOFError("synthetic pre-ack EOF"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="pre-ack EOF backoff",
    )

    assert collector.health.acknowledged_coins == ()
    assert collector.health.sticky_gap is True
    assert collector.health.last_failure_category is FailureCategory.TRANSPORT_RECONNECT
    await _cancel(task)


@pytest.mark.asyncio
async def test_send_failure_after_attempt_is_conservatively_ambiguous() -> None:
    connection = FailingSendConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection, ScriptedConnection()),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = asyncio.create_task(collector.run())
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="ambiguous send failure backoff",
    )

    assert connection.sent == [_expected_subscription("BTC")]
    assert collector.health.sticky_gap is True
    assert collector.health.last_failure_category is FailureCategory.TRANSPORT_RECONNECT
    await _cancel(task)


@pytest.mark.asyncio
async def test_pre_ack_disconnect_reconnect_emits_first_event_with_gap() -> None:
    first = ScriptedConnection()
    second = ScriptedConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(first, second),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, first, 1)
    first.feed(EOFError("synthetic uncertain coverage"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="uncertain coverage backoff",
    )
    sleeper.release(len(sleeper.waiters) - 1)
    await _spin_until(lambda: second.sent == [_expected_subscription("BTC")], description="retry")
    await _activate(collector, second)
    second.feed(_trades_message(_trade(tid=501)))

    batch = await collector.receive_batch()
    assert len(batch) == 1
    assert batch[0].is_gap is True
    assert collector.health.sticky_gap is True
    await _cancel(task)


@pytest.mark.asyncio
async def test_receive_timeout_after_acknowledgement_reconnects_with_sticky_gap() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection, ScriptedConnection()),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
        timeout_runner=FailSelectedTimeout(60.0, occurrence=2),
    )
    task = await _start(collector, connection, 1)
    connection.feed(_acknowledgement("BTC"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="bounded receive timeout after acknowledgement",
    )

    assert collector.health.reconnect_count == 1
    assert collector.health.sticky_gap is True
    assert collector.health.acknowledged_coins == ()
    assert collector.health.last_failure_category is FailureCategory.RECEIVE_TIMEOUT
    assert sleeper.delays[-1] == 3.0
    await _cancel(task)


@pytest.mark.asyncio
async def test_local_clock_oserror_is_fatal_and_never_mislabeled_as_transport() -> None:
    connection = ScriptedConnection()
    factory = ScriptedConnectionFactory(connection, ScriptedConnection())
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=factory,
        utc_now=_failing_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed(_acknowledgement("BTC"))

    with pytest.raises(OSError, match="local clock failure"):
        await task
    assert collector.health.session_state is SessionState.FAILED
    assert collector.health.reconnect_count == 0
    assert collector.health.last_failure_category is FailureCategory.LOCAL_LIFECYCLE_FAILURE
    assert len(factory.calls) == 1


@pytest.mark.asyncio
async def test_controls_never_reach_decoder_or_output_queue() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed("Websocket connection established.")
    connection.feed(_acknowledgement("BTC"))
    connection.feed('{"channel":"pong"}')
    await _spin_until(
        lambda: collector.health.received_control_message_count == 3,
        description="all controls routed",
    )

    assert collector.health.received_trade_message_count == 0
    assert collector.health.queue_depth == 0
    assert collector.health.pong_count == 1
    await _cancel(task)


@pytest.mark.asyncio
async def test_empty_trades_frame_is_a_valid_noop() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    connection.feed(_trades_message())
    await _spin_until(
        lambda: collector.health.received_trade_message_count == 1,
        description="empty trades frame",
    )

    assert collector.health.queue_depth == 0
    assert collector.health.emitted_event_count == 0
    await _cancel(task)


@pytest.mark.asyncio
async def test_trade_frame_preserves_order_precision_side_tid_and_one_receive_clock_pair() -> None:
    connection = ScriptedConnection()
    clock = CountingClock()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=clock.utc_now,
        monotonic_now=clock.monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    previous_clock_calls = (clock.utc_calls, clock.monotonic_calls)

    connection.feed(
        _trades_message(
            _trade(side="B", tid=42),
            _trade(
                side="A",
                price="0.123456789012345678901",
                size="999999999.000000000000000001",
                time_ms=1_720_000_000_456,
                tid=(1 << 50) - 1,
            ),
        )
    )
    batch = await collector.receive_batch()

    assert len(batch) == 2
    assert [event.event.aggressor_side.value for event in batch] == ["buy", "sell"]
    assert str(batch[0].event.price) == "12345.678900000000000001"
    assert str(batch[1].event.quantity) == "999999999.000000000000000001"
    assert batch[0].received_time == batch[1].received_time
    assert batch[0].received_monotonic_ns == batch[1].received_monotonic_ns
    assert (clock.utc_calls, clock.monotonic_calls) == (
        previous_clock_calls[0] + 1,
        previous_clock_calls[1] + 1,
    )
    assert batch[1].source_event_id.endswith(f",{(1 << 50) - 1}]")
    assert batch[1].source_sequence is None
    assert batch[0].source_transaction_id == "synthetic-transaction-BTC-1720000000123-42"
    assert collector.health.emitted_event_count == 2
    await _cancel(task)


@pytest.mark.asyncio
async def test_valid_mixed_coin_frame_preserves_exact_hip3_and_spot_symbols() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC", "xyz:XYZ100", "@107"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 3)
    await _activate(collector, connection)
    connection.feed(
        _trades_message(
            _trade(coin="BTC", tid=1),
            _trade(coin="xyz:XYZ100", tid=2, time_ms=1_720_000_000_124),
            _trade(coin="@107", tid=3, time_ms=1_720_000_000_125),
        )
    )
    batch = await collector.receive_batch()

    assert [event.instrument.native_symbol for event in batch] == ["BTC", "xyz:XYZ100", "@107"]
    assert [event.instrument.venue_market_id for event in batch] == ["BTC", "xyz:XYZ100", "@107"]
    assert [json.loads(event.source_event_id)[2] for event in batch] == [
        "BTC",
        "xyz:XYZ100",
        "@107",
    ]
    await _cancel(task)


@pytest.mark.asyncio
async def test_acknowledged_coin_can_publish_while_other_subscription_is_pending() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC", "xyz:XYZ100"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 2)
    connection.feed(_acknowledgement("BTC"))
    connection.feed(_trades_message(_trade(coin="BTC")))
    batch = await collector.receive_batch()

    assert [event.instrument.native_symbol for event in batch] == ["BTC"]
    assert collector.health.session_state is SessionState.SUBSCRIBING
    assert collector.health.acknowledged_coins == ("BTC",)
    await _cancel(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unconfigured", "unacknowledged", "invalid-second"])
async def test_entire_frame_fails_atomically_before_any_publish(failure: str) -> None:
    connection = ScriptedConnection()
    symbols = ("BTC", "xyz:XYZ100") if failure != "unconfigured" else ("BTC",)
    collector = HyperliquidTradesCollector(
        _config(*symbols),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, len(symbols))
    connection.feed(_acknowledgement("BTC"))
    await _spin_until(
        lambda: collector.health.acknowledged_coins == ("BTC",),
        description="BTC acknowledgement",
    )

    second = _trade(coin="ETH" if failure == "unconfigured" else "xyz:XYZ100", tid=2)
    if failure == "invalid-second":
        del second["px"]
    connection.feed(_trades_message(_trade(coin="BTC", tid=1), second))

    with pytest.raises(HyperliquidProtocolError) as captured:
        await task
    assert collector.health.queue_depth == 0
    assert collector.health.emitted_event_count == 0
    assert collector.health.protocol_error_count == 1
    if failure == "invalid-second":
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert "synthetic-buyer" not in repr(captured.value)


@pytest.mark.asyncio
async def test_trade_additive_fields_are_tolerated_through_existing_decoder() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    trade = _trade()
    trade["future_trade_field"] = {"ignored": True}
    connection.feed(_trades_message(trade, extra=True))

    assert len(await collector.receive_batch()) == 1
    await _cancel(task)


@pytest.mark.asyncio
async def test_dedup_suppresses_within_and_across_frames_and_is_true_lru() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC", dedup_capacity=2),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    event_a = _trade(tid=1)
    event_b = _trade(tid=2, time_ms=1_720_000_000_124)
    event_c = _trade(tid=3, time_ms=1_720_000_000_125)

    connection.feed(_trades_message(event_a, dict(event_a), event_b))
    assert [json.loads(event.source_event_id)[3] for event in await collector.receive_batch()] == [
        1,
        2,
    ]
    connection.feed(_trades_message(dict(event_a)))
    await _spin_until(
        lambda: collector.health.received_trade_message_count == 2,
        description="cross-frame duplicate",
    )
    connection.feed(_trades_message(event_c))
    assert json.loads((await collector.receive_batch())[0].source_event_id)[3] == 3
    connection.feed(_trades_message(dict(event_b)))
    assert json.loads((await collector.receive_batch())[0].source_event_id)[3] == 2

    health = collector.health
    assert health.duplicate_event_count == 2
    assert health.emitted_event_count == 4
    assert health.dedup_cache_size == 2
    await _cancel(task)


@pytest.mark.asyncio
async def test_conflicting_duplicate_inside_frame_is_atomic_even_after_candidate_eviction() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC", dedup_capacity=1),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    event_a = _trade(tid=601)
    event_b = _trade(tid=602, time_ms=1_720_000_000_124)
    conflict_a = _conflicting_trade(event_a, "hash")
    connection.feed(_trades_message(event_a, event_b, conflict_a))

    with pytest.raises(HyperliquidSourceEventConflictError) as captured:
        await task
    health = collector.health
    assert health.queue_depth == 0
    assert health.dedup_cache_size == 0
    assert health.emitted_event_count == 0
    assert health.duplicate_event_count == 0
    assert health.last_failure_category is FailureCategory.SOURCE_EVENT_CONFLICT
    assert "synthetic-conflicting-transaction" not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", ["px", "sz", "side", "hash", "users"])
async def test_conflict_against_retained_id_preserves_cache_queue_and_counters(
    field_name: str,
) -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    original = _trade(tid=610)
    connection.feed(_trades_message(original))
    assert len(await collector.receive_batch()) == 1
    before = collector.health

    connection.feed(_trades_message(_conflicting_trade(original, field_name)))
    with pytest.raises(HyperliquidSourceEventConflictError):
        await task
    after = collector.health

    assert after.queue_depth == before.queue_depth == 0
    assert after.dedup_cache_size == before.dedup_cache_size == 1
    assert after.emitted_event_count == before.emitted_event_count == 1
    assert after.duplicate_event_count == before.duplicate_event_count == 0
    assert after.last_failure_category is FailureCategory.SOURCE_EVENT_CONFLICT
    with pytest.raises(HyperliquidCollectorTerminatedError) as terminated:
        await collector.receive_batch()
    assert terminated.value.failure_category is FailureCategory.SOURCE_EVENT_CONFLICT


@pytest.mark.asyncio
async def test_exact_replay_ignores_receipt_and_gap_metadata_but_refreshes_lru() -> None:
    first = ScriptedConnection()
    second = ScriptedConnection()
    sleeper = ControlledSleeper()
    clock = CountingClock()
    collector = HyperliquidTradesCollector(
        _config("BTC", dedup_capacity=2),
        connection_factory=ScriptedConnectionFactory(first, second),
        utc_now=clock.utc_now,
        monotonic_now=clock.monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, first, 1)
    await _activate(collector, first)
    replayed = _trade(tid=620)
    first.feed(_trades_message(replayed))
    first_event = (await collector.receive_batch())[0]
    calls_after_first = clock.utc_calls
    first.feed(EOFError("synthetic overlap disconnect"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="overlap reconnect backoff",
    )
    sleeper.release(len(sleeper.waiters) - 1)
    await _spin_until(lambda: len(second.sent) == 1, description="overlap resubscription")
    await _activate(collector, second)
    second.feed(_trades_message(dict(replayed)))
    await _spin_until(
        lambda: collector.health.duplicate_event_count == 1,
        description="exact replay suppression",
    )

    assert clock.utc_calls > calls_after_first
    assert first_event.is_gap is False
    assert collector.health.sticky_gap is True
    assert collector.health.queue_depth == 0
    assert collector.health.dedup_cache_size == 1
    await _cancel(task)


@pytest.mark.asyncio
async def test_same_tid_at_another_time_or_coin_remains_distinct() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC", "xyz:XYZ100"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 2)
    await _activate(collector, connection)
    connection.feed(
        _trades_message(
            _trade(coin="BTC", tid=630, time_ms=1_720_000_000_123),
            _trade(coin="BTC", tid=630, time_ms=1_720_000_000_124),
            _trade(coin="xyz:XYZ100", tid=630, time_ms=1_720_000_000_123),
        )
    )

    batch = await collector.receive_batch()
    assert len(batch) == 3
    assert len({event.source_event_id for event in batch}) == 3
    assert collector.health.duplicate_event_count == 0
    await _cancel(task)


@pytest.mark.asyncio
async def test_dedup_cache_persists_across_reconnect_and_gap_becomes_sticky() -> None:
    first = ScriptedConnection()
    second = ScriptedConnection()
    sleeper = ControlledSleeper()
    factory = ScriptedConnectionFactory(first, second)
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=factory,
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, first, 1)
    await _activate(collector, first)
    replayed = _trade(tid=8)
    first.feed(_trades_message(replayed))
    assert (await collector.receive_batch())[0].is_gap is False
    first.feed(EOFError("synthetic transport EOF"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="active disconnect backoff",
    )
    assert sleeper.delays[-1] == 3.0
    assert collector.health.sticky_gap is True
    assert collector.health.acknowledged_coins == ()
    sleeper.release(len(sleeper.waiters) - 1)
    await _spin_until(lambda: len(second.sent) == 1, description="resubscription")
    assert second.sent == [_expected_subscription("BTC")]
    await _activate(collector, second)
    second.feed(
        _trades_message(
            dict(replayed),
            _trade(tid=9, time_ms=1_720_000_000_124),
        )
    )
    batch = await collector.receive_batch()

    assert len(batch) == 1
    assert json.loads(batch[0].source_event_id)[3] == 9
    assert batch[0].is_gap is True
    assert collector.health.duplicate_event_count == 1
    assert collector.health.reconnect_count == 1
    await _cancel(task)


@pytest.mark.asyncio
async def test_reconnect_resubscribes_every_exact_coin_once_in_deterministic_order() -> None:
    first = ScriptedConnection()
    second = ScriptedConnection()
    sleeper = ControlledSleeper()
    config = _config("xyz:XYZ100", "BTC", "@107")
    collector = HyperliquidTradesCollector(
        config,
        connection_factory=ScriptedConnectionFactory(first, second),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, first, 3)
    await _activate(collector, first)
    first.feed(OSError("synthetic active disconnect"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="multi-coin reconnect backoff",
    )
    assert collector.health.acknowledged_coins == ()
    sleeper.release(len(sleeper.waiters) - 1)
    await _spin_until(lambda: len(second.sent) == 3, description="complete resubscription")

    expected = [_expected_subscription(coin) for coin in config.configured_coins]
    assert first.sent == expected
    assert second.sent == expected
    await _cancel(task)


@pytest.mark.asyncio
async def test_queue_applies_backpressure_then_unblocks_as_one_atomic_batch_item() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC", queue_capacity=1),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    connection.feed(_trades_message(_trade(tid=1)))
    await _spin_until(lambda: collector.health.queue_depth == 1, description="first queued batch")
    connection.feed(_trades_message(_trade(tid=2, time_ms=1_720_000_000_124)))
    await _spin_until(
        lambda: collector.health.received_trade_message_count == 2,
        description="second frame waiting for capacity",
    )
    assert collector.health.emitted_event_count == 1

    first_batch = await collector.receive_batch()
    await _spin_until(
        lambda: collector.health.emitted_event_count == 2,
        description="blocked complete batch published",
    )
    second_batch = await collector.receive_batch()
    assert [json.loads(first_batch[0].source_event_id)[3]] == [1]
    assert [json.loads(second_batch[0].source_event_id)[3]] == [2]
    assert collector.health.queue_high_water_mark == 1
    await _cancel(task)


@pytest.mark.asyncio
async def test_backpressure_timeout_is_terminal_sets_gap_and_commits_no_failed_ids() -> None:
    connection = ScriptedConnection()
    timeout_runner = FailSelectedTimeout(7.0, occurrence=2)
    collector = HyperliquidTradesCollector(
        _config("BTC", queue_capacity=1, publish_timeout_seconds=7.0),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
        timeout_runner=timeout_runner,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    connection.feed(_trades_message(_trade(tid=1)))
    await _spin_until(lambda: collector.health.queue_depth == 1, description="full event queue")
    connection.feed(_trades_message(_trade(tid=2, time_ms=1_720_000_000_124)))

    with pytest.raises(HyperliquidBackpressureError):
        await task
    health = collector.health
    assert health.session_state is SessionState.FAILED
    assert health.sticky_gap is True
    assert health.backpressure_error_count == 1
    assert health.queue_depth == 1
    assert health.emitted_event_count == 1
    assert health.dedup_cache_size == 1
    assert health.reconnect_count == 0
    assert health.last_failure_category is FailureCategory.BACKPRESSURE


@pytest.mark.asyncio
async def test_application_heartbeat_uses_exact_ping_and_valid_pong() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    await _spin_until(lambda: 45.0 in sleeper.delays, description="heartbeat sleep")
    heartbeat_index = sleeper.delays.index(45.0)
    sleeper.release(heartbeat_index)
    await _spin_until(
        lambda: '{"method":"ping"}' in connection.sent, description="application ping"
    )
    connection.feed('{"channel":"pong"}')
    await _spin_until(lambda: collector.health.pong_count == 1, description="application pong")

    assert collector.health.ping_count == 1
    assert collector.health.session_state is SessionState.ACTIVE
    assert collector.health.sticky_gap is False
    assert connection.max_concurrent_recv == 1
    await _cancel(task)


@pytest.mark.asyncio
async def test_queued_pong_is_not_misclassified_while_receiver_has_bounded_backpressure() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    timeout_runner = ControlledPongTimeout()
    collector = HyperliquidTradesCollector(
        _config("BTC", queue_capacity=1),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
        timeout_runner=timeout_runner,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    connection.feed(_trades_message(_trade(tid=1)))
    await _spin_until(lambda: collector.health.queue_depth == 1, description="full output queue")

    sleeper.release(sleeper.delays.index(45.0))
    await timeout_runner.first_pong_wait_started.wait()
    connection.feed(_trades_message(_trade(tid=2, time_ms=1_720_000_000_124)))
    await _spin_until(
        lambda: collector.health.received_trade_message_count == 2,
        description="receiver blocked by output queue",
    )
    connection.feed('{"channel":"pong"}')
    timeout_runner.expire_first_pong_wait.set()
    first_batch = await collector.receive_batch()
    second_batch = await collector.receive_batch()
    await _spin_until(lambda: collector.health.pong_count == 1, description="queued pong routed")

    assert json.loads(first_batch[0].source_event_id)[3] == 1
    assert json.loads(second_batch[0].source_event_id)[3] == 2
    assert collector.health.session_state is SessionState.ACTIVE
    assert collector.health.reconnect_count == 0
    assert collector.health.backpressure_error_count == 0
    await _cancel(task)


@pytest.mark.asyncio
async def test_recurrent_ping_deadline_is_anchored_to_previous_completed_send() -> None:
    clock = VirtualClock()
    connection = TimedPingConnection(clock, ping_send_seconds=3.9)
    collector = HyperliquidTradesCollector(
        _config("BTC", queue_capacity=1),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=clock.sleep,
        jitter=lambda _bound: 0.0,
        timeout_runner=VirtualTimeoutRunner(clock),
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    connection.feed(_trades_message(_trade(tid=801)))
    await _spin_until(lambda: collector.health.queue_depth == 1, description="first full batch")

    clock.advance_to(45.0)
    await _spin_until(lambda: len(connection.ping_start_times) == 1, description="first ping send")
    clock.advance_to(48.9)
    await _spin_until(
        lambda: len(connection.ping_completion_times) == 1,
        description="first ping completion",
    )
    connection.feed(_trades_message(_trade(tid=802, time_ms=1_720_000_000_124)))
    connection.feed('{"channel":"pong"}')
    await _spin_until(
        lambda: collector.health.received_trade_message_count == 2,
        description="first receiver backpressure",
    )
    await asyncio.sleep(0)
    clock.advance_to(53.8)
    assert collector.health.pong_count == 0
    first_batch = await collector.receive_batch()
    assert json.loads(first_batch[0].source_event_id)[3] == 801
    await _spin_until(lambda: collector.health.pong_count == 1, description="delayed first pong")

    clock.advance_to(93.8)
    connection.feed(_trades_message(_trade(tid=803, time_ms=1_720_000_000_125)))
    await _spin_until(
        lambda: collector.health.received_trade_message_count == 3,
        description="second receiver backpressure",
    )
    await asyncio.sleep(0)
    clock.advance_to(93.9)
    await asyncio.sleep(0)
    assert len(connection.ping_start_times) == 1
    clock.advance_to(98.7)
    second_batch = await collector.receive_batch()
    assert json.loads(second_batch[0].source_event_id)[3] == 802
    await _spin_until(
        lambda: len(connection.ping_start_times) == 2,
        description="second ping unblocked",
    )
    clock.advance_to(102.61)
    await _spin_until(
        lambda: len(connection.ping_completion_times) == 2,
        description="second ping completion",
    )

    completed_delta = connection.ping_completion_times[1] - connection.ping_completion_times[0]
    assert completed_delta < 54.0
    assert completed_delta < 60.0
    assert [message for message in connection.sent if message == '{"method":"ping"}'] == [
        '{"method":"ping"}',
        '{"method":"ping"}',
    ]
    await _cancel(task)
    assert clock.pending_sleep_count == 0
    names = {pending.get_name() for pending in asyncio.all_tasks() if not pending.done()}
    assert "hyperliquid-heartbeat-interval" not in names


@pytest.mark.asyncio
async def test_blocked_application_ping_send_has_a_bounded_timeout() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection, ScriptedConnection()),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
        timeout_runner=FailSelectedTimeout(4.0),
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    sleeper.release(sleeper.delays.index(45.0))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="bounded application ping send timeout",
    )

    assert collector.health.ping_count == 0
    assert collector.health.reconnect_count == 1
    assert collector.health.sticky_gap is True
    assert collector.health.acknowledged_coins == ()
    assert collector.health.last_failure_category is FailureCategory.HEARTBEAT_TIMEOUT
    await _cancel(task)


@pytest.mark.asyncio
async def test_missing_pong_after_activation_reconnects_and_marks_gap() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    timeout_runner = FailSelectedTimeout(10.0)
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection, ScriptedConnection()),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
        timeout_runner=timeout_runner,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    await _spin_until(lambda: 45.0 in sleeper.delays, description="heartbeat waiting")
    sleeper.release(sleeper.delays.index(45.0))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="missing pong reconnect",
    )

    assert connection.sent[-1] == '{"method":"ping"}'
    assert collector.health.ping_count == 1
    assert collector.health.pong_count == 0
    assert collector.health.reconnect_count == 1
    assert collector.health.sticky_gap is True
    assert sleeper.delays[-1] == 3.0
    assert collector.health.last_failure_category is FailureCategory.HEARTBEAT_TIMEOUT
    await _cancel(task)


@pytest.mark.asyncio
async def test_pre_activation_failures_use_capped_exponential_backoff_without_gap() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    factory = ScriptedConnectionFactory(
        OSError("synthetic DNS failure one"),
        OSError("synthetic DNS failure two"),
        connection,
    )
    collector = HyperliquidTradesCollector(
        _config("BTC", backoff_max_seconds=12.0),
        connection_factory=factory,
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = asyncio.create_task(collector.run())
    await _spin_until(lambda: sleeper.delays == [3.0], description="first backoff")
    sleeper.release(0)
    await _spin_until(lambda: sleeper.delays == [3.0, 6.0], description="second backoff")
    sleeper.release(1)
    await _spin_until(
        lambda: connection.sent == [_expected_subscription("BTC")],
        description="third attempt",
    )

    health = collector.health
    assert health.connection_attempts == 3
    assert health.successful_connections == 1
    assert health.reconnect_count == 2
    assert health.sticky_gap is False
    assert health.last_failure_category is FailureCategory.TRANSPORT_RECONNECT
    await _cancel(task)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "close_code",
    [None, 1000, 1001, 1005, 1011, 1012, 1013, 1014],
)
async def test_retryable_websocket_close_codes_reconnect(close_code: int | None) -> None:
    first = ScriptedConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(first, ScriptedConnection()),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, first, 1)
    first.feed(_closed_connection_error(close_code))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.BACKING_OFF,
        description="retryable WebSocket close",
    )

    assert collector.health.reconnect_count == 1
    assert collector.health.last_failure_category is FailureCategory.TRANSPORT_RECONNECT
    assert "sensitive-close-reason" not in repr(collector.health)
    await _cancel(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("close_code", [1002, 1003, 1007, 1008, 1009, 1010, 4000])
async def test_terminal_websocket_close_codes_fail_closed_and_hide_reason(close_code: int) -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection, ScriptedConnection()),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed(_closed_connection_error(close_code))

    with pytest.raises(HyperliquidTerminalCloseError) as captured:
        await task
    assert collector.health.reconnect_count == 0
    assert collector.health.session_state is SessionState.FAILED
    assert (
        collector.health.last_failure_category
        is FailureCategory.TERMINAL_CLOSE_OR_PROTOCOL_VIOLATION
    )
    assert "sensitive-close-reason" not in repr(captured.value)
    assert "sensitive-close-reason" not in repr(collector.health)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_terminal_close_on_either_direction_wins_over_retryable_code() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    mixed_close = ConnectionClosedError(
        Close(1012, "sensitive-received-reason"),
        Close(1002, "sensitive-sent-reason"),
        True,
    )
    connection.feed(mixed_close)

    with pytest.raises(HyperliquidTerminalCloseError) as captured:
        await task
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "sensitive" not in repr(captured.value)
    assert collector.health.reconnect_count == 0


@pytest.mark.asyncio
async def test_tls_certificate_verification_failure_is_fatal_without_retry() -> None:
    certificate_error = ssl.SSLCertVerificationError(1, "synthetic certificate failure")
    factory = ScriptedConnectionFactory(certificate_error, ScriptedConnection())
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=factory,
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )

    with pytest.raises(ssl.SSLCertVerificationError):
        await collector.run()
    assert collector.health.session_state is SessionState.FAILED
    assert collector.health.reconnect_count == 0
    assert len(factory.calls) == 1


@pytest.mark.asyncio
async def test_backoff_jitter_is_bounded_and_exponential_delay_stays_capped() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config(
            "BTC",
            backoff_max_seconds=6.0,
            backoff_jitter_seconds=1.0,
        ),
        connection_factory=ScriptedConnectionFactory(
            OSError("synthetic failure one"),
            OSError("synthetic failure two"),
            OSError("synthetic failure three"),
            connection,
        ),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda bound: bound,
    )
    task = asyncio.create_task(collector.run())
    await _spin_until(lambda: sleeper.delays == [4.0], description="bounded first jitter")
    sleeper.release(0)
    await _spin_until(lambda: sleeper.delays == [4.0, 6.0], description="backoff cap reached")
    sleeper.release(1)
    await _spin_until(
        lambda: sleeper.delays == [4.0, 6.0, 6.0],
        description="backoff remains capped",
    )
    sleeper.release(2)
    await _spin_until(lambda: len(connection.sent) == 1, description="post-cap connection")

    assert collector.health.connection_attempts == 4
    assert collector.health.reconnect_count == 3
    await _cancel(task)


@pytest.mark.asyncio
async def test_full_activation_resets_backoff_before_later_disconnect() -> None:
    first = ScriptedConnection()
    second = ScriptedConnection()
    sleeper = ControlledSleeper()
    collector = HyperliquidTradesCollector(
        _config("BTC", backoff_max_seconds=12.0),
        connection_factory=ScriptedConnectionFactory(
            OSError("synthetic opening failure"),
            first,
            second,
        ),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = asyncio.create_task(collector.run())
    await _spin_until(lambda: sleeper.delays == [3.0], description="opening backoff")
    sleeper.release(0)
    await _spin_until(lambda: len(first.sent) == 1, description="first connected session")
    await _activate(collector, first)
    first.feed(OSError("synthetic active disconnect"))
    await _spin_until(
        lambda: sleeper.delays.count(3.0) == 2,
        description="reset backoff after activation",
    )

    assert sleeper.delays[-1] == 3.0
    assert collector.health.sticky_gap is True
    assert collector.health.reconnect_count == 2
    await _cancel(task)


@pytest.mark.asyncio
async def test_malformed_protocol_message_is_fatal_without_reconnect() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    factory = ScriptedConnectionFactory(connection, ScriptedConnection())
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=factory,
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed('{"channel":"orders","data":[]}')

    with pytest.raises(HyperliquidProtocolError):
        await task
    assert len(factory.calls) == 1
    assert collector.health.reconnect_count == 0
    assert collector.health.session_state is SessionState.FAILED
    assert (
        collector.health.last_failure_category
        is FailureCategory.TERMINAL_CLOSE_OR_PROTOCOL_VIOLATION
    )


@pytest.mark.asyncio
async def test_waiting_consumer_observes_sanitized_fatal_producer_termination() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    consumer = asyncio.create_task(collector.receive_batch())
    await asyncio.sleep(0)
    connection.feed('{"channel":"private-sensitive-channel"}')

    with pytest.raises(HyperliquidProtocolError):
        await task
    with pytest.raises(HyperliquidCollectorTerminatedError) as terminated:
        await consumer
    assert terminated.value.session_state is SessionState.FAILED
    assert terminated.value.failure_category is FailureCategory.TERMINAL_CLOSE_OR_PROTOCOL_VIOLATION
    assert "private-sensitive-channel" not in repr(terminated.value)
    assert terminated.value.__cause__ is None
    assert terminated.value.__context__ is None


@pytest.mark.asyncio
async def test_waiting_consumer_is_woken_when_producer_is_cancelled() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    consumer = asyncio.create_task(collector.receive_batch())
    await asyncio.sleep(0)
    await _cancel(task)

    with pytest.raises(HyperliquidCollectorTerminatedError) as terminated:
        await consumer
    assert terminated.value.session_state is SessionState.STOPPED
    assert terminated.value.failure_category is None


@pytest.mark.asyncio
async def test_queued_batches_are_drained_before_terminal_outcome() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    connection.feed(_trades_message(_trade(tid=701)))
    await _spin_until(lambda: collector.health.queue_depth == 1, description="queued batch")
    connection.feed('{"channel":"orders","data":[]}')
    with pytest.raises(HyperliquidProtocolError):
        await task

    batch = await collector.receive_batch()
    assert json.loads(batch[0].source_event_id)[3] == 701
    with pytest.raises(HyperliquidCollectorTerminatedError) as terminated:
        await collector.receive_batch()
    assert terminated.value.failure_category is FailureCategory.TERMINAL_CLOSE_OR_PROTOCOL_VIOLATION


@pytest.mark.asyncio
async def test_receive_batch_cancellation_leaves_no_helper_tasks() -> None:
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    consumer = asyncio.create_task(
        collector.receive_batch(),
        name="test-single-logical-consumer",
    )
    await asyncio.sleep(0)
    with pytest.raises(HyperliquidCollectorStateError, match="one logical"):
        await collector.receive_batch()
    assert collector.health.last_failure_category is FailureCategory.LOCAL_LIFECYCLE_FAILURE
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    names = {pending.get_name() for pending in asyncio.all_tasks() if not pending.done()}
    assert "test-single-logical-consumer" not in names
    assert not any("queue-get" in name for name in names)


@pytest.mark.asyncio
async def test_cancellation_closes_session_propagates_and_leaves_no_background_tasks() -> None:
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    await _cancel(task)

    assert connection.close_count == 1
    assert collector.health.session_state is SessionState.STOPPED
    assert collector.health.reconnect_count == 0
    names = {pending.get_name() for pending in asyncio.all_tasks() if not pending.done()}
    assert "hyperliquid-trades-receiver" not in names
    assert "hyperliquid-application-heartbeat" not in names
    assert "hyperliquid-subscription-activation" not in names
    with pytest.raises(HyperliquidCollectorStateError):
        await collector.run()


@pytest.mark.asyncio
async def test_blocked_consumer_and_producer_are_cancellation_safe() -> None:
    empty_collector = HyperliquidTradesCollector(
        _config("BTC"),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    consumer = asyncio.create_task(empty_collector.receive_batch())
    await asyncio.sleep(0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert empty_collector.health.queue_depth == 0

    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC", queue_capacity=1),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    connection.feed(_trades_message(_trade(tid=1)))
    await _spin_until(lambda: collector.health.queue_depth == 1, description="queue filled")
    connection.feed(_trades_message(_trade(tid=2, time_ms=1_720_000_000_124)))
    await _spin_until(
        lambda: collector.health.received_trade_message_count == 2,
        description="producer blocked",
    )
    await _cancel(task)

    assert collector.health.queue_depth == 1
    assert collector.health.emitted_event_count == 1
    assert collector.health.dedup_cache_size == 1
    assert connection.close_count == 1


@pytest.mark.asyncio
async def test_health_snapshot_is_frozen_and_contains_no_payload_or_user_data() -> None:
    connection = ScriptedConnection()
    clock = CountingClock()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        utc_now=clock.utc_now,
        monotonic_now=clock.monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    connection.feed(_trades_message(_trade()))
    await collector.receive_batch()
    health = collector.health

    assert isinstance(health, HyperliquidCollectorHealth)
    assert health.last_received_time is not None
    assert health.last_received_monotonic_ns is not None
    assert health.queue_depth == 0
    assert health.queue_high_water_mark == 1
    assert "synthetic-buyer" not in repr(health)
    assert "synthetic-seller" not in repr(health)
    assert "synthetic-transaction" not in repr(health)
    with pytest.raises(FrozenInstanceError):
        health.sticky_gap = True  # type: ignore[misc]
    await _cancel(task)


@pytest.mark.asyncio
async def test_production_connection_boundary_uses_modern_bounded_unproxied_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = ScriptedConnection()
    captured_uri: str | None = None
    captured_options: dict[str, object] = {}

    def fake_connect(uri: str, **options: object) -> ScriptedConnectionContext:
        nonlocal captured_uri, captured_options
        captured_uri = uri
        captured_options = options
        return ScriptedConnectionContext(connection)

    monkeypatch.setattr(websocket_module, "connect", fake_connect)
    async with websocket_module._open_mainnet_connection(_config("BTC")) as opened:
        assert opened is connection

    transport_logger = websocket_module._TRANSPORT_PRIVACY_LOGGER
    default_logger = logging.getLogger("websockets.client")
    root_logger = logging.getLogger()
    captured_messages: list[str] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_messages.append(self.format(record))

    capture_handler = CaptureHandler()
    original_root_level = root_logger.level
    original_default_level = default_logger.level
    try:
        root_logger.addHandler(capture_handler)
        root_logger.setLevel(logging.DEBUG)
        default_logger.setLevel(logging.DEBUG)
        transport_logger.debug("inbound frame %s", "synthetic-public-frame-marker")
        transport_logger.critical("close reason %s", "synthetic-close-marker")
    finally:
        default_logger.setLevel(original_default_level)
        root_logger.setLevel(original_root_level)
        root_logger.removeHandler(capture_handler)

    assert captured_uri == HYPERLIQUID_MAINNET_WEBSOCKET_URL
    assert captured_options == {
        "logger": transport_logger,
        "proxy": None,
        "open_timeout": 10.0,
        "ping_interval": None,
        "ping_timeout": None,
        "close_timeout": 10.0,
        "max_size": 1_048_576,
        "max_queue": 16,
    }
    assert transport_logger is not default_logger
    assert transport_logger.parent is None
    assert transport_logger.propagate is False
    assert transport_logger.disabled is True
    assert transport_logger.level > logging.CRITICAL
    assert len(transport_logger.handlers) == 1
    assert isinstance(transport_logger.handlers[0], logging.NullHandler)
    assert transport_logger.isEnabledFor(logging.DEBUG) is False
    assert transport_logger.isEnabledFor(logging.CRITICAL) is False
    assert captured_messages == []
    assert not any("synthetic-public-frame-marker" in message for message in captured_messages)
    assert not any("synthetic-close-marker" in message for message in captured_messages)
    assert connection.close_count == 1


def test_collector_exposes_no_authenticated_wallet_order_or_http_surface() -> None:
    public_names = set(dir(HyperliquidTradesCollector))
    assert public_names.isdisjoint(
        {
            "authenticate",
            "http_get",
            "place_order",
            "private_key",
            "sign",
            "wallet",
        }
    )
    assert not any(
        dependency in websocket_module.__dict__
        for dependency in ("aiohttp", "httpx", "os", "pathlib", "requests", "socket", "urllib")
    )
