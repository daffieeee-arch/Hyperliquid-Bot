"""Deterministic tests for the offline Hyperliquid public-trade boundary."""

import ast
import builtins
import inspect
import json
import os
import socket
import time
import urllib.request
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

import hyperliquid_bot.hyperliquid_trades as hyperliquid_trades_module
from hyperliquid_bot.contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    AggressorSide,
    Instrument,
    InstrumentType,
    MarketEventEnvelope,
    Venue,
)
from hyperliquid_bot.hyperliquid_trades import (
    HyperliquidWsTrade,
    decode_hyperliquid_trades_frame,
    normalize_hyperliquid_trade,
    normalize_hyperliquid_trades_frame,
)

_FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "hyperliquid"
_FIXTURE_PATH = _FIXTURE_DIRECTORY / "trades_frame.json"
_HIP3_FIXTURE_PATH = _FIXTURE_DIRECTORY / "hip3_trades_frame.json"
_RECEIVED_TIME = datetime(2026, 8, 25, 13, 0, tzinfo=UTC)


def _frame(fixture_path: Path = _FIXTURE_PATH) -> dict[str, object]:
    loaded = json.loads(fixture_path.read_text(encoding="utf-8"))
    if type(loaded) is not dict:
        raise AssertionError("fixture root must be an object")
    return cast(dict[str, object], loaded)


def _trade_payload(
    index: int = 0,
    *,
    fixture_path: Path = _FIXTURE_PATH,
) -> dict[str, object]:
    frame = _frame(fixture_path)
    data = frame["data"]
    if type(data) is not list:
        raise AssertionError("fixture data must be a list")
    item = cast(list[object], data)[index]
    if type(item) is not dict:
        raise AssertionError("fixture trade must be an object")
    return cast(dict[str, object], deepcopy(item))


def _instrument(
    coin: str = "BTC",
    *,
    venue: Venue = Venue.HYPERLIQUID,
    venue_market_id: str | None = None,
) -> Instrument:
    return Instrument(
        venue=venue,
        instrument_type=InstrumentType.PERPETUAL,
        base_asset="XYZ100" if coin == "xyz:XYZ100" else "BTC",
        quote_asset="USDC",
        venue_market_id=coin if venue_market_id is None else venue_market_id,
        native_symbol=coin,
    )


def _normalize(
    trade: HyperliquidWsTrade,
    *,
    registry: tuple[Instrument, ...] | None = None,
) -> MarketEventEnvelope:
    return normalize_hyperliquid_trade(
        trade,
        instrument_registry=(_instrument(trade.coin),) if registry is None else registry,
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=987_654_321,
        collector_version="collector-v2",
        collector_commit="4ab5128",
        is_gap=False,
    )


def test_complete_trades_frame_decodes_array_and_preserves_documented_values() -> None:
    trades = decode_hyperliquid_trades_frame(_frame())

    assert len(trades) == 2
    buy, sell = trades
    assert {trade.coin for trade in trades} == {"BTC"}
    assert buy.coin == "BTC"
    assert buy.side == "B"
    assert buy.aggressor_side is AggressorSide.BUY
    assert str(buy.px) == "12345.678900000000000001"
    assert str(buy.sz) == "1.0000000000E-8"
    assert buy.px.as_tuple() == Decimal("12345.678900000000000001").as_tuple()
    assert buy.sz.as_tuple() == Decimal("0.000000010000000000").as_tuple()
    assert buy.event_time == datetime(2024, 7, 3, 9, 46, 40, 123_000, tzinfo=UTC)
    assert buy.users == (
        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )

    assert sell.coin == "BTC"
    assert sell.side == "A"
    assert sell.aggressor_side is AggressorSide.SELL
    assert sell.tid == (1 << 50) - 1
    assert sell.users == (
        "0xcccccccccccccccccccccccccccccccccccccccc",
        "0xdddddddddddddddddddddddddddddddddddddddd",
    )


