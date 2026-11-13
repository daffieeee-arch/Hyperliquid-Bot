"""Independent arithmetic, selection and fail-closed continuation examples."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from research import regime_cli as cli
from research.harness.errors import IntegrityError
from research.regime_io import Segment, Sources
from research.repaired_accounting import Frame
from research.two_models_io import BTC, DAY, HOUR, MINUTE


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> int:
    end = 6 * DAY
    monkeypatch.setattr(cli, "START", 0)
    monkeypatch.setattr(cli, "END", end)
    monkeypatch.setattr(
        cli,
        "FOLDS",
        {
            "development": (0, 2 * DAY),
            "validation": (2 * DAY, 4 * DAY),
            "diagnostic": (4 * DAY, end),
        },
    )
    return end


def arithmetic_case(end: int) -> tuple[dict[str, Any], tuple[Frame, ...]]:
    # Buy10 at100, pay1entryfee; exactly1funding atDAY+15ms;
    # sell10at100, pay1exitfee. Account must finish9997, not10000.
    settlement = DAY + 15
    times = sorted({*range(0, end + 1, HOUR), settlement})
    frames, points = [], []
    for instant in times:
        quantity = 10 if HOUR <= instant < end else 0
        cash = 10_000.0
        if instant >= HOUR:
            cash -= 1001
        if instant >= settlement:
            cash -= 1
        if instant == end:
            cash += 999
        points.append(
            {
                "timestamp_ms": instant,
                "cash": cash,
                "equity": cash + 100 * quantity,
                "positions": {BTC: quantity} if quantity else {},
            }
        )
        frames.append(
            Frame(instant, {BTC: 100}, {BTC: 0.001 if instant == settlement else 0}, {BTC: 100})
        )
    trades = [
        {
            "timestamp_ms": t,
            "quantity_delta": q,
            "mark": 100.0,
            "fill_price": 100.0,
            "reference_price": 100.0,
            "fee": 1.0,
            "slippage_cost": 0.0,
        }
        for t, q in [(HOUR, 10.0), (end, -10.0)]
    ]
    case = {
        "initial_equity": 10_000,
        "final_equity": 9997,
        "total_funding": -1.0,
        "ledger": points,
        "trades": trades,
        "risk_events": [],
        "episodes": [
            {
                "entry_ms": HOUR,
                "exit_ms": end,
                "target_met": False,
                "side": 1,
                "net_pnl": -3,
                "initial_margin": 500,
                "net_margin_roi": -0.006,
            }
        ],
    }
    return case, tuple(frames)


def eligible(score: float = 1.0) -> dict[str, Any]:
    return {
        "base": {
            "failed": False,
            "risk_invalid": False,
            "hourly_sampled_max_drawdown": 0.1,
            "folds": {
                name: {
                    "net_return": 0.02,
                    "daily_sharpe": score,
                    "nonflat_calendar_days": 60,
                    "closed_episodes": 5,
                    "risk_invalid": False,
                }
                for name in cli.FOLDS
            },
        },
        "stress": {
            "failed": False,
            "risk_invalid": False,
            "hourly_sampled_max_drawdown": 0.11,
            "folds": {
                name: {
                    "net_return": 0.01,
                    "daily_sharpe": score,
                    "nonflat_calendar_days": 60,
                    "closed_episodes": 5,
                    "risk_invalid": False,
                }
                for name in cli.FOLDS
            },
        },
    }


def test_native_exact_funding_cash_and_marked_fold_carry(clock: int) -> None:
    case, frames = arithmetic_case(clock)
    account = cli.Account()
    account.add(case, frames)
    summary = account.summary()
    assert summary["final_equity"] == 9997
    assert summary["net_return"] == pytest.approx(-0.0003)
    assert summary["fee_cost"] == 2
    assert summary["total_funding"] == -1
    assert summary["same_quantity_gross_pnl"] == pytest.approx(0)
    assert summary["nonflat_calendar_days"] == 6
    assert summary["folds"]["development"]["final_equity"] == 9998
    assert summary["folds"]["validation"]["initial_equity"] == 9998
    assert summary["folds"]["diagnostic"]["final_equity"] == 9997
    assert summary["folds"]["development"]["closed_episodes"] == 0
    assert summary["folds"]["diagnostic"]["closed_episodes"] == 1
    product = math.prod(1 + f["net_return"] for f in summary["folds"].values())
    assert product == pytest.approx(9997 / 10000)


def test_failed_segment_does_not_report_earlier_equity_as_failure_equity(clock: int) -> None:
    case, frames = arithmetic_case(clock)
    account = cli.Account()
    account.add(case, frames)
    account.fail(ValueError("hypothetical next segment insolvency"))
    summary = account.summary()
    assert summary["last_completed_segment_equity"] == 9997
    assert (
        summary["final_equity"]
        is summary["net_return"]
        is summary["same_quantity_gross_pnl"]
        is None
    )
    assert "unreconstructed" in summary["financial_scope"]
    assert summary["no_trade_calendar_days"] is None
    assert summary["folds"]["development"]["final_equity"] == 9998
    with pytest.raises(IntegrityError, match="cannot silently resume"):
        account.add(case, frames)


def test_no_failed_segment_can_restore_original_capital(clock: int) -> None:
    case, frames = arithmetic_case(clock)
    case["initial_equity"] = 9900
    with pytest.raises(IntegrityError, match="carry actual"):
        cli.Account().add(case, frames)


def test_source_funding_mismatch_is_not_success(clock: int) -> None:
    case, frames = arithmetic_case(clock)
    case["total_funding"] = 0
    account = cli.Account()
    with pytest.raises(IntegrityError, match="funding events reconcile"):
        account.add(case, frames)
    assert account.funding == account.points == account.episodes == account.trades == []
    assert account.equity == cli.INITIAL


def test_future_winning_or_failed_flags_cannot_pick_another_development_winner() -> None:
    first, second = eligible(2), eligible(1)
    before = cli.finalist({"first": first, "second": second})
    for cost in ("base", "stress"):
        first[cost]["failed"] = True
        first[cost]["risk_invalid"] = True
        first[cost]["folds"]["validation"]["net_return"] = -0.99
    assert before == cli.finalist({"first": first, "second": second}) == "first"
    assert cli.assessment(first) == "failed_preserved_evidence"


def test_unknown_development_forbids_a_runner_up_selected_by_future_failure() -> None:
    first, second = eligible(2), eligible(1)
    del first["stress"]["folds"]["development"]
    assert cli.finalist({"first": first, "second": second}) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("net_return", -0.1),
        ("daily_sharpe", None),
        ("risk_invalid", True),
        ("closed_episodes", 2),
        ("nonflat_calendar_days", 29),
    ],
)
def test_no_nonpositive_or_insufficient_development_finalist(field: str, value: Any) -> None:
    trial = eligible()
    trial["stress"]["folds"]["development"][field] = value
    assert cli.finalist({"one": trial}) is None


def test_ties_and_cost_cases_have_fixed_development_ranking() -> None:
    first, second = eligible(2), eligible(1)
    first["stress"]["folds"]["development"]["daily_sharpe"] = 0.5
    assert cli.finalist({"first": first, "second": second}) == "second"
    assert cli.finalist({"z": eligible(), "a": eligible()}) == "a"


def test_missing_fold_or_nonfinite_return_cannot_be_positive_candidate() -> None:
    trial = eligible()
    trial["base"]["folds"] = {}
    assert cli.assessment(trial) == "insufficient_incomplete_folds"
    for value in (math.nan, math.inf):
        trial = eligible()
        trial["base"]["folds"]["diagnostic"]["net_return"] = value
        assert cli.assessment(trial) == "failed_invalid_financial_summary"


def test_positive_return_cannot_erase_collateral_or_drawdown_failure() -> None:
    trial = eligible()
    assert cli.assessment(trial).startswith("positive_retrospective")
    trial["stress"]["hourly_sampled_max_drawdown"] = 0.151
    assert cli.assessment(trial) == "reject_drawdown"
    trial["base"]["risk_invalid"] = True
    assert cli.assessment(trial) == "counterfactual_risk_invalid"


def test_shared_bootstrap_is_deterministic_and_zero_difference_is_zero() -> None:
    values = [0.001, -0.002, 0.003] * 12
    one = cli.uncertainty(values, 7)
    assert one == cli.uncertainty(values, 7)
    assert one["annualized_arithmetic_mean"] == pytest.approx(365 * sum(values) / len(values))
    zero = cli.uncertainty([0.0] * 36, 30)
    assert zero["lower_95"] == zero["upper_95"] == 0
    assert cli.sharpe([0.0] * 36) is None


@pytest.mark.parametrize("mode", ["LIVE", "SHADOW", "TESTNET", "paper", ""])
def test_unsafe_mode_is_refused_before_source_or_output_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    output = tmp_path / "out"
    with pytest.raises(UnsafeTradingModeError):
        cli.main(
            [
                "--input",
                str(tmp_path / "absent"),
                "--output",
                str(output),
                "--source-commit",
                "a" * 40,
                "--expected-plan-sha256",
                "b" * 64,
            ]
        )
    assert not output.exists()


def test_full_synthetic_cli_pays_both_sides_and_preserves_output_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: int
) -> None:
    times = sorted(
        {
            *range(0, clock + 1, HOUR),
            *(d + minute * MINUTE for d in range(0, clock, DAY) for minute in (1, 2)),
        }
    )
    frames = tuple(Frame(t, {BTC: 100.0}, {BTC: 0.0}, {BTC: 100.0}) for t in times)
    hourly = {t: 100 * math.exp(t / HOUR * 0.0001) for t in range(-720 * HOUR, clock + 1, HOUR)}
    source = Sources(
        (Segment(0, clock, frames, {t: (100.0, 100.0) for t in range(MINUTE, clock + 1, MINUTE)}),),
        hourly,
        {"synthetic": True},
    )
    monkeypatch.setattr(cli, "code_identity", lambda sha: {"source": "known_fixture"})
    monkeypatch.setattr(cli, "load", lambda path: source)
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    plan = tmp_path / "plan.md"
    plan.write_text("synthetic protocol")
    monkeypatch.setattr(cli, "PLAN", plan)
    inp = tmp_path / "input.json"
    inp.write_text("synthetic input")
    monkeypatch.setattr(cli, "INPUT_SHA256", hashlib.sha256(inp.read_bytes()).hexdigest())
    output = tmp_path / "out"
    args = [
        "--input",
        str(inp),
        "--output",
        str(output),
        "--source-commit",
        "a" * 40,
        "--expected-plan-sha256",
        hashlib.sha256(plan.read_bytes()).hexdigest(),
    ]
    assert cli.main(args) == 0
    result = json.loads((output / "result.json").read_text())
    assert result["promotion_decision"] == "forbidden"
    assert result["development_selected_finalist"] is None
    assert len(result["trials"]) == 4
    first = next(iter(result["trials"].values()))
    assert first["base"]["final_equity"] == pytest.approx(9993.5)
    assert first["stress"]["final_equity"] == pytest.approx(9987)
    assert json.loads((output / "completion.json").read_text())["failed_configurations"] == 0
    with pytest.raises(FileExistsError):
        cli.main(args)
    assert json.loads((output / "result.json").read_text()) == result
