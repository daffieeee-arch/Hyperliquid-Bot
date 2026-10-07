"""Overfitting diagnostics: the deflated Sharpe ratio and PBO by CSCV.

Both are reported next to the label and never change it.

The deflated Sharpe ratio (Bailey and Lopez de Prado, 2014) asks whether the
best validation Sharpe among the pre-registered configs beats the best Sharpe
that as many pure-noise trials would show. The probability of backtest
overfitting (Bailey, Borwein, Lopez de Prado and Zhu, 2017) asks how often the
config that looks best on half of the walk-forward blocks ranks at or below
the median on the other half.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from statistics import NormalDist
from typing import Final

EULER_GAMMA: Final = 0.5772156649015329
MIN_CSCV_BLOCKS: Final = 4
MAX_CSCV_BLOCKS: Final = 16
_NORMAL: Final = NormalDist()


@dataclass(frozen=True, slots=True)
class SharpeTrial:
    """One config's validation net Sharpe per trade and the returns behind it."""

    config_id: str
    sharpe: float
    returns: tuple[float, ...]
    meets_trade_floor: bool


@dataclass(frozen=True, slots=True)
class DeflatedSharpe:
    config_id: str | None
    sharpe_per_trade: float | None
    trades: int | None
    trials: int
    trial_sharpe_variance: float | None
    skewness: float | None
    kurtosis: float | None
    expected_max_sharpe: float | None
    dsr: float | None
    note: str | None


@dataclass(frozen=True, slots=True)
class BlockStats:
    """Per-bar net P&L of one config over one CSCV block; bars without an exit are 0."""

    bars: int
    total: float
    total_squares: float


@dataclass(frozen=True, slots=True)
class Pbo:
    value: float | None
    blocks: int | None
    combinations: int | None
    median_logit: float | None
    note: str | None


@dataclass(frozen=True, slots=True)
class Overfitting:
    deflated_sharpe: DeflatedSharpe
    pbo: Pbo


def expected_max_sharpe(trials: int, trial_variance: float) -> float:
    """The expected highest of ``trials`` Sharpe estimates that are pure noise.

    Each noise Sharpe is drawn with the observed variance across trials. One
    trial, or no spread between trials, leaves nothing to deflate against.
    """

    if trials < 1:
        raise ValueError("expected_max_sharpe needs at least one trial.")
    if trials == 1 or trial_variance <= 0.0:
        return 0.0
    first = _NORMAL.inv_cdf(1.0 - 1.0 / trials)
    second = _NORMAL.inv_cdf(1.0 - 1.0 / (trials * math.e))
    return math.sqrt(trial_variance) * ((1.0 - EULER_GAMMA) * first + EULER_GAMMA * second)


def probabilistic_sharpe(
    sharpe: float,
    benchmark: float,
    observations: int,
    skewness: float,
    kurtosis: float,
) -> float | None:
    """P(true Sharpe > benchmark) for a per-observation Sharpe estimate.

    ``kurtosis`` is the plain kurtosis (3 for a normal sample). Returns None
    when there are fewer than two observations or the estimate's variance term
    is not positive.
    """

    if observations < 2:
        return None
    spread = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe
    if not spread > 0.0:
        return None
    z_score = (sharpe - benchmark) * math.sqrt(observations - 1) / math.sqrt(spread)
    return _NORMAL.cdf(z_score)


def sample_moments(values: Sequence[float]) -> tuple[float, float] | None:
    """Skewness and plain kurtosis from population moments; None for a flat sample."""

    count = len(values)
    if count < 2:
        return None
    mean = math.fsum(values) / count
    second = math.fsum((value - mean) ** 2 for value in values) / count
    if second <= 0.0:
        return None
    third = math.fsum((value - mean) ** 3 for value in values) / count
    fourth = math.fsum((value - mean) ** 4 for value in values) / count
    return third / second**1.5, fourth / (second * second)


