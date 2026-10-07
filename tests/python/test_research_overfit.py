"""Deterministic tests for the deflated Sharpe ratio and PBO by CSCV."""

from __future__ import annotations

import math
import statistics
from statistics import NormalDist

import pytest

from research.harness.overfit import (
    BlockStats,
    SharpeTrial,
    cscv_blocks,
    deflated_sharpe,
    expected_max_sharpe,
    probabilistic_sharpe,
    probability_of_backtest_overfitting,
    sample_moments,
)
from research.harness.overfit import _block_sharpe as block_sharpe


def test_deflated_sharpe_matches_the_published_example() -> None:
    # Bailey and Lopez de Prado (2014): an annualized Sharpe of 2.5 on 1250
    # daily returns with skewness -3 and kurtosis 10, after 100 trials whose
    # annualized Sharpes have a variance of 0.5.
    daily_sharpe = 2.5 / math.sqrt(250.0)
    benchmark = expected_max_sharpe(100, 0.5 / 250.0)
    assert benchmark == pytest.approx(0.1132, abs=5e-4)
    dsr = probabilistic_sharpe(daily_sharpe, benchmark, 1250, -3.0, 10.0)
    assert dsr == pytest.approx(0.9004, abs=5e-4)


def test_probabilistic_sharpe_uses_n_minus_one_and_plain_kurtosis() -> None:
    # Normal returns (skew 0, kurtosis 3) and five observations: z = SR * 2 / sqrt(1.125).
    expected = NormalDist().cdf(0.5 * 2.0 / math.sqrt(1.0 + 0.5 * 0.25))
    assert probabilistic_sharpe(0.5, 0.0, 5, 0.0, 3.0) == pytest.approx(expected)
    assert probabilistic_sharpe(0.5, 0.0, 1, 0.0, 3.0) is None


def test_more_trials_raise_the_noise_maximum() -> None:
    assert expected_max_sharpe(1, 0.04) == 0.0
    assert expected_max_sharpe(10, 0.0) == 0.0
    maxima = [expected_max_sharpe(trials, 0.04) for trials in (2, 10, 100)]
    assert maxima == sorted(maxima)
    assert maxima[0] > 0.0
    # Fatter tails and negative skew widen the estimate's error.
    normal = probabilistic_sharpe(0.2, 0.0, 100, 0.0, 3.0)
    skewed = probabilistic_sharpe(0.2, 0.0, 100, -2.0, 9.0)
    assert normal is not None and skewed is not None
    assert skewed < normal


def test_sample_moments_use_population_moments() -> None:
    assert sample_moments([1.0, 1.0, 1.0]) is None
    moments = sample_moments([1.0, 2.0, 3.0, 4.0, 5.0])
    assert moments is not None
    skewness, kurtosis = moments
    assert skewness == pytest.approx(0.0)
    assert kurtosis == pytest.approx(1.7)


def test_deflated_sharpe_tests_the_best_floored_trial_against_every_trial() -> None:
    returns = (0.02, -0.01, 0.03, 0.0, 0.01, -0.02, 0.04, 0.01)
    lucky = SharpeTrial("lucky", 3.0, (0.1, 0.11), meets_trade_floor=False)
    real = SharpeTrial("real", 0.5, returns, meets_trade_floor=True)
    weak = SharpeTrial("weak", 0.1, returns, meets_trade_floor=True)
    result = deflated_sharpe([lucky, real, weak])
    # Too few trades to be the config under test, but still a trial.
    assert result.config_id == "real"
    assert result.trials == 3
    assert result.trades == len(returns)
    assert result.trial_sharpe_variance == pytest.approx(statistics.variance([3.0, 0.5, 0.1]))
    alone = deflated_sharpe([real])
    assert alone.expected_max_sharpe == 0.0
    assert alone.trial_sharpe_variance is None
    assert result.dsr is not None and alone.dsr is not None
    assert result.dsr < alone.dsr


def test_deflated_sharpe_says_why_it_was_not_computed() -> None:
    assert deflated_sharpe([]).note is not None
    unfloored = deflated_sharpe([SharpeTrial("a", 1.0, (0.1, 0.2), meets_trade_floor=False)])
    assert unfloored.dsr is None
    assert unfloored.trials == 1
    assert unfloored.note is not None and "trade floor" in unfloored.note


def test_cscv_groups_folds_into_even_contiguous_blocks() -> None:
    assert cscv_blocks(3) is None
    assert cscv_blocks(4) == ((0,), (1,), (2,), (3,))
    seven = cscv_blocks(7)
    assert seven is not None
    assert [len(group) for group in seven] == [2, 1, 1, 1, 1, 1]
    forty = cscv_blocks(40)
    assert forty is not None
    assert len(forty) == 16
    assert [index for group in forty for index in group] == list(range(40))


def test_block_sharpe_is_mean_over_sample_stdev_of_per_bar_pnl() -> None:
    values = [1.0, 0.0] * 5
    expected = statistics.mean(values) / statistics.stdev(values)
    assert block_sharpe([_stats(values)], [0]) == pytest.approx(expected)
    assert block_sharpe([_stats([0.0] * 10)], [0]) == 0.0


def test_pbo_is_zero_when_one_config_dominates_every_block() -> None:
    winner = [_stats([1.0, 0.0] * 5) for _ in range(4)]
    loser = [_stats([-1.0, 0.0] * 5) for _ in range(4)]
    result = probability_of_backtest_overfitting([loser, winner])
    assert result.value == 0.0
    assert (result.blocks, result.combinations) == (4, 6)
    assert result.median_logit is not None and result.median_logit > 0.0


def test_pbo_is_one_when_the_in_sample_best_reverses_out_of_sample() -> None:
    up = _stats([1.0, 0.0] * 5)
    down = _stats([-1.0, 0.0] * 5)
    first = [up, down, up, down]
    second = [down, up, down, up]
    result = probability_of_backtest_overfitting([first, second])
    # Two splits pick the config that then loses; the four tied splits rank
    # the in-sample pick at the median, which also counts as overfit.
    assert result.value == 1.0
    assert result.median_logit is not None and result.median_logit <= 0.0


def test_pbo_needs_two_configs_and_an_even_block_count() -> None:
    block = _stats([1.0, 0.0, -0.5])
    one = probability_of_backtest_overfitting([[block] * 4])
    assert one.value is None
    assert one.note is not None and "two configs" in one.note
    odd = probability_of_backtest_overfitting([[block] * 5, [block] * 5])
    assert odd.value is None
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting([[block] * 4, [block] * 6])


def _stats(values: list[float]) -> BlockStats:
    return BlockStats(
        bars=len(values),
        total=math.fsum(values),
        total_squares=math.fsum(value * value for value in values),
    )
