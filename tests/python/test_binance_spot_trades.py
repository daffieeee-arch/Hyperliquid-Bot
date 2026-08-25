"""Deterministic tests for the offline Binance Spot raw-trade boundary."""

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
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

import hyperliquid_bot.binance_spot_trades as binance_spot_trades_module
from hyperliquid_bot.binance_spot_trades import (
    BinanceSpotWsTrade,
    BinanceTimestampUnit,
    decode_binance_spot_trade,
    normalize_binance_spot_trade,
    normalize_binance_spot_trade_payload,
)
from hyperliquid_bot.contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    AggressorSide,
    Instrument,
    InstrumentType,
    MarketEventEnvelope,
    Venue,
)

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "binance" / "spot_trade_event.json"
_RECEIVED_TIME = datetime(2026, 8, 25, 13, 0, tzinfo=UTC)
_DEFAULT_PAYLOAD = object()


def _payload() -> dict[str, object]:
    loaded = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    if type(loaded) is not dict:
        raise AssertionError("fixture root must be an object")
    return cast(dict[str, object], deepcopy(loaded))


def _instrument(
    symbol: str = "BTCUSDT",
    *,
    venue: Venue = Venue.BINANCE,
    instrument_type: InstrumentType = InstrumentType.SPOT,
    venue_market_id: str | None = None,
) -> Instrument:
    return Instrument(
        venue=venue,
        instrument_type=instrument_type,
        base_asset="BTC" if symbol == "BTCUSDT" else "TOKEN",
        quote_asset="USDT",
        venue_market_id=symbol if venue_market_id is None else venue_market_id,
        native_symbol=symbol,
        contract_expiry=(date(2026, 9, 25) if instrument_type is InstrumentType.FUTURE else None),
    )


def _decode(
    payload: object = _DEFAULT_PAYLOAD,
    *,
    timestamp_unit: BinanceTimestampUnit = BinanceTimestampUnit.MILLISECONDS,
) -> BinanceSpotWsTrade:
    return decode_binance_spot_trade(
        _payload() if payload is _DEFAULT_PAYLOAD else payload,
        timestamp_unit=timestamp_unit,
    )


def _normalize(
    trade: BinanceSpotWsTrade,
    *,
    registry: tuple[Instrument, ...] | list[Instrument] | None = None,
    received_time: datetime = _RECEIVED_TIME,
    received_monotonic_ns: int = 987_654_321,
    collector_version: str = "collector-v3",
    collector_commit: str = "faf9d6e",
    is_gap: bool = False,
) -> MarketEventEnvelope:
    return normalize_binance_spot_trade(
        trade,
        instrument_registry=[_instrument(trade.symbol)] if registry is None else registry,
        received_time=received_time,
        received_monotonic_ns=received_monotonic_ns,
        collector_version=collector_version,
        collector_commit=collector_commit,
        is_gap=is_gap,
    )


def test_timestamp_unit_enum_has_exact_supported_values() -> None:
    assert [(unit.name, unit.value) for unit in BinanceTimestampUnit] == [
        ("MILLISECONDS", "MILLISECONDS"),
        ("MICROSECONDS", "MICROSECONDS"),
    ]


def test_exact_fixture_decodes_all_nine_documented_fields() -> None:
    assert set(_payload()) == {"e", "E", "s", "t", "p", "q", "T", "m", "M"}

    trade = _decode()

    assert trade.event_type == "trade"
    assert trade.exchange_event_time == 1_720_000_000_124
    assert trade.symbol == "BTCUSDT"
    assert trade.trade_id == 424_242
    assert trade.price.as_tuple() == Decimal("12345.678900000000000001").as_tuple()
    assert trade.quantity.as_tuple() == Decimal("0.000000010000000000").as_tuple()
    assert trade.trade_time == 1_720_000_000_123
    assert trade.buyer_was_maker is True
    assert trade.ignore_flag is True
    assert trade.timestamp_unit is BinanceTimestampUnit.MILLISECONDS
    assert trade.exchange_event_time_utc == datetime(2024, 7, 3, 9, 46, 40, 124_000, tzinfo=UTC)
    assert trade.trade_time_utc == datetime(2024, 7, 3, 9, 46, 40, 123_000, tzinfo=UTC)
    assert trade.aggressor_side is AggressorSide.SELL


