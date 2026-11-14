"""One frozen cross-venue C100 hypothesis; pure replay, never a venue client."""

from __future__ import annotations

import hashlib
import statistics
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, localcontext
from itertools import groupby, islice
from typing import Any, Literal

from hyperliquid_bot.paper_engine.execution import (
    FillQuote,
    PositionState,
    apply_fill,
    quote_taker_fill,
)
from hyperliquid_bot.paper_engine.precision import adverse_price, floor_size, protective_price
from hyperliquid_bot.paper_risk import (
    PaperOrderIntent,
    PaperPositionKey,
    PaperSignedPosition,
    evaluate_paper_hard_limits,
    paper_snapshot_for_bounded_book,
    size_paper_notional_usdc,
)
from research.volume_repair_io import require

D = Decimal
ZERO = D(0)
ONE = D(1)
SECOND = 1_000_000_000
MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS
LOT = D("0.00001")
TARGET = D("0.01")
STOP = D("-0.05")
BUFFER = D("0.0005")
CAP = D(100)
INITIAL = D(10_000)
MAX_EQUAL_TIME_EVENTS = 100_000


@dataclass(frozen=True, slots=True)
class Cost:
    name: str
    fee: Decimal
    friction: Decimal
    latency: int


COSTS = (
    Cost("base", D(".00045"), D(".0001"), SECOND // 2),
    Cost("stress", D(".0009"), D(".0002"), SECOND),
)


@dataclass(frozen=True, slots=True)
class Level:
    price: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class Event:
    source: str
    session: str
    ordinal: int
    raw_ns: int
    utc_ns: int
    native_ms: int
    available_ns: int
    identity: str
    kind: Literal["hl_book", "bn_book", "flow", "reset", "noop"]
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()
    signed_notional: Decimal = ZERO
    witness: str | None = None
    reason: str | None = None

    @property
    def mid(self) -> Decimal | None:
        return (self.bids[0].price + self.asks[0].price) / 2 if self.bids and self.asks else None


@dataclass(frozen=True, slots=True)
class Intent:
    kind: Literal["entry", "exit"]
    side: int
    decision_ns: int
    due_ns: int
    deadline_ns: int
    quantity: Decimal
    raw_limit: Decimal
    charged_limit: Decimal
    lower: Decimal
    upper: Decimal
    reason: str
    decision_source: str


@dataclass(slots=True)
class Episode:
    entry_ns: int
    side: int
    baseline_equity: Decimal
    entry_notional: Decimal
    quantity: Decimal
    entry_fee: Decimal
    margin: Decimal
    minimum_marked_roi: Decimal = ZERO


def text(value: object) -> object:
    """Stable exact decimal fields; no binary-float financial serialization."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [text(v) for v in value]
    return value


def limits(
    reference_bid: Decimal, reference_ask: Decimal, side: int, cost: Cost
) -> tuple[Decimal, Decimal]:
    """Return separate charged-fill and raw-book IOC bounds, friction once."""
    require(side in (-1, 1), "known side")
    name = "BUY" if side == 1 else "SELL"
    if side == 1:
        exit_fill = adverse_price(
            reference_bid * (1 - BUFFER) * (1 - cost.friction), side="SELL", max_decimals=1
        )
        charged = protective_price(
            exit_fill * (1 - cost.fee) / (1 + cost.fee + TARGET / 10), side=name, max_decimals=1
        )
        raw = protective_price(charged / (1 + cost.friction), side=name, max_decimals=1)
    else:
        require(1 - cost.fee - TARGET / 10 > 0, "positive short target denominator")
        exit_fill = adverse_price(
            reference_ask * (1 + BUFFER) * (1 + cost.friction), side="BUY", max_decimals=1
        )
        charged = protective_price(
            exit_fill * (1 + cost.fee) / (1 - cost.fee - TARGET / 10), side=name, max_decimals=1
        )
        raw = protective_price(charged / (1 - cost.friction), side=name, max_decimals=1)
    return charged, raw


@dataclass(frozen=True, slots=True)
class Quoted:
    fill: FillQuote
    reference_price: Decimal
    level_index: int


class Liquidity:
    """Retain modeled consumption; unchanged snapshots never refill our size."""

    def __init__(self) -> None:
        self.last: dict[tuple[int, Decimal], Decimal] = {}
        self.used: dict[tuple[int, Decimal], Decimal] = {}

    def update(self, book: Event) -> None:
        for side, levels in ((1, book.asks), (-1, book.bids)):
            for level in levels:
                key = side, level.price
                # Available is observed_size minus prior modeled consumption. An
                # observed increase already adds that delta; do not credit it twice.
                self.used.setdefault(key, ZERO)
                self.last[key] = level.size
        require(len(self.last) <= 100_000, "bounded observed liquidity-price state")

    def quote(
        self, book: Event, order: Intent, cost: Cost, *, consume: bool = False
    ) -> list[Quoted]:
        remaining = order.quantity
        fills: list[Quoted] = []
        levels = book.asks if order.side == 1 else book.bids
        for index, level in enumerate(levels):
            if order.side * (level.price - order.raw_limit) > 0:
                break
            key = order.side, level.price
            available = max(ZERO, level.size - self.used.get(key, ZERO))
            fill = quote_taker_fill(
                side="BUY" if order.side == 1 else "SELL",
                quantity=remaining,
                touch_price=level.price,
                touch_size=available,
                slippage_fraction=cost.friction,
                taker_fee_rate=cost.fee,
                size_increment=LOT,
                max_price_decimals=1,
                source="bbo",
            )
            if fill is None:
                continue
            if not order.lower <= fill.price <= order.upper:
                # Do not skip an out-of-band best level to select a worse deeper fill.
                if not fills:
                    return []
                break
            if order.side * (fill.price - order.charged_limit) > 0:
                break
            fills.append(Quoted(fill, level.price, index))
            remaining -= fill.quantity
            if consume:
                self.used[key] = self.used.get(key, ZERO) + fill.quantity
            if remaining == 0:
                break
        return fills


def batch_binding(events: list[Event]) -> dict[str, Any]:
    """Bind rejected groups without a potentially multi-megabyte audit line."""
    digest = hashlib.sha256()
    for event in events:
        digest.update(event.identity.encode() + b"\0")
    return {
        "count": len(events),
        "ordered_source_ids_sha256": digest.hexdigest(),
        "first_source": events[0].identity if events else None,
        "last_source": events[-1].identity if events else None,
    }


class Replay:
    """Own a single deterministic modeled book and every partial-exit obligation."""

    def __init__(
        self,
        cost: Cost,
        start: int,
        end: int,
        clock_offset_ns: int,
        funding_ms: tuple[int, ...],
        emit: Callable[[dict[str, Any]], None],
    ) -> None:
        require(start < end and cost in COSTS, "fixed source interval and cost")
        self.cost, self.start, self.end, self.offset = cost, start, end, clock_offset_ns
        self.funding_ms, self.emit = funding_ms, emit
        self.state = PositionState(INITIAL, ZERO, ZERO, ZERO, ZERO)
        self.episode: Episode | None = None
        self.pending: Intent | None = None
        self.book: Event | None = None
        self.hl_blocked = False
        self.bn: Event | None = None
        self.liquidity = Liquidity()
        self.flow: deque[tuple[int, Decimal]] = deque()
        self.basis: deque[tuple[int, Decimal]] = deque()
        self.counts: Counter[str] = Counter()
        self.sessions: dict[str, str] = {}
        self.first_ns = start
        self.now = start
        self.peak = INITIAL
        self.max_drawdown = ZERO
        self.day_start = self.week_start = INITIAL
        self.day = self.week = ""
        self.halted = False
        self.risk_halt_now = False
        self.turnover = self.friction_paid = ZERO
        self.armed = True
        self.false_ticks = 0
        self.version = 0
        self.episodes: list[dict[str, Any]] = []
        self.failed: str | None = None
        self.fatal_run_error = False
        self.key = PaperPositionKey(
            "PAPER", "fast-catchup-C100", "hyperliquid", "BTC-PERP", "cross-venue-catchup-v1"
        )

    def record(self, kind: str, **values: Any) -> None:
        self.emit(
            {
                "kind": kind,
                "availability_ns": self.now,
                "environment": "PAPER",
                "execution_context": "OFFLINE_CONDITIONAL_MODEL",
                "venue": self.key.venue_id,
                "strategy_id": self.key.strategy_id,
                "configuration": "v1-C100",
                "cost_case": self.cost.name,
                **values,
            }
        )

    def fresh(self, event: Event | None, now: int) -> bool:
        return (
            event is not None
            and event.available_ns <= now
            and 0 <= now - event.raw_ns <= SECOND
            and -1000 <= (self.offset + now) // 1_000_000 - event.native_ms <= 2000
        )

    def book_ready(self, now: int) -> bool:
        return not self.hl_blocked and self.fresh(self.book, now)

    def equity(self) -> Decimal | None:
        if self.state.position_quantity == 0:
            return self.state.cash_usdc
        if self.book is None or self.book.mid is None or not self.book_ready(self.now):
            return None
        return self.state.cash_usdc + self.state.position_quantity * self.book.mid

    def mark(self) -> None:
        import datetime as dt

        equity = self.equity()
        if equity is None:
            self.counts["unavailable_equity_samples"] += 1
            return
        require(equity > 0, "modeled account insolvent")
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, 1 - equity / self.peak)
        clock = dt.datetime.fromtimestamp((self.offset + self.now) // SECOND, dt.UTC)
        day, week = clock.date().isoformat(), str(clock.isocalendar()[:2])
        if not self.day:
            self.day, self.week = day, week
        if day != self.day:
            self.day, self.day_start = day, equity
        if week != self.week:
            self.week, self.week_start = week, equity
        if self.max_drawdown >= D(".07"):
            self.halted = True
        self.risk_halt_now = (
            self.halted
            or equity - self.day_start <= -self.day_start * D(".01")
            or equity - self.week_start <= -self.week_start * D(".03")
        )
        if self.episode is not None:
            roi = (equity - self.episode.baseline_equity) / self.episode.margin
            self.episode.minimum_marked_roi = min(self.episode.minimum_marked_roi, roi)
            if roi <= -1:
                self.counts["sampled_reference_margin_exhausted"] += 1
        self.record(
            "equity",
            cash=self.state.cash_usdc,
            quantity=self.state.position_quantity,
            quote_mid=self.book.mid if self.book else None,
            equity=equity,
            sampled_drawdown=1 - equity / self.peak,
        )

    def risk(self, side: int, quantity: Decimal, price: Decimal, reduce: bool) -> bool:
        equity = self.equity()
        if equity is None:
            return False
        position = PaperSignedPosition(
            self.key, self.state.position_quantity, str(self.version), ZERO, True
        )
        snapshot = paper_snapshot_for_bounded_book(
            equity_usdc=equity,
            price=price,
            current_position=self.state.position_quantity,
            daily_pnl_usdc=equity - self.day_start,
            weekly_pnl_usdc=equity - self.week_start,
            peak_equity_usdc=self.peak,
            position=position,
        )
        decision = evaluate_paper_hard_limits(
            snapshot=snapshot,
            intent=PaperOrderIntent(
                "BUY" if side == 1 else "SELL",
                quantity,
                price,
                reduce,
                stop_distance_fraction=D(".005"),
                asset_id=self.key.instrument_id,
                strategy_id=self.key.strategy_id,
                venue_id=self.key.venue_id,
                position_key=self.key,
                position_version=position.version,
            ),
            trading_mode="PAPER",
        )
        self.record("risk", decision=decision.to_dict(), reduce_only=reduce)
        return decision.approved

    def reference(self) -> tuple[Decimal, Decimal] | None:
        if (
            self.now - self.first_ns < 305 * SECOND
            or not self.fresh(self.bn, self.now)
            or not self.book_ready(self.now)
        ):
            return None
        values = [v for t, v in self.basis if self.now - 305 * SECOND < t <= self.now - 5 * SECOND]
        if len(values) < 240 or self.bn is None:
            return None
        ratio = statistics.median(values)
        return self.bn.bids[0].price * ratio, self.bn.asks[0].price * ratio

    def entry_intent(self) -> Intent | None:
        assert self.book is not None and self.book.mid is not None
        reference = self.reference()
        if reference is None or not self.flow:
            self.counts["feature_unavailable"] += 1
            return None
        signed = sum((v for _, v in self.flow), ZERO)
        if signed == 0:
            return None
        side = 1 if signed > 0 else -1
        charged, raw = limits(*reference, side, self.cost)
        mid = self.book.mid
        lower = protective_price(mid * D(".99"), side="SELL", max_decimals=1)
        upper = protective_price(mid * D("1.01"), side="BUY", max_decimals=1)
        probe = Intent(
            "entry",
            side,
            self.now,
            self.now + self.cost.latency,
            self.now + self.cost.latency + SECOND,
            D("1000000"),
            raw,
            charged,
            lower,
            upper,
            "external_flow_cost_target",
            self.book.identity,
        )
        fills = self.liquidity.quote(self.book, probe, self.cost)
        depth = sum((f.fill.quantity * f.fill.price for f in fills), ZERO)
        equity = self.equity()
        assert equity is not None
        desired = min(
            CAP,
            equity / 2,
            depth,
            size_paper_notional_usdc(equity_usdc=equity, stop_distance_fraction=D(".005")),
        )
        quantity = floor_size(desired / upper, LOT)
        if quantity <= 0 or quantity * lower < 10:
            self.counts["size_or_depth_rejected"] += 1
            return None
        if not self.risk(side, quantity, upper, False):
            self.counts["risk_rejected"] += 1
            return None
        return Intent(
            "entry",
            side,
            self.now,
            self.now + self.cost.latency,
            self.now + self.cost.latency + SECOND,
            quantity,
            raw,
            charged,
            lower,
            upper,
            "external_flow_cost_target",
            self.book.identity,
        )

    def tick(self, now: int) -> None:
        before = self.counts.copy()
        self._tick(now)
        self.record(
            "decision",
            counter_changes=dict(self.counts - before),
            hl_source=self.book.identity if self.book else None,
            bn_source=self.bn.identity if self.bn else None,
            signed_flow_5s=sum((v for _, v in self.flow), ZERO),
            reference=self.reference(),
            armed=self.armed,
            pending=asdict(self.pending) if self.pending else None,
            position_version=self.version,
            equity=self.equity(),
        )

    def _tick(self, now: int) -> None:
        self.advance(now)
        self.counts["decision_ticks"] += 1
        while self.flow and self.flow[0][0] <= now - 5 * SECOND:
            self.flow.popleft()
        while self.basis and self.basis[0][0] <= now - 305 * SECOND:
            self.basis.popleft()
        # Append only today's genuinely available pair; never fill missing past samples.
        if (
            self.book_ready(now)
            and self.fresh(self.bn, now)
            and self.book is not None
            and self.bn is not None
            and self.book.mid is not None
            and self.bn.mid is not None
        ):
            self.basis.append((now, self.book.mid / self.bn.mid))
        if self.pending or self.episode:
            self.counts["busy_ticks"] += 1
            return
        self.mark()
        if (
            self.risk_halt_now
            or not self.book_ready(now)
            or self.book is None
            or self.book.mid is None
        ):
            self.counts["halt_or_quote_unavailable"] += 1
            self.false_ticks = 0
            return
        minute = ((self.offset + now) // 1_000_000) % HOUR_MS
        if not 2 * MINUTE_MS <= minute < 52 * MINUTE_MS or now + 68 * SECOND >= self.end:
            self.counts["calendar_or_endpoint_purge"] += 1
            return
        intent = self.entry_intent()
        if not self.armed:
            self.false_ticks = (
                self.false_ticks + 1 if intent is None and self.reference() is not None else 0
            )
            if self.false_ticks >= 5:
                self.armed, self.false_ticks = True, 0
            self.counts["dislocation_lockout"] += 1
            return
        if intent is not None:
            self.pending = intent
            self.counts["entry_intents"] += 1
            self.record(
                "intent",
                intent=asdict(intent),
                reference=self.reference(),
                book=asdict(self.book),
                binance=asdict(self.bn) if self.bn else None,
                signed_flow_5s=sum((v for _, v in self.flow), ZERO),
                equity=self.equity(),
                risk_position_version=self.version,
            )

    def advance(self, now: int) -> None:
        require(now >= self.now, "monotone modeled availability")
        if self.episode is not None:
            before_ms = (self.offset + self.now) // 1_000_000
            after_ms = (self.offset + now) // 1_000_000
            require(
                not any(before_ms <= f <= after_ms for f in self.funding_ms),
                "unknown held funding exposure",
            )
        self.now = now
        if self.pending and now > self.pending.deadline_ns:
            if self.pending.kind == "exit":
                require(False, "unknown residual exposure after exit deadline")
            self.counts["entry_nonfills"] += 1
            self.record("entry_nonfill", reason="deadline", intent=asdict(self.pending))
            self.pending = None
        if self.episode and self.pending is None:
            if not self.book_ready(now):
                self.exit("stale_guard")
            elif now >= self.episode.entry_ns + 60 * SECOND:
                self.exit("time")

    def exit(self, reason: str) -> None:
        if self.pending is not None:
            return
        require(self.episode is not None and self.book is not None, "owned exit position/book")
        assert self.episode is not None and self.book is not None
        side = -self.episode.side
        levels = self.book.asks if side == 1 else self.book.bids
        require(bool(levels), "unknown held exit side")
        reference = levels[0].price
        raw = protective_price(
            reference * (D("1.01") if side == 1 else D(".99")),
            side="BUY" if side == 1 else "SELL",
            max_decimals=1,
        )
        # The same fixed band limits both raw IOC and charged fill; never widen on retry.
        self.pending = Intent(
            "exit",
            side,
            self.now,
            self.now + self.cost.latency,
            self.now + 5 * SECOND,
            abs(self.state.position_quantity),
            raw,
            raw,
            ZERO,
            D("1e20"),
            reason,
            self.book.identity,
        )
        self.record("intent", intent=asdict(self.pending))

    def complete(self, reason: str) -> None:
        assert self.episode is not None
        ep = self.episode
        pnl = self.state.cash_usdc - ep.baseline_equity
        row = {
            "entry_ns": ep.entry_ns,
            "exit_ns": self.now,
            "side": ep.side,
            "quantity": ep.quantity,
            "entry_fill_notional": ep.entry_notional,
            "initial_reference_margin": ep.margin,
            "margin_basis": "entry_fill_notional/10",
            "net_pnl": pnl,
            "net_margin_roi": pnl / ep.margin,
            "net_ge_1pct": pnl / ep.margin >= TARGET,
            "net_ge_2pct": pnl / ep.margin >= D(".02"),
            "minimum_sampled_marked_roi": ep.minimum_marked_roi,
            "holding_seconds": D(self.now - ep.entry_ns) / SECOND,
            "exit_reason": reason,
        }
        self.episodes.append(row)
        self.record("episode", episode=row)
        self.episode = None
        self.pending = None
        self.armed, self.false_ticks = False, 0

    def execute(self, book: Event) -> None:
        order = self.pending
        if (
            order is None
            or book.raw_ns < order.due_ns
            or book.available_ns < order.due_ns
            or book.identity == order.decision_source
            or not self.fresh(book, self.now)
        ):
            return
        require(self.now <= order.deadline_ns, "bounded execution deadline")
        if order.kind == "exit":
            order = replace(order, quantity=abs(self.state.position_quantity))
            require(
                self.risk(order.side, order.quantity, book.mid or order.raw_limit, True),
                "reduce-only risk ownership rejected",
            )
        before = self.state.cash_usdc
        fills = self.liquidity.quote(book, order, self.cost, consume=True)
        self.counts["IOC_attempts"] += 1
        if order.kind == "entry":
            self.pending = None  # IOC remainder is cancelled, including a zero fill.
            self.counts["entry_nonfills"] += not bool(fills)
        for quote in fills:
            fill = quote.fill
            self.turnover += fill.price * fill.quantity
            self.friction_paid += abs(fill.price - quote.reference_price) * fill.quantity
            self.state = apply_fill(self.state, fill)
            self.version += 1
            self.counts["fills"] += 1
            self.record(
                "fill",
                order=asdict(order),
                source=book.identity,
                raw_receipt_ns=book.raw_ns,
                native_ms=book.native_ms,
                witness=book.witness,
                reference_price=quote.reference_price,
                level_index=quote.level_index,
                fill=asdict(fill),
                state=asdict(self.state),
            )
        if order.kind == "entry" and fills:
            quantity = sum((f.fill.quantity for f in fills), ZERO)
            notional = sum((f.fill.price * f.fill.quantity for f in fills), ZERO)
            require(quantity <= order.quantity and notional <= CAP, "frozen C100 quantity/cap")
            self.episode = Episode(
                self.now,
                order.side,
                before,
                notional,
                quantity,
                sum((f.fill.fee_usdc for f in fills), ZERO),
                notional / 10,
            )
            self.counts["partial_entries"] += quantity < order.quantity
            if notional < 10:
                self.counts["subminimum_partial_owned"] += 1
                self.exit("small_partial_liquidity_guard")
        elif order.kind == "exit":
            if self.state.position_quantity == 0:
                self.complete(order.reason)
            else:
                self.counts["partial_or_unfilled_exit_attempts"] += 1
                # A retry has its own delay, while original limit/deadline remain frozen.
                self.pending = replace(
                    order, due_ns=self.now + self.cost.latency, decision_source=book.identity
                )
        self.mark()

    def exit_checks(self) -> None:
        if self.episode is None or self.pending is not None or self.book is None:
            return
        if not self.book_ready(self.now):
            self.exit("stale_guard")
            return
        self.mark()
        if self.risk_halt_now:
            self.exit("risk_halt")
            return
        side = -self.episode.side
        wide = Intent(
            "exit",
            side,
            self.now,
            self.now,
            self.now,
            abs(self.state.position_quantity),
            D("1e20") if side == 1 else ZERO,
            D("1e20") if side == 1 else ZERO,
            ZERO,
            D("1e20"),
            "preview",
            "",
        )
        fills = self.liquidity.quote(self.book, wide, self.cost)
        if sum((f.fill.quantity for f in fills), ZERO) != abs(self.state.position_quantity):
            self.exit("liquidity_guard")
            return
        hypothetical = self.state
        for quote in fills:
            hypothetical = apply_fill(hypothetical, quote.fill)
        roi = (hypothetical.cash_usdc - self.episode.baseline_equity) / self.episode.margin
        reference = self.reference()
        minute = ((self.offset + self.now) // 1_000_000) % HOUR_MS
        if roi <= STOP:
            self.exit("stop")
        elif minute >= 58 * MINUTE_MS:
            self.exit("calendar")
        elif roi >= TARGET:
            self.exit("target")
        elif (
            reference is not None
            and self.book.mid is not None
            and self.episode.side * ((reference[0] + reference[1]) / 2 / self.book.mid - 1) <= 0
        ):
            self.exit("gap_closed")

    def observe(self, events: list[Event]) -> None:
        # Safety is atomic over the entire equal-time group. Cross-lane tie order
        # cannot fill an entry before a reset that is already known at this time.
        self.counts["released_events"] += len(events)
        sessions = dict(self.sessions)
        boundary = False
        for event in events:
            logical = "bn_flow" if event.kind == "flow" else event.kind
            old = sessions.get(logical)
            boundary |= event.kind == "reset" or (old is not None and old != event.session)
            sessions[logical] = event.session
        if boundary:
            require(self.episode is None, "unknown held session/source gap")
            self.pending = None
            self.flow.clear()
            self.basis.clear()
            self.book = self.bn = None
            self.hl_blocked = False
            self.sessions.clear()
            self.liquidity = Liquidity()
            self.first_ns = self.now
            self.counts["source_resets"] += 1
            self.record("source_boundary", rejected_batch=batch_binding(events))
            return  # None of a boundary's simultaneous observations is entry/fill eligible.
        invalid = [e for e in events if e.kind == "hl_book" and not self.fresh(e, self.now)]
        if invalid:
            self.hl_blocked = True
            self.counts["native_stale_hl_books"] += len(invalid)
            self.record(
                "unqualified_hl_batch",
                rejected_sources=batch_binding(invalid),
                last_valid_source=self.book.identity if self.book else None,
            )
            if self.episode is not None:
                self.exit(
                    "stale_guard"
                )  # Anchor only to the previous valid quote, never this price.
            else:
                self.pending = None
            return
        self.sessions = sessions
        for event in events:
            if event.kind == "flow":
                if 0 <= self.now - event.raw_ns < 5 * SECOND:
                    self.flow.append((event.raw_ns, event.signed_notional))
                else:
                    self.counts["expired_flow_releases"] += 1
            elif event.kind == "bn_book":
                self.bn = event
            elif event.kind == "hl_book":
                self.hl_blocked = False
                self.book = event
                self.liquidity.update(event)
                self.mark()
                self.execute(event)
        self.exit_checks()

    def run(self, events: Iterable[Event]) -> dict[str, Any]:
        from research.harness.errors import HarnessError

        tick = ((self.start + SECOND - 1) // SECOND) * SECOND
        try:
            with localcontext() as context:
                context.prec = 50
                for now, group in groupby(events, key=lambda e: e.available_ns):
                    batch = list(islice(group, MAX_EQUAL_TIME_EVENTS + 1))
                    require(len(batch) <= MAX_EQUAL_TIME_EVENTS, "bounded equal-time batch")
                    if now < self.start:
                        continue
                    if now > self.end:
                        break
                    while tick <= now:
                        self.tick(tick)
                        tick += SECOND
                    self.advance(now)
                    self.observe(batch)
                while tick <= self.end:
                    self.tick(tick)
                    tick += SECOND
                self.advance(self.end)
                require(
                    self.episode is None and self.state.position_quantity == 0,
                    "unknown residual exposure at source end",
                )
        except (HarnessError, OSError, ValueError, ArithmeticError) as error:
            self.failed = f"{type(error).__name__}: {error}"
            self.fatal_run_error = not isinstance(error, HarnessError)
            self.record(
                "failure",
                reason=self.failed,
                fatal_run_error=self.fatal_run_error,
                remaining_position=self.state.position_quantity,
                pending=asdict(self.pending) if self.pending else None,
                state=asdict(self.state),
            )
        return self.summary()

    def summary(self) -> dict[str, Any]:
        streak = maximum_streak = 0
        for episode in self.episodes:
            streak = streak + 1 if episode["net_pnl"] < 0 else 0
            maximum_streak = max(maximum_streak, streak)
        return {
            "status": "FAILED_NULLACCOUNT"
            if self.failed is not None
            else "COMPLETE_CONDITIONAL_RESEARCH",
            "failure": self.failed,
            "fatal_run_error": self.fatal_run_error,
            "initial_equity": INITIAL,
            "final_equity": None if self.failed is not None else self.state.cash_usdc,
            "net_return": None if self.failed is not None else self.state.cash_usdc / INITIAL - 1,
            "last_observed_state": asdict(self.state),
            "sampled_quote_drawdown": self.max_drawdown,
            "counts": dict(self.counts),
            "episodes": self.episodes,
            "longest_completed_loss_streak": maximum_streak,
            "worst_closed_episode_margin_ROI": min(
                (e["net_margin_roi"] for e in self.episodes), default=None
            ),
            "closed_episode_margin_ROIs": [e["net_margin_roi"] for e in self.episodes],
            "charged_turnover_USDC": self.turnover,
            "adverse_friction_and_rounding_USDC": self.friction_paid,
            "completed_trades_per_observed_hour": D(len(self.episodes))
            * 3600
            * SECOND
            / (self.end - self.start),
            "observed_hours_not_daily_extrapolation": D(self.end - self.start) / (3600 * SECOND),
            "remaining_quantity": self.state.position_quantity,
            "source_seconds": D(self.end - self.start) / SECOND,
            "full_UTC_day_return": None,
            "daily_frequency_extrapolation": None,
            "primary_target": TARGET,
            "secondary_attainment_only": D(".02"),
            "reference_leverage_not_venue_setting": 10,
            "capacity_cap_USDC": CAP,
            "capacity_500_2500": "NOT_RUN_UNREGISTERED",
            "native_execution_or_profitability_admission": False,
        }
