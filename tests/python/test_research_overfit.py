"""Deterministic tests for the deflated Sharpe ratio and PBO by CSCV."""

from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from research.harness.overfit import (
    BlockStats,
    ConfigUnderTest,
    cscv_blocks,
    deflated_sharpe,
    expected_max_sharpe,
    probabilistic_sharpe,
    probability_of_backtest_overfitting,
    sample_moments,
)

_RETURNS = (0.02, -0.01, 0.03, 0.0, 0.01, -0.02, 0.04, 0.01, 0.02, -0.01, 0.0)


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


def test_the_noise_bar_uses_every_trial_and_the_null_sampling_variance() -> None:
    tested = ConfigUnderTest("real", 0.4, _RETURNS, selected=True)
    alone = deflated_sharpe(1, tested)
    assert alone.expected_max_sharpe == 0.0
    grid = deflated_sharpe(20, tested)
    # Each noise Sharpe has the sampling variance of the tested config's trades.
    variance = 1.0 / (len(_RETURNS) - 1)
    assert grid.null_sharpe_variance == pytest.approx(variance)
    assert grid.expected_max_sharpe == pytest.approx(expected_max_sharpe(20, variance))
    assert grid.trials == 20
    assert grid.config_id == "real" and grid.selected is True
    assert alone.dsr is not None and grid.dsr is not None
    assert grid.dsr < alone.dsr


def test_deflated_sharpe_says_why_it_was_not_computed() -> None:
    missing = deflated_sharpe(3, None)
    assert missing.dsr is None
    assert missing.trials == 3
    assert missing.config_id is None
    assert missing.note is not None and "trade floor" in missing.note
    # A selected config whose returns are all equal has no Sharpe; the note
    # names the real reason and the result still names the config.
    flat = deflated_sharpe(3, ConfigUnderTest("flat", None, (0.01, 0.01, 0.01), selected=True))
    assert flat.dsr is None
    assert (flat.config_id, flat.selected, flat.trades) == ("flat", True, 3)
    assert flat.note is not None and "no spread" in flat.note


def test_cscv_uses_equal_blocks_of_the_most_recent_folds() -> None:
    assert cscv_blocks(3) is None
    assert cscv_blocks(8) == tuple((index,) for index in range(8))
    # Seven folds: six blocks, the oldest fold left out.
    assert cscv_blocks(7) == tuple((index,) for index in range(1, 7))
    forty = cscv_blocks(40)
    assert forty is not None
    assert [len(group) for group in forty] == [4] * 10
    thirty_one = cscv_blocks(31)
    assert thirty_one is not None
    assert [len(group) for group in thirty_one] == [3] * 10
    assert thirty_one[0][0] == 1
    assert [index for group in thirty_one for index in group] == list(range(1, 31))


def test_pbo_is_zero_when_one_config_dominates_every_block() -> None:
    winner = [BlockStats(5, 5.0)] * 4
    loser = [BlockStats(5, -5.0)] * 4
    result = probability_of_backtest_overfitting([loser, winner])
    assert result.value == 0.0
    assert (result.in_sample_floor, result.blocks, result.splits, result.skipped_splits) == (
        1,
        4,
        6,
        0,
    )
    assert result.median_logit is not None and result.median_logit > 0.0


def test_pbo_is_one_when_the_in_sample_best_reverses_out_of_sample() -> None:
    up = BlockStats(5, 5.0)
    down = BlockStats(5, -5.0)
    result = probability_of_backtest_overfitting([[up, down, up, down], [down, up, down, up]])
    # Two splits pick the config that then loses; in the four others both
    # configs tie in-sample, nothing is selected, and the split is skipped.
    assert result.value == 1.0
    assert (result.splits, result.skipped_splits) == (2, 4)


def test_a_pick_that_only_ties_out_of_sample_counts_as_overfit() -> None:
    # Both configs trade in every block; only the first earns, in block 0.
    # Each split with block 0 in-sample picks the first, which then ties the
    # second at the median out of sample (logit 0). The other splits tie
    # in-sample and are skipped.
    lucky = [BlockStats(5, 5.0), BlockStats(5, 0.0), BlockStats(5, 0.0), BlockStats(5, 0.0)]
    flat = [BlockStats(5, 0.0)] * 4
    result = probability_of_backtest_overfitting([lucky, flat])
    assert result.value == 1.0
    assert (result.splits, result.skipped_splits) == (3, 3)


def test_equal_out_of_sample_blocks_tie_exactly() -> None:
    # Same shape, decimal returns. Taking a half as the total minus the other
    # half would round 0.2 + 0.2 differently for the two configs (0.4 against
    # 0.39999999999999997) and break the out-of-sample tie.
    pick = [BlockStats(5, 0.1), BlockStats(5, 0.2), BlockStats(5, 0.2), BlockStats(5, 0.2)]
    rival = [BlockStats(5, 0.01), BlockStats(5, 0.2), BlockStats(5, 0.2), BlockStats(5, 0.2)]
    result = probability_of_backtest_overfitting([pick, rival])
    assert result.value == 1.0
    assert (result.splits, result.skipped_splits, result.median_logit) == (3, 3, 0.0)


def test_the_in_sample_floor_keeps_thin_configs_out_of_each_split() -> None:
    # One lucky trade in block 0, and nothing else.
    thin = [BlockStats(1, 5.0), BlockStats(0, 0.0), BlockStats(0, 0.0), BlockStats(0, 0.0)]
    better = [BlockStats(5, 1.0)] * 4
    worse = [BlockStats(5, 0.5)] * 4
    # Without a floor, every split with block 0 in-sample picks the thin config,
    # which earns 0 out of sample and ranks last; the other three pick `better`.
    loose = probability_of_backtest_overfitting([thin, better, worse])
    assert loose.value == 0.5
    assert (loose.in_sample_floor, loose.splits) == (1, 6)
    # A floor of 3 in-sample trades leaves the thin config out of every split.
    floored = probability_of_backtest_overfitting([thin, better, worse], min_trades=3)
    assert floored.value == 0.0
    assert (floored.in_sample_floor, floored.splits, floored.skipped_splits) == (3, 6, 0)
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting([thin, better], min_trades=0)


def test_pbo_reports_why_it_has_no_value() -> None:
    idle = [BlockStats(0, 0.0)] * 4
    nothing = probability_of_backtest_overfitting([idle, idle])
    assert nothing.value is None
    assert (nothing.splits, nothing.skipped_splits) == (0, 6)
    assert nothing.note is not None and "nothing was selected" in nothing.note
    # A config with trades but no rival above the floor selects nothing either.
    alone = probability_of_backtest_overfitting([[BlockStats(5, 1.0)] * 4, idle])
    assert (alone.value, alone.splits, alone.skipped_splits) == (None, 0, 6)
    one = probability_of_backtest_overfitting([idle])
    assert (one.value, one.blocks) == (None, None)
    assert one.note is not None and "two configs" in one.note
    odd = probability_of_backtest_overfitting([[BlockStats(1, 1.0)] * 5] * 2)
    assert odd.value is None
    assert odd.note is not None and "got 5" in odd.note
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting([idle, [BlockStats(0, 0.0)] * 6])
