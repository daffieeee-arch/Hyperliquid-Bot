"""Frozen pressure forecasts versus observed prices; never orders or portfolio PnL."""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from hyperliquid_bot.paper_engine.precision import adverse_price
from research.micro_flow import GRID, SECOND, Book, Pressure, _book
from research.micro_response_io import Observation
from research.volume_repair_io import require

HORIZONS = (1, 5, 30, 120, 300)
POLICIES = tuple(
    (f"flow-w{span}-s{int(strength * 100)}", span, strength)
    for span in (30, 120)
    for strength in (0.2, 0.4)
)
COSTS = {
    "base": (500_000_000, Decimal("0.00045"), Decimal("0.0001")),
    "stress": (SECOND, Decimal("0.0009"), Decimal("0.0002")),
}
ENTRY_WAIT = SECOND
LABEL_WAIT = 15 * SECOND
MAX_RING_ITEMS = 1_000_000
MAX_RING_BYTES = 256 << 20
MAX_PENDING = 4096
MAX_PENDING_BYTES = 16 << 20


def charged_price_bps(entry: Book, label: Book, side: int, cost: str) -> float:
    """Unit price opportunity after spread/fee/friction, not a certified fill."""
    require(side in (-1, 1) and cost in COSTS, "fixed side and price-cost scenario")
    _, fee, slip = COSTS[cost]
    opening = entry.ask if side == 1 else entry.bid
    closing = label.bid if side == 1 else label.ask
    opening = adverse_price(
        opening * (1 + side * slip), side="BUY" if side == 1 else "SELL", max_decimals=1
    )
    closing = adverse_price(
        closing * (1 - side * slip), side="SELL" if side == 1 else "BUY", max_decimals=1
    )
    value = (side * (closing - opening) - fee * (opening + closing)) / opening * 10_000
    require(value.is_finite() and math.isfinite(float(value)), "finite charged price opportunity")
    return float(value)


@dataclass(order=True, slots=True)
class Request:
    due_ns: int
    sequence: int
    decision: dict[str, Any] = field(compare=False)
    cost: str = field(compare=False)
    horizon: int = field(default=0, compare=False)
    entry: Observation | None = field(default=None, compare=False)


