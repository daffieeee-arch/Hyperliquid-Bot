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
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

import hyperliquid_bot.hyperliquid_ws_client as websocket_module
from hyperliquid_bot.contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    Instrument,
    InstrumentType,
    MarketEventEnvelope,
    Venue,
)
from hyperliquid_bot.data_provenance import (
    MAX_RAW_APPLICATION_MESSAGE_BYTES,
    CollectorRunId,
    NormalizationRunId,
    RawMarketDataRecord,
    SanitizedValidationFailure,
    SubscriptionAttemptStatus,
    SubscriptionAttemptTransition,
    ValidationFailureCategory,
)
from hyperliquid_bot.hyperliquid_ws_client import (
    HYPERLIQUID_MAINNET_WEBSOCKET_URL,
    HYPERLIQUID_MAX_SUBSCRIPTIONS,
    FailureCategory,
    GreetingMessage,
    HyperliquidBackpressureError,
    HyperliquidCaptureValidationError,
    HyperliquidCollectorHealth,
    HyperliquidCollectorStateError,
    HyperliquidCollectorTerminatedError,
    HyperliquidProtocolError,
    HyperliquidSinkBoundaryError,
    HyperliquidSourceEventConflictError,
    HyperliquidTerminalCloseError,
    HyperliquidTradesCollectorConfig,
    PongMessage,
    SessionState,
    SubscriptionAcknowledgement,
    TradesMessage,
    WebSocketConnection,
    route_hyperliquid_websocket_message,
)
from hyperliquid_bot.hyperliquid_ws_client import (
    HyperliquidTradesCollector as _ProductionHyperliquidTradesCollector,
)
from hyperliquid_bot.market_data_sinks import (
    NormalizationOutcomeAcceptance,
    NormalizationOutcomeRejected,
    NormalizationOutcomeSink,
    RawRecordAcceptance,
    RawRecordRejected,
    RawRecordSink,
    SinkDestinationId,
    SinkFailureCategory,
)
from hyperliquid_bot.market_event_v3 import (
    FrameNormalizationStatus,
    MarketEventEnvelopeV3,
    NormalizationEvidence,
    NormalizationOutcome,
    RawEventDisposition,
)

_BASE_TIME = datetime(2026, 8, 25, 16, 0, tzinfo=UTC)
_COLLECTOR_RUN_ID = CollectorRunId("collector-run-test")
_NORMALIZATION_RUN_ID = NormalizationRunId("normalization-run-test")


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
    raw_sink_timeout_seconds: float = 1.0,
    outcome_sink_timeout_seconds: float = 1.0,
    publish_timeout_seconds: float = 5.0,
    heartbeat_interval_seconds: float = 45.0,
    pong_timeout_seconds: float = 10.0,
    receive_timeout_seconds: float = 60.0,
    send_timeout_seconds: float = 4.0,
    websocket_max_size_bytes: int = 1_048_576,
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
        raw_sink_timeout_seconds=raw_sink_timeout_seconds,
        outcome_sink_timeout_seconds=outcome_sink_timeout_seconds,
        publish_timeout_seconds=publish_timeout_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        pong_timeout_seconds=pong_timeout_seconds,
        receive_timeout_seconds=receive_timeout_seconds,
        send_timeout_seconds=send_timeout_seconds,
        websocket_max_size_bytes=websocket_max_size_bytes,
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


class UnknownFailingSendConnection(ScriptedConnection):
    async def send(self, message: str) -> None:
        self.sent.append(message)
        private_send_marker = "private-unknown-send-marker"
        raise RuntimeError(private_send_marker)


class WrongTypeRecvConnection(ScriptedConnection):
    def __init__(self) -> None:
        super().__init__()
        self.release_wrong_type = asyncio.Event()

    async def recv(self) -> str | bytes:
        self.recv_calls += 1
        await self.release_wrong_type.wait()
        return cast(str | bytes, object())


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


class ExitFailingConnectionContext(ScriptedConnectionContext):
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None:
        await super().__aexit__(exc_type, exc_value, traceback)
        private_exit_marker = "private-context-exit-marker"
        raise RuntimeError(private_exit_marker)


class ExitFailingConnectionFactory:
    def __init__(self, connection: ScriptedConnection) -> None:
        self.connection = connection
        self.calls: list[HyperliquidTradesCollectorConfig] = []

    def __call__(
        self,
        config: HyperliquidTradesCollectorConfig,
    ) -> AbstractAsyncContextManager[WebSocketConnection]:
        self.calls.append(config)
        return ExitFailingConnectionContext(self.connection)


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


class RecordingRawSink:
    def __init__(
        self,
        destination: str = "test-raw-records",
        *,
        journal: list[str] | None = None,
    ) -> None:
        self.destination_id = SinkDestinationId(destination)
        self.records: list[RawMarketDataRecord] = []
        self.close_count = 0
        self.journal = journal

    async def accept(self, record: RawMarketDataRecord) -> RawRecordAcceptance:
        self.records.append(record)
        if self.journal is not None:
            self.journal.append("raw-accepted")
        return RawRecordAcceptance(
            record.raw_record_id,
            record.full_record_integrity_sha256,
            self.destination_id,
        )

    async def aclose(self) -> None:
        self.close_count += 1
        if self.journal is not None:
            self.journal.append("raw-closed")


class RecordingOutcomeSink:
    def __init__(
        self,
        destination: str = "test-normalization-outcomes",
        *,
        journal: list[str] | None = None,
    ) -> None:
        self.destination_id = SinkDestinationId(destination)
        self.outcomes: list[NormalizationOutcome] = []
        self.close_count = 0
        self.journal = journal

    async def accept(
        self,
        outcome: NormalizationOutcome,
    ) -> NormalizationOutcomeAcceptance:
        self.outcomes.append(outcome)
        if self.journal is not None:
            self.journal.append("outcome-accepted")
        return NormalizationOutcomeAcceptance(
            outcome.normalization_outcome_id,
            self.destination_id,
        )

    async def aclose(self) -> None:
        self.close_count += 1
        if self.journal is not None:
            self.journal.append("outcome-closed")


class FaultingRawSink(RecordingRawSink):
    def __init__(
        self,
        mode: str,
        *,
        marker: str = "private-raw-sink-marker",
        destination: str = "test-raw-records",
    ) -> None:
        super().__init__(destination)
        self.mode = mode
        self.marker = marker
        self.accept_count = 0
        self.accept_started = asyncio.Event()
        self.release = asyncio.Event()

    async def accept(self, record: RawMarketDataRecord) -> RawRecordAcceptance:
        self.accept_count += 1
        self.accept_started.set()
        if self.mode == "block":
            await self.release.wait()
        elif self.mode == "reject":
            raise RawRecordRejected
        elif self.mode == "exception":
            private_traceback_local = self.marker
            raise RuntimeError(private_traceback_local)
        elif self.mode == "wrong-type":
            return cast(RawRecordAcceptance, object())
        elif self.mode == "wrong-id":
            other = replace(record, ingress_ordinal=record.ingress_ordinal + 1)
            return RawRecordAcceptance(
                other.raw_record_id,
                other.full_record_integrity_sha256,
                self.destination_id,
            )
        elif self.mode == "wrong-integrity":
            return RawRecordAcceptance(
                record.raw_record_id,
                "0" * 64,
                self.destination_id,
            )
        elif self.mode == "wrong-destination":
            return RawRecordAcceptance(
                record.raw_record_id,
                record.full_record_integrity_sha256,
                SinkDestinationId("different-raw-destination"),
            )
        return await super().accept(record)


class FaultingOutcomeSink(RecordingOutcomeSink):
    def __init__(
        self,
        mode: str,
        *,
        marker: str = "private-outcome-sink-marker",
        destination: str = "test-normalization-outcomes",
    ) -> None:
        super().__init__(destination)
        self.mode = mode
        self.marker = marker
        self.accept_count = 0
        self.accept_started = asyncio.Event()
        self.release = asyncio.Event()

    async def accept(
        self,
        outcome: NormalizationOutcome,
    ) -> NormalizationOutcomeAcceptance:
        self.accept_count += 1
        self.accept_started.set()
        if self.mode == "block":
            await self.release.wait()
        elif self.mode == "reject":
            raise NormalizationOutcomeRejected
        elif self.mode == "exception":
            private_traceback_local = self.marker
            raise RuntimeError(private_traceback_local)
        elif self.mode == "wrong-type":
            return cast(NormalizationOutcomeAcceptance, object())
        elif self.mode == "wrong-id":
            other = replace(outcome, normalizer_commit="other-normalizer-commit")
            return NormalizationOutcomeAcceptance(
                other.normalization_outcome_id,
                self.destination_id,
            )
        elif self.mode == "wrong-destination":
            return NormalizationOutcomeAcceptance(
                outcome.normalization_outcome_id,
                SinkDestinationId("different-outcome-destination"),
            )
        return await super().accept(outcome)


class CancellationSuppressingRawSink(RecordingRawSink):
    def __init__(self, *, fail_late: bool = False) -> None:
        super().__init__()
        self.fail_late = fail_late
        self.accept_count = 0
        self.accept_started = asyncio.Event()
        self.late_returned = asyncio.Event()

    async def accept(self, record: RawMarketDataRecord) -> RawRecordAcceptance:
        self.accept_count += 1
        self.records.append(record)
        self.accept_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)
        self.late_returned.set()
        if self.fail_late:
            private_late_traceback_marker = "private-late-sink-traceback-marker"
            raise RuntimeError(private_late_traceback_marker)
        return RawRecordAcceptance(
            record.raw_record_id,
            record.full_record_integrity_sha256,
            self.destination_id,
        )


