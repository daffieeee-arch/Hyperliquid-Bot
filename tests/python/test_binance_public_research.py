"""Deterministic offline DATA-1F tests for public Binance BTCUSDT research."""

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
from websockets.exceptions import ConnectionClosedError, PayloadTooBig
from websockets.frames import Close

from hyperliquid_bot.binance_public_research import (
    BINANCE_NATIVE_SYMBOL,
    BINANCE_RECONNECT_BACKOFF_CAP_SECONDS,
    BINANCE_SPOT_DEPTH_URL,
    BINANCE_SPOT_PRODUCT,
    BINANCE_SPOT_WEBSOCKET_URL,
    BINANCE_USDM_MARKET_WEBSOCKET_URL,
    BINANCE_USDM_OPEN_INTEREST_URL,
    BINANCE_USDM_PRODUCT,
    BINANCE_USDM_PUBLIC_WEBSOCKET_URL,
    BINANCE_WEBSOCKET_CLIENT_PING_INTERVAL,
    BINANCE_WEBSOCKET_CLIENT_PING_TIMEOUT,
    BINANCE_WEBSOCKET_HIGH_FREQUENCY_MAX_QUEUE,
    BINANCE_WEBSOCKET_MARKET_MAX_QUEUE,
    MAX_CAPTURE_SECONDS,
    REQUIRED_STREAM_STARVATION_SECONDS,
    RETAINED_MAX_RECONNECTS,
    SMOKE_CAPTURE_SECONDS,
    BinanceDataIntegrityError,
    BinancePublicResearchCollector,
    BinancePublicResearchConfig,
    BinanceSinkError,
    WebSocketConnection,
    _argument_parser,
    _combined_stream,
    _config_for_duration,
    _connection_factory,
    _decode_json_object,
    _normalize_open_interest,
    _normalize_spot_bbo,
    _normalize_spot_trade,
    _normalize_usdm,
    _receive_or_stop,
    _reconnect_wait_seconds,
    _require_bounded_duration,
    _resolve_cli_mode,
    _SpotBookState,
    _websocket_connect_kwargs,
    _websocket_incoming_max_queue,
    data1f_capture_claim,
    data1f_capture_health,
    run_reconstructable_capture,
)
from hyperliquid_bot.parquet_research import (
    RESEARCH_VIEW_NAMES,
    ParquetResearchWriter,
    ParquetRotation,
    create_research_catalog,
)
from hyperliquid_bot.raw_research import (
    CapturedApplicationPayload,
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
    RawResearchSink,
)
from hyperliquid_bot.reconstructable_paths import DATA1F_PATH_CONTRACT_ID, data1f_run_paths

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "binance"


def _fixture_bytes(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


def _fixture_text(name: str) -> str:
    return _fixture_bytes(name).decode("utf-8")


def _document(name: str) -> dict[str, object]:
    return _decode_json_object(_fixture_bytes(name))


def _combined_document(name: str) -> tuple[str, dict[str, object]]:
    document = _document(name)
    stream = cast(str, document["stream"])
    return stream, cast(dict[str, object], document["data"])


def _captured(name: str, *, utc_ns: int = 1, monotonic_ns: int = 2) -> CapturedApplicationPayload:
    return CapturedApplicationPayload(
        received_utc_ns=utc_ns,
        received_monotonic_ns=monotonic_ns,
        frame_type=FrameType.TEXT,
        payload_encoding=PayloadEncoding.UTF8,
        payload_bytes=_fixture_bytes(name),
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
    ) -> None:
        self._messages = deque(messages)
        self._on_last = on_last
        self._never = asyncio.Event()

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        if not self._messages:
            await self._never.wait()
            raise AssertionError("unreachable")
        message = self._messages.popleft()
        if not self._messages and self._on_last is not None:
            self._on_last()
        if isinstance(message, BaseException):
            raise message
        return message


class ImmediateConnection:
    def __init__(self, frame: str | bytes, observations: list[str]) -> None:
        self._frame = frame
        self._observations = observations

    async def recv(self) -> str | bytes:
        self._observations.append("recv_return")
        return self._frame


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
            raise AssertionError("unexpected Binance connection")
        return _fake_context(self._connections.popleft())


class SessionIds:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"binance-session-{self.count}"


class StopAfterConnections:
    def __init__(self, stop_event: asyncio.Event, count: int) -> None:
        self._stop_event = stop_event
        self._remaining = count

    def __call__(self) -> None:
        self._remaining -= 1
        if self._remaining == 0:
            self._stop_event.set()


def _local_documents(
    records: Sequence[RawResearchRecord],
    channel: str,
) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(record.payload_bytes))
        for record in records
        if record.direction is MessageDirection.LOCAL and record.channel == channel
    ]


def test_fixed_scope_uses_only_public_data_routes() -> None:
    assert BINANCE_NATIVE_SYMBOL == "BTCUSDT"
    assert BINANCE_SPOT_PRODUCT == "BTCUSDT-SPOT"
    assert BINANCE_USDM_PRODUCT == "BTCUSDT-USDS-M-PERPETUAL"
    assert BINANCE_SPOT_WEBSOCKET_URL.startswith(
        "wss://data-stream.binance.vision:443/stream?streams="
    )
    assert "btcusdt@trade" in BINANCE_SPOT_WEBSOCKET_URL
    assert "btcusdt@depth@100ms" in BINANCE_SPOT_WEBSOCKET_URL
    assert "timeUnit=MICROSECOND" in BINANCE_SPOT_WEBSOCKET_URL
    assert "/market/stream?" in BINANCE_USDM_MARKET_WEBSOCKET_URL
    assert "/public/stream?" in BINANCE_USDM_PUBLIC_WEBSOCKET_URL
    assert BINANCE_SPOT_DEPTH_URL.startswith("https://data-api.binance.vision/")
    assert BINANCE_USDM_OPEN_INTEREST_URL.endswith("symbol=BTCUSDT")


def _combined_stream_names(url: str) -> tuple[str, ...]:
    marker = "stream?streams="
    start = url.index(marker) + len(marker)
    query = url[start:]
    streams, _, _rest = query.partition("&")
    return tuple(part for part in streams.split("/") if part)