def test_hip3_fixture_decodes_one_exact_coin_with_high_precision_values() -> None:
    trades = decode_hyperliquid_trades_frame(_frame(_HIP3_FIXTURE_PATH))

    assert len(trades) == 1
    assert {trade.coin for trade in trades} == {"xyz:XYZ100"}
    (trade,) = trades
    assert trade.coin == "xyz:XYZ100"
    assert trade.side == "A"
    assert trade.aggressor_side is AggressorSide.SELL
    assert trade.px.as_tuple() == Decimal("0.123456789012345678").as_tuple()
    assert trade.sz.as_tuple() == Decimal("987654321.000000000000000001").as_tuple()
    assert trade.users == (
        "0xcccccccccccccccccccccccccccccccccccccccc",
        "0xdddddddddddddddddddddddddddddddddddddddd",
    )


def test_complete_frame_normalizes_with_only_explicit_metadata() -> None:
    envelopes = normalize_hyperliquid_trades_frame(
        _frame(),
        instrument_registry=(_instrument(),),
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=987_654_321,
        collector_version="collector-v2",
        collector_commit="4ab5128",
        is_gap=True,
    )

    buy, sell = envelopes
    assert buy.schema_version == MARKET_EVENT_SCHEMA_VERSION == 2
    assert buy.event.aggressor_side is AggressorSide.BUY
    assert sell.event.aggressor_side is AggressorSide.SELL
    assert not hasattr(buy.event, "users")
    assert not hasattr(buy.event, "buyer")
    assert not hasattr(buy.event, "seller")
    assert sell.instrument.native_symbol == "BTC"
    assert buy.received_time == _RECEIVED_TIME
    assert buy.received_monotonic_ns == 987_654_321
    assert buy.collector_version == "collector-v2"
    assert buy.collector_commit == "4ab5128"
    assert buy.is_gap is True
    assert buy.source_event_id == '["hyperliquid-trade-v1",1720000000123,"BTC",42]'
    assert sell.source_event_id == ('["hyperliquid-trade-v1",1720000000456,"BTC",1125899906842623]')
    assert buy.source_transaction_id == (
        "0x1111111111111111111111111111111111111111111111111111111111111111"
    )
    assert buy.source_sequence is None
    assert buy.correlation_id is None


def test_source_event_id_is_deterministic_and_uses_only_official_identity_tuple() -> None:
    payload = _trade_payload()
    duplicate = deepcopy(payload)
    changed_time = deepcopy(payload)
    changed_time["time"] = cast(int, payload["time"]) + 1
    changed_coin = deepcopy(payload)
    changed_coin["coin"] = "xyz:XYZ100"
    changed_tid = deepcopy(payload)
    changed_tid["tid"] = cast(int, payload["tid"]) + 1
    changed_non_identity = deepcopy(payload)
    changed_non_identity["px"] = "1.000000000000000000"
    changed_non_identity["hash"] = "synthetic-alternate-transaction"
    changed_non_identity["users"] = ["synthetic-buyer", "synthetic-seller"]

    registry = (_instrument(), _instrument("xyz:XYZ100"))
    normalized = _normalize(HyperliquidWsTrade.from_payload(payload), registry=registry)
    normalized_duplicate = _normalize(HyperliquidWsTrade.from_payload(duplicate), registry=registry)
    variants = [
        _normalize(HyperliquidWsTrade.from_payload(changed_time), registry=registry),
        _normalize(HyperliquidWsTrade.from_payload(changed_coin), registry=registry),
        _normalize(HyperliquidWsTrade.from_payload(changed_tid), registry=registry),
    ]
    normalized_non_identity = _normalize(
        HyperliquidWsTrade.from_payload(changed_non_identity),
        registry=registry,
    )

    assert normalized == normalized_duplicate
    assert hash(normalized) == hash(normalized_duplicate)
    assert all(item.source_event_id != normalized.source_event_id for item in variants)
    assert normalized_non_identity.source_event_id == normalized.source_event_id


def test_exact_registry_lookup_preserves_hip3_coin_without_inference() -> None:
    instrument = _instrument("xyz:XYZ100")
    envelopes = normalize_hyperliquid_trades_frame(
        _frame(_HIP3_FIXTURE_PATH),
        instrument_registry=(instrument,),
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=987_654_321,
        collector_version="collector-v2",
        collector_commit="4ab5128",
        is_gap=False,
    )
    (envelope,) = envelopes

    assert envelope.instrument is instrument
    assert envelope.instrument.venue_market_id == "xyz:XYZ100"
    assert envelope.instrument.native_symbol == "xyz:XYZ100"
    assert envelope.source_event_id == (
        '["hyperliquid-trade-v1",1720000000789,"xyz:XYZ100",1125899906842622]'
    )


