"""Deterministic offline DATA-1E tests for Bitvavo Market Data Pro BTC-EUR."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import secrets
import traceback
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast

import duckdb
import pytest
from websockets.exceptions import ConnectionClosedError, PayloadTooBig, WebSocketException
from websockets.frames import Close

from hyperliquid_bot.bitvavo_mdpro_research import (
    BITVAVO_MDPRO_API_KEY_ENV,
    BITVAVO_MDPRO_API_SECRET_ENV,
    BITVAVO_MDPRO_BOOK_DEPTH,
    BITVAVO_MDPRO_FEED_PRODUCT,
    BITVAVO_MDPRO_PRODUCT,
    BITVAVO_MDPRO_SIGNATURE_PATH,
    BITVAVO_MDPRO_WEBSOCKET_URL,
    MAX_CAPTURE_SECONDS,
    RETAINED_MAX_RECONNECTS,
    SMOKE_CAPTURE_SECONDS,
    BitvavoMdProAuthenticationError,
    BitvavoMdProCredentials,
    BitvavoMdProDataIntegrityError,
    BitvavoMdProResearchCollector,
    BitvavoMdProResearchConfig,
    BitvavoMdProSinkError,
    BitvavoMdProTransportError,
    WebSocketConnection,
    _argument_parser,
    _authentication_acknowledged,
    _book_subscription_acknowledged,
    _BookState,
    _config_for_duration,
    _decode_json_object,
    _normalize_book_snapshot,
    _normalize_book_update,
    _normalize_ticker,
    _normalize_trade,
    _require_bounded_duration,
    _resolve_cli_mode,
    _subscription_acknowledged_channels,
    data1e_capture_claim,
    data1e_capture_health,
    data1e_feed_name,
    load_mdpro_credentials_from_env,
    mdpro_subscription_channels,
    mdpro_subscription_payload_text,
    refuse_protected_trade_keys,
    run_reconstructable_capture,
)
from hyperliquid_bot.parquet_research import (
    RESEARCH_VIEW_NAMES,
    ParquetResearchWriter,
    ParquetRotation,
    create_research_catalog,
)
from hyperliquid_bot.raw_research import MessageDirection, RawResearchRecord, RawResearchSink
from hyperliquid_bot.reconstructable_paths import DATA1E_PATH_CONTRACT_ID, data1e_run_paths

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "bitvavo_mdpro"


def _fixture_bytes(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


def _fixture_text(name: str) -> str:
    return _fixture_bytes(name).decode("utf-8")


def _document(payload: str | bytes) -> dict[str, object]:
    payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    return _decode_json_object(payload_bytes)


def _auth_ack(*, authenticated: bool = True) -> str:
    return json.dumps(
        {"event": "authenticate", "authenticated": authenticated},
        separators=(",", ":"),
    )


def _book_ack(*, event: str = "book") -> str:
    return json.dumps(
        {"event": event, "subscriptions": {"book": ["BTC-EUR"]}},
        separators=(",", ":"),
    )


def _pro_ack(*channels: str, event: str = "subscribed") -> str:
    names = channels or ("book", "trades")
    return json.dumps(
        {"event": event, "subscriptions": {name: ["BTC-EUR"] for name in names}},
        separators=(",", ":"),
    )


def _trade(
    *,
    trade_id: str = "391f4d94-485f-4fb0-b11f-39da1cfcfc2d",
    market: str = BITVAVO_MDPRO_PRODUCT,
    side: str = "sell",
) -> str:
    return json.dumps(
        {
            "event": "trade",
            "id": trade_id,
            "amount": "0.000963610000000001",
            "price": "9311.200000000000000001",
            "timestamp": 1_566_817_150_381,
            "market": market,
            "side": side,
            "timestampNs": 1_752_139_200_000_000_000,
        },
        separators=(",", ":"),
    )


def _ticker(*, market: str = BITVAVO_MDPRO_PRODUCT) -> str:
    return json.dumps(
        {
            "event": "ticker",
            "market": market,
            "bestBid": "9156.800000000000000001",
            "bestBidSize": "0.128405310000000001",
            "bestAsk": "9157.900000000000000001",
            "bestAskSize": "0.128660500000000001",
            "lastPrice": "9156.900000000000000001",
        },
        separators=(",", ":"),
    )


def _book_update(
    start: int,
    end: int | None = None,
    *,
    nonce: int | None = None,
    market: str = BITVAVO_MDPRO_PRODUCT,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> str:
    document: dict[str, object] = {
        "event": "book",
        "market": market,
        "bids": [] if bids is None else bids,
        "asks": [] if asks is None else asks,
        "timestamp": 1_752_139_200_123_456_789,
        "startMdSeqNo": start,
        "endMdSeqNo": start if end is None else end,
        "type": "update",
    }
    if nonce is not None:
        document["nonce"] = nonce
    return json.dumps(document, separators=(",", ":"))


def _snapshot(
    sequence: int,
    *,
    request_id: int = 1,
    nonce: int | None = None,
    market: str = BITVAVO_MDPRO_PRODUCT,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> str:
    response: dict[str, object] = {
        "market": market,
        "bids": ([["4999.900000000000000001", "0.015000000000000001"]] if bids is None else bids),
        "asks": ([["5001.100000000000000001", "0.016000000000000001"]] if asks is None else asks),
        "timestamp": 1_752_139_200_123_456_789,
        "mdSeqNo": sequence,
    }
    if nonce is not None:
        response["nonce"] = nonce
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
            raise RuntimeError("injected writer failure")
        self.records.append(record)

    async def aclose(self) -> None:
        return None


class FakeConnection:
    def __init__(
        self,
        messages: Sequence[str | bytes | BaseException],
        *,
        on_last: Callable[[], object] | None = None,
        disconnect_when_empty: bool = False,
        send_error: BaseException | None = None,
    ) -> None:
        self._messages = deque(messages)
        self._on_last = on_last
        self._disconnect_when_empty = disconnect_when_empty
        self._send_error = send_error
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        if self._send_error is not None:
            raise self._send_error
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if not self._messages:
            if self._disconnect_when_empty:
                raise OSError("synthetic disconnect")
            await asyncio.Future()
            raise AssertionError("unreachable")
        value = self._messages.popleft()
        if not self._messages and self._on_last is not None:
            self._on_last()
        if isinstance(value, BaseException):
            raise value
        return value


class ScriptedConnectionFactory:
    def __init__(self, connections: Sequence[FakeConnection]) -> None:
        self._connections = deque(connections)
        self.calls = 0

    def __call__(self) -> AbstractAsyncContextManager[WebSocketConnection]:
        @asynccontextmanager
        async def context() -> AsyncIterator[WebSocketConnection]:
            self.calls += 1
            if not self._connections:
                raise AssertionError("unexpected connection attempt")
            yield self._connections.popleft()

        return context()


class Counter:
    def __init__(self, start: int) -> None:
        self.value = start

    def __call__(self) -> int:
        self.value += 1
        return self.value


class SessionIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"mdpro-session-{self.value}"


def _credentials() -> BitvavoMdProCredentials:
    return BitvavoMdProCredentials(
        api_key=secrets.token_urlsafe(24),
        api_secret=secrets.token_urlsafe(32),
    )


def _marker_documents(
    records: Sequence[RawResearchRecord],
    *,
    channel: str,
) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(record.payload_bytes))
        for record in records
        if record.channel == channel and record.direction is MessageDirection.LOCAL
    ]


def _successful_messages(*, snapshot_sequence: int = 100) -> list[str]:
    return [
        _auth_ack(),
        _pro_ack("book", "trades"),
        _snapshot(snapshot_sequence, nonce=snapshot_sequence),
        _book_update(
            snapshot_sequence + 1,
            snapshot_sequence + 2,
            nonce=snapshot_sequence + 1,
            bids=[["4999.900000000000000001", "0"]],
            asks=[["5001.100000000000000001", "0.017000000000000001"]],
        ),
    ]


async def _capture_authentication_error(
    collector: BitvavoMdProResearchCollector,
) -> BitvavoMdProAuthenticationError:
    try:
        await collector.capture_for(5.0)
    except BitvavoMdProAuthenticationError as error:
        return error
    raise AssertionError("expected Market Data Pro authentication failure")


async def _capture_transport_error(
    collector: BitvavoMdProResearchCollector,
) -> BitvavoMdProTransportError:
    try:
        await collector.capture_for(5.0)
    except BitvavoMdProTransportError as error:
        return error
    raise AssertionError("expected Market Data Pro transport failure")


def test_fixed_scope_and_small_credential_surface() -> None:
    assert BITVAVO_MDPRO_WEBSOCKET_URL == "wss://ws-mdpro.bitvavo.com/v2/"
    assert "ws.bitvavo.com" not in BITVAVO_MDPRO_WEBSOCKET_URL
    assert BITVAVO_MDPRO_PRODUCT == "BTC-EUR"
    assert BITVAVO_MDPRO_FEED_PRODUCT == "market_data_pro"
    assert BITVAVO_MDPRO_BOOK_DEPTH == 1000
    signature = inspect.signature(BitvavoMdProResearchCollector)
    assert "credentials" in signature.parameters
    assert not {
        "api_key",
        "api_secret",
        "environment",
        "fallback",
        "standard_connection",
    } & set(signature.parameters)


def test_subscribe_set_is_pro_book_and_trades_with_optional_ticker() -> None:
    assert mdpro_subscription_channels() == ("book", "trades")
    assert mdpro_subscription_channels(include_ticker=False) == ("book", "trades")
    assert mdpro_subscription_channels(include_ticker=True) == ("book", "trades", "ticker")
    default_payload = json.loads(mdpro_subscription_payload_text())
    ticker_payload = json.loads(mdpro_subscription_payload_text(include_ticker=True))
    assert default_payload == {
        "action": "subscribe",
        "channels": [
            {"markets": ["BTC-EUR"], "name": "book"},
            {"markets": ["BTC-EUR"], "name": "trades"},
        ],
    }
    assert ticker_payload["channels"][-1] == {"markets": ["BTC-EUR"], "name": "ticker"}
    assert data1e_feed_name() == "bitvavo-mdpro-btc-eur-book-trades"
    assert data1e_feed_name(include_ticker=True) == "bitvavo-mdpro-btc-eur-book-trades-ticker"
    assert "standard" not in data1e_feed_name()
    assert "ws.bitvavo.com/v2" not in mdpro_subscription_payload_text()


def test_pro_subscription_ack_accepts_combined_and_incremental_official_forms() -> None:
    expected = frozenset({"book", "trades"})
    combined = _subscription_acknowledged_channels(
        _document(_pro_ack("book", "trades")),
        expected=expected,
    )
    incremental_book = _subscription_acknowledged_channels(
        _document(_book_ack(event="book")),
        expected=expected,
    )
    incremental_trades = _subscription_acknowledged_channels(
        _document(_pro_ack("trades")),
        expected=expected,
    )
    assert combined == expected
    assert incremental_book == frozenset({"book"})
    assert incremental_trades == frozenset({"trades"})
    assert not _subscription_acknowledged_channels(
        _document(
            '{"event":"subscribed","subscriptions":{"book":["BTC-EUR"],"ticker":["BTC-EUR"]}}'
        ),
        expected=expected,
    )
    assert not _subscription_acknowledged_channels(
        _document('{"event":"subscribed","subscriptions":{"trades":["ETH-EUR"]}}'),
        expected=expected,
    )


def test_credentials_sign_exact_official_preimage_and_repr_is_redacted() -> None:
    api_key = secrets.token_urlsafe(24)
    api_secret = secrets.token_urlsafe(32)
    credentials = BitvavoMdProCredentials(api_key=api_key, api_secret=api_secret)
    timestamp = 1_788_112_345_678

    payload = credentials._authentication_message(timestamp_ms=timestamp, window_ms=10_000)
    document = json.loads(payload)
    expected = hmac.new(
        api_secret.encode(),
        f"{timestamp}GET{BITVAVO_MDPRO_SIGNATURE_PATH}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()

    assert document == {
        "action": "authenticate",
        "key": api_key,
        "signature": expected,
        "timestamp": timestamp,
        "window": 10_000,
    }
    assert repr(credentials) == "BitvavoMdProCredentials(<redacted>)"
    assert str(credentials) == "BitvavoMdProCredentials(<redacted>)"
    assert api_key not in repr(credentials)
    assert api_secret not in repr(credentials)
    assert credentials._contains_sensitive_material(payload.encode())


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"event":"authenticate","authenticated":true}', True),
        ('{"event":"authenticate"}', True),
        ('{"event":"authenticate","authenticated":false}', False),
        ('{"event":"authenticate","authenticated":null}', False),
        ('{"authenticated":true}', False),
        ('{"authenticated":1}', False),
        ('{"event":"book","authenticated":true}', False),
    ],
)
def test_documented_and_official_sdk_auth_ack_forms_are_strict(
    payload: str,
    expected: bool,
) -> None:
    assert _authentication_acknowledged(_document(payload)) is expected


@pytest.mark.parametrize("event", ["book", "subscribed"])
def test_book_ack_accepts_only_official_event_forms_with_exact_scope(event: str) -> None:
    assert _book_subscription_acknowledged(_document(_book_ack(event=event)))
    assert not _book_subscription_acknowledged(
        _document('{"event":"subscribed","subscriptions":{"book":["ETH-EUR"]}}')
    )


@pytest.mark.parametrize(
    ("key_length", "secret_length"),
    [
        (7, 32),
        (24, 7),
    ],
)
def test_invalid_credentials_fail_without_value_echo(
    key_length: int,
    secret_length: int,
) -> None:
    api_key = "k" * key_length
    api_secret = "s" * secret_length
    with pytest.raises(BitvavoMdProAuthenticationError) as raised:
        BitvavoMdProCredentials(api_key=api_key, api_secret=api_secret)
    assert api_key not in str(raised.value)
    assert api_secret not in str(raised.value)


def test_official_fixtures_preserve_decimals_ranges_and_wire_order() -> None:
    update = _normalize_book_update(_document(_fixture_bytes("book_update_frame.json")), 7)
    snapshot = _normalize_book_snapshot(
        _document(_fixture_bytes("book_snapshot_response.json")),
        8,
        expected_request_id=1,
    )

    assert update["sequence_start"] == "438526"
    assert update["sequence_end"] == "438527"
    assert update["deprecated_nonce"] == "438526"
    assert update["venue_timestamp_ns"] == "1752139200123456789"
    assert update["events"] == [
        {
            "wire_order": 0,
            "side": "bid",
            "side_index": 0,
            "action": "delete",
            "price": "4999.900000000000000001",
            "quantity": "0",
        },
        {
            "wire_order": 1,
            "side": "ask",
            "side_index": 0,
            "action": "update",
            "price": "5001.100000000000000001",
            "quantity": "0.017000000000000001",
        },
    ]
    assert snapshot["sequence_start"] == snapshot["sequence_end"] == "438525"
    assert snapshot["message_type"] == "snapshot"


def test_official_trade_and_ticker_fixtures_preserve_decimals_and_pro_identity() -> None:
    trade = _normalize_trade(_document(_fixture_bytes("trade_frame.json")), 9)
    ticker = _normalize_ticker(_document(_fixture_bytes("ticker_frame.json")), 10)
    assert trade["event"] == "normalized_mdpro_trade_frame"
    assert trade["source_channel"] == "mdpro_trades"
    assert trade["events"] == [
        {
            "event_index": 0,
            "market": "BTC-EUR",
            "trade_id": "391f4d94-485f-4fb0-b11f-39da1cfcfc2d",
            "price": "9311.200000000000000001",
            "quantity": "0.000963610000000001",
            "taker_side": "sell",
            "event_time_ms": "1566817150381",
            "event_time_ns": "1752139200000000000",
        }
    ]
    assert ticker["event"] == "normalized_mdpro_ticker_frame"
    assert ticker["source_channel"] == "mdpro_ticker"
    assert ticker["bid_price"] == "9156.800000000000000001"
    assert ticker["last_price"] == "9156.900000000000000001"
    with pytest.raises(BitvavoMdProDataIntegrityError):
        _normalize_trade(_document(_trade(market="ETH-EUR")), 1)
    with pytest.raises(BitvavoMdProDataIntegrityError):
        _normalize_ticker(_document(_ticker(market="ETH-EUR")), 1)


def test_snapshot_first_and_grouped_range_chain() -> None:
    state = _BookState()
    acceptance = state.accept_snapshot(_document(_snapshot(100)), 1, expected_request_id=1)
    assert acceptance.retained_updates == 0
    assert state.last_sequence == 100
    normalized = state.ingest_update(_document(_book_update(101, 103)), 2)
    assert normalized is not None
    assert normalized["sequence_start"] == "101"
    assert normalized["sequence_end"] == "103"
    assert state.last_sequence == 103
    assert state.post_snapshot_updates == 1


def test_buffered_updates_are_covered_or_applied_without_fabricated_events() -> None:
    covered = _BookState()
    assert covered.ingest_update(_document(_book_update(90, 95)), 1) is None
    assert covered.ingest_update(_document(_book_update(96, 100)), 2) is None
    acceptance = covered.accept_snapshot(_document(_snapshot(100)), 3, expected_request_id=1)
    assert len(acceptance.normalized_frames) == 1
    assert acceptance.retained_updates == 0

    retained = _BookState()
    assert retained.ingest_update(_document(_book_update(101, 103)), 4) is None
    acceptance = retained.accept_snapshot(_document(_snapshot(100)), 5, expected_request_id=1)
    assert len(acceptance.normalized_frames) == 2
    assert acceptance.retained_updates == 1
    assert retained.last_sequence == 103
    assert retained.post_snapshot_updates == 1


def test_straddling_snapshot_range_fails_closed() -> None:
    state = _BookState()
    assert state.ingest_update(_document(_book_update(99, 101)), 1) is None
    with pytest.raises(BitvavoMdProDataIntegrityError) as raised:
        state.accept_snapshot(_document(_snapshot(100)), 2, expected_request_id=1)
    assert raised.value.quality_event == "sequence_overlap"
    assert not state.has_snapshot
    assert state.last_sequence is None


@pytest.mark.parametrize(
    ("first", "second", "quality_event"),
    [
        ((101, 101), (101, 101), "sequence_error"),
        ((101, 102), (102, 103), "sequence_error"),
        ((101, 102), (104, 104), "sequence_gap"),
    ],
)
def test_duplicate_out_of_order_and_gap_are_distinct_fail_closed_events(
    first: tuple[int, int],
    second: tuple[int, int],
    quality_event: str,
) -> None:
    state = _BookState()
    state.accept_snapshot(_document(_snapshot(100)), 1, expected_request_id=1)
    state.ingest_update(_document(_book_update(*first)), 2)
    with pytest.raises(BitvavoMdProDataIntegrityError) as raised:
        state.ingest_update(_document(_book_update(*second)), 3)
    assert raised.value.quality_event == quality_event
    assert not state.has_snapshot


@pytest.mark.parametrize(
    "payload",
    [
        _book_update(102, 101),
        _book_update(101, market="ETH-EUR"),
        '{"event":"book","market":"BTC-EUR","bids":[],"asks":[],"timestamp":1,'
        '"startMdSeqNo":"1","endMdSeqNo":1,"type":"update"}',
        '{"event":"book","market":"BTC-EUR","bids":[],"asks":[],"timestamp":1,'
        '"startMdSeqNo":1,"endMdSeqNo":1,"type":"snapshot"}',
    ],
)
def test_wrong_range_identity_or_wire_type_is_rejected(payload: str) -> None:
    with pytest.raises(BitvavoMdProDataIntegrityError):
        _normalize_book_update(_document(payload), 1)


def test_deprecated_nonce_is_optional_and_never_drives_sequence() -> None:
    state = _BookState()
    snapshot = state.accept_snapshot(
        _document(_snapshot(100, nonce=9_999)), 1, expected_request_id=1
    )
    update = state.ingest_update(_document(_book_update(101, nonce=1)), 2)
    assert snapshot.normalized_frames[0]["deprecated_nonce"] == "9999"
    assert update is not None and update["deprecated_nonce"] == "1"
    assert state.last_sequence == 101


@pytest.mark.asyncio
async def test_collector_keeps_only_exact_market_frames_and_sanitized_controls() -> None:
    stop_event = asyncio.Event()
    messages = _successful_messages()
    connection = FakeConnection(messages, on_last=stop_event.set)
    sink = MemorySink()
    credentials = _credentials()
    collector = BitvavoMdProResearchCollector(
        sink,
        credentials,
        connection_factory=ScriptedConnectionFactory([connection]),
        utc_ns=Counter(10_000),
        monotonic_ns=Counter(20_000),
        timestamp_ms=lambda: 1_788_112_345_678,
        session_id_factory=SessionIds(),
    )

    await collector.capture_for(5.0, stop_event=stop_event)

    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    assert [record.payload_bytes for record in inbound] == [
        messages[2].encode(),
        messages[3].encode(),
    ]
    assert [record.channel for record in inbound] == [
        "mdpro_book_snapshot",
        "mdpro_book",
    ]
    assert all(
        record.payload_sha256 == hashlib.sha256(record.payload_bytes).hexdigest()
        for record in inbound
    )
    assert [record.message_ordinal for record in sink.records] == list(
        range(1, len(sink.records) + 1)
    )
    assert all(record.received_utc_ns > 10_000 for record in inbound)
    assert all(record.received_monotonic_ns > 20_000 for record in inbound)

    assert len(connection.sent) == 3
    auth = cast(str, connection.sent[0])
    assert json.loads(auth)["action"] == "authenticate"
    assert connection.sent[1] == mdpro_subscription_payload_text(include_ticker=False)
    assert connection.sent[2] == (
        '{"action":"getBook","depth":1000,"market":"BTC-EUR","requestId":1}'
    )
    persisted = b"\n".join(record.payload_bytes for record in sink.records)
    assert not credentials._contains_sensitive_material(persisted)
    assert not any(
        record.payload_bytes in {messages[0].encode(), messages[1].encode()}
        for record in sink.records
    )
    assert [
        item["event"] for item in _marker_documents(sink.records, channel="authentication")
    ] == [
        "authentication_sent",
        "authentication_acknowledged",
    ]
    sent_subscribe = json.loads(connection.sent[1])
    assert {
        channel["name"] for channel in cast(list[dict[str, object]], sent_subscribe["channels"])
    } == {
        "book",
        "trades",
    }


@pytest.mark.asyncio
async def test_incremental_pro_acks_then_trades_stay_on_the_same_socket() -> None:
    stop_event = asyncio.Event()
    messages = [
        _auth_ack(),
        _book_ack(event="book"),
        _pro_ack("trades"),
        _snapshot(100),
        _book_update(101),
        _trade(),
    ]
    connection = FakeConnection(messages, on_last=stop_event.set)
    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        _credentials(),
        connection_factory=ScriptedConnectionFactory([connection]),
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    assert [record.channel for record in inbound] == [
        "mdpro_book_snapshot",
        "mdpro_book",
        "mdpro_trades",
    ]
    assert all(record.product == "BTC-EUR" for record in inbound)
    assert all(record.venue == "bitvavo" for record in inbound)
    assert not any(record.channel in {"trades", "ticker", "book"} for record in inbound)
    normalized_trades = [
        json.loads(record.payload_bytes)
        for record in sink.records
        if record.channel == "normalized_mdpro_trades"
    ]
    assert normalized_trades[0]["source_channel"] == "mdpro_trades"
    assert json.loads(cast(str, connection.sent[1]))["channels"][0]["name"] == "book"
    assert {item["name"] for item in json.loads(cast(str, connection.sent[1]))["channels"]} == {
        "book",
        "trades",
    }


@pytest.mark.asyncio
async def test_optional_ticker_is_flagged_and_unsolicited_ticker_fails_closed() -> None:
    stop_event = asyncio.Event()
    enabled = FakeConnection(
        [
            _auth_ack(),
            _pro_ack("book", "trades", "ticker"),
            _snapshot(100),
            _book_update(101),
            _ticker(),
        ],
        on_last=stop_event.set,
    )
    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        _credentials(),
        config=BitvavoMdProResearchConfig(include_ticker=True),
        connection_factory=ScriptedConnectionFactory([enabled]),
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    assert [
        record.channel for record in sink.records if record.direction is MessageDirection.INBOUND
    ][-1] == "mdpro_ticker"
    assert "ticker" in {item["name"] for item in json.loads(cast(str, enabled.sent[1]))["channels"]}

    refused = FakeConnection(
        [_auth_ack(), _pro_ack("book", "trades"), _snapshot(100), _book_update(101), _ticker()]
    )
    with pytest.raises(BitvavoMdProDataIntegrityError) as raised:
        await BitvavoMdProResearchCollector(
            MemorySink(),
            _credentials(),
            connection_factory=ScriptedConnectionFactory([refused]),
            session_id_factory=SessionIds(),
        ).capture_for(5.0)
    assert raised.value.quality_event == "subscription_error"


@pytest.mark.asyncio
async def test_invalid_auth_is_terminal_without_standard_fallback_or_raw_control() -> None:
    connection = FakeConnection([_auth_ack(authenticated=False)])
    factory = ScriptedConnectionFactory([connection])
    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        _credentials(),
        connection_factory=factory,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoMdProAuthenticationError):
        await collector.capture_for(5.0)
    assert factory.calls == 1
    assert len(connection.sent) == 1
    assert not any(record.direction is MessageDirection.INBOUND for record in sink.records)
    assert _marker_documents(sink.records, channel="data_quality")[-1]["event"] == (
        "authentication_failed"
    )


@pytest.mark.asyncio
async def test_auth_send_lower_failure_drops_secret_exception_context() -> None:
    api_key = secrets.token_urlsafe(24)
    api_secret = secrets.token_urlsafe(32)
    lower_sentinel = secrets.token_urlsafe(20)
    credentials = BitvavoMdProCredentials(api_key=api_key, api_secret=api_secret)
    connection = FakeConnection(
        [],
        send_error=RuntimeError(f"{api_key} {api_secret} {lower_sentinel}"),
    )
    collector = BitvavoMdProResearchCollector(
        MemorySink(),
        credentials,
        connection_factory=ScriptedConnectionFactory([connection]),
        session_id_factory=SessionIds(),
    )
    captured = await _capture_authentication_error(collector)
    rendered = "".join(
        traceback.TracebackException.from_exception(captured, capture_locals=True).format()
    )
    assert captured.__context__ is None
    assert captured.__cause__ is None
    for material in (api_key, api_secret, lower_sentinel):
        assert material not in str(captured)
        assert material not in rendered


@pytest.mark.asyncio
async def test_secret_echo_is_not_persisted_logged_or_retained_in_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_key = secrets.token_urlsafe(24)
    api_secret = secrets.token_urlsafe(32)
    credentials = BitvavoMdProCredentials(api_key=api_key, api_secret=api_secret)
    secret_echo = json.dumps(
        {"event": "authenticate", "authenticated": False, "message": api_key},
        separators=(",", ":"),
    )
    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        credentials,
        connection_factory=ScriptedConnectionFactory([FakeConnection([secret_echo])]),
        session_id_factory=SessionIds(),
    )
    captured = await _capture_authentication_error(collector)
    rendered = "".join(
        traceback.TracebackException.from_exception(
            captured,
            capture_locals=True,
        ).format()
    )
    persisted = b"\n".join(record.payload_bytes for record in sink.records)
    assert captured.__context__ is None
    assert captured.__cause__ is None
    assert not credentials._contains_sensitive_material(persisted)
    for material in (api_key, api_secret):
        assert material not in str(captured)
        assert material not in rendered
        assert material not in caplog.text


@pytest.mark.asyncio
async def test_secret_bearing_market_frame_is_blocked_before_persistence() -> None:
    stop_event = asyncio.Event()
    credentials = _credentials()
    auth_payload = credentials._authentication_message(timestamp_ms=1, window_ms=10_000)
    signature = cast(str, json.loads(auth_payload)["signature"])
    messages = [
        _auth_ack(),
        _pro_ack("book", "trades"),
        json.dumps(
            {
                "event": "book",
                "market": "BTC-EUR",
                "bids": [],
                "asks": [],
                "timestamp": 1,
                "startMdSeqNo": 1,
                "endMdSeqNo": 1,
                "type": "update",
                "signature": signature,
            },
            separators=(",", ":"),
        ),
    ]
    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        credentials,
        connection_factory=ScriptedConnectionFactory([FakeConnection(messages)]),
        timestamp_ms=lambda: 2,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoMdProAuthenticationError):
        await collector.capture_for(5.0, stop_event=stop_event)
    persisted = b"\n".join(record.payload_bytes for record in sink.records)
    assert signature.encode() not in persisted


@pytest.mark.parametrize("material_name", ["api_key", "api_secret", "signature"])
@pytest.mark.asyncio
async def test_json_escaped_credentials_never_reach_records_or_parquet(
    material_name: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_key = secrets.token_urlsafe(24)
    api_secret = secrets.token_urlsafe(32)
    credentials = BitvavoMdProCredentials(api_key=api_key, api_secret=api_secret)
    authentication = credentials._authentication_message(timestamp_ms=1, window_ms=10_000)
    signature = cast(str, json.loads(authentication)["signature"])
    material = {
        "api_key": api_key,
        "api_secret": api_secret,
        "signature": signature,
    }[material_name]
    document = cast(dict[str, object], json.loads(_book_update(1)))
    document["diagnostic"] = material
    unescaped = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    escape_index = len(material) // 2
    escaped_material = (
        f"{material[:escape_index]}\\u{ord(material[escape_index]):04x}"
        f"{material[escape_index + 1 :]}"
    )
    escaped_frame = unescaped.replace(material, escaped_material, 1)
    assert material not in escaped_frame

    parquet_dir = tmp_path / "parquet"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=2,
            max_payload_bytes=1_000_000,
            max_interval_seconds=300,
        ),
    )
    collector = BitvavoMdProResearchCollector(
        writer,
        credentials,
        config=BitvavoMdProResearchConfig(max_reconnects=0),
        connection_factory=ScriptedConnectionFactory(
            [FakeConnection([_auth_ack(), _pro_ack("book", "trades"), escaped_frame])]
        ),
        timestamp_ms=lambda: 2,
        session_id_factory=SessionIds(),
    )
    captured = await _capture_authentication_error(collector)
    rendered = "".join(
        traceback.TracebackException.from_exception(captured, capture_locals=True).format()
    )
    await writer.aclose()
    create_research_catalog(parquet_dir, database_path)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        rows = connection.execute("SELECT direction, payload_bytes FROM raw_records").fetchall()
    finally:
        connection.close()
    persisted = b"\n".join(bytes(row[1]) for row in rows)
    assert not any(row[0] == "inbound" for row in rows)
    assert not credentials._contains_sensitive_material(persisted)
    assert escaped_material.encode() not in persisted
    assert captured.__context__ is None
    assert captured.__cause__ is None
    for sensitive in (api_key, api_secret, signature):
        assert sensitive not in str(captured)
        assert sensitive not in rendered
        assert sensitive not in caplog.text
    for path in (*sorted(parquet_dir.glob("*.parquet")), database_path):
        assert not credentials._contains_sensitive_material(path.read_bytes())


@pytest.mark.parametrize("failure_source", ["oserror", "websocket", "context_exit"])
@pytest.mark.asyncio
async def test_transport_failures_drop_sensitive_lower_exception_context(
    failure_source: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_key = secrets.token_urlsafe(24)
    api_secret = secrets.token_urlsafe(32)
    sentinel = secrets.token_urlsafe(28)
    credentials = BitvavoMdProCredentials(api_key=api_key, api_secret=api_secret)
    stop_event = asyncio.Event()
    if failure_source == "context_exit":
        connection = FakeConnection(_successful_messages(), on_last=stop_event.set)

        def connection_factory() -> AbstractAsyncContextManager[WebSocketConnection]:
            @asynccontextmanager
            async def context() -> AsyncIterator[WebSocketConnection]:
                yield connection
                raise OSError(sentinel)

            return context()

    else:
        lower_error: BaseException
        if failure_source == "oserror":
            lower_error = OSError(sentinel)
        else:
            lower_error = WebSocketException(sentinel)
        connection_factory = ScriptedConnectionFactory(
            [FakeConnection([_auth_ack(), _pro_ack("book", "trades"), lower_error])]
        )

    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        credentials,
        config=BitvavoMdProResearchConfig(max_reconnects=0),
        connection_factory=connection_factory,
        session_id_factory=SessionIds(),
    )
    captured = await _capture_transport_error(collector)
    rendered = "".join(
        traceback.TracebackException.from_exception(captured, capture_locals=True).format()
    )
    persisted = b"\n".join(record.payload_bytes for record in sink.records)
    assert captured.__context__ is None
    assert captured.__cause__ is None
    assert sentinel.encode() not in persisted
    assert not credentials._contains_sensitive_material(persisted)
    for sensitive in (api_key, api_secret, sentinel):
        assert sensitive not in str(captured)
        assert sensitive not in rendered
        assert sensitive not in caplog.text


@pytest.mark.asyncio
async def test_reconnect_reauthenticates_and_starts_with_empty_book_state() -> None:
    second_stop = asyncio.Event()
    first = FakeConnection(_successful_messages(snapshot_sequence=100), disconnect_when_empty=True)
    second = FakeConnection(
        _successful_messages(snapshot_sequence=500),
        on_last=second_stop.set,
    )
    factory = ScriptedConnectionFactory([first, second])
    sink = MemorySink()
    timestamps = Counter(1_788_112_000_000)
    collector = BitvavoMdProResearchCollector(
        sink,
        _credentials(),
        config=BitvavoMdProResearchConfig(reconnect_delay_seconds=0),
        connection_factory=factory,
        timestamp_ms=timestamps,
        session_id_factory=SessionIds(),
    )

    await collector.capture_for(5.0, stop_event=second_stop)

    assert factory.calls == 2
    assert len({record.session_id for record in sink.records}) == 2
    first_auth = json.loads(cast(str, first.sent[0]))
    second_auth = json.loads(cast(str, second.sent[0]))
    assert first_auth["signature"] != second_auth["signature"]
    assert first_auth["timestamp"] != second_auth["timestamp"]
    sessions = _marker_documents(sink.records, channel="session")
    assert any(item["event"] == "reconnected" for item in sessions)
    quality = _marker_documents(sink.records, channel="data_quality")
    assert [item["event"] for item in quality].count("snapshot_received") == 1
    assert [item["event"] for item in quality].count("resnapshot_received") == 1
    assert any(item["event"] == "gap_detected" for item in quality)


@pytest.mark.asyncio
async def test_receive_close_preserves_connection_closed_code_and_reason() -> None:
    stop_event = asyncio.Event()
    closed = ConnectionClosedError(Close(1000, "Ping timeout"), None)
    first = FakeConnection(
        [*_successful_messages(snapshot_sequence=100), closed],
    )
    second = FakeConnection(
        _successful_messages(snapshot_sequence=200),
        on_last=stop_event.set,
    )
    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        _credentials(),
        config=BitvavoMdProResearchConfig(reconnect_delay_seconds=0),
        connection_factory=ScriptedConnectionFactory([first, second]),
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    disconnected = [
        item
        for item in _marker_documents(sink.records, channel="session")
        if item["event"] == "disconnected"
    ]
    assert disconnected
    assert disconnected[0]["exception_class"] == "ConnectionClosedError"
    assert disconnected[0]["close_code"] == 1000
    assert disconnected[0]["close_code_rcvd"] == 1000
    assert disconnected[0]["close_reason_rcvd"] == "Ping timeout"
    assert "OSError" not in disconnected[0]["exception_class"]


@pytest.mark.asyncio
async def test_sequence_failure_is_terminal_without_transport_retry() -> None:
    messages = [
        _auth_ack(),
        _pro_ack("book", "trades"),
        _snapshot(100),
        _book_update(102),
    ]
    factory = ScriptedConnectionFactory([FakeConnection(messages)])
    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        _credentials(),
        connection_factory=factory,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoMdProDataIntegrityError) as raised:
        await collector.capture_for(5.0)
    assert raised.value.quality_event == "sequence_gap"
    assert factory.calls == 1
    assert any(
        item["event"] == "sequence_gap"
        for item in _marker_documents(sink.records, channel="data_quality")
    )


@pytest.mark.asyncio
async def test_buffered_live_update_can_join_snapshot_without_reordering_wire_records() -> None:
    stop_event = asyncio.Event()
    messages = [
        _auth_ack(),
        _pro_ack("book", "trades"),
        _book_update(101, 103),
        _snapshot(100),
    ]
    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        _credentials(),
        connection_factory=ScriptedConnectionFactory(
            [FakeConnection(messages, on_last=stop_event.set)]
        ),
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    assert [record.payload_bytes for record in inbound] == [
        messages[2].encode(),
        messages[3].encode(),
    ]
    normalized = [
        json.loads(record.payload_bytes)
        for record in sink.records
        if record.channel == "normalized_mdpro_book"
    ]
    assert [item["message_type"] for item in normalized] == ["snapshot", "update"]
    assert normalized[1]["sequence_start"] == "101"
    assert normalized[1]["sequence_end"] == "103"


@pytest.mark.asyncio
async def test_missing_post_snapshot_update_is_not_a_successful_capture() -> None:
    stop_event = asyncio.Event()
    messages = [_auth_ack(), _pro_ack("book", "trades"), _snapshot(100)]
    collector = BitvavoMdProResearchCollector(
        MemorySink(),
        _credentials(),
        connection_factory=ScriptedConnectionFactory(
            [FakeConnection(messages, on_last=stop_event.set)]
        ),
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoMdProDataIntegrityError) as raised:
        await collector.capture_for(5.0, stop_event=stop_event)
    assert raised.value.quality_event == "post_snapshot_update_missing"


@pytest.mark.asyncio
async def test_oversize_complete_market_frame_is_not_partially_persisted() -> None:
    messages = [_auth_ack(), _pro_ack("book", "trades"), _snapshot(100)]
    sink = MemorySink()
    collector = BitvavoMdProResearchCollector(
        sink,
        _credentials(),
        config=BitvavoMdProResearchConfig(max_application_payload_bytes=64),
        connection_factory=ScriptedConnectionFactory([FakeConnection(messages)]),
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoMdProDataIntegrityError) as raised:
        await collector.capture_for(5.0)
    assert raised.value.quality_event == "payload_oversize"
    assert not any(record.direction is MessageDirection.INBOUND for record in sink.records)


@pytest.mark.asyncio
async def test_transport_truncation_and_writer_failure_are_terminal() -> None:
    truncation_messages: list[str | bytes | BaseException] = [
        _auth_ack(),
        _pro_ack("book", "trades"),
        PayloadTooBig(9, 8),
    ]
    truncation_factory = ScriptedConnectionFactory([FakeConnection(truncation_messages)])
    with pytest.raises(BitvavoMdProDataIntegrityError) as truncation:
        await BitvavoMdProResearchCollector(
            MemorySink(),
            _credentials(),
            connection_factory=truncation_factory,
            session_id_factory=SessionIds(),
        ).capture_for(5.0)
    assert truncation.value.quality_event == "truncation_error"
    assert truncation_factory.calls == 1

    writer_factory = ScriptedConnectionFactory([FakeConnection([])])
    with pytest.raises(BitvavoMdProSinkError):
        await BitvavoMdProResearchCollector(
            MemorySink(fail_on_append=2),
            _credentials(),
            connection_factory=writer_factory,
            session_id_factory=SessionIds(),
        ).capture_for(5.0)
    assert writer_factory.calls == 1


@pytest.mark.asyncio
async def test_reconnect_bound_is_hard() -> None:
    factory = ScriptedConnectionFactory([FakeConnection([], disconnect_when_empty=True)])
    collector = BitvavoMdProResearchCollector(
        MemorySink(),
        _credentials(),
        config=BitvavoMdProResearchConfig(max_reconnects=0),
        connection_factory=factory,
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoMdProAuthenticationError):
        await collector.capture_for(5.0)
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_exact_parquet_roundtrip_view_isolation_and_secret_absence(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "parquet"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=4,
            max_payload_bytes=1_000_000,
            max_interval_seconds=300,
        ),
    )
    stop_event = asyncio.Event()
    messages = _successful_messages(snapshot_sequence=438_525)
    credentials = _credentials()
    collector = BitvavoMdProResearchCollector(
        writer,
        credentials,
        connection_factory=ScriptedConnectionFactory(
            [FakeConnection(messages, on_last=stop_event.set)]
        ),
        timestamp_ms=lambda: 1_788_112_345_678,
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    await writer.aclose()

    parquet_files = sorted(parquet_dir.glob("*.parquet"))
    assert 1 < len(parquet_files) < 10
    assert not tuple(parquet_dir.glob(".*.partial"))
    create_research_catalog(parquet_dir, database_path)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        stored = connection.execute(
            """
            SELECT payload_bytes, payload_sha256, channel
            FROM raw_records
            WHERE venue = 'bitvavo' AND direction = 'inbound'
            ORDER BY message_ordinal
            """
        ).fetchall()
        expected_market = [messages[2].encode(), messages[3].encode()]
        assert [bytes(row[0]) for row in stored] == expected_market
        assert [row[1] for row in stored] == [
            hashlib.sha256(payload).hexdigest() for payload in expected_market
        ]
        assert [row[2] for row in stored] == ["mdpro_book_snapshot", "mdpro_book"]
        assert "bitvavo_mdpro_spot_l2_events" in RESEARCH_VIEW_NAMES
        assert "bitvavo_mdpro_spot_trades" in RESEARCH_VIEW_NAMES
        assert "bitvavo_mdpro_spot_bbo" in RESEARCH_VIEW_NAMES
        assert connection.execute("SELECT count(*) FROM bitvavo_mdpro_spot_trades").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM bitvavo_mdpro_spot_bbo").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM bitvavo_spot_trades").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM bitvavo_spot_bbo").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM bitvavo_mdpro_spot_l2_events"
        ).fetchone() == (4,)
        assert connection.execute("SELECT count(*) FROM bitvavo_spot_l2_events").fetchone() == (0,)
        assert connection.execute(
            """
            SELECT feed_product, message_type, sequence_start, sequence_end,
                   action, price, quantity
            FROM bitvavo_mdpro_spot_l2_events
            WHERE action = 'delete'
            """
        ).fetchone() == (
            "market_data_pro",
            "update",
            "438526",
            "438527",
            "delete",
            "4999.900000000000000001",
            "0",
        )
        assert connection.execute(
            """
            SELECT count(*)
            FROM bitvavo_mdpro_spot_l2_events AS view_row
            JOIN raw_records AS source
              ON source.session_id = view_row.session_id
             AND source.message_ordinal = view_row.raw_message_ordinal
             AND source.received_utc_ns = view_row.received_utc_ns
             AND source.received_monotonic_ns = view_row.received_monotonic_ns
             AND source.payload_sha256 = view_row.payload_sha256
             AND source.channel = view_row.source_channel
             AND source.direction = 'inbound'
            """
        ).fetchone() == (4,)
        session_count = connection.execute("SELECT count(*) FROM sessions").fetchone()
        subscription_count = connection.execute(
            "SELECT count(*) FROM subscription_events"
        ).fetchone()
        quality_count = connection.execute("SELECT count(*) FROM data_quality_events").fetchone()
        assert session_count is not None and session_count[0] >= 3
        assert subscription_count is not None and subscription_count[0] >= 3
        assert quality_count is not None and quality_count[0] >= 1
    finally:
        connection.close()

    for path in (*parquet_files, database_path):
        assert not credentials._contains_sensitive_material(path.read_bytes())


@pytest.mark.asyncio
async def test_pro_trades_and_ticker_views_do_not_select_standard_channels(
    tmp_path: Path,
) -> None:
    parquet_dir = tmp_path / "parquet"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=20,
            max_payload_bytes=1_000_000,
            max_interval_seconds=300,
        ),
    )
    stop_event = asyncio.Event()
    messages = [
        _auth_ack(),
        _pro_ack("book", "trades", "ticker"),
        _snapshot(100),
        _book_update(101),
        _trade(),
        _ticker(),
    ]
    collector = BitvavoMdProResearchCollector(
        writer,
        _credentials(),
        config=BitvavoMdProResearchConfig(include_ticker=True),
        connection_factory=ScriptedConnectionFactory(
            [FakeConnection(messages, on_last=stop_event.set)]
        ),
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    await writer.aclose()
    create_research_catalog(parquet_dir, database_path)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        inbound = connection.execute(
            """
            SELECT channel
            FROM raw_records
            WHERE direction = 'inbound'
            ORDER BY message_ordinal
            """
        ).fetchall()
        assert [row[0] for row in inbound] == [
            "mdpro_book_snapshot",
            "mdpro_book",
            "mdpro_trades",
            "mdpro_ticker",
        ]
        assert connection.execute("SELECT count(*) FROM bitvavo_mdpro_spot_trades").fetchone() == (
            1,
        )
        assert connection.execute(
            "SELECT feed_product, price, quantity, taker_side FROM bitvavo_mdpro_spot_trades"
        ).fetchone() == (
            "market_data_pro",
            "9311.200000000000000001",
            "0.000963610000000001",
            "sell",
        )
        assert connection.execute("SELECT count(*) FROM bitvavo_mdpro_spot_bbo").fetchone() == (1,)
        assert connection.execute(
            "SELECT feed_product, bbo_complete FROM bitvavo_mdpro_spot_bbo"
        ).fetchone() == ("market_data_pro", True)
        assert connection.execute("SELECT count(*) FROM bitvavo_spot_trades").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM bitvavo_spot_bbo").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM bitvavo_spot_l2_events").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM bitvavo_mdpro_spot_l2_events"
        ).fetchone() == (2,)
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_depth_1000_snapshot_view_fully_materializes(tmp_path: Path) -> None:
    parquet_dir = tmp_path / "parquet"
    database_path = tmp_path / "research.duckdb"
    writer = ParquetResearchWriter(
        parquet_dir,
        rotation=ParquetRotation(
            max_records=10_000,
            max_payload_bytes=10_000_000,
            max_interval_seconds=300,
        ),
    )
    stop_event = asyncio.Event()
    bids = [[f"4999.{index:03d}", "0.010000000000000001"] for index in range(1000)]
    asks = [[f"5001.{index:03d}", "0.020000000000000001"] for index in range(1000)]
    messages = [
        _auth_ack(),
        _pro_ack("book", "trades"),
        _snapshot(438_525, bids=bids, asks=asks),
        _book_update(438_526, bids=[["4999.999", "0"]]),
    ]
    collector = BitvavoMdProResearchCollector(
        writer,
        _credentials(),
        connection_factory=ScriptedConnectionFactory(
            [FakeConnection(messages, on_last=stop_event.set)]
        ),
        timestamp_ms=lambda: 1_788_112_345_678,
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(5.0, stop_event=stop_event)
    await writer.aclose()
    create_research_catalog(parquet_dir, database_path)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT *
            FROM bitvavo_mdpro_spot_l2_events
            ORDER BY normalization_message_ordinal, wire_order
            """
        ).fetchall()
    finally:
        connection.close()

    assert len(rows) == 2001
    assert rows[0][17:] == ("bid", 0, "snapshot", "4999.000", "0.010000000000000001")
    assert rows[999][16:19] == (999, "bid", 999)
    assert rows[1000][16:19] == (1000, "ask", 0)
    assert rows[1999][16:19] == (1999, "ask", 999)
    assert rows[-1][17:] == ("bid", 0, "delete", "4999.999", "0")


