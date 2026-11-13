"""Economic aggregation, selection leakage and execution-boundary fixtures."""

from __future__ import annotations

import gzip
import json
import stat
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from research import two_models_cli as cli
from research.btc_flow_io import PrivateOutput
from research.harness.errors import IntegrityError

TIME_NAME = "daily-l24-forced-time"
BRACKET_NAME = "daily-l24-forced-bracket"
RUNNER_NAME = "daily-l72-filtered-time"


def daily_case(initial: float = 10000, pnl: float = 0, *, offset: int = 0) -> dict[str, Any]:
    """A closed one-unit purchase with explicit cash-compatible marked equity."""
    return {
        "initial_equity": initial,
        "final_equity": initial + pnl,
        "ledger": [
            {"timestamp_ms": offset, "equity": initial},
            {"timestamp_ms": offset + 1, "equity": initial},
            {"timestamp_ms": offset + 2, "equity": initial + pnl},
        ],
        "trades": [
            {
                "timestamp_ms": offset + 1,
                "quantity_delta": 1,
                "mark": 1000,
                "fill_price": 1000,
                "fee": 0,
            },
            {
                "timestamp_ms": offset + 2,
                "quantity_delta": -1,
                "mark": 1000 + pnl,
                "fill_price": 1000 + pnl,
                "fee": 0,
            },
        ],
        "episodes": [
            {
                "entry_ms": offset + 1,
                "side": 1,
                "net_pnl": pnl,
                "net_margin_roi": pnl / 100,
                "realized_target_success": pnl >= 20,
            }
        ],
        "fee_cost": 0.0,
        "slippage_cost": 0.0,
        "total_funding": 0.0,
        "same_quantity_gross_price_return": pnl / initial,
        "calendar_days": 1,
        "unavailable_data_days": 0,
        "liquidation_possible": False,
    }


def test_totals_refuses_monthly_reset_instead_of_restarting_losing_account() -> None:
    state = cli.Totals()
    state.add(daily_case(pnl=-100), "development", "2024", is_micro=False)
    with pytest.raises(IntegrityError, match="equity carry"):
        state.add(daily_case(pnl=50, offset=10), "development", "2024", is_micro=False)
    assert state.equity == 9900
    state.add(daily_case(initial=9900, pnl=50, offset=10), "development", "2024", is_micro=False)
    assert state.result()["final_equity"] == 9950
    assert state.result()["folds"]["development"]["net_return"] == pytest.approx(-0.005)


def test_global_drawdown_retains_preexecution_peak_across_months_and_folds() -> None:
    first = daily_case()
    # Price marks at2,000 immediately before the1,000 execution, then heals:
    # every retained POSTexecution endpoint is10,000 but the real peak is11,000.
    first["trades"][-1]["mark"] = 2000
    state = cli.Totals()
    state.add(first, "development", "2024", is_micro=False)
    assert state.result()["max_drawdown"] == pytest.approx(1 - 10000 / 11000)
    state.add(daily_case(pnl=-100, offset=10), "validation", "2025", is_micro=False)
    result = state.result()
    assert result["max_drawdown"] == pytest.approx(0.10)
    assert result["folds"]["validation"]["max_drawdown"] == pytest.approx(0.01)
    assert result["years"]["2025"]["max_drawdown"] == pytest.approx(0.01)
    assert result["folds"]["development"]["final_equity"] == 10000


def test_calendar_target_denominator_includes_missing_and_no_signal_days() -> None:
    case = daily_case(pnl=25)
    case.update(calendar_days=3, unavailable_data_days=1)
    state = cli.Totals()
    state.add(case, "development", "2024", is_micro=False)
    result = state.result()
    assert result["complete_trades"] == result["target_attainment_count"] == 1
    assert result["target_attainment_fraction_of_trades"] == 1
    assert result["calendar_days"] == 3 and result["unavailable_data_days"] == 1
    assert result["no_trade_days"] == 2
    assert result["target_attainment_fraction_of_calendar_days"] == pytest.approx(1 / 3)


def test_daily_gross_net_cost_and_signed_funding_reconcile_in_usdc() -> None:
    case = daily_case(pnl=85)
    case.update(
        fee_cost=4,
        slippage_cost=6,
        total_funding=-5,
        same_quantity_gross_price_return=0.01,
        ledger=[
            {"timestamp_ms": 0, "equity": 10000},
            {"timestamp_ms": 1, "equity": 9996},
            {"timestamp_ms": 2, "equity": 10085},
        ],
        trades=[
            {"timestamp_ms": 1, "quantity_delta": 10, "mark": 100, "fill_price": 100.2, "fee": 2},
            {"timestamp_ms": 2, "quantity_delta": -10, "mark": 110, "fill_price": 109.6, "fee": 2},
        ],
    )
    case["episodes"][0]["net_margin_roi"] = 85 / 100.2
    state = cli.Totals()
    state.add(case, "development", "2024", is_micro=False)
    result = state.result()
    net = result["final_equity"] - result["initial_equity"]
    assert net == pytest.approx(85)
    assert result["same_quantity_gross_pnl"] == pytest.approx(100)
    assert net == pytest.approx(
        result["same_quantity_gross_pnl"]
        - result["fee_cost"]
        - result["slippage_cost"]
        + result["total_funding"]
    )
    assert result["observed_spread_cost"] == 0
    assert result["executed_turnover_notional"] == pytest.approx(2098)
    assert result["net_pnl_per_completed_trade"] == 85