class CancellationSuppressingOutcomeSink(RecordingOutcomeSink):
    def __init__(self) -> None:
        super().__init__()
        self.accept_count = 0
        self.accept_started = asyncio.Event()
        self.late_returned = asyncio.Event()

    async def accept(
        self,
        outcome: NormalizationOutcome,
    ) -> NormalizationOutcomeAcceptance:
        self.accept_count += 1
        self.accept_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)
        self.late_returned.set()
        return NormalizationOutcomeAcceptance(
            outcome.normalization_outcome_id,
            self.destination_id,
        )


class CancellationSuppressingCloseOutcomeSink(RecordingOutcomeSink):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.late_closed = asyncio.Event()

    async def aclose(self) -> None:
        self.close_count += 1
        self.close_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)
        self.late_closed.set()


class ClosingRawSink(RecordingRawSink):
    def __init__(self, mode: str, *, journal: list[str]) -> None:
        super().__init__(journal=journal)
        self.mode = mode
        self.close_started = asyncio.Event()
        self.release = asyncio.Event()

    async def aclose(self) -> None:
        self.close_count += 1
        if self.journal is not None:
            self.journal.append("raw-close-started")
        self.close_started.set()
        if self.mode == "block":
            await self.release.wait()
        elif self.mode == "exception":
            private_close_traceback_local = "private-raw-close-marker"
            raise RuntimeError(private_close_traceback_local)


class ClosingOutcomeSink(RecordingOutcomeSink):
    def __init__(self, mode: str, *, journal: list[str]) -> None:
        super().__init__(journal=journal)
        self.mode = mode
        self.close_started = asyncio.Event()
        self.release = asyncio.Event()

    async def aclose(self) -> None:
        self.close_count += 1
        if self.journal is not None:
            self.journal.append("outcome-close-started")
        self.close_started.set()
        if self.mode == "block":
            await self.release.wait()
        elif self.mode == "exception":
            private_close_traceback_local = "private-outcome-close-marker"
            raise RuntimeError(private_close_traceback_local)


class VirtualDelayRawSink(RecordingRawSink):
    def __init__(self, clock: VirtualClock, delayed_calls: frozenset[int]) -> None:
        super().__init__()
        self.clock = clock
        self.delayed_calls = delayed_calls
        self.accept_count = 0

    async def accept(self, record: RawMarketDataRecord) -> RawRecordAcceptance:
        self.accept_count += 1
        if self.accept_count in self.delayed_calls:
            await self.clock.sleep(0.9)
        return await super().accept(record)


class VirtualDelayOutcomeSink(RecordingOutcomeSink):
    def __init__(self, clock: VirtualClock, delayed_calls: frozenset[int]) -> None:
        super().__init__()
        self.clock = clock
        self.delayed_calls = delayed_calls
        self.accept_count = 0

    async def accept(
        self,
        outcome: NormalizationOutcome,
    ) -> NormalizationOutcomeAcceptance:
        self.accept_count += 1
        if self.accept_count in self.delayed_calls:
            await self.clock.sleep(0.9)
        return await super().accept(outcome)


class StructurallyDualSink:
    """Deliberately ambiguous test object satisfying both runtime protocols."""

    destination_id = SinkDestinationId("test-dual-sink")

    async def accept(self, value: object) -> object:
        return value

    async def aclose(self) -> None:
        return None


class InvalidDestinationSink:
    """Structurally valid sink with an invalid or failing destination declaration."""

    def __init__(self, *, getter_fails: bool) -> None:
        self.getter_fails = getter_fails

    @property
    def destination_id(self) -> object:
        if self.getter_fails:
            raise RuntimeError("private-destination-getter-marker")
        return "private-invalid-destination-marker"

    async def accept(self, value: object) -> object:
        return value

    async def aclose(self) -> None:
        return None


class HyperliquidTradesCollector(_ProductionHyperliquidTradesCollector):
    """Test harness that injects explicit deterministic mandatory audit sinks."""

    def __init__(
        self,
        config: HyperliquidTradesCollectorConfig,
        *,
        collector_run_id: CollectorRunId = _COLLECTOR_RUN_ID,
        normalization_run_id: NormalizationRunId = _NORMALIZATION_RUN_ID,
        normalizer_version: str = "normalizer-test-v1",
        normalizer_commit: str = "normalizer-test-commit",
        raw_record_sink: RawRecordSink | None = None,
        normalization_outcome_sink: NormalizationOutcomeSink | None = None,
        connection_factory: websocket_module.ConnectionFactory = (
            websocket_module._open_mainnet_connection
        ),
        utc_now: websocket_module.UtcNow = _fixed_utc_now,
        monotonic_now: websocket_module.MonotonicNow = _fixed_monotonic_now,
        sleeper: websocket_module.Sleeper = asyncio.sleep,
        jitter: websocket_module.Jitter = websocket_module._system_jitter,
        timeout_runner: websocket_module.TimeoutRunner = websocket_module._asyncio_timeout,
    ) -> None:
        selected_raw_sink = raw_record_sink or RecordingRawSink()
        selected_outcome_sink = normalization_outcome_sink or RecordingOutcomeSink()
        self.test_raw_sink = selected_raw_sink
        self.test_outcome_sink = selected_outcome_sink
        super().__init__(
            config,
            collector_run_id=collector_run_id,
            normalization_run_id=normalization_run_id,
            normalizer_version=normalizer_version,
            normalizer_commit=normalizer_commit,
            raw_record_sink=selected_raw_sink,
            normalization_outcome_sink=selected_outcome_sink,
            connection_factory=connection_factory,
            utc_now=utc_now,
            monotonic_now=monotonic_now,
            sleeper=sleeper,
            jitter=jitter,
            timeout_runner=timeout_runner,
        )


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


def _assert_exported_exception_excludes(
    error: BaseException,
    *markers: str,
    forbidden_objects: tuple[object, ...] = (),
) -> None:
    pending: list[object] = [error]
    visited: set[int] = set()
    forbidden_ids = {id(value) for value in forbidden_objects}
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        assert id(current) not in forbidden_ids
        visited.add(id(current))

        try:
            surface = repr(current)
        except Exception:
            surface = "<unrepresentable>"
        for marker in markers:
            assert marker not in surface

        if isinstance(current, BaseException):
            pending.extend(current.args)
            pending.extend(vars(current).items())
            if current.__cause__ is not None:
                pending.append(current.__cause__)
            if current.__context__ is not None:
                pending.append(current.__context__)
            traceback = current.__traceback__
            while traceback is not None:
                module_name = traceback.tb_frame.f_globals.get("__name__", "")
                if module_name.startswith("hyperliquid_bot"):
                    pending.extend(traceback.tb_frame.f_locals.items())
                traceback = traceback.tb_next
            continue

        if isinstance(current, dict):
            pending.extend(current.items())
            continue
        if isinstance(current, (list, tuple, set, frozenset, deque)):
            pending.extend(current)
            continue
        if isinstance(current, asyncio.Queue):
            pending.extend(vars(current).items())
            continue
        if isinstance(current, asyncio.Task):
            pending.append(current.get_coro())
            continue
        if inspect.iscoroutine(current):
            frame = current.cr_frame
            if frame is not None and frame.f_globals.get("__name__", "").startswith(
                "hyperliquid_bot"
            ):
                pending.extend(frame.f_locals.items())
            continue
        if inspect.ismodule(current) or inspect.isroutine(current) or inspect.isclass(current):
            continue

        object_module = type(current).__module__
        if not (
            object_module.startswith("hyperliquid_bot")
            or object_module == __name__
            or object_module == "builtins"
        ):
            continue
        try:
            pending.extend(vars(current).items())
        except TypeError:
            pass
        for owner in type(current).__mro__:
            slots = owner.__dict__.get("__slots__", ())
            if type(slots) is str:
                slots = (slots,)
            for slot in slots:
                if slot in {"__dict__", "__weakref__"}:
                    continue
                try:
                    pending.append((slot, object.__getattribute__(current, slot)))
                except (AttributeError, TypeError):
                    pass


def test_config_is_immutable_non_empty_hyperliquid_only_and_deterministically_ordered() -> None:
    config = _config("xyz:XYZ100", "BTC", "@107")

    assert config.configured_coins == ("@107", "BTC", "xyz:XYZ100")
    assert isinstance(config.instruments, tuple)
    recurrent_bound = (
        max(
            config.heartbeat_interval_seconds,
            config.pong_timeout_seconds
            + 2 * (config.raw_sink_timeout_seconds + config.outcome_sink_timeout_seconds)
            + config.publish_timeout_seconds,
        )
        + config.raw_sink_timeout_seconds
        + config.outcome_sink_timeout_seconds
        + config.publish_timeout_seconds
        + config.send_timeout_seconds
    )
    assert recurrent_bound == 56.0
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
        pytest.param("raw_sink_timeout_seconds", 0.0, ValueError, id="zero-raw-sink"),
        pytest.param("outcome_sink_timeout_seconds", 0.0, ValueError, id="zero-outcome-sink"),
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


