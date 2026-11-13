"""Serial, create-only hourly-trend research; no broker, selector or admission."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import math
import os
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from research import hourly_trend as model
from research import regime_momentum as original
from research.btc_flow_io import PrivateOutput, read_bytes
from research.harness.errors import HarnessError
from research.hourly_trend_io import PLAN, ROOT, STUDY_ID, load
from research.oi_recovery_cli import verify_prices
from research.regime_cli import Account, code_identity, compressed, uncertainty
from research.regime_io import INPUT_SHA256, Segment, Sources, midnight
from research.repaired_accounting import Target
from research.two_models_io import BTC, DAY, END, HOUR, MINUTE, START, read_record
from research.volume_repair_io import json_value, require

FROZEN_SOURCE = "bd4bf7c316daed18fbaf46429b579c6266ef494a"
BASELINE_INDEX_SHA256 = "2b1653f765a4d5f480bcc963e93f7c410eede584d96e95298b8673402e6e8232"
BASELINE_SUMMARY_SHA256 = "7a395e2ced136ddb6dee01a6622970100386ee418f1a23dc69b0ffc609803a2f"
BASELINE = "daily_locked_h720_plain"
FOLDS = {
    "development": (START, midnight("2025-01-01")),
    "diagnostic_2025": (midnight("2025-01-01"), midnight("2026-01-01")),
    "diagnostic_2026": (midnight("2026-01-01"), END),
}
FINANCIAL_KEYS = (
    "initial_equity",
    "final_equity",
    "fee_cost",
    "slippage_cost",
    "total_funding",
    "trades",
)


def identity(commit: str) -> dict[str, str]:
    result = code_identity(commit)
    name = str(PLAN.relative_to(ROOT))
    raw = read_bytes(PLAN, 1 << 20)
    require(
        raw == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT),
        "committed hourly protocol bytes",
    )
    result[name] = hashlib.sha256(raw).hexdigest()
    for leaf in (
        "regime_momentum.py",
        "regime_io.py",
        "repaired_accounting.py",
        "two_models_io.py",
    ):
        name = "src/research/" + leaf
        require(
            read_bytes(ROOT / name, 1 << 20)
            == subprocess.check_output(["git", "show", f"{FROZEN_SOURCE}:{name}"], cwd=ROOT),
            "unchanged original helper: " + leaf,
        )
    return result


def baseline(path: Path) -> tuple[dict[tuple[int, str], dict[str, Any]], dict[str, Any]]:
    raw = read_bytes(path, 1 << 20)
    require(
        hashlib.sha256(raw).hexdigest() == BASELINE_INDEX_SHA256, "pinned original baseline index"
    )
    body = json_value(raw)
    require(isinstance(body, dict), "original baseline index object")
    assert isinstance(body, dict)
    require(
        body["schema"] == "hourly-trend-locked-daily-baseline-v1"
        and body["study_id"] == "btc-cost-aware-regime-20261113-v1"
        and body["source_commit"] == FROZEN_SOURCE,
        "original accepted baseline identity",
    )
    summary_raw = read_record(body["summary"])
    require(
        hashlib.sha256(summary_raw).hexdigest() == BASELINE_SUMMARY_SHA256,
        "original published summary",
    )
    summary = json_value(summary_raw)
    assert isinstance(summary, dict)
    cases = {}
    for row in body["cases"]:
        with gzip.GzipFile(fileobj=io.BytesIO(read_record(row))) as stream:
            decoded = stream.read((64 << 20) + 1)
        require(len(decoded) <= 64 << 20, "bounded original baseline decoded case")
        value = json_value(decoded)
        require(isinstance(value, dict), "baseline case object")
        assert isinstance(value, dict)
        key = (row["segment"], row["cost"])
        require(key not in cases, "unique original baseline case")
        cases[key] = {name: value[name] for name in FINANCIAL_KEYS}
    require(
        set(cases) == {(i, c) for i in range(3) for c in ("base", "stress")},
        "six original baseline cases",
    )
    return cases, summary["trials"]["regime-h720-plain"]


def equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(equivalent(v, right[k]) for k, v in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            equivalent(a, b) for a, b in zip(left, right, strict=True)
        )
    if type(left) is float and type(right) is float:
        return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-7)
    return type(left) is type(right) and left == right


def persistent(side: int, segment: Segment, hourly: dict[int, float]) -> tuple[Target, ...]:
    require(side in (-1, 1), "fixed persistent direction")
    indexed = {frame.timestamp_ms: frame for frame in segment.frames}
    for instant in range(segment.start_ms, segment.end_ms, HOUR):
        _, sigma, _ = original.features(original.configs()[0], hourly, instant)
        if sigma is not None and sigma > 0:
            original._execution(indexed, instant + MINUTE)
            return (
                Target(
                    instant,
                    instant + MINUTE,
                    {BTC: side * min(original.ENTRY_CAP, original.RISK_TARGET / sigma)},
                ),
            )
    return ()


def schedules(
    sources: Sources, output: PrivateOutput, *, baseline_only: bool = False
) -> dict[str, list[tuple[Target, ...]]]:
    names = [c.name for c in model.configs()]
    keys = (
        [BASELINE]
        if baseline_only
        else [
            "cash",
            "persistent_long",
            "persistent_short",
            *names,
            *(f"{n}__{side}" for n in names for side in ("long", "short")),
        ]
    )
    result: dict[str, list[tuple[Target, ...]]] = {key: [] for key in keys}
    for index, segment in enumerate(sources.segments):
        request = segment.spec(10_000.0)
        if baseline_only:
            old = original.build_schedule(
                original.RegimeConfig("locked", 720, False), request, segment.frames, sources.hourly
            )
            result[BASELINE].append(old.targets)
            compressed(output, f"segment-{index}-{BASELINE}-schedule", asdict(old))
            continue
        result["cash"].append(())
        for name, sign in (("persistent_long", 1), ("persistent_short", -1)):
            result[name].append(persistent(sign, segment, sources.hourly))
        for config in model.configs():
            schedule = model.build_schedule(config, request, segment.frames, sources.hourly)
            result[config.name].append(schedule.targets)
            counts = Counter(d.action for d in schedule.decisions)
            compressed(
                output,
                f"segment-{index}-{config.name}-schedule",
                {
                    **asdict(schedule),
                    "action_counts": dict(counts),
                    "scheduled_hour_denominator": len(schedule.decisions),
                },
            )
            for side, sign in (("long", 1), ("short", -1)):
                result[f"{config.name}__{side}"].append(
                    tuple(
                        replace(t, weights={BTC: sign * abs(t.weights[BTC])} if t.weights else {})
                        for t in schedule.targets
                    )
                )
    return result


def summarize(account: Account) -> dict[str, Any]:
    result = account.summary()
    # This inherited field includes the newly retained execution/funding frames.
    result["hourly_plus_execution_funding_sampled_max_drawdown"] = result.pop(
        "hourly_sampled_max_drawdown"
    )
    result["sampled_drawdown_is_not_minute_or_tick_continuous"] = True
    result["turnover_notional"] = math.fsum(
        abs(t["quantity_delta"] * t["reference_price"]) for t in account.trades
    )
    episodes = result["episodes"]
    holds = sorted((r["exit_ms"] - r["entry_ms"]) / HOUR for r in episodes)
    result["holding_hours"] = {
        "median": statistics.median(holds) if holds else None,
        "p95": holds[math.ceil(0.95 * len(holds)) - 1] if holds else None,
        "maximum": max(holds) if holds else None,
    }
    result["margin_targets_diagnostic_only"] = {
        str(target): sum(r["net_margin_roi"] >= target for r in episodes)
        for target in (0.01, 0.02, 0.20)
    }
    closes = Counter((r["exit_ms"] - 1) // DAY for r in episodes)
    result["completed_trades_per_UTC_day"] = [
        {"day_start_ms": t, "completed_trades": closes[t // DAY]}
        for t in range(account.start_ms, account.end_ms, DAY)
        if not account.blocked or (account.points and t + DAY <= account.points[-1]["timestamp_ms"])
    ]
    result["completed_trade_day_ownership"] = "(UTC_day_start,UTC_day_end]; terminal_close_included"
    result["trade_frequency_incomplete_after_failure"] = account.blocked
    return result


def dispositions(pair: dict[str, dict[str, Any]]) -> list[str]:
    flags = []
    if any(r["failed"] for r in pair.values()):
        flags.append("failed_calculation")
    if any(r["risk_invalid"] for r in pair.values()):
        flags.append("risk_invalid")
    if any(r["hourly_plus_execution_funding_sampled_max_drawdown"] > 0.15 for r in pair.values()):
        flags.append("reject_drawdown")
    if any(not r["failed"] and r["net_return"] <= 0 for r in pair.values()):
        flags.append("reject_cost")
    if any(
        f["net_return"] <= 0
        for r in pair.values()
        for n, f in r["folds"].items()
        if n != "development"
    ):
        flags.append("reject_regime_robustness")
    if any(
        set(r["folds"]) != set(FOLDS)
        or any(
            f["closed_episodes"] < 3 or f["nonflat_calendar_days"] < 30 for f in r["folds"].values()
        )
        for r in pair.values()
    ):
        flags.append("insufficient_sample")
    return flags or ["positive_retrospective_diagnostic_not_admitted"]


def run(
    sources: Sources,
    output: PrivateOutput,
    expected: dict[tuple[int, str], dict[str, Any]],
    expected_summary: dict[str, Any],
) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {}
    attempts = 0
    # Baseline parity must pass BOTH costs before any new-alpha schedule is built.
    for baseline_only in (True, False):
        planned = schedules(sources, output, baseline_only=baseline_only)
        for name, targets in planned.items():
            results[name] = {}
            for cost in ("base", "stress"):
                # Only this one complete account/cost chain owns a full ledger in RAM.
                account = Account(start_ms=START, end_ms=END, folds=FOLDS)
                for index, segment in enumerate(sources.segments):
                    if account.blocked:
                        output.json(
                            f"{name}-{cost}-segment-{index}-blocked.json",
                            {"status": "not_processed_prior_failure", "no_resume": True},
                        )
                        continue
                    request = segment.spec(account.equity)
                    if cost == "stress":
                        request = replace(
                            request,
                            instruments=tuple(
                                replace(i, fee_bps=2 * i.fee_bps, slippage_bps=2 * i.slippage_bps)
                                for i in request.instruments
                            ),
                        )
                    compressed(
                        output,
                        f"{name}-{cost}-segment-{index}-request",
                        {"spec": asdict(request), "targets": [asdict(t) for t in targets[index]]},
                    )
                    attempts += 1
                    case: dict[str, Any] | None = None
                    try:
                        case = original.evaluate_case(
                            request, segment.frames, targets[index], segment.minute_extremes
                        )
                        compressed(output, f"{name}-{cost}-segment-{index}-case", case)
                        if name == BASELINE:
                            require(
                                equivalent(
                                    {k: case[k] for k in FINANCIAL_KEYS}, expected[index, cost]
                                ),
                                "daily baseline exact native trade/cost/funding/equity parity",
                            )
                        account.add(case, segment.frames)
                    except (HarnessError, ArithmeticError, ValueError, MemoryError) as error:
                        if name == BASELINE:
                            raise
                        account.fail(error)
                        output.json(
                            f"{name}-{cost}-segment-{index}-failure.json",
                            {
                                "status": "failed",
                                "error": str(error)[:1000],
                                "failed_segment_state": "unknown_legacy_simulator_partial_state",
                                "prior_completed_segment_equity": account.equity,
                                "not_failure_equity": True,
                                "no_resume": True,
                            },
                        )
                    finally:
                        # A failed Account.add must not retain its case into the next chain.
                        case = None
                summary = summarize(account)
                if name == BASELINE and not account.blocked:
                    require(
                        equivalent(
                            {
                                k: summary[k]
                                for k in (
                                    "final_equity",
                                    "fee_cost",
                                    "slippage_cost",
                                    "total_funding",
                                )
                            },
                            {
                                k: expected_summary[cost][k]
                                for k in (
                                    "final_equity",
                                    "fee_cost",
                                    "slippage_cost",
                                    "total_funding",
                                )
                            },
                        ),
                        "daily baseline carried whole-account financial parity",
                    )
                results[name][cost] = summary
                output.json(f"{name}-{cost}-summary.json", summary)
                del account
                print("Hourly trend account/cost complete:", name, cost, flush=True)
            # Baseline mismatch is an integrity failure, not another hypothesis outcome.
            if name == BASELINE:
                require(
                    not any(row["failed"] for row in results[name].values()),
                    "original daily baseline reproduction failed; study not certified",
                )
    comparisons: dict[str, Any] = {}
    for config in model.configs():
        comparisons[config.name] = {}
        for side in ("long", "short"):
            comparisons[config.name][side] = {}
            for cost in ("base", "stress"):
                left, right = results[config.name][cost], results[f"{config.name}__{side}"][cost]
                available = not any(r["failed"] or r["risk_invalid"] for r in (left, right))
                row: dict[str, Any] = {"available": available}
                if available:
                    difference = [
                        a["net_return"] - b["net_return"]
                        for a, b in zip(
                            left["daily_equity_returns"], right["daily_equity_returns"], strict=True
                        )
                    ]
                    row["daily_return_difference_uncertainty"] = {
                        str(n): uncertainty(difference, n) for n in (7, 30)
                    }
                comparisons[config.name][side][cost] = row
    return {
        "study_id": STUDY_ID,
        "accounts": results,
        "comparisons": comparisons,
        "dispositions": {c.name: dispositions(results[c.name]) for c in model.configs()},
        "configurations": [asdict(c) for c in model.configs()],
        "selector": None,
        "new_unique_configurations": 4,
        "programme_unique_configurations": 36,
        "programme_limit": 40,
        "case_attempts": attempts,
        "expected_segment_cost_cases": 16 * 2 * len(sources.segments),
        "baseline_reproduced": True,
        "fresh_oos": False,
        "source_venue": "BINANCE_USDM_PROXY_NOT_EXECUTABLE_HYPERLIQUID",
        "source_environment": "DEV_VM_OFFLINE",
        "image_digest": None,
        "data_status": "exposed_retrospective_no_fresh_oos",
        "promotion_decision": "forbidden",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--baseline-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args(argv)
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    code = identity(args.source_commit)
    plan_sha = hashlib.sha256(read_bytes(PLAN, 1 << 20)).hexdigest()
    require(plan_sha == args.expected_plan_sha256, "registered hourly trend protocol SHA")
    with PrivateOutput(args.output) as output:
        output.json(
            "claim.json",
            {
                "study_id": STUDY_ID,
                "source_commit": args.source_commit,
                "code_identities": code,
                "plan_sha256": plan_sha,
                "input_sha256": INPUT_SHA256,
                "baseline_index_sha256": BASELINE_INDEX_SHA256,
                "trading_mode": "PAPER_OFFLINE_RESEARCH_ONLY",
            },
        )
        try:
            sources = load(args.input)
            output.json("source-qualification.json", sources.source_summary)
            expected, expected_summary = baseline(args.baseline_index)
            result = run(sources, output, expected, expected_summary)
            require(identity(args.source_commit) == code, "source changed during hourly study")
            verify_prices(args.input)
            baseline(args.baseline_index)
            result.update(
                source_commit=args.source_commit,
                input_sha256=INPUT_SHA256,
                plan_sha256=plan_sha,
                baseline_index_sha256=BASELINE_INDEX_SHA256,
            )
            output.json("result.json", result)
            failed = sum(r["failed"] for pair in result["accounts"].values() for r in pair.values())
            output.json(
                "completion.json",
                {
                    "status": "complete_with_trial_errors" if failed else "complete",
                    "failed_account_cost_chains": failed,
                    "case_attempts": result["case_attempts"],
                    "expected_segment_cost_cases": result["expected_segment_cost_cases"],
                },
            )
            return 2 if failed else 0
        except (
            HarnessError,
            ArithmeticError,
            ValueError,
            KeyError,
            TypeError,
            OSError,
            MemoryError,
        ) as error:
            output.json(
                "failure.json",
                {
                    "study_id": STUDY_ID,
                    "type": type(error).__name__,
                    "error": str(error)[:1000],
                    "financial_result": None,
                },
            )
            raise


if __name__ == "__main__":
    sys.exit(main())