def test_dto_is_frozen_slotted_hashable_and_deterministic() -> None:
    first = _decode()
    second = _decode()

    assert not hasattr(first, "__dict__")
    assert first == second
    assert hash(first) == hash(second)
    with pytest.raises(FrozenInstanceError):
        first.symbol = "ETHUSDT"  # type: ignore[misc]


def test_direct_dto_construction_matches_decoder_and_revalidates() -> None:
    decoded = _decode()
    direct = BinanceSpotWsTrade(
        event_type="trade",
        exchange_event_time=1_720_000_000_124,
        symbol="BTCUSDT",
        trade_id=424_242,
        price=Decimal("12345.678900000000000001"),
        quantity=Decimal("0.000000010000000000"),
        trade_time=1_720_000_000_123,
        buyer_was_maker=True,
        ignore_flag=True,
        timestamp_unit=BinanceTimestampUnit.MILLISECONDS,
    )

    assert direct == decoded
    assert hash(direct) == hash(decoded)


@pytest.mark.parametrize(
    "field_name,invalid_value,expected_error",
    [
        pytest.param("event_type", "Trade", ValueError, id="event-type"),
        pytest.param("exchange_event_time", True, TypeError, id="exchange-time"),
        pytest.param("symbol", "", ValueError, id="symbol"),
        pytest.param("trade_id", -1, ValueError, id="trade-id"),
        pytest.param("price", "1", TypeError, id="price"),
        pytest.param("quantity", Decimal("0"), ValueError, id="quantity"),
        pytest.param("trade_time", 1.0, TypeError, id="trade-time"),
        pytest.param("buyer_was_maker", 1, TypeError, id="maker"),
        pytest.param("ignore_flag", 0, TypeError, id="ignore"),
        pytest.param("timestamp_unit", "MILLISECONDS", TypeError, id="unit"),
    ],
)
def test_direct_dto_construction_revalidates_every_input(
    field_name: str,
    invalid_value: object,
    expected_error: type[Exception],
) -> None:
    trade = _decode()

    with pytest.raises(expected_error):
        replace(trade, **cast(Any, {field_name: invalid_value}))


@pytest.mark.parametrize(
    "price,quantity",
    [
        pytest.param("1", "1", id="integers-as-strings"),
        pytest.param("+1.2300", "1E-18", id="sign-exponent-and-trailing-zeroes"),
        pytest.param("000001.000", "999999999999.000001", id="leading-and-large"),
        pytest.param(" 1.25 ", "\t2.50\n", id="decimal-compatible-whitespace"),
    ],
)
def test_valid_decimal_strings_are_not_subject_to_an_invented_grammar(
    price: str,
    quantity: str,
) -> None:
    payload = _payload()
    payload["p"] = price
    payload["q"] = quantity

    trade = _decode(payload)

    assert trade.price == Decimal(price)
    assert trade.quantity == Decimal(quantity)
    assert trade.price.as_tuple() == Decimal(price).as_tuple()
    assert trade.quantity.as_tuple() == Decimal(quantity).as_tuple()


def test_millisecond_conversion_uses_exact_integer_arithmetic() -> None:
    payload = _payload()
    payload["E"] = 1_234
    payload["T"] = 1_001

    trade = _decode(payload, timestamp_unit=BinanceTimestampUnit.MILLISECONDS)

    assert trade.exchange_event_time_utc == datetime(1970, 1, 1, 0, 0, 1, 234_000, tzinfo=UTC)
    assert trade.trade_time_utc == datetime(1970, 1, 1, 0, 0, 1, 1_000, tzinfo=UTC)


def test_microsecond_conversion_uses_exact_integer_arithmetic() -> None:
    payload = _payload()
    payload["E"] = 1_234_567
    payload["T"] = 1_000_001

    trade = _decode(payload, timestamp_unit=BinanceTimestampUnit.MICROSECONDS)

    assert trade.exchange_event_time_utc == datetime(1970, 1, 1, 0, 0, 1, 234_567, tzinfo=UTC)
    assert trade.trade_time_utc == datetime(1970, 1, 1, 0, 0, 1, 1, tzinfo=UTC)


def test_equivalent_millisecond_and_microsecond_instants_normalize_identically() -> None:
    milliseconds = _payload()
    microseconds = _payload()
    microseconds["E"] = cast(int, microseconds["E"]) * 1_000
    microseconds["T"] = cast(int, microseconds["T"]) * 1_000

    millisecond_event = _normalize(
        _decode(milliseconds, timestamp_unit=BinanceTimestampUnit.MILLISECONDS)
    )
    microsecond_event = _normalize(
        _decode(microseconds, timestamp_unit=BinanceTimestampUnit.MICROSECONDS)
    )

    assert millisecond_event == microsecond_event
    assert hash(millisecond_event) == hash(microsecond_event)


