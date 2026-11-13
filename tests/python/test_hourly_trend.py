"""Synthetic hourly policy, native clock, serial memory and evidence boundaries."""

from __future__ import annotations

import gc
import hashlib
import math
import weakref
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from research import hourly_trend as model
from research import hourly_trend_cli as cli
from research import hourly_trend_io as source
from research import regime_momentum as original
from research.btc_flow_io import PrivateOutput
from research.harness.errors import IntegrityError
from research.regime_cli import Account
from research.regime_io import Segment, Sources
from research.repaired_accounting import Frame, Target
from research.two_models_io import BTC, DAY, HOUR, MINUTE

START = 40 * DAY


def fixture(days: int = 1) -> Sources:
    end = START + days * DAY
    hours = {
        t: 100 * math.exp(0.001 * ((t - START) // HOUR))
        for t in range(START - 720 * HOUR, end + 1, HOUR)
    }
    times = set(range(START, end + 1, HOUR))
    times |= {t + d for t in range(START, end, HOUR) for d in (MINUTE, 2 * MINUTE)}
    frames = tuple(Frame(t, {BTC: 100.0}, {BTC: 0.0}, {BTC: 99.0}) for t in sorted(times))
    extremes = {t: (100.0, 100.0) for t in range(START + MINUTE, end + 1, MINUTE)}
    return Sources((Segment(START, end, frames, extremes),), hours, {})


def policy(hours: int = 24, confirmed: bool = False) -> model.HourlyTrendConfig:
    return model.HourlyTrendConfig("fixture", hours, confirmed)


def path(
    monkeypatch: pytest.MonkeyPatch, rows: dict[int, tuple[float | None, float | None, int | None]]
) -> None:
    def features(
        config: model.HourlyTrendConfig, hourly: Mapping[int, float], instant: int
    ) -> tuple[float | None, float | None, int | None]:
        del config, hourly
        return rows.get((instant - START) // HOUR, (0.0, 0.2, 0))

    monkeypatch.setattr(model, "features", features)


def test_exact_four_configs_and_no_legacy_config_relaxation() -> None:
    configs = model.configs()
    assert len(configs) == 4 and len({c.name for c in configs}) == 4
    assert {(c.lookback_hours, c.confirmed) for c in configs} == {
        (24, False),
        (24, True),
        (72, False),
        (72, True),
    }
    with pytest.raises(IntegrityError, match="24 or 72"):
        model.HourlyTrendConfig("bad", 168, False)
    with pytest.raises(IntegrityError, match="24 or 72"):
        model.HourlyTrendConfig("bad", True, False)
    with pytest.raises(IntegrityError, match="flag"):
        model.HourlyTrendConfig("bad", 24, 1)  # type: ignore[arg-type]
    with pytest.raises(IntegrityError, match="168 or 720"):
        original.RegimeConfig("old", 24, False)


@pytest.mark.parametrize("lookback", [24, 72])
def test_short_score_and_original_sigma_confirmation_are_prefix_causal(lookback: int) -> None:
    sources = fixture()
    hours = sources.hourly
    score, sigma, confirmation = model.features(policy(lookback), hours, START)
    _, old_sigma, old_confirmation = original.features(original.configs()[0], hours, START)
    assert score == pytest.approx(math.sqrt(lookback))
    assert sigma == old_sigma == pytest.approx(math.sqrt(365 * 24) * 0.001)
    assert confirmation == old_confirmation == 1
    changed = {t: value * 1000 if t > START else value for t, value in hours.items()}
    assert model.features(policy(lookback), hours, START) == model.features(
        policy(lookback), changed, START
    )
    del changed[START - 719 * HOUR]
    assert model.features(policy(lookback), changed, START) == (None, None, None)
    assert model.features(policy(lookback), {t: 100.0 for t in hours}, START) == (None, None, None)


def test_full_schedule_prefix_does_not_read_changed_future() -> None:
    sources = fixture()
    segment = sources.segments[0]
    cut = START + 4 * HOUR
    changed = {t: value * 0.01 if t > cut else value for t, value in sources.hourly.items()}
    a = model.build_schedule(policy(), segment.spec(10000), segment.frames, sources.hourly)
    b = model.build_schedule(policy(), segment.spec(10000), segment.frames, changed)
    assert a.decisions[:5] == b.decisions[:5]
    assert [t for t in a.targets if t.decision_ms <= cut] == [
        t for t in b.targets if t.decision_ms <= cut
    ]
    assert len(a.decisions) == 24 and a.decisions[-1].timestamp_ms == segment.end_ms - HOUR


def test_hourly_hysteresis_delays_reversal_and_confirmation_holding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = fixture()
    segment = sources.segments[0]
    path(
        monkeypatch,
        {
            0: (0.999, 0.2, 1),
            1: (1, 0.2, 1),
            2: (0.5, 0.1, 1),
            3: (0.49, 0.2, 1),
            4: (-1, 0.2, -1),
            5: (1, 0.2, 1),
            6: (1, 0.2, -1),
        },
    )
    schedule = model.build_schedule(
        policy(confirmed=True), segment.spec(10000), segment.frames, sources.hourly
    )
    assert [d.action for d in schedule.decisions[:7]] == [
        "flat",
        "enter",
        "hold",
        "close",
        "enter",
        "reverse",
        "close",
    ]
    assert schedule.decisions[2].entry_weight is None
    assert [(t.execution_ms - START, t.weights) for t in schedule.targets] == [
        (HOUR + MINUTE, {BTC: 0.5}),
        (3 * HOUR + MINUTE, {}),
        (4 * HOUR + MINUTE, {BTC: -0.5}),
        (5 * HOUR + MINUTE, {}),
        (5 * HOUR + 2 * MINUTE, {BTC: 0.5}),
        (6 * HOUR + MINUTE, {}),
    ]
    result = original.evaluate_case(
        segment.spec(10000), segment.frames, schedule.targets, segment.minute_extremes
    )
    assert result["complete_trades"] == 3 and len(result["trades"]) == 6
    assert result["episodes"][0]["quantity"] == pytest.approx(10000 * 0.5 / 99)
    assert result["episodes"][0]["initial_margin"] == pytest.approx(
        abs(result["episodes"][0]["quantity"]) * 100 / 2
    )


def test_native_minute_trade_is_distinct_from_hour_and_mark() -> None:
    sources = fixture()
    segment = sources.segments[0]
    schedule = model.build_schedule(policy(), segment.spec(10000), segment.frames, sources.hourly)
    result = original.evaluate_case(
        segment.spec(10000), segment.frames, schedule.targets, segment.minute_extremes
    )
    trade = result["trades"][0]
    assert trade["timestamp_ms"] == START + MINUTE and trade["reference_price"] == 99
    assert trade["mark"] == 100 and sources.hourly[START + HOUR] != 99
    assert result["episodes"][0]["quantity"] == pytest.approx(10000 * 0.5 / 99)
    assert len(result["trades"]) == 2  # Hourly decisions hold one unchanged quantity.
    without = tuple(f for f in segment.frames if f.timestamp_ms != START + MINUTE)
    with pytest.raises(IntegrityError, match="execution frame"):
        model.build_schedule(policy(), segment.spec(10000), without, sources.hourly)


def adapter_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    omit: int | None = None,
    conflict: bool = False,
) -> Path:
    sources = fixture(days=2)
    segment = sources.segments[0]
    boundary = START + DAY
    funding = {START + 8 * HOUR + 17: (0.001, 100.0)}
    frames = {f.timestamp_ms: f for f in segment.frames}
    frames[START + 8 * HOUR + 17] = Frame(START + 8 * HOUR + 17, {BTC: 100.0}, {BTC: 0.001}, {})
    if omit is not None:
        del frames[omit]
    first = [f for t, f in sorted(frames.items()) if t <= boundary]
    second = [f for t, f in sorted(frames.items()) if t > boundary]
    second.insert(0, Frame(boundary, {}, {BTC: 0.0}, {}))
    if conflict:
        second[0] = Frame(boundary, {BTC: 101.0}, {BTC: 0.0}, {BTC: 99.0})
    local: list[
        tuple[str, list[Frame], dict[int, float], dict[int, tuple[float, float]], frozenset[int]]
    ] = [
        (
            "a",
            first,
            sources.hourly,
            {t: v for t, v in segment.minute_extremes.items() if t <= boundary},
            frozenset(),
        ),
        (
            "b",
            second,
            sources.hourly,
            {t: v for t, v in segment.minute_extremes.items() if t > boundary},
            frozenset(),
        ),
    ]
    monkeypatch.setattr(source, "START", START)
    monkeypatch.setattr(source, "END", segment.end_ms)
    monkeypatch.setattr(source, "INTERVALS", ((START, segment.end_ms),))
    monkeypatch.setattr(source, "BAD_DAYS", ())
    monkeypatch.setattr(source, "check_index", lambda body, verify_raw: {})
    monkeypatch.setattr(source, "funding_censuses", lambda body: (funding, ()))
    monkeypatch.setattr(source, "daily_months", lambda body: iter(local))
    p = tmp_path / "input.json"
    p.write_text("{}")
    monkeypatch.setattr(source, "INPUT_SHA256", hashlib.sha256(p.read_bytes()).hexdigest())
    return p


def test_adapter_keeps_midday_plus1_plus2_funding_and_month_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = source.load(adapter_fixture(monkeypatch, tmp_path))
    frames = {f.timestamp_ms: f for f in sources.segments[0].frames}
    assert {
        START + 13 * HOUR + MINUTE,
        START + 13 * HOUR + 2 * MINUTE,
        START + 8 * HOUR + 17,
    } <= set(frames)
    assert frames[START + DAY].marks == {BTC: 100.0}
    assert (
        len(sources.hourly) == 769
        and sources.source_summary["required_plus1_plus2_references"] == 96
    )
    assert sources.source_summary["retained_frames"] == 146


@pytest.mark.parametrize("delay", [MINUTE, 2 * MINUTE])
def test_adapter_rejects_any_missing_hourly_reference_even_without_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, delay: int
) -> None:
    p = adapter_fixture(monkeypatch, tmp_path, omit=START + 13 * HOUR + delay)
    with pytest.raises(Exception, match="every hourly"):
        source.load(p)


def test_adapter_rejects_conflicting_shared_month_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(Exception, match="shared month boundary"):
        source.load(adapter_fixture(monkeypatch, tmp_path, conflict=True))


def chains(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Sources, dict[tuple[int, str], dict[str, Any]], dict[str, Any]]:
    whole = fixture(days=5)
    hourly = whole.hourly
    full = whole.segments[0]
    segments = tuple(
        Segment(
            START + d * DAY,
            START + (d + 1) * DAY,
            tuple(
                f for f in full.frames if START + d * DAY <= f.timestamp_ms <= START + (d + 1) * DAY
            ),
            {
                t: v
                for t, v in full.minute_extremes.items()
                if START + d * DAY < t <= START + (d + 1) * DAY
            },
        )
        for d in (0, 2, 4)
    )
    sources = Sources(segments, hourly, {})
    folds = {
        "development": (START, START + 2 * DAY),
        "diagnostic_2025": (START + 2 * DAY, START + 4 * DAY),
        "diagnostic_2026": (START + 4 * DAY, START + 5 * DAY),
    }
    monkeypatch.setattr(cli, "START", START)
    monkeypatch.setattr(cli, "END", START + 5 * DAY)
    monkeypatch.setattr(cli, "FOLDS", folds)
    expected = {}
    summaries = {}
    for cost in ("base", "stress"):
        account = Account(start_ms=START, end_ms=START + 5 * DAY, folds=folds)
        for i, segment in enumerate(segments):
            spec = segment.spec(account.equity)
            if cost == "stress":
                spec = replace(
                    spec,
                    instruments=tuple(
                        replace(x, fee_bps=9, slippage_bps=4) for x in spec.instruments
                    ),
                )
            schedule = original.build_schedule(
                original.RegimeConfig("locked", 720, False), spec, segment.frames, hourly
            )
            result = original.evaluate_case(
                spec, segment.frames, schedule.targets, segment.minute_extremes
            )
            expected[i, cost] = {k: result[k] for k in cli.FINANCIAL_KEYS}
            account.add(result, segment.frames)
        summaries[cost] = account.summary()
    return sources, expected, summaries


def test_serial_16_account_96_case_run_releases_points_and_preserves_all_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources, expected, old = chains(monkeypatch)
    references: list[weakref.ReferenceType[Account]] = []

    class Tracked(Account):
        def __init__(self, **kwargs: Any) -> None:
            gc.collect()
            assert not any(ref() is not None for ref in references)
            super().__init__(**kwargs)
            references.append(weakref.ref(self))

    monkeypatch.setattr(cli, "Account", Tracked)
    build = model.build_schedule

    def after_baseline(*args: Any, **kwargs: Any) -> original.RegimeSchedule:
        assert all(
            (tmp_path / "run" / f"{cli.BASELINE}-{cost}-summary.json").is_file()
            for cost in ("base", "stress")
        )
        return build(*args, **kwargs)

    monkeypatch.setattr(model, "build_schedule", after_baseline)
    with PrivateOutput(tmp_path / "run") as output:
        result = cli.run(sources, output, expected, old)
    assert len(references) == 32 and not any(ref() is not None for ref in references)
    assert (
        len(result["accounts"]) == 16
        and result["case_attempts"] == result["expected_segment_cost_cases"] == 96
    )
    assert result["selector"] is None and result["programme_unique_configurations"] == 36
    assert result["baseline_reproduced"] is True
    assert len(list((tmp_path / "run").glob("*-case.json.gz"))) == 96
    for cost in ("base", "stress"):
        row = result["accounts"][cli.BASELINE][cost]
        assert row["final_equity"] == pytest.approx(old[cost]["final_equity"])
        assert (
            sum(x["completed_trades"] for x in row["completed_trades_per_UTC_day"])
            == row["complete_trades"]
        )
        assert len(row["daily_equity_returns"]) == 5
        assert "hourly_sampled_max_drawdown" not in row
        assert row["risk_invalid"] is False
    candidate = result["accounts"][model.configs()[0].name]
    assert "reject_cost" in result["dispositions"][model.configs()[0].name]
    candidate["base"]["hourly_plus_execution_funding_sampled_max_drawdown"] = 0.16
    assert "reject_drawdown" in cli.dispositions(
        candidate
    ) and "risk_invalid" not in cli.dispositions(candidate)


def test_baseline_mismatch_fails_before_other_accounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources, expected, old = chains(monkeypatch)
    expected[0, "base"]["trades"][0]["fee"] += 1
    monkeypatch.setattr(
        model,
        "build_schedule",
        lambda *args, **kwargs: pytest.fail("new alpha read before baseline parity"),
    )
    with (
        PrivateOutput(tmp_path / "run") as output,
        pytest.raises(Exception, match="baseline exact"),
    ):
        cli.run(sources, output, expected, old)
    assert len(list((tmp_path / "run").glob("*-case.json.gz"))) == 1
    assert not list((tmp_path / "run").glob("cash-*-summary.json"))


def test_failed_chain_does_not_restart_or_fabricate_zero_days(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources, expected, old = chains(monkeypatch)
    evaluate = original.evaluate_case
    calls = 0

    def fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 7:
            raise ArithmeticError("synthetic partial state lost")
        return evaluate(*args, **kwargs)

    monkeypatch.setattr(original, "evaluate_case", fail)
    with PrivateOutput(tmp_path / "run") as output:
        result = cli.run(sources, output, expected, old)
    row = result["accounts"]["cash"]["base"]
    assert row["failed"] and row["final_equity"] is None and row["net_return"] is None
    assert row["no_trade_calendar_days"] is None and row["daily_equity_returns"] == []
    assert (
        result["case_attempts"] == 94 and len(list((tmp_path / "run").glob("*-blocked.json"))) == 2
    )


def test_year_marked_carry_is_not_an_episode_pnl_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = fixture(days=3)
    segment = sources.segments[0]
    schedule = model.build_schedule(policy(), segment.spec(10000), segment.frames, sources.hourly)
    frames = tuple(
        replace(f, marks={BTC: 110.0 if f.timestamp_ms >= START + DAY else 100.0})
        for f in segment.frames
    )
    extremes = {
        t: (110.0, 110.0) if t >= START + DAY else (100.0, 100.0) for t in segment.minute_extremes
    }
    case = original.evaluate_case(segment.spec(10000), frames, schedule.targets, extremes)
    folds = {"before": (START, START + DAY), "after": (START + DAY, segment.end_ms)}
    account = Account(start_ms=START, end_ms=segment.end_ms, folds=folds)
    account.add(case, frames)
    summary = account.summary()
    assert (
        summary["folds"]["before"]["closed_episodes"] == 0
        and summary["folds"]["before"]["net_return"] > 0
    )
    assert summary["folds"]["after"]["initial_equity"] == summary["folds"]["before"]["final_equity"]
    assert len(schedule.targets) == 1 and case["complete_trades"] == 1


def test_exact_funding_and_terminal_ownership_use_actual_initial_mark() -> None:
    sources = fixture()
    segment = sources.segments[0]
    entry = START + MINUTE
    rates = {entry: 0.2, entry + 17: 0.001, segment.end_ms: 0.002}
    frames = {f.timestamp_ms: f for f in segment.frames}
    for t, rate in rates.items():
        frames[t] = Frame(t, {BTC: 100.0}, {BTC: rate}, {BTC: 99.0} if t % MINUTE == 0 else {})
    ordered = tuple(frames[t] for t in sorted(frames))
    segment = replace(segment, frames=ordered)
    case = original.evaluate_case(
        segment.spec(10000), ordered, (Target(START, entry, {BTC: 0.5}),), segment.minute_extremes
    )
    q = 10000 * 0.5 / 99
    assert case["total_funding"] == pytest.approx(-q * 100 * 0.003)
    assert case["episodes"][0]["initial_margin"] == pytest.approx(q * 100 / 2)
    assert case["trades"][-1]["timestamp_ms"] == segment.end_ms


@pytest.mark.parametrize("mode", ["LIVE", "SHADOW", "TESTNET", "PAPER "])
def test_mode_gate_before_source_and_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setattr(cli, "identity", lambda commit: pytest.fail("source I/O reached"))
    with pytest.raises(Exception, match="PAPER"):
        cli.main(
            [
                "--input",
                str(tmp_path / "input"),
                "--baseline-index",
                str(tmp_path / "base"),
                "--output",
                str(tmp_path / "run"),
                "--source-commit",
                "a" * 40,
                "--expected-plan-sha256",
                "b" * 64,
            ]
        )
    assert not (tmp_path / "run").exists()


def test_source_and_baseline_pins_refuse_before_financial_labels(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{}")
    with pytest.raises(Exception, match="pinned input"):
        source.load(p)
    with pytest.raises(Exception, match="baseline index"):
        cli.baseline(p)
    assert not cli.equivalent({"price": math.nan}, {"price": math.nan})


def test_private_failure_evidence_is_create_only_before_any_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setattr(cli, "identity", lambda commit: {})
    monkeypatch.setattr(cli, "run", lambda *args: pytest.fail("financial labels reached"))
    p = tmp_path / "input.json"
    p.write_text("{}")
    output = tmp_path / "run"
    argv = [
        "--input",
        str(p),
        "--baseline-index",
        str(tmp_path / "baseline.json"),
        "--output",
        str(output),
        "--source-commit",
        "a" * 40,
        "--expected-plan-sha256",
        hashlib.sha256(source.PLAN.read_bytes()).hexdigest(),
    ]
    with pytest.raises(Exception, match="pinned input"):
        cli.main(argv)
    assert (output / "failure.json").is_file() and not (output / "result.json").exists()
    before = (output / "claim.json").read_bytes()
    with pytest.raises(Exception, match="exist"):
        cli.main(argv)
    assert (output / "claim.json").read_bytes() == before
