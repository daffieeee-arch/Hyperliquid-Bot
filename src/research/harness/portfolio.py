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
all of it under ``long_only``, equal weight within a leg. Its weight, which
the round trip is charged on, is the gross exposure, so one period pays one
round trip on the capital, as each position pays entry and exit on its
notional. Funding is paid by the long leg and received by the short leg,
settlement by settlement, so the stress can treat each adversely.

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
opened, and its leg is spread over the names that filled. A period with a
leg short of ``min_names_per_leg`` names, at the decision or at the fill,
is skipped.

With funding declared, a traded day without any rate that a position could
hold fails the run closed when the source is built, so the outcome is a
property of the panel and the spec's grid, not of which config trades: the
panel keeps such days only outside its funding runs, and a study's range
must not hold a position across one. A held day whose rate is there but
not whole (``funding_covered`` false: at most one settlement missing, or
an interval switch the panel cannot tell apart) is charged the recorded
sum and counted, so the report shows how much of the funding rests on such
days. A halt day without any rate, which the panel builder does not check,
is charged nothing and counted the same way.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from research.harness.data import PanelTable
from research.harness.errors import HarnessError, IntegrityError
from research.harness.evaluate import Decision, TradeSeries, held_funding, next_period
from research.harness.spec import ConfigSpec, HypothesisSpec, Json, PortfolioSpec, leg_size


@dataclass(frozen=True, slots=True)
class PeriodStats:
    """What one window's periods did, beyond their returns."""

    periods: int
    skipped_decisions: int
    long_names: int
    short_names: int
    forced_exits: int
    # Held position-days whose funding was charged from a day not whole.
    uncovered_funding_days: int = 0

    def __add__(self, other: PeriodStats) -> PeriodStats:
        return PeriodStats(
            periods=self.periods + other.periods,
            skipped_decisions=self.skipped_decisions + other.skipped_decisions,
            long_names=self.long_names + other.long_names,
            short_names=self.short_names + other.short_names,
            forced_exits=self.forced_exits + other.forced_exits,
            uncovered_funding_days=self.uncovered_funding_days + other.uncovered_funding_days,
        )


