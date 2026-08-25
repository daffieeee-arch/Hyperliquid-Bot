"""Small venue-neutral contracts for normalized market data."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final


class Venue(StrEnum):
    """Venues currently approved for the normalized data model."""

    HYPERLIQUID = "hyperliquid"
    BITVAVO = "bitvavo"
    KRAKEN = "kraken"
    BINANCE = "binance"


class InstrumentType(StrEnum):
    """Instrument types currently represented by the normalized data model."""

    SPOT = "spot"
    PERPETUAL = "perpetual"
    FUTURE = "future"


MARKET_EVENT_SCHEMA_VERSION: Final = 1

_CANONICAL_ASSET_PATTERN: Final = re.compile(r"[A-Z0-9]+(?:[._][A-Z0-9]+)*")
_CONTRACT_QUALIFIER_PATTERN: Final = re.compile(r"[A-Z0-9]+(?:[._-][A-Z0-9]+)*")


def _require_text(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a built-in string.")
    if not value or value != value.strip() or not value.isprintable():
        raise ValueError(f"{field_name} must be non-empty printable text without outer whitespace.")
    return value


def _require_canonical_asset(value: object, *, field_name: str) -> str:
    asset = _require_text(value, field_name=field_name)
    if _CANONICAL_ASSET_PATTERN.fullmatch(asset) is None:
        raise ValueError(
            f"{field_name} must contain only uppercase ASCII letters, digits, dots or underscores."
        )
    return asset


def _require_contract_qualifier(value: object) -> str:
    qualifier = _require_text(value, field_name="contract_qualifier")
    if _CONTRACT_QUALIFIER_PATTERN.fullmatch(qualifier) is None:
        raise ValueError(
            "contract_qualifier must contain only uppercase ASCII letters, digits, dots, "
            "underscores or hyphens."
        )
    return qualifier


def _require_positive_decimal(value: object, *, field_name: str) -> None:
    if type(value) is not Decimal:
        raise TypeError(f"{field_name} must be a Decimal.")
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name} must be finite and greater than zero.")


def _require_utc_datetime(value: object, *, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise TypeError(f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC.")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Instrument:
    """Immutable normalized identity while retaining the venue-native symbol."""

    venue: Venue
    instrument_type: InstrumentType
    base_asset: str
    quote_asset: str
    native_symbol: str
    contract_qualifier: str | None = None
    canonical_instrument_id: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.venue) is not Venue:
            raise TypeError("venue must be a Venue.")
        if type(self.instrument_type) is not InstrumentType:
            raise TypeError("instrument_type must be an InstrumentType.")

        base_asset = _require_canonical_asset(self.base_asset, field_name="base_asset")
        quote_asset = _require_canonical_asset(self.quote_asset, field_name="quote_asset")
        _require_text(self.native_symbol, field_name="native_symbol")
        if base_asset == quote_asset:
            raise ValueError("base_asset and quote_asset must differ.")

        canonical_id = f"{self.venue.value}:{self.instrument_type.value}:{base_asset}-{quote_asset}"
        if self.instrument_type is InstrumentType.FUTURE:
            if self.contract_qualifier is None:
                raise ValueError("future instruments require a contract_qualifier.")
            qualifier = _require_contract_qualifier(self.contract_qualifier)
            canonical_id = f"{canonical_id}:{qualifier}"
        elif self.contract_qualifier is not None:
            raise ValueError("contract_qualifier is only valid for future instruments.")

        object.__setattr__(self, "canonical_instrument_id", canonical_id)


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """Normalized positive price and quantity reported for one trade."""

    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        _require_positive_decimal(self.price, field_name="price")
        _require_positive_decimal(self.quantity, field_name="quantity")


@dataclass(frozen=True, slots=True)
class MarketEventEnvelope:
    """Versioned event metadata with independent wall-clock and monotonic times."""

    schema_version: int
    instrument: Instrument
    event: TradeEvent
    event_time: datetime
    received_time: datetime
    received_monotonic_ns: int
    collector_version: str
    collector_commit: str
    is_gap: bool
    source_sequence: int | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an integer.")
        if self.schema_version != MARKET_EVENT_SCHEMA_VERSION:
            raise ValueError("schema_version is not supported.")
        if type(self.instrument) is not Instrument:
            raise TypeError("instrument must be an Instrument.")
        if type(self.event) is not TradeEvent:
            raise TypeError("event must be a TradeEvent.")

        event_time = _require_utc_datetime(self.event_time, field_name="event_time")
        received_time = _require_utc_datetime(self.received_time, field_name="received_time")
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "received_time", received_time)

        if type(self.received_monotonic_ns) is not int:
            raise TypeError("received_monotonic_ns must be an integer.")
        if self.received_monotonic_ns < 0:
            raise ValueError("received_monotonic_ns must be non-negative.")

        _require_text(self.collector_version, field_name="collector_version")
        _require_text(self.collector_commit, field_name="collector_commit")
        if type(self.is_gap) is not bool:
            raise TypeError("is_gap must be a boolean.")

        if self.source_sequence is not None:
            if type(self.source_sequence) is not int:
                raise TypeError("source_sequence must be an integer or None.")
            if self.source_sequence < 0:
                raise ValueError("source_sequence must be non-negative when provided.")
        if self.correlation_id is not None:
            _require_text(self.correlation_id, field_name="correlation_id")