def test_usdm_combined_streams_follow_binance_2026_category_split() -> None:
    assert BINANCE_USDM_PUBLIC_WEBSOCKET_URL.startswith(
        "wss://fstream.binance.com/public/stream?streams="
    )
    assert BINANCE_USDM_MARKET_WEBSOCKET_URL.startswith(
        "wss://fstream.binance.com/market/stream?streams="
    )
    public_streams = _combined_stream_names(BINANCE_USDM_PUBLIC_WEBSOCKET_URL)
    market_streams = _combined_stream_names(BINANCE_USDM_MARKET_WEBSOCKET_URL)
    assert public_streams == ("btcusdt@bookTicker",)
    assert market_streams == (
        "btcusdt@aggTrade",
        "btcusdt@markPrice@1s",
        "btcusdt@forceOrder",
    )
    assert "btcusdt@bookTicker" not in market_streams
    assert "bookTicker" not in BINANCE_USDM_MARKET_WEBSOCKET_URL
    assert "/public/" not in BINANCE_USDM_MARKET_WEBSOCKET_URL
    assert "/market/" not in BINANCE_USDM_PUBLIC_WEBSOCKET_URL
    public_category = {"bookTicker", "depth"}
    market_category = {
        "aggTrade",
        "markPrice",
        "markPrice@1s",
        "forceOrder",
        "kline",
        "ticker",
    }

    def _channel(stream: str) -> str:
        return stream.split("@", 1)[1]

    public_channels = {_channel(stream) for stream in public_streams}
    market_channels = {_channel(stream) for stream in market_streams}
    assert public_channels <= public_category
    assert market_channels <= market_category
    assert public_channels.isdisjoint(market_category)
    assert market_channels.isdisjoint(public_category)

    signature = inspect.signature(BinancePublicResearchCollector)
    assert not ({"key", "secret", "token", "credential", "auth"} & set(signature.parameters))
    source = inspect.getsource(BinancePublicResearchCollector)
    for forbidden in ("api-key", "x-mbx-apikey", "listenkey", "place_order", "withdraw"):
        assert forbidden not in source.lower()


def test_websocket_connect_disables_client_keepalive_pings() -> None:
    public_options = _websocket_connect_kwargs(
        BinancePublicResearchConfig(),
        BINANCE_USDM_PUBLIC_WEBSOCKET_URL,
    )
    market_options = _websocket_connect_kwargs(
        BinancePublicResearchConfig(),
        BINANCE_USDM_MARKET_WEBSOCKET_URL,
    )
    spot_options = _websocket_connect_kwargs(
        BinancePublicResearchConfig(),
        BINANCE_SPOT_WEBSOCKET_URL,
    )
    assert BINANCE_WEBSOCKET_CLIENT_PING_INTERVAL is None
    assert BINANCE_WEBSOCKET_CLIENT_PING_TIMEOUT is None
    assert public_options["ping_interval"] is None
    assert public_options["ping_timeout"] is None
    assert public_options["proxy"] is None
    assert public_options["max_size"] == BinancePublicResearchConfig().max_application_payload_bytes
    assert public_options["max_queue"] == BINANCE_WEBSOCKET_HIGH_FREQUENCY_MAX_QUEUE
    assert spot_options["max_queue"] == BINANCE_WEBSOCKET_HIGH_FREQUENCY_MAX_QUEUE
    assert market_options["max_queue"] == BINANCE_WEBSOCKET_MARKET_MAX_QUEUE
    assert _websocket_incoming_max_queue(BINANCE_USDM_PUBLIC_WEBSOCKET_URL) == 1024
    assert _websocket_incoming_max_queue(BINANCE_SPOT_WEBSOCKET_URL) == 1024
    assert (
        _websocket_incoming_max_queue(BINANCE_USDM_MARKET_WEBSOCKET_URL)
        == BINANCE_WEBSOCKET_MARKET_MAX_QUEUE
    )
    with pytest.raises(ValueError, match="unknown Binance public research WebSocket URL"):
        _websocket_incoming_max_queue("wss://example.invalid/stream")


def test_reconnect_backoff_is_mild_and_stays_under_starve_bound() -> None:
    assert _reconnect_wait_seconds(0, 1) == 0.0
    assert _reconnect_wait_seconds(3.0, 1) == 3.0
    assert _reconnect_wait_seconds(3.0, 2) == 6.0
    assert _reconnect_wait_seconds(3.0, 3) == 12.0
    assert _reconnect_wait_seconds(3.0, 4) == BINANCE_RECONNECT_BACKOFF_CAP_SECONDS
    assert _reconnect_wait_seconds(3.0, 8) == BINANCE_RECONNECT_BACKOFF_CAP_SECONDS
    assert BINANCE_RECONNECT_BACKOFF_CAP_SECONDS < REQUIRED_STREAM_STARVATION_SECONDS
    with pytest.raises(ValueError, match="positive integer"):
        _reconnect_wait_seconds(3.0, 0)


@pytest.mark.asyncio
async def test_connection_factory_passes_disabled_client_keepalive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_connect(
        uri: str, **options: object
    ) -> AbstractAsyncContextManager[WebSocketConnection]:
        captured["uri"] = uri
        captured["options"] = options
        return _fake_context(FakeConnection(()))

    monkeypatch.setattr(
        "hyperliquid_bot.binance_public_research.connect",
        fake_connect,
    )
    factory = _connection_factory(
        BINANCE_USDM_PUBLIC_WEBSOCKET_URL,
        BinancePublicResearchConfig(),
    )
    async with factory():
        pass
    assert captured["uri"] == BINANCE_USDM_PUBLIC_WEBSOCKET_URL
    options = captured["options"]
    assert isinstance(options, dict)
    assert options["ping_interval"] is None
    assert options["ping_timeout"] is None
    assert options["proxy"] is None
    assert options["max_queue"] == BINANCE_WEBSOCKET_HIGH_FREQUENCY_MAX_QUEUE


def test_spot_trade_and_bbo_preserve_decimal_lexemes_and_time_contract() -> None:
    trade_stream, trade_data = _combined_document("public_spot_trade_frame.json")
    assert trade_stream == "btcusdt@trade"
    trade = _normalize_spot_trade(trade_data, 7)
    assert trade == {
        "raw_message_ordinal": 7,
        "source_channel": "spot_trade",
        "symbol": "BTCUSDT",
        "trade_id": "500000001",
        "price": "99999.120000000000000001",
        "quantity": "0.001230000000000001",
        "exchange_event_time": "1788134400123456",
        "trade_time": "1788134400123400",
        "timestamp_unit": "MICROSECONDS",
        "buyer_was_maker": False,
        "ignore_flag": True,
        "aggressor_side": "buy",
    }

    _, bbo_data = _combined_document("public_spot_book_ticker_frame.json")
    bbo = _normalize_spot_bbo(bbo_data, 8)
    assert bbo["bid_price"] == "99999.110000000000000001"
    assert bbo["ask_quantity"] == "0.800000000000000001"
    assert "exchange_event_time" not in bbo


def test_unquoted_decimal_and_wrong_combined_identity_fail_closed() -> None:
    bad_decimal = _decode_json_object(
        b'{"e":"trade","E":1,"s":"BTCUSDT","t":2,"p":1.25,"q":"0.1","T":1,"m":false,"M":true}'
    )
    with pytest.raises(BinanceDataIntegrityError, match="decimal"):
        _normalize_spot_trade(bad_decimal, 1)

    with pytest.raises(BinanceDataIntegrityError, match="combined-stream"):
        _combined_stream(
            _decode_json_object(b'{"stream":"btcusdt@trade/private","data":{}}'),
            {"btcusdt@trade": "spot_trade"},
        )


def test_duplicate_keys_and_non_standard_numbers_fail_closed() -> None:
    with pytest.raises(BinanceDataIntegrityError, match="duplicate"):
        _decode_json_object(b'{"stream":"btcusdt@trade","stream":"private","data":{}}')
    with pytest.raises(BinanceDataIntegrityError, match="non-standard"):
        _decode_json_object(b'{"stream":"btcusdt@trade","data":{"p":NaN}}')