def micro_case() -> dict[str, Any]:
    return {
        "status": "complete",
        "initial_equity": 10000,
        "final_equity": 10012,
        "ledger": [{"equity": 10000}, {"equity": 9996}, {"equity": 10016}, {"equity": 10012}],
        "trades": [
            {
                "entry_utc_ns": 1_000_000,
                "side": 1,
                "net_pnl": 12,
                "net_margin_return": 12 / 100.2,
                "realized_target_met": True,
            }
        ],
        "fills": [{"quantity": 10, "price": 100.2}, {"quantity": 10, "price": 101.8}],
        "fee_cost": 4,
        "slippage_cost": 2,
        "total_funding": 0,
        "same_quantity_gross_mid_pnl": 20,
        "execution_spread_and_friction_cost": 4,
        "observed_hours": 1.5,
        "counts": {"entry_nonfills": 2},
    }


def test_micro_spread_is_not_double_counted_as_friction_or_ignored() -> None:
    state = cli.Totals()
    state.add(micro_case(), "development", "2026", is_micro=True)
    result = state.result()
    assert result["same_quantity_gross_pnl"] == 20
    assert result["observed_spread_cost"] == 2 and result["slippage_cost"] == 2
    assert result["final_equity"] - 10000 == (
        result["same_quantity_gross_pnl"]
        - result["observed_spread_cost"]
        - result["slippage_cost"]
        - result["fee_cost"]
    )
    assert result["executed_turnover_notional"] == 2020
    assert result["break_even_all_execution_cost_bps_on_actual_quantities"] == pytest.approx(
        20 / 2020 * 10000
    )
    assert result["completed_trades_per_observed_hour"] == pytest.approx(1 / 1.5)
    assert result["counts"]["entry_nonfills"] == 2


def test_explicit_failure_blocks_restart_and_invalidates_profitability_metrics() -> None:
    state = cli.Totals()
    state.add(daily_case(pnl=25), "development", "2024", is_micro=False)
    state.fail(IntegrityError("fixture", "held data gap"), "validation")
    result = state.result()
    assert result["failed"] and result["folds"]["validation"]["failed"]
    assert result["same_quantity_gross_pnl"] is None
    assert result["net_pnl_per_completed_trade"] is None
    assert result["total_funding"] is None
    with pytest.raises(IntegrityError, match="cannot restart"):
        state.add(daily_case(initial=10025), "diagnostic", "2026", is_micro=False)


def test_failed_unclosed_short_preserves_mark_equity_not_apparent_cash_profit() -> None:
    case = micro_case()
    case.update(
        status="failed",
        error="unknown held exit at input boundary",
        final_equity=9999.5,
        trades=[],
        fills=[{"quantity": 50, "price": 100}],
        ledger=[{"equity": 10000, "cash": 10000}, {"equity": 9999.5, "cash": 14999.5}],
        fee_cost=0.5,
        slippage_cost=0,
        total_funding=None,
        same_quantity_gross_mid_pnl=None,
        execution_spread_and_friction_cost=0,
        open_position={"side": -1, "quantity": 50},
    )
    state = cli.Totals()
    state.add(case, "development", "2026", is_micro=True)
    result = state.result()
    assert result["failed"] and result["final_equity"] == 9999.5
    assert result["net_return"] < 0 and result["complete_trades"] == 0
    assert result["same_quantity_gross_pnl"] is None
    with pytest.raises(IntegrityError, match="cannot restart"):
        state.add(micro_case(), "validation", "2026", is_micro=True)


def scored(base: float = 0.05, stress: float = 0.03, *, recent: bool = False) -> dict[str, Any]:
    groups = ("development", "validation", "diagnostic") + (
        ("recent_diagnostic",) if recent else ()
    )
    return {
        cost: {
            "failed": False,
            "risk_invalid": False,
            "net_return": 0.01,
            "folds": {
                group: {
                    "net_return": score if group == "development" else 0.01,
                    "trades": 10,
                    "failed": False,
                    "risk_invalid": False,
                }
                for group in groups
            },
        }
        for cost, score in (("base", base), ("stress", stress))
    }


