"""Financial counterexamples for the repaired bounded research ledger."""

from __future__ import annotations

from dataclasses import replace

import pytest

from research.harness.errors import IntegrityError
from research.repaired_accounting import (
    Frame,
    Instrument,
    ReplaySpec,
    Target,
    select_scale,
    simulate,
)

HOUR = 3_600_000
PERP = Instrument("X-PERP", "perp", 0.0, 0.0)


def spec(*instruments: Instrument, hours: int = 2) -> ReplaySpec:
    return ReplaySpec(0, hours * HOUR, HOUR, 100.0, instruments or (PERP,))


def frames(prices: list[float], rates: list[float | None] | None = None) -> list[Frame]:
    rates = rates if rates is not None else [0.0] * len(prices)
    return [
        Frame(index * HOUR, {"X-PERP": price}, {"X-PERP": rate})
        for index, (price, rate) in enumerate(zip(prices, rates, strict=True))
    ]


def test_roundtrip_preserves_quantity_and_has_no_phantom_return() -> None:
    result = simulate(spec(), frames([100.0, 110.0, 100.0]), [Target(0, 0, {"X-PERP": 1.0})])
    assert [point.equity for point in result.points] == [100.0, 110.0, 100.0]
    assert [point.positions for point in result.points] == [{"X-PERP": 1.0}] * 2 + [{}]
    assert result.net_return == 0.0
    assert result.final_equity == result.points[-1].cash
    assert result.max_drawdown == pytest.approx(1 - 100 / 110)
    assert [trade.quantity_delta for trade in result.trades] == [1.0, -1.0]


def test_long_and_short_charge_funding_on_the_actual_marked_notional() -> None:
    market = frames([100.0, 120.0, 110.0], [0.0, 0.01, -0.02])
    long = simulate(spec(), market, [Target(0, 0, {"X-PERP": 1.0})])
    short = simulate(spec(), market, [Target(0, 0, {"X-PERP": -1.0})])
    assert long.total_funding == pytest.approx(-1.2 + 2.2)
    assert short.total_funding == pytest.approx(1.2 - 2.2)
    assert long.final_equity == pytest.approx(111.0)
    assert short.final_equity == pytest.approx(89.0)


def test_execution_costs_use_each_fill_notional_and_terminal_close_is_charged() -> None:
    instrument = replace(PERP, fee_bps=10.0, slippage_bps=20.0)
    result = simulate(
        spec(instrument), frames([100.0, 150.0, 200.0]), [Target(0, 0, {"X-PERP": 1.0})]
    )
    entry, exit_trade = result.trades
    assert entry.fill_price == pytest.approx(100.2)
    assert entry.fee == pytest.approx(0.1002)
    assert exit_trade.fill_price == pytest.approx(199.6)
    assert exit_trade.fee == pytest.approx(0.1996)
    assert result.total_cost == pytest.approx(0.2 + 0.4 + 0.1002 + 0.1996)
    assert result.final_equity == pytest.approx(200.0 - result.total_cost)
    assert result.points[-1].positions == {}


def test_rebalance_revalues_equity_and_records_real_turnover() -> None:
    instrument = replace(PERP, fee_bps=10.0)
    result = simulate(
        spec(instrument),
        frames([100.0, 110.0, 110.0]),
        [Target(0, 0, {"X-PERP": 1.0}), Target(HOUR, HOUR, {"X-PERP": 0.5})],
    )
    first, rebalance, close = result.trades
    expected_quantity = (110.0 - first.fee) * 0.5 / 110.0
    assert rebalance.quantity_delta == pytest.approx(expected_quantity - 1.0)
    assert rebalance.fee == pytest.approx(abs(rebalance.quantity_delta) * 110.0 * 0.001)
    assert close.quantity_delta == pytest.approx(-expected_quantity)
    assert result.final_equity == pytest.approx(110.0 - sum(trade.fee for trade in result.trades))


