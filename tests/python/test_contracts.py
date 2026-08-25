"""Deterministic tests for the Phase 1A-1 market-data contracts."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

import pytest

from hyperliquid_bot.contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    Instrument,
    InstrumentType,
    MarketEventEnvelope,
    TradeEvent,
    Venue,
)


def _instrument() -> Instrument:
    return Instrument(
        venue=Venue.BITVAVO,
        instrument_type=InstrumentType.SPOT,
        base_asset="SOL",
        quote_asset="EUR",
        venue_market_id="SOL-EUR",
        native_symbol="SOL-EUR",
    )


def _trade() -> TradeEvent:
    return TradeEvent(price=Decimal("123.4500"), quantity=Decimal("0.125"))


def _envelope() -> MarketEventEnvelope:
    return MarketEventEnvelope(
        schema_version=MARKET_EVENT_SCHEMA_VERSION,
        instrument=_instrument(),
        event=_trade(),
        event_time=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        received_time=datetime(2026, 8, 25, 12, 0, 0, 1, tzinfo=UTC),
        received_monotonic_ns=0,
        collector_version="collector-v1",
        collector_commit="9ab3c0e",
        is_gap=False,
        source_sequence=42,
        correlation_id="trade-42",
    )


def test_enum_members_are_exactly_the_documented_values() -> None:
    assert [(venue.name, venue.value) for venue in Venue] == [
        ("HYPERLIQUID", "hyperliquid"),
        ("BITVAVO", "bitvavo"),
        ("KRAKEN", "kraken"),
        ("BINANCE", "binance"),
    ]
    assert [
        (instrument_type.name, instrument_type.value) for instrument_type in InstrumentType
    ] == [
        ("SPOT", "spot"),
        ("PERPETUAL", "perpetual"),
        ("FUTURE", "future"),
    ]


def test_instrument_generates_unambiguous_versioned_id_and_preserves_native_symbol() -> None:
    instrument = _instrument()

    assert instrument.canonical_instrument_id == (
        '["instrument-v1","bitvavo","spot","SOL-EUR","SOL","EUR",null]'
    )
    assert instrument.venue_market_id == "SOL-EUR"
    assert instrument.native_symbol == "SOL-EUR"


def test_native_symbol_aliases_share_identity_for_the_same_venue_market_id() -> None:
    first = _instrument()
    second = Instrument(
        venue=Venue.BITVAVO,
        instrument_type=InstrumentType.SPOT,
        base_asset="SOL",
        quote_asset="EUR",
        venue_market_id="SOL-EUR",
        native_symbol="SOLEUR",
    )

    assert first.canonical_instrument_id == second.canonical_instrument_id
    assert first.native_symbol != second.native_symbol
    assert first == second
    assert hash(first) == hash(second)


def test_hip3_dex_namespace_disambiguates_markets_with_the_same_pair() -> None:
    first_dex = Instrument(
        venue=Venue.HYPERLIQUID,
        instrument_type=InstrumentType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDC",
        venue_market_id="builder-a:BTC",
        native_symbol="builder-a:BTC",
    )
    second_dex = Instrument(
        venue=Venue.HYPERLIQUID,
        instrument_type=InstrumentType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDC",
        venue_market_id="builder-b:BTC",
        native_symbol="builder-b:BTC",
    )

    assert first_dex.canonical_instrument_id == (
        '["instrument-v1","hyperliquid","perpetual","builder-a:BTC","BTC","USDC",null]'
    )
    assert second_dex.canonical_instrument_id == (
        '["instrument-v1","hyperliquid","perpetual","builder-b:BTC","BTC","USDC",null]'
    )
    assert first_dex.canonical_instrument_id != second_dex.canonical_instrument_id
    assert first_dex != second_dex


def test_future_identity_uses_concrete_market_id_and_normalized_expiry() -> None:
    september_future = Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.FUTURE,
        base_asset="BTC",
        quote_asset="USDT",
        venue_market_id="BTCUSDT_260925",
        native_symbol="BTCUSDT_260925",
        contract_expiry=date(2026, 9, 25),
    )
    december_future = Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.FUTURE,
        base_asset="BTC",
        quote_asset="USDT",
        venue_market_id="BTCUSDT_261225",
        native_symbol="BTCUSDT_261225",
        contract_expiry=date(2026, 12, 25),
    )

    assert september_future.canonical_instrument_id == (
        '["instrument-v1","binance","future","BTCUSDT_260925","BTC","USDT","2026-09-25"]'
    )
    assert december_future.canonical_instrument_id == (
        '["instrument-v1","binance","future","BTCUSDT_261225","BTC","USDT","2026-12-25"]'
    )
    assert september_future.canonical_instrument_id != december_future.canonical_instrument_id
    assert september_future != december_future


def test_future_requires_contract_expiry() -> None:
    with pytest.raises(ValueError):
        Instrument(
            venue=Venue.BINANCE,
            instrument_type=InstrumentType.FUTURE,
            base_asset="BTC",
            quote_asset="USDT",
            venue_market_id="BTCUSDT_260925",
            native_symbol="BTCUSDT_260925",
        )


@pytest.mark.parametrize("instrument_type", [InstrumentType.SPOT, InstrumentType.PERPETUAL])
def test_contract_expiry_is_forbidden_for_non_futures(
    instrument_type: InstrumentType,
) -> None:
    with pytest.raises(ValueError):
        Instrument(
            venue=Venue.BITVAVO,
            instrument_type=instrument_type,
            base_asset="SOL",
            quote_asset="EUR",
            venue_market_id="SOL-EUR",
            native_symbol="SOL-EUR",
            contract_expiry=date(2026, 9, 25),
        )


@pytest.mark.parametrize(
    "invalid_expiry",
    [
        pytest.param("2026-09-25", id="iso-string"),
        pytest.param("20260925", id="compact-date"),
        pytest.param("2026-W39-5", id="iso-week-date"),
        pytest.param("2026-9-25", id="non-padded"),
        pytest.param("25-09-2026", id="day-first"),
        pytest.param("2026-02-29", id="invalid-non-leap-day"),
        pytest.param("2026-04-31", id="invalid-month-day"),
        pytest.param("0000-01-01", id="invalid-year"),
        pytest.param(1, id="integer"),
        pytest.param(True, id="boolean"),
        pytest.param(datetime(2026, 9, 25, tzinfo=UTC), id="datetime-subclass"),
    ],
)
def test_future_rejects_non_date_contract_expiry(
    invalid_expiry: object,
) -> None:
    with pytest.raises(TypeError):
        Instrument(
            venue=Venue.BINANCE,
            instrument_type=InstrumentType.FUTURE,
            base_asset="BTC",
            quote_asset="USDT",
            venue_market_id="BTCUSDT_260925",
            native_symbol="BTCUSDT_260925",
            contract_expiry=cast(date, invalid_expiry),
        )


def test_future_accepts_a_valid_leap_day() -> None:
    instrument = Instrument(
        venue=Venue.BINANCE,
        instrument_type=InstrumentType.FUTURE,
        base_asset="BTC",
        quote_asset="USDT",
        venue_market_id="BTCUSDT_280229",
        native_symbol="BTCUSDT_280229",
        contract_expiry=date(2028, 2, 29),
    )

    assert instrument.contract_expiry == date(2028, 2, 29)
    assert instrument.canonical_instrument_id.endswith('"2028-02-29"]')


def test_identical_contract_input_is_deterministic_and_hashable() -> None:
    first = _instrument()
    second = _instrument()

    assert first == second
    assert hash(first) == hash(second)
    assert first.canonical_instrument_id == second.canonical_instrument_id


@pytest.mark.parametrize(
    "invalid_asset",
    [
        pytest.param("", id="empty"),
        pytest.param(" ", id="whitespace"),
        pytest.param(" sol", id="leading-whitespace"),
        pytest.param("SOL ", id="trailing-whitespace"),
        pytest.param("sol", id="lowercase"),
        pytest.param("SOL-EUR", id="id-separator"),
        pytest.param("SOL:EUR", id="section-separator"),
        pytest.param("SOL/EUR", id="native-separator"),
    ],
)
def test_instrument_rejects_noncanonical_asset_codes(invalid_asset: str) -> None:
    with pytest.raises(ValueError):
        Instrument(
            venue=Venue.BITVAVO,
            instrument_type=InstrumentType.SPOT,
            base_asset=invalid_asset,
            quote_asset="EUR",
            venue_market_id="SOL-EUR",
            native_symbol="SOL-EUR",
        )

    with pytest.raises(ValueError):
        Instrument(
            venue=Venue.BITVAVO,
            instrument_type=InstrumentType.SPOT,
            base_asset="SOL",
            quote_asset=invalid_asset,
            venue_market_id="SOL-EUR",
            native_symbol="SOL-EUR",
        )


@pytest.mark.parametrize(
    "native_symbol",
    [
        pytest.param("", id="empty"),
        pytest.param(" ", id="whitespace"),
        pytest.param(" SOL-EUR", id="leading-whitespace"),
        pytest.param("SOL-EUR\n", id="control-character"),
    ],
)
def test_instrument_rejects_invalid_native_symbols(native_symbol: str) -> None:
    with pytest.raises(ValueError):
        Instrument(
            venue=Venue.BITVAVO,
            instrument_type=InstrumentType.SPOT,
            base_asset="SOL",
            quote_asset="EUR",
            venue_market_id="SOL-EUR",
            native_symbol=native_symbol,
        )


@pytest.mark.parametrize(
    "venue_market_id",
    [
        pytest.param("", id="empty"),
        pytest.param(" ", id="whitespace"),
        pytest.param(" builder:BTC", id="leading-whitespace"),
        pytest.param("builder:BTC ", id="trailing-whitespace"),
        pytest.param("builder:BTC\n", id="control-character"),
    ],
)
def test_instrument_rejects_invalid_venue_market_ids(venue_market_id: str) -> None:
    with pytest.raises(ValueError):
        replace(_instrument(), venue_market_id=venue_market_id)


def test_instrument_rejects_non_string_venue_market_id() -> None:
    with pytest.raises(TypeError):
        replace(_instrument(), venue_market_id=cast(str, 1))


def test_instrument_rejects_invalid_runtime_types_and_identical_assets() -> None:
    with pytest.raises(TypeError):
        Instrument(
            venue=cast(Venue, "bitvavo"),
            instrument_type=InstrumentType.SPOT,
            base_asset="SOL",
            quote_asset="EUR",
            venue_market_id="SOL-EUR",
            native_symbol="SOL-EUR",
        )

    with pytest.raises(TypeError):
        Instrument(
            venue=Venue.BITVAVO,
            instrument_type=cast(InstrumentType, "spot"),
            base_asset="SOL",
            quote_asset="EUR",
            venue_market_id="SOL-EUR",
            native_symbol="SOL-EUR",
        )

    with pytest.raises(TypeError):
        Instrument(
            venue=Venue.BITVAVO,
            instrument_type=InstrumentType.SPOT,
            base_asset=cast(str, 1),
            quote_asset="EUR",
            venue_market_id="SOL-EUR",
            native_symbol="SOL-EUR",
        )

    with pytest.raises(TypeError):
        Instrument(
            venue=Venue.BITVAVO,
            instrument_type=InstrumentType.SPOT,
            base_asset="SOL",
            quote_asset="EUR",
            venue_market_id="SOL-EUR",
            native_symbol=cast(str, b"SOL-EUR"),
        )

    with pytest.raises(ValueError):
        Instrument(
            venue=Venue.BITVAVO,
            instrument_type=InstrumentType.SPOT,
            base_asset="SOL",
            quote_asset="SOL",
            venue_market_id="SOL-SOL",
            native_symbol="SOL-SOL",
        )


@pytest.mark.parametrize(
    "invalid_value",
    [
        pytest.param(1.0, id="float"),
        pytest.param(1, id="integer"),
        pytest.param(True, id="boolean"),
        pytest.param("1.0", id="string"),
    ],
)
def test_trade_rejects_non_decimal_financial_values(invalid_value: object) -> None:
    with pytest.raises(TypeError):
        TradeEvent(price=cast(Decimal, invalid_value), quantity=Decimal("1"))

    with pytest.raises(TypeError):
        TradeEvent(price=Decimal("1"), quantity=cast(Decimal, invalid_value))


@pytest.mark.parametrize(
    "invalid_value",
    [
        pytest.param(Decimal("NaN"), id="nan"),
        pytest.param(Decimal("sNaN"), id="signaling-nan"),
        pytest.param(Decimal("Infinity"), id="positive-infinity"),
        pytest.param(Decimal("-Infinity"), id="negative-infinity"),
        pytest.param(Decimal("0"), id="zero"),
        pytest.param(Decimal("-0"), id="negative-zero"),
        pytest.param(Decimal("-0.0001"), id="negative"),
    ],
)
def test_trade_rejects_nonpositive_or_nonfinite_decimals(invalid_value: Decimal) -> None:
    with pytest.raises(ValueError):
        TradeEvent(price=invalid_value, quantity=Decimal("1"))

    with pytest.raises(ValueError):
        TradeEvent(price=Decimal("1"), quantity=invalid_value)


def test_trade_preserves_small_and_high_precision_decimals() -> None:
    price = Decimal("0.0000000000000000000000000001")
    quantity = Decimal("123456789.123456789123456789")

    trade = TradeEvent(price=price, quantity=quantity)

    assert trade.price is price
    assert trade.quantity is quantity


@pytest.mark.parametrize(
    "schema_version",
    [
        pytest.param(-1, id="negative"),
        pytest.param(0, id="zero"),
        pytest.param(2, id="unsupported"),
    ],
)
def test_envelope_rejects_unsupported_schema_versions(schema_version: int) -> None:
    template = _envelope()

    with pytest.raises(ValueError):
        replace(template, schema_version=schema_version)


@pytest.mark.parametrize(
    "schema_version",
    [
        pytest.param(True, id="boolean"),
        pytest.param(1.0, id="float"),
    ],
)
def test_envelope_rejects_noninteger_schema_versions(schema_version: object) -> None:
    template = _envelope()

    with pytest.raises(TypeError):
        replace(template, schema_version=cast(int, schema_version))


@pytest.mark.parametrize(
    "received_monotonic_ns,expected_error",
    [
        pytest.param(-1, ValueError, id="negative"),
        pytest.param(True, TypeError, id="boolean"),
        pytest.param(1.0, TypeError, id="float"),
    ],
)
def test_envelope_rejects_invalid_monotonic_timestamps(
    received_monotonic_ns: object,
    expected_error: type[Exception],
) -> None:
    template = _envelope()

    with pytest.raises(expected_error):
        replace(template, received_monotonic_ns=cast(int, received_monotonic_ns))


@pytest.mark.parametrize(
    "invalid_time",
    [
        pytest.param(datetime(2026, 8, 25, 12, 0), id="naive"),
        pytest.param(
            datetime(2026, 8, 25, 12, 0, tzinfo=timezone(timedelta(hours=1))),
            id="non-utc",
        ),
    ],
)
def test_envelope_rejects_non_utc_event_and_received_times(invalid_time: datetime) -> None:
    template = _envelope()

    with pytest.raises(ValueError):
        replace(template, event_time=invalid_time)

    with pytest.raises(ValueError):
        replace(template, received_time=invalid_time)


def test_envelope_rejects_non_datetime_times() -> None:
    template = _envelope()

    with pytest.raises(TypeError):
        replace(template, event_time=cast(datetime, "2026-08-25T12:00:00Z"))
    with pytest.raises(TypeError):
        replace(template, received_time=cast(datetime, 0))


def test_envelope_accepts_alternative_zero_offset_utc() -> None:
    zero_offset = timezone(timedelta(0), name="UTC-alias")

    envelope = MarketEventEnvelope(
        schema_version=MARKET_EVENT_SCHEMA_VERSION,
        instrument=_instrument(),
        event=_trade(),
        event_time=datetime(2026, 8, 25, 12, 0, tzinfo=zero_offset),
        received_time=datetime(2026, 8, 25, 12, 1, tzinfo=zero_offset),
        received_monotonic_ns=1,
        collector_version="collector-v1",
        collector_commit="9ab3c0e",
        is_gap=False,
    )

    assert envelope.event_time.tzinfo is UTC
    assert envelope.received_time.tzinfo is UTC


def test_envelope_allows_received_time_before_event_time() -> None:
    envelope = MarketEventEnvelope(
        schema_version=MARKET_EVENT_SCHEMA_VERSION,
        instrument=_instrument(),
        event=_trade(),
        event_time=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        received_time=datetime(2026, 8, 25, 11, 59, tzinfo=UTC),
        received_monotonic_ns=0,
        collector_version="collector-v1",
        collector_commit="9ab3c0e",
        is_gap=False,
    )

    assert envelope.received_time < envelope.event_time


def test_envelope_preserves_provenance_sequence_and_gap_metadata() -> None:
    envelope = _envelope()

    assert envelope.collector_version == "collector-v1"
    assert envelope.collector_commit == "9ab3c0e"
    assert envelope.is_gap is False
    assert envelope.source_sequence == 42
    assert envelope.correlation_id == "trade-42"

    without_source_identifiers = replace(
        envelope,
        source_sequence=None,
        correlation_id=None,
        is_gap=True,
    )
    assert without_source_identifiers.source_sequence is None
    assert without_source_identifiers.correlation_id is None
    assert without_source_identifiers.is_gap is True


def test_envelope_rejects_invalid_required_provenance_metadata() -> None:
    template = _envelope()

    with pytest.raises(ValueError):
        replace(template, collector_version="")
    with pytest.raises(ValueError):
        replace(template, collector_commit=" commit ")
    with pytest.raises(TypeError):
        replace(template, collector_version=cast(str, 1))
    with pytest.raises(TypeError):
        replace(template, is_gap=cast(bool, 0))


@pytest.mark.parametrize(
    "source_sequence,expected_error",
    [
        pytest.param(-1, ValueError, id="negative"),
        pytest.param(True, TypeError, id="boolean"),
        pytest.param(1.0, TypeError, id="float"),
    ],
)
def test_envelope_rejects_invalid_source_sequences(
    source_sequence: object,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        replace(_envelope(), source_sequence=cast(int, source_sequence))


@pytest.mark.parametrize(
    "correlation_id",
    [
        pytest.param("", id="empty"),
        pytest.param(" correlation ", id="outer-whitespace"),
        pytest.param("correlation\n", id="control-character"),
    ],
)
def test_envelope_rejects_invalid_correlation_ids(correlation_id: str) -> None:
    with pytest.raises(ValueError):
        replace(_envelope(), correlation_id=correlation_id)


def test_envelope_rejects_invalid_nested_contract_types() -> None:
    template = _envelope()

    with pytest.raises(TypeError):
        replace(template, instrument=cast(Instrument, object()))

    with pytest.raises(TypeError):
        replace(template, event=cast(TradeEvent, object()))


def test_trade_and_envelope_are_deterministic_and_hashable() -> None:
    first_trade = _trade()
    second_trade = _trade()
    first_envelope = _envelope()
    second_envelope = _envelope()

    assert first_trade == second_trade
    assert hash(first_trade) == hash(second_trade)
    assert first_envelope == second_envelope
    assert hash(first_envelope) == hash(second_envelope)


def test_contracts_are_frozen_and_slotted() -> None:
    instrument = _instrument()
    trade = _trade()
    envelope = _envelope()

    assert not hasattr(instrument, "__dict__")
    assert not hasattr(trade, "__dict__")
    assert not hasattr(envelope, "__dict__")

    mutation_attempts: list[tuple[object, str, object]] = [
        (instrument, "base_asset", "ETH"),
        (trade, "price", Decimal("1")),
        (envelope, "received_monotonic_ns", 2),
    ]
    for contract, attribute, value in mutation_attempts:
        with pytest.raises(FrozenInstanceError):
            setattr(contract, attribute, value)
