"""PAPER strategy runner: target, risk, simulated fill, ledger.

The runner accepts the same normalized events from a Parquet replay and from
a live public feed pushed in by the caller. It does not open sockets and it
does not submit venue orders. ``trading_mode`` must be PAPER.

Risk sizing and portfolio gates come from ``hyperliquid_bot.paper_risk``.
This module adds the max-position, max-notional, stale-data, and missing-price
checks around that gate, and it turns a drawdown breach into a flatten halt.
Every open position carries a stop at the same distance the sizing assumed,
so the per-trade risk budget is an enforced bound rather than a nominal one.
"""

from __future__ import annotations

import contextlib
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Final

from hyperliquid_bot.local_mode import require_local_paper_mode
from hyperliquid_bot.paper_engine.errors import PaperEngineError
from hyperliquid_bot.paper_engine.events import (
    BarEvent,
    BboEvent,
    MarketEvent,
    MarkEvent,
    TradeEvent,
    bbo_is_complete,
    bbo_mid,
)
from hyperliquid_bot.paper_engine.execution import (
    HYPERLIQUID_PERP_BASE_TAKER_FEE_RATE,
    FillQuote,
    FillSource,
    PositionState,
    apply_fill,
    quote_taker_fill,
    unrealized_pnl,
)
from hyperliquid_bot.paper_engine.ledger import (
    CLAIM_SCHEMA,
    HEALTH_SCHEMA,
    STATE_RECENT_RECORD_LIMIT,
    STATE_SCHEMA,
    RunStore,
)
from hyperliquid_bot.paper_engine.precision import (
    BTC_PERP_SZ_DECIMALS,
    HYPERLIQUID_MIN_ORDER_NOTIONAL_USDC,
    floor_size,
    perp_max_price_decimals,
    protective_price,
    size_increment,
)
from hyperliquid_bot.paper_engine.replay import load_hyperliquid_parquet_tape
from hyperliquid_bot.paper_engine.strategy import (
    NonProductionReferenceStrategy,
    PaperStrategy,
    StrategyView,
    TargetPosition,
)
from hyperliquid_bot.paper_engine.time_display import (
    TIMEZONE_NAME,
    datetime_to_utc_ns,
    format_amsterdam,
    format_utc,
    require_utc,
)
from hyperliquid_bot.paper_risk import (
    DEFAULT_PAPER_RISK_LIMITS,
    PAPER_DEFAULT_STOP_DISTANCE_FRACTION,
    PaperOrderIntent,
    PaperRiskLimits,
    effective_stop_distance_fraction,
    evaluate_paper_hard_limits,
    paper_snapshot_for_bounded_book,
    size_paper_notional_usdc,
    size_paper_quantity,
)

# A placeholder for the decision-to-venue delay, not a measurement. Measure
# the VPS-to-Hyperliquid round trip and override it; zero needs an opt-in.
DEFAULT_PAPER_LATENCY_NS: Final = 250_000_000
# Orders are IOC limits around the decision touch, as on the venue: the
# official Python SDK sends a market order as an IOC limit at mid +/- slippage.
# Entries use a tight band so hard limits hold at the worst admissible price.
DEFAULT_ENTRY_PRICE_BAND_FRACTION: Final = Decimal("0.01")
# Exits use the venue's TP/SL market-order slippage tolerance (10%), so a
# stop or flatten still closes in a fast market but not at any price.
# https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl
DEFAULT_EXIT_PRICE_BAND_FRACTION: Final = Decimal("0.10")
# The venue book never sees a PAPER fill, so the size PAPER took at a price is
# held back from that price for this long, then the level counts as refilled.
# An assumption, not a measurement: makers usually re-quote within a few blocks.
DEFAULT_TOUCH_REFILL_NS: Final = 1_000_000_000

LIMITATIONS: Final = (
    "PAPER simulation only. No venue order was submitted.",
    "The shipped reference strategy is non-production and has no researched edge.",
    "Funding is not settled. PnL is simulated, not venue-reconciled.",
    "Fills are an IOC touch or trade-print model, not a queue-position model.",
    "Stops trigger on the engine mark (venue mark when fresher, else BBO mid).",
    "run_id is create-only. This directory is not a resume checkpoint.",
    "D22-B, TESTNET, SHADOW, and LIVE are out of scope.",
)


class KillSwitch(StrEnum):
    """Engine halt state. Drawdown and stale data flatten; loss limits block entries."""

    NONE = "NONE"
    HALT_NEW = "HALT_NEW"
    FLATTEN_HALT = "FLATTEN_HALT"


def decimal_text(value: Decimal) -> str:
    """Render a decimal without scientific notation."""

    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("value must be a finite decimal.")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


