"""Synthetic causality, exposure and economic counterexamples for discovery."""

from __future__ import annotations

import math
from dataclasses import replace
from itertools import pairwise
from typing import Any

import pytest

from research.discovery import (
    ALTS,
    BTC,
    HOUR,
    MINUTE,
    DiscoveryBar,
    DiscoveryConfig,
    Episode,
    _summary,
    benchmarks,
    configs,
    evaluate_config,
    generate_episodes,
    run_batch,
    targets,
)
from research.harness.errors import IntegrityError
from research.repaired_accounting import Frame, Instrument, ReplaySpec, simulate

START = 200 * HOUR
SPLIT = START + 12 * HOUR
END = START + 24 * HOUR


def oi_case(profit: float = 0.02) -> tuple[ReplaySpec, list[Frame], dict[str, list[DiscoveryBar]]]:
    bars = []
    oi = 1000.0
    last = 100.0
    for index in range(25):
        if index % 6 == 1:
            oi *= 0.98
        if index % 6 in (1, 2):
            last = 100 * 1.05 ** (index // 6 + 1)
        elif index % 6 == 3:
            last *= 1 - profit
        instant = START + index * HOUR
        bars.append(DiscoveryBar(BTC, instant, last, oi, instant - 5 * MINUTE))
    frames = [Frame(bar.close_ms, {BTC: bar.close}, {BTC: 0.0}, {BTC: bar.close}) for bar in bars]
    spec = ReplaySpec(START, END, HOUR, 10_000.0, (Instrument(BTC, "perp", 5.0, 2.0),))
    return spec, frames, {BTC: bars}


def residual_bars(flat_btc: bool = False) -> dict[str, list[DiscoveryBar]]:
    output: dict[str, list[DiscoveryBar]] = {symbol: [] for symbol in (BTC, *ALTS)}
    price = 100.0
    for index in range(-180, 80):
        price *= 1 + 0.002 * math.sin(index * 0.7) if not flat_btc else 1
        instant = START + index * HOUR
        output[BTC].append(DiscoveryBar(BTC, instant, price))
        output[ALTS[0]].append(DiscoveryBar(ALTS[0], instant, price * math.exp(index * 0.0005)))
        output[ALTS[1]].append(DiscoveryBar(ALTS[1], instant, price * math.exp(-index * 0.0003)))
    return output


def test_fixed_batch_has_twelve_distinct_bounded_configurations() -> None:
    values = configs()
    assert len(values) == len({item.name for item in values}) == 12
    assert sum(item.family == "residual" for item in values) == 8
    assert sum(item.family == "oi_deleveraging" for item in values) == 4
    with pytest.raises(IntegrityError, match="exact integer"):
        DiscoveryConfig("bad", "oi_deleveraging", True, 4, -1, 0.005)


def test_residual_targets_are_prefix_invariant_and_strictly_delayed() -> None:
    bars = residual_bars()
    config = configs()[0]
    first, _ = generate_episodes(config, bars, START, START + 72 * HOUR, (START + 36 * HOUR,))
    changed = {
        symbol: [
            replace(bar, close=bar.close * 3) if bar.close_ms > START + 24 * HOUR else bar
            for bar in series
        ]
        for symbol, series in bars.items()
    }
    second, _ = generate_episodes(config, changed, START, START + 72 * HOUR, (START + 36 * HOUR,))
    assert first
    assert [item for item in first if item.decision_ms <= START + 24 * HOUR] == [
        item for item in second if item.decision_ms <= START + 24 * HOUR
    ]
    for episode in first:
        assert episode.entry_ms == episode.decision_ms + HOUR
        assert episode.exit_ms == episode.entry_ms + config.hold_hours * HOUR
        assert not (episode.decision_ms < START + 36 * HOUR < episode.exit_ms)
        assert math.fsum(abs(weight) for weight in episode.weights.values()) <= 0.5
        assert BTC in episode.weights and len(episode.weights) == 2
    assert all(b.entry_ms - a.exit_ms >= HOUR for a, b in pairwise(first))


def test_flat_benchmark_prices_cannot_fabricate_beta_or_neutral_signal() -> None:
    episodes, counts = generate_episodes(configs()[0], residual_bars(True), START, END)
    assert not episodes and counts["unavailable"] > 0


def test_known_beta_hedge_and_exact_tie_break_are_independent_of_input_order() -> None:
    bars: dict[str, list[DiscoveryBar]] = {symbol: [] for symbol in ("SOLUSDT", BTC, "ETHUSDT")}
    btc, alt = 100.0, 100.0
    for index in range(-180, 25):
        change = 0.001 if index % 2 else -0.002
        btc *= 1 + change
        alt *= 1 + 2 * change + 0.001
        instant = START + index * HOUR
        bars[BTC].append(DiscoveryBar(BTC, instant, btc))
        for symbol in ALTS:
            bars[symbol].append(DiscoveryBar(symbol, instant, alt))
    episodes, _ = generate_episodes(configs()[0], bars, START, END)
    assert episodes
    for episode in episodes:
        assert set(episode.weights) == {BTC, "ETHUSDT"}
        assert episode.weights["ETHUSDT"] == pytest.approx(1 / 6, abs=1e-10)
        assert episode.weights[BTC] == pytest.approx(-1 / 3, abs=1e-10)
        assert episode.weights[BTC] + 2 * episode.weights["ETHUSDT"] == pytest.approx(0, abs=1e-10)


def test_opposite_residual_directions_use_identical_opportunities() -> None:
    continuation, _ = generate_episodes(configs()[0], residual_bars(), START, END)
    reversion, _ = generate_episodes(configs()[4], residual_bars(), START, END)
    assert len(continuation) == len(reversion) > 0
    assert all(
        a.decision_ms == b.decision_ms and a.weights == {k: -v for k, v in b.weights.items()}
        for a, b in zip(continuation, reversion, strict=True)
    )


@pytest.mark.parametrize("which", ["instrument", "oi-age", "missing-oi-time", "duplicate", "gap"])
def test_bad_input_cannot_be_silently_treated_as_a_neutral_feature(which: str) -> None:
    _, _, bars = oi_case()
    if which == "instrument":
        bars[BTC][0] = replace(bars[BTC][0], symbol="ICPUSDT")
    elif which == "oi-age":
        bars[BTC][0] = replace(bars[BTC][0], oi_timestamp_ms=START)
    elif which == "missing-oi-time":
        bars[BTC][0] = replace(bars[BTC][0], oi_timestamp_ms=None)
    elif which == "duplicate":
        bars[BTC].insert(1, bars[BTC][0])
    else:
        bars[BTC].pop(1)
    with pytest.raises(IntegrityError):
        generate_episodes(configs()[8], bars, START, END)


def test_unknown_oi_is_unavailable_and_not_forward_filled() -> None:
    _, _, bars = oi_case()
    bars = {BTC: [replace(bar, open_interest=None, oi_timestamp_ms=None) for bar in bars[BTC]]}
    episodes, counts = generate_episodes(configs()[8], bars, START, END)
    assert not episodes and counts["unavailable"] > 0


def test_episode_targets_refuse_same_bar_entry_or_excess_gross() -> None:
    with pytest.raises(IntegrityError, match="delayed"):
        targets((Episode(START, START, START + HOUR, {BTC: 0.5}),))
    with pytest.raises(IntegrityError, match="exposure"):
        targets((Episode(START, START + HOUR, START + 5 * HOUR, {BTC: 0.5001}),))


def test_positive_exploration_is_reportable_with_low_sample_limitation() -> None:
    spec, frames, bars = oi_case()
    result = evaluate_config(configs()[8], spec, frames, bars, SPLIT)
    assert result["verdict"] == "exploratory_candidate"
    assert result["base"]["net_return"] > 0
    assert result["stress"]["net_return"] < result["base"]["net_return"]
    for case in (result["base"], result["stress"]):
        for segment in case["segments"].values():
            assert segment["net_return"] > 0 and segment["low_sample"]
        assert (
            next(point for point in case["ledger"] if point["timestamp_ms"] == SPLIT)["positions"]
            == {}
        )
        assert case["ledger"][-1]["positions"] == {}
        assert sum(case["asset_net_pnl"].values()) == pytest.approx(case["final_equity"] - 10_000)


@pytest.mark.parametrize("profit", [-0.02, 0.0005])
def test_losing_and_cost_killed_patterns_are_not_called_candidates(profit: float) -> None:
    spec, frames, bars = oi_case(profit)
    result = evaluate_config(configs()[8], spec, frames, bars, SPLIT)
    assert result["verdict"] == "reject"
    assert result["base"]["net_return"] < 0
    if profit > 0:
        assert result["base"]["same_quantity_gross_price_return"] > 0


def test_native_funding_instant_and_settlement_mark_reach_accounting() -> None:
    spec, frames, bars = oi_case()
    instant = START + 2 * HOUR + 15
    frames.append(Frame(instant, {BTC: 110.0}, {BTC: 0.01}, {}))
    frames.sort(key=lambda item: item.timestamp_ms)
    spec = replace(spec, event_times_ms=tuple(frame.timestamp_ms for frame in frames))
    result = evaluate_config(configs()[8], spec, frames, bars, SPLIT)
    first_fill = result["base"]["trades"][0]
    assert result["base"]["total_funding"] == pytest.approx(
        -first_fill["quantity_delta"] * 110 * 0.01
    )
    assert any(point["timestamp_ms"] == instant for point in result["base"]["ledger"])


def test_batch_retains_failed_config_beside_successful_result() -> None:
    spec, frames, bars = oi_case()
    results = run_batch(spec, frames, bars, SPLIT, (configs()[0], configs()[8]))
    assert len(results) == 2
    assert results[0]["verdict"] == "failed"
    assert results[1]["verdict"] == "exploratory_candidate"
    with pytest.raises(IntegrityError, match="budget"):
        run_batch(spec, frames, bars, SPLIT, (configs()[8], configs()[8]))


def test_benchmarks_are_fixed_and_buy_hold_is_honestly_exposed_at_split() -> None:
    spec, frames, _ = oi_case()
    result: dict[str, Any] = benchmarks(spec, frames, SPLIT)
    assert result["cash"]["base"]["net_return"] == 0
    btc = result["half_btc_buy_hold"]
    assert btc["may_remain_exposed_across_split"] is True
    assert next(p for p in btc["base"]["ledger"] if p["timestamp_ms"] == SPLIT)["positions"]
    assert btc["base"]["trades"][0]["timestamp_ms"] == START + HOUR
    assert btc["base"]["ledger"][-1]["positions"] == {}


@pytest.mark.parametrize("exit_hour", [5, 6])
@pytest.mark.parametrize("exit_mark", [80.0, 120.0])
@pytest.mark.parametrize("funding", [0.0, 0.01])
def test_segment_drawdown_includes_prefill_mark_after_funding_without_boundary_leakage(
    exit_hour: int, exit_mark: float, funding: float
) -> None:
    end = START + 13 * HOUR
    split = START + 6 * HOUR
    exit_ms = START + exit_hour * HOUR
    spec = ReplaySpec(START, end, HOUR, 10_000, (Instrument(BTC, "perp", 0, 0),))
    frames = [
        Frame(
            START + hour * HOUR,
            {BTC: exit_mark if hour == exit_hour else 100.0},
            {BTC: funding if hour == exit_hour else 0.0},
            {BTC: 100.0},
        )
        for hour in range(14)
    ]
    episodes = (Episode(START, START + HOUR, exit_ms, {BTC: 0.5}),)
    ledger = simulate(spec, frames, targets(episodes))
    summary = _summary(ledger, frames, episodes, split)
    funding_payment = -50 * exit_mark * funding
    prefill_equity = 5000 + 50 * exit_mark + funding_payment
    final_equity = 10_000 + funding_payment
    expected = 1 - min(prefill_equity, final_equity) / max(10_000, prefill_equity)
    assert summary["total_funding"] == pytest.approx(funding_payment)
    assert summary["segments"]["development"]["marked_max_drawdown"] == pytest.approx(expected)
    assert ledger.max_drawdown == pytest.approx(expected)
    assert summary["segments"]["diagnostic"]["marked_max_drawdown"] == 0
    assert summary["segments"]["diagnostic"]["net_return"] == 0


def test_expectancy_and_friction_use_actual_episode_quantities_costs_and_funding() -> None:
    end = START + 13 * HOUR
    split = START + 6 * HOUR
    spec = ReplaySpec(START, end, HOUR, 10_000, (Instrument(BTC, "perp", 5, 2),))
    frames = [
        Frame(
            START + hour * HOUR,
            {BTC: 100.0},
            {BTC: 0.01 if hour == 3 else 0.0},
            {BTC: 100.0},
        )
        for hour in range(14)
    ]
    episodes = (Episode(START, START + HOUR, START + 5 * HOUR, {BTC: 0.5}),)
    summary = _summary(simulate(spec, frames, targets(episodes)), frames, episodes, split)
    assert summary["total_cost"] == pytest.approx(7)
    assert summary["total_funding"] == pytest.approx(-50)
    assert summary["turnover_notional"] == pytest.approx(10_000)
    assert summary["net_pnl_per_completed_episode"] == pytest.approx(-57)
    assert summary["same_quantity_gross_price_pnl_per_completed_episode"] == pytest.approx(0)
    assert summary["same_quantity_break_even_friction_bps"] == pytest.approx(-50)
    cash = _summary(simulate(spec, frames, ()), frames, (), split)
    assert cash["net_pnl_per_completed_episode"] is None
    assert cash["same_quantity_gross_price_pnl_per_completed_episode"] is None
    assert cash["same_quantity_break_even_friction_bps"] is None