def test_unknown_case_variant_and_ambiguous_coins_fail_closed() -> None:
    trade = HyperliquidWsTrade.from_payload(_trade_payload())

    with pytest.raises(LookupError, match="not registered"):
        _normalize(trade, registry=())

    lowercase_payload = _trade_payload()
    lowercase_payload["coin"] = "btc"
    lowercase_trade = HyperliquidWsTrade.from_payload(lowercase_payload)
    with pytest.raises(LookupError, match="not registered"):
        _normalize(lowercase_trade, registry=(_instrument("BTC"),))

    ambiguous_registry = (
        _instrument("BTC", venue_market_id="BTC-primary"),
        _instrument("BTC", venue_market_id="BTC-secondary"),
    )
    with pytest.raises(LookupError, match="ambiguous"):
        _normalize(trade, registry=ambiguous_registry)

    wrong_venue = _instrument("BTC", venue=Venue.BINANCE)
    with pytest.raises(LookupError, match="not registered"):
        _normalize(trade, registry=(wrong_venue,))


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(None, id="none"),
        pytest.param("trades", id="string"),
        pytest.param([], id="list"),
        pytest.param((), id="tuple"),
    ],
)
def test_frame_requires_exact_builtin_object(frame: object) -> None:
    with pytest.raises(TypeError):
        decode_hyperliquid_trades_frame(frame)


def test_wrong_channels_and_subscription_acknowledgements_fail_closed() -> None:
    for frame in (
        {"channel": "l2Book", "data": []},
        {"channel": "Trades", "data": []},
        {
            "channel": "subscriptionResponse",
            "data": {
                "method": "subscribe",
                "subscription": {"type": "trades", "coin": "BTC"},
            },
        },
    ):
        with pytest.raises(ValueError, match="channel"):
            decode_hyperliquid_trades_frame(frame)


@pytest.mark.parametrize(
    "frame,expected_error",
    [
        pytest.param({"data": []}, ValueError, id="missing-channel"),
        pytest.param({"channel": 1, "data": []}, TypeError, id="integer-channel"),
        pytest.param({"channel": True, "data": []}, TypeError, id="boolean-channel"),
        pytest.param({"channel": "trades"}, ValueError, id="missing-data"),
        pytest.param({"channel": "trades", "data": None}, TypeError, id="none-data"),
        pytest.param({"channel": "trades", "data": {}}, TypeError, id="object-data"),
        pytest.param({"channel": "trades", "data": ()}, TypeError, id="tuple-data"),
        pytest.param({"channel": "trades", "data": [None]}, TypeError, id="none-item"),
        pytest.param({"channel": "trades", "data": [()]}, TypeError, id="tuple-item"),
    ],
)
def test_frame_rejects_missing_or_wrong_container_types(
    frame: object,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        decode_hyperliquid_trades_frame(frame)


def test_empty_trades_array_is_valid() -> None:
    assert decode_hyperliquid_trades_frame({"channel": "trades", "data": []}) == ()


@pytest.mark.parametrize(
    "missing_field",
    ["coin", "side", "px", "sz", "hash", "time", "tid", "users"],
)
def test_every_documented_trade_field_is_required(missing_field: str) -> None:
    payload = _trade_payload()
    del payload[missing_field]

    with pytest.raises(ValueError, match=missing_field):
        HyperliquidWsTrade.from_payload(payload)


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        pytest.param("coin", None, id="coin-none"),
        pytest.param("coin", 1, id="coin-integer"),
        pytest.param("coin", 1.0, id="coin-float"),
        pytest.param("coin", True, id="coin-boolean"),
        pytest.param("coin", [], id="coin-list"),
        pytest.param("coin", {}, id="coin-object"),
        pytest.param("side", None, id="side-none"),
        pytest.param("side", 1, id="side-integer"),
        pytest.param("side", 1.0, id="side-float"),
        pytest.param("side", True, id="side-boolean"),
        pytest.param("side", [], id="side-list"),
        pytest.param("side", {}, id="side-object"),
        pytest.param("hash", None, id="hash-none"),
        pytest.param("hash", 1, id="hash-integer"),
        pytest.param("hash", 1.0, id="hash-float"),
        pytest.param("hash", True, id="hash-boolean"),
        pytest.param("hash", [], id="hash-list"),
        pytest.param("hash", {}, id="hash-object"),
        pytest.param("time", None, id="time-none"),
        pytest.param("time", "1720000000123", id="time-string"),
        pytest.param("time", 1720000000.123, id="time-float"),
        pytest.param("time", True, id="time-boolean"),
        pytest.param("time", [], id="time-list"),
        pytest.param("time", {}, id="time-object"),
        pytest.param("tid", None, id="tid-none"),
        pytest.param("tid", "42", id="tid-string"),
        pytest.param("tid", 42.0, id="tid-float"),
        pytest.param("tid", True, id="tid-boolean"),
        pytest.param("tid", [], id="tid-list"),
        pytest.param("tid", {}, id="tid-object"),
    ],
)
def test_required_fields_reject_wrong_runtime_types(
    field_name: str,
    invalid_value: object,
) -> None:
    payload = _trade_payload()
    payload[field_name] = invalid_value

    with pytest.raises(TypeError):
        HyperliquidWsTrade.from_payload(payload)