def test_timestamp_unit_is_never_inferred_from_integer_magnitude() -> None:
    payload = _payload()
    payload["E"] = payload["T"] = 1_000_000

    milliseconds = _decode(payload, timestamp_unit=BinanceTimestampUnit.MILLISECONDS)
    microseconds = _decode(payload, timestamp_unit=BinanceTimestampUnit.MICROSECONDS)

    assert milliseconds.trade_time_utc == datetime(1970, 1, 1, 0, 16, 40, tzinfo=UTC)
    assert microseconds.trade_time_utc == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)
    assert milliseconds.trade_time_utc != microseconds.trade_time_utc


@pytest.mark.parametrize(
    "unit,maximum,expected",
    [
        pytest.param(
            BinanceTimestampUnit.MILLISECONDS,
            253_402_300_799_999,
            datetime(9999, 12, 31, 23, 59, 59, 999_000, tzinfo=UTC),
            id="milliseconds",
        ),
        pytest.param(
            BinanceTimestampUnit.MICROSECONDS,
            253_402_300_799_999_999,
            datetime(9999, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC),
            id="microseconds",
        ),
    ],
)
def test_epoch_and_maximum_supported_timestamp_are_exact(
    unit: BinanceTimestampUnit,
    maximum: int,
    expected: datetime,
) -> None:
    epoch_payload = _payload()
    epoch_payload["E"] = epoch_payload["T"] = 0
    maximum_payload = _payload()
    maximum_payload["E"] = maximum_payload["T"] = maximum

    epoch = _decode(epoch_payload, timestamp_unit=unit)
    latest = _decode(maximum_payload, timestamp_unit=unit)

    assert epoch.exchange_event_time_utc == epoch.trade_time_utc == datetime(1970, 1, 1, tzinfo=UTC)
    assert latest.exchange_event_time_utc == latest.trade_time_utc == expected


@pytest.mark.parametrize(
    "buyer_was_maker,expected_side",
    [
        pytest.param(True, AggressorSide.SELL, id="buyer-maker-seller-aggressor"),
        pytest.param(False, AggressorSide.BUY, id="buyer-aggressor"),
    ],
)
def test_buyer_maker_flag_maps_to_aggressor_side(
    buyer_was_maker: bool,
    expected_side: AggressorSide,
) -> None:
    payload = _payload()
    payload["m"] = buyer_was_maker

    trade = _decode(payload)
    envelope = _normalize(trade)

    assert trade.aggressor_side is expected_side
    assert envelope.event.aggressor_side is expected_side


def test_exact_instrument_resolution_and_schema_v2_envelope() -> None:
    trade = _decode()
    instrument = _instrument()

    envelope = _normalize(trade, registry=[instrument])

    assert envelope.schema_version == MARKET_EVENT_SCHEMA_VERSION == 2
    assert envelope.instrument is instrument
    assert envelope.event.price is trade.price
    assert envelope.event.quantity is trade.quantity
    assert envelope.event.aggressor_side is AggressorSide.SELL
    assert envelope.event_time == trade.trade_time_utc
    assert envelope.event_time != trade.exchange_event_time_utc
    assert envelope.received_time == _RECEIVED_TIME
    assert envelope.received_monotonic_ns == 987_654_321
    assert envelope.collector_version == "collector-v3"
    assert envelope.collector_commit == "faf9d6e"
    assert envelope.is_gap is False
    assert envelope.source_event_id == '["binance-spot-trade-v1","BTCUSDT",424242]'
    assert envelope.source_transaction_id is None
    assert envelope.source_sequence is None
    assert envelope.correlation_id is None


def test_convenience_function_decodes_and_normalizes_one_payload() -> None:
    instrument = _instrument()

    envelope = normalize_binance_spot_trade_payload(
        _payload(),
        timestamp_unit=BinanceTimestampUnit.MILLISECONDS,
        instrument_registry=(instrument,),
        received_time=_RECEIVED_TIME,
        received_monotonic_ns=987_654_321,
        collector_version="collector-v3",
        collector_commit="faf9d6e",
        is_gap=True,
    )

    assert envelope.instrument is instrument
    assert envelope.is_gap is True
    assert envelope.source_event_id == '["binance-spot-trade-v1","BTCUSDT",424242]'