@pytest.mark.asyncio
async def test_published_parts_remain_readable_after_injected_sink_crash(tmp_path: Path) -> None:
    writer = ParquetResearchWriter(
        tmp_path / "parquet",
        rotation=ParquetRotation(
            max_records=2,
            max_payload_bytes=1_000_000,
            max_interval_seconds=300,
        ),
    )

    class FailingWriter:
        def __init__(self) -> None:
            self.attempts = 0

        async def append(self, record: RawResearchRecord) -> None:
            self.attempts += 1
            if self.attempts == 10:
                raise RuntimeError("injected crash")
            await writer.append(record)

        async def aclose(self) -> None:
            await writer.aclose()

    stop_event = asyncio.Event()
    sink = FailingWriter()
    collector = BitvavoMdProResearchCollector(
        sink,
        _credentials(),
        connection_factory=ScriptedConnectionFactory(
            [FakeConnection(_successful_messages(), on_last=stop_event.set)]
        ),
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BitvavoMdProSinkError):
        await collector.capture_for(5.0, stop_event=stop_event)
    await sink.aclose()
    parts = writer.parquet_files
    assert parts
    assert not writer.orphan_partial_files
    connection = duckdb.connect()
    try:
        row = connection.execute(
            "SELECT count(*) FROM read_parquet(?)",
            [str(tmp_path / "parquet" / "*.parquet")],
        ).fetchone()
        assert row is not None and row[0] > 0
    finally:
        connection.close()


