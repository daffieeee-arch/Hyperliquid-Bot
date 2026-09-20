"""Deterministic offline DATA-1D tests for public Bitvavo Standard BTC-EUR."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast

import duckdb
import pytest
from websockets.exceptions import PayloadTooBig

from hyperliquid_bot.bitvavo_standard_research import (
    BITVAVO_FEED_PRODUCT,
    BITVAVO_RESEARCH_PRODUCT,
    BITVAVO_STANDARD_WEBSOCKET_CLIENT_PING_INTERVAL,
    BITVAVO_STANDARD_WEBSOCKET_CLIENT_PING_TIMEOUT,
    BITVAVO_STANDARD_WEBSOCKET_URL,
    BitvavoDataIntegrityError,
    BitvavoSinkError,
    BitvavoStandardResearchCollector,
    BitvavoStandardResearchConfig,
    BitvavoTransportError,
    WebSocketConnection,
    _BookState,
    _connection_factory,
    _decode_json_object,
    _normalize_ticker,
    _normalize_trade,
    _subscription_channels,
)
from hyperliquid_bot.parquet_research import (
    RESEARCH_VIEW_NAMES,
    ParquetResearchWriter,
    ParquetRotation,
    create_research_catalog,
)
from hyperliquid_bot.raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
    capture_application_payload,
)

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "bitvavo"


def _fixture_bytes(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


def _fixture_text(name: str) -> str:
    return _fixture_bytes(name).decode("utf-8")


def _document(payload: str | bytes) -> dict[str, object]:
    payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    return _decode_json_object(payload_bytes)


def _ack(channel: str) -> str:
    return json.dumps(
        {
            "event": "book" if channel == "book" else "subscribed",
            "subscriptions": {channel: [BITVAVO_RESEARCH_PRODUCT]},
        },
        separators=(",", ":"),
    )


def _book_update(
    nonce: int,
    *,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
    market: str = BITVAVO_RESEARCH_PRODUCT,
    timestamp: int | None = 1_752_139_200_123_456_789,
) -> str:
    document: dict[str, object] = {
        "event": "book",
        "market": market,
        "nonce": nonce,
        "bids": [] if bids is None else bids,
        "asks": [] if asks is None else asks,
    }
    if timestamp is not None:
        document["timestamp"] = timestamp
    return json.dumps(document, separators=(",", ":"))


def _snapshot(
    nonce: int,
    *,
    request_id: int = 1,
    market: str = BITVAVO_RESEARCH_PRODUCT,
    timestamp: int | None = 1_752_139_200_123_456_789,
) -> str:
    response: dict[str, object] = {
        "market": market,
        "nonce": nonce,
        "bids": [["99999.980000000000000001", "0.020000000000000001"]],
        "asks": [["100000.010000000000000001", "0.030000000000000001"]],
    }
    if timestamp is not None:
        response["timestamp"] = timestamp
    return json.dumps(
        {"action": "getBook", "requestId": request_id, "response": response},
        separators=(",", ":"),
    )


class MemorySink:
    def __init__(self, *, fail_on_append: int | None = None) -> None:
        self.records: list[RawResearchRecord] = []
        self._fail_on_append = fail_on_append
        self.append_attempts = 0

    async def append(self, record: RawResearchRecord) -> None:
        self.append_attempts += 1
        if self.append_attempts == self._fail_on_append:
            raise RuntimeError("injected sink failure")
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
        disconnect_when_empty: bool = False,
    ) -> None:
        self._messages = deque(messages)
        self._on_last = on_last
        self._disconnect_when_empty = disconnect_when_empty
        self._never = asyncio.Event()
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        if not self._messages:
            if self._disconnect_when_empty:
                raise ConnectionError("scripted disconnect")
            await self._never.wait()
            raise AssertionError("unreachable")
        message = self._messages.popleft()
        if not self._messages and self._on_last is not None:
            self._on_last()
        if isinstance(message, BaseException):
            raise message
        return message


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
        return f"standard-session-{self.count}"


def _standard_messages(*, stop_event: asyncio.Event | None = None) -> list[str]:
    del stop_event
    return [
        _ack("trades"),
        _ack("ticker"),
        _ack("book"),
        _fixture_text("book_update_frame.json"),
        _fixture_text("book_snapshot_response.json"),
        _fixture_text("trade_frame.json"),
        _fixture_text("ticker_frame.json"),
        _book_update(
            438525,
            bids=[["99999.980000000000000001", "0"]],
            asks=[["100000.010000000000000001", "0.040000000000000001"]],
        ),
    ]


def _marker_documents(
    records: Sequence[RawResearchRecord],
    *,
    channel: str | None = None,
) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    for record in records:
        if record.direction is not MessageDirection.LOCAL:
            continue
        if channel is not None and record.channel != channel:
            continue
        documents.append(cast(dict[str, object], json.loads(record.payload_bytes)))
    return documents


def test_fixed_public_scope_has_no_credential_or_pro_surface() -> None:
    assert BITVAVO_STANDARD_WEBSOCKET_URL == "wss://ws.bitvavo.com/v2/"
    assert BITVAVO_RESEARCH_PRODUCT == "BTC-EUR"
    assert BITVAVO_FEED_PRODUCT == "standard"
    # Official Exchange WS docs do not mandate client-driven ping for public
    # market data: https://docs.bitvavo.com/docs/websocket-api/introduction/
    # Match DATA-1E / DATA-1F: disable library keepalive self-closes (1011).
    assert BITVAVO_STANDARD_WEBSOCKET_CLIENT_PING_INTERVAL is None
    assert BITVAVO_STANDARD_WEBSOCKET_CLIENT_PING_TIMEOUT is None
    signature = inspect.signature(BitvavoStandardResearchCollector)
    assert not ({"key", "secret", "token", "credential", "auth"} & set(signature.parameters))


@pytest.mark.asyncio
async def test_connection_factory_disables_client_driven_websocket_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_connect(
        uri: str, **options: object
    ) -> AbstractAsyncContextManager[WebSocketConnection]:
        captured["uri"] = uri
        captured["options"] = options

        @asynccontextmanager
        async def context() -> AsyncIterator[WebSocketConnection]:
            yield FakeConnection(())

        return context()

    monkeypatch.setattr("hyperliquid_bot.bitvavo_standard_research.connect", fake_connect)
    factory = _connection_factory(BitvavoStandardResearchConfig())
    async with factory():
        pass
    assert captured["uri"] == BITVAVO_STANDARD_WEBSOCKET_URL
    options = captured["options"]
    assert isinstance(options, dict)
    assert options["ping_interval"] is None
    assert options["ping_timeout"] is None


def test_subscription_acknowledgements_are_structural_and_exact_scope() -> None:
    assert _subscription_channels(_document(_ack("trades"))) == {"trades"}
    assert _subscription_channels(_document(_ack("ticker"))) == {"ticker"}
    assert _subscription_channels(_document(_ack("book"))) == {"book"}
    assert _subscription_channels(
        _document(
            '{"event":"book","subscriptions":{"trades":["BTC-EUR"],'
            '"ticker":["BTC-EUR"],"book":["BTC-EUR"]}}'
        )
    ) == {"trades", "ticker", "book"}

    with pytest.raises(BitvavoDataIntegrityError, match="scope"):
        _subscription_channels(
            _document('{"event":"subscribed","subscriptions":{"account":["BTC-EUR"]}}')
        )
    with pytest.raises(BitvavoDataIntegrityError, match="market"):
        _subscription_channels(
            _document('{"event":"subscribed","subscriptions":{"trades":["ETH-EUR"]}}')
        )


def test_book_bootstrap_uses_documented_newer_snapshot_rule_and_exact_chain() -> None:
    state = _BookState()
    assert state.ingest_update(_document(_book_update(100)), 10) is None
    assert state.ingest_update(_document(_book_update(101)), 11) is None

    accepted = state.accept_snapshot(_document(_snapshot(101)), 12, expected_request_id=1)
    assert not accepted.retry_required
    assert [frame["nonce"] for frame in accepted.normalized_frames] == ["101"]
    assert state.has_snapshot
    assert state.last_nonce == 101

    update = state.ingest_update(_document(_book_update(102)), 13)
    assert update is not None
    assert update["nonce"] == "102"
    assert state.last_nonce == 102


@pytest.mark.parametrize("snapshot_nonce", [99, 100])
def test_book_bootstrap_retries_snapshot_not_newer_than_first_buffered_update(
    snapshot_nonce: int,
) -> None:
    state = _BookState()
    state.ingest_update(_document(_book_update(100)), 1)
    outcome = state.accept_snapshot(
        _document(_snapshot(snapshot_nonce)),
        2,
        expected_request_id=1,
    )
    assert outcome.retry_required
    assert not state.has_snapshot
    assert state.has_buffered_update


def test_book_snapshot_retains_and_applies_only_exactly_joining_buffered_updates() -> None:
    state = _BookState()
    for nonce in (100, 101, 102):
        state.ingest_update(_document(_book_update(nonce)), nonce)
    outcome = state.accept_snapshot(_document(_snapshot(101)), 200, expected_request_id=1)
    assert [frame["nonce"] for frame in outcome.normalized_frames] == ["101", "102"]
    assert state.last_nonce == 102


@pytest.mark.parametrize(
    ("first", "second", "quality_event"),
    [(100, 100, "sequence_error"), (100, 99, "sequence_error"), (100, 102, "sequence_gap")],
)
def test_book_duplicate_regression_reset_and_gap_fail_closed(
    first: int,
    second: int,
    quality_event: str,
) -> None:
    state = _BookState()
    state.ingest_update(_document(_book_update(first)), 1)
    with pytest.raises(BitvavoDataIntegrityError) as raised:
        state.ingest_update(_document(_book_update(second)), 2)
    assert raised.value.quality_event == quality_event
    assert not state.has_snapshot
    assert not state.has_buffered_update


def test_active_book_gap_clears_state_and_requires_a_new_snapshot() -> None:
    state = _BookState()
    state.ingest_update(_document(_book_update(100)), 1)
    state.accept_snapshot(_document(_snapshot(101)), 2, expected_request_id=1)
    with pytest.raises(BitvavoDataIntegrityError) as raised:
        state.ingest_update(_document(_book_update(103)), 3)
    assert raised.value.quality_event == "sequence_gap"
    assert not state.has_snapshot


def test_book_buffer_is_bounded() -> None:
    state = _BookState(max_buffered_updates=1)
    state.ingest_update(_document(_book_update(100)), 1)
    with pytest.raises(BitvavoDataIntegrityError) as raised:
        state.ingest_update(_document(_book_update(101)), 2)
    assert raised.value.quality_event == "buffer_overflow"


def test_book_wire_order_delete_and_optional_timestamp_are_preserved() -> None:
    state = _BookState()
    state.ingest_update(
        _document(
            _book_update(
                100,
                asks=[["100000.010000000000000001", "0"]],
                bids=[["99999.980000000000000001", "0.020000000000000001"]],
                timestamp=None,
            )
        ),
        1,
    )
    outcome = state.accept_snapshot(
        _document(_snapshot(101, timestamp=None)),
        2,
        expected_request_id=1,
    )
    snapshot = outcome.normalized_frames[0]
    assert snapshot["venue_timestamp_ns"] is None
    events = cast(list[dict[str, object]], snapshot["events"])
    assert [(event["wire_order"], event["side"]) for event in events] == [
        (0, "bid"),
        (1, "ask"),
    ]

    update = state.ingest_update(
        _document(
            _book_update(
                102,
                asks=[["100000.010000000000000001", "0"]],
                bids=[],
            )
        ),
        3,
    )
    assert update is not None
    update_events = cast(list[dict[str, object]], update["events"])
    assert update_events[0]["action"] == "delete"


def test_decimal_strings_and_distinct_trade_timestamps_survive_without_float_conversion() -> None:
    trade = _normalize_trade(_document(_fixture_bytes("trade_frame.json")), 10)
    event = cast(list[dict[str, object]], trade["events"])[0]
    assert event["price"] == "99999.990000000000000001"
    assert event["quantity"] == "0.010000000000000001"
    assert event["event_time_ms"] == "1752139200123"
    assert event["event_time_ns"] == "1752139200123456789"

    ticker = _normalize_ticker(_document(_fixture_bytes("ticker_frame.json")), 11)
    assert ticker["bid_price"] == "99999.980000000000000001"
    assert "event_time" not in ticker

    bid_only = _normalize_ticker(
        _document(
            '{"event":"ticker","market":"BTC-EUR","bestBid":"99999.98","bestBidSize":"0.02"}'
        ),
        12,
    )
    assert bid_only["bid_price"] == "99999.98"
    assert bid_only["ask_price"] is None
    assert bid_only["last_price"] is None

    with pytest.raises(BitvavoDataIntegrityError, match="pair"):
        _normalize_ticker(
            _document('{"event":"ticker","market":"BTC-EUR","bestAsk":"100000.01"}'),
            13,
        )

    bad = _document(_fixture_bytes("ticker_frame.json"))
    bad["bestBid"] = "1e5"
    with pytest.raises(BitvavoDataIntegrityError, match="decimal"):
        _normalize_ticker(bad, 14)


def test_numeric_wire_fields_must_be_json_integers_not_quoted_strings() -> None:
    quoted_nonce = _document('{"event":"book","market":"BTC-EUR","nonce":"1","bids":[],"asks":[]}')
    with pytest.raises(BitvavoDataIntegrityError, match="integer"):
        _BookState().ingest_update(quoted_nonce, 1)

    numeric_decimal = _document(
        '{"event":"ticker","market":"BTC-EUR","bestBid":99999.98,'
        '"bestBidSize":"0.02","bestAsk":"100000.01",'
        '"bestAskSize":"0.03","lastPrice":"99999.99"}'
    )
    with pytest.raises(BitvavoDataIntegrityError, match="decimal"):
        _normalize_ticker(numeric_decimal, 2)


@pytest.mark.asyncio
async def test_collector_captures_callback_clocks_exact_bytes_and_fixed_safe_outbound() -> None:
    stop_event = asyncio.Event()
    inbound = _standard_messages()
    connection = FakeConnection(inbound, on_last=stop_event.set)
    sink = MemorySink()
    utc = Counter(10_000)
    monotonic = Counter(20_000)
    collector = BitvavoStandardResearchCollector(
        sink,
        connection_factory=ScriptedConnectionFactory([connection]),
        utc_ns=utc,
        monotonic_ns=monotonic,
        session_id_factory=SessionIds(),
    )

    await collector.capture_for(5.0, stop_event=stop_event)

    inbound_records = [
        record for record in sink.records if record.direction is MessageDirection.INBOUND
    ]
    assert [record.payload_bytes for record in inbound_records] == [
        message.encode("utf-8") for message in inbound
    ]
    assert all(
        record.payload_sha256 == hashlib.sha256(record.payload_bytes).hexdigest()
        for record in inbound_records
    )
    assert all(record.received_utc_ns > 10_000 for record in inbound_records)
    assert all(record.received_monotonic_ns > 20_000 for record in inbound_records)
    assert [record.message_ordinal for record in sink.records] == list(
        range(1, len(sink.records) + 1)
    )

    outbound = [
        record.payload_bytes
        for record in sink.records
        if record.direction is MessageDirection.OUTBOUND
    ]
    assert len(outbound) == 2
    assert b'"action":"subscribe"' in outbound[0]
    assert outbound[1] == (b'{"action":"getBook","depth":1000,"market":"BTC-EUR","requestId":1}')
    lowered = b"\n".join(outbound).lower()
    assert not any(word in lowered for word in (b"secret", b"token", b"key", b"auth"))


@pytest.mark.asyncio
async def test_collector_retries_only_documented_stale_snapshot_within_bound() -> None:
    stop_event = asyncio.Event()
    messages = [
        _ack("trades"),
        _ack("ticker"),
        _ack("book"),
        _book_update(100),
        _snapshot(100, request_id=1),
        _snapshot(101, request_id=2),
        _book_update(102),
    ]
    connection = FakeConnection(messages, on_last=stop_event.set)
    sink = MemorySink()
    collector = BitvavoStandardResearchCollector(
        sink,
        connection_factory=ScriptedConnectionFactory([connection]),
        session_id_factory=SessionIds(),
    )

    await collector.capture_for(5.0, stop_event=stop_event)

    get_book_requests = [
        json.loads(cast(str, payload))
        for payload in connection.sent
        if '"getBook"' in cast(str, payload)
    ]
    assert [request["requestId"] for request in get_book_requests] == [1, 2]
    quality_events = _marker_documents(sink.records, channel="data_quality")
    assert [event["event"] for event in quality_events] == [
        "snapshot_retry_required",
        "snapshot_received",
    ]


@pytest.mark.asyncio
async def test_snapshot_retry_exhaustion_is_terminal_without_transport_retry() -> None:
    messages = [
        _ack("trades"),
        _ack("ticker"),
        _ack("book"),
        _book_update(100),
        _snapshot(100),
    ]
    connection = FakeConnection(messages)
    factory = ScriptedConnectionFactory([connection])
    collector = BitvavoStandardResearchCollector(
        MemorySink(),
        config=BitvavoStandardResearchConfig(max_snapshot_requests=1),
        connection_factory=factory,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoDataIntegrityError, match="retry bound"):
        await collector.capture_for(5.0)
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_reconnect_uses_fresh_session_subscription_buffer_and_snapshot() -> None:
    first_messages = [
        _ack("trades"),
        _ack("ticker"),
        _ack("book"),
        _book_update(100),
        _snapshot(101),
        _book_update(102),
    ]
    second_stop = asyncio.Event()
    second_messages = [
        _ack("trades"),
        _ack("ticker"),
        _ack("book"),
        _book_update(500),
        _snapshot(501),
        _book_update(502),
    ]
    first = FakeConnection(first_messages, disconnect_when_empty=True)
    second = FakeConnection(second_messages, on_last=second_stop.set)
    factory = ScriptedConnectionFactory([first, second])
    sink = MemorySink()
    collector = BitvavoStandardResearchCollector(
        sink,
        config=BitvavoStandardResearchConfig(reconnect_delay_seconds=0),
        connection_factory=factory,
        session_id_factory=SessionIds(),
    )

    await collector.capture_for(5.0, stop_event=second_stop)

    assert factory.calls == 2
    assert len({record.session_id for record in sink.records}) == 2
    quality_events = _marker_documents(sink.records, channel="data_quality")
    assert [event["event"] for event in quality_events] == [
        "snapshot_received",
        "gap_detected",
        "resnapshot_received",
    ]
    sessions = _marker_documents(sink.records, channel="session")
    assert any(event["event"] == "reconnected" for event in sessions)
    assert (
        sum(
            record.channel == "book_snapshot_request"
            for record in sink.records
            if record.direction is MessageDirection.OUTBOUND
        )
        == 2
    )


@pytest.mark.asyncio
async def test_transport_reconnect_count_is_hard_bounded() -> None:
    connection = FakeConnection([], disconnect_when_empty=True)
    factory = ScriptedConnectionFactory([connection])
    collector = BitvavoStandardResearchCollector(
        MemorySink(),
        config=BitvavoStandardResearchConfig(max_reconnects=0),
        connection_factory=factory,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoTransportError, match="reconnect bound"):
        await collector.capture_for(5.0)
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_nonce_gap_fails_closed_without_transport_retry() -> None:
    messages = [
        _ack("trades"),
        _ack("ticker"),
        _ack("book"),
        _book_update(100),
        _snapshot(101),
        _book_update(103),
    ]
    factory = ScriptedConnectionFactory([FakeConnection(messages)])
    sink = MemorySink()
    collector = BitvavoStandardResearchCollector(
        sink,
        connection_factory=factory,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoDataIntegrityError) as raised:
        await collector.capture_for(5.0)
    assert raised.value.quality_event == "sequence_gap"
    assert factory.calls == 1
    assert any(
        event["event"] == "sequence_gap"
        for event in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_private_or_account_channel_is_stored_raw_then_rejected_without_fallback() -> None:
    private_frame = '{"event":"account","market":"BTC-EUR"}'
    factory = ScriptedConnectionFactory([FakeConnection([private_frame])])
    sink = MemorySink()
    collector = BitvavoStandardResearchCollector(
        sink,
        connection_factory=factory,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoDataIntegrityError) as raised:
        await collector.capture_for(5.0)
    assert private_frame not in str(raised.value)
    assert factory.calls == 1
    assert any(record.payload_bytes == private_frame.encode() for record in sink.records)


@pytest.mark.asyncio
async def test_malformed_and_oversize_complete_frames_are_stored_before_safe_error() -> None:
    malformed = b'{"event":"ticker","market":"BTC-EUR","bestBid":"private-sentinel"'
    sink = MemorySink()
    collector = BitvavoStandardResearchCollector(
        sink,
        connection_factory=ScriptedConnectionFactory([FakeConnection([malformed])]),
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoDataIntegrityError) as malformed_error:
        await collector.capture_for(5.0)
    assert "private-sentinel" not in str(malformed_error.value)
    assert any(record.payload_bytes == malformed for record in sink.records)

    oversize = _ack("trades")
    oversize_sink = MemorySink()
    oversize_collector = BitvavoStandardResearchCollector(
        oversize_sink,
        config=BitvavoStandardResearchConfig(max_application_payload_bytes=8),
        connection_factory=ScriptedConnectionFactory([FakeConnection([oversize])]),
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoDataIntegrityError) as oversize_error:
        await oversize_collector.capture_for(5.0)
    assert oversize_error.value.quality_event == "payload_oversize"
    assert any(record.payload_bytes == oversize.encode() for record in oversize_sink.records)


@pytest.mark.asyncio
async def test_transport_truncation_and_sink_failure_are_terminal() -> None:
    truncation_sink = MemorySink()
    truncation_factory = ScriptedConnectionFactory([FakeConnection([PayloadTooBig(9, 8)])])
    truncation_collector = BitvavoStandardResearchCollector(
        truncation_sink,
        connection_factory=truncation_factory,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoDataIntegrityError) as truncation_error:
        await truncation_collector.capture_for(5.0)
    assert truncation_error.value.quality_event == "truncation_error"
    assert truncation_factory.calls == 1
    assert not any(
        record.direction is MessageDirection.INBOUND for record in truncation_sink.records
    )

    sink = MemorySink(fail_on_append=2)
    sink_factory = ScriptedConnectionFactory([FakeConnection([])])
    sink_collector = BitvavoStandardResearchCollector(
        sink,
        connection_factory=sink_factory,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoSinkError):
        await sink_collector.capture_for(5.0)
    assert sink_factory.calls == 1


@pytest.mark.asyncio
async def test_bbo_forward_fill_is_causal_source_linked_and_session_scoped(
    tmp_path: Path,
) -> None:
    writer = ParquetResearchWriter(tmp_path / "parquet")
    raw_payloads = (
        (
            "session-a",
            b'{"event":"ticker","market":"BTC-EUR","bestBid":"99999.98","bestBidSize":"0.02"}',
        ),
        (
            "session-a",
            b'{"event":"ticker","market":"BTC-EUR","bestAsk":"100000.01","bestAskSize":"0.03"}',
        ),
        (
            "session-b",
            b'{"event":"ticker","market":"BTC-EUR","bestAsk":"100001.01","bestAskSize":"0.04"}',
        ),
    )
    expected_sources: list[tuple[int, int, int, str]] = []
    ordinal = 0
    for index, (session_id, payload_bytes) in enumerate(raw_payloads, start=1):
        ordinal += 1
        raw_ordinal = ordinal
        received_utc_ns = 10_000 + index
        received_monotonic_ns = 20_000 + index
        source = RawResearchRecord(
            schema_version=RAW_RESEARCH_SCHEMA_VERSION,
            venue="bitvavo",
            product="BTC-EUR",
            channel="ticker",
            session_id=session_id,
            message_ordinal=raw_ordinal,
            received_utc_ns=received_utc_ns,
            received_monotonic_ns=received_monotonic_ns,
            direction=MessageDirection.INBOUND,
            frame_type=FrameType.TEXT,
            payload_encoding=PayloadEncoding.UTF8,
            payload_bytes=payload_bytes,
        )
        await writer.append(source)
        expected_sources.append(
            (raw_ordinal, received_utc_ns, received_monotonic_ns, source.payload_sha256)
        )

        normalized = _normalize_ticker(_document(payload_bytes), raw_ordinal)
        ordinal += 1
        await writer.append(
            RawResearchRecord(
                schema_version=RAW_RESEARCH_SCHEMA_VERSION,
                venue="bitvavo",
                product="BTC-EUR",
                channel="normalized_ticker",
                session_id=session_id,
                message_ordinal=ordinal,
                received_utc_ns=30_000 + index,
                received_monotonic_ns=40_000 + index,
                direction=MessageDirection.LOCAL,
                frame_type=FrameType.MARKER,
                payload_encoding=PayloadEncoding.UTF8_JSON,
                payload_bytes=json.dumps(
                    normalized,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode(),
            )
        )
    await writer.aclose()
    database_path = tmp_path / "research.duckdb"
    create_research_catalog(tmp_path / "parquet", database_path)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT session_id, raw_message_ordinal,
                   bid_price_update, ask_price_update,
                   bid_price, ask_price, bbo_complete,
                   received_utc_ns, received_monotonic_ns, payload_sha256
            FROM bitvavo_spot_bbo
            ORDER BY raw_message_ordinal
            """
        ).fetchall()
    finally:
        connection.close()

    assert [row[:7] for row in rows] == [
        ("session-a", 1, "99999.98", None, "99999.98", None, False),
        ("session-a", 3, None, "100000.01", "99999.98", "100000.01", True),
        ("session-b", 5, None, "100001.01", None, "100001.01", False),
    ]
    assert [row[1:2] + row[7:] for row in rows] == expected_sources