@pytest.mark.parametrize("field_name", ["coin", "hash"])
@pytest.mark.parametrize("invalid_value", ["", " value", "value ", "value\n"])
def test_required_text_fields_reject_empty_whitespace_or_control_characters(
    field_name: str,
    invalid_value: str,
) -> None:
    payload = _trade_payload()
    payload[field_name] = invalid_value

    with pytest.raises(ValueError):
        HyperliquidWsTrade.from_payload(payload)


@pytest.mark.parametrize("field_name", ["px", "sz"])
@pytest.mark.parametrize(
    "invalid_value,expected_error",
    [
        pytest.param(1, TypeError, id="json-integer"),
        pytest.param(1.0, TypeError, id="json-float"),
        pytest.param(True, TypeError, id="json-boolean"),
        pytest.param(None, TypeError, id="json-null"),
        pytest.param([], TypeError, id="json-list"),
        pytest.param({}, TypeError, id="json-object"),
        pytest.param("", ValueError, id="empty"),
        pytest.param(" 1", ValueError, id="outer-whitespace"),
        pytest.param("not-a-number", ValueError, id="malformed"),
        pytest.param("NaN", ValueError, id="nan"),
        pytest.param("sNaN", ValueError, id="signaling-nan"),
        pytest.param("Infinity", ValueError, id="positive-infinity"),
        pytest.param("-Infinity", ValueError, id="negative-infinity"),
        pytest.param("0", ValueError, id="zero"),
        pytest.param("-0", ValueError, id="negative-zero"),
        pytest.param("-1", ValueError, id="negative"),
    ],
)
def test_price_and_size_require_positive_finite_decimal_strings(
    field_name: str,
    invalid_value: object,
    expected_error: type[Exception],
) -> None:
    payload = _trade_payload()
    payload[field_name] = invalid_value

    with pytest.raises(expected_error):
        HyperliquidWsTrade.from_payload(payload)


@pytest.mark.parametrize("invalid_side", ["", "b", "a", "BUY", "SELL", "B ", "Bid", "Ask"])
def test_trade_side_accepts_only_exact_official_codes(invalid_side: str) -> None:
    payload = _trade_payload()
    payload["side"] = invalid_side

    with pytest.raises(ValueError):
        HyperliquidWsTrade.from_payload(payload)


def test_time_zero_and_maximum_supported_millisecond_are_exact() -> None:
    epoch_payload = _trade_payload()
    epoch_payload["time"] = 0
    max_payload = _trade_payload()
    max_payload["time"] = 253_402_300_799_999

    epoch_trade = HyperliquidWsTrade.from_payload(epoch_payload)
    max_trade = HyperliquidWsTrade.from_payload(max_payload)

    assert epoch_trade.event_time == datetime(1970, 1, 1, tzinfo=UTC)
    assert max_trade.event_time == datetime(9999, 12, 31, 23, 59, 59, 999_000, tzinfo=UTC)