@pytest.mark.parametrize("duration", [0, 0.5, 604800.1, True, "60"])
@pytest.mark.asyncio
async def test_capture_duration_is_strictly_bounded(duration: object) -> None:
    collector = BitvavoMdProResearchCollector(
        MemorySink(),
        _credentials(),
        connection_factory=ScriptedConnectionFactory([]),
        session_id_factory=SessionIds(),
    )
    expected = TypeError if type(duration) not in (int, float) else ValueError
    with pytest.raises(expected):
        await collector.capture_for(cast(float, duration))


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        BitvavoMdProResearchConfig(authentication_window_ms=99)
    with pytest.raises(ValueError):
        BitvavoMdProResearchConfig(authentication_window_ms=60_001)
    with pytest.raises(ValueError):
        BitvavoMdProResearchConfig(max_reconnects=-1)
    with pytest.raises(ValueError):
        BitvavoMdProResearchConfig(max_buffered_book_updates=0)
    with pytest.raises(ValueError):
        BitvavoMdProResearchConfig(include_ticker=cast(bool, 1))


def test_fixture_directory_contains_no_authentication_material() -> None:
    combined = b"\n".join(path.read_bytes() for path in sorted(_FIXTURE_DIR.glob("*.json")))
    lowered = combined.lower()
    assert b"signature" not in lowered
    assert b"api_key" not in lowered
    assert b"api secret" not in lowered
    assert b"token" not in lowered


