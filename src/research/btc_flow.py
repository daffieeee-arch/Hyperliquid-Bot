"""BTC spot-flow-confirmed HL continuation; research fills, not an order engine.

All signal inputs use receipt clocks. Funding is avoided by a fixed calendar
exposure guard, never by replacing an unknown settlement with zero.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from itertools import pairwise

from hyperliquid_bot.paper_engine.precision import adverse_price
from research.harness.errors import IntegrityError
from research.harness.evaluate import TradeSeries
from research.harness.spec import ConfigSpec, HypothesisSpec

SECOND = 1_000_000_000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
LATENCY = SECOND // 4
ENTRY_DELAY = MINUTE + LATENCY
MAX_WAIT = 5 * SECOND
MAX_QUOTE_AGE = 2 * SECOND
FUNDING_GUARD = MINUTE
NOTIONAL = Decimal("100")
LOT = Decimal("0.00001")  # Frozen research assumption, not historical metadata certification.
MIN_NOTIONAL = Decimal("10")
ZERO = Decimal(0)


def require(value: bool, reason: str) -> None:
    if not value:
        raise IntegrityError("data_config", "BTC flow: " + reason)


@dataclass(frozen=True, slots=True)
class Quote:
    received_ns: int
    session: str
    ordinal: int
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True, slots=True)
class SpotTrade:
    received_ns: int
    session: str
    trade_id: int
    price: Decimal
    quantity: Decimal
    buyer_was_maker: bool


@dataclass(frozen=True, slots=True)
class Flow:
    signed_notional: Decimal
    total_notional: Decimal
    sessions: frozenset[str]
    trades: int

    @property
    def imbalance(self) -> Decimal:
        return self.signed_notional / self.total_notional


def aggregate_flow(trades: Iterable[SpotTrade]) -> dict[int, Flow]:
    """Aggregate original Spot trades once, by their availability minute."""
    out: dict[int, Flow] = {}
    seen: set[int] = set()
    for trade in trades:
        require(type(trade.received_ns) is int and trade.received_ns >= 0, "invalid trade clock")
        require(bool(trade.session) and type(trade.trade_id) is int, "invalid trade identity")
        require(trade.trade_id not in seen, "duplicate Spot trade identity")
        require(type(trade.buyer_was_maker) is bool, "invalid aggressor side")
        require(
            trade.price.is_finite()
            and trade.quantity.is_finite()
            and trade.price > 0
            and trade.quantity > 0,
            "invalid Spot trade values",
        )
        seen.add(trade.trade_id)
        key = trade.received_ns // MINUTE
        previous = out.get(key, Flow(ZERO, ZERO, frozenset(), 0))
        notional = trade.price * trade.quantity
        out[key] = Flow(
            previous.signed_notional + (-notional if trade.buyer_was_maker else notional),
            previous.total_notional + notional,
            previous.sessions | {trade.session},
            previous.trades + 1,
        )
    return out


def settlement_safe(decision_ns: int, horizon_minutes: int) -> bool:
    """Engineering calendar exclusion; not proof of actual funding timing."""
    earliest_entry = decision_ns + ENTRY_DELAY
    latest_exit = (
        decision_ns + ENTRY_DELAY + MAX_WAIT + horizon_minutes * MINUTE + LATENCY + MAX_WAIT
    )
    hour = earliest_entry // HOUR * HOUR
    return earliest_entry >= hour + FUNDING_GUARD and latest_exit < hour + HOUR - FUNDING_GUARD


@dataclass(frozen=True, slots=True)
class Fill:
    decision_ns: int
    entry: Quote
    exit: Quote
    side: int
    quantity: Decimal
    gross: float
    execution_cost: float
    weight: float


def entry_quantity(decision_mid: Decimal, quote: Quote, side: int) -> Decimal:
    requested = (NOTIONAL / decision_mid / LOT).to_integral_value(rounding=ROUND_DOWN) * LOT
    available = quote.ask_size if side == 1 else quote.bid_size
    return (min(requested, available) / LOT).to_integral_value(rounding=ROUND_DOWN) * LOT


def quote_fill(
    decision_ns: int,
    decision_mid: Decimal,
    entry: Quote,
    exit_quote: Quote,
    side: int,
    fee_bps: float,
    slippage_bps: float,
) -> Fill | None:
    """Small IOC-like entry cap; an unobservable/undersized exit is a data failure."""
    require(side in (-1, 1), "invalid side")
    require(fee_bps >= 0 and slippage_bps >= 0, "negative research cost")
    quantity = entry_quantity(decision_mid, entry, side)
    entry_touch = entry.ask if side == 1 else entry.bid
    exit_touch = exit_quote.bid if side == 1 else exit_quote.ask
    slip = Decimal(str(slippage_bps)) / 10_000
    entry_price = adverse_price(
        entry_touch * (1 + side * slip), side="BUY" if side == 1 else "SELL", max_decimals=1
    )
    exit_price = adverse_price(
        exit_touch * (1 - side * slip), side="SELL" if side == 1 else "BUY", max_decimals=1
    )
    if quantity <= 0 or quantity * entry_price < MIN_NOTIONAL:
        return None  # Known entry non-fill: no position ever exists.
    exit_size = exit_quote.bid_size if side == 1 else exit_quote.ask_size
    require(exit_size >= quantity, "unknown exit: insufficient touch size")
    gross_cash = side * quantity * (exit_quote.mid - entry.mid)
    actual_cash = side * quantity * (exit_price - entry_price)
    fees = quantity * (entry_price + exit_price) * Decimal(str(fee_bps)) / 10_000
    cost = (gross_cash - actual_cash + fees) / NOTIONAL
    require(cost.is_finite() and cost >= 0, "invalid quote execution cost")
    return Fill(
        decision_ns,
        entry,
        exit_quote,
        side,
        quantity,
        float(gross_cash / NOTIONAL),
        float(cost),
        float(quantity * entry.mid / NOTIONAL),
    )


class FlowSource:
    """Existing harness SeriesSource; a fixed minute axis with no dropped gap rows.

    Validation windows cannot read fills outside that window. Any gap or missing
    exit after a position could open invalidates the window, never removes a
    losing trade. The same rule applies independently to every configuration.
    """

    def __init__(
        self,
        spec: HypothesisSpec,
        start_ns: int,
        end_ns: int,
        quotes: tuple[Quote, ...],
        flow: dict[int, Flow],
        hl_gaps: tuple[int, ...] = (),
        bn_gaps: tuple[int, ...] = (),
        *,
        confirmed: bool = True,
        funding_events_ms: tuple[int, ...] | None = None,
    ) -> None:
        require(start_ns % MINUTE == end_ns % MINUTE == 0 and end_ns > start_ns, "minute axis")
        require((end_ns - start_ns) // MINUTE <= spec.data.max_rows, "decision row bound")
        require(
            spec.direction == "signed" and spec.portfolio is None, "unsupported study direction"
        )
        require(
            spec.sizing.method == "unit" and spec.costs.funding_column is None,
            "study sizing/funding",
        )
        require(
            spec.costs.spread_bps == 0 and spec.costs.latency_bars == 1,
            "quote-cost/latency contract",
        )
        require(bool(quotes), "no Hyperliquid quotes")
        require(
            list(quotes) == sorted(quotes, key=lambda q: (q.received_ns, q.ordinal)),
            "unordered quotes",
        )
        seen: set[tuple[str, int]] = set()
        for quote in quotes:
            require(
                type(quote.received_ns) is int and quote.received_ns >= 0, "invalid quote clock"
            )
            require(
                bool(quote.session) and (quote.session, quote.ordinal) not in seen, "quote identity"
            )
            seen.add((quote.session, quote.ordinal))
            require(
                all(v.is_finite() for v in (quote.bid, quote.ask, quote.bid_size, quote.ask_size))
                and 0 < quote.bid <= quote.ask
                and quote.bid_size >= 0
                and quote.ask_size >= 0,
                "invalid Hyperliquid quote",
            )
        self.spec, self.start_ns, self.end_ns = spec, start_ns, end_ns
        self.quotes, self.flow = quotes, dict(flow)
        self.quote_times = tuple(q.received_ns for q in quotes)
        changes = tuple(b.received_ns for a, b in pairwise(quotes) if a.session != b.session)
        self.hl_gaps, self.bn_gaps = tuple(sorted((*hl_gaps, *changes))), tuple(sorted(bn_gaps))
        self.confirmed = confirmed
        self.funding_events_ms = funding_events_ms

    @property
    def length(self) -> int:
        return (self.end_ns - self.start_ns) // MINUTE

    def _asof(self, when: int) -> Quote | None:
        at = bisect_right(self.quote_times, when) - 1
        if at < 0 or when - self.quotes[at].received_ns > MAX_QUOTE_AGE:
            return None
        return self.quotes[at]

    @staticmethod
    def _gap(gaps: tuple[int, ...], start: int, end: int) -> bool:
        return bisect_right(gaps, end) > bisect_left(gaps, start)

    def signal(self, when: int) -> tuple[int, Decimal, Quote] | None:
        now, previous = self._asof(when), self._asof(when - MINUTE)
        if now is None or previous is None or now.session != previous.session:
            return None
        if self._gap(self.hl_gaps, when - MINUTE, when):
            return None
        change = now.mid - previous.mid
        if change == 0:
            return None
        side = 1 if change > 0 else -1
        if not self.confirmed:
            return side, Decimal(1), now
        minute = self.flow.get(when // MINUTE - 1)
        if (
            minute is None
            or len(minute.sessions) != 1
            or self._gap(self.bn_gaps, when - MINUTE, when)
        ):
            return None
        imbalance = minute.imbalance
        if imbalance * side <= 0:
            return None
        return side, abs(imbalance), now

    def _after(self, when: int) -> Quote:
        at = bisect_left(self.quote_times, when)
        require(at < len(self.quotes), "unknown fill: no later quote")
        quote = self.quotes[at]
        require(quote.received_ns - when <= MAX_WAIT, "unknown fill: quote wait exceeded")
        return quote

    def fills(self, config: ConfigSpec, start: int, end: int) -> tuple[Fill, ...]:
        require(0 <= start < end <= self.length, "window outside minute axis")
        require(config.threshold is not None and config.horizon_bars in (1, 5), "study grid")
        fills: list[Fill] = []
        busy_until = -1
        for index in range(start, end):
            when = self.start_ns + index * MINUTE
            if when < busy_until or not settlement_safe(when, config.horizon_bars):
                continue
            # Boundary censoring is fixed by the window, not a future outcome.
            last_possible_exit = (
                when + ENTRY_DELAY + MAX_WAIT + LATENCY + MAX_WAIT + config.horizon_bars * MINUTE
            )
            if last_possible_exit >= self.start_ns + end * MINUTE:
                continue
            signal = self.signal(when)
            if signal is None or signal[1] < Decimal(str(config.threshold)):
                continue
            side, _, decision_quote = signal
            entry = self._after(when + ENTRY_DELAY)
            require(entry.session == decision_quote.session, "unknown entry across session")
            if self._gap(self.hl_gaps, when, entry.received_ns):
                raise IntegrityError("data_config", "BTC flow: unknown entry across gap")
            # No position is opened when displayed entry capacity is subminimum.
            entry_price = adverse_price(
                (entry.ask if side == 1 else entry.bid)
                * (1 + side * Decimal(str(self.spec.costs.slippage_bps)) / 10_000),
                side="BUY" if side == 1 else "SELL",
                max_decimals=1,
            )
            if entry_quantity(decision_quote.mid, entry, side) * entry_price < MIN_NOTIONAL:
                continue
            exit_quote = self._after(entry.received_ns + config.horizon_bars * MINUTE + LATENCY)
            require(
                exit_quote.received_ns < self.start_ns + end * MINUTE
                and exit_quote.session == entry.session
                and not self._gap(self.hl_gaps, entry.received_ns, exit_quote.received_ns),
                "unknown exit across gap/session/window",
            )
            if self.funding_events_ms is not None:
                # Returned API time is a declared model clock, not proof of
                # onchain payment time. Round both closed holding boundaries
                # conservatively to the API's millisecond precision.
                first = entry.received_ns // 1_000_000
                last = (exit_quote.received_ns + 999_999) // 1_000_000
                at = bisect_left(self.funding_events_ms, first)
                require(
                    at == len(self.funding_events_ms) or self.funding_events_ms[at] > last,
                    "funding API-clock event intersects possible holding interval",
                )
            fill = quote_fill(
                when,
                decision_quote.mid,
                entry,
                exit_quote,
                side,
                self.spec.costs.fee_bps,
                self.spec.costs.slippage_bps,
            )
            if fill is not None:
                fills.append(fill)
                busy_until = exit_quote.received_ns
        return tuple(fills)

    def window(self, config: ConfigSpec, start: int, end: int) -> TradeSeries:
        fills = self.fills(config, start, end)
        require(all(math.isfinite(f.gross) for f in fills), "nonfinite trade return")
        return TradeSeries(
            gross=tuple(f.gross for f in fills),
            # Conditional API-clock scenario only. The public runner requires a
            # separately qualified complete timeline before any financial read.
            funding_paid=(0.0,) * len(fills),
            funding_received=(0.0,) * len(fills),
            weights=tuple(f.weight for f in fills),
            execution_costs=tuple(f.execution_cost for f in fills),
        )