def controls(base: float = 0.001, stress: float = 0.001) -> dict[str, Any]:
    return {
        name: scored(base, stress)
        for name in (
            "always_long_time",
            "always_short_time",
            "always_long_bracket",
            "always_short_bracket",
        )
    }


def test_finalist_uses_worst_development_cost_then_lexical_tie() -> None:
    trials = {
        "zeta": scored(0.10, 0.02),
        "alpha": scored(0.03, 0.02),
        "base_only": scored(1, -0.01),
    }
    assert cli.choose_finalist(trials) == "alpha"


def test_future_results_global_failures_and_risks_cannot_change_development_selection() -> None:
    trials = {"selected": scored(0.10, 0.08), "runner_up": scored(0.04, 0.03)}
    assert cli.choose_finalist(trials) == "selected"
    changed = deepcopy(trials)
    for case in changed["selected"].values():
        case.update(failed=True, risk_invalid=True, net_return=-999)
        for name in ("validation", "diagnostic"):
            case["folds"][name].update(net_return=-999, failed=True, risk_invalid=True, trades=0)
    assert cli.choose_finalist(changed) == "selected"


@pytest.mark.parametrize("defect", ["failed", "risk_invalid", "empty", "negative", "missing"])
def test_development_defects_prevent_selection(
    defect: str,
) -> None:
    trials = {"bad": scored(0.10, 0.08), "valid": scored(0.04, 0.03)}
    development = trials["bad"]["stress"]["folds"]["development"]
    if defect in {"failed", "risk_invalid"}:
        development[defect] = True
    elif defect == "empty":
        development["trades"] = 0
    elif defect == "negative":
        development["net_return"] = -0.01
    else:
        del trials["bad"]["stress"]["folds"]["development"]
    assert cli.choose_finalist(trials) == "valid"


def test_zero_trade_flat_variants_cannot_become_finalists_by_beating_losses() -> None:
    flat = scored(0, 0)
    for case in flat.values():
        case["folds"]["development"]["trades"] = 0
    assert cli.choose_finalist({"flat": flat, "loser": scored(-0.01, -0.02)}) is None


def test_future_failure_does_not_switch_admission_to_runner_up() -> None:
    trials = {TIME_NAME: scored(0.10, 0.08), RUNNER_NAME: scored(0.04, 0.03)}
    trials[TIME_NAME]["base"]["folds"]["validation"]["net_return"] = -0.01
    assessed, finalist = cli.assess(trials, controls(0.01, 0.005), "daily")
    assert finalist == TIME_NAME
    assert assessed[TIME_NAME]["verdict"] == assessed[RUNNER_NAME]["verdict"] == "reject"


def test_positive_selected_candidate_can_pass_without_an_always_noise_gate() -> None:
    assessed, finalist = cli.assess(
        {TIME_NAME: scored()},
        controls(0.01, 0.005),
        "daily",
    )
    assert finalist == TIME_NAME
    assert assessed[TIME_NAME]["verdict"] == "exploratory_candidate"


@pytest.mark.parametrize(
    "defect,expected",
    [
        ("failed", "failed"),
        ("risk_invalid", "counterfactual_risk_invalid"),
        ("empty_fold", "insufficient"),
    ],
)
def test_all_qualified_folds_and_case_failures_are_required_for_admission(
    defect: str,
    expected: str,
) -> None:
    candidate = scored()
    if defect == "empty_fold":
        candidate["stress"]["folds"]["diagnostic"]["trades"] = 0
    else:
        candidate["stress"][defect] = True
    assessed, finalist = cli.assess({TIME_NAME: candidate}, controls(), "daily")
    assert finalist == TIME_NAME
    assert assessed[TIME_NAME]["verdict"] == expected


def test_micro_requires_recent_diagnostic_group_without_selecting_from_it() -> None:
    trials = {"candidate": scored()}
    assessed, finalist = cli.assess(trials, {"momentum": scored(0.001, 0.001)}, "micro")
    assert finalist == "candidate" and assessed["candidate"]["verdict"] == "insufficient"
    assessed, _ = cli.assess(
        {"candidate": scored(recent=True)},
        {"momentum": scored(0.001, 0.001, recent=True)},
        "micro",
    )
    assert assessed["candidate"]["verdict"] == "exploratory_candidate"