def test_retained_duration_raises_the_historical_smoke_cap() -> None:
    assert SMOKE_CAPTURE_SECONDS == 600.0
    assert MAX_CAPTURE_SECONDS == 7 * 24 * 60 * 60
    assert RETAINED_MAX_RECONNECTS == 10_080
    assert _require_bounded_duration(600.1) == 600.1
    assert _require_bounded_duration(86_400) == 86_400.0
    assert _require_bounded_duration(MAX_CAPTURE_SECONDS) == float(MAX_CAPTURE_SECONDS)
    with pytest.raises(ValueError, match="between 1 and 604800"):
        _require_bounded_duration(MAX_CAPTURE_SECONDS + 1)
    assert _config_for_duration(600.0).max_reconnects == 1
    assert _config_for_duration(600.1).max_reconnects == RETAINED_MAX_RECONNECTS


def test_mdpro_env_loader_refuses_trade_keys_without_printing_values() -> None:
    secret = "super-secret-value-must-never-be-printed"
    with pytest.raises(BitvavoMdProAuthenticationError, match="BITVAVO_API_SECRET") as error:
        refuse_protected_trade_keys({"BITVAVO_API_SECRET": secret})
    assert secret not in str(error.value)
    with pytest.raises(BitvavoMdProAuthenticationError, match="BITVAVO_MDPRO_API_KEY"):
        load_mdpro_credentials_from_env({})
    credentials = load_mdpro_credentials_from_env(
        {
            BITVAVO_MDPRO_API_KEY_ENV: "viewonly-key-value",
            BITVAVO_MDPRO_API_SECRET_ENV: "viewonly-secret-value",
        }
    )
    assert "viewonly-key-value" not in repr(credentials)
    assert "viewonly-secret-value" not in str(credentials)