def test_capture_configuration_is_mandatory_typed_distinct_and_pre_network() -> None:
    signature = inspect.signature(_ProductionHyperliquidTradesCollector)
    for parameter_name in (
        "collector_run_id",
        "normalization_run_id",
        "normalizer_version",
        "normalizer_commit",
        "raw_record_sink",
        "normalization_outcome_sink",
    ):
        assert signature.parameters[parameter_name].default is inspect.Parameter.empty

    with pytest.raises(TypeError, match="required keyword-only"):
        _ProductionHyperliquidTradesCollector(_config("BTC"))  # type: ignore[call-arg]

    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    with pytest.raises(TypeError, match="CollectorRunId"):
        _ProductionHyperliquidTradesCollector(
            _config("BTC"),
            collector_run_id="wrong",  # type: ignore[arg-type]
            normalization_run_id=_NORMALIZATION_RUN_ID,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit",
            raw_record_sink=raw_sink,
            normalization_outcome_sink=outcome_sink,
        )
    dual_sink = StructurallyDualSink()
    with pytest.raises(ValueError, match="distinct objects"):
        _ProductionHyperliquidTradesCollector(
            _config("BTC"),
            collector_run_id=_COLLECTOR_RUN_ID,
            normalization_run_id=_NORMALIZATION_RUN_ID,
            normalizer_version="normalizer-v1",
            normalizer_commit="normalizer-commit",
            raw_record_sink=dual_sink,  # type: ignore[arg-type]
            normalization_outcome_sink=dual_sink,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("sink_position", ("raw", "outcome"))
@pytest.mark.parametrize("invalid_sink", (object(), InvalidDestinationSink(getter_fails=False)))
def test_invalid_sink_protocol_or_destination_fails_before_networking(
    sink_position: str,
    invalid_sink: object,
) -> None:
    factory = ScriptedConnectionFactory(ScriptedConnection())
    arguments: dict[str, object] = {
        "collector_run_id": _COLLECTOR_RUN_ID,
        "normalization_run_id": _NORMALIZATION_RUN_ID,
        "normalizer_version": "normalizer-v1",
        "normalizer_commit": "normalizer-commit",
        "raw_record_sink": RecordingRawSink(),
        "normalization_outcome_sink": RecordingOutcomeSink(),
        "connection_factory": factory,
    }
    if sink_position == "raw":
        arguments["raw_record_sink"] = invalid_sink
    else:
        arguments["normalization_outcome_sink"] = invalid_sink

    with pytest.raises(TypeError):
        _ProductionHyperliquidTradesCollector(_config("BTC"), **arguments)  # type: ignore[arg-type]

    assert factory.calls == []


@pytest.mark.parametrize("sink_position", ("raw", "outcome"))
def test_sink_destination_getter_failure_is_sanitized_before_networking(
    sink_position: str,
) -> None:
    marker = "private-destination-getter-marker"
    factory = ScriptedConnectionFactory(ScriptedConnection())
    failing_sink = InvalidDestinationSink(getter_fails=True)
    arguments: dict[str, object] = {
        "collector_run_id": _COLLECTOR_RUN_ID,
        "normalization_run_id": _NORMALIZATION_RUN_ID,
        "normalizer_version": "normalizer-v1",
        "normalizer_commit": "normalizer-commit",
        "raw_record_sink": RecordingRawSink(),
        "normalization_outcome_sink": RecordingOutcomeSink(),
        "connection_factory": factory,
    }
    if sink_position == "raw":
        arguments["raw_record_sink"] = failing_sink
    else:
        arguments["normalization_outcome_sink"] = failing_sink

    with pytest.raises(TypeError, match="valid SinkDestinationId") as captured:
        _ProductionHyperliquidTradesCollector(_config("BTC"), **arguments)  # type: ignore[arg-type]

    _assert_exported_exception_excludes(captured.value, marker)
    assert factory.calls == []


@pytest.mark.parametrize(
    "field_name",
    ("collector_version", "collector_commit", "normalizer_version", "normalizer_commit"),
)
def test_all_collector_and_normalizer_build_fields_are_bounded_before_networking(
    field_name: str,
) -> None:
    factory = ScriptedConnectionFactory(ScriptedConnection())
    config_arguments: dict[str, object] = {
        "instruments": (_instrument("BTC"),),
        "collector_version": "collector-v2",
        "collector_commit": "collector-commit",
    }
    normalizer_version = "normalizer-v1"
    normalizer_commit = "normalizer-commit"

    if field_name.startswith("collector_"):
        config_arguments[field_name] = "x" * 257
        with pytest.raises(ValueError, match="provenance text bound"):
            HyperliquidTradesCollectorConfig(**config_arguments)  # type: ignore[arg-type]
    else:
        if field_name == "normalizer_version":
            normalizer_version = "x" * 257
        else:
            normalizer_commit = "x" * 257
        with pytest.raises(ValueError, match="provenance text bound"):
            _ProductionHyperliquidTradesCollector(
                _config("BTC"),
                collector_run_id=_COLLECTOR_RUN_ID,
                normalization_run_id=_NORMALIZATION_RUN_ID,
                raw_record_sink=RecordingRawSink(),
                normalization_outcome_sink=RecordingOutcomeSink(),
                connection_factory=factory,
                normalizer_version=normalizer_version,
                normalizer_commit=normalizer_commit,
            )
    assert factory.calls == []

    config_arguments[field_name if field_name.startswith("collector_") else "collector_version"] = (
        "x" * 256
    )
    bounded_config = HyperliquidTradesCollectorConfig(**config_arguments)  # type: ignore[arg-type]
    if field_name == "normalizer_commit":
        normalizer_commit = "x" * 256
    else:
        normalizer_version = "x" * 256
    collector = _ProductionHyperliquidTradesCollector(
        bounded_config,
        collector_run_id=_COLLECTOR_RUN_ID,
        normalization_run_id=_NORMALIZATION_RUN_ID,
        raw_record_sink=RecordingRawSink(),
        normalization_outcome_sink=RecordingOutcomeSink(),
        connection_factory=factory,
        normalizer_version=normalizer_version,
        normalizer_commit=normalizer_commit,
    )
    assert collector.health.session_state is SessionState.IDLE
    assert factory.calls == []


def test_raw_size_and_exact_heartbeat_deadline_are_validated_before_networking() -> None:
    with pytest.raises(ValueError, match="Bronze raw-message limit"):
        _config("BTC", websocket_max_size_bytes=MAX_RAW_APPLICATION_MESSAGE_BYTES + 1)
    assert _config("BTC", websocket_max_size_bytes=MAX_RAW_APPLICATION_MESSAGE_BYTES)

    with pytest.raises(ValueError, match="below 60 seconds"):
        _config("BTC", send_timeout_seconds=8.0)
    assert _config("BTC", send_timeout_seconds=7.999)


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
    transition_journal = collector._attempt_transition_journal
    assert len(transition_journal) == 3 * len(config.instruments)
    assert all(type(item) is SubscriptionAttemptTransition for item in transition_journal)
    acknowledged_transitions = tuple(
        item
        for item in transition_journal
        if item.new_status is SubscriptionAttemptStatus.ACKNOWLEDGED
    )
    assert len(acknowledged_transitions) == len(config.instruments)
    assert all(
        item.previous_status is SubscriptionAttemptStatus.SENT for item in acknowledged_transitions
    )

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

    with pytest.raises(HyperliquidProtocolError, match="message was rejected"):
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
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
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
    assert len(raw_sink.records) == len(outcome_sink.outcomes) == 1
    assert (
        raw_sink.records[0].subscription_attempt_snapshots[0].attempt_status
        is SubscriptionAttemptStatus.SEND_STARTED
    )
    transitions = tuple(
        (item.previous_status, item.new_status) for item in collector._attempt_transition_journal
    )
    assert transitions in (
        (
            (SubscriptionAttemptStatus.PENDING, SubscriptionAttemptStatus.SEND_STARTED),
            (
                SubscriptionAttemptStatus.SEND_STARTED,
                SubscriptionAttemptStatus.ACKNOWLEDGED,
            ),
        ),
        (
            (SubscriptionAttemptStatus.PENDING, SubscriptionAttemptStatus.SEND_STARTED),
            (SubscriptionAttemptStatus.SEND_STARTED, SubscriptionAttemptStatus.SENT),
            (SubscriptionAttemptStatus.SENT, SubscriptionAttemptStatus.ACKNOWLEDGED),
        ),
    )
    assert all(
        type(item) is SubscriptionAttemptTransition
        for item in collector._attempt_transition_journal
    )
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

    with pytest.raises(HyperliquidCaptureValidationError, match="local-validation-failure"):
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
    assert all(type(event) is MarketEventEnvelope for event in batch)
    assert all(event.schema_version == MARKET_EVENT_SCHEMA_VERSION == 2 for event in batch)
    assert not any(isinstance(event, MarketEventEnvelopeV3) for event in batch)
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
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC", "xyz:XYZ100", "@107"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
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
    outcome = outcome_sink.outcomes[-1]
    assert outcome.coverage_transition_ids == ()
    assert tuple(item.observation_key.raw_event_index for item in outcome.raw_event_outcomes) == (
        0,
        1,
        2,
    )
    assert tuple(
        item.normalization_scope_binding.source_selector.value
        for item in outcome.raw_event_outcomes
    ) == ("BTC", "xyz:XYZ100", "@107")
    assert (
        len(
            {
                item.normalization_scope_binding.subscription_spec_id
                for item in outcome.raw_event_outcomes
            }
        )
        == 3
    )
    assert all(
        item.normalization_scope_binding.attempt_status is SubscriptionAttemptStatus.ACKNOWLEDGED
        for item in outcome.raw_event_outcomes
    )
    assert all(not isinstance(item, MarketEventEnvelopeV3) for item in outcome_sink.outcomes)
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
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        config,
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
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
    await _activate(collector, second)
    assert tuple(record.ingress_ordinal for record in raw_sink.records) == tuple(range(6))
    assert {record.subscription_plan.subscription_plan_id for record in raw_sink.records} == {
        raw_sink.records[0].subscription_plan.subscription_plan_id
    }
    assert tuple(record.connection_session.connection_ordinal for record in raw_sink.records) == (
        0,
        0,
        0,
        1,
        1,
        1,
    )
    assert all(
        snapshot.subscription_attempt.attempt_ordinal == 0
        for record in raw_sink.records
        for snapshot in record.subscription_attempt_snapshots
    )
    assert all(
        outcome.normalization_run_id == _NORMALIZATION_RUN_ID for outcome in outcome_sink.outcomes
    )
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
    blocked_hash = "private-blocked-transaction-marker"
    blocked_user = "private-blocked-user-marker"
    connection = ScriptedConnection()
    timeout_runner = FailSelectedTimeout(7.0, occurrence=2)
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC", queue_capacity=1, publish_timeout_seconds=7.0),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
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
    before_blocked_frame = collector.health
    blocked_trade = _trade(tid=2, time_ms=1_720_000_000_124)
    blocked_trade["hash"] = blocked_hash
    blocked_trade["users"] = [blocked_user, "synthetic-blocked-seller"]
    connection.feed(_trades_message(blocked_trade))

    with pytest.raises(HyperliquidBackpressureError) as captured:
        await task
    health = collector.health
    assert health.session_state is SessionState.FAILED
    assert health.sticky_gap is True
    assert health.backpressure_error_count == 1
    assert health.queue_depth == 1
    assert health.emitted_event_count == 1
    assert health.dedup_cache_size == 1
    assert health.accepted_raw_record_count == 3
    assert health.accepted_normalization_outcome_count == 3
    assert health.accepted_raw_record_count - before_blocked_frame.accepted_raw_record_count == 1
    assert (
        health.accepted_normalization_outcome_count
        - before_blocked_frame.accepted_normalization_outcome_count
        == 1
    )
    assert len(raw_sink.records) == len(outcome_sink.outcomes) == 3
    assert outcome_sink.outcomes[-1].frame_status is FrameNormalizationStatus.MATERIALIZED
    assert health.reconnect_count == 0
    assert health.last_failure_category is FailureCategory.BACKPRESSURE
    assert blocked_hash not in repr(outcome_sink.outcomes[-1])
    assert blocked_user not in repr(outcome_sink.outcomes[-1])
    _assert_exported_exception_excludes(captured.value, blocked_hash, blocked_user)


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
async def test_continuous_receiver_traffic_cannot_starve_waiting_heartbeat() -> None:
    connection = ScriptedConnection()
    sleeper = ControlledSleeper()
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=sleeper,
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    for _ in range(100):
        connection.feed("Websocket connection established.")
    await _spin_until(lambda: len(raw_sink.records) >= 1, description="receiver traffic started")
    sleeper.release(sleeper.delays.index(45.0))

    await _spin_until(
        lambda: '{"method":"ping"}' in connection.sent,
        description="heartbeat scheduled amid continuous inbound traffic",
    )
    connection.feed('{"channel":"pong"}')
    await _spin_until(lambda: collector.health.pong_count == 1, description="fair pong commit")

    assert collector.health.ping_count == 1
    assert collector.health.session_state is SessionState.ACTIVE
    assert len(raw_sink.records) == len(outcome_sink.outcomes)
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
async def test_heartbeat_bound_includes_raw_outcome_queue_and_send_waits() -> None:
    clock = VirtualClock()
    connection = TimedPingConnection(clock, ping_send_seconds=3.9)
    delayed_calls = frozenset({3, 4, 5})
    raw_sink = VirtualDelayRawSink(clock, delayed_calls)
    outcome_sink = VirtualDelayOutcomeSink(clock, delayed_calls)
    collector = HyperliquidTradesCollector(
        _config("BTC", queue_capacity=1),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=clock.sleep,
        jitter=lambda _bound: 0.0,
        timeout_runner=VirtualTimeoutRunner(clock),
    )
    task = await _start(collector, connection, 1)
    connection.feed(_acknowledgement("BTC"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.ACTIVE,
        description="virtual-time activation",
    )
    connection.feed(_trades_message(_trade(tid=901)))
    await _spin_until(lambda: collector.health.queue_depth == 1, description="initial full queue")

    clock.advance_to(45.0)
    await _spin_until(lambda: len(connection.ping_start_times) == 1, description="first ping start")
    clock.advance_to(48.9)
    await _spin_until(
        lambda: len(connection.ping_completion_times) == 1,
        description="first ping completion",
    )

    connection.feed(_trades_message(_trade(tid=902, time_ms=1_720_000_000_124)))
    connection.feed('{"channel":"pong"}')
    await _spin_until(lambda: raw_sink.accept_count == 3, description="delayed trade raw accept")
    clock.advance_to(49.8)
    await _spin_until(
        lambda: outcome_sink.accept_count == 3,
        description="delayed trade outcome accept",
    )
    clock.advance_to(50.7)
    await _spin_until(
        lambda: collector.health.received_trade_message_count == 2,
        description="trade blocked at queue after audit acceptance",
    )
    clock.advance_to(55.6)
    first_batch = await collector.receive_batch()
    assert json.loads(first_batch[0].source_event_id)[3] == 901
    await _spin_until(lambda: raw_sink.accept_count == 4, description="delayed pong raw accept")
    clock.advance_to(56.5)
    await _spin_until(
        lambda: outcome_sink.accept_count == 4,
        description="delayed pong outcome accept",
    )
    clock.advance_to(57.4)
    await _spin_until(lambda: collector.health.pong_count == 1, description="delayed pong commit")

    clock.advance_to(93.8)
    connection.feed(_trades_message(_trade(tid=903, time_ms=1_720_000_000_125)))
    await _spin_until(lambda: raw_sink.accept_count == 5, description="next delayed raw accept")
    clock.advance_to(94.7)
    await _spin_until(
        lambda: outcome_sink.accept_count == 5,
        description="next delayed outcome accept",
    )
    # Avoid binary-float rounding at 94.7 + 0.9 while retaining the same bound.
    clock.advance_to(95.61)
    await _spin_until(
        lambda: collector.health.received_trade_message_count == 3,
        description="second queue blockage",
    )
    clock.advance_to(100.5)
    second_batch = await collector.receive_batch()
    assert json.loads(second_batch[0].source_event_id)[3] == 902
    await _spin_until(lambda: len(connection.ping_start_times) == 2, description="second ping")
    clock.advance_to(104.4)
    await _spin_until(
        lambda: len(connection.ping_completion_times) == 2,
        description="second bounded ping completion",
    )

    completed_delta = connection.ping_completion_times[1] - connection.ping_completion_times[0]
    assert completed_delta == pytest.approx(55.5)
    assert completed_delta < 56.0 < 60.0
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
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    factory = ScriptedConnectionFactory(
        OSError("synthetic DNS failure one"),
        OSError("synthetic DNS failure two"),
        connection,
    )
    collector = HyperliquidTradesCollector(
        _config("BTC", backoff_max_seconds=12.0),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
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
    connection.feed(_acknowledgement("BTC"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.ACTIVE,
        description="third-attempt acknowledgement",
    )
    assert len(raw_sink.records) == len(outcome_sink.outcomes) == 1
    record = raw_sink.records[0]
    assert record.collector_run_id == _COLLECTOR_RUN_ID
    assert record.connection_session.connection_ordinal == 2
    assert record.subscription_plan.subscription_plan_id == (
        collector._capture_plan.subscription_plan.subscription_plan_id
    )
    assert len(record.subscription_attempt_snapshots) == 1
    assert record.subscription_attempt_snapshots[0].subscription_attempt.attempt_ordinal == 0
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
    _assert_exported_exception_excludes(captured.value, "sensitive-close-reason")


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
    _assert_exported_exception_excludes(
        captured.value,
        "sensitive-received-reason",
        "sensitive-sent-reason",
    )


@pytest.mark.asyncio
async def test_tls_certificate_verification_failure_is_fatal_without_retry() -> None:
    marker = "private-certificate-failure-marker"
    certificate_error = ssl.SSLCertVerificationError(1, marker)
    factory = ScriptedConnectionFactory(certificate_error, ScriptedConnection())
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=factory,
        utc_now=_fixed_utc_now,
        monotonic_now=_fixed_monotonic_now,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )

    with pytest.raises(HyperliquidTerminalCloseError) as captured:
        await collector.run()
    assert collector.health.session_state is SessionState.FAILED
    assert collector.health.reconnect_count == 0
    _assert_exported_exception_excludes(captured.value, marker)


@pytest.mark.asyncio
async def test_unknown_open_failure_is_terminal_and_sanitized() -> None:
    marker = "private-unknown-open-marker"
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(RuntimeError(marker)),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )

    with pytest.raises(HyperliquidTerminalCloseError) as captured:
        await collector.run()

    assert collector.health.reconnect_count == 0
    _assert_exported_exception_excludes(captured.value, marker)


@pytest.mark.asyncio
async def test_factory_cannot_bypass_privacy_with_a_public_collector_error_type() -> None:
    marker = "private-factory-protocol-marker"
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(HyperliquidProtocolError(marker)),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )

    with pytest.raises(HyperliquidTerminalCloseError) as captured:
        await collector.run()

    _assert_exported_exception_excludes(captured.value, marker)
    assert collector.health.last_failure_category is (
        FailureCategory.TERMINAL_CLOSE_OR_PROTOCOL_VIOLATION
    )


@pytest.mark.asyncio
async def test_unknown_send_failure_is_terminal_and_sanitized() -> None:
    connection = UnknownFailingSendConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )

    with pytest.raises(HyperliquidTerminalCloseError) as captured:
        await collector.run()

    assert connection.sent == [_expected_subscription("BTC")]
    assert collector.health.reconnect_count == 0
    _assert_exported_exception_excludes(captured.value, "private-unknown-send-marker")


