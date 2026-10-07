"""Overfitting diagnostics: the deflated Sharpe ratio and PBO by CSCV.

Both are reported next to the label and never change it.

The deflated Sharpe ratio (Bailey and Lopez de Prado, 2014) asks whether the
Sharpe of the config the run takes forward beats the best Sharpe that as many
pure-noise trials would show. The probability of backtest overfitting (Bailey,
Borwein, Lopez de Prado and Zhu, 2017) asks how often the config that ranks
first on half of the walk-forward blocks ranks at or below the median on the
other half, ranking by the statistic validation selection uses.
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
class ConfigUnderTest:
    """The config the deflated Sharpe ratio tests, with its validation net returns.

    ``sharpe`` is None when the returns have no spread.
    """

    config_id: str
    sharpe: float | None
    returns: tuple[float, ...]
    selected: bool


@dataclass(frozen=True, slots=True)
class DeflatedSharpe:
    config_id: str | None
    selected: bool | None
    sharpe_per_trade: float | None
    trades: int | None
    trials: int
    null_sharpe_variance: float | None
    skewness: float | None
    kurtosis: float | None
    expected_max_sharpe: float | None
    dsr: float | None
    note: str | None


@dataclass(frozen=True, slots=True)
class BlockStats:
    """One config's trades in one CSCV block: their count and summed net return."""

    trades: int
    total: float


@dataclass(frozen=True, slots=True)
class Pbo:
    value: float | None
    blocks: int | None
    folds_used: int | None
    splits: int | None
    skipped_splits: int | None
    median_logit: float | None
    note: str | None


@dataclass(frozen=True, slots=True)
class Overfitting:
    deflated_sharpe: DeflatedSharpe
    pbo: Pbo