@pytest.mark.asyncio
async def test_reconstructable_capture_writes_the_path_contract(tmp_path: Path) -> None:
    stop_event = asyncio.Event()

    def collector_factory(sink: RawResearchSink) -> BitvavoMdProResearchCollector:
        return BitvavoMdProResearchCollector(
            sink,
            _credentials(),
            connection_factory=ScriptedConnectionFactory(
                [FakeConnection(_successful_messages(), on_last=stop_event.set)]
            ),
            timestamp_ms=lambda: 1_788_112_345_678,
            session_id_factory=SessionIds(),
        )

    report = await run_reconstructable_capture(
        artifact_root=tmp_path,
        run_id="sample-run",
        duration_seconds=86_400,
        credentials=_credentials(),
        stop_event=stop_event,
        collector_factory=collector_factory,
    )
    paths = data1e_run_paths(tmp_path, "sample-run")
    assert report["path_contract"] == DATA1E_PATH_CONTRACT_ID
    assert report["status"] == "COMPLETED"
    assert report["twenty_four_seven"] is False
    assert paths.capture_claim_path.is_file()
    assert paths.capture_health_path.is_file()
    assert paths.database_path.is_file()
    assert list(paths.raw_dir.glob(paths.parquet_glob))
    claim = json.loads(paths.capture_claim_path.read_text(encoding="utf-8"))
    health = json.loads(paths.capture_health_path.read_text(encoding="utf-8"))
    assert claim["retained"] is True
    assert claim["signing"] is False
    assert claim["authenticated_read_only"] is True
    assert claim["feed"] == "bitvavo-mdpro-btc-eur-book-trades"
    assert claim["standard_fallback"] is False
    assert claim["channels"] == ["book", "trades"]
    assert health["status"] == "COMPLETED"
    assert health["path_contract"] == DATA1E_PATH_CONTRACT_ID
    with pytest.raises(FileExistsError, match="refuses to reuse"):
        await run_reconstructable_capture(
            artifact_root=tmp_path,
            run_id="sample-run",
            duration_seconds=60,
            credentials=_credentials(),
            collector_factory=collector_factory,
        )