@dataclass(frozen=True, slots=True)
class PaperEngineConfig:
    """Explicit PAPER economics. Defaults follow the published Hyperliquid base tier."""

    venue: str = "hyperliquid"
    instrument_id: str = "BTC-PERP"
    starting_cash_usdc: Decimal = Decimal("100000")
    taker_fee_rate: Decimal = HYPERLIQUID_PERP_BASE_TAKER_FEE_RATE
    slippage_fraction: Decimal = Decimal("0")
    latency_ns: int = DEFAULT_PAPER_LATENCY_NS
    # A decision and its fill on the same quote is optimistic; say so explicitly.
    allow_zero_latency: bool = False
    entry_price_band_fraction: Decimal = DEFAULT_ENTRY_PRICE_BAND_FRACTION
    exit_price_band_fraction: Decimal = DEFAULT_EXIT_PRICE_BAND_FRACTION
    touch_refill_ns: int = DEFAULT_TOUCH_REFILL_NS
    stale_after_ns: int = 15_000_000_000
    max_position_quantity: Decimal = Decimal("1")
    max_notional_usdc: Decimal = Decimal("100000")
    sz_decimals: int = BTC_PERP_SZ_DECIMALS
    min_order_notional_usdc: Decimal = HYPERLIQUID_MIN_ORDER_NOTIONAL_USDC
    stop_distance_fraction: Decimal = PAPER_DEFAULT_STOP_DISTANCE_FRACTION
    volatility_multiple: Decimal = Decimal("1")
    risk_limits: PaperRiskLimits = DEFAULT_PAPER_RISK_LIMITS
    source_commit: str = "unknown"
    image_digest: str = "unknown"
    trading_mode: str | None = None
    # fsync the ledger and run claim once per event. A live PAPER run keeps
    # this on; an offline replay may turn it off because it can be re-run.
    durable_ledger: bool = True

    def __post_init__(self) -> None:
        require_local_paper_mode(self.trading_mode)
        if self.venue != "hyperliquid":
            raise PaperEngineError("this engine build is the Hyperliquid PAPER profile.")
        if type(self.instrument_id) is not str or not self.instrument_id:
            raise PaperEngineError("instrument_id must be non-empty text.")
        _require_positive(self.starting_cash_usdc, field_name="starting_cash_usdc")
        _require_non_negative(self.taker_fee_rate, field_name="taker_fee_rate")
        _require_non_negative(self.slippage_fraction, field_name="slippage_fraction")
        if type(self.latency_ns) is not int or self.latency_ns < 0:
            raise PaperEngineError("latency_ns must be a non-negative integer.")
        if type(self.allow_zero_latency) is not bool:
            raise PaperEngineError("allow_zero_latency must be a bool.")
        if self.latency_ns == 0 and not self.allow_zero_latency:
            raise PaperEngineError(
                "latency_ns 0 fills a decision on its own quote; set allow_zero_latency=True "
                "to accept that optimistic assumption."
            )
        for band_name, band in (
            ("entry_price_band_fraction", self.entry_price_band_fraction),
            ("exit_price_band_fraction", self.exit_price_band_fraction),
        ):
            _require_positive(band, field_name=band_name)
            if band >= 1:
                raise PaperEngineError(f"{band_name} must be a fraction below 1.")
            if self.slippage_fraction >= band:
                raise PaperEngineError(
                    f"slippage_fraction must be below {band_name}, or no order could fill."
                )
        if type(self.touch_refill_ns) is not int or self.touch_refill_ns <= 0:
            # Zero would let two fills on one quote both take its full size.
            raise PaperEngineError("touch_refill_ns must be a positive integer.")
        if type(self.stale_after_ns) is not int or self.stale_after_ns <= 0:
            raise PaperEngineError("stale_after_ns must be a positive integer.")
        _require_positive(self.max_position_quantity, field_name="max_position_quantity")
        _require_positive(self.max_notional_usdc, field_name="max_notional_usdc")
        _require_positive(self.min_order_notional_usdc, field_name="min_order_notional_usdc")
        _require_positive(self.stop_distance_fraction, field_name="stop_distance_fraction")
        _require_positive(self.volatility_multiple, field_name="volatility_multiple")
        if self.volatility_multiple < 1:
            raise PaperEngineError("volatility_multiple must be >= 1.")
        effective_stop = self.stop_distance_fraction * self.volatility_multiple
        if effective_stop >= 1:
            raise PaperEngineError("the effective stop distance must be a fraction below 1.")
        if effective_stop <= self.slippage_fraction:
            raise PaperEngineError(
                "the effective stop distance must exceed slippage_fraction, "
                "or every fill would be stopped out at once."
            )
        if type(self.sz_decimals) is not int:
            raise PaperEngineError("sz_decimals must be an integer.")
        perp_max_price_decimals(self.sz_decimals)
        if type(self.risk_limits) is not PaperRiskLimits:
            raise PaperEngineError("risk_limits must be a PaperRiskLimits value.")
        if type(self.source_commit) is not str or not self.source_commit:
            raise PaperEngineError("source_commit must be non-empty text.")
        if type(self.image_digest) is not str or not self.image_digest:
            raise PaperEngineError("image_digest must be non-empty text.")
        if type(self.durable_ledger) is not bool:
            raise PaperEngineError("durable_ledger must be a bool.")


@dataclass(frozen=True, slots=True)
class _DesiredOrder:
    side: str
    quantity: Decimal
    reduce_only: bool


@dataclass(frozen=True, slots=True)
class _WorkingOrder:
    """A pending IOC order.

    ``quantity`` is the floored, risk-clipped size that will be filled.
    ``desired_quantity`` is the unrounded size the strategy asked for. A
    repeated target is matched against ``desired_quantity`` so a clipped
    order is not cancelled and re-armed on every event while it waits out
    the configured latency.
    """

    client_order_id: str
    side: str
    quantity: Decimal
    desired_quantity: Decimal
    reduce_only: bool
    # IOC limit: a BUY never fills above it, a SELL never below it.
    limit_price: Decimal
    eligible_received_ns: int
    decision_received_ns: int
    reason: str
    clipped: bool
    # A kill flatten or stop exit: zero latency, never replaced by itself.
    immediate_exit: bool


@dataclass(frozen=True, slots=True)
class _Taken:
    """Size PAPER took at one book price, held back until ``until_ns``."""

    quantity: Decimal
    until_ns: int


