"""A cross-sectional portfolio over a panel, scored as one trade per period.

Each period is decided on one day of the date axis: the universe is the
symbols that traded that day with a rank at most ``universe_size`` and a
known signal; funding plays no part in it. Sorted by the signal, the top ``quantile`` of them is the
long leg and, under ``direction: signed``, the bottom ``quantile`` the
short leg. The legs fill ``latency_bars`` days later at that day's close and
exit ``horizon_bars`` days after the fill; the next decision is the exit
day, so periods never overlap, like the bar series' trades.

A period's gross return is the capital-weighted sum of its positions'
returns: each leg holds half the capital under ``signed`` and the long leg
all of it under ``long_only``, equal weight within a leg, sized at the
decision. Its weight, which the round trip is charged on, is the capital
deployed: the weights of the names that filled, so one period pays one
round trip on what it holds, as each position pays entry and exit on its
notional, and the capital of a name that did not fill sits idle. Funding
is paid by the long leg and received by the short leg, settlement by
settlement, so the stress can treat each adversely.

A position is held to the period's exit day whatever happens in between,
so no exit uses knowledge of a later day. It exits at that day's close when
the symbol trades then; otherwise at its last traded close at or before the
exit day, the one price a holder of a halted or delisted contract has, and
the period records a forced exit. A day without a row ends the contract:
the panel keeps such gaps only between the runs of a relisted symbol, and
the rows after the gap are another listing, so the hold stops at the last
traded close before it, and a symbol whose rows break between the decision
and the fill is not opened. A day with a row that did not trade is a halt,
held through. Funding is charged on every held day that has a row, through
the exit day, whether the symbol traded that day or not: a halt pays its
days, and the flat archive days a delisted contract keeps pay their
recorded rate until the exit, which overstates a long's cost and a short's
income by at most the horizon's worth of that rate; ``forced_exits`` counts
such positions. A symbol that does not trade on the fill day is not
opened; its capital sits idle, since the other orders were sized before
its non-fill was known. A period with a leg short of ``min_names_per_leg``
names at the decision sends no orders and is skipped; one with a leg short
of them at the fill unwinds the names that did fill at the fill close,
paying the round trip on them for no return: a trade of the series, a
period, counted apart as unwound. After a skip at the decision the next
decision is the next day; after an unwind it is the fill day, when the
non-fill is known, so no decision is placed with a later day's knowledge.

With funding declared, a traded day without any rate that a position could
hold fails the run closed before any window is scored (the first window
scored audits every window of the split, and any other window is audited
before it is scored), so the outcome is a property of the panel, the
spec's grid and the split, not of which config trades or is selected:
the panel keeps such days only outside its funding runs, and a study's
range must not hold a position across one. A held day whose rate is there but
not whole (``funding_covered`` false: at most one settlement missing, or
an interval switch the panel cannot tell apart) is charged the recorded
sum and counted as uncovered, so the report shows how much of the funding
rests on such days. A halt day without any rate, which the panel builder
does not check, is charged nothing and counted as unfunded.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from fractions import Fraction

from research.harness.data import PanelTable
from research.harness.errors import HarnessError, IntegrityError
from research.harness.evaluate import Decision, TradeSeries, held_funding, next_period
from research.harness.spec import (
    ConfigSpec,
    HypothesisSpec,
    Json,
    PortfolioSpec,
    exact_quantile,
    leg_size,
)
from research.harness.splits import walk_forward


@dataclass(frozen=True, slots=True)
class PeriodStats:
    """What one window's periods did, beyond their returns."""

    periods: int = 0
    skipped_decisions: int = 0
    long_names: int = 0
    short_names: int = 0
    forced_exits: int = 0
    # Held position-days whose funding was charged from a day not whole.
    uncovered_funding_days: int = 0
    # Held position-days of a halt without any rate, charged nothing.
    unfunded_halt_days: int = 0
    # Of the periods, those whose fills were unwound at the fill close
    # because a leg fell short: a trade of the round trip and no return.
    unwound_periods: int = 0

    def __add__(self, other: PeriodStats) -> PeriodStats:
        # Every counter sums, so a new one cannot be left out here.
        return PeriodStats(
            **{name: getattr(self, name) + getattr(other, name) for name in _STAT_NAMES}
        )


