"""One locked candidate on exposed recent data, never a fresh-OOS selector."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from research import regime_momentum as model
from research.btc_flow_io import PrivateOutput, read_bytes
from research.harness.errors import HarnessError
from research.regime_cli import INITIAL, Account, code_identity, compressed, uncertainty
from research.regime_io import Sources
from research.regime_recent_io import (
    CANDIDATE_LOCK_SHA256,
    END,
    INPUT_SHA256,
    PLAN,
    ROOT,
    START,
    STUDY_ID,
    load,
)
from research.volume_repair_io import json_value, require

FROZEN_SOURCE = "bd4bf7c316daed18fbaf46429b579c6266ef494a"
CONFIG = model.RegimeConfig("regime-h720-plain", 720, False)
CONTROLS = ("cash", "persistent_long", "persistent_short", "matched_long", "matched_short")


def identity(commit: str) -> dict[str, str]:
    result = code_identity(commit)
    name = str(PLAN.relative_to(ROOT))
    raw = read_bytes(PLAN, 1 << 20)
    require(
        raw == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT),
        "new diagnostic protocol must equal committed bytes",
    )
    result[name] = hashlib.sha256(raw).hexdigest()
    for leaf in ("regime_momentum.py", "repaired_accounting.py"):
        name = "src/research/" + leaf
        require(
            read_bytes(ROOT / name, 1 << 20)
            == subprocess.check_output(["git", "show", f"{FROZEN_SOURCE}:{name}"], cwd=ROOT),
            "selected alpha and ledger must remain byte-identical: " + leaf,
        )
    return result


def locked_candidate(path: Path) -> dict[str, Any]:
    raw = read_bytes(path, 64 << 10)
    require(hashlib.sha256(raw).hexdigest() == CANDIDATE_LOCK_SHA256, "frozen candidate lock")
    value = json_value(raw)
    require(isinstance(value, dict), "candidate lock object")
    assert isinstance(value, dict)
    require(
        value["source_commit"] == FROZEN_SOURCE
        and value["name"] == CONFIG.name
        and value["lookback_hours"] == 720
        and value["slow_confirmation"] is False,
        "only the earlier development-selected candidate is eligible",
    )
    return value


def verdict(trial: dict[str, Any]) -> str:
    if any(trial[c]["failed"] for c in ("base", "stress")):
        return "failed_preserved_evidence"
    if any(trial[c]["risk_invalid"] for c in ("base", "stress")):
        return "counterfactual_risk_invalid"
    if any(
        set(trial[c]["folds"]) != {"recent_diagnostic"}
        or len(trial[c]["daily_equity_returns"]) != (END - START) // 86_400_000
        for c in ("base", "stress")
    ):
        return "insufficient_incomplete_diagnostic_period"
    if any(trial[c]["complete_trades"] == 0 for c in ("base", "stress")):
        return "insufficient_no_complete_trades"
    returns = [trial[c]["net_return"] for c in ("base", "stress")]
    if not all(type(r) in (int, float) and math.isfinite(r) and r > -1 for r in returns):
        return "failed_invalid_financial_summary"
    return (
        "positive_recent_diagnostic_not_oos"
        if all(r > 0 for r in returns)
        else "not_positive_under_both_costs"
    )


def run(sources: Sources, output: PrivateOutput) -> dict[str, Any]:
    require(len(sources.segments) == 1, "one complete recent source segment")
    segment = sources.segments[0]
    require((segment.start_ms, segment.end_ms) == (START, END), "registered diagnostic dates")
    spec = segment.spec(INITIAL)
    schedule = model.build_schedule(CONFIG, spec, segment.frames, sources.hourly)
    targets = {CONFIG.name: schedule.targets}
    for control in CONTROLS:
        targets[control] = model.control_schedule(
            control,
            spec,
            segment.frames,
            sources.hourly,
            matched=schedule if control.startswith("matched") else None,
        )
    compressed(
        output,
        "schedule",
        {
            "config": asdict(CONFIG),
            "targets": [asdict(t) for t in schedule.targets],
            "decisions": [asdict(d) for d in schedule.decisions],
        },
    )
    summarized: dict[str, dict[str, Any]] = {}
    for name, orders in targets.items():
        summarized[name] = {}
        for cost, multiplier in (("base", 1), ("stress", 2)):
            instrument = spec.instruments[0]
            current = replace(
                spec,
                instruments=(
                    replace(
                        instrument,
                        fee_bps=multiplier * instrument.fee_bps,
                        slippage_bps=multiplier * instrument.slippage_bps,
                    ),
                ),
            )
            account = Account(start_ms=START, end_ms=END, folds={"recent_diagnostic": (START, END)})
            compressed(
                output,
                f"{name}-{cost}-request",
                {
                    "spec": asdict(current),
                    "targets": [asdict(t) for t in orders],
                    "input_sha256": INPUT_SHA256,
                    "candidate_lock_sha256": CANDIDATE_LOCK_SHA256,
                },
            )
            try:
                case = model.evaluate_case(current, segment.frames, orders, segment.minute_extremes)
                compressed(output, f"{name}-{cost}", case)
                account.add(case, segment.frames)
            except (HarnessError, ValueError, ArithmeticError) as error:
                account.fail(error)
                output.json(
                    f"{name}-{cost}-failure.json",
                    {
                        "error": str(error),
                        "type": type(error).__name__,
                        "prior_equity": INITIAL,
                        "prior_equity_scope": "initial_diagnostic_capital_not_failure_equity",
                        "failed_segment_state": "unknown_not_serialized_by_legacy_simulator",
                        "no_resume": True,
                    },
                )
            summarized[name][cost] = account.summary()
            print(f"Recent case complete: {name}/{cost}", flush=True)
    trial = summarized.pop(CONFIG.name)
    comparisons: dict[str, Any] = {}
    for side in ("matched_long", "matched_short"):
        comparisons[side] = {}
        for cost in ("base", "stress"):
            left = trial[cost]["folds"].get("recent_diagnostic")
            right = summarized[side][cost]["folds"].get("recent_diagnostic")
            comparisons[side][cost] = {"available": bool(left and right)}
            if left and right:
                difference = [
                    a - b
                    for a, b in zip(
                        left["daily_returns"],
                        right["daily_returns"],
                        strict=True,
                    )
                ]
                comparisons[side][cost].update(
                    {
                        "control_risk_invalid": right["risk_invalid"],
                        "net_return_difference": left["net_return"] - right["net_return"],
                        "daily_return_difference_uncertainty": {
                            str(b): uncertainty(difference, b) for b in (7, 30)
                        },
                    }
                )
    return {
        "study_id": STUDY_ID,
        "config": asdict(CONFIG),
        "frozen_alpha_source_commit": FROZEN_SOURCE,
        "candidate_lock_sha256": CANDIDATE_LOCK_SHA256,
        "data_status": "exposed_recent_diagnostic_not_fresh_oos",
        "source_environment": "DEV_VM_OFFLINE",
        "source_venue": "BINANCE_USDM_PROXY_NOT_EXECUTABLE_HYPERLIQUID",
        "quote_currency": "USDT",
        "image_digest": None,
        "calendar_days": (END - START) // 86_400_000,
        "initial_account_policy": "independent_10000_quote_accounts_not_stitched_to_primary",
        "promotion_decision": "forbidden",
        "fresh_oos": False,
        "new_strategy_configurations": 0,
        "pilot_unique_configurations_to_date": 32,
        "new_selection_permitted": False,
        "diagnostic_verdict": verdict(trial),
        "trial": trial,
        "controls": summarized,
        "matched_comparisons": comparisons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--candidate-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args(argv)
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    code = identity(args.source_commit)
    candidate = locked_candidate(args.candidate_lock)
    plan_sha = hashlib.sha256(read_bytes(PLAN, 1 << 20)).hexdigest()
    require(plan_sha == args.expected_plan_sha256, "registered recent protocol hash")
    with PrivateOutput(args.output) as output:
        output.json(
            "claim.json",
            {
                "study_id": STUDY_ID,
                "source_commit": args.source_commit,
                "input_sha256": INPUT_SHA256,
                "plan_sha256": plan_sha,
                "code_identities": code,
                "candidate_lock_sha256": CANDIDATE_LOCK_SHA256,
                "candidate_name": candidate["name"],
                "trading_mode": "PAPER_OFFLINE_RESEARCH_ONLY",
            },
        )
        try:
            sources = load(args.input)
            output.json("source-qualification.json", sources.source_summary)
            result = run(sources, output)
            require(identity(args.source_commit) == code, "source identity changed during run")
            require(locked_candidate(args.candidate_lock) == candidate, "candidate lock changed")
            require(
                hashlib.sha256(read_bytes(args.input, 8 << 20)).hexdigest() == INPUT_SHA256,
                "recent input identity changed during run",
            )
            result.update(
                source_commit=args.source_commit, input_sha256=INPUT_SHA256, plan_sha256=plan_sha
            )
            output.json("result.json", result)
            failed = sum(
                case[c]["failed"]
                for case in [result["trial"], *result["controls"].values()]
                for c in ("base", "stress")
            )
            output.json(
                "completion.json",
                {
                    "study_id": STUDY_ID,
                    "status": "complete_with_trial_errors" if failed else "complete",
                    "failed_cost_cases": failed,
                    "expected_cost_cases": 12,
                },
            )
            return 2 if failed else 0
        except (HarnessError, ValueError, ArithmeticError, OSError) as error:
            output.json(
                "failure.json",
                {"type": type(error).__name__, "error": str(error)[:1000], "study_id": STUDY_ID},
            )
            raise


if __name__ == "__main__":
    sys.exit(main())