class PaperEngine:
    """One create-only PAPER run. Push events, or replay a Parquet tape."""

    def __init__(
        self,
        *,
        store_root: Path,
        run_id: str,
        created_at_utc: datetime,
        config: PaperEngineConfig | None = None,
        strategy: PaperStrategy | None = None,
    ) -> None:
        self._config = config if config is not None else PaperEngineConfig()
        if type(self._config) is not PaperEngineConfig:
            raise PaperEngineError("config must be a PaperEngineConfig.")
        self._mode = require_local_paper_mode(self._config.trading_mode)
        self._created_at = require_utc(created_at_utc, field_name="created_at_utc")
        self._created_ns = datetime_to_utc_ns(self._created_at, field_name="created_at_utc")
        self._strategy = (
            strategy
            if strategy is not None
            else NonProductionReferenceStrategy(instrument_id=self._config.instrument_id)
        )
        _require_strategy(self._strategy)
        self._store = RunStore(store_root, run_id, durable=self._config.durable_ledger)
        self._size_increment = size_increment(self._config.sz_decimals)
        self._max_price_decimals = perp_max_price_decimals(self._config.sz_decimals)
        self._position = PositionState(
            cash_usdc=self._config.starting_cash_usdc,
            position_quantity=Decimal("0"),
            average_entry_price=Decimal("0"),
            realized_pnl_usdc=Decimal("0"),
            fees_usdc=Decimal("0"),
        )
        self._peak = self._config.starting_cash_usdc
        # Windows start at the first event, not at created_at: a replay is
        # created after the tape it plays.
        self._day: date | None = None
        self._day_start = self._config.starting_cash_usdc
        self._week: tuple[int, int] | None = None
        self._week_start = self._config.starting_cash_usdc
        self._last_daily_pnl = Decimal("0")
        self._last_weekly_pnl = Decimal("0")
        self._kill = KillSwitch.NONE
        self._kill_reason: str | None = None
        self._bbo: BboEvent | None = None
        self._trade: TradeEvent | None = None
        self._bar: BarEvent | None = None
        self._venue_mark: Decimal | None = None
        self._venue_mark_ns: int | None = None
        self._bbo_mark: Decimal | None = None
        self._bbo_mark_ns: int | None = None
        self._last_data_ns: int | None = None
        self._last_event_time: datetime | None = None
        self._working: _WorkingOrder | None = None
        self._next_order_number = 1
        # Stop for the open position, set from its average entry when it opens.
        self._stop_price: Decimal | None = None
        # Trades are counted in processing order; the last-trade stop fallback
        # only uses a trade that arrived after the stop was set.
        self._trade_count = 0
        self._stop_set_trade_count: int | None = None
        self._stop_exit_pending = False
        # +1 after a long was stopped, -1 after a short: blocks re-entering the
        # same direction until the strategy's target goes flat or reverses.
        self._stop_lockout = 0
        # Size PAPER took per (order side, book price), held back from the
        # displayed size there for touch_refill_ns. A trade print is used up
        # until the next print.
        self._book_taken: dict[tuple[str, Decimal], _Taken] = {}
        self._trade_taken = Decimal("0")
        # state.json is rewritten on every event, so it keeps only the most
        # recent records. ledger.jsonl stays the complete audit trail.
        self._orders: deque[dict[str, object]] = deque(maxlen=STATE_RECENT_RECORD_LIMIT)
        self._fills: deque[dict[str, object]] = deque(maxlen=STATE_RECENT_RECORD_LIMIT)
        self._rejections: deque[dict[str, object]] = deque(maxlen=STATE_RECENT_RECORD_LIMIT)
        self._order_count = 0
        self._fill_count = 0
        self._rejection_count = 0
        # The last recorded rejection. Re-evaluating the same blocked order
        # on later events is not a new rejection and writes nothing.
        self._last_block: tuple[str, str | None, str, _DesiredOrder | None] | None = None
        self._closed = False
        self._failed = False
        self._failed_at: datetime | None = None
        self._missing_flatten = False
        # An immediate exit is waiting because PAPER already used up the quote.
        self._exit_waiting_for_quote = False
        self._store.write_claim(self._claim())
        self._persist(self._created_at)

    @property
    def run_id(self) -> str:
        return self._store.run_id

    @property
    def health_path(self) -> Path:
        return self._store.health_path

    @property
    def state_path(self) -> Path:
        return self._store.state_path

    @property
    def kill_switch(self) -> str:
        return self._kill.value

    @property
    def position_quantity(self) -> Decimal:
        return self._position.position_quantity

    def run_events(self, events: Iterable[MarketEvent]) -> None:
        for event in events:
            self.on_event(event)

    def run_parquet(self, parquet_paths: Sequence[Path]) -> None:
        """Replay completed Hyperliquid BTC-PERP parts through this run."""

        self.run_events(
            load_hyperliquid_parquet_tape(
                parquet_paths,
                instrument_id=self._config.instrument_id,
                venue=self._config.venue,
            )
        )

    def on_event(self, event: MarketEvent) -> None:
        """Advance one normalized event. Replay and live feeds both call this."""

        self._require_open()
        self._check_event(event)
        with self._fail_closed(event.event_time_utc):
            self._advance(event)

    def _advance(self, event: MarketEvent) -> None:
        if self._stale_gap(event.received_utc_ns):
            self._flatten_halt("stale_data")
            self._last_data_ns = event.received_utc_ns
            self._last_event_time = event.event_time_utc
            self._update_market(event)
            self._enforce_flat(event.received_utc_ns, event.event_time_utc)
            self._persist(event.event_time_utc)
            return
        self._last_data_ns = event.received_utc_ns
        self._last_event_time = event.event_time_utc
        self._update_market(event)
        self._try_fill(event)
        self._check_stop(event.received_utc_ns)
        self._mark_limits(event.event_time_utc)
        self._enforce_flat(event.received_utc_ns, event.event_time_utc)
        if self._kill is not KillSwitch.FLATTEN_HALT:
            self._apply_strategy(event)
            self._try_fill(event)
            self._check_stop(event.received_utc_ns)
        self._mark_limits(event.event_time_utc)
        self._enforce_flat(event.received_utc_ns, event.event_time_utc)
        self._persist(event.event_time_utc)

    def on_clock(self, *, now_utc_ns: int, now_utc: datetime) -> None:
        """Notice a quiet feed. The caller supplies the clock so replay stays deterministic."""

        self._require_open()
        if type(now_utc_ns) is not int or now_utc_ns < 0:
            raise PaperEngineError("now_utc_ns must be a non-negative integer.")
        observed = require_utc(now_utc, field_name="now_utc")
        anchor = self._created_ns if self._last_data_ns is None else self._last_data_ns
        if now_utc_ns < anchor:
            raise PaperEngineError("clock moved backwards.")
        with self._fail_closed(observed):
            if now_utc_ns - anchor > self._config.stale_after_ns:
                self._flatten_halt("stale_data")
                self._enforce_flat(now_utc_ns, observed)
            elif self._exit_waiting_for_quote:
                self._retry_waiting_exit(now_utc_ns, observed)
            self._persist(observed)

    def _retry_waiting_exit(self, now_utc_ns: int, observed: datetime) -> None:
        """Retry an exit that waits on a used-up level, once it may have refilled.

        The BBO feed only pushes changes, so a quiet, unchanged book would hold
        the exit until the stale-data halt. Only this wait is retried on the
        clock: a band or a missing touch needs a new quote, not more time.
        """

        fills_before = self._fill_count
        if self._stop_exit_pending:
            self._check_stop(now_utc_ns)
        self._enforce_flat(now_utc_ns, observed)
        if self._fill_count != fills_before and self._last_event_time is not None:
            # A fill changed realized equity: apply the limits now rather than
            # at the next event. Loss windows roll on venue event time only,
            # never on the caller's clock, so the last event's window is used.
            self._mark_limits(self._last_event_time)
            self._enforce_flat(now_utc_ns, observed)

    def close(self) -> None:
        """Write the final health file. The run still cannot be resumed."""

        if self._closed:
            raise PaperEngineError("run is already closed.")
        self._closed = True
        if self._failed:
            # The ledger is refused after a failure; write the final
            # projections directly and let a write error reach the caller.
            observed = self._failed_at or self._created_at
            self._store.write_health(self._health(observed))
            self._store.write_state(self._state())
            return
        self._persist(self._last_event_time or self._created_at)

    def _require_open(self) -> None:
        if self._failed:
            raise PaperEngineError("run failed mid-event; start a new run_id.")
        if self._closed:
            raise PaperEngineError("run is closed.")

    @contextlib.contextmanager
    def _fail_closed(self, observed_at: datetime) -> Iterator[None]:
        """Stop the run if applying an event or clock tick raises.

        Fills and orders already applied in memory are written to the ledger
        and the projections are rewritten with status FAILED. The run then
        refuses further events, so it never continues from a half-applied
        event. Write errors here are swallowed so the caller sees the original
        exception; the store refuses appends after a failed write, so nothing
        is written twice.
        """

        try:
            yield
        except BaseException:
            self._failed = True
            self._failed_at = observed_at
            with contextlib.suppress(Exception):
                self._store.commit()
            self._write_failed_projections(observed_at)
            raise

    def _write_failed_projections(self, observed_at: datetime) -> None:
        # Health first and on its own: it is what monitoring reads.
        with contextlib.suppress(Exception):
            self._store.write_health(self._health(observed_at))
        with contextlib.suppress(Exception):
            self._store.write_state(self._state())

    def _check_event(self, event: MarketEvent) -> None:
        if event.venue != self._config.venue or event.instrument_id != self._config.instrument_id:
            raise PaperEngineError("event venue or instrument does not match the run.")

    def _stale_gap(self, received_utc_ns: int) -> bool:
        if self._last_data_ns is None:
            return False
        return received_utc_ns - self._last_data_ns > self._config.stale_after_ns

    def _update_market(self, event: MarketEvent) -> None:
        if isinstance(event, BboEvent):
            self._bbo = event
            if bbo_is_complete(event):
                mid = bbo_mid(event)
                self._bbo_mark = mid
                self._bbo_mark_ns = event.received_utc_ns
            else:
                self._bbo_mark = None
                self._bbo_mark_ns = None
            return
        if isinstance(event, TradeEvent):
            self._trade = event
            self._trade_count += 1
            self._trade_taken = Decimal("0")
            return
        if isinstance(event, MarkEvent):
            self._venue_mark = event.mark_price
            self._venue_mark_ns = event.received_utc_ns if event.mark_price is not None else None
            return
        if isinstance(event, BarEvent):
            self._bar = event
            return
        raise PaperEngineError("unsupported market event.")

    def _display_mark(self) -> tuple[Decimal, str] | None:
        venue_ns = -1 if self._venue_mark_ns is None else self._venue_mark_ns
        bbo_ns = -1 if self._bbo_mark_ns is None else self._bbo_mark_ns
        if self._venue_mark is not None and venue_ns >= bbo_ns:
            return self._venue_mark, "venue_mark"
        if self._bbo_mark is not None:
            return self._bbo_mark, "bbo_mid"
        return None

    def _equity(self) -> Decimal | None:
        if self._position.position_quantity == 0:
            return self._position.cash_usdc
        marked = self._display_mark()
        if marked is None:
            return None
        return self._position.cash_usdc + (self._position.position_quantity * marked[0])

    def _mark_limits(self, event_time: datetime) -> None:
        equity = self._equity()
        if equity is None:
            if self._position.position_quantity != 0:
                self._halt_new("missing_price")
            return
        self._roll_windows(event_time, equity)
        if equity > self._peak:
            self._peak = equity
        drawdown = (self._peak - equity) / self._peak
        if drawdown >= self._config.risk_limits.drawdown_kill_fraction:
            self._flatten_halt("drawdown")
            return
        daily_limit = self._day_start * self._config.risk_limits.daily_loss_fraction
        weekly_limit = self._week_start * self._config.risk_limits.weekly_loss_fraction
        self._last_daily_pnl = equity - self._day_start
        self._last_weekly_pnl = equity - self._week_start
        if self._last_daily_pnl <= -daily_limit:
            self._halt_new("daily_loss")
        if self._last_weekly_pnl <= -weekly_limit:
            self._halt_new("weekly_loss")

    def _roll_windows(self, event_time: datetime, equity: Decimal) -> None:
        """Start a new UTC day / ISO week. A late, older event never rolls back."""

        day = event_time.date()
        iso = event_time.isocalendar()
        week = (iso.year, iso.week)
        if self._day is None or self._week is None:
            self._day = day
            self._week = week
            return
        if day > self._day:
            self._day = day
            self._day_start = equity
            self._release_halt("daily_loss")
        if week > self._week:
            self._week = week
            self._week_start = equity
            self._release_halt("weekly_loss")

    def _release_halt(self, reason: str) -> None:
        """Lift a loss-limit entry halt when its window ends; other halts stay."""

        if self._kill is not KillSwitch.HALT_NEW or self._kill_reason != reason:
            return
        self._kill = KillSwitch.NONE
        self._kill_reason = None
        self._store.append(
            {"type": "kill_switch", "state": self._kill.value, "reason": f"{reason}_window_reset"}
        )

    def _apply_strategy(self, event: MarketEvent) -> None:
        target = self._strategy.on_market(event, self._view())
        if target is None:
            return
        if type(target) is not TargetPosition:
            raise PaperEngineError("strategy must return a TargetPosition or None.")
        if target.instrument_id != self._config.instrument_id:
            self._reject(
                reason="instrument_mismatch",
                detail=target.reason,
                received_utc_ns=event.received_utc_ns,
                desired=None,
            )
            return
        if self._stop_exit_pending:
            # The stop exit owns the position until it is flat.
            return
        desired = _desired_order(self._position.position_quantity, target.target_quantity)
        if self._stop_lockout != 0:
            if _sign(target.target_quantity) == self._stop_lockout:
                self._reject(
                    reason="stop_lockout",
                    detail=target.reason,
                    received_utc_ns=event.received_utc_ns,
                    desired=desired,
                )
                return
            self._stop_lockout = 0
            self._store.append(
                {
                    "type": "stop_lockout_cleared",
                    "mode": "PAPER",
                    "target_quantity": decimal_text(target.target_quantity),
                    "received_utc_ns": event.received_utc_ns,
                }
            )
        self._submit_desired(
            desired,
            received_ns=event.received_utc_ns,
            reason=target.reason,
            immediate=False,
            fills_on_decision_quote=(
                self._config.latency_ns == 0 and event is self._current_touch_event()
            ),
        )

    def _check_stop(self, received_ns: int) -> None:
        """Exit the open position once the mark crosses its stop.

        Hyperliquid triggers TP/SL orders on the mark price; the engine uses
        its own mark (the venue mark when fresher, else the BBO mid). The exit
        is a zero-latency reduce-only IOC because a venue trigger does not
        wait on our round trip. It is retried on later events until flat.
        """

        position = self._position.position_quantity
        if position == 0 or self._stop_price is None:
            return
        if not self._stop_exit_pending:
            marked = self._display_mark()
            trade = self._trade
            if (
                marked is None
                and trade is not None
                and self._stop_set_trade_count is not None
                and self._trade_count > self._stop_set_trade_count
            ):
                # No mark at all (one-sided book, no venue mark): a print
                # processed after the stop was set still protects the position.
                # Venue times are not compared: a re-delivered old print may
                # exit early, but a skewed or mis-stamped one never hides a real
                # stop. This is an exit trigger only; equity still treats the
                # price as missing.
                marked = (trade.price, "last_trade")
            if marked is None:
                return
            mark, source = marked
            crossed = mark <= self._stop_price if position > 0 else mark >= self._stop_price
            if not crossed:
                return
            self._stop_exit_pending = True
            self._stop_lockout = _sign(position)
            self._store.append(
                {
                    "type": "stop_triggered",
                    "mode": "PAPER",
                    "stop_price": decimal_text(self._stop_price),
                    "mark_price": decimal_text(mark),
                    "mark_source": source,
                    "position_quantity": decimal_text(position),
                    "received_utc_ns": received_ns,
                }
            )
        if self._kill is KillSwitch.FLATTEN_HALT:
            # The kill flatten already closes the position.
            return
        self._attempt_immediate_exit(_flatten_desired(position), received_ns, "stop-exit")

    def _attempt_immediate_exit(
        self, desired: _DesiredOrder | None, received_ns: int, reason: str
    ) -> None:
        if desired is not None and self._touch_used_up(desired.side, received_ns):
            # Our own earlier fill took what this quote shows (for example a
            # partial exit): wait for a refill or a new price rather than take
            # it twice. A strategy order must not fill meanwhile: cancel it.
            self._exit_waiting_for_quote = True
            if self._working is not None and not self._working.immediate_exit:
                self._cancel_working(received_ns, f"{reason}-pending")
            return
        self._exit_waiting_for_quote = False
        self._submit_desired(desired, received_ns=received_ns, reason=reason, immediate=True)
        self._fill_working_from_book(received_ns)

    def _current_touch_event(self) -> BboEvent | TradeEvent | None:
        """The quote an order is priced against: a complete BBO, else the last trade."""

        if self._bbo is not None and bbo_is_complete(self._bbo):
            return self._bbo
        return self._trade

    def _available(
        self, event: BboEvent | TradeEvent, side: str, received_ns: int
    ) -> tuple[Decimal | None, Decimal | None, bool]:
        """Touch price, the size PAPER may still take, and whether its own fills used it up.

        A displayed size of zero is not "used up": that is a missing touch.
        """

        price, size = _displayed(event, side)
        if price is None or size is None or size <= 0:
            return price, size, False
        if isinstance(event, TradeEvent):
            taken = self._trade_taken
        else:
            held = self._book_taken.get((side, price))
            taken = Decimal("0") if held is None or held.until_ns <= received_ns else held.quantity
        if taken <= 0:
            return price, size, False
        left = size - taken
        if left <= 0:
            return price, Decimal("0"), True
        return price, left, False

    def _touch_used_up(self, side: str, received_ns: int) -> bool:
        event = self._current_touch_event()
        if event is None:
            return False
        return self._available(event, side, received_ns)[2]

    def _enforce_flat(self, received_ns: int, event_time: datetime) -> None:
        """Apply the kill switch. Runs after every limit check and stale-data halt."""

        del event_time
        if self._kill is KillSwitch.NONE:
            return
        if self._working is not None and not self._working.reduce_only:
            # A waiting entry would add risk the switch now forbids.
            self._cancel_working(received_ns, "halted")
        if self._kill is not KillSwitch.FLATTEN_HALT:
            return
        desired = _flatten_desired(self._position.position_quantity)
        if desired is None:
            if self._working is not None:
                self._cancel_working(received_ns, "flatten-already-flat")
            self._missing_flatten = False
            return
        self._attempt_immediate_exit(desired, received_ns, "kill-flatten")

    def _submit_desired(
        self,
        desired: _DesiredOrder | None,
        *,
        received_ns: int,
        reason: str,
        immediate: bool,
        fills_on_decision_quote: bool = False,
    ) -> None:
        if desired is None:
            self._last_block = None
            if self._working is not None:
                self._cancel_working(received_ns, "target-flat")
            return
        if (
            self._working is not None
            and _same_desire(self._working, desired)
            and not (
                immediate
                and not self._working.immediate_exit
                and self._working.eligible_received_ns > received_ns
            )
        ):
            # A kill flatten or stop exit is zero-latency. A matching strategy
            # order still waiting out its latency is replaced, not reused.
            return
        if self._working is not None:
            self._cancel_working(received_ns, "replaced")
        if self._kill is KillSwitch.HALT_NEW and not desired.reduce_only:
            self._reject(
                reason="halt_new",
                detail=self._kill_reason,
                received_utc_ns=received_ns,
                desired=desired,
            )
            return
        if self._kill is KillSwitch.FLATTEN_HALT and not desired.reduce_only:
            self._reject(
                reason="flatten_halt",
                detail=self._kill_reason,
                received_utc_ns=received_ns,
                desired=desired,
            )
            return
        priced = self._price_order(desired.side)
        if priced is None:
            self._missing_flatten = bool(desired.reduce_only)
            self._reject(
                reason="missing_price", detail=reason, received_utc_ns=received_ns, desired=desired
            )
            return
        touch_price, _touch_size = priced
        if fills_on_decision_quote and self._touch_used_up(desired.side, received_ns):
            # The order would fill on this very quote, which PAPER already
            # took: one recorded block, not an accepted-then-cancelled IOC on
            # every event. Any other order meets the depletion check when it
            # is filled.
            self._reject(
                reason="touch_consumed",
                detail=reason,
                received_utc_ns=received_ns,
                desired=desired,
            )
            return
        limit_price = self._limit_price(desired, touch_price)
        # The notional a fill can reach. A BUY limit caps it; a SELL limit only
        # floors the price, so a short gets the same cushion above the touch
        # (a bid rise beyond the band while the order waits is not bounded).
        risk_price = touch_price * (Decimal(1) + self._config.entry_price_band_fraction)
        quantity = floor_size(desired.quantity, self._size_increment)
        if quantity <= 0:
            self._reject(
                reason="below_increment",
                detail=reason,
                received_utc_ns=received_ns,
                desired=desired,
            )
            return
        clipped = False
        if not desired.reduce_only:
            # Size and check hard limits at the risk price, so they still hold
            # when the IOC fills anywhere inside its band.
            sized = self._clip_entry(quantity, risk_price)
            if sized is None:
                self._reject(
                    reason="risk_based_size",
                    detail=reason,
                    received_utc_ns=received_ns,
                    desired=desired,
                )
                return
            quantity, clipped = sized
            projected = self._position.position_quantity + _signed(desired.side, quantity)
            if abs(projected) > self._config.max_position_quantity:
                self._reject(
                    reason="max_position",
                    detail=reason,
                    received_utc_ns=received_ns,
                    desired=desired,
                )
                return
            projected_notional = abs(projected) * risk_price
            if projected_notional > self._config.max_notional_usdc:
                self._reject(
                    reason="max_notional",
                    detail=reason,
                    received_utc_ns=received_ns,
                    desired=desired,
                )
                return
            # The venue values an order at its limit price; a SELL limit is
            # below the touch, so use whichever of the two is lower.
            if quantity * min(touch_price, limit_price) < self._config.min_order_notional_usdc:
                self._reject(
                    reason="min_notional",
                    detail=reason,
                    received_utc_ns=received_ns,
                    desired=desired,
                )
                return
            if not self._paper_risk_allows(
                side=desired.side,
                quantity=quantity,
                price=risk_price,
                reduce_only=False,
                received_ns=received_ns,
                desired=desired,
            ):
                return
        elif not self._paper_risk_allows(
            side=desired.side,
            quantity=quantity,
            price=touch_price,
            reduce_only=True,
            received_ns=received_ns,
            desired=desired,
        ):
            return
        latency = 0 if immediate else self._config.latency_ns
        client_order_id = f"{self._store.run_id}-{self._next_order_number:06d}"
        self._next_order_number += 1
        self._working = _WorkingOrder(
            client_order_id=client_order_id,
            side=desired.side,
            quantity=quantity,
            desired_quantity=desired.quantity,
            reduce_only=desired.reduce_only,
            limit_price=limit_price,
            eligible_received_ns=received_ns + latency,
            decision_received_ns=received_ns,
            reason=reason,
            clipped=clipped,
            immediate_exit=immediate,
        )
        self._missing_flatten = False
        self._last_block = None
        self._store.append(
            {
                "type": "order_accepted",
                "mode": "PAPER",
                "client_order_id": client_order_id,
                "side": desired.side,
                "quantity": decimal_text(quantity),
                "reduce_only": desired.reduce_only,
                "limit_price": decimal_text(limit_price),
                "touch_price": decimal_text(touch_price),
                "reason": reason,
                "clipped_to_risk_size": clipped,
                "received_utc_ns": received_ns,
                "venue_orders_submitted": False,
            }
        )

    def _limit_price(self, desired: _DesiredOrder, touch_price: Decimal) -> Decimal:
        band = (
            self._config.exit_price_band_fraction
            if desired.reduce_only
            else self._config.entry_price_band_fraction
        )
        raw = (
            touch_price * (Decimal(1) + band)
            if desired.side == "BUY"
            else touch_price * (Decimal(1) - band)
        )
        return protective_price(raw, side=desired.side, max_decimals=self._max_price_decimals)

    def _clip_entry(self, quantity: Decimal, price: Decimal) -> tuple[Decimal, bool] | None:
        equity = self._equity()
        if equity is None or equity <= 0:
            return None
        notional = size_paper_notional_usdc(
            equity_usdc=equity,
            stop_distance_fraction=self._config.stop_distance_fraction,
            risk_per_trade_fraction=self._config.risk_limits.risk_per_trade_fraction,
            volatility_multiple=self._config.volatility_multiple,
        )
        sized = size_paper_quantity(
            notional_usdc=notional,
            price=price,
            increment=self._size_increment,
        )
        if sized <= 0:
            return None
        if quantity > sized:
            return sized, True
        return quantity, False

    def _paper_risk_allows(
        self,
        *,
        side: str,
        quantity: Decimal,
        price: Decimal,
        reduce_only: bool,
        received_ns: int,
        desired: _DesiredOrder,
    ) -> bool:
        equity = self._equity()
        if equity is None:
            if not reduce_only:
                self._reject(
                    reason="missing_price",
                    detail="equity",
                    received_utc_ns=received_ns,
                    desired=desired,
                )
                return False
            equity = self._position.cash_usdc + (self._position.position_quantity * price)
        if equity <= 0:
            if reduce_only:
                return True
            self._reject(
                reason="non_positive_equity",
                detail=None,
                received_utc_ns=received_ns,
                desired=desired,
            )
            return False
        peak = self._peak if self._peak >= equity else equity
        snapshot = paper_snapshot_for_bounded_book(
            equity_usdc=equity,
            price=price,
            current_position=self._position.position_quantity,
            daily_pnl_usdc=self._last_daily_pnl,
            weekly_pnl_usdc=self._last_weekly_pnl,
            peak_equity_usdc=peak,
        )
        decision = evaluate_paper_hard_limits(
            snapshot=snapshot,
            intent=PaperOrderIntent(
                side=side,
                quantity=quantity,
                price=price,
                reduce_only=reduce_only,
                stop_distance_fraction=self._config.stop_distance_fraction,
                volatility_multiple=self._config.volatility_multiple,
                asset_id=self._config.instrument_id,
                strategy_id=self._strategy.strategy_id,
                venue_id=self._config.venue,
            ),
            limits=self._config.risk_limits,
            trading_mode=self._mode,
        )
        if decision.approved:
            return True
        self._reject(
            reason="paper_risk",
            detail="; ".join(decision.reasons),
            received_utc_ns=received_ns,
            desired=desired,
        )
        return False

    def _price_order(self, side: str) -> tuple[Decimal, Decimal] | None:
        """Return the real touch. A one-sided or crossed book is not a price."""

        event = self._current_touch_event()
        if event is None:
            return None
        price, size = _displayed(event, side)
        if price is None or size is None:
            return None
        return price, size

    def _try_fill(self, event: MarketEvent) -> None:
        working = self._working
        if working is None or event.received_utc_ns < working.eligible_received_ns:
            return
        if isinstance(event, BboEvent) or (
            isinstance(event, TradeEvent) and not self._book_complete()
        ):
            self._fill_against(working, event, received_ns=event.received_utc_ns)

    def _fill_working_from_book(self, received_ns: int) -> None:
        working = self._working
        if working is None or received_ns < working.eligible_received_ns:
            return
        event = self._current_touch_event()
        if event is None:
            # The flag reports a blocked exit of an open position.
            self._missing_flatten = working.reduce_only and self._position.position_quantity != 0
            return
        self._fill_against(working, event, received_ns=received_ns)

    def _fill_against(
        self, working: _WorkingOrder, event: BboEvent | TradeEvent, *, received_ns: int
    ) -> None:
        quote: FillQuote | None
        unfilled: str | None
        if isinstance(event, BboEvent) and not bbo_is_complete(event):
            quote, unfilled = None, "no_touch"
        else:
            quote, unfilled = self._quote(working, event=event, received_ns=received_ns)
        self._complete_ioc(
            working, quote, event=event, received_utc_ns=received_ns, unfilled_reason=unfilled
        )

    def _quote(
        self, working: _WorkingOrder, *, event: BboEvent | TradeEvent, received_ns: int
    ) -> tuple[FillQuote | None, str | None]:
        """Price a fill, or say why the IOC does not fill.

        ``no_touch``: no price on that side. ``touch_consumed``: PAPER's own
        fills already took the displayed size at that price (see
        ``touch_refill_ns``). ``price_band``: the fill would be beyond the
        order's limit.
        """

        source: FillSource = "bbo" if isinstance(event, BboEvent) else "trade"
        touch_price, touch_size, used_up = self._available(event, working.side, received_ns)
        if used_up:
            return None, "touch_consumed"
        quote = quote_taker_fill(
            side=working.side,
            quantity=working.quantity,
            touch_price=touch_price,
            touch_size=touch_size,
            slippage_fraction=self._config.slippage_fraction,
            taker_fee_rate=self._config.taker_fee_rate,
            size_increment=self._size_increment,
            max_price_decimals=self._max_price_decimals,
            source=source,
        )
        if quote is None:
            return None, "no_touch"
        beyond_limit = (
            quote.price > working.limit_price
            if working.side == "BUY"
            else quote.price < working.limit_price
        )
        if beyond_limit:
            return None, "price_band"
        return quote, None

    def _complete_ioc(
        self,
        working: _WorkingOrder,
        quote: FillQuote | None,
        *,
        event: BboEvent | TradeEvent,
        received_utc_ns: int,
        unfilled_reason: str | None,
    ) -> None:
        if self._working is not working:
            return
        filled = Decimal("0")
        before = self._position.position_quantity
        if quote is not None:
            self._position = apply_fill(self._position, quote)
            filled = quote.quantity
            self._take_touch(event, quote, received_utc_ns)
            fill_record: dict[str, object] = {
                "client_order_id": working.client_order_id,
                "side": quote.side,
                "quantity": decimal_text(quote.quantity),
                "price": decimal_text(quote.price),
                "fee_usdc": decimal_text(quote.fee_usdc),
                "source": quote.source,
                "received_utc_ns": received_utc_ns,
                "venue_fill": False,
            }
            self._fills.append(fill_record)
            self._fill_count += 1
            self._store.append({"type": "fill", "mode": "PAPER", **fill_record})
        if filled <= 0:
            status = "CANCELED"
        elif filled < working.quantity:
            status = "PARTIALLY_FILLED"
            unfilled_reason = "touch_size"
        else:
            status = "FILLED"
            unfilled_reason = None
        order_record: dict[str, object] = {
            "client_order_id": working.client_order_id,
            "side": working.side,
            "quantity": decimal_text(working.quantity),
            "filled_quantity": decimal_text(filled),
            "reduce_only": working.reduce_only,
            "limit_price": decimal_text(working.limit_price),
            "status": status,
            "unfilled_reason": unfilled_reason,
            "reason": working.reason,
            "clipped_to_risk_size": working.clipped,
            "environment": "PAPER",
            "venue": self._config.venue,
            "strategy_id": self._strategy.strategy_id,
            "configuration_version": self._strategy.configuration_version,
            "source_commit": self._config.source_commit,
            "image_digest": self._config.image_digest,
            "correlation_id": working.client_order_id,
            "venue_orders_submitted": False,
        }
        self._orders.append(order_record)
        self._order_count += 1
        self._store.append({"type": "order_completed", **order_record})
        self._working = None
        if filled > 0:
            self._update_stop_after_fill(before, received_utc_ns)
        # Only a missing price blocks a flatten; a band or a used-up quote
        # retries on the next quote without raising that flag.
        self._missing_flatten = (
            working.reduce_only
            and self._position.position_quantity != 0
            and filled <= 0
            and unfilled_reason == "no_touch"
        )

    def _take_touch(self, event: BboEvent | TradeEvent, quote: FillQuote, received_ns: int) -> None:
        if isinstance(event, TradeEvent):
            self._trade_taken += quote.quantity
            return
        price, _size = _displayed(event, quote.side)
        if price is None:
            raise PaperEngineError("a book fill must have a touch price.")
        key = (quote.side, price)
        held = self._book_taken.get(key)
        taken = quote.quantity
        if held is not None and held.until_ns > received_ns:
            taken += held.quantity
        # Keep only what is still held back, so the map stays small.
        for expired in [k for k, v in self._book_taken.items() if v.until_ns <= received_ns]:
            del self._book_taken[expired]
        self._book_taken[key] = _Taken(
            quantity=taken, until_ns=received_ns + self._config.touch_refill_ns
        )

    def _update_stop_after_fill(self, before: Decimal, received_ns: int) -> None:
        """Place the stop when a position opens or grows; clear it when flat."""

        after = self._position.position_quantity
        if after == 0:
            self._stop_price = None
            self._stop_set_trade_count = None
            self._stop_exit_pending = False
            self._exit_waiting_for_quote = False
            return
        if abs(after) <= abs(before) and _sign(after) == _sign(before):
            return
        # The same distance the risk-based size assumed, so a stop-out loses
        # about the per-trade risk budget (plus fees and gap slippage).
        distance = effective_stop_distance_fraction(
            stop_distance_fraction=self._config.stop_distance_fraction,
            volatility_multiple=self._config.volatility_multiple,
        )
        average = self._position.average_entry_price
        self._stop_price = (
            average * (Decimal(1) - distance) if after > 0 else average * (Decimal(1) + distance)
        )
        self._stop_set_trade_count = self._trade_count
        self._store.append(
            {
                "type": "stop_set",
                "mode": "PAPER",
                "stop_price": decimal_text(self._stop_price),
                "average_entry_price": decimal_text(average),
                "position_quantity": decimal_text(after),
                "stop_distance_fraction": decimal_text(distance),
                "received_utc_ns": received_ns,
            }
        )

    def _cancel_working(self, received_ns: int, why: str) -> None:
        working = self._working
        if working is None:
            return
        order_record: dict[str, object] = {
            "client_order_id": working.client_order_id,
            "side": working.side,
            "quantity": decimal_text(working.quantity),
            "filled_quantity": "0",
            "reduce_only": working.reduce_only,
            "limit_price": decimal_text(working.limit_price),
            "status": "CANCELED",
            "reason": why,
            "received_utc_ns": received_ns,
            "venue_orders_submitted": False,
        }
        self._orders.append(order_record)
        self._order_count += 1
        self._store.append({"type": "order_canceled", **order_record})
        self._working = None

    def _book_complete(self) -> bool:
        return self._bbo is not None and bbo_is_complete(self._bbo)

    def _reject(
        self,
        *,
        reason: str,
        detail: str | None,
        received_utc_ns: int | None,
        desired: _DesiredOrder | None,
    ) -> None:
        """Record a rejection when a block starts, not on every re-evaluation.

        A strategy that keeps asking for the same blocked order is re-checked
        on every event. That is one rejection, recorded when it first happens.
        A new record is written only when the order, the reason, the detail,
        or the kill switch changes, or after the block was cleared by an
        accepted order or a flat target. Nothing is held back in memory, so
        the ledger is complete as soon as each event is persisted.
        """

        key = (reason, detail, self._kill.value, desired)
        if key == self._last_block:
            return
        self._last_block = key
        self._rejection_count += 1
        record: dict[str, object] = {
            "reason": reason,
            "detail": detail,
            "received_utc_ns": received_utc_ns,
            "kill_switch": self._kill.value,
        }
        self._rejections.append(record)
        self._store.append({"type": "risk_rejected", "mode": "PAPER", **record})

    def _halt_new(self, reason: str) -> None:
        if self._kill is KillSwitch.FLATTEN_HALT:
            return
        if self._kill is KillSwitch.NONE:
            self._kill = KillSwitch.HALT_NEW
            self._kill_reason = reason
            self._store.append({"type": "kill_switch", "state": self._kill.value, "reason": reason})

    def _flatten_halt(self, reason: str) -> None:
        if self._kill is KillSwitch.FLATTEN_HALT:
            return
        self._kill = KillSwitch.FLATTEN_HALT
        self._kill_reason = reason
        self._store.append({"type": "kill_switch", "state": self._kill.value, "reason": reason})

    def _view(self) -> StrategyView:
        return StrategyView(
            position_quantity=self._position.position_quantity,
            bid_price=None if self._bbo is None else self._bbo.bid_price,
            ask_price=None if self._bbo is None else self._bbo.ask_price,
            mid_price=None if self._bbo is None else bbo_mid(self._bbo),
            last_trade_price=None if self._trade is None else self._trade.price,
            venue_mark_price=self._venue_mark,
            last_bar_close=None if self._bar is None else self._bar.close_price,
            kill_switch=self._kill.value,
        )

    def _claim(self) -> dict[str, object]:
        return {
            "schema": CLAIM_SCHEMA,
            "mode": "PAPER",
            "run_id": self._store.run_id,
            "create_only": True,
            "resume": False,
            "venue": self._config.venue,
            "instrument_id": self._config.instrument_id,
            "strategy_id": self._strategy.strategy_id,
            "strategy_configuration_version": self._strategy.configuration_version,
            "strategy_label": self._strategy.label,
            "production_eligible": self._strategy.production_eligible,
            "source_commit": self._config.source_commit,
            "image_digest": self._config.image_digest,
            "latency_ns": self._config.latency_ns,
            "entry_price_band_fraction": decimal_text(self._config.entry_price_band_fraction),
            "exit_price_band_fraction": decimal_text(self._config.exit_price_band_fraction),
            "touch_refill_ns": self._config.touch_refill_ns,
            "stop_distance_fraction": decimal_text(
                effective_stop_distance_fraction(
                    stop_distance_fraction=self._config.stop_distance_fraction,
                    volatility_multiple=self._config.volatility_multiple,
                )
            ),
            "created_at_utc": format_utc(self._created_at, field_name="created_at_utc"),
            "created_at_local": format_amsterdam(self._created_at, field_name="created_at_utc"),
            "timezone": TIMEZONE_NAME,
            "venue_orders_submitted": False,
        }

    def _persist(self, observed_at: datetime) -> None:
        self._store.commit()
        self._store.write_state(self._state())
        self._store.write_health(self._health(observed_at))

    def _state(self) -> dict[str, object]:
        marked = self._display_mark()
        unrealized = unrealized_pnl(
            position_quantity=self._position.position_quantity,
            average_entry_price=self._position.average_entry_price,
            mark_price=None if marked is None else marked[0],
        )
        return {
            "schema": STATE_SCHEMA,
            "mode": "PAPER",
            "run_id": self._store.run_id,
            "venue": self._config.venue,
            "instrument_id": self._config.instrument_id,
            "strategy_id": self._strategy.strategy_id,
            "strategy_configuration_version": self._strategy.configuration_version,
            "production_eligible": self._strategy.production_eligible,
            "source_commit": self._config.source_commit,
            "image_digest": self._config.image_digest,
            "position_quantity": decimal_text(self._position.position_quantity),
            "average_entry_price": decimal_text(self._position.average_entry_price),
            "cash_usdc": decimal_text(self._position.cash_usdc),
            "realized_pnl_usdc": decimal_text(self._position.realized_pnl_usdc),
            "unrealized_pnl_usdc": None if unrealized is None else decimal_text(unrealized),
            "fees_usdc": decimal_text(self._position.fees_usdc),
            "kill_switch": self._kill.value,
            "kill_reason": self._kill_reason,
            **self._stop_projection(),
            "orders": list(self._orders),
            "fills": list(self._fills),
            "risk_rejections": list(self._rejections),
            "recent_record_limit": STATE_RECENT_RECORD_LIMIT,
            "order_count": self._order_count,
            "fill_count": self._fill_count,
            "risk_rejection_count": self._rejection_count,
            "open_order": None if self._working is None else self._working.client_order_id,
            "venue_orders_submitted": False,
            "resume": False,
        }

    def _stop_projection(self) -> dict[str, object]:
        lockout = {1: "long", -1: "short"}.get(self._stop_lockout)
        return {
            "stop_price": None if self._stop_price is None else decimal_text(self._stop_price),
            "stop_exit_pending": self._stop_exit_pending,
            "stop_lockout": lockout,
        }

    def _health(self, observed_at: datetime) -> dict[str, object]:
        marked = self._display_mark()
        equity = self._equity()
        unrealized = unrealized_pnl(
            position_quantity=self._position.position_quantity,
            average_entry_price=self._position.average_entry_price,
            mark_price=None if marked is None else marked[0],
        )
        last_event = self._last_event_time
        status = self._kill.value if self._kill is not KillSwitch.NONE else "RUNNING"
        if self._closed and self._kill is KillSwitch.NONE:
            status = "COMPLETED"
        if self._failed:
            status = "FAILED"
        return {
            "schema": HEALTH_SCHEMA,
            "mode": "PAPER",
            "run_id": self._store.run_id,
            "status": status,
            "run_closed": self._closed,
            "kill_switch": self._kill.value,
            "kill_reason": self._kill_reason,
            "strategy_id": self._strategy.strategy_id,
            "strategy_label": self._strategy.label,
            "strategy_configuration_version": self._strategy.configuration_version,
            "production_eligible": self._strategy.production_eligible,
            "position_quantity": decimal_text(self._position.position_quantity),
            "average_entry_price": decimal_text(self._position.average_entry_price),
            "realized_pnl_usdc": decimal_text(self._position.realized_pnl_usdc),
            "unrealized_pnl_usdc": None if unrealized is None else decimal_text(unrealized),
            "fees_usdc": decimal_text(self._position.fees_usdc),
            "equity_usdc": None if equity is None else decimal_text(equity),
            "mark_price": None if marked is None else decimal_text(marked[0]),
            "mark_source": None if marked is None else marked[1],
            "stale": self._kill_reason == "stale_data",
            **self._stop_projection(),
            "flatten_blocked_missing_price": self._missing_flatten,
            "exit_waiting_for_quote": self._exit_waiting_for_quote,
            "ledger_write_failed": self._store.write_failed,
            "last_event_at_utc": (
                None
                if last_event is None
                else format_utc(last_event, field_name="last_event_at_utc")
            ),
            "last_event_at_local": (
                None
                if last_event is None
                else format_amsterdam(last_event, field_name="last_event_at_local")
            ),
            "observed_at_utc": format_utc(observed_at, field_name="observed_at_utc"),
            "observed_at_local": format_amsterdam(observed_at, field_name="observed_at_local"),
            "timezone": TIMEZONE_NAME,
            "order_count": self._order_count,
            "fill_count": self._fill_count,
            "risk_rejection_count": self._rejection_count,
            "source_commit": self._config.source_commit,
            "image_digest": self._config.image_digest,
            "venue": self._config.venue,
            "instrument_id": self._config.instrument_id,
            "venue_orders_submitted": False,
            "twenty_four_seven": False,
            "resume": False,
            "limitations": list(LIMITATIONS),
        }