def test_spot_book_bootstrap_and_post_snapshot_sequence() -> None:
    state = _SpotBookState(max_buffered_updates=4)
    _, update = _combined_document("public_spot_depth_frame.json")
    assert state.ingest_update(update, 10) is None
    acceptance = state.accept_snapshot(_document("public_spot_depth_snapshot.json"), 11)
    assert not acceptance.retry_required
    assert [frame["message_type"] for frame in acceptance.normalized_frames] == [
        "snapshot",
        "update",
    ]
    assert state.has_snapshot
    assert state.last_update_id == 900000011
    assert state.validated_post_snapshot_updates == 1
    update_marker = acceptance.normalized_frames[1]
    events = cast(list[dict[str, object]], update_marker["events"])
    assert events[0]["action"] == "delete"
    assert events[1]["quantity"] == "1.700000000000000001"

    next_update = _decode_json_object(
        b'{"e":"depthUpdate","E":1788134400123666,"s":"BTCUSDT",'
        b'"U":900000012,"u":900000013,"b":[],"a":[]}'
    )
    normalized = state.ingest_update(next_update, 12)
    assert normalized is not None
    assert normalized["sequence_event"] == "update"
    assert state.last_update_id == 900000013


def test_spot_book_retries_stale_snapshot_and_rejects_gap_or_out_of_order() -> None:
    state = _SpotBookState(max_buffered_updates=2)
    _, update = _combined_document("public_spot_depth_frame.json")
    state.ingest_update(update, 1)
    stale = _decode_json_object(b'{"lastUpdateId":900000008,"bids":[["1","1"]],"asks":[["2","1"]]}')
    assert state.accept_snapshot(stale, 2).retry_required
    state.accept_snapshot(_document("public_spot_depth_snapshot.json"), 3)

    equal_final = state.ingest_update(update, 4)
    assert equal_final is not None
    assert equal_final["sequence_event"] == "equal_final_reapplication"
    assert state.validated_post_snapshot_updates == 1
    gap = _decode_json_object(
        b'{"e":"depthUpdate","E":1788134400123666,"s":"BTCUSDT",'
        b'"U":900000020,"u":900000021,"b":[],"a":[]}'
    )
    with pytest.raises(BinanceDataIntegrityError, match="gap"):
        state.ingest_update(gap, 5)

    pre_snapshot = _SpotBookState(max_buffered_updates=2)
    pre_snapshot.ingest_update(update, 1)
    older = _decode_json_object(
        b'{"e":"depthUpdate","E":1788134400123000,"s":"BTCUSDT",'
        b'"U":900000001,"u":900000002,"b":[],"a":[]}'
    )
    with pytest.raises(BinanceDataIntegrityError, match="out of order"):
        pre_snapshot.ingest_update(older, 2)


def test_usdm_context_is_strict_and_labels_feed_limitations() -> None:
    cases = (
        ("public_usdm_agg_trade_frame.json", "aggregate_trade", "usdm_agg_trade"),
        ("public_usdm_book_ticker_frame.json", "book_ticker", "usdm_book_ticker"),
        ("public_usdm_mark_price_frame.json", "mark_price", "usdm_mark_price"),
        ("public_usdm_force_order_frame.json", "liquidation", "usdm_force_order"),
    )
    normalized: dict[str, dict[str, object]] = {}
    for ordinal, (name, context_type, source_channel) in enumerate(cases, start=1):
        stream, document = _combined_document(name)
        marker = _normalize_usdm(stream, document, ordinal)
        assert marker["context_type"] == context_type
        assert marker["source_channel"] == source_channel
        assert marker["symbol"] == "BTCUSDT"
        normalized[context_type] = marker

    aggregate = normalized["aggregate_trade"]
    assert aggregate["individual_trade"] is False
    assert aggregate["normal_quantity"] == "0.040000000000000001"
    assert aggregate["quantity"] == "0.045000000000000001"
    assert normalized["book_ticker"]["rpi_excluded"] is True
    assert normalized["mark_price"]["funding_rate"] == "0.000100000000000001"
    assert normalized["liquidation"]["incomplete_snapshot"] is True
    assert normalized["liquidation"]["absence_is_zero"] is False

    open_interest = _normalize_open_interest(_document("public_usdm_open_interest.json"), 5)
    assert open_interest["context_type"] == "open_interest"
    assert open_interest["open_interest"] == "10659.509000000000000001"


def test_usdm_wrong_market_type_symbol_or_quantity_fails_closed() -> None:
    _, aggregate = _combined_document("public_usdm_agg_trade_frame.json")
    wrong_type_frame = _fixture_bytes("public_usdm_agg_trade_frame.json").replace(
        b'"st":1', b'"st":2'
    )
    wrong_type = cast(dict[str, object], _decode_json_object(wrong_type_frame)["data"])
    with pytest.raises(BinanceDataIntegrityError, match="market-type"):
        _normalize_usdm("btcusdt@aggTrade", wrong_type, 1)

    wrong_symbol = dict(aggregate)
    wrong_symbol["s"] = "ETHUSDT"
    with pytest.raises(BinanceDataIntegrityError, match="symbol"):
        _normalize_usdm("btcusdt@aggTrade", wrong_symbol, 1)

    bad_quantity = dict(aggregate)
    bad_quantity["nq"] = "0.050000000000000001"
    with pytest.raises(BinanceDataIntegrityError, match="quantity"):
        _normalize_usdm("btcusdt@aggTrade", bad_quantity, 1)

    force_stream, force_order = _combined_document("public_usdm_force_order_frame.json")
    assert _normalize_usdm(force_stream, force_order, 2)["context_type"] == "liquidation"
    force_with_wrong_market_type = dict(force_order)
    force_with_wrong_market_type["st"] = wrong_type["st"]
    with pytest.raises(BinanceDataIntegrityError, match="market-type"):
        _normalize_usdm(force_stream, force_with_wrong_market_type, 2)


def test_fixture_corpus_is_synthetic_and_secret_free() -> None:
    corpus = b"\n".join(path.read_bytes() for path in sorted(_FIXTURE_DIR.iterdir()))
    lowered = corpus.lower()
    for forbidden in (
        b"x-mbx-apikey",
        b"authorization",
        b"api_secret",
        b"listenkey",
        b"private key",
        b"credential=",
    ):
        assert forbidden not in lowered
    assert b"synthetic" in lowered