def expected_max_sharpe(trials: int, trial_variance: float) -> float:
    """The expected highest of ``trials`` Sharpe estimates that are pure noise.

    Each noise Sharpe has variance ``trial_variance``. One trial, or no
    variance, leaves nothing to deflate against.
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


def deflated_sharpe(trials: int, tested: ConfigUnderTest | None) -> DeflatedSharpe:
    """Deflate the tested config's Sharpe by the noise maximum of ``trials`` trials.

    Every pre-registered config is a trial, as in the multiple-testing family,
    so configs that hardly trade cannot shrink the bar. Under the null each
    trial's Sharpe estimate has the sampling variance 1 / (T - 1) of the
    tested config's T trades, so configs with other horizons or trade counts
    do not distort the bar.
    """

    if tested is None:
        return _no_dsr(
            trials, "No config to test: none was selected and none meets the trade floor."
        )
    observations = len(tested.returns)
    moments = sample_moments(tested.returns)
    if tested.sharpe is None or observations < 2 or moments is None:
        return _no_dsr(
            trials, "The tested config's validation trade returns have no spread.", tested
        )
    variance = 1.0 / (observations - 1)
    benchmark = expected_max_sharpe(trials, variance)
    skewness, kurtosis = moments
    dsr = probabilistic_sharpe(tested.sharpe, benchmark, observations, skewness, kurtosis)
    return DeflatedSharpe(
        config_id=tested.config_id,
        selected=tested.selected,
        sharpe_per_trade=tested.sharpe,
        trades=observations,
        trials=trials,
        null_sharpe_variance=variance,
        skewness=skewness,
        kurtosis=kurtosis,
        expected_max_sharpe=benchmark,
        dsr=dsr,
        note=None if dsr is not None else "The trade return distribution is degenerate.",
    )


def cscv_blocks(fold_count: int) -> tuple[tuple[int, ...], ...] | None:
    """Equal contiguous blocks of the most recent walk-forward folds.

    The even block count in [MIN_CSCV_BLOCKS, MAX_CSCV_BLOCKS] that leaves out
    the fewest folds wins, the larger count on a tie. Equal blocks keep the
    in-sample and out-of-sample halves the same length; the oldest left-over
    folds are not used. None when fewer than MIN_CSCV_BLOCKS folds exist.
    """

    counts = [
        count for count in range(MIN_CSCV_BLOCKS, MAX_CSCV_BLOCKS + 1, 2) if count <= fold_count
    ]
    if not counts:
        return None
    blocks = min(counts, key=lambda count: (fold_count % count, -count))
    size = fold_count // blocks
    first = fold_count - blocks * size
    return tuple(
        tuple(range(first + index * size, first + (index + 1) * size)) for index in range(blocks)
    )


def probability_of_backtest_overfitting(stats: Sequence[Sequence[BlockStats]]) -> Pbo:
    """PBO by combinatorially symmetric cross-validation over ``stats[config][block]``.

    For every choice of half the blocks as in-sample, the config with the best
    in-sample mean net return per trade (the first one on a tie) is ranked by
    its out-of-sample mean, ties sharing the average rank. A config without
    trades in a half earns 0 there. A split where every config ties in-sample
    selects nothing and is skipped. A split counts as overfit when the pick
    ranks at or below the median (logit <= 0).
    """

    configs = len(stats)
    if configs < 2:
        return Pbo(None, None, None, None, None, None, "PBO needs at least two configs.")
    blocks = len(stats[0])
    if any(len(row) != blocks for row in stats):
        raise ValueError("Every config needs the same blocks.")
    if blocks < MIN_CSCV_BLOCKS or blocks % 2:
        return Pbo(
            None, blocks, None, None, None, None, "PBO needs an even number of at least 4 blocks."
        )
    totals = [
        (sum(block.trades for block in row), math.fsum(block.total for block in row))
        for row in stats
    ]
    logits: list[float] = []
    skipped = 0
    for chosen in combinations(range(blocks), blocks // 2):
        in_sample = [
            (
                sum(row[block].trades for block in chosen),
                math.fsum(row[block].total for block in chosen),
            )
            for row in stats
        ]
        in_perf = [_mean(trades, total) for trades, total in in_sample]
        if max(in_perf) == min(in_perf):
            skipped += 1
            continue
        out_perf = [
            _mean(all_trades - trades, all_total - total)
            for (all_trades, all_total), (trades, total) in zip(totals, in_sample, strict=True)
        ]
        best = max(range(configs), key=in_perf.__getitem__)
        omega = _average_rank(out_perf, best) / (configs + 1)
        logits.append(math.log(omega / (1.0 - omega)))
    if not logits:
        return Pbo(
            None,
            blocks,
            None,
            0,
            skipped,
            None,
            "No split separates the configs in-sample, so nothing was selected.",
        )
    return Pbo(
        value=sum(1 for logit in logits if logit <= 0.0) / len(logits),
        blocks=blocks,
        folds_used=None,
        splits=len(logits),
        skipped_splits=skipped,
        median_logit=statistics.median(logits),
        note=None,
    )


def _mean(trades: int, total: float) -> float:
    """Mean net return per trade; a config that does not trade earns 0."""

    return total / trades if trades > 0 else 0.0


def _average_rank(values: Sequence[float], index: int) -> float:
    """1 is the worst; tied values share the average of their ranks."""

    target = values[index]
    below = sum(1 for value in values if value < target)
    equal = sum(1 for value in values if value == target)
    return below + (equal + 1) / 2.0


def _no_dsr(trials: int, note: str, tested: ConfigUnderTest | None = None) -> DeflatedSharpe:
    return DeflatedSharpe(
        config_id=None if tested is None else tested.config_id,
        selected=None if tested is None else tested.selected,
        sharpe_per_trade=None,
        trades=None if tested is None else len(tested.returns),
        trials=trials,
        null_sharpe_variance=None,
        skewness=None,
        kurtosis=None,
        expected_max_sharpe=None,
        dsr=None,
        note=note,
    )
