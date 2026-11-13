"""Locked-candidate, calendar, terminal cash and startup counterexamples."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from research import regime_momentum as model
from research import regime_recent_cli as cli
from research.harness.errors import IntegrityError
from research.regime_cli import Account
from research.regime_io import Segment, Sources
from research.repaired_accounting import Frame
from research.two_models_io import BTC, DAY, HOUR, MINUTE


def synthetic_source(*, funding: float = 0.0) -> Sources:
    end = 6 * DAY
    times = sorted(
        {
            *range(0, end + 1, HOUR),
            *(day + minute * MINUTE for day in range(0, end, DAY) for minute in (1, 2)),
        }
    )
    frames = tuple(
        Frame(
            t,
            {BTC: 110.0 if t == end and funding else 100.0},
            {BTC: funding if t == end else 0.0},
            {BTC: 100.0},
        )
        for t in times
    )
    hours = {t: 100 * math.exp(t / HOUR * 0.0001) for t in range(-720 * HOUR, end + 1, HOUR)}
    bounds = {t: (100.0, 100.0) for t in range(MINUTE, end + 1, MINUTE)}
    return Sources((Segment(0, end, frames, bounds),), hours, {"synthetic": True})


@pytest.fixture
def execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(cli, "START", 0)
    monkeypatch.setattr(cli, "END", 6 * DAY)
    monkeypatch.setattr(cli, "identity", lambda sha: {"synthetic_immutable_code": "known"})
    monkeypatch.setattr(cli, "load", lambda path: synthetic_source())
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    plan = tmp_path / "plan.md"
    plan.write_text("synthetic diagnostic protocol")
    monkeypatch.setattr(cli, "PLAN", plan)
    lock = tmp_path / "candidate.json"
    lock.write_text(
        json.dumps(
            {
                "source_commit": cli.FROZEN_SOURCE,
                "name": "regime-h720-plain",
                "lookback_hours": 720,
                "slow_confirmation": False,
            }
        )
    )
    monkeypatch.setattr(cli, "CANDIDATE_LOCK_SHA256", hashlib.sha256(lock.read_bytes()).hexdigest())
    inp = tmp_path / "input.json"
    inp.write_text("synthetic qualified sources")
    monkeypatch.setattr(cli, "INPUT_SHA256", hashlib.sha256(inp.read_bytes()).hexdigest())
    return [
        "--input",
        str(inp),
        "--candidate-lock",
        str(lock),
        "--output",
        str(tmp_path / "result"),
        "--source-commit",
        "a" * 40,
        "--expected-plan-sha256",
        hashlib.sha256(plan.read_bytes()).hexdigest(),
    ]


def read_result(args: list[str]) -> dict[str, Any]:
    value = json.loads((Path(args[args.index("--output") + 1]) / "result.json").read_text())
    assert isinstance(value, dict)
    return value


def test_new_calendar_does_not_inherit_974_days_or_development_selection(
    execution: list[str],
) -> None:
    assert cli.main(execution) == 0
    result = read_result(execution)
    assert result["calendar_days"] == 6
    assert result["config"] == {
        "name": "regime-h720-plain",
        "lookback_hours": 720,
        "confirmed": False,
    }
    assert result["new_strategy_configurations"] == 0
    assert result["new_selection_permitted"] is False
    assert "development_selected_finalist" not in result
    assert len(result["controls"]) == 5
    case = result["trial"]["base"]
    assert set(case["folds"]) == {"recent_diagnostic"}
    assert len(case["daily_equity_returns"]) == 6
    assert case["nonflat_calendar_days"] == 6
    assert case["no_trade_calendar_days"] == 0
    assert case["final_equity"] == pytest.approx(9993.5)
    assert result["trial"]["stress"]["final_equity"] == pytest.approx(9987)
    assert result["diagnostic_verdict"] == "not_positive_under_both_costs"
    assert result["fresh_oos"] is False and result["promotion_decision"] == "forbidden"
    with pytest.raises(FileExistsError):
        cli.main(execution)
    assert read_result(execution) == result


def test_exact_terminal_event_charges_settlement_mark_before_close(
    execution: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load", lambda path: synthetic_source(funding=0.0001))
    assert cli.main(execution) == 0
    case = read_result(execution)["trial"]["base"]
    # q50BTC * native settlement mark110 * rate.0001 = .55, not .50
    # and not zero. Flat price execution still pays6.50 trading costs.
    assert case["total_funding"] == pytest.approx(-0.55)
    assert case["final_equity"] == pytest.approx(9992.95)
    assert case["episodes"][0]["initial_margin"] == pytest.approx(2500)


def test_failed_candidate_cannot_turn_into_a_control_or_reset(
    execution: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluate = model.evaluate_case
    attempts = 0

    def fail_once(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise IntegrityError("synthetic", "unknown hypothetical held state")
        return evaluate(*args, **kwargs)

    monkeypatch.setattr(model, "evaluate_case", fail_once)
    assert cli.main(execution) == 2
    result = read_result(execution)
    assert attempts == 12
    assert result["diagnostic_verdict"] == "failed_preserved_evidence"
    assert result["trial"]["base"]["final_equity"] is None
    assert result["trial"]["base"]["net_return"] is None
    assert result["trial"]["base"]["no_trade_calendar_days"] is None
    assert result["controls"]["cash"]["base"]["final_equity"] == 10000
    assert not result["new_selection_permitted"]


def test_modified_candidate_lock_is_rejected_before_output(execution: list[str]) -> None:
    lock = Path(execution[execution.index("--candidate-lock") + 1])
    lock.write_text('{"name":"regime-h168-confirmed"}')
    with pytest.raises(IntegrityError, match="frozen candidate lock"):
        cli.main(execution)
    assert not Path(execution[execution.index("--output") + 1]).exists()


def test_plan_mismatch_is_rejected_before_output(execution: list[str]) -> None:
    execution[-1] = "0" * 64
    with pytest.raises(IntegrityError, match="protocol hash"):
        cli.main(execution)
    assert not Path(execution[execution.index("--output") + 1]).exists()


@pytest.mark.parametrize("mode", ["LIVE", "SHADOW", "TESTNET", "paper", ""])
def test_mode_refusal_precedes_identity_and_output(
    execution: list[str], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setattr(cli, "identity", lambda sha: pytest.fail("source read before safe mode"))
    with pytest.raises(UnsafeTradingModeError):
        cli.main(execution)
    assert not Path(execution[execution.index("--output") + 1]).exists()


@pytest.mark.parametrize(
    "folds",
    [
        {},
        {"one": (0, DAY), "two": (2 * DAY, 3 * DAY)},
        {"one": (0, 2 * DAY), "two": (DAY, 3 * DAY)},
        {"one": (DAY, 3 * DAY)},
        {"one": (0, 2 * DAY)},
    ],
)
def test_context_folds_cannot_hide_missing_or_overlapping_days(
    folds: dict[str, tuple[int, int]],
) -> None:
    with pytest.raises(IntegrityError, match=r"fold|partition"):
        Account(start_ms=0, end_ms=3 * DAY, folds=folds)


@pytest.mark.parametrize("start,end", [(True, DAY), (-DAY, DAY), (MINUTE, DAY), (DAY, DAY)])
def test_context_requires_exact_ordered_utc_days(start: int, end: int) -> None:
    with pytest.raises(IntegrityError, match="UTC research account"):
        Account(start_ms=start, end_ms=end, folds={"recent": (start, end)})


def test_mismatched_source_period_fails_before_mutating_account() -> None:
    account = Account(start_ms=2 * DAY, end_ms=3 * DAY, folds={"recent": (2 * DAY, 3 * DAY)})
    source = synthetic_source().segments[0]
    with pytest.raises(IntegrityError, match="account interval"):
        account.add({"initial_equity": 10000}, source.frames)
    assert account.equity == 10000 and not account.funding and not account.points


def test_incomplete_period_cannot_be_classified_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "START", 0)
    monkeypatch.setattr(cli, "END", DAY)
    case = {
        "failed": False,
        "risk_invalid": False,
        "complete_trades": 1,
        "net_return": 0.1,
        "folds": {},
        "daily_equity_returns": [],
    }
    assert (
        cli.verdict({"base": case, "stress": case}) == "insufficient_incomplete_diagnostic_period"
    )


def test_frozen_alpha_file_mismatch_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "src/research").mkdir(parents=True)
    plan = root / "plan.md"
    plan.write_bytes(b"registered protocol")
    for name in ("regime_momentum.py", "repaired_accounting.py"):
        (root / "src/research" / name).write_bytes(b"frozen alpha")
    monkeypatch.setattr(cli, "ROOT", root)
    monkeypatch.setattr(cli, "PLAN", plan)
    monkeypatch.setattr(cli, "code_identity", lambda sha: {})

    def committed(args: list[str], **kwargs: Any) -> bytes:
        return b"registered protocol" if args[-1].endswith(":plan.md") else b"frozen alpha"

    monkeypatch.setattr(subprocess, "check_output", committed)
    assert "plan.md" in cli.identity("a" * 40)
    (root / "src/research/regime_momentum.py").write_bytes(b"retuned alpha")
    with pytest.raises(IntegrityError, match="byte-identical"):
        cli.identity("a" * 40)
