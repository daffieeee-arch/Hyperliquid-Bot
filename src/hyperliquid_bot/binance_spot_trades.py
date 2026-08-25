"""Pure offline decoding and normalization for Binance Spot raw trades."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, cast

from .contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    AggressorSide,
    Instrument,
    InstrumentType,
    MarketEventEnvelope,
    TradeEvent,
    Venue,
)

_SOURCE_EVENT_ID_VERSION: Final = "binance-spot-trade-v1"
_UNIX_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_REQUIRED_FIELDS: Final[tuple[str, ...]] = ("e", "E", "s", "t", "p", "q", "T", "m", "M")


class BinanceTimestampUnit(StrEnum):
    """Explicit timestamp units supported by Binance Spot JSON streams."""

    MILLISECONDS = "MILLISECONDS"
    MICROSECONDS = "MICROSECONDS"


def _require_timestamp_unit(value: object) -> BinanceTimestampUnit:
    if type(value) is not BinanceTimestampUnit:
        raise TypeError("timestamp_unit must be a BinanceTimestampUnit.")
    return value


def _require_non_negative_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be a built-in integer.")
    integer = value
    if integer < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    return integer


def _require_symbol(value: object) -> str:
    if type(value) is not str:
        raise TypeError("s must be a built-in string.")
    symbol = value
    if not symbol or symbol != symbol.strip() or not symbol.isprintable():
        raise ValueError("s must be non-empty printable text without outer whitespace.")
    return symbol


def _parse_positive_decimal(value: object, *, field_name: str) -> Decimal:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a built-in string.")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be a valid decimal string.") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError(f"{field_name} must be finite and greater than zero.")
    return decimal_value


def _require_positive_decimal(value: object, *, field_name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{field_name} must be a Decimal.")
    decimal_value = value
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError(f"{field_name} must be finite and greater than zero.")
    return decimal_value


def _require_boolean(value: object, *, field_name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be a built-in boolean.")
    return value


def _timestamp_to_utc(
    value: int,
    *,
    timestamp_unit: BinanceTimestampUnit,
    field_name: str,
) -> datetime:
    divisor = 1_000 if timestamp_unit is BinanceTimestampUnit.MILLISECONDS else 1_000_000
    seconds, remainder = divmod(value, divisor)
    microseconds = (
        remainder * 1_000 if timestamp_unit is BinanceTimestampUnit.MILLISECONDS else remainder
    )
    try:
        return _UNIX_EPOCH + timedelta(seconds=seconds, microseconds=microseconds)
    except OverflowError as exc:
        raise ValueError(f"{field_name} must be within the supported UTC datetime range.") from exc


def _require_payload(value: object) -> dict[object, object]:
    if type(value) is not dict:
        raise TypeError("payload must be a built-in dict.")
    return cast(dict[object, object], value)


def _required_value(payload: dict[object, object], field_name: str) -> object:
    if field_name not in payload:
        raise ValueError(f"trade payload is missing required field {field_name}.")
    return payload[field_name]


def _require_event_type(value: object) -> str:
    if type(value) is not str:
        raise TypeError("e must be a built-in string.")
    if value != "trade":
        raise ValueError("e must be exactly trade.")
    return value


def _source_event_id(trade: "BinanceSpotWsTrade") -> str:
    return json.dumps(
        [_SOURCE_EVENT_ID_VERSION, trade.symbol, trade.trade_id],
        ensure_ascii=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class BinanceSpotWsTrade:
    """Validated immutable DTO for one Binance Spot raw ``@trade`` event."""

    event_type: str
    exchange_event_time: int
    symbol: str
    trade_id: int
    price: Decimal
    quantity: Decimal
    trade_time: int
    buyer_was_maker: bool
    ignore_flag: bool
    timestamp_unit: BinanceTimestampUnit
    exchange_event_time_utc: datetime = field(init=False)
    trade_time_utc: datetime = field(init=False)
    aggressor_side: AggressorSide = field(init=False)

    def __post_init__(self) -> None:
        _require_event_type(self.event_type)
        exchange_event_time = _require_non_negative_int(
            self.exchange_event_time,
            field_name="E",
        )
        _require_symbol(self.symbol)
        _require_non_negative_int(self.trade_id, field_name="t")
        _require_positive_decimal(self.price, field_name="p")
        _require_positive_decimal(self.quantity, field_name="q")
        trade_time = _require_non_negative_int(self.trade_time, field_name="T")
        buyer_was_maker = _require_boolean(self.buyer_was_maker, field_name="m")
        _require_boolean(self.ignore_flag, field_name="M")
        timestamp_unit = _require_timestamp_unit(self.timestamp_unit)

        object.__setattr__(
            self,
            "exchange_event_time_utc",
            _timestamp_to_utc(
                exchange_event_time,
                timestamp_unit=timestamp_unit,
                field_name="E",
            ),
        )
        object.__setattr__(
            self,
            "trade_time_utc",
            _timestamp_to_utc(
                trade_time,
                timestamp_unit=timestamp_unit,
                field_name="T",
            ),
        )
        object.__setattr__(
            self,
            "aggressor_side",
            AggressorSide.SELL if buyer_was_maker else AggressorSide.BUY,
        )


def decode_binance_spot_trade(
    payload: object,
    *,
    timestamp_unit: BinanceTimestampUnit,
) -> BinanceSpotWsTrade:
    """Decode one already JSON-decoded Binance Spot raw trade without I/O."""

    unit = _require_timestamp_unit(timestamp_unit)
    trade = _require_payload(payload)
    values = {field_name: _required_value(trade, field_name) for field_name in _REQUIRED_FIELDS}
    return BinanceSpotWsTrade(
        event_type=_require_event_type(values["e"]),
        exchange_event_time=_require_non_negative_int(values["E"], field_name="E"),
        symbol=_require_symbol(values["s"]),
        trade_id=_require_non_negative_int(values["t"], field_name="t"),
        price=_parse_positive_decimal(values["p"], field_name="p"),
        quantity=_parse_positive_decimal(values["q"], field_name="q"),
        trade_time=_require_non_negative_int(values["T"], field_name="T"),
        buyer_was_maker=_require_boolean(values["m"], field_name="m"),
        ignore_flag=_require_boolean(values["M"], field_name="M"),
        timestamp_unit=unit,
    )


def _resolve_instrument(
    symbol: str,
    instrument_registry: Sequence[Instrument],
) -> Instrument:
    matches: list[Instrument] = []
    for instrument in instrument_registry:
        if type(instrument) is not Instrument:
            raise TypeError("instrument_registry must contain only Instrument values.")
        if (
            instrument.venue is Venue.BINANCE
            and instrument.instrument_type is InstrumentType.SPOT
            and instrument.native_symbol == symbol
        ):
            matches.append(instrument)
    if not matches:
        raise LookupError("Binance Spot symbol is not registered.")
    if len(matches) != 1:
        raise LookupError("Binance Spot symbol mapping is ambiguous.")
    return matches[0]


def normalize_binance_spot_trade(
    trade: BinanceSpotWsTrade,
    *,
    instrument_registry: Sequence[Instrument],
    received_time: datetime,
    received_monotonic_ns: int,
    collector_version: str,
    collector_commit: str,
    is_gap: bool,
) -> MarketEventEnvelope:
    """Normalize one decoded Binance Spot trade using only explicit inputs."""

    if type(trade) is not BinanceSpotWsTrade:
        raise TypeError("trade must be a BinanceSpotWsTrade.")
    instrument = _resolve_instrument(trade.symbol, instrument_registry)
    return MarketEventEnvelope(
        schema_version=MARKET_EVENT_SCHEMA_VERSION,
        instrument=instrument,
        event=TradeEvent(
            price=trade.price,
            quantity=trade.quantity,
            aggressor_side=trade.aggressor_side,
        ),
        event_time=trade.trade_time_utc,
        received_time=received_time,
        received_monotonic_ns=received_monotonic_ns,
        collector_version=collector_version,
        collector_commit=collector_commit,
        is_gap=is_gap,
        source_event_id=_source_event_id(trade),
        source_transaction_id=None,
        source_sequence=None,
        correlation_id=None,
    )


def normalize_binance_spot_trade_payload(
    payload: object,
    *,
    timestamp_unit: BinanceTimestampUnit,
    instrument_registry: Sequence[Instrument],
    received_time: datetime,
    received_monotonic_ns: int,
    collector_version: str,
    collector_commit: str,
    is_gap: bool,
) -> MarketEventEnvelope:
    """Decode and normalize one Binance Spot raw trade atomically without I/O."""

    return normalize_binance_spot_trade(
        decode_binance_spot_trade(payload, timestamp_unit=timestamp_unit),
        instrument_registry=instrument_registry,
        received_time=received_time,
        received_monotonic_ns=received_monotonic_ns,
        collector_version=collector_version,
        collector_commit=collector_commit,
        is_gap=is_gap,
    )