def _collector(
    sink: RawResearchSink,
    stop_event: asyncio.Event,
    *,
    spot_connections: Sequence[WebSocketConnection] | None = None,
    include_force_order: bool = True,
    max_reconnects: int = 0,
) -> tuple[BinancePublicResearchCollector, ScriptedConnectionFactory]:
    stop_after = StopAfterConnections(stop_event, 3)
    spot_factory = ScriptedConnectionFactory(
        spot_connections
        or (
            FakeConnection(
                (
                    _fixture_text("public_spot_trade_frame.json"),
                    _fixture_text("public_spot_book_ticker_frame.json"),
                    _fixture_text("public_spot_depth_frame.json"),
                ),
                on_last=stop_after,
            ),
        )
    )
    market_factory = ScriptedConnectionFactory(
        (
            FakeConnection(
                tuple(
                    frame
                    for frame in (
                        _fixture_text("public_usdm_agg_trade_frame.json"),
                        _fixture_text("public_usdm_mark_price_frame.json"),
                        (
                            _fixture_text("public_usdm_force_order_frame.json")
                            if include_force_order
                            else None
                        ),
                    )
                    if frame is not None
                ),
                on_last=stop_after,
            ),
        )
    )
    public_factory = ScriptedConnectionFactory(
        (
            FakeConnection(
                (_fixture_text("public_usdm_book_ticker_frame.json"),),
                on_last=stop_after,
            ),
        )
    )

    async def spot_snapshot() -> CapturedApplicationPayload:
        return _captured("public_spot_depth_snapshot.json", utc_ns=200, monotonic_ns=201)

    async def open_interest() -> CapturedApplicationPayload:
        return _captured("public_usdm_open_interest.json", utc_ns=202, monotonic_ns=203)

    return (
        BinancePublicResearchCollector(
            sink,
            config=BinancePublicResearchConfig(
                reconnect_delay_seconds=0,
                max_reconnects=max_reconnects,
            ),
            spot_connection_factory=spot_factory,
            usdm_market_connection_factory=market_factory,
            usdm_public_connection_factory=public_factory,
            spot_depth_fetcher=spot_snapshot,
            usdm_open_interest_fetcher=open_interest,
            utc_ns=Counter(1000),
            monotonic_ns=Counter(2000),
            session_id_factory=SessionIds(),
        ),
        spot_factory,
    )


@pytest.mark.asyncio
async def test_full_offline_capture_preserves_raw_bytes_ordinals_and_markers() -> None:
    sink = MemorySink()
    stop_event = asyncio.Event()
    collector, _ = _collector(sink, stop_event)
    await collector.capture_for(1, stop_event=stop_event)

    ordinals = [record.message_ordinal for record in sink.records]
    assert ordinals == list(range(1, len(ordinals) + 1))
    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    expected = {
        _fixture_bytes(name)
        for name in (
            "public_spot_trade_frame.json",
            "public_spot_book_ticker_frame.json",
            "public_spot_depth_frame.json",
            "public_spot_depth_snapshot.json",
            "public_usdm_agg_trade_frame.json",
            "public_usdm_mark_price_frame.json",
            "public_usdm_force_order_frame.json",
            "public_usdm_book_ticker_frame.json",
            "public_usdm_open_interest.json",
        )
    }
    assert {record.payload_bytes for record in inbound} == expected
    assert all(
        record.payload_sha256 == hashlib.sha256(record.payload_bytes).hexdigest()
        for record in inbound
    )
    subscription_events = _local_documents(sink.records, "subscription")
    assert any(marker["event"] == "subscription_requested" for marker in subscription_events)
    assert any(marker["event"] == "subscription_observed" for marker in subscription_events)
    assert not any("ack" in str(marker["event"]).lower() for marker in subscription_events)
    assert not _local_documents(sink.records, "data_quality")


@pytest.mark.asyncio
async def test_sparse_force_order_is_optional_and_silence_is_not_zero() -> None:
    sink = MemorySink()
    stop_event = asyncio.Event()
    collector, _ = _collector(sink, stop_event, include_force_order=False)
    await collector.capture_for(1, stop_event=stop_event)

    assert not any(record.channel == "usdm_force_order" for record in sink.records)
    assert not _local_documents(sink.records, "data_quality")


def _hanging_collector(
    sink: RawResearchSink,
    *,
    spot_messages: Sequence[str | bytes | BaseException] = (),
    market_messages: Sequence[str | bytes | BaseException] = (),
    public_messages: Sequence[str | bytes | BaseException] = (),
    starvation_seconds: float = REQUIRED_STREAM_STARVATION_SECONDS,
    max_reconnects: int = 0,
) -> BinancePublicResearchCollector:
    async def spot_snapshot() -> CapturedApplicationPayload:
        return _captured("public_spot_depth_snapshot.json", utc_ns=200, monotonic_ns=201)

    async def open_interest() -> CapturedApplicationPayload:
        return _captured("public_usdm_open_interest.json", utc_ns=202, monotonic_ns=203)

    return BinancePublicResearchCollector(
        sink,
        config=BinancePublicResearchConfig(
            reconnect_delay_seconds=0,
            max_reconnects=max_reconnects,
            required_stream_starvation_seconds=starvation_seconds,
        ),
        spot_connection_factory=ScriptedConnectionFactory((FakeConnection(spot_messages),)),
        usdm_market_connection_factory=ScriptedConnectionFactory(
            (FakeConnection(market_messages),)
        ),
        usdm_public_connection_factory=ScriptedConnectionFactory(
            (FakeConnection(public_messages),)
        ),
        spot_depth_fetcher=spot_snapshot,
        usdm_open_interest_fetcher=open_interest,
        utc_ns=Counter(1000),
        monotonic_ns=Counter(2000),
        session_id_factory=SessionIds(),
    )


@pytest.mark.asyncio
async def test_empty_required_streams_fail_closed_and_are_not_a_healthy_retain() -> None:
    sink = MemorySink()
    collector = _hanging_collector(sink)
    with pytest.raises(BinanceDataIntegrityError, match="not observed") as raised:
        await collector.capture_for(1)
    assert raised.value.quality_event == "liveness_error"
    quality = _local_documents(sink.records, "data_quality")
    assert any(
        marker["event"] == "liveness_error" and marker["reason"] == "required_streams_unobserved"
        for marker in quality
    )
    assert not any(marker["event"] == "gap" for marker in quality)


@pytest.mark.asyncio
async def test_empty_required_stream_operator_stop_writes_failed_health(
    tmp_path: Path,
) -> None:
    stop_event = asyncio.Event()

    def collector_factory(sink: RawResearchSink) -> BinancePublicResearchCollector:
        return _hanging_collector(sink)

    async def request_stop() -> None:
        await asyncio.sleep(0.05)
        stop_event.set()

    stopper = asyncio.create_task(request_stop())
    with pytest.raises(BinanceDataIntegrityError, match="not observed"):
        await run_reconstructable_capture(
            artifact_root=tmp_path,
            run_id="empty-required",
            duration_seconds=86_400,
            stop_event=stop_event,
            operator_stop=lambda: True,
            collector_factory=collector_factory,
        )
    stopper.cancel()
    paths = data1f_run_paths(tmp_path, "empty-required")
    health = json.loads(paths.capture_health_path.read_text(encoding="utf-8"))
    assert health["status"] == "FAILED"
    assert health["status"] != "OPERATOR_STOP"
    assert int(health["integrity_events"]) >= 1
    assert health["gaps"] == 0


