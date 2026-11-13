"""Synthetic regime causality, carry, exact funding and minute-risk examples."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

import pytest

from research import regime_momentum as model
from research.harness.errors import IntegrityError
from research.regime_momentum import BTC, DAY, HOUR, MINUTE
from research.repaired_accounting import Frame, Instrument, ReplaySpec, Target

START = 40 * DAY


def case(
    *,
    days: int = 2,
    prices: dict[int, float] | None = None,
    marks: dict[int, float] | None = None,
    funding: dict[int, float] | None = None,
    extremes: dict[int, tuple[float, float]] | None = None,
    direction: int = 1,
    fee: float = 4.5,
    slip: float = 2,
) -> tuple[ReplaySpec, list[Frame], dict[int, float], dict[int, tuple[float, float]]]:
    prices, marks, funding, extremes = prices or {}, marks or {}, funding or {}, extremes or {}
    end = START + days * DAY
    times = set(range(START, end + 1, HOUR)) | {START + offset for offset in funding}
    for day in range(START, end, DAY):
        times |= {day + MINUTE, day + 2 * MINUTE}
    frames = [
        Frame(
            instant,
            {BTC: marks.get((instant - START) // MINUTE, 100.0)},
            {BTC: funding.get(instant - START, 0.0)},
            {BTC: prices.get((instant - START) // MINUTE, 100.0)},
        )
        for instant in sorted(times)
    ]
    hourly = {
        START + index * HOUR: 100 * math.exp(direction * 0.001 * index)
        for index in range(-720, days * 24 + 1)
    }
    minute_extremes = {
        instant: extremes.get((instant - START) // MINUTE, (100.0, 100.0))
        for instant in range(START + MINUTE, end + 1, MINUTE)
    }
    spec = ReplaySpec(
        START,
        end,
        HOUR,
        10_000,
        (Instrument(BTC, "perp", fee, slip),),
        tuple(frame.timestamp_ms for frame in frames),
    )
    return spec, frames, hourly, minute_extremes


def policy(*, hours: int = 168, confirmed: bool = False) -> model.RegimeConfig:
    return model.RegimeConfig("fixture", hours, confirmed)


def feature_path(
    monkeypatch: pytest.MonkeyPatch,
    values: list[tuple[float | None, float | None, int | None]],
) -> None:
    def fixed(
        config: model.RegimeConfig, hourly: Mapping[int, float], instant: int
    ) -> tuple[float | None, float | None, int | None]:
        del config, hourly
        return values[(instant - START) // DAY]

    monkeypatch.setattr(model, "features", fixed)


def test_four_fixed_configs_have_exact_types() -> None:
    configs = model.configs()
    assert len(configs) == len({config.name for config in configs}) == 4
    assert {config.lookback_hours for config in configs} == {168, 720}
    assert sum(config.confirmed for config in configs) == 2
    with pytest.raises(IntegrityError, match="Lookback"):
        model.RegimeConfig("bad", True, False)
    with pytest.raises(IntegrityError, match="flag"):
        model.RegimeConfig("bad", 168, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("side", [-1, 1])
def test_persistent_signal_holds_fixed_units_without_daily_reentry(side: int) -> None:
    spec, frames, hourly, extremes = case(days=4, direction=side)
    schedule = model.build_schedule(policy(), spec, frames, hourly)
    assert len(schedule.targets) == 1
    assert schedule.targets[0].execution_ms == START + MINUTE
    assert schedule.targets[0].weights == {BTC: side * 0.5}
    assert [decision.action for decision in schedule.decisions] == ["enter", "hold", "hold", "hold"]
    result = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert result["complete_trades"] == 1
    assert len(result["trades"]) == 2
    assert result["episodes"][0]["quantity"] == side * 50
    assert result["episodes"][0]["exit_ms"] == spec.end_ms
    assert result["final_equity"] == pytest.approx(9993.5)


def test_features_and_first_decision_cannot_read_future_hours() -> None:
    spec, frames, hourly, _ = case()
    changed = {t: value * 1e6 if t > START else value for t, value in hourly.items()}
    assert model.features(policy(), hourly, START) == model.features(policy(), changed, START)
    assert (
        model.build_schedule(policy(), spec, frames, hourly).decisions[0]
        == (model.build_schedule(policy(), spec, frames, changed).decisions[0])
    )


def test_long_lookback_and_volatility_need_complete_721_hour_warmup() -> None:
    spec, frames, hourly, _ = case(days=1)
    assert model.build_schedule(policy(hours=720), spec, frames, hourly).targets
    del hourly[START - 719 * HOUR]
    assert not model.build_schedule(policy(hours=720), spec, frames, hourly).targets
    assert not model.build_schedule(policy(hours=168), spec, frames, hourly).targets


def test_flat_or_invalid_history_never_fabricates_direction() -> None:
    spec, frames, hourly, _ = case(days=1)
    assert not model.build_schedule(policy(), spec, frames, dict.fromkeys(hourly, 100.0)).targets
    hourly[START] = float("nan")
    with pytest.raises(IntegrityError, match="hourly closes"):
        model.build_schedule(policy(), spec, frames, hourly)


def test_hysteresis_flat_entry_hold_and_close_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    spec, frames, hourly, _ = case(days=5)
    feature_path(
        monkeypatch, [(0.9, 0.2, 1), (1, 0.2, 1), (0.5, 0.5, 1), (0.49, 0.2, 1), (0.8, 0.2, 1)]
    )
    schedule = model.build_schedule(policy(), spec, frames, hourly)
    assert [decision.action for decision in schedule.decisions] == [
        "flat",
        "enter",
        "hold",
        "close",
        "flat",
    ]
    assert [target.execution_ms for target in schedule.targets] == [
        START + DAY + MINUTE,
        START + 3 * DAY + MINUTE,
    ]
    assert schedule.decisions[2].entry_weight is None


def test_reversal_closes_then_opens_one_minute_later_with_all_costs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, frames, hourly, extremes = case(days=2)
    feature_path(monkeypatch, [(2, 0.2, 1), (-2, 0.2, -1)])
    schedule = model.build_schedule(policy(), spec, frames, hourly)
    assert [target.execution_ms for target in schedule.targets] == [
        START + MINUTE,
        START + DAY + MINUTE,
        START + DAY + 2 * MINUTE,
    ]
    assert schedule.targets[1].weights == {}
    assert schedule.targets[2].weights == {BTC: -0.5}
    result = model.evaluate_case(spec, frames, schedule.targets, extremes)
    assert len(result["trades"]) == 4
    assert result["complete_trades"] == 2
    assert result["final_equity"] < 9987.1
    assert result["episodes"][0]["side"] == 1
    assert result["episodes"][1]["side"] == -1


def test_opposite_weak_signal_closes_without_opening_a_weak_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, frames, hourly, _ = case(days=2)
    feature_path(monkeypatch, [(2, 0.2, 1), (-0.6, 0.2, -1)])
    schedule = model.build_schedule(policy(), spec, frames, hourly)
    assert len(schedule.targets) == 2
    assert schedule.targets[-1].weights == {}
    assert schedule.decisions[-1].action == "close"


def test_confirmation_blocks_entry_and_closes_on_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    spec, frames, hourly, _ = case(days=3)
    feature_path(monkeypatch, [(2, 0.2, -1), (2, 0.2, 1), (2, 0.2, 0)])
    schedule = model.build_schedule(policy(confirmed=True), spec, frames, hourly)
    assert [decision.action for decision in schedule.decisions] == ["flat", "enter", "close"]
    assert schedule.targets[0].execution_ms == START + DAY + MINUTE


def test_missing_features_close_a_held_position_instead_of_hiding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, frames, hourly, _ = case(days=2)
    feature_path(monkeypatch, [(2, 0.2, 1), (None, None, None)])
    schedule = model.build_schedule(policy(), spec, frames, hourly)
    assert len(schedule.targets) == 2
    assert schedule.decisions[-1].action == "close"


def test_risk_sizing_is_entry_only_not_a_daily_volatility_rebalance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, frames, hourly, _ = case(days=3)
    feature_path(monkeypatch, [(2, 0.5, 1), (2, 2, 1), (2, 0.1, 1)])
    schedule = model.build_schedule(policy(), spec, frames, hourly)
    assert len(schedule.targets) == 1
    assert schedule.targets[0].weights == {BTC: pytest.approx(0.2)}


def test_entry_and_reverse_require_actual_trade_references() -> None:
    spec, frames, hourly, _ = case(days=1)
    frames[1] = replace(frames[1], execution_prices=None)
    with pytest.raises(IntegrityError, match="marks are not fills"):
        model.build_schedule(policy(), spec, frames, hourly)


def test_initial_margin_uses_entry_mark_not_fill_notional() -> None:
    spec, frames, hourly, extremes = case(days=1, marks={1: 120})
    result = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    episode = result["episodes"][0]
    assert episode["initial_margin"] == 3000
    assert episode["entry_fill_price"] == pytest.approx(100.02)
    assert episode["net_margin_roi"] == pytest.approx(-6.5 / 3000)
    assert not episode["target_met"]


@pytest.mark.parametrize("side,low,high", [(1, 45, 100), (-1, 100, 160)])
def test_every_held_minute_is_screened_even_between_sparse_account_frames(
    side: int, low: float, high: float
) -> None:
    spec, frames, hourly, extremes = case(days=1, direction=side, extremes={37: (low, high)})
    assert START + 37 * MINUTE not in {frame.timestamp_ms for frame in frames}
    result = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert result["risk_invalid"]
    assert any(
        event["timestamp_ms"] == START + 37 * MINUTE
        and event["kind"] == "intraminute_possible_liquidation"
        for event in result["risk_events"]
    )
    assert result["final_equity"] == pytest.approx(9993.5)


def test_entry_collateral_is_checked_before_a_later_mark_can_heal_it() -> None:
    spec, frames, hourly, extremes = case(days=1, marks={1: 20})
    result = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert result["risk_invalid"]
    assert result["risk_events"][0]["kind"] == "entry_possible_liquidation"
    assert result["risk_events"][0]["timestamp_ms"] == START + MINUTE


def test_missing_held_minute_extreme_is_an_error_not_imputed_or_skipped() -> None:
    spec, frames, hourly, extremes = case(days=1)
    del extremes[START + 37 * MINUTE]
    with pytest.raises(IntegrityError, match="held-minute"):
        model.evaluate(policy(), spec, frames, hourly, extremes)


@pytest.mark.parametrize("offset,expected", [(MINUTE, 0), (MINUTE + 15, -5), (DAY, -5)])
def test_funding_ownership_keeps_exact_entry_and_exit_milliseconds(
    offset: int, expected: float
) -> None:
    spec, frames, hourly, extremes = case(days=1, funding={offset: 0.001}, fee=0, slip=0)
    result = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert result["total_funding"] == pytest.approx(expected)
    assert result["episodes"][0]["funding"] == pytest.approx(expected)
    assert result["final_equity"] == pytest.approx(10000 + expected)


def test_exact_funding_mark_has_its_own_collateral_screen() -> None:
    spec, frames, hourly, extremes = case(days=1, funding={MINUTE + 15: 0.001})
    point = next(
        index for index, frame in enumerate(frames) if frame.timestamp_ms == START + MINUTE + 15
    )
    frames[point] = replace(frames[point], marks={BTC: 20})
    result = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert result["risk_invalid"]
    assert any(event["timestamp_ms"] == START + MINUTE + 15 for event in result["risk_events"])
    assert result["total_funding"] == pytest.approx(-1)


def test_stress_uses_same_schedule_but_own_starting_capital_and_double_costs() -> None:
    spec, frames, hourly, extremes = case(days=1)
    output = model.evaluate(policy(), spec, frames, hourly, extremes, stress_initial_equity=8000)
    base, stress = output["base"], output["stress"]
    assert base["episodes"][0]["entry_ms"] == stress["episodes"][0]["entry_ms"]
    assert base["episodes"][0]["quantity"] == 50
    assert stress["episodes"][0]["quantity"] == 40
    assert base["fee_cost"] == pytest.approx(4.5)
    assert stress["fee_cost"] == pytest.approx(7.2)
    assert stress["final_equity"] == pytest.approx(7989.6)


def test_persistent_controls_do_not_repeat_the_old_daily_round_trip() -> None:
    spec, frames, hourly, extremes = case(days=4)
    for side, name in [(1, "persistent_long"), (-1, "persistent_short")]:
        targets = model.control_schedule(name, spec, frames, hourly)
        assert len(targets) == 1
        assert targets[0].weights == {BTC: side * 0.5}
        assert model.evaluate_case(spec, frames, targets, extremes)["complete_trades"] == 1
    assert model.control_schedule("cash", spec, frames, hourly) == ()


def test_matched_controls_preserve_regime_clock_flat_states_and_risk_weights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, frames, hourly, extremes = case(days=4)
    feature_path(monkeypatch, [(2, 0.5, 1), (-2, 0.2, -1), (0.1, 0.2, 1), (2, 0.4, 1)])
    schedule = model.build_schedule(policy(), spec, frames, hourly)
    for side, name in [(1, "matched_long"), (-1, "matched_short")]:
        targets = model.control_schedule(name, spec, frames, hourly, matched=schedule)
        assert [target.execution_ms for target in targets] == [
            target.execution_ms for target in schedule.targets
        ]
        assert [abs(target.weights.get(BTC, 0)) for target in targets] == [
            abs(target.weights.get(BTC, 0)) for target in schedule.targets
        ]
        result = model.evaluate_case(spec, frames, targets, extremes)
        assert result["complete_trades"] == 3
        assert all(episode["side"] == side for episode in result["episodes"])


def test_controls_fail_closed_for_unknown_name_or_missing_matched_schedule() -> None:
    spec, frames, hourly, _ = case(days=1)
    with pytest.raises(IntegrityError, match="control name"):
        model.control_schedule("free_money", spec, frames, hourly)
    with pytest.raises(IntegrityError, match="original regime"):
        model.control_schedule("matched_long", spec, frames, hourly)


def test_episode_gross_cost_funding_and_attribution_reconcile() -> None:
    spec, frames, hourly, extremes = case(days=1, prices={1440: 110}, funding={4 * HOUR: 0.001})
    base = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    episode = base["episodes"][0]
    assert base["same_quantity_gross_pnl"] == pytest.approx(500)
    assert base["final_equity"] - 10000 == pytest.approx(
        500 - base["fee_cost"] - base["slippage_cost"] + base["total_funding"]
    )
    assert base["long_net_pnl"] == pytest.approx(episode["net_pnl"])
    assert base["short_net_pnl"] == 0
    assert episode["net_margin_roi"] == pytest.approx(episode["net_pnl"] / 2500)


def test_exact_twenty_percent_target_uses_net_actual_margin_not_price_change() -> None:
    spec, frames, hourly, extremes = case(days=1, prices={1440: 110}, fee=0, slip=0)
    base = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert base["target_attainment_count"] == 1
    assert base["episodes"][0]["net_margin_roi"] == pytest.approx(0.20)
    charged = model.evaluate(
        policy(),
        replace(spec, instruments=(Instrument(BTC, "perp", 4.5, 2),)),
        frames,
        hourly,
        extremes,
    )["base"]
    assert charged["target_attainment_count"] == 0


def test_unknown_funding_and_partial_resize_schedules_fail_closed() -> None:
    spec, frames, hourly, extremes = case(days=1)
    schedule = model.build_schedule(policy(), spec, frames, hourly)
    changed = list(frames)
    changed[3] = replace(changed[3], funding_rates={BTC: None})
    with pytest.raises(IntegrityError, match="funding"):
        model.evaluate_case(spec, changed, schedule.targets, extremes)
    partial = (*schedule.targets, Target(START + DAY // 2, START + DAY // 2, {BTC: 0.1}))
    with pytest.raises(IntegrityError, match=r"pairs|fixed units"):
        model.evaluate_case(spec, frames, partial, extremes)


def test_insufficient_entry_margin_cannot_be_paid_with_immediate_mark_profit() -> None:
    spec, frames, hourly, extremes = case(days=1, marks={1: 1000})
    result = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    episode = result["episodes"][0]
    assert episode["initial_margin"] == 25000
    assert episode["entry_equity_before_execution"] == pytest.approx(10000)
    assert result["risk_invalid"]
    assert any(event["kind"] == "entry_margin_unavailable" for event in result["risk_events"])


def test_sparse_valuation_reports_exposure_drift_without_claiming_minute_maximum() -> None:
    spec, frames, hourly, extremes = case(days=1, marks={60: 120})
    result = model.evaluate(policy(), spec, frames, hourly, extremes)["base"]
    assert result["maximum_sampled_gross_exposure"] > 0.5


def test_terminal_close_cannot_fall_back_to_a_mark_price() -> None:
    spec, frames, hourly, extremes = case(days=1)
    schedule = model.build_schedule(policy(), spec, frames, hourly)
    frames[-1] = replace(frames[-1], execution_prices=None)
    with pytest.raises(IntegrityError, match="marks are not fills"):
        model.evaluate_case(spec, frames, schedule.targets, extremes)


def test_control_execution_cannot_bypass_native_price_validation() -> None:
    spec, frames, hourly, extremes = case(days=1)
    schedule = model.control_schedule("persistent_long", spec, frames, hourly)
    frames[1] = replace(frames[1], execution_prices=None)
    with pytest.raises(IntegrityError, match="marks are not fills"):
        model.evaluate_case(spec, frames, schedule, extremes)
