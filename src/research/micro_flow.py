"""Causal BTC pressure replay with charged, delayed executable touch fills.

This is a numerical research model, not a broker or an order API. Native input
and complete funding-census qualification belong to the caller. Public L2 does
not establish maker queue fills; this module only simulates taker execution.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal

from hyperliquid_bot.paper_engine.precision import adverse_price, floor_size
from research.harness.errors import IntegrityError

SECOND = 1_000_000_000
HOUR_MS = 3_600_000
LOT = Decimal("0.00001")
ZERO = Decimal(0)
TEN = Decimal(10)
MAX_NOTIONAL = Decimal("100")
MAX_AGE = SECOND
MAX_WAIT = SECOND
HOLD = 300 * SECOND
GRID = 10 * SECOND


def require(value: bool, message: str) -> None:
    if not value:
        raise IntegrityError("micro_flow", message)


@dataclass(frozen=True, slots=True)
class MicroEvent:
    mono_ns: int
    utc_ns: int
    venue_ms: int
    kind: Literal["book", "trade", "gap"]
    session: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    trade_price: Decimal | None = None
    trade_quantity: Decimal | None = None
    trade_side: int = 0


@dataclass(frozen=True, slots=True)
class MicroConfig:
    name: str
    lookback_seconds: int
    strength: float
    target_margin_return: float

    def __post_init__(self) -> None:
        require(bool(self.name) and len(self.name) <= 80, "configuration identity")
        require(self.lookback_seconds in (30, 120), "fixed lookback")
        require(self.strength in (0.0, 0.2, 0.4), "fixed pressure strength")
        require(self.target_margin_return in (0.01, 0.02), "fixed net-margin target")


def configs() -> tuple[MicroConfig, ...]:
    return tuple(
        MicroConfig(
            f"flow-w{window}-s{int(strength * 100)}-tp{int(target * 100)}", window, strength, target
        )
        for window in (30, 120)
        for strength in (0.2, 0.4)
        for target in (0.01, 0.02)
    )


@dataclass(frozen=True, slots=True)
class Book:
    event: MicroEvent
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True, slots=True)
class Position:
    side: int
    quantity: Decimal
    entry: Decimal
    entry_fee: Decimal
    initial_margin: Decimal
    entry_mono_ns: int
    entry_utc_ns: int
    entry_venue_ms: int
    decision_mono_ns: int
    cash_before_entry: Decimal


@dataclass(frozen=True, slots=True)
class Pending:
    kind: Literal["entry", "exit"]
    decision_mono_ns: int
    due_ns: int
    side: int
    notional: Decimal
    reason: str


def _book(event: MicroEvent) -> Book:
    values = (event.bid, event.ask, event.bid_size, event.ask_size)
    require(
        all(
            type(v) is Decimal and v.is_finite() and v > 0 and math.isfinite(float(v))
            for v in values
        ),
        "positive finite bid/ask and displayed sizes required",
    )
    assert event.bid is not None and event.ask is not None
    assert event.bid_size is not None and event.ask_size is not None
    require(event.ask >= event.bid, "crossed book")
    return Book(event, event.bid, event.ask, event.bid_size, event.ask_size)


def _execution(book: Book, side: int, slip: Decimal) -> tuple[Decimal, Decimal]:
    touch = book.ask if side == 1 else book.bid
    price = adverse_price(
        touch * (1 + side * slip), side="BUY" if side == 1 else "SELL", max_decimals=1
    )
    return touch, price


class Pressure:
    """Rolling features consume only observations already received in order."""

    def __init__(self, seconds: int) -> None:
        self.span = seconds * SECOND
        self.books: deque[tuple[int, Book]] = deque()
        self.ofi: deque[tuple[int, float]] = deque()
        self.flow: deque[tuple[int, float, float]] = deque()
        self.sum_ofi = self.abs_ofi = self.signed_flow = self.total_flow = 0.0
        self.first_ns: int | None = None
        self.first_book_ns: int | None = None
        self.first_trade_ns: int | None = None
        self.previous: Book | None = None

    def add(self, event: MicroEvent, book: Book | None) -> None:
        if book is not None:
            if self.first_book_ns is None:
                self.first_book_ns = event.mono_ns
            previous = self.previous
            if previous is not None:
                value = float(
                    (book.bid_size if book.bid >= previous.bid else ZERO)
                    - (previous.bid_size if book.bid <= previous.bid else ZERO)
                    - (book.ask_size if book.ask <= previous.ask else ZERO)
                    + (previous.ask_size if book.ask >= previous.ask else ZERO)
                )
                require(math.isfinite(value), "finite observed order flow")
                self.ofi.append((event.mono_ns, value))
                self.sum_ofi += value
                self.abs_ofi += abs(value)
            self.books.append((event.mono_ns, book))
            self.previous = book
        elif event.kind == "trade":
            if self.first_trade_ns is None:
                self.first_trade_ns = event.mono_ns
            assert event.trade_price is not None and event.trade_quantity is not None
            value = float(event.trade_price * event.trade_quantity)
            require(math.isfinite(value) and value > 0, "finite aggressive notional")
            signed = value * event.trade_side
            self.flow.append((event.mono_ns, signed, value))
            self.signed_flow += signed
            self.total_flow += value
        if self.first_book_ns is not None and self.first_trade_ns is not None:
            self.first_ns = max(self.first_book_ns, self.first_trade_ns)

    def signal(self, now: int, book: Book, strength: float, benchmark: bool) -> int:
        cutoff = now - self.span
        while len(self.books) > 1 and self.books[1][0] <= cutoff:
            self.books.popleft()
        for queue, signed_name, total_name in ((self.ofi, "sum_ofi", "abs_ofi"),):
            while queue and queue[0][0] <= cutoff:
                _, signed = queue.popleft()
                setattr(self, signed_name, getattr(self, signed_name) - signed)
                setattr(self, total_name, getattr(self, total_name) - abs(signed))
        while self.flow and self.flow[0][0] <= cutoff:
            _, signed, total = self.flow.popleft()
            self.signed_flow -= signed
            self.total_flow -= total
        if not self.ofi:
            self.sum_ofi = self.abs_ofi = 0.0
        if not self.flow:
            self.signed_flow = self.total_flow = 0.0
        if self.first_ns is None or now - self.first_ns < self.span:
            return 0
        if benchmark:
            if not self.books or self.books[0][0] > cutoff:
                return 0
            change = book.mid - self.books[0][1].mid
            return 1 if change > 0 else -1 if change < 0 else 0
        if self.total_flow <= 1e-10 or self.abs_ofi <= 1e-10:
            return 0
        queue_imbalance = float((book.bid_size - book.ask_size) / (book.bid_size + book.ask_size))
        trade_imbalance = self.signed_flow / self.total_flow
        order_imbalance = self.sum_ofi / self.abs_ofi
        for side in (1, -1):
            if (
                side * queue_imbalance >= strength
                and side * trade_imbalance >= strength
                and side * order_imbalance > 1e-12
            ):
                return side
        return 0


def replay(
    config: MicroConfig,
    events: Sequence[MicroEvent],
    funding_events_ms: Sequence[int],
    initial_equity: float = 10_000.0,
    *,
    fee_bps: float = 4.5,
    slippage_bps: float = 1.0,
    latency_ns: int = SECOND // 2,
    end_mono_ns: int | None = None,
    benchmark: bool = False,
) -> dict[str, Any]:
    """Replay one metadata-qualified contiguous window; retain unknown outcomes.

    Calendar restrictions use the native quote clock, physical delays use the
    host monotonic clock. Their correspondence is an explicit source assumption.
    Entry sizing is a notional-limited IOC proxy, not a native signed order.
    """
    require(bool(events), "nonempty qualified window")
    require(math.isfinite(initial_equity) and initial_equity > 0, "positive initial equity")
    require(math.isfinite(fee_bps) and 0 <= fee_bps < 100, "research fee range")
    require(math.isfinite(slippage_bps) and 0 <= slippage_bps < 100, "research friction range")
    require(type(latency_ns) is int and 0 < latency_ns <= SECOND, "physical execution delay")
    funding = tuple(funding_events_ms)
    require(bool(funding) and funding == tuple(sorted(set(funding))), "qualified funding census")
    require(all(type(t) is int and t >= 0 for t in funding), "native funding clock")
    end = events[-1].mono_ns if end_mono_ns is None else end_mono_ns
    require(type(end) is int and end > events[0].mono_ns, "declared window end")
    covered_last = next(event for event in reversed(events) if event.mono_ns <= end)
    require(
        funding[0] <= events[0].venue_ms - 60_000 and funding[-1] >= covered_last.venue_ms + 60_000,
        "funding census must bracket the source window with clock padding",
    )
    fee = Decimal(str(fee_bps)) / 10_000
    slip = Decimal(str(slippage_bps)) / 10_000
    cash = Decimal(str(initial_equity))
    peak = cash
    max_dd = ZERO
    position: Position | None = None
    pending: Pending | None = None
    book: Book | None = None
    pressure = Pressure(config.lookback_seconds)
    next_decision = (events[0].mono_ns // GRID + 1) * GRID
    ledger: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    counts = dict.fromkeys(
        (
            "decisions",
            "busy",
            "stale",
            "calendar",
            "boundary",
            "no_signal",
            "signals",
            "entry_nonfills",
            "partial_entries",
            "old_book",
            "cost_exceeds_stop",
        ),
        0,
    )
    failure: str | None = None
    liquidation_possible = False
    margin_exhausted = False

    def mark(current: Book, phase: str) -> None:
        nonlocal peak, max_dd, liquidation_possible, margin_exhausted
        equity = cash + (position.side * position.quantity * current.mid if position else ZERO)
        require(equity > 0, "research account insolvent")
        peak = max(peak, equity)
        dd = 1 - equity / peak
        max_dd = max(max_dd, dd)
        if position is not None:
            reference_margin_equity = (
                position.initial_margin
                + position.side * position.quantity * (current.mid - position.entry)
                - position.entry_fee
            )
            margin_exhausted |= reference_margin_equity <= 0
            liquidation_possible |= reference_margin_equity <= position.quantity * current.mid / 80
        ledger.append(
            {
                "mono_ns": current.event.mono_ns,
                "utc_ns": current.event.utc_ns,
                "venue_ms": current.event.venue_ms,
                "phase": phase,
                "cash": float(cash),
                "equity": float(equity),
                "mark": float(current.mid),
                "quantity": float(position.side * position.quantity) if position else 0.0,
                "drawdown": float(dd),
            }
        )

    def decision(now: int) -> None:
        nonlocal pending
        counts["decisions"] += 1
        if position is not None or pending is not None:
            counts["busy"] += 1
            return
        if book is None or not 0 <= now - book.event.mono_ns <= MAX_AGE:
            counts["stale"] += 1
            return
        # The known study/chunk deadline is supplied before financial decoding.
        if now + 2 * (latency_ns + MAX_WAIT) + HOLD >= end:
            counts["boundary"] += 1
            return
        minute = book.event.venue_ms % HOUR_MS
        if not 120_000 <= minute < 52 * 60_000:
            counts["calendar"] += 1
            return
        side = pressure.signal(now, book, config.strength, benchmark)
        if side == 0:
            counts["no_signal"] += 1
            return
        _, entry_price = _execution(book, side, slip)
        _, exit_price = _execution(book, -side, slip)
        known_close_roi = (
            TEN
            * (side * (exit_price - entry_price) - fee * (entry_price + exit_price))
            / entry_price
        )
        if known_close_roi <= Decimal("-0.05"):
            counts["cost_exceeds_stop"] += 1
            return
        counts["signals"] += 1
        pending = Pending(
            "entry", now, now + latency_ns, side, min(cash / 2, MAX_NOTIONAL), "signal"
        )

    def close_roi(current: Book) -> tuple[Decimal, Decimal, Decimal]:
        assert position is not None
        _, price = _execution(current, -position.side, slip)
        charged_fee = position.quantity * price * fee
        pnl = (
            position.side * position.quantity * (price - position.entry)
            - position.entry_fee
            - charged_fee
        )
        return pnl / position.initial_margin, price, charged_fee

    try:
        last_mono = -1
        session = events[0].session
        for event in events:
            require(
                type(event.mono_ns) is int and event.mono_ns >= last_mono,
                "ordered monotonic events",
            )
            require(
                type(event.utc_ns) is int
                and event.utc_ns >= 0
                and type(event.venue_ms) is int
                and event.venue_ms >= 0,
                "source clocks",
            )
            if event.mono_ns > end:
                break
            require(event.session == session and bool(session), "one uninterrupted native session")
            require(event.kind in ("book", "trade", "gap"), "native event kind")
            require(event.kind != "gap", "unqualified capture gap")
            last_mono = event.mono_ns
            while next_decision < event.mono_ns:
                decision(next_decision)
                next_decision += GRID
            if pending is not None and event.mono_ns > pending.due_ns + MAX_WAIT:
                if pending.kind == "exit":
                    raise IntegrityError("micro_flow", "unknown held exit: quote deadline")
                counts["entry_nonfills"] += 1
                pending = None
            current: Book | None = None
            if event.kind == "book":
                current = _book(event)
                if book is not None and current.event.venue_ms < book.event.venue_ms:
                    counts["old_book"] += 1
                    current = None
                else:
                    book = current
            else:
                require(event.trade_side in (-1, 1), "native aggressor side")
                require(
                    type(event.trade_price) is Decimal
                    and event.trade_price.is_finite()
                    and event.trade_price > 0
                    and type(event.trade_quantity) is Decimal
                    and event.trade_quantity.is_finite()
                    and event.trade_quantity > 0,
                    "positive finite native trade",
                )
            if current is not None or event.kind == "trade":
                pressure.add(event, current)
            if current is not None:
                if position is not None or not ledger:
                    mark(current, "before")
                if pending is not None and event.mono_ns >= pending.due_ns:
                    if pending.kind == "entry":
                        touch, price = _execution(current, pending.side, slip)
                        available = current.ask_size if pending.side == 1 else current.bid_size
                        wanted = floor_size(pending.notional / price, LOT)
                        quantity = floor_size(min(wanted, available), LOT)
                        if quantity <= 0 or quantity * price < 10:
                            counts["entry_nonfills"] += 1
                            pending = None
                        else:
                            counts["partial_entries"] += int(quantity < wanted)
                            entry_fee = quantity * price * fee
                            position = Position(
                                pending.side,
                                quantity,
                                price,
                                entry_fee,
                                quantity * price / TEN,
                                event.mono_ns,
                                event.utc_ns,
                                event.venue_ms,
                                pending.decision_mono_ns,
                                cash,
                            )
                            cash -= pending.side * quantity * price + entry_fee
                            fills.append(
                                {
                                    "kind": "entry",
                                    "mono_ns": event.mono_ns,
                                    "utc_ns": event.utc_ns,
                                    "venue_ms": event.venue_ms,
                                    "side": pending.side,
                                    "quantity": float(quantity),
                                    "touch": float(touch),
                                    "mid": float(current.mid),
                                    "price": float(price),
                                    "fee": float(entry_fee),
                                    "slippage": float(quantity * abs(price - touch)),
                                    "decision_mono_ns": pending.decision_mono_ns,
                                }
                            )
                            pending = None
                            mark(current, "after_entry")
                    else:
                        assert position is not None
                        available = current.bid_size if position.side == 1 else current.ask_size
                        require(available >= position.quantity, "unknown held exit: touch capacity")
                        roi, price, exit_fee = close_roi(current)
                        touch = current.bid if position.side == 1 else current.ask
                        cash += position.side * position.quantity * price - exit_fee
                        fills.append(
                            {
                                "kind": "exit",
                                "mono_ns": event.mono_ns,
                                "utc_ns": event.utc_ns,
                                "venue_ms": event.venue_ms,
                                "side": -position.side,
                                "quantity": float(position.quantity),
                                "touch": float(touch),
                                "mid": float(current.mid),
                                "price": float(price),
                                "fee": float(exit_fee),
                                "slippage": float(position.quantity * abs(price - touch)),
                                "decision_mono_ns": pending.decision_mono_ns,
                            }
                        )
                        trades.append(
                            {
                                **asdict(position),
                                "quantity": float(position.quantity),
                                "entry": float(position.entry),
                                "entry_fee": float(position.entry_fee),
                                "initial_margin": float(position.initial_margin),
                                "cash_before_entry": float(position.cash_before_entry),
                                "exit_mono_ns": event.mono_ns,
                                "exit_utc_ns": event.utc_ns,
                                "exit_venue_ms": event.venue_ms,
                                "exit": float(price),
                                "exit_fee": float(exit_fee),
                                "reason": pending.reason,
                                "net_pnl": float(cash - position.cash_before_entry),
                                "net_margin_return": float(roi),
                                "realized_target_met": roi
                                >= Decimal(str(config.target_margin_return)),
                                "funding": 0.0,
                            }
                        )
                        lower, upper = position.entry_venue_ms - 60_000, event.venue_ms + 60_000
                        position = None
                        pending = None
                        mark(current, "after_exit")
                        index = bisect_left(funding, lower)
                        require(
                            index == len(funding) or funding[index] > upper,
                            "funding event overlaps conservative exposure clock",
                        )
                require(
                    not liquidation_possible,
                    "possible isolated liquidation on observed book; native mark unqualified",
                )
                if position is not None and pending is None:
                    roi, _, _ = close_roi(current)
                    reason = (
                        "target"
                        if roi >= Decimal(str(config.target_margin_return))
                        else "stop"
                        if roi <= Decimal("-0.05")
                        else "timeout"
                        if event.mono_ns >= position.entry_mono_ns + HOLD
                        else None
                    )
                    if reason is not None:
                        pending = Pending(
                            "exit",
                            event.mono_ns,
                            event.mono_ns + latency_ns,
                            -position.side,
                            ZERO,
                            reason,
                        )
            while next_decision == event.mono_ns:
                decision(next_decision)
                next_decision += GRID
        if position is not None:
            raise IntegrityError("micro_flow", "unknown held exit at input boundary")
        if pending is not None and pending.kind == "entry":
            counts["entry_nonfills"] += 1
        if book is not None and (not ledger or ledger[-1]["mono_ns"] != book.event.mono_ns):
            mark(book, "terminal_flat")
    except (IntegrityError, ArithmeticError, ValueError) as error:
        failure = str(error)
    # An unclosed position is retained; final cash alone is never reported as equity.
    final_equity = cash + (
        position.side * position.quantity * book.mid
        if position is not None and book is not None
        else ZERO
    )
    fees = math.fsum(float(fill["fee"]) for fill in fills)
    friction = math.fsum(float(fill["slippage"]) for fill in fills)
    return {
        "status": "failed" if failure else "complete",
        "error": failure,
        "initial_equity": initial_equity,
        "max_requested_notional_usdc": float(MAX_NOTIONAL),
        "final_equity": float(final_equity),
        "net_return": float(final_equity / Decimal(str(initial_equity)) - 1),
        "max_drawdown": float(max_dd),
        "fee_cost": fees,
        "slippage_cost": friction,
        "total_funding": 0.0 if not failure else None,
        "complete_trades": len(trades),
        "same_quantity_gross_mid_pnl": math.fsum(
            -fill["side"] * fill["quantity"] * fill["mid"] for fill in fills
        )
        if position is None
        else None,
        "execution_spread_and_friction_cost": math.fsum(
            fill["side"] * fill["quantity"] * (fill["price"] - fill["mid"]) for fill in fills
        ),
        "net_pnl_per_completed_trade": float((cash - Decimal(str(initial_equity))) / len(trades))
        if trades and position is None
        else None,
        "trades": trades,
        "fills": fills,
        "ledger": ledger,
        "counts": counts,
        "liquidation_possible_on_observed_book": liquidation_possible,
        "reference_margin_exhausted": margin_exhausted,
        "open_position": (
            {
                key: float(value) if isinstance(value, Decimal) else value
                for key, value in asdict(position).items()
            }
            if position is not None
            else None
        ),
        "observed_start_utc_ns": events[0].utc_ns,
        "observed_end_utc_ns": covered_last.utc_ns,
        "observed_start_mono_ns": events[0].mono_ns,
        "observed_end_mono_ns": covered_last.mono_ns,
        "observed_start_venue_ms": events[0].venue_ms,
        "observed_end_venue_ms": covered_last.venue_ms,
        "observed_hours": (covered_last.mono_ns - events[0].mono_ns) / SECOND / 3600,
        "execution": {
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
            "latency_ns": latency_ns,
            "max_quote_wait_ns": MAX_WAIT,
            "funding_guard_clock": "native_venue_ms_with_60s_exposure_padding",
        },
    }


def evaluate(
    config: MicroConfig,
    events: Sequence[MicroEvent],
    funding_events_ms: Sequence[int],
    initial_equity: float = 10_000.0,
    *,
    end_mono_ns: int | None = None,
    benchmark: bool = False,
    stress_initial_equity: float | None = None,
) -> dict[str, Any]:
    return {
        "config": asdict(config),
        "base": replay(
            config,
            events,
            funding_events_ms,
            initial_equity,
            end_mono_ns=end_mono_ns,
            benchmark=benchmark,
        ),
        "stress": replay(
            config,
            events,
            funding_events_ms,
            initial_equity if stress_initial_equity is None else stress_initial_equity,
            fee_bps=9.0,
            slippage_bps=2.0,
            latency_ns=SECOND,
            end_mono_ns=end_mono_ns,
            benchmark=benchmark,
        ),
        "limitations": [
            "Conditional contiguous-window replay is not gap/restart recovery evidence.",
            "Native exchange and host monotonic clock correspondence is assumed.",
            "Stress replays the same fee-aware policy; exits and trade counts may differ.",
            "BTC metadata, touch capacity and taker fills are research proxies.",
            "No maker queue, collateral/liquidation certification or trading admission.",
        ],
    }