@pytest.mark.asyncio
async def test_mid_run_required_stream_starvation_fails_closed_when_reconnects_exhausted() -> None:
    sink = MemorySink()
    collector = _hanging_collector(
        sink,
        spot_messages=(
            _fixture_text("public_spot_trade_frame.json"),
            _fixture_text("public_spot_book_ticker_frame.json"),
            _fixture_text("public_spot_depth_frame.json"),
        ),
        market_messages=(
            _fixture_text("public_usdm_agg_trade_frame.json"),
            _fixture_text("public_usdm_mark_price_frame.json"),
        ),
        public_messages=(_fixture_text("public_usdm_book_ticker_frame.json"),),
        starvation_seconds=0.05,
        max_reconnects=0,
    )
    with pytest.raises(BinanceDataIntegrityError, match="starved") as raised:
        await collector.capture_for(10)
    assert raised.value.quality_event == "liveness_error"
    quality = _local_documents(sink.records, "data_quality")
    assert any(
        marker["event"] == "gap" and marker["reason"] == "required_stream_starved"
        for marker in quality
    )
    starved = [
        marker
        for marker in quality
        if marker["event"] == "liveness_error" and marker["reason"] == "required_stream_starved"
    ]
    assert starved
    assert starved[0]["threshold_seconds"] == 0.05
    assert "stream" in starved[0]
    assert float(cast(float, starved[0]["silence_seconds"])) >= 0.05


@pytest.mark.asyncio
async def test_mid_run_required_stream_starvation_force_reconnects_profile() -> None:
    stop_event = asyncio.Event()
    public_first = FakeConnection((_fixture_text("public_usdm_book_ticker_frame.json"),))
    public_second = FakeConnection(
        (_fixture_text("public_usdm_book_ticker_frame.json"),),
        on_last=stop_event.set,
    )

    class RepeatingRequiredConnection:
        def __init__(self, frames: Sequence[str]) -> None:
            self._cycle = tuple(frames)
            self._index = 0

        async def recv(self) -> str | bytes:
            await asyncio.sleep(0.01)
            if stop_event.is_set():
                await asyncio.Event().wait()
            frame = self._cycle[self._index]
            self._index = (self._index + 1) % len(self._cycle)
            return frame

    async def spot_snapshot() -> CapturedApplicationPayload:
        return _captured("public_spot_depth_snapshot.json", utc_ns=200, monotonic_ns=201)

    async def open_interest() -> CapturedApplicationPayload:
        return _captured("public_usdm_open_interest.json", utc_ns=202, monotonic_ns=203)

    sink = MemorySink()
    collector = BinancePublicResearchCollector(
        sink,
        config=BinancePublicResearchConfig(
            reconnect_delay_seconds=0,
            max_reconnects=2,
            required_stream_starvation_seconds=0.05,
        ),
        spot_connection_factory=ScriptedConnectionFactory(
            (
                RepeatingRequiredConnection(
                    (
                        _fixture_text("public_spot_trade_frame.json"),
                        _fixture_text("public_spot_book_ticker_frame.json"),
                        _fixture_text("public_spot_depth_frame.json"),
                    )
                ),
            )
        ),
        usdm_market_connection_factory=ScriptedConnectionFactory(
            (
                RepeatingRequiredConnection(
                    (
                        _fixture_text("public_usdm_agg_trade_frame.json"),
                        _fixture_text("public_usdm_mark_price_frame.json"),
                    )
                ),
            )
        ),
        usdm_public_connection_factory=ScriptedConnectionFactory((public_first, public_second)),
        spot_depth_fetcher=spot_snapshot,
        usdm_open_interest_fetcher=open_interest,
        utc_ns=Counter(1000),
        monotonic_ns=Counter(2000),
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(10, stop_event=stop_event)
    quality = _local_documents(sink.records, "data_quality")
    assert any(
        marker["event"] == "gap" and marker["reason"] == "required_stream_starved"
        for marker in quality
    )
    assert not any(marker["event"] == "liveness_error" for marker in quality)
    sessions = _local_documents(sink.records, "session")
    assert any(
        marker["event"] == "reconnect" and marker.get("reason") == "required_stream_starved"
        for marker in sessions
    )


@pytest.mark.asyncio
async def test_force_order_silence_is_not_required_stream_starvation() -> None:
    stop_event = asyncio.Event()
    market_frames = (
        _fixture_text("public_usdm_agg_trade_frame.json"),
        _fixture_text("public_usdm_mark_price_frame.json"),
    )

    class RepeatingRequiredConnection:
        def __init__(self, frames: Sequence[str]) -> None:
            self._cycle = tuple(frames)
            self._index = 0

        async def recv(self) -> str | bytes:
            await asyncio.sleep(0.02)
            if stop_event.is_set():
                await asyncio.Event().wait()
            frame = self._cycle[self._index]
            self._index = (self._index + 1) % len(self._cycle)
            return frame

    async def spot_snapshot() -> CapturedApplicationPayload:
        return _captured("public_spot_depth_snapshot.json", utc_ns=200, monotonic_ns=201)

    async def open_interest() -> CapturedApplicationPayload:
        return _captured("public_usdm_open_interest.json", utc_ns=202, monotonic_ns=203)

    sink = MemorySink()
    collector = BinancePublicResearchCollector(
        sink,
        config=BinancePublicResearchConfig(
            reconnect_delay_seconds=0,
            max_reconnects=0,
            required_stream_starvation_seconds=0.2,
        ),
        spot_connection_factory=ScriptedConnectionFactory(
            (
                RepeatingRequiredConnection(
                    (
                        _fixture_text("public_spot_trade_frame.json"),
                        _fixture_text("public_spot_book_ticker_frame.json"),
                        _fixture_text("public_spot_depth_frame.json"),
                    )
                ),
            )
        ),
        usdm_market_connection_factory=ScriptedConnectionFactory(
            (RepeatingRequiredConnection(market_frames),)
        ),
        usdm_public_connection_factory=ScriptedConnectionFactory(
            (RepeatingRequiredConnection((_fixture_text("public_usdm_book_ticker_frame.json"),)),)
        ),
        spot_depth_fetcher=spot_snapshot,
        usdm_open_interest_fetcher=open_interest,
        utc_ns=Counter(1000),
        monotonic_ns=Counter(2000),
        session_id_factory=SessionIds(),
    )

    async def request_stop() -> None:
        await asyncio.sleep(0.45)
        stop_event.set()

    stopper = asyncio.create_task(request_stop())
    try:
        await collector.capture_for(10, stop_event=stop_event)
    finally:
        stopper.cancel()
    assert not any(record.channel == "usdm_force_order" for record in sink.records)
    assert not any(
        marker["event"] == "liveness_error"
        for marker in _local_documents(sink.records, "data_quality")
    )


@pytest.mark.asyncio
async def test_receive_boundary_clocks_immediately_and_does_not_drop_completed_frame() -> None:
    observations: list[str] = []

    def utc_ns() -> int:
        observations.append("utc_clock")
        return 11

    def monotonic_ns() -> int:
        observations.append("monotonic_clock")
        return 12

    stop_event = asyncio.Event()
    stop_event.set()
    captured = await _receive_or_stop(
        ImmediateConnection("exact-frame", observations),
        stop_event,
        utc_ns=utc_ns,
        monotonic_ns=monotonic_ns,
    )
    assert captured is not None
    assert captured.payload_bytes == b"exact-frame"
    assert observations == ["recv_return", "utc_clock", "monotonic_clock"]


@pytest.mark.asyncio
async def test_parquet_roundtrip_and_all_binance_views_are_source_linked(tmp_path: Path) -> None:
    sink = MemorySink()
    stop_event = asyncio.Event()
    collector, _ = _collector(sink, stop_event)
    await collector.capture_for(1, stop_event=stop_event)

    output_dir = tmp_path / "raw"
    writer = ParquetResearchWriter(
        output_dir,
        rotation=ParquetRotation(max_records=5, max_payload_bytes=4096, max_interval_seconds=60),
    )
    for record in sink.records:
        await writer.append(record)
    await writer.aclose()
    assert 1 < len(writer.parquet_files) < len(sink.records)
    assert not writer.orphan_partial_files

    database = tmp_path / "research.duckdb"
    create_research_catalog(output_dir, database)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        views = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.views"
            ).fetchall()
        }
        expected_views = {
            "binance_spot_trades",
            "binance_spot_bbo",
            "binance_spot_l2_events",
            "binance_usdm_context",
        }
        assert expected_views <= views
        assert expected_views <= set(RESEARCH_VIEW_NAMES)
        assert connection.execute(
            "SELECT price, quantity, timestamp_unit FROM binance_spot_trades"
        ).fetchone() == (
            "99999.120000000000000001",
            "0.001230000000000001",
            "MICROSECONDS",
        )
        assert connection.execute(
            "SELECT bid_price, ask_quantity FROM binance_spot_bbo"
        ).fetchone() == (
            "99999.110000000000000001",
            "0.800000000000000001",
        )
        l2_counts = connection.execute(
            "SELECT count(*), count(DISTINCT payload_sha256) FROM binance_spot_l2_events"
        ).fetchone()
        assert l2_counts is not None
        assert l2_counts[0] == 7
        contexts = dict(
            connection.execute(
                "SELECT context_type, count(*) FROM binance_usdm_context GROUP BY ALL"
            ).fetchall()
        )
        assert contexts == {
            "aggregate_trade": 1,
            "book_ticker": 1,
            "liquidation": 1,
            "mark_price": 1,
            "open_interest": 1,
        }
        assert connection.execute(
            "SELECT quantity, normal_quantity FROM binance_usdm_context "
            "WHERE context_type = 'aggregate_trade'"
        ).fetchone() == (
            "0.045000000000000001",
            "0.040000000000000001",
        )
        stored = connection.execute(
            "SELECT payload_bytes, payload_sha256 FROM raw_records WHERE direction = 'inbound'"
        ).fetchall()
        assert all(
            hashlib.sha256(bytes(payload)).hexdigest() == digest for payload, digest in stored
        )
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_transport_reconnect_uses_fresh_session_and_gap_marker() -> None:
    sink = MemorySink()
    stop_event = asyncio.Event()
    stop_after = StopAfterConnections(stop_event, 3)
    first = FakeConnection((ConnectionError("synthetic disconnect"),))
    second = FakeConnection(
        (
            _fixture_text("public_spot_trade_frame.json"),
            _fixture_text("public_spot_book_ticker_frame.json"),
            _fixture_text("public_spot_depth_frame.json"),
        ),
        on_last=stop_after,
    )
    collector, spot_factory = _collector(
        sink,
        stop_event,
        spot_connections=(first, second),
        max_reconnects=1,
    )
    # Replace the helper's callbacks for the unaffected sockets with this test's coordinator.
    collector._usdm_market_connection_factory = ScriptedConnectionFactory(
        (
            FakeConnection(
                (
                    _fixture_text("public_usdm_agg_trade_frame.json"),
                    _fixture_text("public_usdm_mark_price_frame.json"),
                ),
                on_last=stop_after,
            ),
        )
    )
    collector._usdm_public_connection_factory = ScriptedConnectionFactory(
        (
            FakeConnection(
                (_fixture_text("public_usdm_book_ticker_frame.json"),), on_last=stop_after
            ),
        )
    )
    await collector.capture_for(1, stop_event=stop_event)
    assert spot_factory.calls == 2
    quality = _local_documents(sink.records, "data_quality")
    assert any(
        marker["event"] == "gap"
        and marker.get("transport_profile") == "spot"
        and marker.get("exception_class") == "ConnectionError"
        for marker in quality
    )
    sessions = _local_documents(sink.records, "session")
    assert any(
        marker["event"] == "disconnect"
        and marker.get("transport_profile") == "spot"
        and marker.get("exception_class") == "ConnectionError"
        for marker in sessions
    )
    spot_starts = [
        marker
        for marker in sessions
        if marker["event"] == "session_start" and marker["transport_profile"] == "spot"
    ]
    assert len(spot_starts) == 2
    snapshot_sources = [
        record
        for record in sink.records
        if record.channel == "spot_depth_snapshot" and record.direction is MessageDirection.INBOUND
    ]
    assert len(snapshot_sources) == 1