_STAT_NAMES = tuple(item.name for item in fields(PeriodStats))
EMPTY_STATS = PeriodStats()


@dataclass(frozen=True, slots=True)
class _Position:
    symbol: int
    side: int
    weight: float


@dataclass(frozen=True, slots=True)
class _Skipped:
    """A period not taken at the decision: no orders were sent."""


@dataclass(frozen=True, slots=True)
class _Opened:
    """A period's filled positions, their stats, and whether a leg fell short.

    ``deployed`` is the capital the fills hold. With ``unwound`` the fills
    are closed at the fill close: the period pays the round trip on them
    for no return.
    """

    positions: list[_Position]
    stats: PeriodStats
    deployed: float
    unwound: bool


@dataclass(frozen=True, slots=True)
class _Held:
    value: float
    paid: float
    received: float
    stats: PeriodStats


@dataclass
class PanelSource:
    """``SeriesSource`` over a panel; ``stats`` remembers each window it scored."""

    spec: HypothesisSpec
    panel: PanelTable
    stats: dict[tuple[str, int, int], PeriodStats] = field(default_factory=dict)
    # The day's eligible symbols, best signal first; the same for every config.
    _ranked: dict[int, list[tuple[float, int]]] = field(default_factory=dict, init=False)
    # The windows whose held days were audited for funding.
    _audited: set[tuple[int, int]] = field(default_factory=set, init=False)
    # Per symbol, its days without a row, in order.
    _gaps: dict[int, list[int]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.spec.portfolio is None:
            raise HarnessError("invariant", "PanelSource needs a portfolio spec.")
        if (self.spec.costs.funding_column is None) != (self.panel.funding is None):
            raise HarnessError("invariant", "The panel's funding does not match the spec.")
        if (self.panel.funding is None) != (self.panel.covered is None):
            raise HarnessError("invariant", "A panel with funding carries its covered flags.")
        if self.panel.funding is not None:
            self._audit_split(self.panel.funding)

    def _audit_window(
        self, funding: Sequence[Sequence[float | None]], start: int, end: int
    ) -> None:
        """A traded day without a rate fails closed wherever a window's position could hold it.

        A position decided on a day of ``[start, end)`` the symbol is in the
        universe and fills from is charged, as ``_hold`` charges, the days
        after its fill through its exit (``_held_through``): ``decision +
        latency_bars + 1`` through ``decision + latency_bars +
        horizon_bars`` for the grid's longest horizon whose exit stays
        inside the window, and never across a day without a row. A traded
        day without a rate among those days of any such decision fails the
        run, whichever config's legs would hold it. The same days matter to
        every config scored on the window, so this is a property of the
        panel, the grid and the window.
        """

        panel = self.panel
        universe_size = self.portfolio.universe_size
        latency = self.spec.costs.latency_bars
        horizons = sorted({config.horizon_bars for config in self.spec.configs}, reverse=True)
        for symbol in range(len(panel.symbols)):
            # The charged spans of the universe days so far, in day order.
            spans: deque[tuple[int, int]] = deque()
            traded = panel.traded[symbol]
            rates = funding[symbol]
            for day in range(start, end):
                while spans and spans[0][1] < day:
                    spans.popleft()
                if traded[day] is not True:
                    continue
                if spans and spans[0][0] <= day and rates[day] is None:
                    raise IntegrityError(
                        "funding",
                        f"{panel.symbols[symbol]} has no funding on {panel.timestamps[day]}, a "
                        "traded day a position could hold; a study's range must not hold "
                        "across one.",
                    )
                if not self._in_universe(symbol, day, universe_size):
                    continue
                longest = next((h for h in horizons if day + latency + h < end), None)
                # A symbol that cannot fill from this day never holds from it.
                if longest is None or not self._fills(symbol, day, day + latency):
                    continue
                entry = day + latency
                through = self._held_through(symbol, entry, entry + longest)
                if through > entry:
                    spans.append((entry + 1, through))

    @property
    def length(self) -> int:
        return len(self.panel.timestamps)

    @property
    def portfolio(self) -> PortfolioSpec:
        portfolio = self.spec.portfolio
        if portfolio is None:
            raise HarnessError("invariant", "PanelSource needs a portfolio spec.")
        return portfolio

    def _audit_split(self, funding: Sequence[Sequence[float | None]]) -> None:
        """Audit the funding of every window the split will score, before any is.

        The folds' test windows and the holdout are the windows a run
        scores; auditing them all when the source is built, rather than
        each as it is scored, makes a hole in the holdout fail the run
        whether or not validation scores a config on it.
        """

        folds, holdout = walk_forward(self.length, self.spec.split)
        for start, end in (*((fold.test_start, fold.test_end) for fold in folds), holdout):
            self._audit_once(funding, start, end)

    def _audit_once(self, funding: Sequence[Sequence[float | None]], start: int, end: int) -> None:
        if (start, end) not in self._audited:
            self._audit_window(funding, start, end)
            self._audited.add((start, end))

    def window(self, config: ConfigSpec, start: int, end: int) -> TradeSeries:
        """Non-overlapping periods decided inside ``[start, end)``.

        The fill and the exit stay strictly inside the window, so a
        validation period never reads a holdout close.
        """

        if start < 0 or end < start or end > self.length:
            raise HarnessError("split", "Period window is not a valid index range.")
        if config.quantile is None:
            raise HarnessError("invariant", f"Config {config.id} has no quantile.")
        latency = self.spec.costs.latency_bars
        if self.panel.funding is not None:
            self._audit_once(self.panel.funding, start, end)
        quantile = exact_quantile(config.quantile)
        gross: list[float] = []
        paid: list[float] = []
        received: list[float] = []
        weights: list[float] = []
        stats = EMPTY_STATS
        decision = start
        while (period := next_period(decision, latency, config.horizon_bars, end)) is not None:
            entry, exit_index = period
            opened = self._positions(quantile, decision, entry)
            if isinstance(opened, _Skipped):
                stats += PeriodStats(skipped_decisions=1)
                decision += 1
                continue
            if opened.unwound:
                # The fills are closed at the fill close: no return, no
                # funding, the round trip on the capital they held, one trade
                # of the series like a held period. The non-fill is known on
                # the fill day, so the next decision is there; the decision
                # always advances, at zero latency too.
                if opened.positions:
                    gross.append(0.0)
                    paid.append(0.0)
                    received.append(0.0)
                    weights.append(opened.deployed)
                stats += opened.stats
                decision = max(entry, decision + 1)
                continue
            period_stats = opened.stats
            period_gross = 0.0
            period_paid = 0.0
            period_received = 0.0
            for position in opened.positions:
                held = self._hold(position, entry, exit_index)
                period_gross += held.value
                period_paid += held.paid
                period_received += held.received
                period_stats += held.stats
            gross.append(period_gross)
            paid.append(period_paid)
            received.append(period_received)
            # The round trip is charged on the capital the fills hold.
            weights.append(opened.deployed)
            stats += period_stats
            decision = exit_index
        self.stats[(config.id, start, end)] = stats
        return TradeSeries(
            gross=tuple(gross),
            funding_paid=tuple(paid),
            funding_received=tuple(received),
            weights=tuple(weights),
        )

    def _positions(self, quantile: Fraction, decision: int, entry: int) -> _Opened | _Skipped:
        """The period's positions, or why it is skipped."""

        portfolio = self.portfolio
        eligible = self._eligible(decision)
        names = leg_size(len(eligible), quantile)
        if names < portfolio.min_names_per_leg:
            return _Skipped()
        if self.spec.direction == "signed" and 2 * names > len(eligible):
            # The spec caps a signed quantile at one half; a wider one would
            # put a name in both legs.
            raise HarnessError("invariant", "The long and short legs would overlap.")
        long_leg = [symbol for _signal, symbol in eligible[:names]]
        short_leg = (
            [symbol for _signal, symbol in eligible[len(eligible) - names :]]
            if self.spec.direction == "signed"
            else []
        )
        # Each order is sized at the decision: a leg's capital over its names.
        # A symbol that does not trade on the fill day is not opened and its
        # capital sits idle; a leg that fills below the floor unwinds the
        # period's fills.
        weight = (0.5 if short_leg else 1.0) / names
        long_filled = [symbol for symbol in long_leg if self._fills(symbol, decision, entry)]
        short_filled = [symbol for symbol in short_leg if self._fills(symbol, decision, entry)]
        positions = [_Position(symbol, 1, weight) for symbol in long_filled]
        positions.extend(_Position(symbol, -1, weight) for symbol in short_filled)
        floor = portfolio.min_names_per_leg
        unwound = len(long_filled) < floor or (bool(short_leg) and len(short_filled) < floor)
        if not unwound:
            stats = PeriodStats(
                periods=1, long_names=len(long_filled), short_names=len(short_filled)
            )
        elif positions:
            # A trade of the series that held nothing.
            stats = PeriodStats(periods=1, unwound_periods=1)
        else:
            # Nothing filled, so nothing was traded: a skipped decision.
            stats = PeriodStats(skipped_decisions=1)
        return _Opened(positions, stats, weight * len(positions), unwound)

    def _fills(self, symbol: int, decision: int, entry: int) -> bool:
        """Whether the symbol trades at the fill and had a row on every day since the decision.

        A day without a row ends the contract, so rows after one are another
        listing, which the decision day's signal says nothing about.
        """

        if self._gap_from(symbol, decision + 1) <= entry:
            return False
        return self.panel.traded[symbol][entry] is True

    def _held_through(self, symbol: int, entry: int, exit_index: int) -> int:
        """The last day a position filled at ``entry`` holds: ``exit_index``, or the
        day before the first without a row, where the contract ends. The hold
        and the funding audit share this one rule."""

        return max(entry, min(exit_index, self._gap_from(symbol, entry + 1) - 1))

    def _gap_from(self, symbol: int, day: int) -> int:
        """The first day at or after ``day`` without a row for the symbol, or the length."""

        gaps = self._gaps.get(symbol)
        if gaps is None:
            # The symbol's days without a row, once; a few per symbol at most.
            prices = self.panel.prices[symbol]
            gaps = [index for index, price in enumerate(prices) if price is None]
            self._gaps[symbol] = gaps
        position = bisect_left(gaps, day)
        return gaps[position] if position < len(gaps) else self.length

    def _in_universe(self, symbol: int, day: int, universe_size: int) -> bool:
        """Whether the symbol can be decided on that day: traded, ranked within the
        universe, signal known. The ranking and the funding audit share this one test."""

        panel = self.panel
        rank = panel.ranks[symbol][day]
        return (
            panel.traded[symbol][day] is True
            and rank is not None
            and rank <= universe_size
            and panel.signals[symbol][day] is not None
        )

    def _eligible(self, decision: int) -> list[tuple[float, int]]:
        """The decision day's universe ranked by signal, cached across configs."""

        cached = self._ranked.get(decision)
        if cached is not None:
            return cached
        panel = self.panel
        universe_size = self.portfolio.universe_size
        eligible: list[tuple[float, int]] = []
        for symbol in range(len(panel.symbols)):
            if not self._in_universe(symbol, decision, universe_size):
                continue
            signal = panel.signals[symbol][decision]
            if signal is None:
                raise HarnessError("invariant", "A universe member has a signal.")
            eligible.append((signal, symbol))
        # One ranking, ties by symbol, so the legs are deterministic and
        # disjoint: the long leg is its top and the short leg its bottom.
        eligible.sort(key=lambda item: (-item[0], panel.symbols[item[1]]))
        self._ranked[decision] = eligible
        return eligible

    def _hold(self, position: _Position, entry: int, exit_index: int) -> _Held:
        """One position's weighted return and funding over its hold.

        The position holds days ``entry + 1`` through ``exit_index``, or
        through the day before the first without a row, where the contract
        ends. It exits at its last traded close in that hold. Funding is
        paid on every day of the hold, traded or not, on the notional at
        the day's close. A halt day without a rate, which the panel builder
        does not check, pays nothing and counts as unfunded; a traded day
        without one fails closed here too: the audit refused such a day for
        every config of the grid, so this guards a config handed to
        ``window`` from outside the grid.
        """

        panel = self.panel
        symbol = position.symbol
        prices = panel.prices[symbol]
        entry_price = prices[entry]
        if entry_price is None:
            raise HarnessError("invariant", "A filled position has no entry price.")
        held_prices: list[float] = []
        held_rates: list[float] = []
        uncovered = 0
        unfunded = 0
        last = entry
        for day in range(entry + 1, self._held_through(symbol, entry, exit_index) + 1):
            price = prices[day]
            if price is None:
                raise HarnessError("invariant", "A held day has a row.")
            if panel.traded[symbol][day] is True:
                last = day
            if panel.funding is None or panel.covered is None:
                continue
            rate = panel.funding[symbol][day]
            if rate is None and panel.traded[symbol][day] is True:
                raise IntegrityError(
                    "funding",
                    f"{panel.symbols[symbol]} has no funding on a held day at "
                    f"{panel.timestamps[day]}; a study's range must not hold across one.",
                )
            if rate is None:
                unfunded += 1
            elif panel.covered[symbol][day] is not True:
                uncovered += 1
            held_prices.append(price)
            held_rates.append(0.0 if rate is None else rate)
        paid, received = held_funding(position.side, entry_price, held_prices, held_rates)
        exit_price = prices[last]
        if exit_price is None:
            raise HarnessError("invariant", "A held position has no exit price.")
        value = position.weight * position.side * (exit_price - entry_price) / entry_price
        return _Held(
            value=value,
            paid=position.weight * paid,
            received=position.weight * received,
            stats=PeriodStats(
                forced_exits=0 if last == exit_index else 1,
                uncovered_funding_days=uncovered,
                unfunded_halt_days=unfunded,
            ),
        )

    def window_stats(self, config_id: str, windows: Sequence[tuple[int, int]]) -> PeriodStats:
        """The summed stats of the given windows, each scored before."""

        total = EMPTY_STATS
        for start, end in windows:
            scored = self.stats.get((config_id, start, end))
            if scored is None:
                raise HarnessError(
                    "invariant", f"Window [{start}, {end}) of {config_id} was not scored."
                )
            total += scored
        return total


def portfolio_block(source: PanelSource, decision: Decision) -> dict[str, Json]:
    """The report's panel block: the universe rule and each config's period stats.

    Validation sums the walk-forward test folds. The holdout is present only
    for the config that was scored on it.
    """

    portfolio = source.portfolio
    validation = [(fold.test_start, fold.test_end) for fold in decision.folds]
    configs: list[Json] = []
    for config in source.spec.configs:
        entry: dict[str, Json] = {
            "id": config.id,
            "quantile": config.quantile,
            "horizon_bars": config.horizon_bars,
            "validation": _stats_json(source.window_stats(config.id, validation)),
        }
        if config.id == decision.holdout_config_id:
            holdout = (decision.holdout_start, decision.holdout_end)
            entry["holdout"] = _stats_json(source.window_stats(config.id, [holdout]))
        configs.append(entry)
    return {
        "method": "quantile_long_short",
        "signed": source.spec.direction == "signed",
        "universe_size": portfolio.universe_size,
        "min_names_per_leg": portfolio.min_names_per_leg,
        "symbol_count": len(source.panel.symbols),
        "delisting": "exit_at_last_traded_close",
        "configs": configs,
    }


def _stats_json(stats: PeriodStats) -> dict[str, Json]:
    # The names per leg are a mean over the periods that held, so the
    # unwound ones, which held nothing, do not dilute it.
    held = stats.periods - stats.unwound_periods
    return {
        "periods": stats.periods,
        "skipped_decisions": stats.skipped_decisions,
        "mean_long_names": None if held == 0 else stats.long_names / held,
        "mean_short_names": None if held == 0 else stats.short_names / held,
        "forced_exits": stats.forced_exits,
        "uncovered_funding_days": stats.uncovered_funding_days,
        "unfunded_halt_days": stats.unfunded_halt_days,
        "unwound_periods": stats.unwound_periods,
    }