@pytest.mark.asyncio
async def test_unknown_receive_failure_and_consumer_outcome_are_sanitized() -> None:
    marker = "private-unknown-receive-marker"
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    consumer = asyncio.create_task(collector.receive_batch())
    connection.feed(RuntimeError(marker))

    with pytest.raises(HyperliquidTerminalCloseError) as captured:
        await task
    with pytest.raises(HyperliquidCollectorTerminatedError) as terminated:
        await consumer

    _assert_exported_exception_excludes(captured.value, marker)
    _assert_exported_exception_excludes(terminated.value, marker)
    assert marker not in repr(collector.health)


@pytest.mark.asyncio
async def test_context_exit_failure_cannot_mask_or_export_private_transport_state() -> None:
    connection = ScriptedConnection()
    factory = ExitFailingConnectionFactory(connection)
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=factory,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed('{"channel":"unsupported"}')

    with pytest.raises(HyperliquidProtocolError) as captured:
        await task

    _assert_exported_exception_excludes(captured.value, "private-context-exit-marker")
    assert collector.health.last_failure_category is (
        FailureCategory.TERMINAL_CLOSE_OR_PROTOCOL_VIOLATION
    )
    assert collector.health.reconnect_count == 0
    assert len(factory.calls) == 1


@pytest.mark.asyncio
async def test_cancellation_wins_over_private_connection_context_exit_failure() -> None:
    marker = "private-context-exit-marker"
    connection = ScriptedConnection()
    factory = ExitFailingConnectionFactory(connection)
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        connection_factory=factory,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    consumer = asyncio.create_task(collector.receive_batch())

    task.cancel()
    with pytest.raises(asyncio.CancelledError) as captured:
        await task
    with pytest.raises(HyperliquidCollectorTerminatedError) as terminated:
        await consumer

    _assert_exported_exception_excludes(captured.value, marker)
    _assert_exported_exception_excludes(terminated.value, marker)
    assert collector.health.session_state is SessionState.STOPPED
    assert collector.health.last_failure_category is None
    assert collector.health.reconnect_count == 0
    assert len(factory.calls) == 1
    assert marker not in repr(collector.health)


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
async def test_exported_producer_and_consumer_graphs_exclude_payload_state() -> None:
    raw_marker = "private-exported-raw-marker"
    parsed_marker = "private-exported-parsed-marker"
    user_marker = "private-exported-user-marker"
    transaction_marker = "private-exported-transaction-marker"
    raw_destination_marker = "private-exported-raw-destination-marker"
    outcome_destination_marker = "private-exported-outcome-destination-marker"
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink(raw_destination_marker)
    outcome_sink = RecordingOutcomeSink(outcome_destination_marker)
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    marked_trade = _trade(tid=8_001)
    marked_trade["hash"] = transaction_marker
    marked_trade["users"] = [user_marker, "synthetic-other-user"]
    marked_trade["future_private_field"] = raw_marker
    connection.feed(_trades_message(marked_trade))
    await _spin_until(lambda: collector.health.queue_depth == 1, description="marked v2 batch")
    connection.feed(json.dumps({"channel": parsed_marker, "payload": raw_marker}))

    with pytest.raises(HyperliquidProtocolError) as producer_failure:
        await task

    assert raw_marker.encode() in raw_sink.records[-1].application_message_bytes
    assert transaction_marker in collector._queue._queue[0][0].source_transaction_id  # type: ignore[attr-defined]
    _assert_exported_exception_excludes(
        producer_failure.value,
        raw_marker,
        parsed_marker,
        user_marker,
        transaction_marker,
        raw_destination_marker,
        outcome_destination_marker,
    )

    queued_batch = await collector.receive_batch()
    assert queued_batch[0].source_transaction_id == transaction_marker
    del queued_batch
    with pytest.raises(HyperliquidCollectorTerminatedError) as consumer_failure:
        await collector.receive_batch()
    _assert_exported_exception_excludes(
        consumer_failure.value,
        raw_marker,
        parsed_marker,
        user_marker,
        transaction_marker,
        raw_destination_marker,
        outcome_destination_marker,
    )
    with pytest.raises(HyperliquidCollectorStateError) as lifecycle_failure:
        await collector.run()
    _assert_exported_exception_excludes(
        lifecycle_failure.value,
        raw_marker,
        parsed_marker,
        user_marker,
        transaction_marker,
        raw_destination_marker,
        outcome_destination_marker,
    )