@pytest.mark.asyncio
async def test_client_keepalive_1011_is_recorded_as_transport_gap() -> None:
    sink = MemorySink()
    stop_event = asyncio.Event()
    stop_after = StopAfterConnections(stop_event, 3)
    keepalive_timeout = ConnectionClosedError(
        None,
        Close(1011, "keepalive ping timeout"),
    )
    first = FakeConnection((keepalive_timeout,))
    second = FakeConnection(
        (_fixture_text("public_usdm_book_ticker_frame.json"),),
        on_last=stop_after,
    )
    collector, _spot_factory = _collector(sink, stop_event, max_reconnects=1)
    collector._usdm_public_connection_factory = ScriptedConnectionFactory((first, second))
    collector._usdm_market_connection_factory = ScriptedConnectionFactory(
        (
            FakeConnection(
                (
                    _fixture_text("public_usdm_agg_trade_frame.json"),
                    _fixture_text("public_usdm_mark_price_frame.json"),
                ),
                on_last=stop_after,
            ),
        )
    )
    await collector.capture_for(1, stop_event=stop_event)
    quality = _local_documents(sink.records, "data_quality")
    assert any(
        marker["event"] == "gap"
        and marker.get("transport_profile") == "usdm_public"
        and marker.get("exception_class") == "ConnectionClosedError"
        and marker.get("close_code") == 1011
        for marker in quality
    )
    sessions = _local_documents(sink.records, "session")
    assert any(
        marker["event"] == "disconnect"
        and marker.get("transport_profile") == "usdm_public"
        and marker.get("close_code") == 1011
        for marker in sessions
    )


