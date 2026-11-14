"""Synthetic causal timing, marked margin, funding, failure and source contracts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from research import compression_breakout as model
from research import compression_breakout_cli as cli
from research import compression_breakout_io as source
from research.btc_flow_io import PrivateOutput
from research.harness.errors import IntegrityError
from research.regime_cli import Account
from research.regime_io import Segment, midnight
from research.repaired_accounting import Frame, Instrument, ReplaySpec
from research.two_models_io import BTC, DAY, HOUR, MINUTE, Candle

START = 40 * DAY


def fixture(
    hours: int = 3,
) -> tuple[ReplaySpec, tuple[Frame, ...], dict[int, model.HourBox], dict[int, tuple[float, float]]]:
    end = START + hours * HOUR
    spec = ReplaySpec(START, end, MINUTE, 10000, (Instrument(BTC, "perp", 4.5, 2),))
    frames = tuple(
        Frame(t, {BTC: 100.0}, {BTC: 0.0}, {BTC: 100.0}) for t in range(START, end + 1, MINUTE)
    )
    hourly = {t: model.HourBox(95, 105, 100) for t in range(START - 24 * HOUR, end + 1, HOUR)}
    hourly[START] = model.HourBox(99.5, 100.5, 100)
    extremes = {t: (100.0, 100.0) for t in range(START + MINUTE, end + 1, MINUTE)}
    return spec, frames, hourly, extremes


def prices(
    frames: tuple[Frame, ...], values: dict[int, float], *, mark: bool = False
) -> tuple[Frame, ...]:
    result = []
    for frame in frames:
        if frame.timestamp_ms in values:
            data = {BTC: values[frame.timestamp_ms]}
            frame = replace(frame, marks=data) if mark else replace(frame, execution_prices=data)
        result.append(frame)
    return tuple(result)


def first_entry(
    hours: int = 3,
) -> tuple[ReplaySpec, tuple[Frame, ...], dict[int, model.HourBox], dict[int, tuple[float, float]]]:
    spec, frames, hourly, extremes = fixture(hours)
    frames = prices(frames, {START + MINUTE: 101})
    return spec, frames, hourly, extremes


@pytest.mark.parametrize(
    "value,background,accepted",
    [(0.05, 0.10, True), (0.0500001, 0.10, False), (0, 0.10, False), (0, 0, False)],
)
def test_half_median_equality_zero_and_current_box_exclusion(
    value: float, background: float, accepted: bool
) -> None:
    _, _, hours, _ = fixture()
    hours = {t: model.HourBox(100 - background * 50, 100 + background * 50, 100) for t in hours}
    hours[START] = model.HourBox(100 - value * 50, 100 + value * 50, 100)
    result = model.setup(hours, START)
    assert result == pytest.approx((accepted, value, background))


def test_even_median_and_complete_history() -> None:
    _, _, hours, _ = fixture()
    for i in range(1, 25):
        hours[START - i * HOUR] = model.HourBox(100 - i / 2, 100 + i / 2, 100)
    assert model.setup(hours, START)[2] == pytest.approx(0.125)
    del hours[START - HOUR]
    with pytest.raises(IntegrityError, match="25 complete"):
        model.setup(hours, START)


@pytest.mark.parametrize(
    "low,high,close", [(0, 100, 100), (100, 99, 100), (90, 100, math.nan), (90, math.inf, 100)]
)
def test_invalid_h1_fails(low: float, high: float, close: float) -> None:
    with pytest.raises(IntegrityError):
        model.HourBox(low, high, close)


@pytest.mark.parametrize("side,price", [(1, 101), (-1, 99)])
def test_strict_close_not_touch_and_next_minute_entry(side: int, price: float) -> None:
    spec, frames, hourly, extremes = fixture()
    frames = prices(
        frames, {START + MINUTE: 100.5, START + 2 * MINUTE: 99.5, START + 3 * MINUTE: price}
    )
    extremes[START + MINUTE] = (1, 1000)  # MARK extrema never create a TRADE-close signal.
    schedule = model.build_schedule(spec, frames, hourly)
    assert len(schedule.episodes) == 1
    episode = schedule.episodes[0]
    assert (episode.side, episode.decision_ms, episode.entry_ms) == (
        side,
        START + 3 * MINUTE,
        START + 4 * MINUTE,
    )
    assert schedule.counts["signals"] == 1
    assert schedule.counts["within_or_equal_box_minutes"] == 2


def test_feature_and_signal_prefix_ignores_future_ohlc_and_minute_prices() -> None:
    spec, frames, hourly, _ = first_entry()
    a = model.build_schedule(spec, frames, hourly)
    cut = START + 10 * MINUTE
    future = {t: model.HourBox(1, 1000, 50) if t > cut else b for t, b in hourly.items()}
    changed = prices(frames, {f.timestamp_ms: 1 for f in frames if f.timestamp_ms > cut})
    b = model.build_schedule(spec, changed, future)
    assert [r for r in a.observations if r["timestamp_ms"] <= cut] == [
        r for r in b.observations if r["timestamp_ms"] <= cut
    ]
    assert a.episodes[0].coefficient == b.episodes[0].coefficient


def test_first_busy_crossing_consumes_and_hour_expiry_has_priority() -> None:
    spec, frames, hourly, _ = first_entry()
    hourly[START + HOUR] = model.HourBox(99.5, 100.5, 100)
    frames = prices(frames, {START + HOUR + MINUTE: 101, START + HOUR + 2 * MINUTE: 102})
    schedule = model.build_schedule(spec, frames, hourly)
    assert schedule.counts["signals"] == 2 and schedule.counts["busy"] == 1
    assert len(schedule.episodes) == 1
    # Whole-hour TRADE outside the previous box does not signal.
    spec, frames, hourly, _ = fixture()
    frames = prices(frames, {START + HOUR: 101})
    schedule = model.build_schedule(spec, frames, hourly)
    assert not schedule.episodes and schedule.counts["expired_boxes"] == 1


def test_committed_last_minute_entry_survives_new_hour_box() -> None:
    spec, frames, hourly, _ = fixture()
    frames = prices(frames, {START + 59 * MINUTE: 101})
    schedule = model.build_schedule(spec, frames, hourly)
    assert schedule.episodes[0].entry_ms == START + HOUR


@pytest.mark.parametrize("trade,mark", [(101.0, 100.0), (200.0, 100.0), (100.0, 300.0)])
def test_future_fill_and_mark_cannot_resize_committed_quantity(trade: float, mark: float) -> None:
    spec, frames, hourly, extremes = first_entry()
    entry = START + 2 * MINUTE
    frames = prices(prices(frames, {entry: trade}), {entry: mark}, mark=True)
    schedule = model.build_schedule(spec, frames, hourly)
    case = model.evaluate_case(spec, frames, schedule, extremes)
    row = case["episodes"][0]
    assert row["quantity"] == pytest.approx(25)
    assert row["committed_quantity"] == pytest.approx(0.25 * 10000 / 100)
    assert row["decision_mark"] == 100 and row["decision_equity"] == 10000
    assert row["initial_margin"] == pytest.approx(25 * mark / 10)
    assert row["margin_denominator"] == "abs(quantity)*ACTUAL_ENTRY_MARK/10"
    if mark == 300:
        assert row["risk_invalid"]
        assert any(r["kind"] == "entry_asset_cap_overrun" for r in row["risk_events"])
    if trade == 200:
        assert row["risk_invalid"] and row["net_pnl"] < 0  # retained losing gap, never cancelled.


@pytest.mark.parametrize("side,trigger_price,overshoot", [(1, 103, 100), (-1, 97, 100)])
def test_target_trigger_next_minute_miss_is_not_clamped(
    side: int, trigger_price: float, overshoot: float
) -> None:
    spec, frames, hours, extremes = fixture()
    frames = prices(
        frames,
        {
            START + MINUTE: 101 if side > 0 else 99,
            START + 3 * MINUTE: trigger_price,
            START + 4 * MINUTE: overshoot,
        },
    )
    schedule = model.build_schedule(spec, frames, hours)
    ep = schedule.episodes[0]
    assert ep.reason == "target" and ep.trigger_ms == START + 3 * MINUTE
    assert ep.exit_ms == START + 4 * MINUTE
    assert ep.trigger_estimated_roi is not None and ep.trigger_estimated_roi > 0.20
    row = model.evaluate_case(spec, frames, schedule, extremes)["episodes"][0]
    assert not row["target_met"] and row["net_margin_roi"] < 0


def test_stop_overshoot_and_stress_has_own_policy_exit() -> None:
    spec, frames, hours, extremes = first_entry()
    frames = prices(frames, {START + 3 * MINUTE: 98.5, START + 4 * MINUTE: 95})
    ep = model.build_schedule(spec, frames, hours).episodes[0]
    assert ep.reason == "stop" and ep.exit_ms == START + 4 * MINUTE
    row = model.evaluate_case(spec, frames, model.build_schedule(spec, frames, hours), extremes)[
        "episodes"
    ][0]
    assert row["net_margin_roi"] < -0.4
    spec, frames, hours, _ = first_entry()
    frames = prices(frames, {START + 3 * MINUTE: 102.2, START + 4 * MINUTE: 103})
    base = model.build_schedule(spec, frames, hours)
    stress_spec = replace(spec, instruments=(Instrument(BTC, "perp", 9, 4),))
    stress = model.build_schedule(stress_spec, frames, hours)
    assert base.episodes[0].exit_ms == START + 4 * MINUTE
    assert stress.episodes[0].exit_ms == START + 5 * MINUTE


def test_no_entry_minute_bracket_and_24h_paid_terminal_priority() -> None:
    spec, frames, hourly, _ = first_entry(26)
    entry = START + 2 * MINUTE
    frames = prices(frames, {entry: 150, entry + MINUTE: 150})
    schedule = model.build_schedule(spec, frames, hourly)
    assert schedule.episodes[0].trigger_ms == entry + 2 * MINUTE
    spec, frames, hourly, _ = first_entry(26)
    ep = model.build_schedule(spec, frames, hourly).episodes[0]
    assert ep.reason == "time" and ep.exit_ms == entry + DAY
    # A trigger at deadline-1 would fill at deadline; terminal/time owns it.
    frames = prices(frames, {entry + DAY - MINUTE: 110})
    ep = model.build_schedule(spec, frames, hourly).episodes[0]
    assert ep.reason == "time" and ep.exit_ms == entry + DAY


def test_segment_end_entry_exclusion_is_counted_without_loss_deletion() -> None:
    spec, frames, hourly, _ = fixture(1)
    frames = prices(frames, {spec.end_ms - MINUTE: 101})
    schedule = model.build_schedule(spec, frames, hourly)
    assert schedule.counts["signals"] == 1 and schedule.counts["terminal_excluded"] == 1
    assert not schedule.targets


@pytest.mark.parametrize("side", [-1, 1])
def test_exact_funding_ownership_entry_exit_and_plusminus_ms(side: int) -> None:
    spec, frames, hourly, extremes = fixture(1)
    frames = prices(frames, {START + MINUTE: 101 if side > 0 else 99})
    entry, exit_t = START + 2 * MINUTE, spec.end_ms
    events = {
        entry - 1: 0.00001,
        entry: 0.00001,
        entry + 1: 0.00002,
        exit_t - 1: -0.00001,
        exit_t: 0.00003,
    }
    frames = tuple(replace(f, funding_rates={BTC: events.get(f.timestamp_ms, 0.0)}) for f in frames)
    frames = tuple(
        sorted(
            (
                *frames,
                *(Frame(t, {BTC: 100.0}, {BTC: rate}) for t, rate in events.items() if t % MINUTE),
            ),
            key=lambda f: f.timestamp_ms,
        )
    )
    spec = replace(spec, event_times_ms=tuple(f.timestamp_ms for f in frames))
    schedule = model.build_schedule(spec, frames, hourly)
    case = model.evaluate_case(spec, frames, schedule, extremes)
    expected = -side * 25 * 100 * (0.00002 - 0.00001 + 0.00003)
    assert case["total_funding"] == pytest.approx(expected)
    assert case["episodes"][0]["funding"] == pytest.approx(expected)
    assert case["episodes"][0]["net_pnl"] == pytest.approx(case["final_equity"] - 10000)


def test_controls_keep_candidate_clocks_but_use_their_own_capital() -> None:
    spec, frames, hours, extremes = first_entry()
    schedule = model.build_schedule(spec, frames, hours)
    short = model.control_schedule(schedule, {f.timestamp_ms: f for f in frames}, spec.end_ms, -1)
    assert [(e.decision_ms, e.entry_ms, e.exit_ms) for e in short.episodes] == [
        (e.decision_ms, e.entry_ms, e.exit_ms) for e in schedule.episodes
    ]
    assert short.episodes[0].coefficient == -abs(schedule.episodes[0].coefficient)
    case = model.evaluate_case(replace(spec, initial_equity=5000), frames, short, extremes)
    assert case["episodes"][0]["quantity"] == pytest.approx(-12.5)
    cash = model.control_schedule(schedule, {f.timestamp_ms: f for f in frames}, spec.end_ms, 0)
    assert not cash.targets and not cash.episodes


def test_held_adverse_minute_risk_does_not_erase_pnl() -> None:
    spec, frames, hourly, extremes = first_entry()
    extremes[START + 3 * MINUTE] = (50, 100)
    case = model.evaluate_case(spec, frames, model.build_schedule(spec, frames, hourly), extremes)
    assert case["episodes"][0]["risk_invalid"] and case["final_equity"] is not None
    assert any(r["kind"] == "intraminute_possible_liquidation" for r in case["risk_events"])
    del extremes[START + 3 * MINUTE]
    with pytest.raises(IntegrityError, match="extremes"):
        model.evaluate_case(spec, frames, model.build_schedule(spec, frames, hourly), extremes)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "mark", "funding", "trade"])
def test_unexpected_source_problem_fails(kind: str) -> None:
    spec, frames, hours, _ = first_entry()
    if kind == "missing":
        frames = frames[:3] + frames[4:]
    elif kind == "duplicate":
        frames = frames[:3] + frames[2:]
    elif kind == "mark":
        frames = (frames[0], replace(frames[1], marks={}), *frames[2:])
    elif kind == "funding":
        frames = (frames[0], replace(frames[1], funding_rates={BTC: None}), *frames[2:])
    else:
        frames = (frames[0], replace(frames[1], execution_prices={}), *frames[2:])
    with pytest.raises(IntegrityError):
        model.build_schedule(spec, frames, hours)


@pytest.mark.parametrize("failure_at", [0, 2, 4])
def test_transactional_partial_add_rollback_keeps_completed_prefix(failure_at: int) -> None:
    account = Account()
    for name in cli.AGGREGATES:
        getattr(account, name).append({"completed": True})
    account.equity = 12345
    saved = cli.checkpoint(account)
    for index, name in enumerate(cli.AGGREGATES):
        getattr(account, name).append({"failed": True})
        if index == failure_at:
            break
    account.equity = 1
    cli.rollback(account, saved)
    account.fail(MemoryError("partial extend"))
    assert account.equity == 12345 and account.blocked
    assert all(getattr(account, n) == [{"completed": True}] for n in cli.AGGREGATES)
    with pytest.raises(IntegrityError, match="cannot silently resume"):
        account.add({}, ())


@pytest.mark.parametrize("mode", ["LIVE", "TESTNET", "SHADOW"])
def test_paper_gate_precedes_all_io(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setattr(cli, "identity", lambda _: pytest.fail("source IO before PAPER gate"))
    with pytest.raises(Exception, match="PAPER"):
        cli.main(
            [
                "--input",
                str(tmp_path / "missing"),
                "--output",
                str(tmp_path / "out"),
                "--source-commit",
                "0" * 40,
                "--expected-plan-sha256",
                "0" * 64,
            ]
        )
    assert not (tmp_path / "out").exists()


def test_serial_all_accounts_costs_and_gap_capital_carry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec, frames, hourly, extremes = first_entry(24)
    first = Segment(spec.start_ms, spec.end_ms, frames, extremes)
    shift = 2 * DAY
    second = Segment(
        spec.start_ms + shift,
        spec.end_ms + shift,
        tuple(replace(f, timestamp_ms=f.timestamp_ms + shift) for f in frames),
        {t + shift: v for t, v in extremes.items()},
    )
    hours = dict(hourly)
    hours.update({t + shift: v for t, v in hourly.items()})
    monkeypatch.setattr(cli, "START", START)
    monkeypatch.setattr(cli, "END", second.end_ms)
    monkeypatch.setattr(cli, "FOLDS", {"development": (START, second.end_ms)})
    with PrivateOutput(tmp_path / "output") as output:
        result = cli.run(source.Sources((first, second), hours, {}), output)
    assert (
        result["unique_configurations"] == 1 and result["pilot_unique_configurations_to_date"] == 37
    )
    assert result["selector"] is None and result["promotion_decision"] == "forbidden"
    assert set(result["controls"]) == {"cash", "matched_long", "matched_short"}
    assert "insufficient_sample" in result["disposition"]
    for pair in (result["candidate"], *result["controls"].values()):
        assert set(pair) == {"base", "stress"}
        for account in pair.values():
            assert not account["failed"] and account["calendar_days"] == 3
            assert len(account["daily_equity_returns"]) == 3  # flat gap is retained.
            assert account["daily_equity_returns"][1]["net_return"] == 0
            if account["episodes"]:
                assert account["episodes"][1]["decision_equity"] != 10000
                assert account["episodes"][0]["exit_ms"] == first.end_ms
    assert len(list((tmp_path / "output").glob("*-case.json.gz"))) == 16
    with pytest.raises(FileExistsError):
        with PrivateOutput(tmp_path / "output"):
            pass


def test_frozen_protocol_and_no_legacy_source_change() -> None:
    assert (
        hashlib.sha256(source.PLAN.read_bytes()).hexdigest()
        == "6c3b823a20f9cd2ff0948b5c44956a64540d886f9738acaa8a42e513b31ed70b"
    )
    assert "20% account/day" in source.PLAN.read_text()
    assert "37/40" in source.PLAN.read_text()


def test_dense_source_projection_clock_shape_and_no_financial_schedule(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec, frames, hourly, extremes = fixture(24)
    raw = b"{}"
    p = tmp_path / "input.json"
    p.write_bytes(raw)
    monkeypatch.setattr(source, "INPUT_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(source, "START", START)
    monkeypatch.setattr(source, "END", spec.end_ms)
    monkeypatch.setattr(source, "INTERVALS", ((START, spec.end_ms),))
    monkeypatch.setattr(source, "BAD_DAYS", ())
    monkeypatch.setattr(source, "json_value", lambda _: {"daily_sources": [{"role": "hourly"}]})
    monkeypatch.setattr(source, "check_index", lambda *a, **kw: {})
    monkeypatch.setattr(source, "funding_censuses", lambda _: ({}, {}))
    monkeypatch.setattr(
        source,
        "candles",
        lambda *a: [Candle(t - HOUR, b.close, b.low, b.high) for t, b in hourly.items()],
    )
    monkeypatch.setattr(
        source,
        "daily_months",
        lambda _: iter([("synthetic", list(frames), {}, extremes, frozenset())]),
    )
    monkeypatch.setattr(
        model,
        "build_schedule",
        lambda *a: pytest.fail("source shape pass created financial schedule"),
    )
    loaded = source.load(p)
    assert len(loaded.segments[0].frames) == 1441
    assert loaded.source_summary["retained_frames"] == 1441
    assert loaded.source_summary["calendar_days"] == 1
    assert loaded.hourly == hourly
    p.write_bytes(b'{"changed":true}')
    with pytest.raises(IntegrityError, match="pinned input"):
        source.load(p)


def test_month_year_rollover_keeps_committed_quantity_and_paid_position() -> None:
    spec, frames, hourly, extremes = fixture(2)
    # A 23:59 signal enters exactly on the new year's whole-hour clock.
    frames = prices(frames, {START + 59 * MINUTE: 101})
    shift = midnight("2024-12-31") + 23 * HOUR - START
    spec = replace(spec, start_ms=spec.start_ms + shift, end_ms=spec.end_ms + shift)
    frames = tuple(replace(f, timestamp_ms=f.timestamp_ms + shift) for f in frames)
    hourly = {t + shift: b for t, b in hourly.items()}
    extremes = {t + shift: v for t, v in extremes.items()}
    schedule = model.build_schedule(spec, frames, hourly)
    case = model.evaluate_case(spec, frames, schedule, extremes)
    row = case["episodes"][0]
    assert row["entry_ms"] == midnight("2025-01-01")
    assert row["exit_ms"] == spec.end_ms and row["quantity"] == pytest.approx(25)
    assert len(case["trades"]) == 2


def test_source_only_cli_never_scores_or_selects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    spec, frames, hours, extremes = fixture(24)
    sources = source.Sources(
        (Segment(spec.start_ms, spec.end_ms, frames, extremes),), hours, {"shape_only": True}
    )
    monkeypatch.setattr(cli, "identity", lambda _: {"synthetic": "identity"})
    monkeypatch.setattr(cli, "verify_prices", lambda _: None)
    monkeypatch.setattr(cli, "load", lambda _: sources)
    monkeypatch.setattr(cli, "run", lambda *a: pytest.fail("source-only financial run"))
    assert (
        cli.main(
            [
                "--input",
                str(tmp_path / "unused"),
                "--output",
                str(tmp_path / "shape"),
                "--source-commit",
                "0" * 40,
                "--expected-plan-sha256",
                hashlib.sha256(source.PLAN.read_bytes()).hexdigest(),
                "--source-only",
            ]
        )
        == 0
    )
    assert '"source_only_no_financial_labels"' in (tmp_path / "shape/result.json").read_text()
    assert not list((tmp_path / "shape").glob("*-case.json.gz"))


def test_actual_partial_add_failure_does_not_publish_failed_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec, frames, hourly, extremes = first_entry(24)
    first = Segment(spec.start_ms, spec.end_ms, frames, extremes)
    shift = 2 * DAY
    second = Segment(
        spec.start_ms + shift,
        spec.end_ms + shift,
        tuple(replace(f, timestamp_ms=f.timestamp_ms + shift) for f in frames),
        {t + shift: v for t, v in extremes.items()},
    )
    hours = dict(hourly)
    hours.update({t + shift: b for t, b in hourly.items()})
    monkeypatch.setattr(cli, "START", START)
    monkeypatch.setattr(cli, "END", second.end_ms)
    monkeypatch.setattr(cli, "FOLDS", {"development": (START, second.end_ms)})
    original = Account.add
    calls: dict[int, int] = {}

    def partial(self: Account, case: dict[str, Any], segment_frames: tuple[Frame, ...]) -> None:
        index = calls.get(id(self), 0)
        calls[id(self)] = index + 1
        if segment_frames[0].timestamp_ms == second.start_ms:
            self.points.extend(case["ledger"])
            self.episodes.extend(case["episodes"])
            raise MemoryError("injected after partial aggregate mutation")
        original(self, case, segment_frames)

    monkeypatch.setattr(Account, "add", partial)
    with PrivateOutput(tmp_path / "failures") as output:
        result = cli.run(source.Sources((first, second), hours, {}), output)
    for pair in (result["candidate"], *result["controls"].values()):
        for row in pair.values():
            assert row["failed"] and row["final_equity"] is None
            assert all(e["entry_ms"] < second.start_ms for e in row["episodes"])
            assert row["complete_trades"] <= 1
            assert len(row["trade_day_counts_completed_segments_only"]) == 1
    assert "failed_calculation" in result["disposition"]