def _desired_order(position: Decimal, target: Decimal) -> _DesiredOrder | None:
    delta = target - position
    if delta == 0:
        return None
    if position == 0:
        side = "BUY" if delta > 0 else "SELL"
        return _DesiredOrder(side, abs(delta), False)
    if position > 0 and delta < 0:
        return _DesiredOrder("SELL", min(abs(delta), position), True)
    if position < 0 and delta > 0:
        return _DesiredOrder("BUY", min(delta, abs(position)), True)
    side = "BUY" if delta > 0 else "SELL"
    return _DesiredOrder(side, abs(delta), False)


def _flatten_desired(position: Decimal) -> _DesiredOrder | None:
    if position > 0:
        return _DesiredOrder("SELL", position, True)
    if position < 0:
        return _DesiredOrder("BUY", abs(position), True)
    return None


def _displayed(event: BboEvent | TradeEvent, side: str) -> tuple[Decimal | None, Decimal | None]:
    """Price and displayed size a ``side`` order takes: the ask, the bid, or a print."""

    if isinstance(event, TradeEvent):
        return event.price, event.quantity
    if side == "BUY":
        return event.ask_price, event.ask_size
    return event.bid_price, event.bid_size


def _same_desire(working: _WorkingOrder, desired: _DesiredOrder) -> bool:
    return (
        working.side == desired.side
        and working.desired_quantity == desired.quantity
        and working.reduce_only == desired.reduce_only
    )


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _signed(side: str, quantity: Decimal) -> Decimal:
    if side == "BUY":
        return quantity
    return -quantity


def _require_strategy(strategy: PaperStrategy) -> None:
    strategy_id = strategy.strategy_id
    configuration_version = strategy.configuration_version
    label = strategy.label
    if type(strategy_id) is not str or not strategy_id:
        raise PaperEngineError("strategy_id must be non-empty text.")
    if type(configuration_version) is not str or not configuration_version:
        raise PaperEngineError("configuration_version must be non-empty text.")
    if type(label) is not str or not label:
        raise PaperEngineError("strategy label must be non-empty text.")
    if type(strategy.production_eligible) is not bool:
        raise PaperEngineError("production_eligible must be a bool.")
    if not callable(strategy.on_market):
        raise PaperEngineError("strategy must implement on_market.")


def _require_positive(value: Decimal, *, field_name: str) -> None:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise PaperEngineError(f"{field_name} must be a finite positive decimal.")


def _require_non_negative(value: Decimal, *, field_name: str) -> None:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise PaperEngineError(f"{field_name} must be a finite non-negative decimal.")
