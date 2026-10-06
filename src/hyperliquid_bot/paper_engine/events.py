"""Normalized market inputs for a deterministic PAPER strategy.

Events are pure values. A missing price stays missing. Nothing in this module
synthesizes a mid from one side or from the last trade.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hyperliquid_bot.paper_engine.time_display import require_utc


def _require_text(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty text without outer whitespace.")
    return value


def _require_ns(value: object, *, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer.")
    return value


def _optional_decimal(value: Decimal | None, *, field_name: str, positive: bool) -> None:
    if value is None:
        return
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal when present.")
    if positive and value <= 0:
        raise ValueError(f"{field_name} must be positive when present.")
    if not positive and value < 0:
        raise ValueError(f"{field_name} must be non-negative when present.")


class MarketEventBase:
    """Shared identity for one normalized market observation."""

    venue: str
    instrument_id: str
    event_time_utc: datetime
    received_utc_ns: int
    source_event_id: str
    message_ordinal: int
    event_index: int

    def __init__(
        self,
        *,
        venue: str,
        instrument_id: str,
        event_time_utc: datetime,
        received_utc_ns: int,
        source_event_id: str,
        message_ordinal: int = 0,
        event_index: int = 0,
    ) -> None:
        self.venue = _require_text(venue, field_name="venue")
        self.instrument_id = _require_text(instrument_id, field_name="instrument_id")
        self.event_time_utc = require_utc(event_time_utc, field_name="event_time_utc")
        self.received_utc_ns = _require_ns(received_utc_ns, field_name="received_utc_ns")
        self.source_event_id = _require_text(source_event_id, field_name="source_event_id")
        self.message_ordinal = _require_ns(message_ordinal, field_name="message_ordinal")
        self.event_index = _require_ns(event_index, field_name="event_index")


class TradeEvent(MarketEventBase):
    """One public trade print. ``aggressor_side`` is the taker side, BUY or SELL."""

    price: Decimal
    quantity: Decimal
    aggressor_side: str

    def __init__(
        self,
        *,
        venue: str,
        instrument_id: str,
        event_time_utc: datetime,
        received_utc_ns: int,
        source_event_id: str,
        price: Decimal,
        quantity: Decimal,
        aggressor_side: str,
        message_ordinal: int = 0,
        event_index: int = 0,
    ) -> None:
        super().__init__(
            venue=venue,
            instrument_id=instrument_id,
            event_time_utc=event_time_utc,
            received_utc_ns=received_utc_ns,
            source_event_id=source_event_id,
            message_ordinal=message_ordinal,
            event_index=event_index,
        )
        _optional_decimal(price, field_name="price", positive=True)
        _optional_decimal(quantity, field_name="quantity", positive=True)
        if type(price) is not Decimal or type(quantity) is not Decimal:
            raise ValueError("trade price and quantity are required.")
        if aggressor_side not in {"BUY", "SELL"}:
            raise ValueError("aggressor_side must be exactly BUY or SELL.")
        self.price = price
        self.quantity = quantity
        self.aggressor_side = aggressor_side


class BboEvent(MarketEventBase):
    """Best bid and offer. Either side may be missing; a missing side is not a mid."""

    bid_price: Decimal | None
    bid_size: Decimal | None
    ask_price: Decimal | None
    ask_size: Decimal | None

    def __init__(
        self,
        *,
        venue: str,
        instrument_id: str,
        event_time_utc: datetime,
        received_utc_ns: int,
        source_event_id: str,
        bid_price: Decimal | None,
        bid_size: Decimal | None,
        ask_price: Decimal | None,
        ask_size: Decimal | None,
        message_ordinal: int = 0,
        event_index: int = 0,
    ) -> None:
        super().__init__(
            venue=venue,
            instrument_id=instrument_id,
            event_time_utc=event_time_utc,
            received_utc_ns=received_utc_ns,
            source_event_id=source_event_id,
            message_ordinal=message_ordinal,
            event_index=event_index,
        )
        _optional_decimal(bid_price, field_name="bid_price", positive=True)
        _optional_decimal(ask_price, field_name="ask_price", positive=True)
        _optional_decimal(bid_size, field_name="bid_size", positive=False)
        _optional_decimal(ask_size, field_name="ask_size", positive=False)
        self.bid_price = bid_price
        self.bid_size = bid_size
        self.ask_price = ask_price
        self.ask_size = ask_size


class MarkEvent(MarketEventBase):
    """Venue-published mark. This is not a mid computed by the engine."""

    mark_price: Decimal | None

    def __init__(
        self,
        *,
        venue: str,
        instrument_id: str,
        event_time_utc: datetime,
        received_utc_ns: int,
        source_event_id: str,
        mark_price: Decimal | None,
        message_ordinal: int = 0,
        event_index: int = 0,
    ) -> None:
        super().__init__(
            venue=venue,
            instrument_id=instrument_id,
            event_time_utc=event_time_utc,
            received_utc_ns=received_utc_ns,
            source_event_id=source_event_id,
            message_ordinal=message_ordinal,
            event_index=event_index,
        )
        _optional_decimal(mark_price, field_name="mark_price", positive=True)
        self.mark_price = mark_price


class BarEvent(MarketEventBase):
    """One OHLCV bar built only from trades already in hand."""

    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    bar_start_utc: datetime

    def __init__(
        self,
        *,
        venue: str,
        instrument_id: str,
        event_time_utc: datetime,
        received_utc_ns: int,
        source_event_id: str,
        open_price: Decimal,
        high_price: Decimal,
        low_price: Decimal,
        close_price: Decimal,
        volume: Decimal,
        bar_start_utc: datetime,
        message_ordinal: int = 0,
        event_index: int = 0,
    ) -> None:
        super().__init__(
            venue=venue,
            instrument_id=instrument_id,
            event_time_utc=event_time_utc,
            received_utc_ns=received_utc_ns,
            source_event_id=source_event_id,
            message_ordinal=message_ordinal,
            event_index=event_index,
        )
        for field_name, field_value in (
            ("open_price", open_price),
            ("high_price", high_price),
            ("low_price", low_price),
            ("close_price", close_price),
        ):
            _optional_decimal(field_value, field_name=field_name, positive=True)
            if type(field_value) is not Decimal:
                raise ValueError(f"{field_name} is required.")
        _optional_decimal(volume, field_name="volume", positive=False)
        if type(volume) is not Decimal:
            raise ValueError("volume is required.")
        if high_price < max(open_price, close_price, low_price) or low_price > min(
            open_price, close_price, high_price
        ):
            raise ValueError("bar high/low must bound the open and close.")
        self.open_price = open_price
        self.high_price = high_price
        self.low_price = low_price
        self.close_price = close_price
        self.volume = volume
        self.bar_start_utc = require_utc(bar_start_utc, field_name="bar_start_utc")


MarketEvent = TradeEvent | BboEvent | MarkEvent | BarEvent


def event_sort_key(event: MarketEvent) -> tuple[int, int, int, str]:
    """Deterministic tape order: receipt time, then source ordinal, then kind."""

    kind_order = {
        "bbo": 0,
        "trade": 1,
        "mark": 2,
        "bar": 3,
    }
    if isinstance(event, BboEvent):
        kind = "bbo"
    elif isinstance(event, TradeEvent):
        kind = "trade"
    elif isinstance(event, MarkEvent):
        kind = "mark"
    elif isinstance(event, BarEvent):
        kind = "bar"
    else:
        raise TypeError("event must be a normalized market event.")
    return (
        event.received_utc_ns,
        event.message_ordinal,
        event.event_index,
        f"{kind_order[kind]}:{event.source_event_id}",
    )


def bbo_is_complete(event: BboEvent) -> bool:
    """True only when both sides exist, are positive, and the book is not crossed."""

    bid = event.bid_price
    ask = event.ask_price
    bid_size = event.bid_size
    ask_size = event.ask_size
    if bid is None or ask is None or bid_size is None or ask_size is None:
        return False
    if bid <= 0 or ask <= 0 or bid_size < 0 or ask_size < 0:
        return False
    return ask > bid


def bbo_mid(event: BboEvent) -> Decimal | None:
    """Return ``(bid + ask) / 2`` only for a complete book. Never invent a side."""

    if not bbo_is_complete(event):
        return None
    bid = event.bid_price
    ask = event.ask_price
    if bid is None or ask is None:
        return None
    return (bid + ask) / Decimal(2)


def aggregate_trade_bars(
    trades: Sequence[TradeEvent],
    *,
    interval_ns: int,
) -> tuple[BarEvent, ...]:
    """Bucket trades into OHLCV bars. Empty input returns no bars."""

    if type(interval_ns) is not int or interval_ns <= 0:
        raise ValueError("interval_ns must be a positive integer.")
    if not trades:
        return ()
    ordered = tuple(sorted(trades, key=event_sort_key))
    bars: list[BarEvent] = []
    bucket_start: int | None = None
    bucket: list[TradeEvent] = []

    def _emit(group: list[TradeEvent], start_ns: int) -> None:
        prices = [item.price for item in group]
        volume = sum((item.quantity for item in group), Decimal("0"))
        start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=start_ns // 1000)
        end_ns = group[-1].received_utc_ns
        end = group[-1].event_time_utc
        bars.append(
            BarEvent(
                venue=group[0].venue,
                instrument_id=group[0].instrument_id,
                event_time_utc=end,
                received_utc_ns=end_ns,
                source_event_id=f"bar-{start_ns}",
                open_price=prices[0],
                high_price=max(prices),
                low_price=min(prices),
                close_price=prices[-1],
                volume=volume,
                bar_start_utc=start,
                message_ordinal=group[-1].message_ordinal,
                event_index=0,
            )
        )

    for trade in ordered:
        start_ns = (trade.received_utc_ns // interval_ns) * interval_ns
        if bucket_start is None:
            bucket_start = start_ns
        if start_ns != bucket_start:
            _emit(bucket, bucket_start)
            bucket = []
            bucket_start = start_ns
        bucket.append(trade)
    if bucket and bucket_start is not None:
        _emit(bucket, bucket_start)
    return tuple(bars)


def utc_from_epoch_ms(epoch_ms: int) -> datetime:
    """Build a UTC datetime from integer Unix milliseconds."""

    if type(epoch_ms) is not int or epoch_ms < 0:
        raise ValueError("epoch_ms must be a non-negative integer.")
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=epoch_ms)
