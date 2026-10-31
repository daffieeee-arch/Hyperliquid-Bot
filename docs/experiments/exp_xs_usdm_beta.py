"""Market beta of an S4 family's selected config over the holdout, after the harness scored it.

PAPER research only. Rule 4 of exp_xsmom_usdm and exp_xscarry_usdm reads the
``beta_adjusted`` block of this script's output. It runs only when
``result.json`` is a completed run with a ``holdout`` block, that is after
validation selected a config and the harness scored it once on the holdout,
so it reads nothing the harness had sealed. It decides no selection.

It rebuilds the selected config's holdout periods with the pinned harness
code (``PanelSource``), checks them against the harness's own series and
``result.json`` (and fails closed on any difference), and regresses the
book's per-period gross return (price only, before costs and funding) on two
market returns over each period's own fill and exit days:

- ``universe``: the equal-weight price return of every name of the decision
  day's universe (traded, ranked at most ``universe_size``, signal known)
  that fills, held and exited by the harness's rule (the last traded close
  at or before the exit day, never across a day without a row);
- ``btc``: BTCUSDT's price return by the same rule (descriptive).

The slope is ordinary least squares over the periods that held positions;
standard errors are Newey-West (Bartlett kernel, the harness's lag rule).
The beta-adjusted mean subtracts beta times each held period's market return
from that period's return and averages over every period the harness scored,
so an unwound period, which held nothing, carries no market return.

    PYTHONPATH=src uv run --frozen python docs/experiments/exp_xs_usdm_beta.py \\
        "$STUDY_DIR/spec.yaml" "$STUDY_DIR/out/result.json" > "$STUDY_DIR/beta.json"

The period loop mirrors ``PanelSource.window`` through its private helpers,
which the code pin fixes; the check against ``window`` guards that mirror.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from research.harness.data import fingerprint_inputs, load_panel
from research.harness.evaluate import next_period, summarize
from research.harness.portfolio import PanelSource, _Skipped
from research.harness.spec import (
    ConfigSpec,
    exact_quantile,
    load_document,
    spec_sha256,
    validate_spec,
)
from research.harness.splits import walk_forward
from research.harness.stats import _newey_west_lag

BTC = "BTCUSDT"


@dataclass(frozen=True, slots=True)
class Period:
    decision: int
    entry: int
    exit: int
    held: bool
    gross: float
    long_gross: float
    short_gross: float
    universe: float | None
    btc: float | None


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"exp_xs_usdm_beta: {message}")


def _price_return(source: PanelSource, symbol: int, entry: int, exit_index: int) -> float:
    """A unit long's price return, exited like a harness position; no funding."""

    prices = source.panel.prices[symbol]
    traded = source.panel.traded[symbol]
    entry_price = prices[entry]
    if entry_price is None:
        _fail("a filled name has no entry price")
    last = entry
    for day in range(entry + 1, source._held_through(symbol, entry, exit_index) + 1):
        if traded[day] is True:
            last = day
    exit_price = prices[last]
    if exit_price is None:
        _fail("a held name has no exit price")
    return (exit_price - entry_price) / entry_price


def _universe_return(
    source: PanelSource, decision: int, entry: int, exit_index: int
) -> float | None:
    filled = [
        symbol
        for _signal, symbol in source._eligible(decision)
        if source._fills(symbol, decision, entry)
    ]
    if not filled:
        return None
    return math.fsum(_price_return(source, symbol, entry, exit_index) for symbol in filled) / len(
        filled
    )


def _btc_return(source: PanelSource, decision: int, entry: int, exit_index: int) -> float | None:
    if BTC not in source.panel.symbols:
        return None
    symbol = source.panel.symbols.index(BTC)
    if not source._fills(symbol, decision, entry):
        return None
    return _price_return(source, symbol, entry, exit_index)


def rebuild(source: PanelSource, config: ConfigSpec, start: int, end: int) -> list[Period]:
    """The window's periods, as ``PanelSource.window`` scores them, with each leg's part."""

    if config.quantile is None:
        _fail(f"config {config.id} has no quantile")
    latency = source.spec.costs.latency_bars
    quantile = exact_quantile(config.quantile)
    periods: list[Period] = []
    decision = start
    while (period := next_period(decision, latency, config.horizon_bars, end)) is not None:
        entry, exit_index = period
        opened = source._positions(quantile, decision, entry)
        if isinstance(opened, _Skipped):
            decision += 1
            continue
        if opened.unwound:
            if opened.positions:
                periods.append(
                    Period(decision, entry, exit_index, False, 0.0, 0.0, 0.0, None, None)
                )
            decision = max(entry, decision + 1)
            continue
        gross = 0.0
        long_gross = 0.0
        short_gross = 0.0
        for position in opened.positions:
            value = source._hold(position, entry, exit_index).value
            gross += value  # the same order and arithmetic as PanelSource.window
            if position.side > 0:
                long_gross += value
            else:
                short_gross += value
        periods.append(
            Period(
                decision,
                entry,
                exit_index,
                True,
                gross,
                long_gross,
                short_gross,
                _universe_return(source, decision, entry, exit_index),
                _btc_return(source, decision, entry, exit_index),
            )
        )
        decision = exit_index
    return periods