def test_source_identity_is_deterministic_and_changes_only_with_symbol_or_trade_id() -> None:
    baseline_payload = _payload()
    duplicate = deepcopy(baseline_payload)
    changed_symbol = deepcopy(baseline_payload)
    changed_symbol["s"] = "ETHUSDT"
    changed_trade_id = deepcopy(baseline_payload)
    changed_trade_id["t"] = cast(int, changed_trade_id["t"]) + 1
    changed_non_identity = deepcopy(baseline_payload)
    changed_non_identity["E"] = cast(int, changed_non_identity["E"]) + 99
    changed_non_identity["M"] = False

    registry = [_instrument("ETHUSDT"), _instrument("BTCUSDT")]
    baseline = _normalize(_decode(baseline_payload), registry=registry)
    repeated = _normalize(_decode(duplicate), registry=list(reversed(registry)))
    symbol_variant = _normalize(_decode(changed_symbol), registry=registry)
    trade_id_variant = _normalize(_decode(changed_trade_id), registry=registry)
    non_identity_variant = _normalize(_decode(changed_non_identity), registry=registry)

    assert baseline.source_event_id == repeated.source_event_id
    assert baseline.source_event_id == non_identity_variant.source_event_id
    assert symbol_variant.source_event_id != baseline.source_event_id
    assert trade_id_variant.source_event_id != baseline.source_event_id
    assert json.loads(baseline.source_event_id) == [
        "binance-spot-trade-v1",
        "BTCUSDT",
        424_242,
    ]


@pytest.mark.parametrize(
    "field_name,new_value",
    [
        pytest.param("p", "54321.000000000000000002", id="price"),
        pytest.param("q", "0.000000020000000000", id="quantity"),
        pytest.param("T", 1_720_000_000_125, id="trade-time"),
        pytest.param("m", False, id="buyer-was-maker"),
    ],
)
def test_source_identity_ignores_valid_non_identity_trade_semantics(
    field_name: str,
    new_value: object,
) -> None:
    baseline_payload = _payload()
    changed_payload = deepcopy(baseline_payload)
    changed_payload[field_name] = new_value

    baseline = _normalize(_decode(baseline_payload))
    changed = _normalize(_decode(changed_payload))

    assert baseline.source_event_id == changed.source_event_id
    assert baseline.source_event_id == '["binance-spot-trade-v1","BTCUSDT",424242]'
    assert baseline != changed


def test_source_identity_ignores_receipt_collector_gap_and_timestamp_unit_metadata() -> None:
    milliseconds_payload = _payload()
    microseconds_payload = _payload()
    microseconds_payload["E"] = cast(int, microseconds_payload["E"]) * 1_000
    microseconds_payload["T"] = cast(int, microseconds_payload["T"]) * 1_000
    milliseconds = _decode(
        milliseconds_payload,
        timestamp_unit=BinanceTimestampUnit.MILLISECONDS,
    )
    microseconds = _decode(
        microseconds_payload,
        timestamp_unit=BinanceTimestampUnit.MICROSECONDS,
    )

    first = _normalize(milliseconds)
    second = _normalize(
        microseconds,
        received_time=datetime(2020, 1, 1, tzinfo=UTC),
        received_monotonic_ns=0,
        collector_version="another-version",
        collector_commit="another-commit",
        is_gap=True,
    )

    assert first.source_event_id == second.source_event_id
    assert first.event_time == second.event_time
    assert first != second


def test_exchange_event_time_and_ignore_flag_do_not_change_normalized_trade() -> None:
    baseline_payload = _payload()
    changed_payload = _payload()
    changed_payload["E"] = cast(int, changed_payload["E"]) + 10_000
    changed_payload["M"] = False

    baseline_trade = _decode(baseline_payload)
    changed_trade = _decode(changed_payload)
    baseline = _normalize(baseline_trade)
    changed = _normalize(changed_trade)

    assert baseline_trade.exchange_event_time != changed_trade.exchange_event_time
    assert baseline_trade.ignore_flag is not changed_trade.ignore_flag
    assert baseline == changed
    assert baseline.source_event_id == changed.source_event_id