@pytest.mark.parametrize("invalid_time", [-1, 253_402_300_800_000, 10**100])
def test_negative_or_overflowing_timestamps_fail_cleanly(invalid_time: int) -> None:
    payload = _trade_payload()
    payload["time"] = invalid_time

    with pytest.raises(ValueError):
        HyperliquidWsTrade.from_payload(payload)


@pytest.mark.parametrize("invalid_tid", [-1, 1 << 50])
def test_tid_must_fit_documented_unsigned_50_bit_range(invalid_tid: int) -> None:
    payload = _trade_payload()
    payload["tid"] = invalid_tid

    with pytest.raises(ValueError):
        HyperliquidWsTrade.from_payload(payload)


def test_zero_tid_is_valid() -> None:
    payload = _trade_payload()
    payload["tid"] = 0

    assert HyperliquidWsTrade.from_payload(payload).tid == 0


@pytest.mark.parametrize(
    "invalid_users,expected_error",
    [
        pytest.param(None, TypeError, id="none"),
        pytest.param(1, TypeError, id="integer"),
        pytest.param(1.0, TypeError, id="float"),
        pytest.param(True, TypeError, id="boolean"),
        pytest.param("buyer,seller", TypeError, id="string"),
        pytest.param(("buyer", "seller"), TypeError, id="tuple-wire-type"),
        pytest.param({}, TypeError, id="object"),
        pytest.param([], ValueError, id="empty"),
        pytest.param(["buyer"], ValueError, id="one-user"),
        pytest.param(["buyer", "seller", "third"], ValueError, id="three-users"),
        pytest.param([None, "seller"], TypeError, id="none-buyer"),
        pytest.param(["buyer", 1], TypeError, id="integer-seller"),
        pytest.param([True, "seller"], TypeError, id="boolean-buyer"),
        pytest.param(["", "seller"], ValueError, id="empty-buyer"),
        pytest.param([" buyer", "seller"], ValueError, id="whitespace-buyer"),
        pytest.param(["buyer", "seller\n"], ValueError, id="control-seller"),
    ],
)
def test_users_must_be_exact_ordered_two_string_array(
    invalid_users: object,
    expected_error: type[Exception],
) -> None:
    payload = _trade_payload()
    payload["users"] = invalid_users

    with pytest.raises(expected_error):
        HyperliquidWsTrade.from_payload(payload)


def test_unknown_additive_frame_and_trade_fields_are_tolerated_and_discarded() -> None:
    baseline = decode_hyperliquid_trades_frame(_frame())
    extended = _frame()
    extended["futureFrameField"] = {"ignored": True}
    data = cast(list[object], extended["data"])
    first = cast(dict[str, object], data[0])
    first["futureTradeField"] = ["ignored"]

    decoded = decode_hyperliquid_trades_frame(extended)

    assert decoded == baseline
    assert not hasattr(decoded[0], "futureTradeField")


def test_one_invalid_trade_rejects_the_entire_frame() -> None:
    frame = _frame()
    data = cast(list[object], frame["data"])
    second = cast(dict[str, object], data[1])
    second["px"] = 1.25

    with pytest.raises(TypeError):
        decode_hyperliquid_trades_frame(frame)


def test_registry_rejects_non_instrument_entries() -> None:
    trade = HyperliquidWsTrade.from_payload(_trade_payload())

    with pytest.raises(TypeError):
        _normalize(trade, registry=cast(tuple[Instrument, ...], (object(),)))


def test_dto_is_frozen_slotted_deterministic_and_hashable() -> None:
    first = HyperliquidWsTrade.from_payload(_trade_payload())
    second = HyperliquidWsTrade.from_payload(_trade_payload())

    assert not hasattr(first, "__dict__")
    assert first == second
    assert hash(first) == hash(second)
    with pytest.raises(FrozenInstanceError):
        first.coin = "ETH"  # type: ignore[misc]