class Window:
    """One bounded conditional source window; no position or cash account exists."""

    def __init__(
        self,
        metadata: dict[str, Any],
        capture: str,
        group: str,
        funding: tuple[int, ...],
        funding_ranges: tuple[tuple[int, int], ...],
        emit: Callable[[dict[str, Any]], None],
    ) -> None:
        self.meta, self.capture, self.group, self.funding = metadata, capture, group, funding
        self.funding_ranges = funding_ranges
        self.emit = emit
        self.pressure = {span: Pressure(span) for span in (30, 120)}
        self.book: Book | None = None
        self.observation: Observation | None = None
        self.entries: list[Request] = []
        self.labels: list[Request] = []
        self.sequence = 0
        self.next_decision = (metadata["start_mono_ns"] // GRID + 1) * GRID
        self.last_mono = metadata["start_mono_ns"]
        self.last_utc = metadata["start_utc_ns"]
        self.last_inbound: int | None = None
        self.old_native_books = 0

    def common(self) -> dict[str, Any]:
        return {"capture": self.capture, "group": self.group, "window": self.meta["id"]}

    def bound(self) -> None:
        for pressure in self.pressure.values():
            count = len(pressure.books) + len(pressure.ofi) + len(pressure.flow)
            require(
                count <= MAX_RING_ITEMS and count * 256 <= MAX_RING_BYTES,
                "feature ring count/byte bound; no silent dropping",
            )
        count = len(self.entries) + len(self.labels)
        require(
            count <= MAX_PENDING and count * 4096 <= MAX_PENDING_BYTES,
            "pending label count/byte bound; no silent dropping",
        )

    def request(
        self,
        due: int,
        decision: dict[str, Any],
        cost: str,
        horizon: int = 0,
        entry: Observation | None = None,
    ) -> Request:
        self.sequence += 1
        return Request(due, self.sequence, decision, cost, horizon, entry)

    def advance(self, instant: int, *, inclusive: bool = False) -> None:
        while self.next_decision < instant or (inclusive and self.next_decision == instant):
            if self.next_decision > self.meta["end_mono_ns"]:
                break
            self.decide(self.next_decision)
            self.next_decision += GRID

    def decide(self, now: int) -> None:
        book = self.book
        sides: dict[str, int] = {}
        reasons = {}
        momentum = 0
        fresh = book is not None and 0 <= now - book.event.mono_ns <= SECOND
        for name, span, strength in POLICIES:
            pressure = self.pressure[span]
            reason, side = "stale_quote", 0
            # Eviction is independent of eligibility. A quiet calendar period
            # must not turn a fixed trailing ring into an unbounded history.
            candidate = pressure.signal(now, book, strength, False) if book is not None else 0
            if fresh and book is not None:
                if pressure.first_ns is None or now - pressure.first_ns < span * SECOND:
                    reason = "warmup"
                elif not 120_000 <= book.event.venue_ms % 3_600_000 < 52 * 60_000:
                    reason = "calendar"
                else:
                    side = candidate
                    reason = "long" if side == 1 else "short" if side == -1 else "no_signal"
            reasons[name] = reason
            sides[name] = side
        if fresh and book is not None:
            momentum = self.pressure[30].signal(now, book, 0.0, True)
        decision = {
            **self.common(),
            "kind": "decision",
            "decision_mono_ns": now,
            "decision_utc_estimated_ns": self.last_utc + now - self.last_mono,
            "utc_mapping": "last_observed_receipt_utc_plus_monotonic_delta_not_NTP_proof",
            "directions": sides,
            "reasons": reasons,
            "momentum_direction": momentum,
            "quote_receipt_age_ns": None if book is None else now - book.event.mono_ns,
            "last_inbound_age_ns": None if self.last_inbound is None else now - self.last_inbound,
            "book": self.observation.quote_record() if self.observation else None,
        }
        self.emit(decision)
        if any(sides.values()):
            for cost, (latency, _, _) in COSTS.items():
                request = self.request(now + latency, decision, cost)
                if request.due_ns + ENTRY_WAIT > self.meta["end_mono_ns"]:
                    self.entry_result(request, None, "boundary_purged")
                else:
                    heapq.heappush(self.entries, request)
        self.bound()

    def entry_result(self, request: Request, quote: Observation | None, status: str) -> None:
        self.emit(
            {
                **request.decision,
                "kind": "entry",
                "cost": request.cost,
                "status": status,
                "entry_due_ns": request.due_ns,
                "entry_deadline_ns": request.due_ns + ENTRY_WAIT,
                "entry": quote.quote_record() if quote else None,
            }
        )
        if quote is None:
            return
        for horizon in HORIZONS:
            pending = self.request(
                quote.mono_ns + horizon * SECOND, request.decision, request.cost, horizon, quote
            )
            if pending.due_ns + LABEL_WAIT > self.meta["end_mono_ns"]:
                self.label_result(pending, None, "boundary_purged")
            else:
                heapq.heappush(self.labels, pending)
        self.bound()

    def label_result(self, request: Request, quote: Observation | None, status: str) -> None:
        entry = request.entry
        require(entry is not None, "future label has an observed entry quote")
        assert entry is not None and entry.event is not None
        result = {
            **request.decision,
            "kind": "label",
            "cost": request.cost,
            "horizon_seconds": request.horizon,
            "primary_horizon": request.horizon == 300,
            "status": status,
            "label_due_ns": request.due_ns,
            "label_deadline_ns": request.due_ns + LABEL_WAIT,
            "entry": entry.quote_record(),
            "label": quote.quote_record() if quote else None,
        }
        if quote is not None:
            assert quote.event is not None
            opening, closing = _book(entry.event), _book(quote.event)
            lower, upper = entry.event.venue_ms - 60_000, quote.event.venue_ms + 60_000
            require(
                any(start <= lower <= upper <= end for start, end in self.funding_ranges),
                "actual observation interval must fit one qualified native funding request range",
            )
            require(
                self.funding[0] <= lower and self.funding[-1] >= upper,
                "native funding census brackets actual observation interval",
            )
            result.update(
                actual_horizon_ns=quote.mono_ns - entry.mono_ns,
                label_wait_ns=quote.mono_ns - request.due_ns,
                gross_mid_bps=float((closing.mid / opening.mid - 1) * 10_000),
                charged_long_price_bps=charged_price_bps(opening, closing, 1, request.cost),
                charged_short_price_bps=charged_price_bps(opening, closing, -1, request.cost),
                funding_event_within_padded_interval=any(lower <= t <= upper for t in self.funding),
                funding_cash=None,
                price_opportunity_only=True,
                capacity_certified=False,
            )
        self.emit(result)

    def receive(self, item: Observation) -> None:
        if item.mono_ns > self.meta["end_mono_ns"]:
            self.advance(self.meta["end_mono_ns"], inclusive=True)
            return
        require(item.mono_ns >= self.last_mono, "ordered diagnostic receipt clock")
        self.advance(item.mono_ns)
        while self.entries and self.entries[0].due_ns + ENTRY_WAIT < item.mono_ns:
            self.entry_result(heapq.heappop(self.entries), None, "unavailable_quote")
        while self.labels and self.labels[0].due_ns + LABEL_WAIT < item.mono_ns:
            self.label_result(heapq.heappop(self.labels), None, "unavailable_quote")
        self.last_mono, self.last_utc = item.mono_ns, item.utc_ns
        self.last_inbound = item.last_inbound_ns
        if item.event is not None:
            event = item.event
            current: Book | None = None
            if event.kind == "book":
                candidate = _book(event)
                if self.book is not None and candidate.event.venue_ms < self.book.event.venue_ms:
                    self.old_native_books += 1
                else:
                    current = self.book = candidate
                    self.observation = item
            for pressure in self.pressure.values():
                if current is not None or event.kind == "trade":
                    pressure.add(event, current)
            self.bound()
            if current is not None:
                while self.labels and self.labels[0].due_ns <= item.mono_ns:
                    self.label_result(heapq.heappop(self.labels), item, "observed")
                while self.entries and self.entries[0].due_ns <= item.mono_ns:
                    self.entry_result(heapq.heappop(self.entries), item, "observed")
        self.advance(item.mono_ns, inclusive=True)

    def finish(self) -> None:
        self.advance(self.meta["end_mono_ns"], inclusive=True)
        while self.entries:
            self.entry_result(heapq.heappop(self.entries), None, "unavailable_quote")
        while self.labels:
            self.label_result(heapq.heappop(self.labels), None, "unavailable_quote")
