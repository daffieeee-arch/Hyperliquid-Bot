"""Serial create-only, PAPER-gated compression research; no venue or runtime I/O."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from research import compression_breakout as model
from research.btc_flow_io import PrivateOutput, read_bytes
from research.compression_breakout_io import PLAN, ROOT, STUDY_ID, Sources, load
from research.harness.errors import HarnessError
from research.oi_recovery_cli import verify_prices
from research.regime_cli import Account, code_identity, compressed, uncertainty
from research.regime_io import INPUT_SHA256, midnight
from research.repaired_accounting import Instrument, ReplaySpec
from research.two_models_io import BTC, DAY, END, MINUTE, START
from research.volume_repair_io import require

FROZEN_SOURCE = "bd4bf7c316daed18fbaf46429b579c6266ef494a"
FOLDS = {
    "development": (START, midnight("2025-01-01")),
    "diagnostic_2025": (midnight("2025-01-01"), midnight("2026-01-01")),
    "diagnostic_2026": (midnight("2026-01-01"), END),
}
AGGREGATES = ("funding", "points", "episodes", "trades", "risks")


def identity(commit: str) -> dict[str, str]:
    result = code_identity(commit)
    name = str(PLAN.relative_to(ROOT))
    raw = read_bytes(PLAN, 1 << 20)
    require(
        raw == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT),
        "committed compression protocol bytes",
    )
    result[name] = hashlib.sha256(raw).hexdigest()
    for leaf in (
        "repaired_accounting.py",
        "two_models_io.py",
        "regime_momentum.py",
        "regime_io.py",
        "daily_momentum.py",
    ):
        source = "src/research/" + leaf
        require(
            read_bytes(ROOT / source, 1 << 20)
            == subprocess.check_output(["git", "show", f"{FROZEN_SOURCE}:{source}"], cwd=ROOT),
            "unchanged frozen helper: " + leaf,
        )
    return result


def checkpoint(account: Account) -> tuple[float, tuple[int, ...]]:
    return account.equity, tuple(len(getattr(account, n)) for n in AGGREGATES)


def rollback(account: Account, saved: tuple[float, tuple[int, ...]]) -> None:
    equity, lengths = saved
    for name, length in zip(AGGREGATES, lengths, strict=True):
        values = getattr(account, name)
        require(len(values) >= length, "account add cannot shrink completed evidence")
        del values[length:]
    account.equity = equity


def summarize(account: Account) -> dict[str, Any]:
    result = account.summary()
    result["minute_plus_exact_funding_sampled_max_drawdown"] = result.pop(
        "hourly_sampled_max_drawdown"
    )
    result["minute_or_funding_sampling_is_not_tick_continuous"] = True
    result["turnover_notional"] = math.fsum(
        abs(t["quantity_delta"] * t["reference_price"]) for t in account.trades
    )
    holds = sorted(row["holding_minutes"] for row in account.episodes)
    rois = sorted(row["net_margin_roi"] for row in account.episodes)
    result["holding_minutes"] = {
        "median": statistics.median(holds) if holds else None,
        "maximum": max(holds) if holds else None,
    }
    result["net_initial_margin_roi_distribution"] = {
        "minimum": min(rois) if rois else None,
        "median": statistics.median(rois) if rois else None,
        "p05": rois[max(0, math.ceil(0.05 * len(rois)) - 1)] if rois else None,
        "maximum": max(rois) if rois else None,
    }
    streak, longest = 0, 0
    for row in account.episodes:
        streak = streak + 1 if row["net_pnl"] < 0 else 0
        longest = max(longest, streak)
    result["maximum_consecutive_losing_trades"] = longest
    result["exit_reason_counts"] = dict(Counter(r["reason"] for r in account.episodes))
    result["trigger_target_missed_on_delayed_fill"] = sum(
        r["reason"] == "target" and not r["target_met"] for r in account.episodes
    )
    result["stop_overshoot_below_minus10pct"] = sum(
        r["reason"] == "stop" and r["net_margin_roi"] < -0.10 for r in account.episodes
    )
    closes = Counter((r["exit_ms"] - 1) // DAY for r in account.episodes)
    positive_winners = Counter(
        (r["exit_ms"] - 1) // DAY for r in account.episodes if r["net_pnl"] > 0
    )
    winners = Counter((r["exit_ms"] - 1) // DAY for r in account.episodes if r["target_met"])
    result["calendar_days"] = (account.end_ms - account.start_ms) // DAY
    result["trade_day_counts_completed_segments_only"] = [
        {
            "day_start_ms": t,
            "closed_trades": closes[t // DAY],
            "margin_target_winners": winners[t // DAY],
            "net_positive_trades": positive_winners[t // DAY],
        }
        for t in range(account.start_ms, account.end_ms, DAY)
        if not account.blocked or (account.points and t + DAY <= account.points[-1]["timestamp_ms"])
    ]
    result["close_day_ownership"] = "(UTC_day_start,UTC_day_end]; includes paid terminal"
    daily_open: dict[int, float] = {}
    intraday = []
    for point in account.points:
        t = point["timestamp_ms"]
        day = t // DAY
        if t % DAY == 0:
            daily_open[day] = point["equity"]
        opening = daily_open.get(day)
        if opening is not None and point["equity"] < 0.99 * opening:
            intraday.append(
                {"timestamp_ms": t, "equity": point["equity"], "UTC_open_equity": opening}
            )
    result["paper_limit_diagnostics_not_admission"] = {
        "gross_above_1x": result["maximum_sampled_gross_exposure"] > 1,
        "asset_strategy_above_half": result["maximum_sampled_gross_exposure"] > 0.5,
        "drawdown_above_7pct": result["minute_plus_exact_funding_sampled_max_drawdown"] > 0.07,
        "sampled_intraday_loss_above_1pct": intraday,
        "intraday_loss_reset": "post-execution marked equity at exact UTC midnight; no actual halt",
        "utc_daily_loss_above_1pct": [
            r for r in result["daily_equity_returns"] if r["net_return"] < -0.01
        ],
        "risk_limits_and_actual_modes_unchanged": True,
    }
    return result


def dispositions(pair: dict[str, dict[str, Any]]) -> list[str]:
    flags = []
    if any(r["failed"] for r in pair.values()):
        flags.append("failed_calculation")
    if any(r["risk_invalid"] for r in pair.values()):
        flags.append("risk_invalid")
    if any(r["minute_plus_exact_funding_sampled_max_drawdown"] > 0.15 for r in pair.values()):
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


def run(sources: Sources, output: PrivateOutput) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {
        n: {} for n in (model.NAME, "cash", "matched_long", "matched_short")
    }
    counts: dict[str, dict[str, int]] = {}
    schedules: dict[str, list[model.Schedule]] = {}
    # Cost-dependent candidate exit policies are registered independently.
    for cost in ("base", "stress"):
        schedules[cost] = []
        counts[cost] = {}
        for i, segment in enumerate(sources.segments):
            instrument = Instrument(
                BTC, "perp", 4.5 if cost == "base" else 9, 2 if cost == "base" else 4
            )
            spec = ReplaySpec(
                segment.start_ms,
                segment.end_ms,
                MINUTE,
                10000,
                (instrument,),
                tuple(f.timestamp_ms for f in segment.frames),
            )
            schedule = model.build_schedule(spec, segment.frames, sources.hourly)
            schedules[cost].append(schedule)
            for name, value in schedule.counts.items():
                counts[cost][name] = counts[cost].get(name, 0) + value
            compressed(output, f"{cost}-segment-{i}-event-schedule", asdict(schedule))
    for name in results:
        for cost in ("base", "stress"):
            account = Account(start_ms=START, end_ms=END, folds=FOLDS)
            for i, segment in enumerate(sources.segments):
                if account.blocked:
                    output.json(
                        f"{name}-{cost}-segment-{i}-blocked.json",
                        {"status": "blocked_prior_failure", "no_resume": True},
                    )
                    continue
                instrument = Instrument(
                    BTC, "perp", 4.5 if cost == "base" else 9, 2 if cost == "base" else 4
                )
                spec = ReplaySpec(
                    segment.start_ms,
                    segment.end_ms,
                    MINUTE,
                    account.equity,
                    (instrument,),
                    tuple(f.timestamp_ms for f in segment.frames),
                )
                schedule = schedules[cost][i]
                if name != model.NAME:
                    schedule = model.control_schedule(
                        schedule,
                        {f.timestamp_ms: f for f in segment.frames},
                        segment.end_ms,
                        {"cash": 0, "matched_long": 1, "matched_short": -1}[name],
                    )
                compressed(
                    output,
                    f"{name}-{cost}-segment-{i}-request",
                    {"spec": asdict(spec), "schedule": asdict(schedule)},
                )
                saved = checkpoint(account)
                case = None
                try:
                    case = model.evaluate_case(
                        spec, segment.frames, schedule, segment.minute_extremes
                    )
                    compressed(output, f"{name}-{cost}-segment-{i}-case", case)
                    account.add(case, segment.frames)
                except (HarnessError, ArithmeticError, ValueError, MemoryError) as error:
                    rollback(account, saved)
                    account.fail(error)
                    output.json(
                        f"{name}-{cost}-segment-{i}-failure.json",
                        {
                            "status": "failed",
                            "type": type(error).__name__,
                            "error": str(error)[:1000],
                            "failed_segment_exposure": "unknown_legacy_simulator_partial_state",
                            "prior_completed_segment_equity": account.equity,
                            "not_failure_equity": True,
                            "no_resume": True,
                        },
                    )
                finally:
                    case = None
            results[name][cost] = summarize(account)
            output.json(f"{name}-{cost}-summary.json", results[name][cost])
            del account
            print("Compression account/cost complete:", name, cost, flush=True)
    paired: dict[str, Any] = {}
    for name in ("cash", "matched_long", "matched_short"):
        paired[name] = {}
        for cost in ("base", "stress"):
            left, right = results[model.NAME][cost], results[name][cost]
            available = not any(r["failed"] or r["risk_invalid"] for r in (left, right))
            row: dict[str, Any] = {
                "available": available,
                "conditional_timing_control": True,
                "control_risk_invalid": right["risk_invalid"],
            }
            if available:
                differences = [
                    a["net_return"] - b["net_return"]
                    for a, b in zip(
                        left["daily_equity_returns"], right["daily_equity_returns"], strict=True
                    )
                ]
                row["whole_period_paired_daily_return_uncertainty"] = {
                    str(b): uncertainty(differences, b) for b in (7, 30)
                }
                row["folds"] = {}
                for fold in FOLDS:
                    left_fold, right_fold = left["folds"][fold], right["folds"][fold]
                    delta = [
                        a - b
                        for a, b in zip(
                            left_fold["daily_returns"], right_fold["daily_returns"], strict=True
                        )
                    ]
                    row["folds"][fold] = {
                        "net_return_difference": left_fold["net_return"] - right_fold["net_return"],
                        "paired_daily_uncertainty": {
                            str(b): uncertainty(delta, b) for b in (7, 30)
                        },
                    }
            paired[name][cost] = row
    candidate = results.pop(model.NAME)
    flags = dispositions(candidate)
    return {
        "study_id": STUDY_ID,
        "source_environment": "DEV_VM_OFFLINE",
        "data_status": "exposed_retrospective_no_fresh_oos",
        "source_venue": "BINANCE_USDM_PROXY_NOT_EXECUTABLE_HYPERLIQUID",
        "unique_configurations": 1,
        "pilot_unique_configurations_to_date": 37,
        "family_count": 4,
        "selector": None,
        "promotion_decision": "forbidden",
        "margin": "immutable_actual_entry_MARK_times_abs_q_divided_by_10",
        "candidate_counts_by_cost": counts,
        "candidate": candidate,
        "controls": results,
        "paired_controls": paired,
        "disposition": flags,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--source-only", action="store_true")
    args = parser.parse_args(argv)
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    code = identity(args.source_commit)
    plan_sha = hashlib.sha256(read_bytes(PLAN, 1 << 20)).hexdigest()
    require(plan_sha == args.expected_plan_sha256, "registered protocol SHA256")
    with PrivateOutput(args.output) as output:
        output.json(
            "claim.json",
            {
                "study_id": STUDY_ID,
                "source_commit": args.source_commit,
                "input_sha256": INPUT_SHA256,
                "plan_sha256": plan_sha,
                "code_identities": code,
                "source_only": args.source_only,
                "mode": "PAPER_OFFLINE_RESEARCH_ONLY",
            },
        )
        try:
            verify_prices(args.input)
            sources = load(args.input)
            output.json("source-qualification.json", sources.source_summary)
            result: dict[str, Any] = (
                {"study_id": STUDY_ID, "status": "source_only_no_financial_labels"}
                if args.source_only
                else run(sources, output)
            )
            require(identity(args.source_commit) == code, "unchanged clean code identity after run")
            verify_prices(args.input)
            result.update(
                source_commit=args.source_commit, input_sha256=INPUT_SHA256, plan_sha256=plan_sha
            )
            output.json("result.json", result)
            failed = not args.source_only and any(
                r["failed"]
                for p in [result["candidate"], *result["controls"].values()]
                for r in p.values()
            )
            output.json(
                "completion.json",
                {
                    "status": "complete_with_trial_errors" if failed else "complete",
                    "source_only": args.source_only,
                },
            )
            return 2 if failed else 0
        except (HarnessError, ArithmeticError, ValueError, OSError, MemoryError) as error:
            output.json("failure.json", {"type": type(error).__name__, "error": str(error)[:1000]})
            raise


if __name__ == "__main__":
    sys.exit(main())