def ols_hac(y: list[float], x: list[float]) -> dict[str, float | int | None] | None:
    """y = alpha + beta x by OLS, with Newey-West (Bartlett) standard errors."""

    count = len(y)
    if count < 3:
        return None
    mean_x = math.fsum(x) / count
    mean_y = math.fsum(y) / count
    sxx = math.fsum((value - mean_x) ** 2 for value in x)
    if sxx == 0.0:
        return None
    beta = math.fsum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True)) / sxx
    alpha = mean_y - beta * mean_x
    residuals = [b - alpha - beta * a for a, b in zip(x, y, strict=True)]
    scores = [(e, e * a) for a, e in zip(x, residuals, strict=True)]
    lag = _newey_west_lag(count)
    meat = [[0.0, 0.0], [0.0, 0.0]]
    for distance in range(lag + 1):
        weight = 1.0 if distance == 0 else 1.0 - distance / (lag + 1.0)
        for i in range(2):
            for j in range(2):
                total = math.fsum(
                    scores[t][i] * scores[t - distance][j] for t in range(distance, count)
                )
                if distance:
                    total += math.fsum(
                        scores[t - distance][i] * scores[t][j] for t in range(distance, count)
                    )
                meat[i][j] += weight * total
    sum_x = math.fsum(x)
    sum_xx = math.fsum(value * value for value in x)
    determinant = count * sum_xx - sum_x * sum_x
    bread = [
        [sum_xx / determinant, -sum_x / determinant],
        [-sum_x / determinant, count / determinant],
    ]
    covariance = [
        [
            math.fsum(bread[i][k] * meat[k][m] * bread[m][j] for k in range(2) for m in range(2))
            for j in range(2)
        ]
        for i in range(2)
    ]
    alpha_se = math.sqrt(covariance[0][0]) if covariance[0][0] > 0.0 else None
    beta_se = math.sqrt(covariance[1][1]) if covariance[1][1] > 0.0 else None
    return {
        "periods": count,
        "market_mean": mean_x,
        "beta": beta,
        "beta_hac_se": beta_se,
        "beta_hac_t": None if beta_se is None else beta / beta_se,
        "alpha": alpha,
        "alpha_hac_t": None if alpha_se is None else alpha / alpha_se,
        "hac_lag": lag,
    }


def _adjusted(values: list[float], periods: list[Period], beta: float, field: str) -> float:
    """Mean over every scored period of the value minus beta times its market return."""

    market = [getattr(period, field) if period.held else 0.0 for period in periods]
    if any(value is None for value in market):
        _fail(f"a held period has no {field} return")
    return math.fsum(v - beta * m for v, m in zip(values, market, strict=True)) / len(values)


def main(spec_path: Path, result_path: Path) -> dict[str, object]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    holdout = result.get("holdout")
    if result.get("status") != "completed" or not isinstance(holdout, dict):
        _fail("result.json has no holdout block; the holdout was not scored, so nothing is read")
    document = load_document(spec_path)
    if spec_sha256(document) != result.get("spec_sha256"):
        _fail("the spec is not the one result.json ran")
    spec = validate_spec(document)
    if fingerprint_inputs(spec, spec_path.parent) != result.get("data_fingerprint"):
        _fail("the panel is not the one result.json ran")
    config = next((item for item in spec.configs if item.id == holdout.get("config_id")), None)
    if config is None:
        _fail("the holdout config is not in the spec")
    source = PanelSource(spec, load_panel(spec, spec_path.parent))
    _folds, (start, end) = walk_forward(source.length, spec.split)
    periods = rebuild(source, config, start, end)
    series = source.window(config, start, end)
    if tuple(period.gross for period in periods) != series.gross:
        _fail("the rebuilt periods differ from PanelSource.window")
    net = series.net(spec.costs, 1.0)
    reported = holdout["net"]["1.0"]
    if summarize(net).mean_return != reported["mean_return"] or len(net) != reported["trade_count"]:
        _fail("the rebuilt holdout differs from result.json")
    if summarize(series.gross).mean_return != holdout["gross"]["mean_return"]:
        _fail("the rebuilt holdout gross differs from result.json")
    held = [period for period in periods if period.held]
    gross = list(series.gross)
    fits: dict[str, object] = {}
    adjusted: dict[str, object] = {"gross_mean": None, "net_mean_1x": None}
    for field in ("universe", "btc"):
        usable = [period for period in held if getattr(period, field) is not None]
        fit = ols_hac([p.gross for p in usable], [getattr(p, field) for p in usable])
        fits[field] = {"periods_without_return": len(held) - len(usable), "fit": fit}
        if field == "universe" and fit is not None and len(usable) == len(held):
            beta = fit["beta"]
            if not isinstance(beta, float):
                _fail("the universe beta is not a number")
            adjusted = {
                "gross_mean": _adjusted(gross, periods, beta, field),
                "net_mean_1x": _adjusted(list(net), periods, beta, field),
            }
    return {
        "config_id": config.id,
        "holdout_days": [start, end],
        "periods": len(periods),
        "unwound_periods": len(periods) - len(held),
        "check": "rebuilt periods equal PanelSource.window and result.json",
        "gross_mean": summarize(gross).mean_return,
        "net_mean_1x": summarize(net).mean_return,
        "long_leg_gross_mean": None
        if not held
        else math.fsum(p.long_gross for p in held) / len(held),
        "short_leg_gross_mean": None
        if not held
        else math.fsum(p.short_gross for p in held) / len(held),
        "market": fits,
        "beta_adjusted": adjusted,
    }


if __name__ == "__main__":
    json.dump(main(Path(sys.argv[1]), Path(sys.argv[2])), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