def test_two_leg_carry_uses_separate_price_paths_and_settlement_notional() -> None:
    spot = Instrument("X-SPOT", "spot", 0.0, 0.0)
    market = [
        Frame(0, {"X-SPOT": 100.0, "X-PERP": 102.0}, {}),
        Frame(HOUR, {"X-SPOT": 110.0, "X-PERP": 115.0}, {"X-PERP": 0.01}),
        Frame(2 * HOUR, {"X-SPOT": 120.0, "X-PERP": 121.0}, {"X-PERP": 0.0}),
    ]
    # Equal underlying units rather than a premium-index substitute.
    result = simulate(spec(spot, PERP), market, [Target(0, 0, {"X-SPOT": 1.0, "X-PERP": -1.02})])
    assert result.total_funding == pytest.approx(1.15)
    assert result.final_equity == pytest.approx(100 + 20 - 19 + 1.15)


@pytest.mark.parametrize("index", [1, 2])
@pytest.mark.parametrize("missing", [False, True])
def test_unknown_funding_fails_including_the_terminal_holding_interval(
    index: int, missing: bool
) -> None:
    market = frames([100.0, 100.0, 100.0])
    market[index] = replace(market[index], funding_rates={} if missing else {"X-PERP": None})
    with pytest.raises(IntegrityError, match="Unknown funding coverage"):
        simulate(spec(), market, [Target(0, 0, {"X-PERP": 1.0})])


def test_funding_at_exit_belongs_to_previous_units_and_funding_at_entry_is_not_earned() -> None:
    market = frames([100.0] * 3, [None, 0.01, 0.01])
    entering_late = simulate(spec(), market, [Target(0, HOUR, {"X-PERP": -1.0})])
    assert entering_late.total_funding == pytest.approx(1.0)
    closing_early = simulate(
        spec(), market, [Target(0, 0, {"X-PERP": -1.0}), Target(HOUR, HOUR, {})]
    )
    assert closing_early.total_funding == pytest.approx(1.0)


def test_intratrade_mark_loss_is_visible_even_when_the_final_trade_breaks_even() -> None:
    result = simulate(spec(), frames([100.0, 50.0, 100.0]), [Target(0, 0, {"X-PERP": 1.0})])
    assert result.net_return == 0.0
    assert result.max_drawdown == 0.5
    assert result.points[1].drawdown == 0.5


def test_scale_is_verified_with_complete_equity_replay_instead_of_dividing_drawdown() -> None:
    # Rebalance daily at constant gross weight; ten losses have nonlinear compounded drawdown.
    count = 10
    market = frames([100.0 * 0.9**index for index in range(count + 1)])
    targets = [Target(i * HOUR, i * HOUR, {"X-PERP": 1.0}) for i in range(count)]
    base = simulate(spec(hours=count), market, targets)
    old_scale = 0.25 / base.max_drawdown
    assert simulate(spec(hours=count), market, targets, old_scale).max_drawdown > 0.32
    selected = select_scale(
        spec(hours=count), market, targets, [0.0, 0.2, 0.25, 0.3, old_scale, 1.0], 0.25
    )
    assert selected.scale == 0.25
    assert selected.result.max_drawdown <= 0.25
    assert all(drawdown > 0.25 for scale, drawdown in selected.evaluations if scale > 0.25)


def test_insolvent_scale_is_rejected_and_a_lower_scale_can_be_selected() -> None:
    market = frames([100.0, 50.0, 100.0])
    targets = [Target(0, 0, {"X-PERP": 1.0})]
    with pytest.raises(IntegrityError, match="insolvent"):
        simulate(spec(), market, targets, 2.0)
    selected = select_scale(spec(), market, targets, [0.0, 0.25, 0.5, 2.0], 0.25)
    assert selected.scale == 0.5
    assert selected.result.max_drawdown == 0.25


@pytest.mark.parametrize("target", [Target(0, 2 * HOUR, {"X-PERP": 1.0}), Target(HOUR, 0, {})])
def test_cross_boundary_and_future_decisions_fail(target: Target) -> None:
    with pytest.raises(IntegrityError, match="causal order"):
        simulate(spec(), frames([100.0] * 3), [target])


def test_source_frames_and_targets_are_not_mutated() -> None:
    market = frames([100.0] * 3)
    targets = [Target(0, 0, {"X-PERP": 1.0})]
    result = simulate(spec(), market, targets)
    assert targets[0].weights == {"X-PERP": 1.0}
    assert market[0].marks == {"X-PERP": 100.0}
    result.points[0].positions["X-PERP"] = 99
    assert result.points[1].positions == {"X-PERP": 1.0}