@pytest.mark.parametrize(
    "field_name,invalid_value,expected_error",
    [
        pytest.param("coin", "", ValueError, id="coin"),
        pytest.param("side", "X", ValueError, id="side"),
        pytest.param("px", 1.0, TypeError, id="price"),
        pytest.param("sz", Decimal("0"), ValueError, id="size"),
        pytest.param("hash", "", ValueError, id="hash"),
        pytest.param("time", True, TypeError, id="time"),
        pytest.param("tid", 1 << 50, ValueError, id="trade-id"),
        pytest.param("users", ["buyer", "seller"], TypeError, id="users"),
    ],
)
def test_dto_constructor_revalidates_all_eight_source_fields(
    field_name: str,
    invalid_value: object,
    expected_error: type[Exception],
) -> None:
    template = HyperliquidWsTrade.from_payload(_trade_payload())

    with pytest.raises(expected_error):
        match field_name:
            case "coin":
                replace(template, coin=cast(str, invalid_value))
            case "side":
                replace(template, side=cast(str, invalid_value))
            case "px":
                replace(template, px=cast(Decimal, invalid_value))
            case "sz":
                replace(template, sz=cast(Decimal, invalid_value))
            case "hash":
                replace(template, hash=cast(str, invalid_value))
            case "time":
                replace(template, time=cast(int, invalid_value))
            case "tid":
                replace(template, tid=cast(int, invalid_value))
            case "users":
                replace(template, users=cast(tuple[str, str], invalid_value))
            case _:
                raise AssertionError(f"unsupported test field: {field_name}")


def test_decoder_and_normalizer_do_not_use_clock_network_filesystem_or_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    registry = (_instrument(),)

    def unexpected_call(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"unexpected external dependency: {args!r} {kwargs!r}")

    class UnexpectedDateTime:
        now = staticmethod(unexpected_call)
        utcnow = staticmethod(unexpected_call)

    with monkeypatch.context() as isolated:
        isolated.setattr(builtins, "open", unexpected_call)
        isolated.setattr(Path, "open", unexpected_call)
        isolated.setattr(Path, "read_bytes", unexpected_call)
        isolated.setattr(Path, "read_text", unexpected_call)
        isolated.setattr(os, "getenv", unexpected_call)
        isolated.setattr(os.environ, "get", unexpected_call)
        isolated.setattr(time, "time", unexpected_call)
        isolated.setattr(time, "time_ns", unexpected_call)
        isolated.setattr(time, "monotonic_ns", unexpected_call)
        isolated.setattr(socket, "socket", unexpected_call)
        isolated.setattr(socket, "create_connection", unexpected_call)
        isolated.setattr(urllib.request, "urlopen", unexpected_call)
        isolated.setattr(hyperliquid_trades_module, "datetime", UnexpectedDateTime)
        result = normalize_hyperliquid_trades_frame(
            frame,
            instrument_registry=registry,
            received_time=_RECEIVED_TIME,
            received_monotonic_ns=1,
            collector_version="collector-v2",
            collector_commit="4ab5128",
            is_gap=False,
        )

    assert len(result) == 2
    assert result[0].received_time == _RECEIVED_TIME
    assert result[0].source_sequence is None
    assert result[0].correlation_id is None


def test_decoder_module_imports_only_offline_dependency_modules() -> None:
    syntax_tree = ast.parse(inspect.getsource(hyperliquid_trades_module))
    imported_modules = {
        (node.level, node.module)
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        (0, alias.name)
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert imported_modules == {
        (0, "collections.abc"),
        (0, "dataclasses"),
        (0, "datetime"),
        (0, "decimal"),
        (0, "json"),
        (0, "typing"),
        (1, "contracts"),
    }


def test_normalizer_validates_trade_runtime_type() -> None:
    with pytest.raises(TypeError):
        normalize_hyperliquid_trade(
            cast(HyperliquidWsTrade, object()),
            instrument_registry=(_instrument(),),
            received_time=_RECEIVED_TIME,
            received_monotonic_ns=1,
            collector_version="collector-v2",
            collector_commit="4ab5128",
            is_gap=False,
        )


def test_received_time_before_event_time_remains_valid() -> None:
    trade = HyperliquidWsTrade.from_payload(_trade_payload())
    envelope = normalize_hyperliquid_trade(
        trade,
        instrument_registry=(_instrument(),),
        received_time=datetime(2020, 1, 1, tzinfo=UTC),
        received_monotonic_ns=0,
        collector_version="collector-v2",
        collector_commit="4ab5128",
        is_gap=False,
    )

    assert envelope.received_time < envelope.event_time
    assert envelope.event == replace(
        envelope.event,
        aggressor_side=AggressorSide.BUY,
    )
