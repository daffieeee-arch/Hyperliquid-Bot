"""Deterministic offline tests for bounded public Deribit BTC research capture."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import duckdb
import pytest

import hyperliquid_bot.deribit_public_research as deribit_module
from hyperliquid_bot.deribit_public_research import (
    DERIBIT_INDEX_NAME,
    DERIBIT_PERPETUAL,
    DERIBIT_PUBLIC_WEBSOCKET_URL,
    DERIBIT_RESEARCH_PRODUCT,
    DeribitDataIntegrityError,
    DeribitPublicResearchCollector,
    DeribitPublicResearchConfig,
    DeribitSinkError,
    DeribitTransportError,
    DeribitTransportTruncation,
    WebSocketConnection,
    _BookState,
    _classify_document,
    _decode_json_object,
    _normalize_notification,
    _TradeState,
    select_research_instruments,
)
from hyperliquid_bot.parquet_research import (
    ParquetResearchWriter,
    ParquetRotation,
    create_research_catalog,
)
from hyperliquid_bot.raw_research import MessageDirection, RawResearchRecord

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "deribit"
_DATED_FUTURE = "BTC-25SEP26"
_OPTIONS = (
    "BTC-4SEP26-60000-C",
    "BTC-4SEP26-60000-P",
    "BTC-4SEP26-61000-C",
    "BTC-4SEP26-61000-P",
    "BTC-4SEP26-62000-C",
    "BTC-4SEP26-62000-P",
    "BTC-11SEP26-60000-C",
    "BTC-11SEP26-60000-P",
    "BTC-11SEP26-61000-C",
    "BTC-11SEP26-61000-P",
    "BTC-11SEP26-62000-C",
    "BTC-11SEP26-62000-P",
)


def _config(
    *, max_reconnects: int = 1, max_bytes: int = 8 * 1024 * 1024
) -> DeribitPublicResearchConfig:
    return DeribitPublicResearchConfig(
        dated_future=_DATED_FUTURE,
        option_instruments=_OPTIONS,
        reconnect_delay_seconds=0,
        max_application_payload_bytes=max_bytes,
        max_reconnects=max_reconnects,
    )


def _fixture_bytes(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


def _fixture_text(name: str) -> str:
    return _fixture_bytes(name).decode("utf-8")


def _document(value: str | bytes) -> dict[str, object]:
    return _decode_json_object(value.encode() if isinstance(value, str) else value)


def _fixture_document(name: str) -> dict[str, object]:
    return _document(_fixture_bytes(name))


def _ack(config: DeribitPublicResearchConfig, *, reorder: bool = False) -> str:
    channels = list(config.channels)
    if reorder:
        channels.reverse()
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "data-1h-public-subscribe",
            "result": channels,
        },
        separators=(",", ":"),
    )


def _normalized(
    fixture: str,
    *,
    config: DeribitPublicResearchConfig | None = None,
    state: _BookState | None = None,
    ordinal: int = 7,
) -> tuple[str, dict[str, object]]:
    document = _fixture_document(fixture)
    channel = _classify_document(document)
    return _normalize_notification(
        document,
        source_channel=channel,
        raw_ordinal=ordinal,
        config=_config() if config is None else config,
        book_state=_BookState() if state is None else state,
    )


class MemorySink:
    def __init__(self, *, fail_on_append: int | None = None) -> None:
        self.records: list[RawResearchRecord] = []
        self.append_attempts = 0
        self._fail_on_append = fail_on_append

    async def append(self, record: RawResearchRecord) -> None:
        self.append_attempts += 1
        if self.append_attempts == self._fail_on_append:
            raise RuntimeError("synthetic sink failure")
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
        on_recv_return: Callable[[], None] | None = None,
        disconnect_when_empty: bool = False,
    ) -> None:
        self._messages = deque(messages)
        self._on_last = on_last
        self._on_recv_return = on_recv_return
        self._disconnect_when_empty = disconnect_when_empty
        self._never = asyncio.Event()
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0)
        if not self._messages:
            if self._disconnect_when_empty:
                raise OSError("synthetic disconnect")
            await self._never.wait()
            raise AssertionError("unreachable")
        value = self._messages.popleft()
        if isinstance(value, BaseException):
            raise value
        if not self._messages and self._on_last is not None:
            self._on_last()
        if self._on_recv_return is not None:
            self._on_recv_return()
        return value


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
        return f"public-session-{self.count}"


def _marker_documents(records: Sequence[RawResearchRecord]) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(record.payload_bytes))
        for record in records
        if record.direction is MessageDirection.LOCAL
    ]


def _success_messages(
    config: DeribitPublicResearchConfig, *, reorder_ack: bool = False
) -> list[str]:
    option_messages: list[str] = []
    for instrument_name in config.option_instruments:
        suffix = "C" if instrument_name.endswith("-C") else "P"
        fixture_name = "option_call_ticker.json" if suffix == "C" else "option_put_ticker.json"
        fixture_instrument = f"BTC-4SEP26-61000-{suffix}"
        option_messages.append(
            _fixture_text(fixture_name).replace(fixture_instrument, instrument_name)
        )
    return [
        _ack(config, reorder=reorder_ack),
        _fixture_text("book_snapshot.json"),
        _fixture_text("book_change.json"),
        _fixture_text("trades.json"),
        _fixture_text("perpetual_ticker.json"),
        _fixture_text("future_ticker.json"),
        *option_messages,
        _fixture_text("index.json"),
        _fixture_text("dvol.json"),
    ]


async def _capture_success(sink: MemorySink | ParquetResearchWriter) -> FakeConnection:
    stop = asyncio.Event()
    config = _config()
    connection = FakeConnection(_success_messages(config, reorder_ack=True), on_last=stop.set)
    collector = DeribitPublicResearchCollector(
        sink,
        config=config,
        connection_factory=ScriptedConnectionFactory([connection]),
        utc_ns=Counter(1000),
        monotonic_ns=Counter(2000),
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(30, stop_event=stop)
    return connection


def test_fixed_public_scope_is_credentialless_and_100ms_only() -> None:
    config = _config()
    assert DERIBIT_PUBLIC_WEBSOCKET_URL == "wss://www.deribit.com/ws/api/v2"
    assert DERIBIT_RESEARCH_PRODUCT == "BTC-DERIVATIVES"
    assert DERIBIT_PERPETUAL == "BTC-PERPETUAL"
    assert DERIBIT_INDEX_NAME == "btc_usd"
    assert len(config.channels) == 18
    assert all(
        channel.endswith(".100ms")
        for channel in config.channels
        if channel.startswith(("book.", "trades.", "ticker."))
    )
    assert all("raw" not in channel for channel in config.channels)


def test_config_rejects_unpaired_duplicates_too_many_expiries_and_reconnects() -> None:
    with pytest.raises(ValueError):
        DeribitPublicResearchConfig(_DATED_FUTURE, (_OPTIONS[0], _OPTIONS[1], _OPTIONS[0]))
    with pytest.raises(ValueError):
        DeribitPublicResearchConfig(_DATED_FUTURE, (_OPTIONS[0],))
    third_expiry = ("BTC-18SEP26-61000-C", "BTC-18SEP26-61000-P")
    with pytest.raises(ValueError):
        DeribitPublicResearchConfig(_DATED_FUTURE, (*_OPTIONS[:2], *_OPTIONS[6:8], *third_expiry))
    with pytest.raises(ValueError):
        DeribitPublicResearchConfig(_DATED_FUTURE, _OPTIONS, max_reconnects=2)
    for invalid_delay in (float("nan"), float("inf"), -float("inf"), 31.0):
        with pytest.raises(ValueError, match="reconnect_delay_seconds"):
            DeribitPublicResearchConfig(
                _DATED_FUTURE,
                _OPTIONS,
                reconnect_delay_seconds=invalid_delay,
            )
    for invalid_timeout in (0.0, float("nan"), float("inf"), -float("inf"), 31.0):
        with pytest.raises(ValueError, match="send_timeout_seconds"):
            DeribitPublicResearchConfig(
                _DATED_FUTURE,
                _OPTIONS,
                send_timeout_seconds=invalid_timeout,
            )


def _expiry_ms(day: int, month: int) -> int:
    return int(datetime(2026, month, day, 8, tzinfo=UTC).timestamp()) * 1000


def _discovery_metadata() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {
            "is_active": True,
            "base_currency": "BTC",
            "kind": "future",
            "instrument_name": "BTC-25SEP26",
            "expiration_timestamp": _expiry_ms(25, 9),
        },
        {
            "is_active": True,
            "base_currency": "BTC",
            "kind": "future",
            "instrument_name": "BTC-30OCT26",
            "expiration_timestamp": _expiry_ms(30, 10),
        },
    ]
    for day, expiry in ((4, "4SEP26"), (11, "11SEP26")):
        for strike in (59000, 60000, 61000, 62000):
            for leg, option_type in (("C", "call"), ("P", "put")):
                rows.append(
                    {
                        "is_active": True,
                        "base_currency": "BTC",
                        "kind": "option",
                        "instrument_name": f"BTC-{expiry}-{strike}-{leg}",
                        "expiration_timestamp": _expiry_ms(day, 9),
                        "strike": strike,
                        "option_type": option_type,
                    }
                )
    return rows


def test_selector_picks_nearest_future_and_two_by_three_paired_strikes() -> None:
    selection = select_research_instruments(
        _discovery_metadata(),
        index_price="61050.000000000000000001",
        now_ms=_expiry_ms(31, 8),
    )
    assert selection.dated_future == "BTC-25SEP26"
    assert selection.option_instruments == _OPTIONS


def test_selector_rejects_insufficient_pairs_and_expiry_metadata_mismatch() -> None:
    rows = _discovery_metadata()
    rows[:] = [
        row
        for row in rows
        if row.get("instrument_name") not in {"BTC-11SEP26-59000-P", "BTC-11SEP26-62000-P"}
    ]
    with pytest.raises(DeribitDataIntegrityError):
        select_research_instruments(rows, index_price="61050", now_ms=_expiry_ms(31, 8))

    rows = _discovery_metadata()
    rows[2]["strike"] = 60_000.0
    with pytest.raises(DeribitDataIntegrityError, match="metadata decimal"):
        select_research_instruments(rows, index_price="61050", now_ms=_expiry_ms(31, 8))

    rows = _discovery_metadata()
    rows[0]["expiration_timestamp"] = _expiry_ms(24, 9)
    with pytest.raises(DeribitDataIntegrityError):
        select_research_instruments(rows, index_price="61050", now_ms=_expiry_ms(31, 8))


def test_selector_skips_an_earlier_incomplete_expiry() -> None:
    rows = _discovery_metadata()
    rows[:] = [
        row
        for row in rows
        if not (
            "BTC-4SEP26" in cast(str, row.get("instrument_name"))
            and "-61000-" not in cast(str, row.get("instrument_name"))
        )
    ]
    for strike in (60_000, 61_000, 62_000):
        for leg, option_type in (("C", "call"), ("P", "put")):
            rows.append(
                {
                    "is_active": True,
                    "base_currency": "BTC",
                    "kind": "option",
                    "instrument_name": f"BTC-18SEP26-{strike}-{leg}",
                    "expiration_timestamp": _expiry_ms(18, 9),
                    "strike": strike,
                    "option_type": option_type,
                }
            )

    selection = select_research_instruments(
        rows,
        index_price="61050",
        now_ms=_expiry_ms(31, 8),
    )

    assert all("4SEP26" not in name for name in selection.option_instruments)
    assert {name.split("-")[1] for name in selection.option_instruments} == {
        "11SEP26",
        "18SEP26",
    }


def test_book_accepts_non_gapless_linked_change_and_preserves_decimal_lexemes() -> None:
    state = _BookState()
    _, snapshot = _normalized("book_snapshot.json", state=state, ordinal=10)
    _, change = _normalized("book_change.json", state=state, ordinal=11)
    assert snapshot["change_id"] == "4000"
    assert change["prev_change_id"] == "4000"
    assert change["change_id"] == "4009"
    events = cast(list[dict[str, object]], change["events"])
    assert events[0]["price"] == "61234.125000000000000001"
    assert events[0]["quantity"] == "1.375000000000000001"
    assert [event["wire_order"] for event in events] == [0, 1, 2]


@pytest.mark.parametrize("case", ["change-first", "bad-prev", "duplicate", "second-snapshot"])
def test_book_fails_closed_on_snapshot_and_sequence_violations(case: str) -> None:
    state = _BookState()
    snapshot = _fixture_document("book_snapshot.json")
    change = _fixture_document("book_change.json")
    snapshot_data = cast(dict[str, object], cast(dict[str, object], snapshot["params"])["data"])
    change_data = cast(dict[str, object], cast(dict[str, object], change["params"])["data"])
    source = "book.BTC-PERPETUAL.100ms"
    if case == "change-first":
        with pytest.raises(DeribitDataIntegrityError, match="preceded"):
            state.normalize(change_data, source_channel=source, raw_ordinal=1)
        return
    state.normalize(snapshot_data, source_channel=source, raw_ordinal=1)
    if case == "bad-prev":
        change_data["prev_change_id"] = deribit_module._JsonIntegerLexeme("3999")
        expected = "linkage"
        target = change_data
    elif case == "duplicate":
        change_data["change_id"] = deribit_module._JsonIntegerLexeme("4000")
        expected = "duplicate"
        target = change_data
    else:
        expected = "snapshot boundary"
        target = snapshot_data
    with pytest.raises(DeribitDataIntegrityError, match=expected):
        state.normalize(target, source_channel=source, raw_ordinal=2)
    assert state.has_snapshot is False


def test_delete_action_is_authoritative_even_if_amount_is_nonzero() -> None:
    state = _BookState()
    _normalized("book_snapshot.json", state=state)
    document = _fixture_document("book_change.json")
    data = cast(dict[str, object], cast(dict[str, object], document["params"])["data"])
    asks = cast(list[list[object]], data["asks"])
    asks[0][2] = deribit_module._JsonFloatLexeme("0.125")
    normalized = state.normalize(
        data,
        source_channel="book.BTC-PERPETUAL.100ms",
        raw_ordinal=2,
    )
    events = cast(list[dict[str, object]], normalized["events"])
    assert events[1]["action"] == "delete"
    assert events[1]["quantity"] == "0.125"


def test_quoted_numbers_cannot_satisfy_wire_numeric_fields() -> None:
    document = _fixture_document("book_snapshot.json")
    data = cast(dict[str, object], cast(dict[str, object], document["params"])["data"])
    data["change_id"] = "4000"
    with pytest.raises(DeribitDataIntegrityError, match="integer"):
        _BookState().normalize(
            data,
            source_channel="book.BTC-PERPETUAL.100ms",
            raw_ordinal=1,
        )

    document = _fixture_document("perpetual_ticker.json")
    data = cast(dict[str, object], cast(dict[str, object], document["params"])["data"])
    data["mark_price"] = "61234.25"
    with pytest.raises(DeribitDataIntegrityError, match="decimal"):
        _normalize_notification(
            document,
            source_channel="ticker.BTC-PERPETUAL.100ms",
            raw_ordinal=1,
            config=_config(),
            book_state=_BookState(),
        )


def test_trade_wire_order_preserves_contiguous_trade_seq_and_required_fields() -> None:
    channel, normalized = _normalized("trades.json")
    assert channel == "normalized_trades"
    assert normalized["source_channel"] == "trades.BTC-PERPETUAL.100ms"
    events = cast(list[dict[str, object]], normalized["events"])
    assert [event["wire_order"] for event in events] == [0, 1]
    assert [event["trade_seq"] for event in events] == ["12000", "12001"]
    assert events[0]["tick_direction"] == "2"
    assert events[1]["liquidation"] == "M"
    assert events[0]["contracts"] == "1250.000000000000000001"


@pytest.mark.parametrize(
    ("next_sequence", "quality_event"),
    [("12002", "trade_sequence_gap"), ("12000", "trade_sequence_error")],
)
def test_trade_sequence_fails_closed_within_and_across_notifications(
    next_sequence: str,
    quality_event: str,
) -> None:
    document = _fixture_document("trades.json")
    trades = cast(list[dict[str, object]], cast(dict[str, object], document["params"])["data"])
    state = _TradeState()
    state.normalize(
        [trades[0]],
        source_channel="trades.BTC-PERPETUAL.100ms",
        raw_ordinal=1,
    )
    trades[1]["trade_seq"] = deribit_module._JsonIntegerLexeme(next_sequence)

    with pytest.raises(DeribitDataIntegrityError) as error:
        state.normalize(
            [trades[1]],
            source_channel="trades.BTC-PERPETUAL.100ms",
            raw_ordinal=2,
        )

    assert error.value.quality_event == quality_event


@pytest.mark.parametrize("field", ["tick_direction", "index_price", "mark_price"])
def test_trade_rejects_missing_required_fields(field: str) -> None:
    document = _fixture_document("trades.json")
    params = cast(dict[str, object], document["params"])
    trades = cast(list[dict[str, object]], params["data"])
    del trades[0][field]
    with pytest.raises(DeribitDataIntegrityError):
        _normalize_notification(
            document,
            source_channel="trades.BTC-PERPETUAL.100ms",
            raw_ordinal=1,
            config=_config(),
            book_state=_BookState(),
        )


def test_trade_rejects_unknown_tick_direction_and_liquidation_enum() -> None:
    document = _fixture_document("trades.json")
    trades = cast(
        list[dict[str, object]],
        cast(dict[str, object], document["params"])["data"],
    )
    trades[0]["tick_direction"] = deribit_module._JsonIntegerLexeme("4")
    with pytest.raises(DeribitDataIntegrityError, match="tick direction"):
        _normalize_notification(
            document,
            source_channel="trades.BTC-PERPETUAL.100ms",
            raw_ordinal=1,
            config=_config(),
            book_state=_BookState(),
        )
    trades[0]["tick_direction"] = deribit_module._JsonIntegerLexeme("2")
    trades[0]["liquidation"] = "unexpected"
    with pytest.raises(DeribitDataIntegrityError, match="liquidation"):
        _normalize_notification(
            document,
            source_channel="trades.BTC-PERPETUAL.100ms",
            raw_ordinal=1,
            config=_config(),
            book_state=_BookState(),
        )


def test_derivative_tickers_preserve_funding_and_absent_future_funding_as_null() -> None:
    channel, perpetual = _normalized("perpetual_ticker.json")
    assert channel == "normalized_derivative_ticker"
    assert perpetual["instrument_kind"] == "perpetual"
    assert perpetual["current_funding"] == "-0.000012500000000001"
    _, future = _normalized("future_ticker.json")
    assert future["instrument_kind"] == "future"
    assert future["current_funding"] is None
    assert future["funding_8h"] is None


@pytest.mark.parametrize(
    "state",
    ["open", "settlement", "delivered", "inactive", "locked", "halted", "archivized"],
)
def test_ticker_accepts_exact_official_state_enum(state: str) -> None:
    document = _fixture_document("perpetual_ticker.json")
    data = cast(dict[str, object], cast(dict[str, object], document["params"])["data"])
    data["state"] = state
    _, normalized = _normalize_notification(
        document,
        source_channel="ticker.BTC-PERPETUAL.100ms",
        raw_ordinal=1,
        config=_config(),
        book_state=_BookState(),
    )
    assert normalized["state"] == state


def test_ticker_rejects_unknown_state_and_wrong_instrument() -> None:
    document = _fixture_document("perpetual_ticker.json")
    data = cast(dict[str, object], cast(dict[str, object], document["params"])["data"])
    data["state"] = "unknown"
    with pytest.raises(DeribitDataIntegrityError, match="state"):
        _normalize_notification(
            document,
            source_channel="ticker.BTC-PERPETUAL.100ms",
            raw_ordinal=1,
            config=_config(),
            book_state=_BookState(),
        )
    data["state"] = "open"
    data["instrument_name"] = "ETH-PERPETUAL"
    with pytest.raises(DeribitDataIntegrityError, match="identity"):
        _normalize_notification(
            document,
            source_channel="ticker.BTC-PERPETUAL.100ms",
            raw_ordinal=1,
            config=_config(),
            book_state=_BookState(),
        )


def test_option_iv_quotes_underlying_and_greeks_keep_null_distinct_from_zero() -> None:
    channel, call = _normalized("option_call_ticker.json")
    assert channel == "normalized_option_ticker"
    assert call["expiry_code"] == "4SEP26"
    assert call["strike"] == "61000"
    assert call["option_type"] == "call"
    assert call["bid_iv"] is None
    assert call["best_bid_price"] is None
    assert call["underlying_index"] == "SYNTH-BTC-4SEP26"
    greeks = cast(dict[str, str | None], call["greeks"])
    assert greeks["theta"] == "-9.125000000000000001"

    _, put = _normalized("option_put_ticker.json")
    assert put["underlying_index"] is None
    assert put["ask_iv"] is None
    assert all(value is None for value in cast(dict[str, str | None], put["greeks"]).values())

    document = _fixture_document("option_call_ticker.json")
    data = cast(dict[str, object], cast(dict[str, object], document["params"])["data"])
    data["underlying_index"] = deribit_module._JsonFloatLexeme("61240.125")
    _, numeric_opaque = _normalize_notification(
        document,
        source_channel="ticker.BTC-4SEP26-61000-C.100ms",
        raw_ordinal=8,
        config=_config(),
        book_state=_BookState(),
    )
    assert numeric_opaque["underlying_index"] == "61240.125"


def test_index_and_dvol_use_current_scalar_subscription_contracts() -> None:
    index_channel, index = _normalized("index.json")
    assert index_channel == "normalized_index"
    assert index["index_price"] == "61233.875000000000000001"
    assert index["source_channel"] == "deribit_price_index.btc_usd"

    dvol_channel, dvol = _normalized("dvol.json")
    assert dvol_channel == "normalized_dvol"
    assert dvol["volatility"] == "48.625000000000000001"
    assert "events" not in dvol


def test_duplicate_json_keys_nonfinite_values_and_wrong_channel_fail_closed() -> None:
    with pytest.raises(DeribitDataIntegrityError):
        _document(b'{"jsonrpc":"2.0","jsonrpc":"2.0"}')
    with pytest.raises(DeribitDataIntegrityError):
        _document(b'{"value":NaN}')
    document = _fixture_document("index.json")
    with pytest.raises(DeribitDataIntegrityError, match="channel identity"):
        _normalize_notification(
            document,
            source_channel="deribit_price_index.eth_usd",
            raw_ordinal=1,
            config=_config(),
            book_state=_BookState(),
        )


@pytest.mark.asyncio
async def test_collector_accepts_reordered_exact_ack_and_captures_bytes_before_parsing() -> None:
    sink = MemorySink()
    connection = await _capture_success(sink)
    outbound = cast(dict[str, object], json.loads(cast(str, connection.sent[0])))
    assert outbound["method"] == "public/subscribe"
    assert "private" not in cast(str, connection.sent[0]).lower()
    inbound = [record for record in sink.records if record.direction is MessageDirection.INBOUND]
    fixture = _fixture_bytes("book_snapshot.json")
    source = next(record for record in inbound if record.payload_bytes == fixture)
    assert source.payload_sha256 == hashlib.sha256(fixture).hexdigest()
    assert source.channel == "book.BTC-PERPETUAL.100ms"
    assert [record.message_ordinal for record in sink.records] == list(
        range(1, len(sink.records) + 1)
    )
    markers = _marker_documents(sink.records)
    assert any(marker.get("event") == "subscription_acknowledged" for marker in markers)
    assert any(marker.get("event") == "book_change_validated" for marker in markers)


@pytest.mark.asyncio
async def test_callback_entry_clocks_are_sampled_immediately_after_recv() -> None:
    calls: list[str] = []

    def utc_ns() -> int:
        calls.append("utc")
        return 1_234_567

    def monotonic_ns() -> int:
        calls.append("monotonic")
        return 7_654_321

    connection = FakeConnection(
        [_fixture_text("book_snapshot.json")],
        on_recv_return=lambda: calls.append("recv_return"),
    )
    collector = DeribitPublicResearchCollector(
        MemorySink(),
        config=_config(),
        connection_factory=ScriptedConnectionFactory([]),
        utc_ns=utc_ns,
        monotonic_ns=monotonic_ns,
    )

    captured = await collector._receive_captured(connection)

    assert calls == ["recv_return", "utc", "monotonic"]
    assert captured.received_utc_ns == 1_234_567
    assert captured.received_monotonic_ns == 7_654_321
    assert captured.payload_bytes == _fixture_bytes("book_snapshot.json")


@pytest.mark.asyncio
async def test_zero_public_trades_does_not_substitute_or_block_other_required_evidence() -> None:
    config = _config()
    stop = asyncio.Event()
    messages = [
        message
        for message in _success_messages(config)
        if '"channel":"trades.BTC-PERPETUAL.100ms"' not in message
    ]
    connection = FakeConnection(messages, on_last=stop.set)
    sink = MemorySink()
    collector = DeribitPublicResearchCollector(
        sink,
        config=config,
        connection_factory=ScriptedConnectionFactory([connection]),
    )
    await collector.capture_for(30, stop_event=stop)
    assert not any(record.channel == "trades.BTC-PERPETUAL.100ms" for record in sink.records)
    assert any(
        marker.get("event") == "session_stopped"
        and marker.get("reason") == "external_stop_requested"
        for marker in _marker_documents(sink.records)
    )


@pytest.mark.asyncio
async def test_exact_bytes_and_sha_survive_zstd_parquet_without_file_per_message(
    tmp_path: Path,
) -> None:
    writer = ParquetResearchWriter(
        tmp_path / "parts",
        rotation=ParquetRotation(
            max_records=1000,
            max_payload_bytes=16 * 1024 * 1024,
            max_interval_seconds=3600,
        ),
    )
    await _capture_success(writer)
    await writer.aclose()
    assert len(writer.parquet_files) == 1
    fixture = _fixture_bytes("option_call_ticker.json")
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            "SELECT payload_bytes, payload_sha256 FROM read_parquet(?) "
            "WHERE venue = 'deribit' AND direction = 'inbound'",
            [str(writer.parquet_files[0])],
        ).fetchall()
    finally:
        connection.close()
    matching = [(bytes(payload), digest) for payload, digest in rows if bytes(payload) == fixture]
    assert matching == [(fixture, hashlib.sha256(fixture).hexdigest())]
    assert writer.orphan_partial_files == ()

    database_path = tmp_path / "research.duckdb"
    create_research_catalog(writer.output_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        trades = connection.execute(
            "SELECT trade_seq, price, source_channel, received_utc_ns, "
            "received_monotonic_ns, payload_sha256 FROM deribit_btc_trades ORDER BY wire_order"
        ).fetchall()
        l2 = connection.execute(
            "SELECT message_type, change_id, prev_change_id, price, source_channel "
            "FROM deribit_btc_l2_events ORDER BY normalization_message_ordinal, wire_order"
        ).fetchall()
        contexts = connection.execute(
            "SELECT context_type, instrument_kind, current_funding, volatility, source_channel "
            "FROM deribit_btc_derivative_context ORDER BY normalization_message_ordinal"
        ).fetchall()
        options = connection.execute(
            "SELECT option_type, bid_iv, ask_iv, underlying_index, theta, source_channel, "
            "received_utc_ns, payload_sha256 FROM deribit_btc_option_sample "
            "ORDER BY normalization_message_ordinal"
        ).fetchall()
    finally:
        connection.close()
    assert [row[0] for row in trades] == ["12000", "12001"]
    assert trades[0][1] == "61234.625000000000000001"
    assert trades[0][2] == "trades.BTC-PERPETUAL.100ms"
    assert type(trades[0][3]) is int and type(trades[0][4]) is int
    assert trades[0][5] == hashlib.sha256(_fixture_bytes("trades.json")).hexdigest()
    assert len(l2) == 5
    assert l2[-1][1:3] == ("4009", "4000")
    assert all(row[4] == "book.BTC-PERPETUAL.100ms" for row in l2)
    assert {row[0] for row in contexts} == {"ticker", "index_price", "volatility_index"}
    assert any(row[2] == "-0.000012500000000001" for row in contexts)
    assert any(row[3] == "48.625000000000000001" for row in contexts)
    assert options[0][0] == "call"
    assert options[0][1] is None
    assert options[0][4] == "-9.125000000000000001"
    assert options[1][0] == "put"
    assert options[1][2] is None
    assert options[1][3] is None
    assert len(options) == 12
    assert {row[5] for row in options} == {f"ticker.{name}.100ms" for name in _OPTIONS}
    assert all(type(row[6]) is int and len(row[7]) == 64 for row in options)


@pytest.mark.asyncio
async def test_empty_book_event_array_does_not_create_a_phantom_l2_row(tmp_path: Path) -> None:
    config = _config()
    snapshot = cast(dict[str, object], json.loads(_fixture_text("book_snapshot.json")))
    data = cast(dict[str, object], cast(dict[str, object], snapshot["params"])["data"])
    data["bids"] = []
    data["asks"] = []
    messages = _success_messages(config)
    messages[1] = json.dumps(snapshot, separators=(",", ":"))
    stop = asyncio.Event()
    writer = ParquetResearchWriter(tmp_path / "parts")
    collector = DeribitPublicResearchCollector(
        writer,
        config=config,
        connection_factory=ScriptedConnectionFactory([FakeConnection(messages, on_last=stop.set)]),
    )
    await collector.capture_for(30, stop_event=stop)
    await writer.aclose()
    database_path = tmp_path / "research.duckdb"
    create_research_catalog(writer.output_dir, database_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        snapshot_rows = connection.execute(
            "SELECT count(*) FROM deribit_btc_l2_events WHERE message_type = 'snapshot'"
        ).fetchone()
    finally:
        connection.close()

    assert snapshot_rows == (0,)


@pytest.mark.asyncio
async def test_reconnect_starts_new_session_and_requires_fresh_snapshot() -> None:
    config = _config()
    stop = asyncio.Event()
    first = FakeConnection(
        [_ack(config), _fixture_text("book_snapshot.json")],
        disconnect_when_empty=True,
    )
    second = FakeConnection(_success_messages(config), on_last=stop.set)
    sink = MemorySink()
    factory = ScriptedConnectionFactory([first, second])
    collector = DeribitPublicResearchCollector(
        sink,
        config=config,
        connection_factory=factory,
        session_id_factory=SessionIds(),
    )
    await collector.capture_for(30, stop_event=stop)
    assert factory.calls == 2
    assert {record.session_id for record in sink.records} == {
        "public-session-1",
        "public-session-2",
    }
    markers = _marker_documents(sink.records)
    assert any(marker.get("event") == "gap_detected" for marker in markers)
    assert any(marker.get("event") == "reconnected" for marker in markers)
    assert any(marker.get("event") == "resnapshot_received" for marker in markers)


@pytest.mark.asyncio
async def test_reconnect_change_without_fresh_snapshot_fails_closed() -> None:
    config = _config()
    first = FakeConnection(
        [_ack(config), _fixture_text("book_snapshot.json")],
        disconnect_when_empty=True,
    )
    second = FakeConnection([_ack(config), _fixture_text("book_change.json")])
    collector = DeribitPublicResearchCollector(
        MemorySink(),
        config=config,
        connection_factory=ScriptedConnectionFactory([first, second]),
        session_id_factory=SessionIds(),
    )
    with pytest.raises(DeribitDataIntegrityError, match="preceded"):
        await collector.capture_for(30)


@pytest.mark.asyncio
async def test_capture_cannot_succeed_without_required_nontrade_session_evidence() -> None:
    config = _config(max_reconnects=0)
    stop = asyncio.Event()
    sink = MemorySink()
    connection = FakeConnection(
        [_ack(config), _fixture_text("book_snapshot.json")],
        on_last=stop.set,
    )
    collector = DeribitPublicResearchCollector(
        sink,
        config=config,
        connection_factory=ScriptedConnectionFactory([connection]),
    )
    with pytest.raises(DeribitDataIntegrityError, match="evidence was incomplete"):
        await collector.capture_for(30, stop_event=stop)
    coverage = [
        marker
        for marker in _marker_documents(sink.records)
        if marker.get("event") == "coverage_incomplete"
    ]
    assert len(coverage) == 1
    assert coverage[0]["missing_channel_count"] == 16
    assert coverage[0]["book_change_observed"] is False
    assert coverage[0]["public_trades_required"] is False


@pytest.mark.asyncio
async def test_binary_json_frame_is_preserved_then_rejected_as_wrong_transport_type() -> None:
    config = _config(max_reconnects=0)
    sink = MemorySink()
    collector = DeribitPublicResearchCollector(
        sink,
        config=config,
        connection_factory=ScriptedConnectionFactory(
            [FakeConnection([_ack(config), _fixture_bytes("book_snapshot.json")])]
        ),
    )
    with pytest.raises(DeribitDataIntegrityError, match="was not text"):
        await collector.capture_for(30)
    binary = [
        record
        for record in sink.records
        if record.direction is MessageDirection.INBOUND
        and record.payload_bytes == _fixture_bytes("book_snapshot.json")
    ]
    assert len(binary) == 1
    assert binary[0].frame_type.value == "binary"


@pytest.mark.asyncio
async def test_partial_duplicate_or_rejected_ack_cannot_be_public_success() -> None:
    config = _config(max_reconnects=0)
    for result in (
        list(config.channels[:-1]),
        [*config.channels[:-1], config.channels[0]],
    ):
        response = json.dumps(
            {"jsonrpc": "2.0", "id": "data-1h-public-subscribe", "result": result}
        )
        collector = DeribitPublicResearchCollector(
            MemorySink(),
            config=config,
            connection_factory=ScriptedConnectionFactory([FakeConnection([response])]),
        )
        with pytest.raises(DeribitDataIntegrityError, match="did not match"):
            await collector.capture_for(30)

    error = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "data-1h-public-subscribe",
            "error": {"code": 10000, "message": "synthetic rejection"},
        }
    )
    collector = DeribitPublicResearchCollector(
        MemorySink(),
        config=config,
        connection_factory=ScriptedConnectionFactory([FakeConnection([error])]),
    )
    with pytest.raises(DeribitDataIntegrityError, match="rejected"):
        await collector.capture_for(30)


@pytest.mark.asyncio
async def test_oversize_and_transport_truncation_stop_without_retry() -> None:
    config = _config(max_reconnects=1, max_bytes=20)
    factory = ScriptedConnectionFactory([FakeConnection([_ack(config)])])
    collector = DeribitPublicResearchCollector(
        MemorySink(), config=config, connection_factory=factory
    )
    with pytest.raises(DeribitDataIntegrityError, match="exceeded"):
        await collector.capture_for(30)
    assert factory.calls == 1

    factory = ScriptedConnectionFactory(
        [FakeConnection([DeribitTransportTruncation("synthetic truncation")])]
    )
    collector = DeribitPublicResearchCollector(
        MemorySink(), config=_config(), connection_factory=factory
    )
    with pytest.raises(DeribitDataIntegrityError, match="truncated"):
        await collector.capture_for(30)
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_sink_failure_is_sticky_and_never_reconnects() -> None:
    sink = MemorySink(fail_on_append=2)
    factory = ScriptedConnectionFactory([FakeConnection([])])
    collector = DeribitPublicResearchCollector(
        sink,
        config=_config(),
        connection_factory=factory,
    )
    with pytest.raises(DeribitSinkError):
        await collector.capture_for(30)
    assert factory.calls == 1
    attempts = sink.append_attempts
    with pytest.raises(DeribitSinkError):
        await collector._append_marker("session", "session", "after_failure")
    assert sink.append_attempts == attempts


@pytest.mark.asyncio
async def test_preset_stop_and_invalid_frame_type_cannot_report_success() -> None:
    preset = asyncio.Event()
    preset.set()
    collector = DeribitPublicResearchCollector(
        MemorySink(),
        config=_config(),
        connection_factory=ScriptedConnectionFactory([]),
    )
    with pytest.raises(DeribitDataIntegrityError, match="already requested"):
        await collector.capture_for(30, stop_event=preset)

    sink = MemorySink()
    collector = DeribitPublicResearchCollector(
        sink,
        config=_config(max_reconnects=0),
        connection_factory=ScriptedConnectionFactory(
            [cast(WebSocketConnection, FakeConnection([cast(BaseException, object())]))]
        ),
    )
    with pytest.raises(DeribitDataIntegrityError, match="frame type"):
        await collector.capture_for(30)
    assert any(marker.get("event") == "schema_error" for marker in _marker_documents(sink.records))


@pytest.mark.parametrize("duration", [0, 0.5, 601, True, "30"])
@pytest.mark.asyncio
async def test_capture_duration_is_hard_bounded(duration: object) -> None:
    collector = DeribitPublicResearchCollector(
        MemorySink(),
        config=_config(),
        connection_factory=ScriptedConnectionFactory([]),
    )
    with pytest.raises((TypeError, ValueError)):
        await collector.capture_for(cast(float, duration))


@pytest.mark.asyncio
async def test_hanging_connection_enter_is_cancelled_at_the_hard_deadline() -> None:
    cancelled = asyncio.Event()

    @asynccontextmanager
    async def hanging_context() -> AsyncIterator[WebSocketConnection]:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        yield FakeConnection([])

    def factory() -> AbstractAsyncContextManager[WebSocketConnection]:
        return hanging_context()

    collector = DeribitPublicResearchCollector(
        MemorySink(),
        config=_config(max_reconnects=0),
        connection_factory=factory,
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(DeribitTransportError, match="hard duration"):
        async with asyncio.timeout(1.3):
            await collector.capture_for(1)

    assert loop.time() - started < 1.25
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_private_duration_stop_retains_its_distinct_reason() -> None:
    config = _config(max_reconnects=0)
    sink = MemorySink()
    collector = DeribitPublicResearchCollector(
        sink,
        config=config,
        connection_factory=ScriptedConnectionFactory([FakeConnection(_success_messages(config))]),
    )

    await collector.capture_for(1)

    stopped = [
        marker
        for marker in _marker_documents(sink.records)
        if marker.get("event") == "session_stopped"
    ]
    assert stopped == [{"event": "session_stopped", "reason": "capture_limit_reached"}]


def test_source_has_no_builtin_network_auth_execution_or_secret_surface() -> None:
    source_path = Path(inspect.getsourcefile(deribit_module) or "")
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imports.isdisjoint({"socket", "urllib", "httpx", "requests", "websockets"})
    signature = inspect.signature(DeribitPublicResearchCollector)
    assert signature.parameters["connection_factory"].default is inspect.Parameter.empty
    lowered = source.lower()
    for forbidden in ("api_key", "access_token", "client_secret", "private/subscribe"):
        assert forbidden not in lowered


def test_fixtures_are_synthetic_public_market_data_without_secret_literals() -> None:
    combined = b"".join(path.read_bytes() for path in sorted(_FIXTURE_DIR.iterdir()))
    lowered = combined.lower()
    assert b"synthetic" in lowered
    for forbidden in (b"api_key", b"access_token", b"client_secret", b"bitwarden"):
        assert forbidden not in lowered