@pytest.mark.parametrize(
    "market",
    [
        frames([100.0, 100.0]),
        [Frame(0, {}, {}), Frame(HOUR, {}, {}), Frame(HOUR, {}, {})],
        frames([100.0, float("nan"), 100.0]),
        frames([100.0, -1.0, 100.0]),
        frames([100.0, True, 100.0]),
    ],
)
def test_invalid_or_incomplete_mark_grid_fails(market: list[Frame]) -> None:
    with pytest.raises(IntegrityError):
        simulate(spec(), market, [Target(0, 0, {"X-PERP": 1.0})])


def test_missing_held_mark_fails_while_flat_missing_prices_are_tolerated() -> None:
    market = [
        Frame(0, {"X-PERP": 100.0}, {}),
        Frame(HOUR, {}, {"X-PERP": 0.0}),
        Frame(2 * HOUR, {}, {"X-PERP": 0.0}),
    ]
    assert simulate(spec(), market, []).final_equity == 100.0
    with pytest.raises(IntegrityError, match="Missing funding settlement mark"):
        simulate(spec(), market, [Target(0, 0, {"X-PERP": 1.0})])


@pytest.mark.parametrize(
    "target",
    [
        Target(0, 0, {"UNKNOWN": 1.0}),
        Target(0, 0, {"X-PERP": float("inf")}),
        Target(0, 0, {"X-PERP": True}),
        Target(False, 0, {}),
    ],
)
def test_invalid_target_fields_fail(target: Target) -> None:
    with pytest.raises(IntegrityError):
        simulate(spec(), frames([100.0] * 3), [target])


def test_duplicate_executions_and_unknown_instruments_fail() -> None:
    with pytest.raises(IntegrityError, match="unique"):
        simulate(spec(), frames([100.0] * 3), [Target(0, 0, {}), Target(0, 0, {})])
    with pytest.raises(IntegrityError, match="Unknown mark instrument"):
        simulate(spec(), [Frame(i * HOUR, {"OTHER": 100.0}, {}) for i in range(3)], [])


def test_spot_funding_and_shorting_require_a_separate_model() -> None:
    spot = Instrument("X-SPOT", "spot", 0.0, 0.0)
    market = [Frame(i * HOUR, {"X-SPOT": 100.0}, {}) for i in range(3)]
    with pytest.raises(IntegrityError, match="Spot shorting"):
        simulate(spec(spot), market, [Target(0, 0, {"X-SPOT": -1.0})])
    with pytest.raises(IntegrityError, match="perpetual"):
        simulate(
            spec(spot), [replace(frame, funding_rates={"X-SPOT": 0.0}) for frame in market], []
        )


@pytest.mark.parametrize("candidates", [[], [True], [-1.0], [0.0, 0.0], [float("nan")]])
def test_invalid_scale_candidates_fail(candidates: list[float]) -> None:
    with pytest.raises(IntegrityError):
        select_scale(spec(), frames([100.0] * 3), [], candidates, 0.25)


def test_partial_targets_preserve_existing_units_when_other_coins_enter_and_exit() -> None:
    other = Instrument("Y-PERP", "perp", 0.0, 0.0)
    market = [
        Frame(i * HOUR, {"X-PERP": x, "Y-PERP": 100.0}, {"X-PERP": 0.0, "Y-PERP": 0.0})
        for i, x in enumerate([100.0, 110.0, 120.0, 120.0])
    ]
    targets = [
        Target(0, 0, {"X-PERP": 1.0}, replace_all=False),
        Target(HOUR, HOUR, {"Y-PERP": 0.2}, replace_all=False),
        Target(2 * HOUR, 2 * HOUR, {"X-PERP": 0.0}, replace_all=False),
    ]
    result = simulate(spec(PERP, other, hours=3), market, targets)
    assert result.points[1].positions == pytest.approx({"X-PERP": 1.0, "Y-PERP": 0.22})
    assert result.points[2].positions == pytest.approx({"Y-PERP": 0.22})
    assert result.final_equity == pytest.approx(120.0)
    assert len(result.trades) == 4
    assert result.points[-1].positions == {}


@pytest.mark.parametrize("flag", [None, 1, 0, float("nan")])
def test_partial_target_flag_requires_a_real_boolean(flag: object) -> None:
    target = Target(0, 0, {})
    object.__setattr__(target, "replace_all", flag)
    with pytest.raises(IntegrityError, match="replacement flag"):
        simulate(spec(), frames([100.0] * 3), [target])
