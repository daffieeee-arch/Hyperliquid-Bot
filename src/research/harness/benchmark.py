"""Buy-and-hold context next to the scored family. It never changes the label."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from research.harness.costs import STRESS_MULTIPLIERS, stress_key
from research.harness.data import BarTable
from research.harness.errors import HarnessError
from research.harness.evaluate import Decision, Trade, summarize, trade_series
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


EVALUATED: Final = "evaluated"
SEALED: Final = "sealed"
NO_FOLDS: Final = "no_folds"
TOO_SHORT: Final = "too_short"


@dataclass(frozen=True, slots=True)
class Window:
    """One benchmark window: ``result`` is set only when ``status`` is evaluated."""

    status: str
    result: BuyAndHold | None = None


@dataclass(frozen=True, slots=True)
class Benchmark:
    validation: Window
    # Sealed until validation selects a config, as for the strategy.
    holdout: Window


def benchmark(costs: CostSpec, table: BarTable, decision: Decision) -> Benchmark:
    """Buy-and-hold over the validation test folds and, once opened, the holdout.

    The holdout window is read only when validation selected a config, the
    same rule the strategy follows, so a sealed holdout stays unread.
    """

    validation = Window(NO_FOLDS)
    if decision.folds:
        validation = _window(
            costs, table, decision.folds[0].test_start, decision.folds[-1].test_end
        )
    holdout = Window(SEALED)
    if decision.holdout_config_id is not None:
        holdout = _window(costs, table, decision.holdout_start, decision.holdout_end)
    return Benchmark(validation=validation, holdout=holdout)


def _window(costs: CostSpec, table: BarTable, start: int, end: int) -> Window:
    result = buy_and_hold(costs, table, start, end)
    return Window(TOO_SHORT) if result is None else Window(EVALUATED, result)


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
    per_bar = summarize(
        [math.log(prices[index] / prices[index - 1]) for index in range(entry + 1, end)]
    )
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
        mean_log_return_per_bar=_number(per_bar.mean_return),
        stdev_log_return_per_bar=per_bar.stdev,
        sharpe_per_bar=per_bar.sharpe_per_trade,
    )


def _number(value: float | None) -> float:
    if value is None:
        raise HarnessError("invariant", "A held benchmark window has at least one bar.")
    return value