@pytest.mark.parametrize(
    "reference_defect",
    ["failed", "risk_invalid", "development_failed", "development_risk", "better_development"],
)
def test_invalid_or_superior_required_benchmark_cannot_support_candidate(
    reference_defect: str,
) -> None:
    reference = scored(0.001, 0.001)
    if reference_defect == "better_development":
        reference["stress"]["folds"]["development"]["net_return"] = 0.50
    elif reference_defect == "development_failed":
        reference["stress"]["folds"]["development"]["failed"] = True
    elif reference_defect == "development_risk":
        reference["stress"]["folds"]["development"]["risk_invalid"] = True
    else:
        reference["stress"][reference_defect] = True
    references = controls()
    references["always_short_time"] = reference
    assessed, _ = cli.assess({TIME_NAME: scored()}, references, "daily")
    assert assessed[TIME_NAME]["verdict"] != "exploratory_candidate"


@pytest.mark.parametrize("name,unmatched", [(TIME_NAME, "bracket"), (BRACKET_NAME, "time")])
def test_each_exit_policy_uses_only_its_matching_directional_controls(
    name: str,
    unmatched: str,
) -> None:
    references = controls()
    for side in ("long", "short"):
        references[f"always_{side}_{unmatched}"] = scored(1.0, 1.0)
        references[f"always_{side}_{unmatched}"]["stress"]["risk_invalid"] = True
    assessed, finalist = cli.assess({name: scored()}, references, "daily")
    assert finalist == name
    assert assessed[name]["verdict"] == "exploratory_candidate"


@pytest.mark.parametrize("missing", ["long", "short", "both"])
def test_missing_matching_directional_control_cannot_vacuously_award_candidate(
    missing: str,
) -> None:
    references = controls()
    for side in ("long", "short"):
        if missing in (side, "both"):
            del references[f"always_{side}_time"]
    assessed, finalist = cli.assess({TIME_NAME: scored()}, references, "daily")
    assert finalist == TIME_NAME
    assert assessed[TIME_NAME]["verdict"] != "exploratory_candidate"


def unexpected(*args: Any, **kwargs: Any) -> Any:
    pytest.fail("operation must be rejected before data/output work")


@pytest.mark.parametrize("mode", ["LIVE", "SHADOW", "TESTNET", "paper", "", " PAPER"])
def test_run_mode_refusal_happens_before_code_or_source_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setattr(cli, "verify_code", unexpected)
    monkeypatch.setattr(cli, "read_bytes", unexpected)
    monkeypatch.setattr(cli, "PrivateOutput", unexpected)
    with pytest.raises(UnsafeTradingModeError):
        cli.run("daily", tmp_path / "input", "0" * 64, "1" * 40, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_actual_head_mismatch_refuses_before_hashes_sources_or_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "0" * 40)
    monkeypatch.setattr(cli, "implementation_hashes", unexpected)
    monkeypatch.setattr(cli, "read_bytes", unexpected)
    monkeypatch.setattr(cli, "PrivateOutput", unexpected)
    with pytest.raises(IntegrityError, match="checkout HEAD"):
        cli.run("micro", tmp_path / "input", "0" * 64, "1" * 40, tmp_path / "output")


@pytest.mark.parametrize("drift", [" M src/research/repaired_replay.py", "?? src/local_mode.py"])
def test_any_imported_source_drift_refuses_even_when_pinned_modules_were_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    replies = iter(["1" * 40, drift])
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(cli, "implementation_hashes", unexpected)
    with pytest.raises(IntegrityError, match="clean complete imported"):
        cli.verify_code("1" * 40)


def test_wrong_input_hash_refuses_before_source_decoding_or_output_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setattr(cli, "verify_code", lambda commit: {})
    monkeypatch.setattr(cli, "read_bytes", lambda path, maximum: b"{}")
    monkeypatch.setattr(cli, "document", unexpected)
    monkeypatch.setattr(cli, "PrivateOutput", unexpected)
    with pytest.raises(IntegrityError, match="operator-pinned"):
        cli.run("daily", tmp_path / "input", "0" * 64, "1" * 40, tmp_path / "output")


def test_artifact_is_deterministic_private_gzip_json_and_never_executes_payloads(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "artifacts"
    payload = {"z": 2, "a": [1, "__import__('os').system('false')"]}
    with PrivateOutput(destination) as output:
        cli.artifact(output, "first", payload)
        cli.artifact(output, "second", {"a": payload["a"], "z": 2})
        with pytest.raises(ValueError):
            cli.artifact(output, "nonfinite", {"score": math_nan()})
        with pytest.raises(IntegrityError):
            cli.artifact(output, "../escape", payload)
    first = destination / "first.json.gz"
    assert first.read_bytes() == (destination / "second.json.gz").read_bytes()
    assert json.loads(gzip.decompress(first.read_bytes())) == payload
    assert first.read_bytes()[4:8] == b"\0\0\0\0"  # No clock-dependent gzip timestamp.
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert not (destination / "nonfinite.json.gz").exists()
    assert not (tmp_path / "escape.json.gz").exists()


def math_nan() -> float:
    return float("nan")