@pytest.mark.parametrize(
    "exchange_event_time,trade_time",
    [
        pytest.param(999, 1_000, id="exchange-event-before-trade"),
        pytest.param(1_000, 1_000, id="equal"),
        pytest.param(1_001, 1_000, id="exchange-event-after-trade"),
    ],
)
def test_all_exchange_event_and_trade_time_orderings_are_representable(
    exchange_event_time: int,
    trade_time: int,
) -> None:
    payload = _payload()
    payload["E"] = exchange_event_time
    payload["T"] = trade_time

    trade = _decode(payload)

    assert trade.exchange_event_time == exchange_event_time
    assert trade.trade_time == trade_time


def test_receipt_time_before_exchange_times_and_metadata_passthrough_remain_valid() -> None:
    received_time = datetime(2020, 1, 1, tzinfo=UTC)

    envelope = _normalize(
        _decode(),
        received_time=received_time,
        received_monotonic_ns=0,
        collector_version="collector-build-42",
        collector_commit="commit-42",
        is_gap=True,
    )

    assert envelope.received_time == received_time
    assert envelope.received_time < envelope.event_time
    assert envelope.received_monotonic_ns == 0
    assert envelope.collector_version == "collector-build-42"
    assert envelope.collector_commit == "commit-42"
    assert envelope.is_gap is True


def test_unknown_additive_fields_are_tolerated_and_discarded() -> None:
    payload = _payload()
    payload["futureField"] = {"nested": [1, 2, 3]}

    decoded = _decode(payload)

    assert decoded == _decode()
    assert not hasattr(decoded, "futureField")


def test_non_ascii_printable_symbol_is_preserved_and_resolved_without_inference() -> None:
    payload = _payload()
    payload["s"] = "ÆTHUSDT"
    instrument = _instrument("ÆTHUSDT")

    trade = _decode(payload)
    envelope = _normalize(trade, registry=[instrument])

    assert trade.symbol == "ÆTHUSDT"
    assert envelope.instrument is instrument
    assert json.loads(envelope.source_event_id)[1] == "ÆTHUSDT"


def test_large_non_negative_trade_id_is_accepted_without_invented_sequence_semantics() -> None:
    payload = _payload()
    payload["t"] = 10**100

    envelope = _normalize(_decode(payload))

    assert json.loads(envelope.source_event_id)[2] == 10**100
    assert envelope.source_sequence is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="none"),
        pytest.param([], id="list"),
        pytest.param((), id="tuple"),
        pytest.param("trade", id="string"),
        pytest.param(object(), id="object"),
    ],
)
def test_payload_requires_exact_builtin_dict(payload: object) -> None:
    with pytest.raises(TypeError):
        _decode(payload)


def test_payload_rejects_dict_subclasses() -> None:
    class DictSubclass(dict[str, object]):
        pass

    with pytest.raises(TypeError):
        _decode(DictSubclass(_payload()))


def test_wire_scalar_fields_reject_builtin_subclasses() -> None:
    class StringSubclass(str):
        pass

    class IntegerSubclass(int):
        pass

    for field_name, invalid_value in (
        ("e", StringSubclass("trade")),
        ("s", StringSubclass("BTCUSDT")),
        ("p", StringSubclass("1")),
        ("q", StringSubclass("1")),
        ("E", IntegerSubclass(1)),
        ("t", IntegerSubclass(1)),
        ("T", IntegerSubclass(1)),
    ):
        payload = _payload()
        payload[field_name] = invalid_value
        with pytest.raises(TypeError):
            _decode(payload)


def test_combined_stream_wrapper_is_not_accepted_by_raw_event_decoder() -> None:
    wrapper = {"stream": "btcusdt@trade", "data": _payload()}

    with pytest.raises(ValueError, match="required field e"):
        _decode(wrapper)


@pytest.mark.parametrize("event_type", ["aggTrade", "blockTrade", "Trade", "TRADE", " trade"])
def test_only_exact_raw_trade_event_type_is_accepted(event_type: str) -> None:
    payload = _payload()
    payload["e"] = event_type

    with pytest.raises(ValueError, match="exactly trade"):
        _decode(payload)


