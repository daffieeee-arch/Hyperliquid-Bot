"""Buy-and-hold context next to the scored family. It never changes the label."""

from __future__ import annotations

import math
from dataclasses import dataclass

from research.harness.costs import STRESS_MULTIPLIERS, stress_key
from research.harness.data import BarTable
from research.harness.errors import HarnessError
from research.harness.evaluate import Decision, Trade, trade_series
from research.harness.spec import UNIT_SIZING, CostSpec


@dataclass(frozen=True, slots=True)
class BuyAndHold:
    """One unit long from the window's first close to its last close.

    It is priced like a strategy trade at unit weight: one round trip, and
    the funding a fixed quantity pays on the notional at each held bar's
    close. ``funding_rate_sum`` is what a long of constant notional pays.
    The per-bar figures use log returns between consecutive closes in the
    window and are not annualized.
    """

    start: int
    end: int
    bars_held: int
    gross_return: float
    log_return: float
    funding: float | None
    funding_rate_sum: float | None
    net: dict[str, float]
    mean_log_return_per_bar: float
    stdev_log_return_per_bar: float | None
    sharpe_per_bar: float | None


@dataclass(frozen=True, slots=True)
class Benchmark:
    # None when there is no validation fold.
    validation: BuyAndHold | None
    # None while the holdout is sealed: it opens only for a selected config.
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
    """Rows ``[start, end)``; None when the window holds fewer than two closes."""

    prices = table.prices
    if not 0 <= start < end <= len(prices):
        raise HarnessError("invariant", f"Benchmark window [{start}, {end}) is outside the table.")
    if end - start < 2:
        return None
    trade = Trade(decision=start, entry=start, exit=end - 1, side=1)
    series = trade_series((trade,), prices, funding=table.funding, sizing=UNIT_SIZING, vol=None)
    log_returns = [math.log(prices[index] / prices[index - 1]) for index in range(start + 1, end)]
    mean = math.fsum(log_returns) / len(log_returns)
    stdev = _sample_stdev(log_returns, mean)
    funding_rate_sum = None if table.funding is None else math.fsum(table.funding[start + 1 : end])
    return BuyAndHold(
        start=start,
        end=end,
        bars_held=end - 1 - start,
        gross_return=series.gross[0],
        log_return=math.log(prices[end - 1] / prices[start]),
        funding=None if table.funding is None else series.funding[0],
        funding_rate_sum=funding_rate_sum,
        net={stress_key(stress): series.net(costs, stress)[0] for stress in STRESS_MULTIPLIERS},
        mean_log_return_per_bar=mean,
        stdev_log_return_per_bar=stdev,
        sharpe_per_bar=None if stdev is None or stdev == 0.0 else mean / stdev,
    )


def _sample_stdev(values: list[float], mean: float) -> float | None:
    if len(values) < 2:
        return None
    return math.sqrt(math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1))
