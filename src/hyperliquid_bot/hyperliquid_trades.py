"""Pure offline decoding and normalization for Hyperliquid public trades."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final, cast

from .contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    AggressorSide,
    Instrument,
    MarketEventEnvelope,
    TradeEvent,
    Venue,
)

_TRADES_CHANNEL: Final = "trades"
_SOURCE_EVENT_ID_VERSION: Final = "hyperliquid-trade-v1"
_MAX_TRADE_ID: Final = (1 << 50) - 1
_UNIX_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_REQUIRED_TRADE_FIELDS: Final[tuple[str, ...]] = (
    "coin",
    "side",
    "px",
    "sz",
    "hash",
    "time",
    "tid",
    "users",
)


def _require_text(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a built-in string.")
    text = value
    if not text or text != text.strip() or not text.isprintable():
        raise ValueError(f"{field_name} must be non-empty printable text without outer whitespace.")
    return text


def _parse_positive_decimal(value: object, *, field_name: str) -> Decimal:
    text = _require_text(value, field_name=field_name)
    try:
        decimal_value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be a valid decimal string.") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError(f"{field_name} must be finite and greater than zero.")
    return decimal_value


def _require_non_negative_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be a built-in integer.")
    integer = value
    if integer < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    return integer


def _epoch_milliseconds_to_utc(time_ms: int) -> datetime:
    seconds, milliseconds = divmod(time_ms, 1_000)
    try:
        return _UNIX_EPOCH + timedelta(seconds=seconds, milliseconds=milliseconds)
    except OverflowError as exc:
        raise ValueError("time must be within the supported UTC datetime range.") from exc


def _parse_side(value: object) -> tuple[str, AggressorSide]:
    side = _require_text(value, field_name="side")
    if side == "B":
        return side, AggressorSide.BUY
    if side == "A":
        return side, AggressorSide.SELL
    raise ValueError("side must be exactly B or A.")


def _parse_users(value: object) -> tuple[str, str]:
    if type(value) is not list:
        raise TypeError("users must be a built-in list.")
    users = cast(list[object], value)
    if len(users) != 2:
        raise ValueError("users must contain exactly buyer and seller.")
    return (
        _require_text(users[0], field_name="users[0]"),
        _require_text(users[1], field_name="users[1]"),
    )


def _require_trade_object(value: object) -> dict[object, object]:
    if type(value) is not dict:
        raise TypeError("each trades data item must be a built-in object.")
    return cast(dict[object, object], value)


def _required_value(payload: dict[object, object], field_name: str) -> object:
    if field_name not in payload:
        raise ValueError(f"trade payload is missing required field {field_name}.")
    return payload[field_name]


def _source_event_id(trade: "HyperliquidWsTrade") -> str:
    return json.dumps(
        [_SOURCE_EVENT_ID_VERSION, trade.time, trade.coin, trade.tid],
        ensure_ascii=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class HyperliquidWsTrade:
    """Validated immutable DTO for one documented Hyperliquid ``WsTrade``."""

    coin: str
    side: str
    px: Decimal
    sz: Decimal
    hash: str
    time: int
    tid: int
    users: tuple[str, str]
    event_time: datetime = field(init=False)
    aggressor_side: AggressorSide = field(init=False)

    def __post_init__(self) -> None:
        _require_text(self.coin, field_name="coin")
        side, aggressor_side = _parse_side(self.side)
        if type(self.px) is not Decimal:
            raise TypeError("px must be a Decimal.")
        if not self.px.is_finite() or self.px <= 0:
            raise ValueError("px must be finite and greater than zero.")
        if type(self.sz) is not Decimal:
            raise TypeError("sz must be a Decimal.")
        if not self.sz.is_finite() or self.sz <= 0:
            raise ValueError("sz must be finite and greater than zero.")
        _require_text(self.hash, field_name="hash")
        time_ms = _require_non_negative_int(self.time, field_name="time")
        tid = _require_non_negative_int(self.tid, field_name="tid")
        if tid > _MAX_TRADE_ID:
            raise ValueError("tid must fit within the documented 50-bit range.")
        if type(self.users) is not tuple:
            raise TypeError("users must be a tuple in the decoded DTO.")
        if len(self.users) != 2:
            raise ValueError("users must contain exactly buyer and seller.")
        users = (
            _require_text(self.users[0], field_name="users[0]"),
            _require_text(self.users[1], field_name="users[1]"),
        )

        object.__setattr__(self, "side", side)
        object.__setattr__(self, "users", users)
        object.__setattr__(self, "event_time", _epoch_milliseconds_to_utc(time_ms))
        object.__setattr__(self, "aggressor_side", aggressor_side)

    @classmethod
    def from_payload(cls, payload: object) -> "HyperliquidWsTrade":
        """Decode one JSON-decoded trade object, tolerating unknown additive fields."""

        trade = _require_trade_object(payload)
        values = {
            field_name: _required_value(trade, field_name) for field_name in _REQUIRED_TRADE_FIELDS
        }
        side, _ = _parse_side(values["side"])
        time_ms = _require_non_negative_int(values["time"], field_name="time")
        tid = _require_non_negative_int(values["tid"], field_name="tid")
        return cls(
            coin=_require_text(values["coin"], field_name="coin"),
            side=side,
            px=_parse_positive_decimal(values["px"], field_name="px"),
            sz=_parse_positive_decimal(values["sz"], field_name="sz"),
            hash=_require_text(values["hash"], field_name="hash"),
            time=time_ms,
            tid=tid,
            users=_parse_users(values["users"]),
        )


def decode_hyperliquid_trades_frame(frame: object) -> tuple[HyperliquidWsTrade, ...]:
    """Decode a JSON-decoded ``trades`` frame without performing any I/O."""

    if type(frame) is not dict:
        raise TypeError("frame must be a built-in object.")
    message = cast(dict[object, object], frame)
    if "channel" not in message:
        raise ValueError("frame is missing required field channel.")
    channel = _require_text(message["channel"], field_name="channel")
    if channel != _TRADES_CHANNEL:
        raise ValueError("frame channel must be exactly trades.")
    if "data" not in message:
        raise ValueError("frame is missing required field data.")
    data = message["data"]
    if type(data) is not list:
        raise TypeError("trades frame data must be a built-in list.")
    return tuple(HyperliquidWsTrade.from_payload(item) for item in cast(list[object], data))


def _resolve_instrument(
    coin: str,
    instrument_registry: Sequence[Instrument],
) -> Instrument:
    matches: list[Instrument] = []
    for instrument in instrument_registry:
        if type(instrument) is not Instrument:
            raise TypeError("instrument_registry must contain only Instrument values.")
        if instrument.venue is Venue.HYPERLIQUID and instrument.native_symbol == coin:
            matches.append(instrument)
    if not matches:
        raise LookupError("Hyperliquid coin is not registered.")
    if len(matches) != 1:
        raise LookupError("Hyperliquid coin mapping is ambiguous.")
    return matches[0]


def normalize_hyperliquid_trade(
    trade: HyperliquidWsTrade,
    *,
    instrument_registry: Sequence[Instrument],
    received_time: datetime,
    received_monotonic_ns: int,
    collector_version: str,
    collector_commit: str,
    is_gap: bool,
) -> MarketEventEnvelope:
    """Normalize one decoded trade using only explicit inputs."""

    if type(trade) is not HyperliquidWsTrade:
        raise TypeError("trade must be a HyperliquidWsTrade.")
    instrument = _resolve_instrument(trade.coin, instrument_registry)
    return MarketEventEnvelope(
        schema_version=MARKET_EVENT_SCHEMA_VERSION,
        instrument=instrument,
        event=TradeEvent(
            price=trade.px,
            quantity=trade.sz,
            aggressor_side=trade.aggressor_side,
        ),
        event_time=trade.event_time,
        received_time=received_time,
        received_monotonic_ns=received_monotonic_ns,
        collector_version=collector_version,
        collector_commit=collector_commit,
        is_gap=is_gap,
        source_event_id=_source_event_id(trade),
        source_transaction_id=trade.hash,
        source_sequence=None,
        correlation_id=None,
    )


def normalize_hyperliquid_trades_frame(
    frame: object,
    *,
    instrument_registry: Sequence[Instrument],
    received_time: datetime,
    received_monotonic_ns: int,
    collector_version: str,
    collector_commit: str,
    is_gap: bool,
) -> tuple[MarketEventEnvelope, ...]:
    """Decode and normalize every trade in one frame without partial output."""

    trades = decode_hyperliquid_trades_frame(frame)
    return tuple(
        normalize_hyperliquid_trade(
            trade,
            instrument_registry=instrument_registry,
            received_time=received_time,
            received_monotonic_ns=received_monotonic_ns,
            collector_version=collector_version,
            collector_commit=collector_commit,
            is_gap=is_gap,
        )
        for trade in trades
    )