@pytest.mark.asyncio
async def test_spot_1008_records_sanitized_close_reason_without_host_change() -> None:
    sink = MemorySink()
    stop_event = asyncio.Event()
    stop_after = StopAfterConnections(stop_event, 3)
    policy = ConnectionClosedError(
        Close(1008, "Too many requests"),
        None,
    )
    first = FakeConnection((policy,))
    second = FakeConnection(
        (
            _fixture_text("public_spot_trade_frame.json"),
            _fixture_text("public_spot_book_ticker_frame.json"),
            _fixture_text("public_spot_depth_frame.json"),
        ),
        on_last=stop_after,
    )
    collector, spot_factory = _collector(
        sink,
        stop_event,
        spot_connections=(first, second),
        max_reconnects=1,
    )
    await collector.capture_for(1, stop_event=stop_event)
    sessions = _local_documents(sink.records, "session")
    assert any(
        marker["event"] == "disconnect"
        and marker.get("transport_profile") == "spot"
        and marker.get("exception_class") == "ConnectionClosedError"
        and marker.get("close_code") == 1008
        and marker.get("close_code_rcvd") == 1008
        and marker.get("close_reason_rcvd") == "Too many requests"
        for marker in sessions
    )
    assert BINANCE_SPOT_WEBSOCKET_URL.startswith("wss://data-stream.binance.vision:443/stream")
    assert "/public/" not in BINANCE_SPOT_WEBSOCKET_URL
    assert "/market/" not in BINANCE_SPOT_WEBSOCKET_URL
    assert "btcusdt@depth@100ms" in BINANCE_SPOT_WEBSOCKET_URL
    assert spot_factory.calls == 2


@pytest.mark.asyncio
async def test_documented_spot_server_shutdown_is_exact_raw_and_reconnects() -> None:
    sink = MemorySink()
    stop_event = asyncio.Event()
    stop_after = StopAfterConnections(stop_event, 3)
    first = FakeConnection((_fixture_text("public_spot_server_shutdown_frame.json"),))
    second = FakeConnection(
        (
            _fixture_text("public_spot_trade_frame.json"),
            _fixture_text("public_spot_book_ticker_frame.json"),
            _fixture_text("public_spot_depth_frame.json"),
        ),
        on_last=stop_after,
    )
    collector, spot_factory = _collector(
        sink,
        stop_event,
        spot_connections=(first, second),
        max_reconnects=1,
    )
    collector._usdm_market_connection_factory = ScriptedConnectionFactory(
        (
            FakeConnection(
                (
                    _fixture_text("public_usdm_agg_trade_frame.json"),
                    _fixture_text("public_usdm_mark_price_frame.json"),
                ),
                on_last=stop_after,
            ),
        )
    )
    collector._usdm_public_connection_factory = ScriptedConnectionFactory(
        (
            FakeConnection(
                (_fixture_text("public_usdm_book_ticker_frame.json"),), on_last=stop_after
            ),
        )
    )
    await collector.capture_for(1, stop_event=stop_event)
    assert spot_factory.calls == 2
    shutdown = [record for record in sink.records if record.channel == "server_shutdown"]
    assert len(shutdown) == 1
    assert shutdown[0].payload_bytes == _fixture_bytes("public_spot_server_shutdown_frame.json")
    quality = _local_documents(sink.records, "data_quality")
    assert any(
        marker["event"] == "gap" and marker["reason"] == "venue_server_shutdown"
        for marker in quality
    )


@pytest.mark.asyncio
async def test_invalid_schema_is_stored_then_fails_without_public_fallback() -> None:
    sink = MemorySink()
    stop_event = asyncio.Event()
    bad_spot = FakeConnection((b'{"stream":"btcusdt@trade","data":',))
    collector, spot_factory = _collector(
        sink,
        stop_event,
        spot_connections=(bad_spot,),
        max_reconnects=1,
    )
    with pytest.raises(BinanceDataIntegrityError):
        await collector.capture_for(1, stop_event=stop_event)
    assert spot_factory.calls == 1
    raw = [record for record in sink.records if record.channel == "unrouted"]
    assert len(raw) == 1
    assert raw[0].payload_bytes == b'{"stream":"btcusdt@trade","data":'
    assert any(
        marker["event"] == "schema_error"
        for marker in _local_documents(sink.records, "data_quality")
    )


@pytest.mark.asyncio
async def test_malformed_snapshot_has_one_marker_at_the_snapshot_raw_ordinal() -> None:
    sink = MemorySink()

    async def malformed_snapshot() -> CapturedApplicationPayload:
        return CapturedApplicationPayload(
            received_utc_ns=51,
            received_monotonic_ns=52,
            frame_type=FrameType.TEXT,
            payload_encoding=PayloadEncoding.UTF8,
            payload_bytes=b'{"lastUpdateId":',
        )

    collector = BinancePublicResearchCollector(
        sink,
        spot_depth_fetcher=malformed_snapshot,
        utc_ns=Counter(100),
        monotonic_ns=Counter(200),
        session_id_factory=SessionIds(),
    )
    ws_raw_ordinal = await collector._raw(
        BINANCE_SPOT_PRODUCT,
        "spot_depth",
        "snapshot-test-session",
        _captured("public_spot_depth_frame.json"),
    )
    _, update = _combined_document("public_spot_depth_frame.json")
    with pytest.raises(BinanceDataIntegrityError, match="JSON"):
        await collector._handle_spot(
            "btcusdt@depth@100ms",
            update,
            ws_raw_ordinal,
            "snapshot-test-session",
            _SpotBookState(),
        )
    markers = _local_documents(sink.records, "data_quality")
    assert len(markers) == 1
    snapshot_raw = next(
        record for record in sink.records if record.channel == "spot_depth_snapshot"
    )
    assert markers[0]["raw_message_ordinal"] == snapshot_raw.message_ordinal


@pytest.mark.asyncio
async def test_oversize_open_interest_has_terminal_quality_marker_without_false_raw_link() -> None:
    sink = MemorySink()

    async def oversize_open_interest() -> CapturedApplicationPayload:
        return CapturedApplicationPayload(
            received_utc_ns=61,
            received_monotonic_ns=62,
            frame_type=FrameType.TEXT,
            payload_encoding=PayloadEncoding.UTF8,
            payload_bytes=b"x" * 65,
        )

    collector = BinancePublicResearchCollector(
        sink,
        config=BinancePublicResearchConfig(max_application_payload_bytes=64),
        usdm_open_interest_fetcher=oversize_open_interest,
        utc_ns=Counter(300),
        monotonic_ns=Counter(400),
        session_id_factory=SessionIds(),
    )
    with pytest.raises(BinanceDataIntegrityError, match="exceeded"):
        await collector._capture_open_interest(asyncio.Event())
    markers = _local_documents(sink.records, "data_quality")
    assert len(markers) == 1
    assert markers[0] == {
        "event": "truncation_error",
        "reason": "open_interest_transport_truncation",
    }
    assert not any(record.channel == "usdm_open_interest" for record in sink.records)


@pytest.mark.asyncio
async def test_transport_truncation_is_terminal_and_never_retried() -> None:
    sink = MemorySink()
    stop_event = asyncio.Event()
    truncated = FakeConnection((PayloadTooBig(65_537, 65_536),))
    collector, spot_factory = _collector(
        sink,
        stop_event,
        spot_connections=(truncated,),
        max_reconnects=1,
    )
    with pytest.raises(BinanceDataIntegrityError, match="transport bound"):
        await collector.capture_for(1, stop_event=stop_event)
    assert spot_factory.calls == 1
    assert any(
        marker["event"] == "truncation_error"
        for marker in _local_documents(sink.records, "data_quality")
    )