@pytest.mark.asyncio
async def test_consumer_lifecycle_error_graph_excludes_retained_collector_state() -> None:
    raw_marker = "private-consumer-lifecycle-raw-marker"
    destination_marker = "private-consumer-lifecycle-destination"
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink(destination_marker)
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=RecordingOutcomeSink(),
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    producer = await _start(collector, connection, 1)
    acknowledgement = cast(dict[str, object], json.loads(_acknowledgement("BTC")))
    acknowledgement["private_test_field"] = raw_marker
    connection.feed(json.dumps(acknowledgement, separators=(",", ":")))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.ACTIVE,
        description="private lifecycle acknowledgement",
    )
    consumer = asyncio.create_task(collector.receive_batch())
    await asyncio.sleep(0)

    with pytest.raises(HyperliquidCollectorStateError, match="one logical") as captured:
        await collector.receive_batch()
    _assert_exported_exception_excludes(
        captured.value,
        raw_marker,
        destination_marker,
    )

    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    await _cancel(producer)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutated_error",
    (
        "sink-category",
        "sink-category-deleted",
        "validation-failure",
        "validation-failure-deleted",
        "validation-category",
        "validation-category-deleted",
    ),
)
async def test_export_classifier_rejects_mutated_public_error_attributes(
    mutated_error: str,
) -> None:
    destination_marker = "private-mutated-error-destination"
    raw_sink = RecordingRawSink(destination_marker)
    destination = raw_sink.destination_id

    async def adversarial_sleeper(_delay: float) -> None:
        failure: Exception
        if mutated_error.startswith("sink-category"):
            sink_failure = HyperliquidSinkBoundaryError(
                SinkFailureCategory.RAW_ACCEPTANCE_AMBIGUOUS
            )
            if mutated_error.endswith("deleted"):
                del sink_failure.category
            else:
                sink_failure.category = destination  # type: ignore[assignment]
            failure = sink_failure
        else:
            sanitized = SanitizedValidationFailure(ValidationFailureCategory.INVALID_VALUE)
            capture_failure = HyperliquidCaptureValidationError(sanitized)
            if mutated_error == "validation-failure":
                capture_failure.failure = destination  # type: ignore[assignment]
            elif mutated_error == "validation-failure-deleted":
                del capture_failure.failure
            elif mutated_error == "validation-category":
                object.__setattr__(sanitized, "category", destination)
            else:
                object.__delattr__(sanitized, "category")
            failure = capture_failure
        raise failure

    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=RecordingOutcomeSink(),
        connection_factory=ScriptedConnectionFactory(OSError("synthetic retryable open failure")),
        sleeper=adversarial_sleeper,
        jitter=lambda _bound: 0.0,
    )

    with pytest.raises(HyperliquidCollectorStateError) as captured:
        await collector.run()
    _assert_exported_exception_excludes(
        captured.value,
        destination_marker,
        forbidden_objects=(destination,),
    )
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,category",
    [
        ("reject", SinkFailureCategory.RAW_EXPLICIT_REJECTION),
        ("block", SinkFailureCategory.RAW_ACCEPTANCE_TIMEOUT),
        ("exception", SinkFailureCategory.RAW_ACCEPTANCE_AMBIGUOUS),
        ("wrong-type", SinkFailureCategory.RAW_ACCEPTANCE_INVALID),
        ("wrong-id", SinkFailureCategory.RAW_ACCEPTANCE_INVALID),
        ("wrong-integrity", SinkFailureCategory.RAW_ACCEPTANCE_INVALID),
        ("wrong-destination", SinkFailureCategory.RAW_ACCEPTANCE_INVALID),
    ],
)
async def test_raw_sink_failure_is_terminal_sanitized_and_never_retried(
    mode: str,
    category: SinkFailureCategory,
) -> None:
    raw_frame_marker = "private-raw-failure-frame-marker"
    sink_marker = "private-raw-sink-marker"
    destination_marker = "private-raw-destination-marker"
    connection = ScriptedConnection()
    factory = ScriptedConnectionFactory(connection, ScriptedConnection())
    raw_sink = FaultingRawSink(
        mode,
        marker=sink_marker,
        destination=destination_marker,
    )
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC", raw_sink_timeout_seconds=(0.01 if mode == "block" else 1.0)),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=factory,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed(json.dumps({"channel": raw_frame_marker}))

    with pytest.raises(HyperliquidSinkBoundaryError) as captured:
        await task

    assert captured.value.category is category
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert raw_sink.accept_count == 1
    assert outcome_sink.outcomes == []
    assert collector.health.accepted_raw_record_count == 0
    assert collector.health.accepted_normalization_outcome_count == 0
    assert collector.health.last_sink_failure_category is category
    assert collector.health.last_failure_category is FailureCategory.RAW_SINK_FAILURE
    assert len(factory.calls) == 1
    assert raw_sink.close_count == outcome_sink.close_count == 1
    _assert_exported_exception_excludes(
        captured.value,
        raw_frame_marker,
        sink_marker,
        destination_marker,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_late", (False, True), ids=("late-success", "late-failure"))
async def test_raw_sink_deadline_does_not_wait_for_cancellation_suppression(
    monkeypatch: pytest.MonkeyPatch,
    fail_late: bool,
) -> None:
    router_calls = 0
    original_router = websocket_module.route_hyperliquid_websocket_message

    def tracked_router(message: object) -> object:
        nonlocal router_calls
        router_calls += 1
        return original_router(message)

    monkeypatch.setattr(websocket_module, "route_hyperliquid_websocket_message", tracked_router)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop_failures: list[dict[str, object]] = []

    def capture_loop_failure(_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        loop_failures.append(context)

    loop.set_exception_handler(capture_loop_failure)
    connection = ScriptedConnection()
    raw_sink = CancellationSuppressingRawSink(fail_late=fail_late)
    outcome_sink = RecordingOutcomeSink()
    factory = ScriptedConnectionFactory(connection, ScriptedConnection())
    collector = HyperliquidTradesCollector(
        _config("BTC", raw_sink_timeout_seconds=0.01),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=factory,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    started_at = asyncio.get_running_loop().time()
    connection.feed("Websocket connection established.")
    await raw_sink.accept_started.wait()

    try:
        await asyncio.sleep(0.025)
        assert task.done()
        with pytest.raises(HyperliquidSinkBoundaryError) as captured:
            await task
        assert captured.value.category is SinkFailureCategory.RAW_ACCEPTANCE_TIMEOUT
        assert asyncio.get_running_loop().time() - started_at < 0.05
        assert raw_sink.accept_count == 1
        assert router_calls == 0
        assert outcome_sink.outcomes == []
        assert collector.health.accepted_raw_record_count == 0
        assert collector.health.accepted_normalization_outcome_count == 0
        assert collector.health.received_control_message_count == 0
        assert collector.health.queue_depth == collector.health.dedup_cache_size == 0
        assert len(factory.calls) == 1
        await asyncio.wait_for(raw_sink.late_returned.wait(), timeout=0.2)
        await _spin_until(
            lambda: not collector._quarantined_sink_operations,
            description="late raw acceptance privately reaped",
        )
        assert collector.health.accepted_raw_record_count == 0
        assert loop_failures == []
    finally:
        loop.set_exception_handler(previous_handler)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_outcome_sink_deadline_ignores_late_success_and_commits_nothing() -> None:
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = CancellationSuppressingOutcomeSink()
    factory = ScriptedConnectionFactory(connection, ScriptedConnection())
    collector = HyperliquidTradesCollector(
        _config("BTC", outcome_sink_timeout_seconds=0.01),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=factory,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    started_at = asyncio.get_running_loop().time()
    connection.feed(_acknowledgement("BTC"))
    await outcome_sink.accept_started.wait()

    await asyncio.sleep(0.025)
    assert task.done()
    with pytest.raises(HyperliquidSinkBoundaryError) as captured:
        await task
    assert captured.value.category is SinkFailureCategory.OUTCOME_ACCEPTANCE_TIMEOUT
    assert asyncio.get_running_loop().time() - started_at < 0.05
    assert outcome_sink.accept_count == 1
    assert len(raw_sink.records) == 1
    assert collector.health.accepted_raw_record_count == 1
    assert collector.health.accepted_normalization_outcome_count == 0
    assert collector.health.acknowledged_coins == ()
    assert collector.health.received_control_message_count == 0
    assert collector.health.queue_depth == collector.health.dedup_cache_size == 0
    assert len(factory.calls) == 1

    await asyncio.wait_for(outcome_sink.late_returned.wait(), timeout=0.2)
    await _spin_until(
        lambda: not collector._quarantined_sink_operations,
        description="late outcome acceptance privately reaped",
    )
    assert collector.health.accepted_normalization_outcome_count == 0
    assert collector.health.acknowledged_coins == ()


@pytest.mark.asyncio
async def test_noncooperative_close_cannot_delay_or_mask_primary_protocol_failure() -> None:
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = CancellationSuppressingCloseOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC", outcome_sink_timeout_seconds=0.01),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    started_at = asyncio.get_running_loop().time()
    connection.feed('{"channel":"unsupported"}')
    await outcome_sink.close_started.wait()

    await asyncio.sleep(0.025)
    assert task.done()
    with pytest.raises(HyperliquidProtocolError, match="message was rejected"):
        await task
    assert asyncio.get_running_loop().time() - started_at < 0.05
    assert collector.health.accepted_raw_record_count == 1
    assert collector.health.accepted_normalization_outcome_count == 1
    assert collector.health.last_sink_failure_category is SinkFailureCategory.OUTCOME_CLOSE_TIMEOUT
    assert outcome_sink.close_count == raw_sink.close_count == 1

    await asyncio.wait_for(outcome_sink.late_closed.wait(), timeout=0.2)
    await _spin_until(
        lambda: not collector._quarantined_sink_operations,
        description="late outcome close privately reaped",
    )


@pytest.mark.asyncio
async def test_blocked_raw_acceptance_prevents_router_and_every_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_router = websocket_module.route_hyperliquid_websocket_message
    router_calls = 0

    def tracked_router(message: object) -> object:
        nonlocal router_calls
        router_calls += 1
        return original_router(message)

    monkeypatch.setattr(websocket_module, "route_hyperliquid_websocket_message", tracked_router)
    connection = ScriptedConnection()
    raw_sink = FaultingRawSink("block")
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed("Websocket connection established.")
    await raw_sink.accept_started.wait()

    assert router_calls == 0
    assert outcome_sink.outcomes == []
    assert collector.health.accepted_raw_record_count == 0
    assert collector.health.accepted_normalization_outcome_count == 0
    assert collector.health.received_control_message_count == 0
    assert collector.health.queue_depth == collector.health.dedup_cache_size == 0

    raw_sink.release.set()
    await _spin_until(
        lambda: collector.health.received_control_message_count == 1,
        description="raw-first greeting commit",
    )
    assert router_calls == 1
    assert len(raw_sink.records) == len(outcome_sink.outcomes) == 1
    assert collector.health.accepted_raw_record_count == 1
    assert collector.health.accepted_normalization_outcome_count == 1
    await _cancel(task)


@pytest.mark.asyncio
async def test_acceptance_telemetry_advances_at_each_verified_sink_boundary() -> None:
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = FaultingOutcomeSink("block")
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed("Websocket connection established.")
    await outcome_sink.accept_started.wait()

    waiting = collector.health
    assert waiting.accepted_raw_record_count == 1
    assert waiting.accepted_normalization_outcome_count == 0
    assert waiting.received_control_message_count == 0
    assert waiting.queue_depth == waiting.dedup_cache_size == 0

    outcome_sink.release.set()
    await _spin_until(
        lambda: collector.health.received_control_message_count == 1,
        description="outcome-accepted greeting commit",
    )
    committed = collector.health
    assert committed.accepted_raw_record_count == 1
    assert committed.accepted_normalization_outcome_count == 1
    assert committed.received_control_message_count == 1
    await _cancel(task)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,category",
    [
        ("reject", SinkFailureCategory.OUTCOME_EXPLICIT_REJECTION),
        ("block", SinkFailureCategory.OUTCOME_ACCEPTANCE_TIMEOUT),
        ("exception", SinkFailureCategory.OUTCOME_ACCEPTANCE_AMBIGUOUS),
        ("wrong-type", SinkFailureCategory.OUTCOME_ACCEPTANCE_INVALID),
        ("wrong-id", SinkFailureCategory.OUTCOME_ACCEPTANCE_INVALID),
        ("wrong-destination", SinkFailureCategory.OUTCOME_ACCEPTANCE_INVALID),
    ],
)
async def test_outcome_sink_failure_prevents_every_external_commit_and_retry(
    mode: str,
    category: SinkFailureCategory,
) -> None:
    raw_frame_marker = "private-outcome-failure-frame-marker"
    sink_marker = "private-outcome-sink-marker"
    raw_destination_marker = "private-outcome-test-raw-destination"
    outcome_destination_marker = "private-outcome-destination-marker"
    connection = ScriptedConnection()
    factory = ScriptedConnectionFactory(connection, ScriptedConnection())
    raw_sink = RecordingRawSink(raw_destination_marker)
    outcome_sink = FaultingOutcomeSink(
        mode,
        marker=sink_marker,
        destination=outcome_destination_marker,
    )
    collector = HyperliquidTradesCollector(
        _config("BTC", outcome_sink_timeout_seconds=(0.01 if mode == "block" else 1.0)),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=factory,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    acknowledgement = cast(dict[str, object], json.loads(_acknowledgement("BTC")))
    acknowledgement["private_test_field"] = raw_frame_marker
    connection.feed(json.dumps(acknowledgement, separators=(",", ":")))

    with pytest.raises(HyperliquidSinkBoundaryError) as captured:
        await task

    assert captured.value.category is category
    assert len(raw_sink.records) == 1
    assert outcome_sink.accept_count == 1
    assert collector.health.acknowledged_coins == ()
    assert collector.health.received_control_message_count == 0
    assert collector.health.accepted_raw_record_count == 1
    assert collector.health.accepted_normalization_outcome_count == 0
    assert collector.health.queue_depth == collector.health.dedup_cache_size == 0
    assert collector.health.last_sink_failure_category is category
    assert (
        collector.health.last_failure_category is FailureCategory.NORMALIZATION_OUTCOME_SINK_FAILURE
    )
    assert len(factory.calls) == 1
    assert raw_sink.close_count == outcome_sink.close_count == 1
    _assert_exported_exception_excludes(
        captured.value,
        raw_frame_marker,
        sink_marker,
        raw_destination_marker,
        outcome_destination_marker,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("message_kind", ("trade", "pong"))
async def test_failed_outcome_acceptance_discards_trade_candidates_and_pong_state(
    message_kind: str,
) -> None:
    connection = ScriptedConnection()
    factory = ScriptedConnectionFactory(connection, ScriptedConnection())
    raw_sink = RecordingRawSink()
    outcome_sink = FaultingOutcomeSink("success")
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=factory,
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    before = collector.health
    outcome_sink.mode = "reject"
    connection.feed(
        _trades_message(_trade(tid=820)) if message_kind == "trade" else '{"channel":"pong"}'
    )

    with pytest.raises(HyperliquidSinkBoundaryError) as captured:
        await task

    assert captured.value.category is SinkFailureCategory.OUTCOME_EXPLICIT_REJECTION
    assert len(raw_sink.records) == 2
    assert outcome_sink.accept_count == 2
    assert len(outcome_sink.outcomes) == 1
    after = collector.health
    assert after.queue_depth == before.queue_depth == 0
    assert after.dedup_cache_size == before.dedup_cache_size == 0
    assert after.emitted_event_count == before.emitted_event_count == 0
    assert after.duplicate_event_count == before.duplicate_event_count == 0
    assert after.received_trade_message_count == before.received_trade_message_count == 0
    assert after.received_control_message_count == before.received_control_message_count == 1
    assert after.pong_count == before.pong_count == 0
    assert before.accepted_raw_record_count == before.accepted_normalization_outcome_count == 1
    assert after.accepted_raw_record_count == 2
    assert after.accepted_normalization_outcome_count == 1
    assert len(factory.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_sink", ["raw", "outcome"])
async def test_cancellation_during_sink_accept_remains_cancellation_without_retry(
    blocked_sink: str,
) -> None:
    connection = ScriptedConnection()
    raw_sink: RecordingRawSink | FaultingRawSink
    outcome_sink: RecordingOutcomeSink | FaultingOutcomeSink
    raw_sink = FaultingRawSink("block") if blocked_sink == "raw" else RecordingRawSink()
    outcome_sink = (
        FaultingOutcomeSink("block") if blocked_sink == "outcome" else RecordingOutcomeSink()
    )
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed("Websocket connection established.")
    selected = raw_sink if blocked_sink == "raw" else outcome_sink
    await selected.accept_started.wait()  # type: ignore[union-attr]

    await _cancel(task)

    assert collector.health.session_state is SessionState.STOPPED
    assert collector.health.reconnect_count == 0
    assert raw_sink.close_count == outcome_sink.close_count == 1
    if blocked_sink == "raw":
        assert outcome_sink.outcomes == []
        assert collector.health.accepted_raw_record_count == 0
        assert collector.health.accepted_normalization_outcome_count == 0
    else:
        assert len(raw_sink.records) == 1
        assert collector.health.accepted_raw_record_count == 1
        assert collector.health.accepted_normalization_outcome_count == 0
    assert (
        collector.health.accepted_normalization_outcome_count
        <= collector.health.accepted_raw_record_count
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        pytest.param("", id="empty-text"),
        pytest.param(' { "channel" : "unknown-private-marker" } ', id="whitespace-unicode"),
        pytest.param('{"channel":"pong","channel":"trades"}', id="duplicate-keys"),
        pytest.param('{"channel":"pong","value":NaN}', id="non-finite"),
        pytest.param(b"\x00\xffarbitrary-binary", id="binary"),
    ],
)
async def test_every_successful_recv_is_raw_accepted_before_protocol_rejection(
    message: str | bytes,
) -> None:
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed(message)

    with pytest.raises(HyperliquidProtocolError, match="message was rejected"):
        await task

    assert len(raw_sink.records) == len(outcome_sink.outcomes) == 1
    expected_bytes = message.encode() if type(message) is str else message
    assert raw_sink.records[0].application_message_bytes == expected_bytes
    assert (
        outcome_sink.outcomes[0].frame_status is FrameNormalizationStatus.REJECTED_BEFORE_INDEXING
    )
    assert outcome_sink.outcomes[0].coverage_transition_ids == ()
    assert collector.health.accepted_raw_record_count == 1
    assert collector.health.accepted_normalization_outcome_count == 1


@pytest.mark.asyncio
async def test_open_receive_and_internal_utf8_failures_create_no_fictional_raw_record() -> None:
    for failure in (
        OSError("synthetic-open-failure"),
        UnicodeDecodeError("utf-8", b"\xffprivate-utf8-marker", 0, 1, "invalid"),
    ):
        raw_sink = RecordingRawSink()
        outcome_sink = RecordingOutcomeSink()
        sleeper = ControlledSleeper()
        if isinstance(failure, OSError):
            connection = ScriptedConnection()
            factory = ScriptedConnectionFactory(failure, connection)
            collector = HyperliquidTradesCollector(
                _config("BTC"),
                raw_record_sink=raw_sink,
                normalization_outcome_sink=outcome_sink,
                connection_factory=factory,
                sleeper=sleeper,
                jitter=lambda _bound: 0.0,
            )
            open_task = asyncio.create_task(collector.run())

            def open_collector_is_backing_off(
                current: HyperliquidTradesCollector = collector,
            ) -> bool:
                return current.health.session_state is SessionState.BACKING_OFF

            await _spin_until(
                open_collector_is_backing_off,
                description="open failure backoff",
            )
            await _cancel(open_task)
        else:
            connection = ScriptedConnection()
            collector = HyperliquidTradesCollector(
                _config("BTC"),
                raw_record_sink=raw_sink,
                normalization_outcome_sink=outcome_sink,
                connection_factory=ScriptedConnectionFactory(connection),
                sleeper=sleeper,
                jitter=lambda _bound: 0.0,
            )
            receive_task = await _start(collector, connection, 1)
            connection.feed(failure)
            with pytest.raises(HyperliquidTerminalCloseError) as captured:
                await receive_task
            _assert_exported_exception_excludes(captured.value, "private-utf8-marker")
        assert raw_sink.records == []
        assert outcome_sink.outcomes == []
        assert collector.health.accepted_raw_record_count == 0
        assert collector.health.accepted_normalization_outcome_count == 0


@pytest.mark.asyncio
async def test_successful_recv_wrong_runtime_type_fails_without_fabricated_raw_record() -> None:
    connection = WrongTypeRecvConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.release_wrong_type.set()

    with pytest.raises(HyperliquidCaptureValidationError, match="local-validation-failure"):
        await task

    assert raw_sink.records == []
    assert outcome_sink.outcomes == []
    assert collector.health.accepted_raw_record_count == 0
    assert collector.health.accepted_normalization_outcome_count == 0


@pytest.mark.asyncio
async def test_private_local_preparation_failure_still_commits_one_sanitized_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_locally(
        _collector: object,
        _message: object,
        _record: RawMarketDataRecord,
    ) -> object:
        private_local_marker = "private-local-preparation-marker"
        raise RuntimeError(private_local_marker)

    monkeypatch.setattr(_ProductionHyperliquidTradesCollector, "_prepare_message", fail_locally)
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed("Websocket connection established.")

    with pytest.raises(HyperliquidProtocolError) as captured:
        await task

    assert len(raw_sink.records) == len(outcome_sink.outcomes) == 1
    assert (
        outcome_sink.outcomes[0].frame_status is FrameNormalizationStatus.REJECTED_BEFORE_INDEXING
    )
    assert outcome_sink.outcomes[0].evidence == (NormalizationEvidence.LOCAL_CONTRACT_FAILURE,)
    _assert_exported_exception_excludes(captured.value, "private-local-preparation-marker")


@pytest.mark.asyncio
async def test_deep_json_is_sanitized_after_raw_and_preindex_outcome_acceptance() -> None:
    marker = "deep-json-private-marker"
    message = "[" * 20_000 + f'"{marker}"' + "]" * 20_000
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed(message)

    with pytest.raises(HyperliquidProtocolError) as captured:
        await task

    assert marker.encode() in raw_sink.records[0].application_message_bytes
    assert outcome_sink.outcomes[0].evidence == (NormalizationEvidence.PROTOCOL_REJECTION,)
    _assert_exported_exception_excludes(captured.value, marker)
    assert marker not in repr(collector.health)
    assert marker not in repr(outcome_sink.outcomes)


@pytest.mark.asyncio
async def test_raw_then_outcome_acceptance_precedes_control_and_v2_visibility() -> None:
    journal: list[str] = []
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink(journal=journal)
    outcome_sink = RecordingOutcomeSink(journal=journal)
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed("Websocket connection established.")
    connection.feed(_acknowledgement("BTC"))
    await _spin_until(
        lambda: collector.health.session_state is SessionState.ACTIVE,
        description="ack committed after outcome",
    )
    connection.feed('{"channel":"pong"}')
    connection.feed('{"channel":"trades","data":[]}')
    connection.feed(_trades_message(_trade()))
    batch = await collector.receive_batch()

    assert len(batch) == 1
    assert len(raw_sink.records) == len(outcome_sink.outcomes) == 5
    assert [item.frame_status for item in outcome_sink.outcomes] == [
        FrameNormalizationStatus.CONTROL_NO_EVENT,
        FrameNormalizationStatus.CONTROL_NO_EVENT,
        FrameNormalizationStatus.CONTROL_NO_EVENT,
        FrameNormalizationStatus.VALID_EMPTY_MARKET_FRAME,
        FrameNormalizationStatus.MATERIALIZED,
    ]
    assert journal[:10] == [
        "raw-accepted",
        "outcome-accepted",
        "raw-accepted",
        "outcome-accepted",
        "raw-accepted",
        "outcome-accepted",
        "raw-accepted",
        "outcome-accepted",
        "raw-accepted",
        "outcome-accepted",
    ]
    assert collector.health.accepted_raw_record_count == 5
    assert collector.health.accepted_normalization_outcome_count == 5
    await _cancel(task)
    assert journal[-2:] == ["outcome-closed", "raw-closed"]


@pytest.mark.asyncio
async def test_duplicate_mixed_rejected_and_conflict_outcomes_are_index_exact() -> None:
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    original = _trade(tid=500)
    connection.feed(_trades_message(original))
    await collector.receive_batch()
    connection.feed(
        _trades_message(
            original,
            _trade(tid=501, time_ms=1_720_000_000_124),
        )
    )
    mixed_batch = await collector.receive_batch()
    assert len(mixed_batch) == 1
    mixed = outcome_sink.outcomes[-1]
    assert mixed.frame_status is FrameNormalizationStatus.MIXED_SUCCESS
    assert tuple(item.observation_key.raw_event_index for item in mixed.raw_event_outcomes) == (
        0,
        1,
    )
    assert tuple(item.disposition for item in mixed.raw_event_outcomes) == (
        RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
        RawEventDisposition.MATERIALIZED_NEW,
    )

    invalid = _trade(tid=502, time_ms=1_720_000_000_125)
    invalid["px"] = "invalid"
    connection.feed(_trades_message(_trade(tid=503), invalid))
    with pytest.raises(HyperliquidProtocolError):
        await task
    rejected = outcome_sink.outcomes[-1]
    assert rejected.frame_status is FrameNormalizationStatus.REJECTED_AFTER_INDEXING
    assert rejected.coverage_transition_ids == ()
    assert collector.health.emitted_event_count == 2
    assert collector.health.dedup_cache_size == 2


@pytest.mark.asyncio
async def test_frame_local_replay_identity_survives_candidate_lru_eviction() -> None:
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC", dedup_capacity=1),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    retained = _trade(tid=991)
    connection.feed(_trades_message(retained))
    await collector.receive_batch()
    before = collector.health

    conflict = dict(retained)
    conflict["px"] = "987.654"
    connection.feed(
        _trades_message(
            retained,
            _trade(tid=992, time_ms=1_720_000_000_124),
            conflict,
        )
    )

    with pytest.raises(HyperliquidSourceEventConflictError):
        await task

    outcome = outcome_sink.outcomes[-1]
    assert outcome.frame_status is FrameNormalizationStatus.SOURCE_EVENT_CONFLICT
    assert tuple(item.disposition for item in outcome.raw_event_outcomes) == (
        RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
        RawEventDisposition.NOT_MATERIALIZED_FRAME_ABORTED,
        RawEventDisposition.SOURCE_EVENT_CONFLICT,
    )
    after = collector.health
    assert after.queue_depth == before.queue_depth == 0
    assert after.dedup_cache_size == before.dedup_cache_size == 1
    assert after.emitted_event_count == before.emitted_event_count == 1
    assert after.duplicate_event_count == before.duplicate_event_count == 0


@pytest.mark.asyncio
async def test_frame_local_exact_replay_remains_duplicate_after_candidate_lru_eviction() -> None:
    connection = ScriptedConnection()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC", dedup_capacity=1),
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    retained = _trade(tid=993)
    connection.feed(_trades_message(retained))
    await collector.receive_batch()
    connection.feed(
        _trades_message(
            retained,
            _trade(tid=994, time_ms=1_720_000_000_124),
            retained,
        )
    )
    batch = await collector.receive_batch()

    assert [json.loads(event.source_event_id)[3] for event in batch] == [994]
    outcome = outcome_sink.outcomes[-1]
    assert outcome.frame_status is FrameNormalizationStatus.MIXED_SUCCESS
    assert tuple(item.disposition for item in outcome.raw_event_outcomes) == (
        RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
        RawEventDisposition.MATERIALIZED_NEW,
        RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED,
    )
    assert collector.health.emitted_event_count == 2
    assert collector.health.duplicate_event_count == 2
    assert collector.health.dedup_cache_size == 1
    await _cancel(task)


@pytest.mark.asyncio
async def test_exact_duplicate_frames_remain_distinct_bronze_records() -> None:
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    frame = _trades_message(_trade(tid=650))
    connection.feed(frame)
    await collector.receive_batch()
    connection.feed(frame)
    await _spin_until(
        lambda: collector.health.duplicate_event_count == 1,
        description="duplicate audit outcome",
    )

    first_raw, replay_raw = raw_sink.records[-2:]
    first_outcome, replay_outcome = outcome_sink.outcomes[-2:]
    assert first_raw.application_message_bytes == replay_raw.application_message_bytes
    assert first_raw.raw_record_id != replay_raw.raw_record_id
    assert replay_raw.ingress_ordinal == first_raw.ingress_ordinal + 1
    assert first_outcome.frame_status is FrameNormalizationStatus.MATERIALIZED
    assert replay_outcome.frame_status is FrameNormalizationStatus.DUPLICATES_ONLY
    assert replay_outcome.raw_event_outcomes[0].disposition is (
        RawEventDisposition.EXACT_DUPLICATE_SUPPRESSED
    )
    await _cancel(task)


@pytest.mark.asyncio
async def test_source_conflict_outcome_is_accepted_without_payload_privacy_leak() -> None:
    retained_hash = "private-retained-hash-marker"
    retained_user = "private-retained-user-marker"
    private_hash = "private-conflict-hash-marker"
    private_user = "private-conflict-user-marker"
    connection = ScriptedConnection()
    raw_sink = RecordingRawSink()
    outcome_sink = RecordingOutcomeSink()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    await _activate(collector, connection)
    original = _trade(tid=700)
    original["hash"] = retained_hash
    original["users"] = [retained_user, "synthetic-retained-seller"]
    connection.feed(_trades_message(original))
    first_batch = await collector.receive_batch()
    assert first_batch[0].source_transaction_id == retained_hash
    del first_batch
    assert retained_hash not in repr(collector._dedup_cache)
    assert retained_user not in repr(collector._dedup_cache)
    conflict = dict(original)
    conflict["hash"] = private_hash
    conflict["users"] = [private_user, "synthetic-other-user"]
    connection.feed(_trades_message(conflict))

    with pytest.raises(HyperliquidSourceEventConflictError) as captured:
        await task

    outcome = outcome_sink.outcomes[-1]
    assert outcome.frame_status is FrameNormalizationStatus.SOURCE_EVENT_CONFLICT
    assert outcome.raw_event_outcomes[0].disposition is RawEventDisposition.SOURCE_EVENT_CONFLICT
    assert outcome.coverage_transition_ids == ()
    assert private_hash not in repr(outcome)
    assert private_user not in repr(outcome)
    _assert_exported_exception_excludes(
        captured.value,
        retained_hash,
        retained_user,
        private_hash,
        private_user,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("blocking_close", ["outcome", "raw"])
async def test_cancellation_during_each_sink_close_attempts_both_once(
    blocking_close: str,
) -> None:
    journal: list[str] = []
    raw_sink = ClosingRawSink("block" if blocking_close == "raw" else "success", journal=journal)
    outcome_sink = ClosingOutcomeSink(
        "block" if blocking_close == "outcome" else "success",
        journal=journal,
    )
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    consumer = asyncio.create_task(collector.receive_batch())
    await asyncio.sleep(0)
    task.cancel()
    selected = outcome_sink if blocking_close == "outcome" else raw_sink
    await selected.close_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert outcome_sink.close_count == raw_sink.close_count == 1
    assert journal[0] == "outcome-close-started"
    assert journal[-1] == "raw-close-started"
    assert collector._running is False
    with pytest.raises(HyperliquidCollectorTerminatedError) as terminated:
        await consumer
    assert terminated.value.session_state is SessionState.STOPPED
    pending_names = {item.get_name() for item in asyncio.all_tasks() if not item.done()}
    assert "hyperliquid-trades-receiver" not in pending_names
    assert "hyperliquid-application-heartbeat" not in pending_names
    assert "hyperliquid-subscription-activation" not in pending_names


@pytest.mark.asyncio
async def test_sink_close_timeouts_are_bounded_ordered_and_wake_consumer() -> None:
    journal: list[str] = []
    raw_sink = ClosingRawSink("block", journal=journal)
    outcome_sink = ClosingOutcomeSink("block", journal=journal)
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config(
            "BTC",
            raw_sink_timeout_seconds=0.01,
            outcome_sink_timeout_seconds=0.01,
        ),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    consumer = asyncio.create_task(collector.receive_batch())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)

    assert outcome_sink.close_count == raw_sink.close_count == 1
    assert journal == ["outcome-close-started", "raw-close-started"]
    assert collector.health.last_sink_failure_category is SinkFailureCategory.OUTCOME_CLOSE_TIMEOUT
    assert collector._running is False
    with pytest.raises(HyperliquidCollectorTerminatedError) as terminated:
        await consumer
    assert terminated.value.session_state is SessionState.STOPPED
    assert not any(
        "aclose" in getattr(item.get_coro(), "__qualname__", "")
        for item in asyncio.all_tasks()
        if not item.done()
    )


@pytest.mark.asyncio
async def test_sink_close_failures_never_mask_primary_protocol_failure() -> None:
    journal: list[str] = []
    raw_sink = ClosingRawSink("exception", journal=journal)
    outcome_sink = ClosingOutcomeSink("exception", journal=journal)
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed('{"channel":"unsupported"}')

    with pytest.raises(HyperliquidProtocolError, match="message was rejected") as captured:
        await task

    assert outcome_sink.close_count == raw_sink.close_count == 1
    assert journal == [
        "raw-accepted",
        "outcome-accepted",
        "outcome-close-started",
        "raw-close-started",
    ]
    assert collector.health.last_sink_failure_category is SinkFailureCategory.OUTCOME_CLOSE_FAILURE
    _assert_exported_exception_excludes(
        captured.value,
        "private-outcome-close-marker",
        "private-raw-close-marker",
    )


@pytest.mark.asyncio
async def test_close_failure_does_not_overwrite_primary_sink_failure_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_sink = FaultingRawSink("reject")

    async def fail_close() -> None:
        raw_sink.close_count += 1
        private_close_marker = "private-close-after-accept-failure"
        raise RuntimeError(private_close_marker)

    monkeypatch.setattr(raw_sink, "aclose", fail_close)
    outcome_sink = RecordingOutcomeSink()
    connection = ScriptedConnection()
    collector = HyperliquidTradesCollector(
        _config("BTC"),
        raw_record_sink=raw_sink,
        normalization_outcome_sink=outcome_sink,
        connection_factory=ScriptedConnectionFactory(connection),
        sleeper=ControlledSleeper(),
        jitter=lambda _bound: 0.0,
    )
    task = await _start(collector, connection, 1)
    connection.feed("Websocket connection established.")

    with pytest.raises(HyperliquidSinkBoundaryError) as captured:
        await task

    assert captured.value.category is SinkFailureCategory.RAW_EXPLICIT_REJECTION
    assert collector.health.last_sink_failure_category is SinkFailureCategory.RAW_EXPLICIT_REJECTION
    assert raw_sink.close_count == outcome_sink.close_count == 1
    _assert_exported_exception_excludes(captured.value, "private-close-after-accept-failure")


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
