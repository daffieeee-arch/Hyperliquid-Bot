"""A cross-sectional portfolio over a panel, scored as one trade per period.

Each period is decided on one day of the date axis: the universe is the
symbols that traded that day with a rank at most ``universe_size`` and a
known signal. Sorted by the signal, the top ``quantile`` of them is the
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

A position whose symbol does not trade on a held day is closed at its last
traded close, as a holder of a delisted contract is; the period records a
forced exit. A symbol that does not trade on the fill day is not opened, and
its leg is spread over the names that filled. A period with a leg short of
``min_names_per_leg`` names at the decision, or empty at the fill, is
skipped.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from research.harness.data import PanelTable
from research.harness.errors import HarnessError, IntegrityError
from research.harness.evaluate import Decision, TradeSeries
from research.harness.spec import ConfigSpec, HypothesisSpec, Json


@dataclass(frozen=True, slots=True)
class PeriodStats:
    """What one window's periods did, beyond their returns."""

    periods: int
    skipped_decisions: int
    long_names: int
    short_names: int
    forced_exits: int

    def __add__(self, other: PeriodStats) -> PeriodStats:
        return PeriodStats(
            periods=self.periods + other.periods,
            skipped_decisions=self.skipped_decisions + other.skipped_decisions,
            long_names=self.long_names + other.long_names,
            short_names=self.short_names + other.short_names,
            forced_exits=self.forced_exits + other.forced_exits,
        )


EMPTY_STATS = PeriodStats(0, 0, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class _Position:
    symbol: int
    side: int
    weight: float


@dataclass
class PanelSource:
    """``SeriesSource`` over a panel; ``stats`` remembers each window it scored."""

    spec: HypothesisSpec
    panel: PanelTable
    stats: dict[tuple[str, int, int], PeriodStats] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.spec.portfolio is None:
            raise HarnessError("invariant", "PanelSource needs a portfolio spec.")
        if (self.spec.costs.funding_column is None) != (self.panel.funding is None):
            raise HarnessError("invariant", "The panel's funding does not match the spec.")

    @property
    def length(self) -> int:
        return len(self.panel.timestamps)

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
        gross: list[float] = []
        paid: list[float] = []
        received: list[float] = []
        weights: list[float] = []
        stats = EMPTY_STATS
        decision = start
        while True:
            entry = decision + latency
            exit_index = entry + config.horizon_bars
            if decision >= end or exit_index >= end:
                break
            positions = self._positions(config.quantile, decision, entry)
            if positions is None:
                stats += PeriodStats(0, 1, 0, 0, 0)
                decision += 1
                continue
            period_gross = 0.0
            period_paid = 0.0
            period_received = 0.0
            forced = 0
            for position in positions:
                value, flows, early = self._hold(position, entry, exit_index)
                period_gross += value
                period_paid += flows[0]
                period_received += flows[1]
                forced += early
            gross.append(period_gross)
            paid.append(period_paid)
            received.append(period_received)
            weights.append(math.fsum(position.weight for position in positions))
            stats += PeriodStats(
                periods=1,
                skipped_decisions=0,
                long_names=sum(1 for position in positions if position.side > 0),
                short_names=sum(1 for position in positions if position.side < 0),
                forced_exits=forced,
            )
            decision = exit_index
        self.stats[(config.id, start, end)] = stats
        return TradeSeries(
            gross=tuple(gross),
            funding_paid=tuple(paid),
            funding_received=tuple(received),
            weights=tuple(weights),
        )

    def _positions(self, quantile: float, decision: int, entry: int) -> list[_Position] | None:
        """The period's positions, or None when it is skipped."""

        portfolio = self.spec.portfolio
        if portfolio is None:
            raise HarnessError("invariant", "PanelSource needs a portfolio spec.")
        panel = self.panel
        eligible: list[tuple[float, int]] = []
        for symbol in range(len(panel.symbols)):
            rank = panel.ranks[symbol][decision]
            signal = panel.signals[symbol][decision]
            if (
                panel.traded[symbol][decision] is not True
                or rank is None
                or rank > portfolio.universe_size
                or signal is None
                or (panel.funding is not None and panel.funding[symbol][decision] is None)
            ):
                continue
            eligible.append((signal, symbol))
        # Ties go to the symbol that sorts first, so the legs are deterministic.
        eligible.sort(key=lambda item: (-item[0], panel.symbols[item[1]]))
        names = int(len(eligible) * quantile)
        if names < portfolio.min_names_per_leg:
            return None
        long_leg = [symbol for _signal, symbol in eligible[:names]]
        short_leg = (
            [symbol for _signal, symbol in eligible[-names:]]
            if self.spec.direction == "signed"
            else []
        )
        # A symbol that does not trade on the fill day is not opened.
        long_filled = [symbol for symbol in long_leg if panel.traded[symbol][entry] is True]
        short_filled = [symbol for symbol in short_leg if panel.traded[symbol][entry] is True]
        if not long_filled or (short_leg and not short_filled):
            return None
        capital = 0.5 if short_leg else 1.0
        positions = [_Position(symbol, 1, capital / len(long_filled)) for symbol in long_filled]
        positions.extend(
            _Position(symbol, -1, capital / len(short_filled)) for symbol in short_filled
        )
        return positions

    def _hold(
        self, position: _Position, entry: int, exit_index: int
    ) -> tuple[float, tuple[float, float], int]:
        """One position's weighted return, funding (paid, received) and forced-exit flag.

        The position holds days ``entry + 1`` through ``exit_index``, paying
        each day's funding on the notional at that day's close. A day the
        symbol does not trade closes it at the previous close.
        """

        panel = self.panel
        prices = panel.prices[position.symbol]
        entry_price = prices[entry]
        if entry_price is None:
            raise HarnessError("invariant", "A filled position has no entry price.")
        paid = 0.0
        received = 0.0
        last = entry
        forced = 0
        for day in range(entry + 1, exit_index + 1):
            price = prices[day]
            if price is None or panel.traded[position.symbol][day] is not True:
                forced = 1
                break
            last = day
            if panel.funding is not None:
                rate = panel.funding[position.symbol][day]
                if rate is None:
                    raise IntegrityError(
                        "funding",
                        f"{panel.symbols[position.symbol]} has no funding on a held day at "
                        f"{panel.timestamps[day]}; the panel must not leave a held day empty.",
                    )
                flow = -position.side * rate * price / entry_price
                if flow < 0.0:
                    paid -= flow
                else:
                    received += flow
        exit_price = prices[last]
        if exit_price is None:
            raise HarnessError("invariant", "A held position has no exit price.")
        value = position.weight * position.side * (exit_price - entry_price) / entry_price
        return value, (position.weight * paid, position.weight * received), forced

    def window_stats(self, config_id: str, windows: Sequence[tuple[int, int]]) -> PeriodStats:
        """The summed stats of the given windows, each scored before."""

        total = EMPTY_STATS
        for start, end in windows:
            total += self.stats[(config_id, start, end)]
        return total


def portfolio_block(source: PanelSource, decision: Decision) -> dict[str, Json]:
    """The report's panel block: the universe rule and each config's period stats.

    Validation sums the walk-forward test folds. The holdout is present only
    for the config that was scored on it.
    """

    portfolio = source.spec.portfolio
    if portfolio is None:
        raise HarnessError("invariant", "portfolio_block needs a portfolio spec.")
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
    }