@pytest.mark.asyncio
async def test_sink_failure_is_context_free_and_never_reconnects() -> None:
    sink = MemorySink(fail_on_append=2)
    stop_event = asyncio.Event()
    collector, spot_factory = _collector(sink, stop_event, max_reconnects=1)
    with pytest.raises(BinanceSinkError, match="append failed") as raised:
        await collector.capture_for(1, stop_event=stop_event)
    assert "injected" not in str(raised.value)
    assert spot_factory.calls <= 1


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_application_payload_bytes", 0),
        ("max_buffered_spot_depth_updates", 0),
        ("max_spot_snapshot_requests", 0),
        ("max_reconnects", -1),
        ("http_timeout_seconds", 0),
        ("reconnect_delay_seconds", float("nan")),
        ("reconnect_delay_seconds", float("inf")),
        ("http_timeout_seconds", float("nan")),
        ("http_timeout_seconds", float("inf")),
        ("required_stream_starvation_seconds", 0),
        ("required_stream_starvation_seconds", float("nan")),
        ("required_stream_starvation_seconds", float("inf")),
    ),
)
def test_config_rejects_unbounded_or_invalid_controls(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        BinancePublicResearchConfig(**{field: value})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_capture_duration_is_bounded() -> None:
    sink = MemorySink()
    collector, _ = _collector(sink, asyncio.Event())
    for duration in (0, MAX_CAPTURE_SECONDS + 0.1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="between 1 and 604800"):
            await collector.capture_for(cast(float, duration))
    with pytest.raises(TypeError, match="built-in number"):
        await collector.capture_for(cast(float, True))


def test_retained_duration_raises_the_historical_smoke_cap() -> None:
    assert SMOKE_CAPTURE_SECONDS == 180.0
    assert REQUIRED_STREAM_STARVATION_SECONDS == 60.0
    assert MAX_CAPTURE_SECONDS == 7 * 24 * 60 * 60
    assert RETAINED_MAX_RECONNECTS == 10_080
    assert _require_bounded_duration(180.1) == 180.1
    assert _require_bounded_duration(86_400) == 86_400.0
    assert _require_bounded_duration(MAX_CAPTURE_SECONDS) == float(MAX_CAPTURE_SECONDS)
    with pytest.raises(ValueError, match="between 1 and 604800"):
        _require_bounded_duration(MAX_CAPTURE_SECONDS + 1)
    assert _config_for_duration(180.0).max_reconnects == 1
    assert _config_for_duration(180.1).max_reconnects == RETAINED_MAX_RECONNECTS


@pytest.mark.asyncio
async def test_reconstructable_capture_writes_the_path_contract(tmp_path: Path) -> None:
    stop_event = asyncio.Event()

    def collector_factory(sink: RawResearchSink) -> BinancePublicResearchCollector:
        collector, _ = _collector(sink, stop_event)
        return collector

    report = await run_reconstructable_capture(
        artifact_root=tmp_path,
        run_id="sample-run",
        duration_seconds=86_400,
        stop_event=stop_event,
        collector_factory=collector_factory,
    )
    paths = data1f_run_paths(tmp_path, "sample-run")
    assert report["path_contract"] == DATA1F_PATH_CONTRACT_ID
    assert report["status"] == "COMPLETED"
    assert report["twenty_four_seven"] is False
    assert paths.capture_claim_path.is_file()
    assert paths.capture_health_path.is_file()
    assert paths.database_path.is_file()
    assert list(paths.raw_dir.glob(paths.parquet_glob))
    claim = json.loads(paths.capture_claim_path.read_text(encoding="utf-8"))
    health = json.loads(paths.capture_health_path.read_text(encoding="utf-8"))
    assert claim["retained"] is True
    assert claim["duration_seconds"] == 86_400.0
    assert claim["twenty_four_seven"] is False
    assert claim["signing"] is False
    assert claim["credentialless"] is True
    assert health["status"] == "COMPLETED"
    assert health["path_contract"] == DATA1F_PATH_CONTRACT_ID
    assert health["duration_seconds"] == 86_400.0
    assert float(health["elapsed_seconds"]) < float(health["duration_seconds"])
    assert "independent_websocket_profiles" in claim
    assert claim["independent_websocket_profiles"] == ["spot", "usdm_market", "usdm_public"]
    assert claim["usdm_public_websocket_url"] == BINANCE_USDM_PUBLIC_WEBSOCKET_URL
    assert claim["required_stream_starvation_seconds"] == REQUIRED_STREAM_STARVATION_SECONDS
    required_streams = claim["required_streams"]
    optional_streams = claim["optional_streams"]
    assert isinstance(required_streams, dict)
    assert isinstance(optional_streams, dict)
    assert required_streams["spot"] == [
        "btcusdt@bookTicker",
        "btcusdt@depth@100ms",
        "btcusdt@trade",
    ]
    assert required_streams["usdm_market"] == [
        "btcusdt@aggTrade",
        "btcusdt@markPrice@1s",
    ]
    assert optional_streams["usdm_market"] == ["btcusdt@forceOrder"]
    log_path = paths.run_dir / "capture-sample-run.log"
    assert log_path.is_file()
    assert "data1f start" in log_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="refuses to reuse"):
        await run_reconstructable_capture(
            artifact_root=tmp_path,
            run_id="sample-run",
            duration_seconds=60,
            collector_factory=collector_factory,
        )


def test_data1f_claim_and_health_are_create_only_and_not_twenty_four_seven() -> None:
    paths = data1f_run_paths(Path("/var/reconstructable"), "sample-run")
    claim = data1f_capture_claim(run_id="sample-run", duration_seconds=86_400, paths=paths)
    health = data1f_capture_health(
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
            "transport_profiles": [
                {"transport_profile": "spot", "gaps": 0, "reconnects": 0},
                {"transport_profile": "usdm_market", "gaps": 0, "reconnects": 0},
                {"transport_profile": "usdm_public", "gaps": 0, "reconnects": 0},
            ],
            "integrity_events": 0,
        },
    )
    assert claim["schema"] == "data-1f-retained-capture-claim-v1"
    assert claim["path_contract"] == DATA1F_PATH_CONTRACT_ID
    assert claim["resume_policy"] == "never resume or overwrite an existing DATA-1F run directory"
    assert claim["retained"] is True
    assert health["twenty_four_seven"] is False
    assert health["credentialless"] is True
    assert health["elapsed_seconds"] == 86_400.0
    assert health["duration_seconds"] == 86_400.0
    assert health["required_stream_starvation_seconds"] == REQUIRED_STREAM_STARVATION_SECONDS
    optional_streams = health["optional_streams"]
    assert isinstance(optional_streams, dict)
    assert optional_streams["usdm_market"] == ["btcusdt@forceOrder"]
    limitations = health["limitations"]
    assert isinstance(limitations, list)
    assert any("liveness_error" in str(item) for item in limitations)
    profiles = health["transport_profiles"]
    assert isinstance(profiles, list)
    assert profiles[0]["transport_profile"] == "spot"


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