@pytest.mark.parametrize("missing_field", ["e", "E", "s", "t", "p", "q", "T", "m", "M"])
def test_every_documented_wire_field_is_required(missing_field: str) -> None:
    payload = _payload()
    del payload[missing_field]

    with pytest.raises(ValueError, match=missing_field):
        _decode(payload)


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        pytest.param("e", 1, id="event-integer"),
        pytest.param("E", "1720000000124", id="event-time-string"),
        pytest.param("s", 1, id="symbol-integer"),
        pytest.param("t", "424242", id="trade-id-string"),
        pytest.param("p", Decimal("1"), id="price-decimal"),
        pytest.param("q", None, id="quantity-none"),
        pytest.param("T", "1720000000123", id="trade-time-string"),
        pytest.param("m", 1, id="maker-integer"),
        pytest.param("M", "true", id="ignore-string"),
    ],
)
def test_each_wire_field_rejects_a_wrong_runtime_type(
    field_name: str,
    invalid_value: object,
) -> None:
    payload = _payload()
    payload[field_name] = invalid_value

    with pytest.raises(TypeError):
        _decode(payload)


@pytest.mark.parametrize("field_name", ["E", "t", "T"])
@pytest.mark.parametrize("invalid_value", [True, False, 1.0, 1.5])
def test_integer_fields_reject_booleans_and_floats(
    field_name: str,
    invalid_value: object,
) -> None:
    payload = _payload()
    payload[field_name] = invalid_value

    with pytest.raises(TypeError):
        _decode(payload)


@pytest.mark.parametrize("field_name", ["p", "q"])
@pytest.mark.parametrize(
    "invalid_value",
    [
        pytest.param(1, id="integer"),
        pytest.param(1.0, id="float"),
        pytest.param(True, id="boolean"),
    ],
)
def test_decimal_fields_reject_json_numbers_and_booleans(
    field_name: str,
    invalid_value: object,
) -> None:
    payload = _payload()
    payload[field_name] = invalid_value

    with pytest.raises(TypeError):
        _decode(payload)


@pytest.mark.parametrize(
    "invalid_symbol", ["", " ", " BTCUSDT", "BTCUSDT ", "BTC\nUSDT", "BTC\x00USDT"]
)
def test_symbol_rejects_empty_outer_whitespace_or_control_characters(
    invalid_symbol: str,
) -> None:
    payload = _payload()
    payload["s"] = invalid_symbol

    with pytest.raises(ValueError):
        _decode(payload)


@pytest.mark.parametrize("field_name", ["p", "q"])
@pytest.mark.parametrize(
    "invalid_value",
    [
        pytest.param("", id="empty"),
        pytest.param("not-a-number", id="malformed"),
        pytest.param("0", id="zero"),
        pytest.param("-0", id="negative-zero"),
        pytest.param("-1", id="negative"),
        pytest.param("NaN", id="nan"),
        pytest.param("sNaN", id="signaling-nan"),
        pytest.param("Infinity", id="positive-infinity"),
        pytest.param("-Infinity", id="negative-infinity"),
    ],
)
def test_price_and_quantity_reject_malformed_nonpositive_or_nonfinite_strings(
    field_name: str,
    invalid_value: str,
) -> None:
    payload = _payload()
    payload[field_name] = invalid_value

    with pytest.raises(ValueError):
        _decode(payload)


@pytest.mark.parametrize("field_name", ["E", "T"])
def test_negative_timestamps_fail_closed(field_name: str) -> None:
    payload = _payload()
    payload[field_name] = -1

    with pytest.raises(ValueError, match="non-negative"):
        _decode(payload)


@pytest.mark.parametrize(
    "unit,overflow",
    [
        pytest.param(
            BinanceTimestampUnit.MILLISECONDS,
            253_402_300_800_000,
            id="milliseconds",
        ),
        pytest.param(
            BinanceTimestampUnit.MICROSECONDS,
            253_402_300_800_000_000,
            id="microseconds",
        ),
        pytest.param(BinanceTimestampUnit.MILLISECONDS, 10**100, id="huge"),
    ],
)
@pytest.mark.parametrize("field_name", ["E", "T"])
def test_out_of_range_timestamps_fail_cleanly(
    unit: BinanceTimestampUnit,
    overflow: int,
    field_name: str,
) -> None:
    payload = _payload()
    payload[field_name] = overflow

    with pytest.raises(ValueError, match=field_name):
        _decode(payload, timestamp_unit=unit)


def test_negative_trade_id_fails_closed() -> None:
    payload = _payload()
    payload["t"] = -1

    with pytest.raises(ValueError, match="non-negative"):
        _decode(payload)


@pytest.mark.parametrize("field_name", ["m", "M"])
@pytest.mark.parametrize("invalid_value", [0, 1, "true", None, 0.0])
def test_boolean_fields_require_exact_builtin_booleans(
    field_name: str,
    invalid_value: object,
) -> None:
    payload = _payload()
    payload[field_name] = invalid_value

    with pytest.raises(TypeError):
        _decode(payload)


