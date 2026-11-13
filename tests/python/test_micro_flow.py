"""Synthetic causal, execution, margin and missing-outcome counterexamples."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from research.harness.errors import IntegrityError
from research.micro_flow import (
    SECOND,
    MicroConfig,
    MicroEvent,
    Pressure,
    _book,
    configs,
    evaluate,
    replay,
)

D = Decimal
FUNDING = (0, 3_600_000)


def events(
    *,
    side: int = 1,
    duration: int = 900,
    slope: int = 2,
    touch: str = "1",
    venue_start_ms: int = 600_000,
) -> list[MicroEvent]:
    out = []
    for second in range(duration + 1):
        mid = D(100_000 + side * second * slope)
        for half in (0, SECOND // 2):
            t = second * SECOND + half
            out.append(
                MicroEvent(
                    t,
                    1_700_000_000 * SECOND + t,
                    venue_start_ms + t // 1_000_000,
                    "book",
                    "session",
                    mid - 1,
                    mid + 1,
                    D(touch) * (3 if side == 1 else 1),
                    D(touch) * (1 if side == 1 else 3),
                )
            )
        t = second * SECOND + SECOND // 10
        out.append(
            MicroEvent(
                t,
                1_700_000_000 * SECOND + t,
                venue_start_ms + t // 1_000_000,
                "trade",
                "session",
                trade_price=mid,
                trade_quantity=D("0.1"),
                trade_side=side,
            )
        )
    return sorted(out, key=lambda row: row.mono_ns)


def case(data: list[MicroEvent], **kwargs: Any) -> dict[str, Any]:
    return replay(configs()[0], data, FUNDING, **kwargs)


def test_configuration_budget_is_eight_fixed_rules() -> None:
    values = configs()
    assert len(values) == len({c.name for c in values}) == 8
    assert {c.lookback_seconds for c in values} == {30, 120}
    assert {c.strength for c in values} == {0.2, 0.4}
    assert {c.target_margin_return for c in values} == {0.01, 0.02}


@pytest.mark.parametrize("side", (1, -1))
def test_trend_both_directions_has_delayed_adverse_taker_fills(side: int) -> None:
    result = case(events(side=side))
    assert result["status"] == "complete", result["error"]
    assert result["complete_trades"] > 0
    assert result["open_position"] is None
    assert sum(t["net_pnl"] for t in result["trades"]) == pytest.approx(
        result["final_equity"] - result["initial_equity"]
    )
    for fill in result["fills"]:
        assert fill["mono_ns"] >= fill["decision_mono_ns"] + SECOND // 2
        assert fill["fee"] > 0 and fill["slippage"] > 0
        assert fill["side"] * (fill["price"] - fill["touch"]) > 0
    assert all(t["side"] == side for t in result["trades"])


def test_initial_margin_and_realized_target_are_accounted_on_actual_fill() -> None:
    result = case(events())
    for trade in result["trades"]:
        assert trade["initial_margin"] == pytest.approx(trade["quantity"] * trade["entry"] / 10)
        expected = trade["net_pnl"] / trade["initial_margin"]
        assert trade["net_margin_return"] == pytest.approx(expected)
        assert trade["realized_target_met"] == (expected >= 0.01)
        assert trade["entry_mono_ns"] > trade["decision_mono_ns"]
    assert result["net_return"] > 0
    assert result["same_quantity_gross_mid_pnl"] - result["fee_cost"] - result[
        "execution_spread_and_friction_cost"
    ] == pytest.approx(result["final_equity"] - 10_000)


def test_small_visible_touch_caps_the_actual_entry_quantity() -> None:
    result = case(events(touch="0.0005"))
    assert result["status"] == "complete"
    assert result["counts"]["partial_entries"] > 0
    assert all(t["quantity"] <= 0.0005 for t in result["trades"])


def test_subminimum_entry_is_known_nonfill_without_fabricated_position() -> None:
    result = case(events(touch="0.00001"))
    assert result["status"] == "complete"
    assert result["complete_trades"] == 0
    assert result["counts"]["entry_nonfills"] > 0
    assert result["final_equity"] == 10_000


def test_future_prices_cannot_change_a_prefix_of_executed_fills() -> None:
    full = events()
    cutoff = 180 * SECOND
    whole = case(full, end_mono_ns=901 * SECOND)
    prefix = case([e for e in full if e.mono_ns <= cutoff], end_mono_ns=901 * SECOND)
    assert prefix["status"] == "failed"  # Its open position cannot acquire a future exit.
    assert prefix["fills"] == [f for f in whole["fills"] if f["mono_ns"] <= cutoff]
    assert prefix["open_position"] is not None


def test_unknown_exit_capacity_retains_the_open_position_and_partial_cash() -> None:
    data = events()
    for index, event in enumerate(data):
        if event.kind == "book" and event.mono_ns >= 100 * SECOND:
            data[index] = replace(event, bid_size=D("0.00001"))
    result = case(data)
    assert result["status"] == "failed"
    assert "touch capacity" in result["error"]
    assert result["open_position"] is not None
    assert len(result["fills"]) == 1
    assert result["complete_trades"] == 0


def test_missing_future_quotes_is_failure_not_zero_pnl() -> None:
    data = [event for event in events() if event.mono_ns < 180 * SECOND]
    result = case(data, end_mono_ns=901 * SECOND)
    assert result["status"] == "failed"
    assert "input boundary" in result["error"]
    assert result["open_position"] is not None


def test_native_calendar_blocks_funding_boundary_entries() -> None:
    data = events(duration=100, venue_start_ms=3_600_000)
    result = replay(configs()[0], data, (0, 3_600_000, 7_200_000), end_mono_ns=901 * SECOND)
    assert result["status"] == "complete"
    assert result["complete_trades"] == 0
    assert result["counts"]["calendar"] > 0


def test_actual_funding_inside_exposure_invalidates_case_without_erasing_close() -> None:
    result = replay(configs()[0], events(), (0, 700_000, 3_600_000))
    assert result["status"] == "failed"
    assert "funding event" in result["error"]
    assert result["complete_trades"] == 1
    assert result["open_position"] is None
    assert result["total_funding"] is None
    assert result["final_equity"] == pytest.approx(10_000 + result["trades"][0]["net_pnl"])


def test_capture_gap_is_not_silently_ignored() -> None:
    data = events()
    item = data[200]
    data[200] = replace(item, kind="gap")
    result = case(data)
    assert result["status"] == "failed"
    assert "capture gap" in result["error"]


def test_session_change_refuses_continuity() -> None:
    data = events()
    data[200] = replace(data[200], session="reconnected")
    result = case(data)
    assert result["status"] == "failed"
    assert "native session" in result["error"]


def test_l2_snapshot_refresh_can_preserve_a_fresh_unchanged_book() -> None:
    pressure = Pressure(30)
    rows = [e for e in events() if e.mono_ns < 50 * SECOND]
    current = None
    for event in rows:
        current = _book(event) if event.kind == "book" else None
        pressure.add(event, current)
    assert current is not None
    assert pressure.signal(50 * SECOND, current, 0.2, False) == 1


def test_opposing_tape_prevents_the_queue_only_signal() -> None:
    rows = [replace(e, trade_side=-1) if e.kind == "trade" else e for e in events()]
    result = case(rows)
    assert result["status"] == "complete"
    assert not result["fills"]


def test_long_quote_silence_prevents_entries_instead_of_future_quote_backfill() -> None:
    data = [e for e in events() if e.kind != "book" or e.mono_ns % (5 * SECOND) == 0]
    result = case(data)
    assert result["status"] == "complete"
    assert not result["fills"]
    assert result["counts"]["entry_nonfills"] > 0


def test_known_round_trip_cost_above_stop_is_rejected_before_position_exists() -> None:
    rows = [replace(e, ask=e.ask + D(1000)) if e.kind == "book" and e.ask else e for e in events()]
    result = case(rows)
    assert result["status"] == "complete"
    assert not result["fills"]
    assert result["counts"]["cost_exceeds_stop"] > 0


def test_base_and_stress_are_full_fee_aware_policy_replays() -> None:
    result = evaluate(configs()[0], events(), FUNDING)
    assert result["base"]["status"] == result["stress"]["status"] == "complete"
    assert result["base"]["complete_trades"] > 0
    assert result["stress"]["complete_trades"] > 0
    assert result["base"]["fills"][0]["mono_ns"] < result["stress"]["fills"][0]["mono_ns"]
    assert (
        result["base"]["trades"][0]["exit_mono_ns"] != result["stress"]["trades"][0]["exit_mono_ns"]
    )


def test_simple_momentum_benchmark_uses_same_fill_and_target_contract() -> None:
    result = replay(
        MicroConfig("momentum-benchmark", 30, 0.0, 0.02), events(), FUNDING, benchmark=True
    )
    assert result["status"] == "complete"
    assert result["complete_trades"] > 0
    assert all(t["side"] == 1 for t in result["trades"])


def test_observed_reference_margin_exhaustion_is_not_a_recoverable_paper_win() -> None:
    rows = events()
    for index, event in enumerate(rows):
        if event.kind == "book" and event.mono_ns >= 100 * SECOND:
            rows[index] = replace(event, bid=D(85_000), ask=D(85_002))
    result = case(rows)
    assert result["status"] == "failed"
    assert result["liquidation_possible_on_observed_book"]
    assert result["reference_margin_exhausted"]
    assert result["open_position"] is not None


def test_unqualified_funding_census_cannot_certify_zero_payments() -> None:
    with pytest.raises(IntegrityError, match="qualified funding census"):
        replay(configs()[0], events(), ())
    with pytest.raises(IntegrityError, match="bracket"):
        replay(configs()[0], events(), (0,))


def test_late_capture_gap_outside_known_window_cannot_change_results() -> None:
    rows = events()
    cutoff = 600 * SECOND
    expected = case([event for event in rows if event.mono_ns <= cutoff], end_mono_ns=cutoff)
    first = next(index for index, event in enumerate(rows) if event.mono_ns > cutoff)
    rows[first] = replace(rows[first], kind="gap", session="future-session")
    actual = case(rows, end_mono_ns=cutoff)
    assert actual == expected


def test_stop_execution_reports_actual_overshoot_instead_of_clipping_loss() -> None:
    rows = events()
    for index, event in enumerate(rows):
        if event.kind == "book" and event.mono_ns >= 100 * SECOND:
            rows[index] = replace(event, bid=D(98_000), ask=D(98_002))
    result = case(rows)
    assert result["status"] == "complete"
    first = result["trades"][0]
    assert first["reason"] == "stop"
    assert first["net_margin_return"] < -0.05
    assert result["max_drawdown"] > 0


def test_expired_exit_quote_is_an_unknown_exposure_and_preserves_loss() -> None:
    rows = events()
    changed = []
    for event in rows:
        if event.kind == "book" and event.mono_ns >= 100 * SECOND:
            if event.mono_ns < 102 * SECOND and event.mono_ns != 100 * SECOND:
                continue
            event = replace(event, bid=D(98_000), ask=D(98_002))
        changed.append(event)
    result = case(changed)
    assert result["status"] == "failed"
    assert "quote deadline" in result["error"]
    assert result["open_position"] is not None
    assert result["final_equity"] < 10_000


def test_both_native_book_and_trade_need_a_full_feature_warmup() -> None:
    rows = [event for event in events() if event.kind != "trade" or event.mono_ns >= 70 * SECOND]
    result = case(rows)
    assert result["status"] == "complete"
    assert result["fills"][0]["decision_mono_ns"] >= 100 * SECOND


def test_small_trade_notional_cap_is_independent_of_large_unused_account() -> None:
    result = case(events())
    assert result["status"] == "complete"
    assert result["complete_trades"] > 0
    assert result["max_requested_notional_usdc"] == 100.0
    assert all(trade["quantity"] * trade["entry"] <= 100.0 + 1e-9 for trade in result["trades"])
    assert all(trade["initial_margin"] <= 10.0 + 1e-9 for trade in result["trades"])