@pytest.mark.asyncio
async def test_exact_raw_parquet_roundtrip_and_standard_duckdb_views(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "parquet"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=12,
            max_payload_bytes=1_000_000,
            max_interval_seconds=300,
        ),
    )
    stop_event = asyncio.Event()
    inbound = _standard_messages()
    collector = BitvavoStandardResearchCollector(
        writer,
        connection_factory=ScriptedConnectionFactory(
            [FakeConnection(inbound, on_last=stop_event.set)]
        ),
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    await writer.aclose()

    parquet_files = sorted(parquet_dir.glob("*.parquet"))
    assert 1 < len(parquet_files) < len(inbound)
    assert not tuple(parquet_dir.glob("*.partial"))
    create_research_catalog(parquet_dir, database_path)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        stored = connection.execute(
            """
            SELECT payload_bytes, payload_sha256
            FROM raw_records
            WHERE venue = 'bitvavo' AND direction = 'inbound'
            ORDER BY message_ordinal
            """
        ).fetchall()
        assert [bytes(row[0]) for row in stored] == [message.encode() for message in inbound]
        assert [row[1] for row in stored] == [
            hashlib.sha256(message.encode()).hexdigest() for message in inbound
        ]
        assert {
            "bitvavo_spot_trades",
            "bitvavo_spot_bbo",
            "bitvavo_spot_l2_events",
        } <= set(RESEARCH_VIEW_NAMES)
        assert connection.execute("SELECT count(*) FROM bitvavo_spot_trades").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM bitvavo_spot_bbo").fetchone() == (1,)
        assert connection.execute("SELECT bbo_complete FROM bitvavo_spot_bbo").fetchone() == (True,)
        assert connection.execute(
            "SELECT feed_product, price, quantity FROM bitvavo_spot_trades"
        ).fetchone() == (
            "standard",
            "99999.990000000000000001",
            "0.010000000000000001",
        )
        assert connection.execute(
            """
            SELECT message_type, nonce, action, price, quantity
            FROM bitvavo_spot_l2_events
            WHERE action = 'delete'
            """
        ).fetchone() == (
            "update",
            "438525",
            "delete",
            "99999.980000000000000001",
            "0",
        )
        assert connection.execute(
            "SELECT count(DISTINCT source_channel) FROM bitvavo_spot_l2_events"
        ).fetchone() == (2,)
        assert connection.execute(
            """
            SELECT count(*)
            FROM bitvavo_spot_trades AS view_row
            JOIN raw_records AS raw
              ON raw.message_ordinal = view_row.raw_message_ordinal
             AND raw.received_utc_ns = view_row.received_utc_ns
             AND raw.received_monotonic_ns = view_row.received_monotonic_ns
             AND raw.payload_sha256 = view_row.payload_sha256
             AND raw.channel = 'trades'
             AND raw.direction = 'inbound'
            """
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_capture_application_payload_keeps_bytes_and_callback_clock_order() -> None:
    utc = Counter(10)
    monotonic = Counter(20)
    frame = _fixture_bytes("trade_frame.json")
    captured = capture_application_payload(frame, utc_ns=utc, monotonic_ns=monotonic)
    assert captured.received_utc_ns == 11
    assert captured.received_monotonic_ns == 21
    assert captured.payload_bytes == frame


@pytest.mark.parametrize("duration", [0, 0.5, 604801, True, "60"])
@pytest.mark.asyncio
async def test_capture_duration_is_strictly_bounded(duration: object) -> None:
    collector = BitvavoStandardResearchCollector(
        MemorySink(),
        connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )
    expected = TypeError if type(duration) not in (int, float) else ValueError
    with pytest.raises(expected):
        await collector.capture_for(cast(float, duration))


def test_retained_duration_raises_the_historical_smoke_cap() -> None:
    from hyperliquid_bot.bitvavo_standard_research import (
        MAX_CAPTURE_SECONDS,
        RETAINED_MAX_RECONNECTS,
        SMOKE_CAPTURE_SECONDS,
        _config_for_duration,
        _require_bounded_duration,
        data1d_feed_name,
        standard_subscription_channels,
        standard_subscription_payload_text,
    )

    assert SMOKE_CAPTURE_SECONDS == 600.0
    assert MAX_CAPTURE_SECONDS == 7 * 24 * 60 * 60
    assert _require_bounded_duration(259200) == 259200.0
    assert _require_bounded_duration(MAX_CAPTURE_SECONDS) == float(MAX_CAPTURE_SECONDS)
    with pytest.raises(ValueError):
        _require_bounded_duration(MAX_CAPTURE_SECONDS + 1)
    assert _config_for_duration(600.0).max_reconnects == 1
    assert _config_for_duration(600.1).max_reconnects == RETAINED_MAX_RECONNECTS
    assert standard_subscription_channels() == ("trades", "ticker", "book")
    assert standard_subscription_channels(include_candles=True) == (
        "trades",
        "ticker",
        "book",
        "candles",
    )
    payload = json.loads(
        standard_subscription_payload_text(include_candles=True, candle_interval="1m")
    )
    assert payload["channels"][-1] == {
        "interval": ["1m"],
        "markets": ["BTC-EUR"],
        "name": "candles",
    }
    assert data1d_feed_name() == "bitvavo-standard-btc-eur-trades-ticker-book"
    assert data1d_feed_name(include_candles=True, candle_interval="5m") == (
        "bitvavo-standard-btc-eur-trades-ticker-book-candles-5m"
    )
    assert "mdpro" not in data1d_feed_name()


def test_candles_subscription_ack_and_normalize() -> None:
    from hyperliquid_bot.bitvavo_standard_research import _classify_document, _normalize_candles

    ack = _document(
        json.dumps(
            {
                "event": "subscribed",
                "subscriptions": {"candles": {"1m": ["BTC-EUR"]}},
            },
            separators=(",", ":"),
        )
    )
    assert _subscription_channels(
        ack,
        allowed_channels=frozenset({"trades", "ticker", "book", "candles"}),
        candle_interval="1m",
    ) == {"candles"}
    with pytest.raises(BitvavoDataIntegrityError, match="scope"):
        _subscription_channels(ack)

    frame = _document(
        json.dumps(
            {
                "event": "candles",
                "market": "BTC-EUR",
                "interval": "1m",
                "candle": [["1538784000000", "4999", "5012", "4999", "5012", "0.45"]],
            },
            separators=(",", ":"),
        )
    )
    assert _classify_document(frame) == "candles"
    normalized = _normalize_candles(frame, 7, expected_interval="1m")
    assert normalized["source_channel"] == "candles"
    assert normalized["interval"] == "1m"
    candles = cast(list[dict[str, object]], normalized["candles"])
    assert candles[0]["open"] == "4999"
    assert candles[0]["volume"] == "0.45"

    object_row_frame = _document(
        json.dumps(
            {
                "event": "candles",
                "market": "BTC-EUR",
                "interval": "1m",
                "candle": [
                    {
                        "timestamp": "1538784000000",
                        "open": "4999",
                        "high": "5012",
                        "low": "4999",
                        "close": "5012",
                        "volume": "0.45",
                    }
                ],
            },
            separators=(",", ":"),
        )
    )
    object_normalized = _normalize_candles(object_row_frame, 8, expected_interval="1m")
    object_candles = cast(list[dict[str, object]], object_normalized["candles"])
    assert object_candles[0]["timestamp_ms"] == "1538784000000"
    assert object_candles[0]["close"] == "5012"


def test_production_singular_candle_event_array_rows_classify_and_normalize() -> None:
    """Regression: Phase A std-candles run failed on singular event name.

    Exact inbound bytes from
    ``20260919t001418z-vps-phase-a-std-candles`` raw ordinal 695.
    Docs advertise ``event:"candles"``; Bitvavo production sent ``event:"candle"``.
    """
    from hyperliquid_bot.bitvavo_standard_research import _classify_document, _normalize_candles

    production_bytes = (
        b'{"event":"candle","market":"BTC-EUR","interval":"1m",'
        b'"candle":[[1789776840000,"70380","70389","70380","70389","0.00077932"]]}'
    )
    document = _document(production_bytes)
    assert document["event"] == "candle"
    assert _classify_document(document) == "candles"
    normalized = _normalize_candles(document, 695, expected_interval="1m")
    assert normalized["source_channel"] == "candles"
    assert normalized["interval"] == "1m"
    assert normalized["raw_message_ordinal"] == 695
    candles = cast(list[dict[str, object]], normalized["candles"])
    assert candles[0]["timestamp_ms"] == "1789776840000"
    assert candles[0]["open"] == "70380"
    assert candles[0]["high"] == "70389"
    assert candles[0]["low"] == "70380"
    assert candles[0]["close"] == "70389"
    assert candles[0]["volume"] == "0.00077932"


def test_data1d_capture_claim_never_uses_pro_paths(tmp_path: Path) -> None:
    from hyperliquid_bot.bitvavo_standard_research import data1d_capture_claim
    from hyperliquid_bot.reconstructable_paths import data1d_run_paths

    paths = data1d_run_paths(tmp_path, "sample-run")
    claim = data1d_capture_claim(run_id="sample-run", duration_seconds=259200, paths=paths)
    assert claim["schema"] == "data-1d-retained-capture-claim-v1"
    assert claim["state"] == "STARTED_FAIL_CLOSED"
    assert claim["path_contract"] == "data-1d-bitvavo-btc-eur-v1"
    assert claim["retained"] is True
    assert claim["mdpro_fallback"] is False
    assert claim["data1e_path_fallback"] is False
    assert claim["credentialless"] is True
    run_dir = cast(str, claim["run_dir"])
    assert "data-1d" in run_dir
    assert "data-1e" not in run_dir
    candles_claim = data1d_capture_claim(
        run_id="sample-run",
        duration_seconds=60,
        paths=paths,
        include_candles=True,
        candle_interval="1h",
    )
    assert candles_claim["include_candles"] is True
    assert candles_claim["candle_interval"] == "1h"
    assert candles_claim["retained"] is False
    assert "candles" in cast(list[str], candles_claim["channels"])