@pytest.mark.parametrize(
    "invalid_unit",
    [
        pytest.param("MILLISECONDS", id="string"),
        pytest.param("MICROSECONDS", id="microseconds-string"),
        pytest.param("NANOSECONDS", id="unsupported"),
        pytest.param(None, id="none"),
        pytest.param(1, id="integer"),
    ],
)
def test_timestamp_unit_requires_exact_supported_enum(invalid_unit: object) -> None:
    with pytest.raises(TypeError):
        decode_binance_spot_trade(
            _payload(),
            timestamp_unit=cast(BinanceTimestampUnit, invalid_unit),
        )


def test_unknown_and_case_variant_instruments_fail_closed() -> None:
    trade = _decode()

    with pytest.raises(LookupError, match="not registered"):
        _normalize(trade, registry=[])
    with pytest.raises(LookupError, match="not registered"):
        _normalize(trade, registry=[_instrument("btcusdt")])


def test_multiple_exact_binance_spot_matches_are_ambiguous() -> None:
    trade = _decode()
    registry = [
        _instrument(venue_market_id="BTCUSDT-primary"),
        _instrument(venue_market_id="BTCUSDT-secondary"),
    ]

    with pytest.raises(LookupError, match="ambiguous"):
        _normalize(trade, registry=registry)


@pytest.mark.parametrize(
    "instrument",
    [
        pytest.param(_instrument(venue=Venue.HYPERLIQUID), id="wrong-venue"),
        pytest.param(
            _instrument(instrument_type=InstrumentType.PERPETUAL),
            id="binance-perpetual",
        ),
        pytest.param(
            _instrument(instrument_type=InstrumentType.FUTURE),
            id="binance-future",
        ),
    ],
)
def test_same_symbol_wrong_venue_or_instrument_type_fails_closed(
    instrument: Instrument,
) -> None:
    with pytest.raises(LookupError, match="not registered"):
        _normalize(_decode(), registry=[instrument])


def test_multi_venue_registry_resolves_only_the_exact_binance_spot_match() -> None:
    trade = _decode()
    valid = _instrument()
    registry = [
        _instrument(venue=Venue.HYPERLIQUID),
        _instrument(instrument_type=InstrumentType.PERPETUAL),
        _instrument("ETHUSDT"),
        valid,
    ]

    assert _normalize(trade, registry=registry).instrument is valid


def test_registry_rejects_non_instrument_members_even_after_a_valid_match() -> None:
    registry = cast(list[Instrument], [_instrument(), object()])

    with pytest.raises(TypeError, match="only Instrument"):
        _normalize(_decode(), registry=registry)


def test_normalizer_requires_exact_binance_dto_type() -> None:
    with pytest.raises(TypeError, match="BinanceSpotWsTrade"):
        normalize_binance_spot_trade(
            cast(BinanceSpotWsTrade, object()),
            instrument_registry=[_instrument()],
            received_time=_RECEIVED_TIME,
            received_monotonic_ns=1,
            collector_version="collector-v3",
            collector_commit="faf9d6e",
            is_gap=False,
        )


def test_decoder_and_normalizer_use_no_clock_network_filesystem_or_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    instrument = _instrument()

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
        isolated.setattr(binance_spot_trades_module, "datetime", UnexpectedDateTime)
        envelope = normalize_binance_spot_trade_payload(
            payload,
            timestamp_unit=BinanceTimestampUnit.MILLISECONDS,
            instrument_registry=[instrument],
            received_time=_RECEIVED_TIME,
            received_monotonic_ns=1,
            collector_version="collector-v3",
            collector_commit="faf9d6e",
            is_gap=False,
        )

    assert envelope.instrument is instrument
    assert envelope.received_time == _RECEIVED_TIME


def test_decoder_module_imports_only_offline_standard_library_and_contracts() -> None:
    syntax_tree = ast.parse(inspect.getsource(binance_spot_trades_module))
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
        (0, "enum"),
        (0, "json"),
        (0, "typing"),
        (1, "contracts"),
    }
    assert not any(
        forbidden in binance_spot_trades_module.__dict__
        for forbidden in (
            "aiohttp",
            "httpx",
            "logging",
            "os",
            "pathlib",
            "requests",
            "socket",
            "urllib",
            "websockets",
        )
    )
