"""Deterministic offline tests for bounded public Polymarket research capture."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import duckdb
import pytest

from hyperliquid_bot.parquet_research import (
    ParquetResearchWriter,
    ParquetRotation,
    create_research_catalog,
)
from hyperliquid_bot.polymarket_public_research import (
    MAX_CAPTURE_SECONDS,
    MAX_MARKET_BATCH_EVENTS,
    POLYMARKET_MARKET_WEBSOCKET_URL,
    PolymarketCaptureError,
    PolymarketDataIntegrityError,
    PolymarketMarketSpec,
    PolymarketOutcomeSpec,
    PolymarketPayloadTruncated,
    PolymarketPublicResearchCapture,
    PolymarketPublicResearchConfig,
    PolymarketSinkError,
    PolymarketTransportError,
    WebSocketConnection,
    _SessionState,
)
from hyperliquid_bot.raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
    RawResearchSink,
)

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "polymarket"
_CONDITION_ID = f"0x{1:064x}"
_YES_TOKEN = "1001"
_NO_TOKEN = "1002"


def _fixture_bytes(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


def _fixture_text(name: str) -> str:
    return _fixture_bytes(name).decode("utf-8")


def _fixture_object(name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(_fixture_bytes(name)))


def _event_array(*documents: object) -> str:
    return json.dumps(documents, separators=(",", ":"))


def _market(index: int = 1) -> PolymarketMarketSpec:
    return PolymarketMarketSpec(
        event_id=f"synthetic-event-{index}",
        market_id=f"synthetic-market-{index}",
        condition_id=f"0x{index:064x}",
        slug=f"synthetic-btc-research-{index}",
        question=f"Synthetic BTC research question {index}?",
        end_time="2027-01-01T00:00:00Z",
        active=True,
        closed=False,
        enable_order_book=True,
        accepting_orders=True,
        neg_risk=False,
        outcomes=(
            PolymarketOutcomeSpec("Synthetic Yes", str(index * 1000 + 1), "0.01", "5"),
            PolymarketOutcomeSpec("Synthetic No", str(index * 1000 + 2), "0.01", "5"),
        ),
    )


def _config(
    *,
    max_reconnects: int = 1,
    max_bytes: int = 8 * 1024 * 1024,
    reconnect_delay_seconds: float = 0,
    heartbeat_interval_seconds: float = 10,
    pong_timeout_seconds: float = 5,
) -> PolymarketPublicResearchConfig:
    return PolymarketPublicResearchConfig(
        markets=(_market(),),
        reconnect_delay_seconds=reconnect_delay_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        pong_timeout_seconds=pong_timeout_seconds,
        send_timeout_seconds=5,
        max_application_payload_bytes=max_bytes,
        max_reconnects=max_reconnects,
    )


class MemorySink:
    def __init__(self, *, fail_on_append: int | None = None) -> None:
        self.records: list[RawResearchRecord] = []
        self.append_attempts = 0
        self._fail_on_append = fail_on_append

    async def append(self, record: RawResearchRecord) -> None:
        self.append_attempts += 1
        if self.append_attempts == self._fail_on_append:
            raise RuntimeError("synthetic sink failure containing no market frame")
        self.records.append(record)

    async def aclose(self) -> None:
        return None


class Counter:
    def __init__(self, start: int) -> None:
        self.value = start

    def __call__(self) -> int:
        self.value += 1
        return self.value


class FakeConnection:
    def __init__(
        self,
        messages: Sequence[str | bytes | BaseException],
        *,
        on_last: Callable[[], None] | None = None,
        on_empty: Callable[[], None] | None = None,
        disconnect_when_empty: bool = False,
    ) -> None:
        self._messages = deque(messages)
        self._on_last = on_last
        self._on_empty = on_empty
        self._disconnect_when_empty = disconnect_when_empty
        self._never = asyncio.Event()
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        if not self._messages:
            if self._on_empty is not None:
                self._on_empty()
            if self._disconnect_when_empty:
                raise OSError("synthetic public transport disconnect")
            await self._never.wait()
            raise AssertionError("unreachable fake receive")
        value = self._messages.popleft()
        if isinstance(value, BaseException):
            raise value
        if not self._messages and self._on_last is not None:
            self._on_last()
        return value


class BusyHeartbeatConnection:
    """Keep producing valid market frames until a periodic PING is observed."""

    def __init__(self, stop: asyncio.Event) -> None:
        self._messages = deque(_success_messages())
        self._stop = stop
        self._ping_seen = False
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)
        if message == "PING":
            self._ping_seen = True

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        if self._messages:
            return self._messages.popleft()
        if self._ping_seen:
            self._stop.set()
            return "PONG"
        return _fixture_text("best_bid_ask.json")


@asynccontextmanager
async def _fake_context(connection: WebSocketConnection) -> AsyncIterator[WebSocketConnection]:
    yield connection


class ScriptedConnectionFactory:
    def __init__(self, connections: Sequence[WebSocketConnection]) -> None:
        self._connections = deque(connections)
        self.calls = 0

    def __call__(self) -> AbstractAsyncContextManager[WebSocketConnection]:
        self.calls += 1
        if not self._connections:
            raise AssertionError("collector requested an unexpected connection")
        return _fake_context(self._connections.popleft())


class SessionIds:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"synthetic-session-{self.count}"


def _local_payloads(
    records: Sequence[RawResearchRecord],
    channel: str | None = None,
) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(record.payload_bytes))
        for record in records
        if record.direction is MessageDirection.LOCAL
        and (channel is None or record.channel == channel)
    ]


def _success_messages() -> list[str]:
    return [
        _fixture_text("book_yes.json"),
        _fixture_text("book_no.json"),
        _fixture_text("book_yes_resnapshot.json"),
        _fixture_text("price_change.json"),
        _fixture_text("best_bid_ask.json"),
        _fixture_text("last_trade_price.json"),
        _fixture_text("tick_size_change.json"),
    ]


def _capture(
    sink: RawResearchSink,
    factory: ScriptedConnectionFactory,
    *,
    config: PolymarketPublicResearchConfig | None = None,
    session_ids: SessionIds | None = None,
) -> PolymarketPublicResearchCapture:
    return PolymarketPublicResearchCapture(
        sink,
        _config() if config is None else config,
        connection_factory=factory,
        utc_ns=Counter(1_000),
        monotonic_ns=Counter(10_000),
        session_id_factory=SessionIds() if session_ids is None else session_ids,
    )


async def _capture_success(sink: RawResearchSink) -> FakeConnection:
    stop = asyncio.Event()
    connection = FakeConnection(_success_messages(), on_last=stop.set)
    collector = _capture(sink, ScriptedConnectionFactory([connection]))
    await collector.capture_for(30, stop_event=stop)
    return connection


def _complete_session_state() -> _SessionState:
    state = _SessionState(_config())
    state, _, _ = state.stage_frame(
        (_fixture_object("book_yes_resnapshot.json"), _fixture_object("book_no.json")),
        1,
        is_batch=True,
        source_frame_channel="market_batch",
    )
    assert state.all_snapshots_received
    return state


def _book_state_signature(state: _SessionState) -> dict[str, tuple[object, ...]]:
    return {
        token_id: (
            dict(book.bids),
            dict(book.asks),
            book.tick_size,
            book.has_snapshot,
            book.snapshot_count,
        )
        for token_id, book in state.books.items()
    }


def test_config_bounds_and_market_identities_are_strict() -> None:
    market = _market()
    config = PolymarketPublicResearchConfig(markets=(market,))
    assert config.token_ids == tuple(outcome.token_id for outcome in market.outcomes)
    assert tuple((outcome.outcome, outcome.token_id) for outcome in market.outcomes) == (
        ("Synthetic Yes", _YES_TOKEN),
        ("Synthetic No", _NO_TOKEN),
    )

    with pytest.raises(ValueError, match="exactly one binary condition"):
        PolymarketPublicResearchConfig(markets=())
    with pytest.raises(ValueError, match="exactly one binary condition"):
        PolymarketPublicResearchConfig(markets=(market, _market(2)))
    with pytest.raises(ValueError, match="token IDs must be distinct"):
        replace(
            market,
            outcomes=(
                market.outcomes[0],
                replace(market.outcomes[1], token_id=market.outcomes[0].token_id),
            ),
        )
    with pytest.raises(ValueError, match="outcome labels must be distinct"):
        replace(
            market,
            outcomes=(
                market.outcomes[0],
                replace(market.outcomes[1], outcome="Synthetic Yes"),
            ),
        )

    for invalid in (-1.0, float("nan"), float("inf"), 31.0):
        with pytest.raises(ValueError, match="reconnect_delay_seconds"):
            PolymarketPublicResearchConfig(markets=(market,), reconnect_delay_seconds=invalid)
    for invalid in (0.0, -1.0, float("nan"), float("inf"), 11.0):
        with pytest.raises(ValueError, match="heartbeat_interval_seconds"):
            PolymarketPublicResearchConfig(markets=(market,), heartbeat_interval_seconds=invalid)
    for invalid in (0.0, -1.0, float("nan"), float("inf"), 31.0):
        with pytest.raises(ValueError, match="pong_timeout_seconds"):
            PolymarketPublicResearchConfig(markets=(market,), pong_timeout_seconds=invalid)
        with pytest.raises(ValueError, match="send_timeout_seconds"):
            PolymarketPublicResearchConfig(markets=(market,), send_timeout_seconds=invalid)
    with pytest.raises(ValueError, match="must not exceed heartbeat_interval_seconds"):
        PolymarketPublicResearchConfig(
            markets=(market,),
            heartbeat_interval_seconds=1,
            pong_timeout_seconds=2,
        )
    with pytest.raises(ValueError, match="payload"):
        PolymarketPublicResearchConfig(markets=(market,), max_application_payload_bytes=0)
    with pytest.raises(ValueError, match="zero or one"):
        PolymarketPublicResearchConfig(markets=(market,), max_reconnects=2)


@pytest.mark.parametrize("duration", [0, -1, 181, float("nan"), float("inf"), True])
@pytest.mark.asyncio
async def test_capture_duration_is_finite_and_hard_bounded(duration: object) -> None:
    collector = _capture(MemorySink(), ScriptedConnectionFactory([]))
    with pytest.raises(ValueError, match="at most 180"):
        await collector.capture_for(cast(float, duration))
    assert MAX_CAPTURE_SECONDS == 180


@pytest.mark.asyncio
async def test_hard_duration_cancels_a_hanging_connection_context() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class HangingContext(AbstractAsyncContextManager[WebSocketConnection]):
        async def __aenter__(self) -> WebSocketConnection:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("unreachable hanging context")

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: object,
        ) -> None:
            return None

    collector = PolymarketPublicResearchCapture(
        MemorySink(),
        _config(max_reconnects=0),
        connection_factory=HangingContext,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(PolymarketTransportError, match="hard duration"):
        async with asyncio.timeout(0.5):
            await collector.capture_for(0.05)
    assert entered.is_set()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_callback_entry_clocks_follow_receive_before_any_parsing() -> None:
    observations: list[str] = []

    class ObservedConnection:
        async def send(self, message: str | bytes) -> None:
            raise AssertionError("send is not used")

        async def recv(self) -> str | bytes:
            observations.append("recv_return")
            return _fixture_text("book_yes.json")

    def utc_ns() -> int:
        observations.append("utc_clock")
        return 101

    def monotonic_ns() -> int:
        observations.append("monotonic_clock")
        return 202

    collector = PolymarketPublicResearchCapture(
        MemorySink(),
        _config(max_reconnects=0),
        connection_factory=lambda: _fake_context(ObservedConnection()),
        utc_ns=utc_ns,
        monotonic_ns=monotonic_ns,
    )
    captured = await collector._receive_captured(ObservedConnection())
    assert observations == ["recv_return", "utc_clock", "monotonic_clock"]
    assert captured.received_utc_ns == 101
    assert captured.received_monotonic_ns == 202
    assert captured.payload_bytes == _fixture_bytes("book_yes.json")


@pytest.mark.asyncio
async def test_dual_token_subscription_requires_both_snapshots() -> None:
    stop = asyncio.Event()
    sink = MemorySink()
    connection = FakeConnection([_fixture_text("book_yes.json")], on_last=stop.set)
    collector = _capture(
        sink,
        ScriptedConnectionFactory([connection]),
        config=_config(max_reconnects=0),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="required evidence"):
        await collector.capture_for(30, stop_event=stop)

    subscription = json.loads(cast(str, connection.sent[0]))
    assert subscription["assets_ids"] == [_YES_TOKEN, _NO_TOKEN]
    metadata = _local_payloads(sink.records, "normalized_market_metadata")
    assert [(row["outcome"], row["asset_id"]) for row in metadata] == [
        ("Synthetic Yes", _YES_TOKEN),
        ("Synthetic No", _NO_TOKEN),
    ]
    assert not any(
        row.get("event") == "subscriptions_active"
        for row in _local_payloads(sink.records, "subscription")
    )
    normalized = _local_payloads(sink.records, "normalized_l2")
    assert [(row["source_frame_channel"], row["frame_wire_order"]) for row in normalized] == [
        ("book", 0)
    ]
    assert {row["event"] for row in _local_payloads(sink.records, "data_quality")} == {
        "snapshot_received",
        "coverage_incomplete",
    }


@pytest.mark.parametrize(
    "fixture_names",
    [
        ("book_yes.json", "book_no.json"),
        ("book_no.json", "book_yes.json"),
    ],
)
@pytest.mark.asyncio
async def test_initial_book_array_is_one_exact_raw_frame_in_wire_order(
    fixture_names: tuple[str, ...],
) -> None:
    documents = tuple(_fixture_object(name) for name in fixture_names)
    payload = _event_array(*documents)
    stop = asyncio.Event()
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory([FakeConnection([payload], on_last=stop.set)]),
        config=_config(max_reconnects=0),
    )
    await collector.capture_for(30, stop_event=stop)

    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    assert len(inbound) == 1
    assert inbound[0].channel == "market_batch"
    assert inbound[0].payload_bytes == payload.encode()
    assert inbound[0].payload_sha256 == hashlib.sha256(payload.encode()).hexdigest()
    normalized = _local_payloads(sink.records, "normalized_l2")
    assert [row["asset_id"] for row in normalized] == [
        cast(str, document["asset_id"]) for document in documents
    ]
    assert [row["frame_wire_order"] for row in normalized] == list(range(len(documents)))
    assert {row["source_frame_channel"] for row in normalized} == {"market_batch"}
    assert {row["raw_message_ordinal"] for row in normalized} == {inbound[0].message_ordinal}
    snapshot_markers = [
        row
        for row in _local_payloads(sink.records, "data_quality")
        if row.get("event") == "snapshot_received"
    ]
    assert [row["frame_wire_order"] for row in snapshot_markers] == list(range(len(documents)))
    assert any(
        row.get("event") == "subscriptions_active"
        for row in _local_payloads(sink.records, "subscription")
    )


@pytest.mark.parametrize(
    "fixture_names",
    [
        ("book_yes.json", "book_no.json"),
        ("book_no.json", "book_yes.json"),
    ],
)
@pytest.mark.asyncio
async def test_separate_snapshot_frames_complete_the_dual_token_gate_without_resubscribe(
    fixture_names: tuple[str, str],
) -> None:
    stop = asyncio.Event()
    sink = MemorySink()
    connection = FakeConnection(
        [_fixture_text(name) for name in fixture_names],
        on_last=stop.set,
    )
    factory = ScriptedConnectionFactory([connection])
    collector = _capture(sink, factory, config=_config(max_reconnects=0))

    await collector.capture_for(30, stop_event=stop)

    assert factory.calls == 1
    assert len(connection.sent) == 1
    assert json.loads(cast(str, connection.sent[0]))["assets_ids"] == [
        _YES_TOKEN,
        _NO_TOKEN,
    ]
    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    assert [record.channel for record in inbound] == ["book", "book"]
    assert [row["asset_id"] for row in _local_payloads(sink.records, "normalized_l2")] == [
        cast(str, _fixture_object(name)["asset_id"]) for name in fixture_names
    ]
    active = [
        row
        for row in _local_payloads(sink.records, "subscription")
        if row.get("event") == "subscriptions_active"
    ]
    assert len(active) == 1


@pytest.mark.asyncio
async def test_invalid_initial_arrays_fail_atomically_after_one_raw_write() -> None:
    wrong_market = _fixture_object("book_no.json")
    wrong_market["market"] = f"0x{2:064x}"
    unknown_asset = _fixture_object("book_no.json")
    unknown_asset["asset_id"] = "9999"
    invalid_last = _fixture_object("book_no.json")
    invalid_last["hash"] = ""
    cases: tuple[tuple[str, tuple[object, ...]], ...] = (
        ("empty", ()),
        ("non_object", (_fixture_object("book_yes.json"), "not-an-event-object")),
        (
            "duplicate_asset",
            (_fixture_object("book_yes.json"), _fixture_object("book_yes_resnapshot.json")),
        ),
        ("unknown_asset", (_fixture_object("book_yes.json"), unknown_asset)),
        ("wrong_market", (_fixture_object("book_yes.json"), wrong_market)),
        (
            "mixed_before_snapshot",
            (_fixture_object("book_yes.json"), _fixture_object("price_change.json")),
        ),
        (
            "initial_oversize",
            (
                _fixture_object("book_yes.json"),
                _fixture_object("book_no.json"),
                _fixture_object("book_yes_resnapshot.json"),
            ),
        ),
        ("invalid_last", (_fixture_object("book_yes.json"), invalid_last)),
    )

    for case_name, documents in cases:
        payload = _event_array(*documents)
        sink = MemorySink()
        collector = _capture(
            sink,
            ScriptedConnectionFactory([FakeConnection([payload])]),
            config=_config(max_reconnects=0),
        )
        with pytest.raises(PolymarketDataIntegrityError):
            await collector.capture_for(30)
        inbound = [
            record
            for record in sink.records
            if record.direction is MessageDirection.INBOUND
            and record.payload_bytes == payload.encode()
        ]
        assert len(inbound) == 1, case_name
        assert inbound[0].channel == "market_batch", case_name
        assert inbound[0].payload_encoding is PayloadEncoding.UTF8_JSON, case_name
        assert not _local_payloads(sink.records, "normalized_l2"), case_name
        assert len(_local_payloads(sink.records, "data_quality")) == 1, case_name


def test_invalid_late_batch_element_does_not_mutate_original_state() -> None:
    state = _SessionState(_config())
    state, _, _ = state.stage_frame(
        (_fixture_object("book_yes.json"), _fixture_object("book_no.json")),
        1,
        is_batch=True,
        source_frame_channel="market_batch",
    )
    before = {
        token_id: (dict(book.bids), dict(book.asks), book.tick_size, book.snapshot_count)
        for token_id, book in state.books.items()
    }
    invalid_bbo = _fixture_object("best_bid_ask.json")
    invalid_bbo["best_ask"] = "0.990000000000000001"
    invalid_bbo["spread"] = "0.490000000000000000"
    with pytest.raises(PolymarketDataIntegrityError, match="session-local L2"):
        state.stage_frame(
            (_fixture_object("book_yes_resnapshot.json"), invalid_bbo),
            2,
            is_batch=True,
            source_frame_channel="market_batch",
        )
    after = {
        token_id: (dict(book.bids), dict(book.asks), book.tick_size, book.snapshot_count)
        for token_id, book in state.books.items()
    }
    assert after == before
    assert state.books[_YES_TOKEN].bids[Decimal("0.490000000000000001")] == (
        "12.000000000000000001"
    )


@pytest.mark.parametrize(
    "referenced_tokens",
    [(_YES_TOKEN,), (_NO_TOKEN,), (_YES_TOKEN, _NO_TOKEN)],
)
def test_price_change_accepts_one_or_both_condition_tokens(
    referenced_tokens: tuple[str, ...],
) -> None:
    state = _complete_session_state()
    document = _fixture_object("price_change.json")
    changes = cast(list[dict[str, object]], document["price_changes"])
    document["price_changes"] = [
        change for change in changes if cast(str, change["asset_id"]) in referenced_tokens
    ]

    staged, normalized, _ = state.stage_frame(
        (document,),
        2,
        is_batch=False,
        source_frame_channel="price_change",
    )

    l2_rows = [row for row in normalized if row["event"] == "normalized_l2_frame"]
    assert [row["asset_id"] for row in l2_rows] == list(referenced_tokens)
    retained = [event for row in l2_rows for event in cast(list[dict[str, object]], row["events"])]
    retained.sort(key=lambda event: cast(int, event["wire_order"]))
    expected_count = sum(cast(str, change["asset_id"]) in referenced_tokens for change in changes)
    assert len(retained) == expected_count
    assert [event["wire_order"] for event in retained] == list(range(expected_count))
    assert staged is not state
    assert staged.all_snapshots_received


def test_dual_token_price_change_array_retains_raw_child_provenance() -> None:
    state = _complete_session_state()
    document = _fixture_object("price_change.json")

    _, normalized, event_types = state.stage_frame(
        (document,),
        77,
        is_batch=True,
        source_frame_channel="market_batch",
    )

    assert event_types == ("price_change",)
    l2_rows = [row for row in normalized if row["event"] == "normalized_l2_frame"]
    assert [row["asset_id"] for row in l2_rows] == [_YES_TOKEN, _NO_TOKEN]
    assert {row["raw_message_ordinal"] for row in l2_rows} == {77}
    assert {row["frame_wire_order"] for row in l2_rows} == {0}
    retained = [event for row in l2_rows for event in cast(list[dict[str, object]], row["events"])]
    retained.sort(key=lambda event: cast(int, event["wire_order"]))
    assert [event["wire_order"] for event in retained] == [0, 1, 2]
    assert [event["opaque_hash"] for event in retained] == [
        "synthetic-change-0001",
        "synthetic-change-0002",
        "synthetic-change-0003",
    ]


@pytest.mark.parametrize("invalid_identity", ["unknown_token", "wrong_condition"])
def test_price_change_identity_failure_is_atomic(invalid_identity: str) -> None:
    state = _complete_session_state()
    before = _book_state_signature(state)
    document = _fixture_object("price_change.json")
    if invalid_identity == "unknown_token":
        changes = cast(list[dict[str, object]], document["price_changes"])
        changes[-1]["asset_id"] = "9999"
    else:
        document["market"] = f"0x{2:064x}"

    with pytest.raises(PolymarketDataIntegrityError):
        state.stage_frame(
            (document,),
            2,
            is_batch=False,
            source_frame_channel="price_change",
        )

    assert _book_state_signature(state) == before


def test_price_change_requires_snapshot_for_every_referenced_token() -> None:
    state = _SessionState(_config())
    state, _, _ = state.stage_frame(
        (_fixture_object("book_yes.json"),),
        1,
        is_batch=False,
        source_frame_channel="book",
    )
    document = _fixture_object("price_change.json")
    changes = cast(list[dict[str, object]], document["price_changes"])
    document["price_changes"] = [change for change in changes if change["asset_id"] == _NO_TOKEN]

    with pytest.raises(PolymarketDataIntegrityError, match="before its token snapshot"):
        state.stage_frame(
            (document,),
            2,
            is_batch=False,
            source_frame_channel="price_change",
        )

    assert state.books[_YES_TOKEN].has_snapshot is True
    assert state.books[_NO_TOKEN].has_snapshot is False


@pytest.mark.parametrize("missing_fields", [False, True])
def test_price_change_nullable_fields_and_empty_bbo_are_normalized_without_inference(
    missing_fields: bool,
) -> None:
    state = _complete_session_state()
    document = _fixture_object("price_change.json")
    changes = cast(list[dict[str, object]], document["price_changes"])
    document["price_changes"] = [changes[0]]
    change = cast(dict[str, object], cast(list[object], document["price_changes"])[0])
    change["best_bid"] = ""
    change["best_ask"] = ""
    if missing_fields:
        document.pop("timestamp")
        change.pop("hash")
    else:
        document["timestamp"] = None
        change["hash"] = None

    _, normalized, _ = state.stage_frame(
        (document,),
        2,
        is_batch=False,
        source_frame_channel="price_change",
    )

    assert len(normalized) == 1
    row = normalized[0]
    assert row["event"] == "normalized_l2_frame"
    assert row["timestamp_ms"] is None
    events = cast(list[dict[str, object]], row["events"])
    assert events[0]["opaque_hash"] is None
    assert not any(item["event"] == "normalized_bbo" for item in normalized)


@pytest.mark.asyncio
async def test_supported_post_snapshot_batch_is_atomic_and_in_wire_order() -> None:
    initial_payload = _event_array(
        _fixture_object("book_yes.json"), _fixture_object("book_no.json")
    )
    update_payload = _event_array(
        _fixture_object("book_yes_resnapshot.json"),
        _fixture_object("price_change.json"),
        _fixture_object("last_trade_price.json"),
    )
    stop = asyncio.Event()
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory(
            [FakeConnection([initial_payload, update_payload], on_last=stop.set)]
        ),
        config=_config(max_reconnects=0),
    )
    await collector.capture_for(30, stop_event=stop)

    update_raw = next(
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND
        and record.payload_bytes == update_payload.encode()
    )
    derived = [
        payload
        for payload in _local_payloads(sink.records)
        if payload.get("raw_message_ordinal") == update_raw.message_ordinal
        and str(payload.get("event", "")).startswith("normalized_")
    ]
    assert derived
    assert [cast(int, payload["frame_wire_order"]) for payload in derived] == sorted(
        cast(int, payload["frame_wire_order"]) for payload in derived
    )
    assert {payload["source_frame_channel"] for payload in derived} == {"market_batch"}
    assert {payload["frame_wire_order"] for payload in derived} == {0, 1, 2}
    assert any(payload["event"] == "normalized_last_trade_price" for payload in derived)

    unknown = {
        "event_type": "trade",
        "market": _CONDITION_ID,
        "asset_id": _YES_TOKEN,
        "price": "0.5",
        "size": "1",
        "timestamp": "1788200000600",
    }
    invalid_payload = _event_array(
        _fixture_object("book_yes_resnapshot.json"),
        _fixture_object("price_change.json"),
        unknown,
    )
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory([FakeConnection([initial_payload, invalid_payload])]),
        config=_config(max_reconnects=0),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="not requested or supported"):
        await collector.capture_for(30)
    invalid_raw = next(
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND
        and record.payload_bytes == invalid_payload.encode()
    )
    assert not any(
        payload.get("raw_message_ordinal") == invalid_raw.message_ordinal
        for payload in _local_payloads(sink.records)
        if str(payload.get("event", "")).startswith("normalized_")
    )


@pytest.mark.asyncio
async def test_post_snapshot_event_array_has_an_absolute_bound() -> None:
    initial_payload = _event_array(_fixture_object("book_yes.json"))
    oversized_payload = _event_array(
        *[_fixture_object("last_trade_price.json") for _ in range(MAX_MARKET_BATCH_EVENTS + 1)]
    )
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory([FakeConnection([initial_payload, oversized_payload])]),
        config=_config(max_reconnects=0),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="absolute bound"):
        await collector.capture_for(30)
    oversized_raw = [
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND
        and record.payload_bytes == oversized_payload.encode()
    ]
    assert len(oversized_raw) == 1
    assert oversized_raw[0].channel == "market_batch"
    assert not _local_payloads(sink.records, "normalized_last_trade_price")
    assert MAX_MARKET_BATCH_EVENTS == 64


@pytest.mark.asyncio
async def test_public_subscription_exact_bytes_and_all_normalized_shapes() -> None:
    sink = MemorySink()
    connection = await _capture_success(sink)

    assert POLYMARKET_MARKET_WEBSOCKET_URL == (
        "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    )
    assert len(connection.sent) == 1
    outbound_text = cast(str, connection.sent[0])
    assert json.loads(outbound_text) == {
        "assets_ids": [_YES_TOKEN, _NO_TOKEN],
        "custom_feature_enabled": False,
        "initial_dump": True,
        "level": 2,
        "type": "market",
    }
    lowered = outbound_text.lower()
    for forbidden in ("auth", "key", "secret", "passphrase", "wallet", "order", "trade"):
        assert forbidden not in lowered
    subscription_record = next(
        record
        for record in sink.records
        if record.direction is MessageDirection.OUTBOUND and record.channel == "subscription"
    )
    assert subscription_record.payload_encoding is PayloadEncoding.UTF8_JSON

    exact = _fixture_bytes("book_yes.json")
    source = next(
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND and record.payload_bytes == exact
    )
    assert source.frame_type is FrameType.TEXT
    assert source.payload_sha256 == hashlib.sha256(exact).hexdigest()
    assert [record.message_ordinal for record in sink.records] == list(
        range(1, len(sink.records) + 1)
    )

    l2 = _local_payloads(sink.records, "normalized_l2")
    snapshots = [row for row in l2 if row["source_channel"] == "book"]
    assert [row["message_type"] for row in snapshots] == [
        "snapshot",
        "snapshot",
        "resnapshot",
    ]
    first_events = cast(list[dict[str, object]], snapshots[0]["events"])
    assert first_events[0]["price"] == "0.490000000000000001"
    assert first_events[0]["size"] == "12.000000000000000001"
    assert snapshots[0]["sequence_available"] is False
    assert snapshots[0]["checksum_available"] is False

    updates = [row for row in l2 if row["source_channel"] == "price_change"]
    update_events = [
        event for row in updates for event in cast(list[dict[str, object]], row["events"])
    ]
    update_events.sort(key=lambda event: cast(int, event["wire_order"]))
    assert [event["wire_order"] for event in update_events] == [0, 1, 2]
    assert [event["action"] for event in update_events] == ["upsert", "delete", "delete"]
    assert update_events[0]["size"] == "13.500000000000000001"
    assert update_events[1]["size"] == "0"

    bbo = _local_payloads(sink.records, "normalized_bbo")
    assert {row["source_channel"] for row in bbo} == {"price_change", "best_bid_ask"}
    assert any(row["spread"] == "0.040000000000000000" for row in bbo)
    last_trade = _local_payloads(sink.records, "normalized_last_trade_price")
    assert last_trade == [
        {
            "asset_id": _YES_TOKEN,
            "complete_trade_tape": False,
            "event": "normalized_last_trade_price",
            "fee_rate_bps": "0",
            "frame_wire_order": 0,
            "market": _CONDITION_ID,
            "price": "0.510000000000000001",
            "raw_message_ordinal": last_trade[0]["raw_message_ordinal"],
            "side": "BUY",
            "size": "3.250000000000000001",
            "source_channel": "last_trade_price",
            "source_frame_channel": "last_trade_price",
            "timestamp_ms": "1788200000400",
            "transaction_hash": "synthetic-transaction-0001",
        }
    ]
    metadata = _local_payloads(sink.records, "normalized_market_metadata")
    assert [row["record_type"] for row in metadata] == [
        "configured_market",
        "configured_market",
        "tick_size_change",
    ]
    assert metadata[-1]["new_tick_size"] == "0.001"

    markers = _local_payloads(sink.records)
    assert any(marker.get("event") == "subscriptions_active" for marker in markers)
    assert any(
        marker.get("event") == "session_stopped"
        and marker.get("reason") == "external_stop_requested"
        for marker in markers
    )
    assert not any(marker.get("event") == "coverage_incomplete" for marker in markers)


@pytest.mark.asyncio
async def test_heartbeat_is_periodic_during_busy_market_traffic_and_times_out_without_pong() -> (
    None
):
    stop = asyncio.Event()
    busy = BusyHeartbeatConnection(stop)
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory([busy]),
        config=_config(heartbeat_interval_seconds=0.005, pong_timeout_seconds=0.005),
    )
    await collector.capture_for(1, stop_event=stop)
    assert "PING" in busy.sent
    ping = next(record for record in sink.records if record.payload_bytes == b"PING")
    pong = next(record for record in sink.records if record.payload_bytes == b"PONG")
    assert ping.direction is MessageDirection.OUTBOUND
    assert pong.direction is MessageDirection.INBOUND
    assert ping.payload_encoding is PayloadEncoding.UTF8
    assert pong.payload_encoding is PayloadEncoding.UTF8

    sink = MemorySink()
    no_pong = FakeConnection(_success_messages())
    factory = ScriptedConnectionFactory([no_pong])
    collector = _capture(
        sink,
        factory,
        config=_config(
            max_reconnects=0,
            heartbeat_interval_seconds=0.005,
            pong_timeout_seconds=0.005,
        ),
    )
    with pytest.raises(PolymarketTransportError, match="reconnect bound"):
        await collector.capture_for(1)
    assert "PING" in no_pong.sent
    assert any(
        row.get("event") == "heartbeat_timeout"
        for row in _local_payloads(sink.records, "data_quality")
    )


@pytest.mark.asyncio
async def test_exact_raw_sha_and_all_four_views_survive_atomic_zstd_parquet(
    tmp_path: Path,
) -> None:
    writer = ParquetResearchWriter(
        tmp_path / "parts",
        rotation=ParquetRotation(
            max_records=1_000,
            max_payload_bytes=16 * 1024 * 1024,
            max_interval_seconds=3_600,
        ),
    )
    await _capture_success(writer)
    forged_session = "synthetic-forged-source-link-session"
    await writer.append(
        RawResearchRecord(
            schema_version=RAW_RESEARCH_SCHEMA_VERSION,
            venue="polymarket",
            product="BTC-CRYPTO-RESEARCH",
            channel="book",
            session_id=forged_session,
            message_ordinal=900,
            received_utc_ns=900,
            received_monotonic_ns=900,
            direction=MessageDirection.INBOUND,
            frame_type=FrameType.TEXT,
            payload_encoding=PayloadEncoding.UTF8_JSON,
            payload_bytes=b"{}",
        )
    )
    forged_payloads = (
        (
            "normalized_market_metadata",
            {
                "event": "normalized_market_metadata",
                "record_type": "tick_size_change",
                "source_channel": "tick_size_change",
                "raw_message_ordinal": 999_999,
            },
        ),
        (
            "normalized_l2",
            {
                "event": "normalized_l2_frame",
                "source_channel": "book",
                "raw_message_ordinal": 900,
                "events": [],
            },
        ),
        (
            "normalized_bbo",
            {
                "event": "normalized_bbo",
                "source_channel": "book",
                "raw_message_ordinal": 900,
            },
        ),
        (
            "normalized_last_trade_price",
            {
                "event": "normalized_last_trade_price",
                "source_channel": "last_trade_price",
                "raw_message_ordinal": 900,
            },
        ),
    )
    for offset, (channel, payload) in enumerate(forged_payloads, start=901):
        await writer.append(
            RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue="polymarket",
                product="BTC-CRYPTO-RESEARCH",
                channel=channel,
                session_id=forged_session,
                message_ordinal=offset,
                received_utc_ns=offset,
                received_monotonic_ns=offset,
                direction=MessageDirection.LOCAL,
                frame_type=FrameType.MARKER,
                payload_encoding=PayloadEncoding.UTF8_JSON,
                payload_bytes=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            )
        )
    await writer.aclose()
    assert len(writer.parquet_files) == 1
    assert writer.orphan_partial_files == ()

    fixture = _fixture_bytes("price_change.json")
    connection = duckdb.connect(":memory:")
    try:
        raw_rows = connection.execute(
            "SELECT payload_bytes, payload_sha256 FROM read_parquet(?) "
            "WHERE venue = 'polymarket' AND direction = 'inbound'",
            [str(writer.parquet_files[0])],
        ).fetchall()
    finally:
        connection.close()
    assert [
        (bytes(payload), digest) for payload, digest in raw_rows if bytes(payload) == fixture
    ] == [(fixture, hashlib.sha256(fixture).hexdigest())]

    database_path = tmp_path / "research.duckdb"
    create_research_catalog(writer.output_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        metadata = connection.execute(
            "SELECT record_type, source_channel, asset_id, tick_size, new_tick_size, "
            "payload_sha256 FROM polymarket_crypto_market_metadata "
            "ORDER BY normalization_message_ordinal"
        ).fetchall()
        outcome_mapping = connection.execute(
            "SELECT outcome, asset_id FROM polymarket_crypto_market_metadata "
            "WHERE record_type = 'configured_market' ORDER BY outcome_index"
        ).fetchall()
        updates = connection.execute(
            "SELECT wire_order, asset_id, action, price, size, payload_sha256 "
            "FROM polymarket_crypto_l2_events WHERE source_channel = 'price_change' "
            "ORDER BY wire_order"
        ).fetchall()
        bbo = connection.execute(
            "SELECT source_channel, asset_id, best_bid, best_ask, spread, payload_sha256 "
            "FROM polymarket_crypto_bbo ORDER BY normalization_message_ordinal"
        ).fetchall()
        last_trade = connection.execute(
            "SELECT asset_id, price, size, fee_rate_bps, side, complete_trade_tape, "
            "payload_sha256 FROM polymarket_crypto_last_trade_prices"
        ).fetchall()
        view_counts = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM polymarket_crypto_market_metadata), "
            "(SELECT count(*) FROM polymarket_crypto_l2_events), "
            "(SELECT count(*) FROM polymarket_crypto_bbo), "
            "(SELECT count(*) FROM polymarket_crypto_last_trade_prices)"
        ).fetchone()
        empty_event_rows = connection.execute(
            "SELECT count(*) FROM polymarket_crypto_l2_events "
            "WHERE normalization_message_ordinal = 902"
        ).fetchone()
    finally:
        connection.close()

    assert [(row[0], row[1], row[2]) for row in metadata] == [
        ("configured_market", "local_config", _YES_TOKEN),
        ("configured_market", "local_config", _NO_TOKEN),
        ("tick_size_change", "tick_size_change", _YES_TOKEN),
    ]
    assert outcome_mapping == [
        ("Synthetic Yes", _YES_TOKEN),
        ("Synthetic No", _NO_TOKEN),
    ]
    assert metadata[0][3] == "0.01" and metadata[0][5] is None
    assert metadata[-1][4] == "0.001" and len(metadata[-1][5]) == 64
    assert [row[:5] for row in updates] == [
        (0, _YES_TOKEN, "upsert", "0.500000000000000001", "13.500000000000000001"),
        (1, _NO_TOKEN, "delete", "0.480000000000000001", "0"),
        (2, _YES_TOKEN, "delete", "0.530000000000000001", "0"),
    ]
    assert all(len(row[5]) == 64 for row in updates)
    assert {row[0] for row in bbo} == {"price_change", "best_bid_ask"}
    assert all(len(row[5]) == 64 for row in bbo)
    assert last_trade[0][:6] == (
        _YES_TOKEN,
        "0.510000000000000001",
        "3.250000000000000001",
        "0",
        "BUY",
        False,
    )
    assert len(last_trade[0][6]) == 64
    assert view_counts == (3, 11, 3, 1)
    assert empty_event_rows == (0,)


@pytest.mark.asyncio
async def test_batch_rows_link_to_one_exact_raw_frame_in_all_four_views(tmp_path: Path) -> None:
    initial_payload = _event_array(
        _fixture_object("book_yes.json"), _fixture_object("book_no.json")
    )
    update_payload = _event_array(
        _fixture_object("book_yes_resnapshot.json"),
        _fixture_object("price_change.json"),
        _fixture_object("last_trade_price.json"),
        _fixture_object("tick_size_change.json"),
    )
    stop = asyncio.Event()
    writer = ParquetResearchWriter(
        tmp_path / "parts",
        rotation=ParquetRotation(
            max_records=1_000,
            max_payload_bytes=16 * 1024 * 1024,
            max_interval_seconds=3_600,
        ),
    )
    collector = _capture(
        writer,
        ScriptedConnectionFactory(
            [FakeConnection([initial_payload, update_payload], on_last=stop.set)]
        ),
        config=_config(max_reconnects=0),
    )
    await collector.capture_for(30, stop_event=stop)
    await writer.aclose()
    assert len(writer.parquet_files) == 1
    assert writer.orphan_partial_files == ()

    initial_hash = hashlib.sha256(initial_payload.encode()).hexdigest()
    update_hash = hashlib.sha256(update_payload.encode()).hexdigest()
    connection = duckdb.connect(":memory:")
    try:
        raw = connection.execute(
            "SELECT payload_bytes, payload_sha256, channel FROM read_parquet(?) "
            "WHERE venue = 'polymarket' AND direction = 'inbound' "
            "ORDER BY message_ordinal",
            [str(writer.parquet_files[0])],
        ).fetchall()
    finally:
        connection.close()
    assert [(bytes(row[0]), row[1], row[2]) for row in raw] == [
        (initial_payload.encode(), initial_hash, "market_batch"),
        (update_payload.encode(), update_hash, "market_batch"),
    ]

    database_path = tmp_path / "research.duckdb"
    create_research_catalog(writer.output_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        l2 = connection.execute(
            "SELECT DISTINCT source_channel, source_frame_channel, frame_wire_order, "
            "payload_sha256 FROM polymarket_crypto_l2_events ORDER BY ALL"
        ).fetchall()
        bbo = connection.execute(
            "SELECT DISTINCT source_frame_channel, frame_wire_order, payload_sha256 "
            "FROM polymarket_crypto_bbo"
        ).fetchall()
        trade = connection.execute(
            "SELECT source_frame_channel, frame_wire_order, payload_sha256 "
            "FROM polymarket_crypto_last_trade_prices"
        ).fetchall()
        tick = connection.execute(
            "SELECT source_frame_channel, frame_wire_order, payload_sha256 "
            "FROM polymarket_crypto_market_metadata WHERE record_type = 'tick_size_change'"
        ).fetchall()
    finally:
        connection.close()

    assert ("book", "market_batch", 0, initial_hash) in l2
    assert ("book", "market_batch", 1, initial_hash) in l2
    assert ("book", "market_batch", 0, update_hash) in l2
    assert ("price_change", "market_batch", 1, update_hash) in l2
    assert bbo == [("market_batch", 1, update_hash)]
    assert trade == [("market_batch", 2, update_hash)]
    assert tick == [("market_batch", 3, update_hash)]


@pytest.mark.asyncio
async def test_both_outcome_tokens_require_a_fresh_snapshot_but_delta_is_optional() -> None:
    stop = asyncio.Event()
    complete_sink = MemorySink()
    complete = _capture(
        complete_sink,
        ScriptedConnectionFactory(
            [
                FakeConnection(
                    [_fixture_text("book_yes.json"), _fixture_text("book_no.json")],
                    on_last=stop.set,
                )
            ]
        ),
        config=_config(max_reconnects=0),
    )
    await complete.capture_for(30, stop_event=stop)
    assert {row["event"] for row in _local_payloads(complete_sink.records, "data_quality")} == {
        "snapshot_received"
    }

    stop = asyncio.Event()
    incomplete_sink = MemorySink()
    incomplete = _capture(
        incomplete_sink,
        ScriptedConnectionFactory(
            [FakeConnection([_fixture_text("book_yes.json")], on_last=stop.set)]
        ),
        config=_config(max_reconnects=0),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="required evidence"):
        await incomplete.capture_for(30, stop_event=stop)
    coverage = [
        row
        for row in _local_payloads(incomplete_sink.records, "data_quality")
        if row.get("event") == "coverage_incomplete"
    ]
    assert len(coverage) == 1
    assert coverage[0]["missing_snapshot_count"] == 1
    assert "post_snapshot_price_change" not in coverage[0]


@pytest.mark.parametrize(
    ("payload", "expected_encoding"),
    [
        ('{"event_type":"book","event_type":"book"}', PayloadEncoding.UTF8),
        ('{"event_type":"book","value":NaN}', PayloadEncoding.UTF8),
        ('{"event_type":"book","value":Infinity}', PayloadEncoding.UTF8),
        ("[]", PayloadEncoding.UTF8_JSON),
        (
            _fixture_text("book_yes.json").replace(
                '"0.490000000000000001"', "0.490000000000000001", 1
            ),
            PayloadEncoding.UTF8_JSON,
        ),
    ],
)
@pytest.mark.asyncio
async def test_strict_json_and_quoted_decimal_contract_fail_closed(
    payload: str,
    expected_encoding: PayloadEncoding,
) -> None:
    sink = MemorySink()
    factory = ScriptedConnectionFactory([FakeConnection([payload])])
    collector = _capture(sink, factory, config=_config(max_reconnects=0))
    with pytest.raises(PolymarketDataIntegrityError):
        await collector.capture_for(30)
    assert factory.calls == 1
    raw = next(
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND
        and record.payload_bytes == payload.encode("utf-8")
    )
    assert raw.payload_encoding is expected_encoding
    assert any(
        row.get("event") == "schema_error" for row in _local_payloads(sink.records, "data_quality")
    )


@pytest.mark.asyncio
async def test_numeric_price_identity_prevents_duplicate_or_stale_levels() -> None:
    duplicate = _fixture_object("book_yes.json")
    duplicate_bids = cast(list[dict[str, object]], duplicate["bids"])
    duplicate_bids.append(
        {
            "price": "0.4900000000000000010",
            "size": "1.000000000000000001",
        }
    )
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory([FakeConnection([json.dumps(duplicate, separators=(",", ":"))])]),
        config=_config(max_reconnects=0),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="duplicate price level"):
        await collector.capture_for(30)

    snapshot = _fixture_object("book_yes_resnapshot.json")
    snapshot_asks = cast(list[dict[str, object]], snapshot["asks"])
    snapshot_asks[0]["price"] = "0.5300000000000000010"
    changes = _fixture_object("price_change.json")
    changes["price_changes"] = [
        change
        for change in cast(list[dict[str, object]], changes["price_changes"])
        if change["asset_id"] == _YES_TOKEN
    ]
    stop = asyncio.Event()
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory(
            [
                FakeConnection(
                    [
                        json.dumps(snapshot, separators=(",", ":")),
                        _fixture_text("book_no.json"),
                        json.dumps(changes, separators=(",", ":")),
                        _fixture_text("best_bid_ask.json"),
                    ],
                    on_last=stop.set,
                )
            ]
        ),
        config=_config(max_reconnects=0),
    )
    await collector.capture_for(30, stop_event=stop)
    update_events = [
        event
        for row in _local_payloads(sink.records, "normalized_l2")
        if row["source_channel"] == "price_change"
        for event in cast(list[dict[str, object]], row["events"])
    ]
    assert any(event["action"] == "delete" for event in update_events)


@pytest.mark.asyncio
async def test_source_bbo_must_match_the_session_local_book() -> None:
    wrong_bbo = _fixture_object("best_bid_ask.json")
    wrong_bbo["best_ask"] = "0.990000000000000001"
    wrong_bbo["spread"] = "0.490000000000000000"
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory(
            [
                FakeConnection(
                    [
                        _fixture_text("book_yes.json"),
                        json.dumps(wrong_bbo, separators=(",", ":")),
                    ]
                )
            ]
        ),
        config=_config(max_reconnects=0),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="session-local L2"):
        await collector.capture_for(30)
    assert any(
        row.get("event") == "book_state_mismatch"
        for row in _local_payloads(sink.records, "data_quality")
    )


@pytest.mark.asyncio
async def test_wrong_identity_delta_before_snapshot_and_trade_event_fail_closed() -> None:
    wrong_market = _fixture_object("book_yes.json")
    wrong_market["market"] = f"0x{2:064x}"
    wrong_token = _fixture_object("book_yes.json")
    wrong_token["asset_id"] = "9999"
    trade_event = json.dumps(
        {
            "event_type": "trade",
            "market": _CONDITION_ID,
            "asset_id": _YES_TOKEN,
            "price": "0.5",
            "size": "1",
            "timestamp": "1788200000600",
        },
        separators=(",", ":"),
    )
    cases = (
        json.dumps(wrong_market, separators=(",", ":")),
        json.dumps(wrong_token, separators=(",", ":")),
        _fixture_text("price_change.json"),
        trade_event,
    )
    for payload in cases:
        sink = MemorySink()
        collector = _capture(
            sink,
            ScriptedConnectionFactory([FakeConnection([payload])]),
            config=_config(max_reconnects=0),
        )
        with pytest.raises(PolymarketDataIntegrityError):
            await collector.capture_for(30)
        assert not _local_payloads(sink.records, "normalized_last_trade_price")


@pytest.mark.asyncio
async def test_tick_state_mismatch_and_selected_market_resolution_fail_closed() -> None:
    tick = _fixture_object("tick_size_change.json")
    tick["new_tick_size"] = "0.0001"
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory(
            [
                FakeConnection(
                    [
                        _fixture_text("book_yes.json"),
                        _fixture_text("book_no.json"),
                        _fixture_text("tick_size_change.json"),
                        json.dumps(tick, separators=(",", ":")),
                    ]
                )
            ]
        ),
        config=_config(max_reconnects=0),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="tick-size"):
        await collector.capture_for(30)
    assert any(
        row.get("event") == "tick_size_mismatch"
        for row in _local_payloads(sink.records, "data_quality")
    )

    resolved = json.dumps(
        {
            "event_type": "market_resolved",
            "id": "synthetic-market-1",
            "market": _CONDITION_ID,
            "assets_ids": [_YES_TOKEN, _NO_TOKEN],
            "winning_asset_id": _YES_TOKEN,
            "winning_outcome": "Synthetic Yes",
            "timestamp": "1788200000700",
        },
        separators=(",", ":"),
    )
    sink = MemorySink()
    collector = _capture(
        sink,
        ScriptedConnectionFactory([FakeConnection([resolved])]),
        config=_config(max_reconnects=0),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="resolved during capture"):
        await collector.capture_for(30)
    raw = next(record for record in sink.records if record.payload_bytes == resolved.encode())
    assert raw.payload_encoding is PayloadEncoding.UTF8_JSON
    assert any(
        row.get("event") == "market_closed" for row in _local_payloads(sink.records, "data_quality")
    )


@pytest.mark.asyncio
async def test_binary_is_exact_then_rejected_and_oversize_or_truncation_never_retries() -> None:
    fixture = _fixture_bytes("book_yes.json")
    sink = MemorySink()
    factory = ScriptedConnectionFactory([FakeConnection([fixture])])
    collector = _capture(sink, factory, config=_config(max_reconnects=1))
    with pytest.raises(PolymarketDataIntegrityError, match="text frame"):
        await collector.capture_for(30)
    binary = [
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND and record.payload_bytes == fixture
    ]
    assert len(binary) == 1
    assert binary[0].frame_type is FrameType.BINARY
    assert binary[0].payload_sha256 == hashlib.sha256(fixture).hexdigest()
    assert factory.calls == 1

    sink = MemorySink()
    factory = ScriptedConnectionFactory([FakeConnection([fixture.decode("utf-8")])])
    collector = _capture(
        sink,
        factory,
        config=_config(max_reconnects=1, max_bytes=len(fixture) - 1),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="exceeded"):
        await collector.capture_for(30)
    assert any(record.payload_bytes == fixture for record in sink.records)
    assert factory.calls == 1

    sink = MemorySink()
    factory = ScriptedConnectionFactory(
        [FakeConnection([PolymarketPayloadTruncated("synthetic truncation")])]
    )
    collector = _capture(sink, factory, config=_config(max_reconnects=1))
    with pytest.raises(PolymarketDataIntegrityError, match="truncated"):
        await collector.capture_for(30)
    assert factory.calls == 1
    assert any(
        row.get("event") == "truncation_error"
        for row in _local_payloads(sink.records, "data_quality")
    )


@pytest.mark.asyncio
async def test_deep_json_recursion_is_exact_then_fails_as_schema_without_retry() -> None:
    payload = "[" * 10_000 + "0" + "]" * 10_000
    payload_bytes = payload.encode()
    sink = MemorySink()
    factory = ScriptedConnectionFactory([FakeConnection([payload])])
    collector = _capture(sink, factory, config=_config(max_reconnects=1))

    with pytest.raises(PolymarketDataIntegrityError, match="strict JSON"):
        await collector.capture_for(30)

    inbound = [
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND and record.payload_bytes == payload_bytes
    ]
    assert len(inbound) == 1
    assert inbound[0].channel == "unknown"
    assert inbound[0].payload_sha256 == hashlib.sha256(payload_bytes).hexdigest()
    quality = _local_payloads(sink.records, "data_quality")
    assert [row["event"] for row in quality] == ["schema_error"]
    assert not _local_payloads(sink.records, "normalized_l2")
    assert not _local_payloads(sink.records, "normalized_bbo")
    assert not _local_payloads(sink.records, "normalized_last_trade_price")
    assert not _local_payloads(sink.records, "normalized_tick_size")
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_sink_failure_is_sticky_and_never_reconnects() -> None:
    sink = MemorySink(fail_on_append=7)
    factory = ScriptedConnectionFactory([FakeConnection(_success_messages())])
    collector = _capture(sink, factory, config=_config(max_reconnects=1))
    with pytest.raises(PolymarketSinkError, match="raw sink failed"):
        await collector.capture_for(30)
    assert factory.calls == 1
    attempts = sink.append_attempts
    with pytest.raises(PolymarketSinkError, match="terminal"):
        await collector._append_marker("synthetic-session", "session", "after_failure")
    assert sink.append_attempts == attempts


@pytest.mark.asyncio
async def test_batch_writer_failure_is_sticky_without_success_markers_or_reconnect() -> None:
    payload = _event_array(_fixture_object("book_yes.json"), _fixture_object("book_no.json"))
    sink = MemorySink(fail_on_append=9)
    factory = ScriptedConnectionFactory([FakeConnection([payload])])
    collector = _capture(sink, factory, config=_config(max_reconnects=1))

    with pytest.raises(PolymarketSinkError, match="raw sink failed"):
        await collector.capture_for(30)

    assert factory.calls == 1
    normalized = _local_payloads(sink.records, "normalized_l2")
    assert len(normalized) == 1
    assert normalized[0]["frame_wire_order"] == 0
    markers = _local_payloads(sink.records)
    assert not any(row.get("event") == "subscriptions_active" for row in markers)
    assert not any(row.get("event") == "snapshot_received" for row in markers)
    attempts = sink.append_attempts
    with pytest.raises(PolymarketSinkError, match="terminal"):
        await collector._append_marker("synthetic-session", "session", "after_failure")
    assert sink.append_attempts == attempts


@pytest.mark.asyncio
async def test_reconnect_uses_fresh_session_state_and_records_gap() -> None:
    stop = asyncio.Event()
    first = FakeConnection(
        [_fixture_text("book_yes.json")],
        disconnect_when_empty=True,
    )
    second = FakeConnection(_success_messages(), on_last=stop.set)
    sink = MemorySink()
    factory = ScriptedConnectionFactory([first, second])
    collector = _capture(
        sink,
        factory,
        config=_config(max_reconnects=1),
        session_ids=SessionIds(),
    )
    await collector.capture_for(30, stop_event=stop)
    assert factory.calls == 2
    assert {record.session_id for record in sink.records} == {
        "synthetic-session-1",
        "synthetic-session-2",
    }
    markers = _local_payloads(sink.records)
    assert any(row.get("event") == "gap" for row in markers)
    assert any(row.get("event") == "reconnected" for row in markers)

    first = FakeConnection(
        [_fixture_text("book_yes.json"), _fixture_text("book_no.json")],
        disconnect_when_empty=True,
    )
    second = FakeConnection([_fixture_text("price_change.json")])
    sink = MemorySink()
    factory = ScriptedConnectionFactory([first, second])
    collector = _capture(
        sink,
        factory,
        config=_config(max_reconnects=1),
        session_ids=SessionIds(),
    )
    with pytest.raises(PolymarketDataIntegrityError, match="before its token snapshot"):
        await collector.capture_for(30)
    assert factory.calls == 2
    assert any(row.get("event") == "gap" for row in _local_payloads(sink.records))


@pytest.mark.asyncio
async def test_preset_stop_and_stop_during_reconnect_cannot_report_success() -> None:
    preset = asyncio.Event()
    preset.set()
    factory = ScriptedConnectionFactory([])
    collector = _capture(MemorySink(), factory)
    with pytest.raises(PolymarketDataIntegrityError, match="stopped before a session"):
        await collector.capture_for(30, stop_event=preset)
    assert factory.calls == 0

    stop = asyncio.Event()
    sink = MemorySink()
    factory = ScriptedConnectionFactory(
        [
            FakeConnection(
                [],
                on_empty=stop.set,
                disconnect_when_empty=True,
            )
        ]
    )
    collector = _capture(
        sink,
        factory,
        config=_config(max_reconnects=1, reconnect_delay_seconds=1),
    )
    with pytest.raises(PolymarketTransportError, match="reconnect"):
        await collector.capture_for(30, stop_event=stop)
    assert factory.calls == 1
    assert any(
        row.get("event") == "reconnect_incomplete"
        for row in _local_payloads(sink.records, "data_quality")
    )


def test_fixtures_are_synthetic_and_contain_no_account_or_secret_material() -> None:
    combined = b"".join(path.read_bytes() for path in sorted(_FIXTURE_DIR.iterdir()))
    lowered = combined.lower()
    assert b"wholly synthetic" in lowered
    assert b"not captured from a live" in lowered
    for forbidden in (
        b"poly_api_key",
        b"poly_signature",
        b"poly_passphrase",
        b"private_key",
        b"authorization:",
        b"bearer ",
    ):
        assert forbidden not in lowered
    assert issubclass(PolymarketDataIntegrityError, PolymarketCaptureError)
