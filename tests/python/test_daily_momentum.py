"""Synthetic causality, margin and risk counterexamples; no market archives."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from research.daily_momentum import (
    BENCHMARK_NAMES,
    BTC,
    DAY,
    GROSS,
    HOUR,
    MINUTE,
    DailyConfig,
    benchmarks,
    build_schedule,
    configs,
    evaluate,
)
from research.harness.errors import IntegrityError
from research.repaired_accounting import Frame, Instrument, ReplaySpec

START = 4 * DAY


def case(
    *,
    days: int = 1,
    direction: int = 1,
    prices: dict[int, float] | None = None,
    rates: dict[int, float] | None = None,
    extreme: dict[int, tuple[float, float]] | None = None,
    fee: float = 4.5,
    slip: float = 2.0,
) -> tuple[ReplaySpec, list[Frame], dict[int, float], dict[int, tuple[float, float]]]:
    prices, rates, extreme = prices or {}, rates or {}, extreme or {}
    frames = []
    extremes = {}
    for index in range(days * 1440 + 1):
        price = prices.get(index, 100.0)
        instant = START + index * MINUTE
        frames.append(Frame(instant, {BTC: price}, {BTC: rates.get(index, 0.0)}, {BTC: price}))
        extremes[instant] = extreme.get(index, (price, price))
    hourly = {
        START + index * HOUR: 100 * math.exp(direction * 0.001 * index)
        for index in range(-72, days * 24 + 1)
    }
    spec = ReplaySpec(
        START, START + days * DAY, MINUTE, 10000, (Instrument(BTC, "perp", fee, slip),)
    )
    return spec, frames, hourly, extremes


def policy(*, filtered: bool = False, bracket: bool = False) -> DailyConfig:
    return DailyConfig("fixture", 24, filtered, bracket)


def test_eight_fixed_configs_and_strict_flags() -> None:
    chosen = configs()
    assert len(chosen) == len({item.name for item in chosen}) == 8
    assert sum(item.filtered for item in chosen) == sum(item.bracket for item in chosen) == 4
    with pytest.raises(IntegrityError, match="lookback"):
        DailyConfig("bad", True, False, False)
    with pytest.raises(IntegrityError, match="flags"):
        DailyConfig("bad", 24, 1, False)  # type: ignore[arg-type]


@pytest.mark.parametrize("side", [-1, 1])
def test_both_momentum_directions_are_delayed_one_trade_per_day(side: int) -> None:
    spec, frames, hourly, _ = case(days=2, direction=side)
    targets, episodes, counts = build_schedule(policy(), spec, frames, hourly)
    assert counts["traded_days"] == 2
    assert len(episodes) == 2
    assert episodes[0].entry_ms == START + MINUTE
    assert episodes[0].exit_ms == START + DAY
    assert episodes[1].entry_ms == START + DAY + MINUTE
    assert episodes[1].exit_ms == spec.end_ms
    assert [target.weights for target in targets] == [{BTC: side * GROSS}, {}, {BTC: side * GROSS}]
    assert all(target.execution_ms > target.decision_ms for target in targets)


def test_daily_direction_is_prefix_invariant_and_cannot_read_future_hours() -> None:
    spec, frames, hourly, _ = case(days=2)
    first = build_schedule(policy(), spec, frames, hourly)[1]
    changed = {t: price * 1e6 if t > START else price for t, price in hourly.items()}
    second = build_schedule(policy(), spec, frames, changed)[1]
    assert first[0] == second[0]


def test_filter_and_unavailable_history_are_counted_without_invented_trades() -> None:
    spec, frames, hourly, _ = case()
    # Alternating returns nearly cancel rather than producing a persistent trend.
    for index in range(-24, 1):
        hourly[START + index * HOUR] = 100 * math.exp(0.001 * (index % 2))
    hourly[START] *= 1.00001
    assert build_schedule(policy(), spec, frames, hourly)[2]["traded_days"] == 1
    assert build_schedule(policy(filtered=True), spec, frames, hourly)[2]["filtered_days"] == 1
    del hourly[START - HOUR]
    assert build_schedule(policy(), spec, frames, hourly)[2]["unavailable_days"] == 1


def test_zero_variance_history_is_unavailable_not_forced_long() -> None:
    spec, frames, hourly, _ = case()
    targets, episodes, counts = build_schedule(policy(), spec, frames, dict.fromkeys(hourly, 100))
    assert not targets and not episodes and counts["unavailable_days"] == 1


def test_mark_prices_cannot_silently_replace_trade_execution_references() -> None:
    spec, frames, hourly, _ = case()
    frames[1] = replace(frames[1], execution_prices=None)
    with pytest.raises(IntegrityError, match="marks are not fills"):
        build_schedule(policy(), spec, frames, hourly)


def test_flat_prices_pay_both_sides_and_margin_is_actual_entry_fill_not_account() -> None:
    spec, frames, hourly, extremes = case()
    result = evaluate(policy(), spec, frames, hourly, extremes)
    base = result["base"]
    episode = base["episodes"][0]
    assert base["total_cost"] == pytest.approx(6.5)
    assert base["final_equity"] == pytest.approx(9993.5)
    assert episode["initial_margin"] == pytest.approx(500.1)
    assert episode["net_margin_roi"] == pytest.approx(-6.5 / 500.1)
    assert base["same_quantity_gross_price_return"] == pytest.approx(0)
    assert not episode["realized_target_success"]


@pytest.mark.parametrize("funding_index,expected", [(1, 0), (2, -5), (1440, -5)])
def test_funding_at_entry_is_excluded_but_held_and_exit_settlements_are_paid(
    funding_index: int, expected: float
) -> None:
    spec, frames, hourly, extremes = case(rates={funding_index: 0.001}, fee=0, slip=0)
    result = evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert result["total_funding"] == pytest.approx(expected)
    assert result["episodes"][0]["funding"] == pytest.approx(expected)
    assert result["final_equity"] == pytest.approx(10000 + expected)


def test_asynchronous_funding_after_entry_is_not_floored_away() -> None:
    spec, frames, hourly, extremes = case(fee=0, slip=0)
    event = Frame(START + MINUTE + 15, {BTC: 100}, {BTC: 0.001})
    frames.insert(2, event)
    spec = replace(spec, event_times_ms=tuple(frame.timestamp_ms for frame in frames))
    base = evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert base["total_funding"] == pytest.approx(-5)
    assert base["episodes"][0]["net_margin_roi"] == pytest.approx(-0.01)


def test_take_profit_trigger_uses_net_costs_and_fill_is_following_minute_gap() -> None:
    # A +2% price move is below a +20% NET margin target after fees/friction.
    spec, frames, hourly, extremes = case(prices={2: 102, 3: 102.2, 4: 98})
    _, episodes, _ = build_schedule(policy(bracket=True), spec, frames, hourly)
    assert episodes[0].trigger_ms == START + 3 * MINUTE
    assert episodes[0].exit_ms == START + 4 * MINUTE
    assert episodes[0].reason == "take_profit"
    base = evaluate(policy(bracket=True), spec, frames, hourly, extremes)["base"]
    assert base["episodes"][0]["net_margin_roi"] < -0.20
    assert not base["episodes"][0]["realized_target_success"]


def test_stop_is_observed_at_close_and_does_not_fill_at_threshold() -> None:
    spec, frames, hourly, extremes = case(prices={2: 98.9, 3: 97}, fee=0, slip=0)
    result = evaluate(policy(bracket=True), spec, frames, hourly, extremes)
    episode = result["base"]["episodes"][0]
    assert episode["reason"] == "stop"
    assert episode["trigger_ms"] == START + 2 * MINUTE
    assert episode["exit_ms"] == START + 3 * MINUTE
    assert episode["net_margin_roi"] == pytest.approx(-0.3)


def test_bracket_includes_funding_in_trigger_and_between_trigger_and_exit() -> None:
    spec, frames, hourly, extremes = case(rates={2: 0.011, 3: 0.001}, fee=0, slip=0)
    base = evaluate(policy(bracket=True), spec, frames, hourly, extremes)["base"]
    episode = base["episodes"][0]
    assert episode["reason"] == "stop" and episode["trigger_ms"] == START + 2 * MINUTE
    assert episode["exit_ms"] == START + 3 * MINUTE
    assert episode["funding"] == pytest.approx(-60)
    assert episode["net_margin_roi"] == pytest.approx(-0.12)


def test_ohlc_touch_is_not_a_target_fill_but_still_participates_in_risk_screen() -> None:
    spec, frames, hourly, extremes = case(extreme={2: (100, 104)}, fee=0, slip=0)
    _, episodes, _ = build_schedule(policy(bracket=True), spec, frames, hourly)
    assert episodes[0].exit_ms == spec.end_ms and episodes[0].reason == "time"
    assert not evaluate(policy(bracket=True), spec, frames, hourly, extremes)["base"][
        "liquidation_possible"
    ]


def test_stress_keeps_base_schedule_and_independent_carry_forward_capital() -> None:
    spec, frames, hourly, extremes = case(prices={2: 102.2, 3: 103})
    result = evaluate(
        policy(bracket=True), spec, frames, hourly, extremes, stress_initial_equity=20000
    )
    base, stress = result["base"], result["stress"]
    assert [r["timestamp_ms"] for r in base["trades"]] == [
        r["timestamp_ms"] for r in stress["trades"]
    ]
    assert stress["initial_equity"] == 20000
    assert stress["episodes"][0]["quantity"] == 2 * base["episodes"][0]["quantity"]
    assert stress["episodes"][0]["net_margin_roi"] < base["episodes"][0]["net_margin_roi"]


@pytest.mark.parametrize("side,low,high", [(1, 90, 100), (-1, 100, 110)])
def test_possible_isolated_liquidation_remains_visible_and_invalidates_admission(
    side: int, low: float, high: float
) -> None:
    spec, frames, hourly, extremes = case(direction=side, extreme={2: (low, high)}, fee=0, slip=0)
    result = evaluate(policy(), spec, frames, hourly, extremes)
    assert result["disposition"] == "counterfactual_risk_invalid"
    assert result["base"]["liquidation_possible"]
    assert result["base"]["complete_episodes"] == 1
    assert result["base"]["episodes"][0]["net_pnl"] == 0
    assert result["base"]["risk_events"][0]["timestamp_ms"] == START + 2 * MINUTE


def test_extreme_from_minute_before_entry_does_not_liquidate_unheld_position() -> None:
    spec, frames, hourly, extremes = case(extreme={1: (1, 1000)}, fee=0, slip=0)
    assert not evaluate(policy(), spec, frames, hourly, extremes)["base"]["liquidation_possible"]


def test_missing_held_minute_risk_data_refuses_result() -> None:
    spec, frames, hourly, extremes = case()
    del extremes[START + 2 * MINUTE]
    with pytest.raises(IntegrityError, match="mark extremes"):
        evaluate(policy(), spec, frames, hourly, extremes)


def test_benchmark_directions_cash_and_independent_monthly_equity_pairs() -> None:
    spec, frames, _, extremes = case(prices={1440: 105}, fee=0, slip=0)
    values = benchmarks(
        spec,
        frames,
        extremes,
        initial_equities={
            "cash": (9000, 8000),
            "always_long_time": (11000, 12000),
            "always_short_time": (10000, 13000),
            "always_long_bracket": (14000, 15000),
            "always_short_bracket": (16000, 17000),
        },
    )
    assert values["cash"]["base"]["final_equity"] == 9000
    assert values["always_long_time"]["base"]["net_return"] == pytest.approx(0.025)
    assert values["always_short_time"]["base"]["net_return"] == pytest.approx(-0.025)
    assert values["always_long_time"]["stress"]["initial_equity"] == 12000
    assert values["always_short_time"]["stress"]["initial_equity"] == 13000
    assert all(not item["base"]["ledger"][-1]["positions"] for item in values.values())


def test_source_quarantined_day_is_flat_even_for_forced_policy_and_benchmarks() -> None:
    spec, frames, hourly, extremes = case(days=2)
    missing = 1442
    frames[missing] = replace(frames[missing], marks={})
    del extremes[START + missing * MINUTE]
    eligible = frozenset({START // DAY})
    result = evaluate(policy(), spec, frames, hourly, extremes, eligible_days=eligible)
    counts = result["counts"]
    assert counts["calendar_days"] == 2
    assert counts["eligible_days"] == counts["unavailable_data_days"] == 1
    assert counts["traded_days"] == result["base"]["complete_episodes"] == 1
    assert result["base"]["calendar_days"] == 2
    assert result["base"]["no_trade_days"] == 1
    for point in result["base"]["ledger"]:
        if point["timestamp_ms"] >= START + DAY:
            assert not point["positions"]
    values = benchmarks(spec, frames, extremes, eligible_days=eligible)
    assert values["always_long_time"]["base"]["complete_episodes"] == 1
    assert values["always_short_time"]["base"]["unavailable_data_days"] == 1
    assert values["cash"]["base"]["calendar_days"] == 2


def test_missing_held_mark_is_fatal_without_quarantine() -> None:
    spec, frames, hourly, extremes = case()
    frames[2] = replace(frames[2], marks={})
    with pytest.raises(IntegrityError, match="Missing"):
        evaluate(policy(), spec, frames, hourly, extremes)


def test_split_month_carry_matches_continuous_cash_and_ending_equity() -> None:
    spec, frames, hourly, extremes = case(days=2, prices={1440: 105, 2880: 110})
    continuous = evaluate(policy(), spec, frames, hourly, extremes)
    split = START + DAY
    first_spec = replace(spec, end_ms=split)
    first = evaluate(policy(), first_spec, frames[:1441], hourly, extremes)
    second_spec = replace(spec, start_ms=split, initial_equity=first["base"]["final_equity"])
    second = evaluate(
        policy(),
        second_spec,
        frames[1440:],
        hourly,
        extremes,
        stress_initial_equity=first["stress"]["final_equity"],
    )
    for name in ("base", "stress"):
        assert second[name]["final_equity"] == pytest.approx(continuous[name]["final_equity"])
        assert second[name]["episodes"][0]["quantity"] == pytest.approx(
            continuous[name]["episodes"][1]["quantity"]
        )
        assert second[name]["initial_equity"] == first[name]["final_equity"]
    assert first["base"]["ledger"][-1]["positions"] == {}


def test_benchmarks_also_refuse_mark_only_execution_references() -> None:
    spec, frames, _, extremes = case()
    frames[1] = replace(frames[1], execution_prices=None)
    with pytest.raises(IntegrityError, match="marks are not fills"):
        benchmarks(spec, frames, extremes)


def test_short_bracket_uses_opposite_adverse_fills_and_gap_can_miss_target() -> None:
    spec, frames, hourly, extremes = case(direction=-1, prices={2: 97.8, 3: 98})
    base = evaluate(policy(bracket=True), spec, frames, hourly, extremes)["base"]
    episode = base["episodes"][0]
    assert episode["side"] == -1 and episode["reason"] == "take_profit"
    assert episode["trigger_ms"] == START + 2 * MINUTE
    assert episode["exit_ms"] == START + 3 * MINUTE
    assert 0 < episode["net_margin_roi"] < 0.20
    assert base["trades"][0]["fill_price"] == pytest.approx(99.98)
    assert base["trades"][1]["fill_price"] == pytest.approx(98.0196)


def test_closed_bracket_schedule_cannot_read_later_future_prices() -> None:
    spec, frames, hourly, _ = case(prices={2: 103, 3: 103})
    original = build_schedule(policy(bracket=True), spec, frames, hourly)
    changed = [
        replace(frame, execution_prices={BTC: 10000})
        if frame.timestamp_ms >= START + 4 * MINUTE
        else frame
        for frame in frames
    ]
    assert build_schedule(policy(bracket=True), spec, changed, hourly) == original


def test_later_funding_credit_cannot_rescue_unknown_earlier_intrabar_liquidation() -> None:
    spec, frames, hourly, extremes = case(rates={2: -0.10}, extreme={2: (90, 100)}, fee=0, slip=0)
    result = evaluate(policy(), spec, frames, hourly, extremes)
    assert result["base"]["total_funding"] == pytest.approx(500)
    assert result["base"]["net_return"] > 0
    assert result["base"]["risk_events"][0]["kind"] == "intraminute_possible_liquidation"
    assert result["disposition"] == "counterfactual_risk_invalid"


@pytest.mark.parametrize("side,entry_mark", [(1, 80.0), (-1, 120.0)])
@pytest.mark.parametrize("entry_rate", [0.0, -0.5, 0.5])
def test_exact_entry_mark_can_liquidate_before_it_heals_next_minute(
    side: int, entry_mark: float, entry_rate: float
) -> None:
    spec, frames, hourly, extremes = case(direction=side, rates={1: entry_rate}, fee=0, slip=0)
    frames[1] = replace(frames[1], marks={BTC: entry_mark})
    # The trade reference remains100 and all later marks immediately heal.
    result = evaluate(policy(), spec, frames, hourly, extremes)
    assert result["disposition"] == "counterfactual_risk_invalid"
    base = result["base"]
    assert base["total_funding"] == 0
    assert base["episodes"][0]["net_pnl"] == 0
    assert base["risk_events"] == [
        {
            "timestamp_ms": START + MINUTE,
            "kind": "entry_possible_liquidation",
            "collateral": pytest.approx(-500),
            "maintenance": pytest.approx(0.0125 * 50 * entry_mark),
        }
    ]


@pytest.mark.parametrize("entry_rate", [-0.5, 0.5])
def test_preentry_same_timestamp_funding_cannot_debit_or_credit_new_margin(
    entry_rate: float,
) -> None:
    spec, frames, hourly, extremes = case(rates={1: entry_rate}, fee=0, slip=0)
    base = evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert base["total_funding"] == base["episodes"][0]["funding"] == 0
    assert not base["liquidation_possible"] and not base["risk_events"]


@pytest.mark.parametrize("side,second,third", [(1, 103, 102), (-1, 97, 98)])
def test_fixed_direction_benchmarks_share_delayed_bracket_risk_policy(
    side: int,
    second: float,
    third: float,
) -> None:
    spec, frames, _, extremes = case(prices={2: second, 3: third}, fee=0, slip=0)
    values = benchmarks(spec, frames, extremes)
    assert set(values) == set(BENCHMARK_NAMES)
    name = "always_long_bracket" if side == 1 else "always_short_bracket"
    episode = values[name]["base"]["episodes"][0]
    assert episode["reason"] == "take_profit"
    assert episode["trigger_ms"] == START + 2 * MINUTE
    assert episode["exit_ms"] == START + 3 * MINUTE
    assert episode["net_margin_roi"] == pytest.approx(0.20)
    assert values["cash"]["base"]["complete_episodes"] == 0
    opposite = "always_short_bracket" if side == 1 else "always_long_bracket"
    assert values[opposite]["base"]["episodes"][0]["reason"] == "stop"
    time_name = "always_long_time" if side == 1 else "always_short_time"
    assert values[time_name]["base"]["episodes"][0]["exit_ms"] == spec.end_ms
    assert values[time_name]["base"]["episodes"][0]["reason"] == "time"
    assert values[time_name]["base"]["episodes"][0]["net_margin_roi"] == 0
    assert [trade["timestamp_ms"] for trade in values[name]["base"]["trades"]] == [
        trade["timestamp_ms"] for trade in values[name]["stress"]["trades"]
    ]