EMPTY_STATS = PeriodStats(0, 0, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class _Position:
    symbol: int
    side: int
    weight: float


@dataclass(frozen=True, slots=True)
class _Held:
    value: float
    paid: float
    received: float
    forced: int
    uncovered: int


@dataclass
class PanelSource:
    """``SeriesSource`` over a panel; ``stats`` remembers each window it scored."""

    spec: HypothesisSpec
    panel: PanelTable
    stats: dict[tuple[str, int, int], PeriodStats] = field(default_factory=dict)
    # The day's eligible symbols, best signal first; the same for every config.
    _ranked: dict[int, list[tuple[float, int]]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.spec.portfolio is None:
            raise HarnessError("invariant", "PanelSource needs a portfolio spec.")
        if (self.spec.costs.funding_column is None) != (self.panel.funding is None):
            raise HarnessError("invariant", "The panel's funding does not match the spec.")
        if (self.panel.funding is None) != (self.panel.covered is None):
            raise HarnessError("invariant", "A panel with funding carries its covered flags.")
        if self.panel.funding is not None:
            self._audit_funding_reach(self.panel.funding)

    def _audit_funding_reach(self, funding: Sequence[Sequence[float | None]]) -> None:
        """A traded day without a rate fails closed wherever a position could hold it.

        A position opened on a day the symbol is in the universe holds at
        most ``latency_bars + max(horizon_bars)`` days after it; a traded
        day without a rate inside that reach of any such day fails the run,
        whichever config's legs would hold it. The same days matter to
        every config, so this is a property of the panel and the grid.
        """

        panel = self.panel
        reach = self.spec.costs.latency_bars + max(
            config.horizon_bars for config in self.spec.configs
        )
        for symbol in range(len(panel.symbols)):
            reach_until = -1
            traded = panel.traded[symbol]
            ranks = panel.ranks[symbol]
            signals = panel.signals[symbol]
            rates = funding[symbol]
            for day in range(self.length):
                if traded[day] is not True:
                    continue
                # The reach starts after the day: a position decided on it
                # holds from its fill on, never the decision day itself.
                if day <= reach_until and rates[day] is None:
                    raise IntegrityError(
                        "funding",
                        f"{panel.symbols[symbol]} has no funding on {panel.timestamps[day]}, a "
                        "traded day a position could hold; a study's range must not hold "
                        "across one.",
                    )
                rank = ranks[day]
                if (
                    rank is not None
                    and rank <= self.portfolio.universe_size
                    and signals[day] is not None
                ):
                    reach_until = max(reach_until, day + reach)

    @property
    def length(self) -> int:
        return len(self.panel.timestamps)

    @property
    def portfolio(self) -> PortfolioSpec:
        if self.spec.portfolio is None:
            raise HarnessError("invariant", "PanelSource needs a portfolio spec.")
        return self.spec.portfolio

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
        quantile = config.quantile
        gross: list[float] = []
        paid: list[float] = []
        received: list[float] = []
        stats = EMPTY_STATS
        decision = start
        while (period := next_period(decision, latency, config.horizon_bars, end)) is not None:
            entry, exit_index = period
            positions = self._positions(quantile, decision, entry)
            if positions is None:
                stats += PeriodStats(0, 1, 0, 0, 0)
                decision += 1
                continue
            period_gross = 0.0
            period_paid = 0.0
            period_received = 0.0
            forced = 0
            uncovered = 0
            for position in positions:
                held = self._hold(position, entry, exit_index)
                period_gross += held.value
                period_paid += held.paid
                period_received += held.received
                forced += held.forced
                uncovered += held.uncovered
            gross.append(period_gross)
            paid.append(period_paid)
            received.append(period_received)
            stats += PeriodStats(
                periods=1,
                skipped_decisions=0,
                long_names=sum(1 for position in positions if position.side > 0),
                short_names=sum(1 for position in positions if position.side < 0),
                forced_exits=forced,
                uncovered_funding_days=uncovered,
            )
            decision = exit_index
        self.stats[(config.id, start, end)] = stats
        return TradeSeries(
            gross=tuple(gross),
            funding_paid=tuple(paid),
            funding_received=tuple(received),
            # Each period deploys the capital: both legs together, or the long
            # leg alone, so one period pays one round trip on it.
            weights=(1.0,) * len(gross),
        )

    def _positions(self, quantile: float, decision: int, entry: int) -> list[_Position] | None:
        """The period's positions, or None when it is skipped."""

        portfolio = self.portfolio
        eligible = self._eligible(decision)
        names = leg_size(len(eligible), quantile)
        if names < portfolio.min_names_per_leg:
            return None
        long_leg = [symbol for _signal, symbol in eligible[:names]]
        short_leg = (
            [symbol for _signal, symbol in eligible[len(eligible) - names :]]
            if self.spec.direction == "signed"
            else []
        )
        # A symbol that does not trade on the fill day is not opened; a leg
        # that fills below the floor skips the period, as at the decision.
        floor = portfolio.min_names_per_leg
        long_filled = [symbol for symbol in long_leg if self._fills(symbol, decision, entry)]
        short_filled = [symbol for symbol in short_leg if self._fills(symbol, decision, entry)]
        if len(long_filled) < floor or (short_leg and len(short_filled) < floor):
            return None
        capital = 0.5 if short_leg else 1.0
        positions = [_Position(symbol, 1, capital / len(long_filled)) for symbol in long_filled]
        positions.extend(
            _Position(symbol, -1, capital / len(short_filled)) for symbol in short_filled
        )
        return positions

    def _fills(self, symbol: int, decision: int, entry: int) -> bool:
        """Whether the symbol trades at the fill and had a row on every day since the decision.

        A day without a row ends the contract, so rows after one are another
        listing, which the decision day's signal says nothing about.
        """

        panel = self.panel
        prices = panel.prices[symbol]
        if any(prices[day] is None for day in range(decision + 1, entry + 1)):
            return False
        return panel.traded[symbol][entry] is True

    def _eligible(self, decision: int) -> list[tuple[float, int]]:
        """The decision day's universe ranked by signal, cached across configs."""

        cached = self._ranked.get(decision)
        if cached is not None:
            return cached
        panel = self.panel
        eligible: list[tuple[float, int]] = []
        for symbol in range(len(panel.symbols)):
            rank = panel.ranks[symbol][decision]
            signal = panel.signals[symbol][decision]
            if (
                panel.traded[symbol][decision] is not True
                or rank is None
                or rank > self.portfolio.universe_size
                or signal is None
            ):
                continue
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
        does not check, pays nothing and counts as uncovered; a traded day
        without one was refused when the source was built.
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
        last = entry
        for day in range(entry + 1, exit_index + 1):
            price = prices[day]
            if price is None:
                break
            if panel.traded[symbol][day] is True:
                last = day
            if panel.funding is None or panel.covered is None:
                continue
            rate = panel.funding[symbol][day]
            if rate is None and panel.traded[symbol][day] is True:
                raise HarnessError("invariant", "A traded held day without a rate was not refused.")
            if rate is None or panel.covered[symbol][day] is not True:
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
            forced=0 if last == exit_index else 1,
            uncovered=uncovered,
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
    periods = stats.periods
    return {
        "periods": periods,
        "skipped_decisions": stats.skipped_decisions,
        "mean_long_names": None if periods == 0 else stats.long_names / periods,
        "mean_short_names": None if periods == 0 else stats.short_names / periods,
        "forced_exits": stats.forced_exits,
        "uncovered_funding_days": stats.uncovered_funding_days,
    }