def test_data1e_claim_and_health_are_create_only_and_not_twenty_four_seven() -> None:
    paths = data1e_run_paths(Path("/var/reconstructable"), "sample-run")
    claim = data1e_capture_claim(run_id="sample-run", duration_seconds=86_400, paths=paths)
    health = data1e_capture_health(
        run_id="sample-run",
        duration_seconds=86_400,
        status="COMPLETED",
        report={
            "events": 0,
            "payload_bytes": 0,
            "parquet_files": 0,
            "parquet_bytes": 0,
            "gaps": 0,
            "reconnects": 0,
            "elapsed_seconds": 86_400.0,
        },
    )
    assert claim["schema"] == "data-1e-retained-capture-claim-v1"
    assert claim["path_contract"] == DATA1E_PATH_CONTRACT_ID
    assert claim["resume_policy"] == "never resume or overwrite an existing DATA-1E run directory"
    assert claim["retained"] is True
    assert claim["feed"] == "bitvavo-mdpro-btc-eur-book-trades"
    assert claim["feed_product"] == "market_data_pro"
    assert claim["channels"] == ["book", "trades"]
    assert claim["book_depth"] == 1000
    assert claim["include_ticker"] is False
    assert claim["standard_fallback"] is False
    assert claim["websocket_url"] == BITVAVO_MDPRO_WEBSOCKET_URL
    assert health["twenty_four_seven"] is False
    assert health["signing"] is False
    assert health["elapsed_seconds"] == 86_400.0
    assert health["duration_seconds"] == 86_400.0
    assert health["feed"] == "bitvavo-mdpro-btc-eur-book-trades"
    assert health["standard_fallback"] is False
    assert health["channels"] == ["book", "trades"]
    ticker_claim = data1e_capture_claim(
        run_id="sample-run",
        duration_seconds=86_400,
        paths=paths,
        include_ticker=True,
    )
    assert ticker_claim["feed"] == "bitvavo-mdpro-btc-eur-book-trades-ticker"
    assert ticker_claim["channels"] == ["book", "trades", "ticker"]
    assert ticker_claim["include_ticker"] is True
    assert ticker_claim["standard_fallback"] is False


def test_cli_modes_are_mutually_exclusive(tmp_path: Path) -> None:
    parser = _argument_parser()
    reconstructable = parser.parse_args(
        ["--artifact-root", str(tmp_path), "--run-id", "sample-run", "--duration-seconds", "3600"]
    )
    assert _resolve_cli_mode(reconstructable) == "reconstructable"
    ad_hoc = parser.parse_args(
        [
            "--output-dir",
            str(tmp_path / "raw"),
            "--database",
            str(tmp_path / "research.duckdb"),
            "--duration-seconds",
            "60",
        ]
    )
    assert _resolve_cli_mode(ad_hoc) == "ad_hoc"
    mixed = parser.parse_args(
        [
            "--artifact-root",
            str(tmp_path),
            "--run-id",
            "sample-run",
            "--output-dir",
            str(tmp_path / "raw"),
            "--database",
            str(tmp_path / "research.duckdb"),
            "--duration-seconds",
            "60",
        ]
    )
    with pytest.raises(ValueError, match="not both"):
        _resolve_cli_mode(mixed)