def deflated_sharpe(trials: Sequence[SharpeTrial]) -> DeflatedSharpe:
    """Deflate the best Sharpe that meets the trade floor by all trials' spread.

    Every config with a validation Sharpe is a trial. The best one must also
    meet ``sample.min_trades_validation``, so a handful of lucky trades cannot
    be the config under test.
    """

    count = len(trials)
    if count == 0:
        return _no_dsr(0, "No config has a validation Sharpe (at least two trades with spread).")
    variance = statistics.variance([trial.sharpe for trial in trials]) if count >= 2 else None
    floored = [trial for trial in trials if trial.meets_trade_floor]
    if not floored:
        return _no_dsr(count, "No config with a validation Sharpe meets the trade floor.")
    best = floored[0]
    for trial in floored[1:]:
        if trial.sharpe > best.sharpe:
            best = trial
    benchmark = expected_max_sharpe(count, 0.0 if variance is None else variance)
    moments = sample_moments(best.returns)
    if moments is None:
        return _no_dsr(count, "The best config's trade returns have no spread.")
    skewness, kurtosis = moments
    dsr = probabilistic_sharpe(best.sharpe, benchmark, len(best.returns), skewness, kurtosis)
    return DeflatedSharpe(
        config_id=best.config_id,
        sharpe_per_trade=best.sharpe,
        trades=len(best.returns),
        trials=count,
        trial_sharpe_variance=variance,
        skewness=skewness,
        kurtosis=kurtosis,
        expected_max_sharpe=benchmark,
        dsr=dsr,
        note=None if dsr is not None else "The trade return distribution is degenerate.",
    )


def cscv_blocks(fold_count: int) -> tuple[tuple[int, ...], ...] | None:
    """Group walk-forward folds into an even number of contiguous blocks.

    At most ``MAX_CSCV_BLOCKS`` blocks, as even in size as possible, with the
    earlier blocks taking any extra fold. None when fewer than
    ``MIN_CSCV_BLOCKS`` blocks are possible.
    """

    blocks = min(fold_count - fold_count % 2, MAX_CSCV_BLOCKS)
    if blocks < MIN_CSCV_BLOCKS:
        return None
    base, extra = divmod(fold_count, blocks)
    groups: list[tuple[int, ...]] = []
    start = 0
    for index in range(blocks):
        size = base + (1 if index < extra else 0)
        groups.append(tuple(range(start, start + size)))
        start += size
    return tuple(groups)


def probability_of_backtest_overfitting(stats: Sequence[Sequence[BlockStats]]) -> Pbo:
    """PBO by combinatorially symmetric cross-validation over ``stats[config][block]``.

    For every choice of half the blocks as in-sample, the config with the best
    in-sample Sharpe of per-bar P&L (the first one on a tie) is ranked by its
    out-of-sample Sharpe, ties sharing the average rank. A split counts as
    overfit when that rank is at or below the median (logit <= 0).
    """

    configs = len(stats)
    if configs < 2:
        return Pbo(None, None, None, None, "PBO needs at least two configs.")
    blocks = len(stats[0])
    if any(len(row) != blocks for row in stats):
        raise ValueError("Every config needs the same blocks.")
    if blocks < MIN_CSCV_BLOCKS or blocks % 2:
        return Pbo(None, blocks, None, None, "PBO needs an even number of at least four blocks.")
    logits: list[float] = []
    for chosen in combinations(range(blocks), blocks // 2):
        in_sample = set(chosen)
        out_of_sample = [block for block in range(blocks) if block not in in_sample]
        in_perf = [_block_sharpe(row, chosen) for row in stats]
        out_perf = [_block_sharpe(row, out_of_sample) for row in stats]
        best = 0
        for index in range(1, configs):
            if in_perf[index] > in_perf[best]:
                best = index
        omega = _average_rank(out_perf, best) / (configs + 1)
        logits.append(math.log(omega / (1.0 - omega)))
    overfit = sum(1 for logit in logits if logit <= 0.0)
    return Pbo(
        value=overfit / len(logits),
        blocks=blocks,
        combinations=len(logits),
        median_logit=statistics.median(logits),
        note=None,
    )


def _block_sharpe(row: Sequence[BlockStats], blocks: Sequence[int]) -> float:
    """Mean over sample stdev of per-bar P&L; a flat series scores 0."""

    bars = sum(row[block].bars for block in blocks)
    if bars < 2:
        return 0.0
    total = math.fsum(row[block].total for block in blocks)
    squares = math.fsum(row[block].total_squares for block in blocks)
    variance = (squares - total * total / bars) / (bars - 1)
    if not variance > 0.0:
        return 0.0
    return (total / bars) / math.sqrt(variance)


def _average_rank(values: Sequence[float], index: int) -> float:
    """1 is the worst; tied values share the average of their ranks."""

    target = values[index]
    below = sum(1 for value in values if value < target)
    equal = sum(1 for value in values if value == target)
    return below + (equal + 1) / 2.0


def _no_dsr(trials: int, note: str) -> DeflatedSharpe:
    return DeflatedSharpe(
        config_id=None,
        sharpe_per_trade=None,
        trades=None,
        trials=trials,
        trial_sharpe_variance=None,
        skewness=None,
        kurtosis=None,
        expected_max_sharpe=None,
        dsr=None,
        note=note,
    )
