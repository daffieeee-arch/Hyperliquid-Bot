"""Buy-and-hold context next to the scored family. It never changes the label."""

from __future__ import annotations

import math
from dataclasses import dataclass

from research.harness.costs import STRESS_MULTIPLIERS, stress_key
from research.harness.data import BarTable
from research.harness.errors import HarnessError
from research.harness.evaluate import Decision, Trade, _sample_stdev, trade_series
from research.harness.spec import UNIT_SIZING, CostSpec


@dataclass(frozen=True, slots=True)
class BuyAndHold:
    """One unit long, decided at the window's first close and held to its last.

    It is priced like a strategy trade at unit weight: the fill waits
    ``latency_bars``, it pays one round trip, and ``funding`` is the cashflow
    of a fixed quantity on the notional at each held bar's close.
    ``funding_constant_notional`` is the same bars' cashflow at a constant
    notional, the negated sum of their rates. Both funding fields are positive
    when funding is received, like the strategy's funding block; ``net`` uses
    ``funding``. The per-bar figures use log returns between consecutive
    closes from the fill to the exit and are not annualized.
    """

    start: int
    end: int
    bars_held: int
    gross_return: float
    log_return: float
    funding: float | None
    funding_constant_notional: float | None
    net: dict[str, float]
    mean_log_return_per_bar: float
    stdev_log_return_per_bar: float | None
    sharpe_per_bar: float | None


@dataclass(frozen=True, slots=True)
class Benchmark:
    # None without a validation fold, or when the window is too short to hold.
    validation: BuyAndHold | None
    # None while the holdout is sealed (it opens only for a selected config),
    # or when the opened holdout is too short to hold.
    holdout: BuyAndHold | None


def benchmark(costs: CostSpec, table: BarTable, decision: Decision) -> Benchmark:
    """Buy-and-hold over the validation test folds and, once opened, the holdout.

    The holdout window is read only when validation selected a config, the
    same rule the strategy follows, so a sealed holdout stays unread.
    """

    validation = None
    if decision.folds:
        validation = buy_and_hold(
            costs, table, decision.folds[0].test_start, decision.folds[-1].test_end
        )
    holdout = None
    if decision.holdout_config_id is not None:
        holdout = buy_and_hold(costs, table, decision.holdout_start, decision.holdout_end)
    return Benchmark(validation=validation, holdout=holdout)


def buy_and_hold(costs: CostSpec, table: BarTable, start: int, end: int) -> BuyAndHold | None:
    """Rows ``[start, end)``, filled at ``start + latency_bars``.

    None when the fill leaves no later close in the window to exit at.
    """

    prices = table.prices
    if not 0 <= start < end <= len(prices):
        raise HarnessError("invariant", f"Benchmark window [{start}, {end}) is outside the table.")
    entry = start + costs.latency_bars
    if end - entry < 2:
        return None
    trade = Trade(decision=start, entry=entry, exit=end - 1, side=1)
    series = trade_series((trade,), prices, funding=table.funding, sizing=UNIT_SIZING, vol=None)
    log_returns = [math.log(prices[index] / prices[index - 1]) for index in range(entry + 1, end)]
    mean = math.fsum(log_returns) / len(log_returns)
    stdev = _sample_stdev(log_returns, mean) if len(log_returns) >= 2 else None
    constant_notional = (
        None if table.funding is None else -math.fsum(table.funding[entry + 1 : end])
    )
    return BuyAndHold(
        start=start,
        end=end,
        bars_held=end - 1 - entry,
        gross_return=series.gross[0],
        log_return=math.log(prices[end - 1] / prices[entry]),
        funding=None if table.funding is None else series.funding[0],
        funding_constant_notional=constant_notional,
        net={stress_key(stress): series.net(costs, stress)[0] for stress in STRESS_MULTIPLIERS},
        mean_log_return_per_bar=mean,
        stdev_log_return_per_bar=stdev,
        sharpe_per_bar=None if stdev is None or stdev == 0.0 else mean / stdev,
    )
