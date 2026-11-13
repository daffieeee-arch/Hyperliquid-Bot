"""Frozen offline regime research; never a broker or trading admission."""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from research import regime_momentum as model
from research.btc_flow_io import PrivateOutput, read_bytes
from research.harness.errors import HarnessError
from research.regime_io import INPUT_SHA256, PLAN, ROOT, STUDY_ID, Sources, load
from research.repaired_accounting import Frame
from research.two_models_io import BTC, DAY, END, START
from research.volume_repair_io import require

INITIAL = 10_000.0
FOLDS = {
    "development": (START, 1_735_689_600_000),
    "validation": (1_735_689_600_000, 1_767_225_600_000),
    "diagnostic": (1_767_225_600_000, END),
}


def code_identity(commit: str) -> dict[str, str]:
    require(len(commit) == 40 and all(c in "0123456789abcdef" for c in commit), "actual Git SHA")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    require(head == commit, "source commit must be actual checkout HEAD")
    drift = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "src", str(PLAN)],
        cwd=ROOT,
        text=True,
    ).strip()
    require(not drift, "complete imported source and plan must be clean")
    names = subprocess.check_output(["git", "ls-files", "src"], cwd=ROOT, text=True).splitlines()
    names.append(str(PLAN.relative_to(ROOT)))
    result = {}
    for name in names:
        raw = read_bytes(ROOT / name, 8 << 20)
        require(
            raw == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT),
            "tracked source bytes changed: " + name,
        )
        result[name] = hashlib.sha256(raw).hexdigest()
    return result


def sharpe(values: list[float]) -> float | None:
    require(all(math.isfinite(v) and v > -1 for v in values), "finite solvent daily returns")
    if len(values) < 2:
        return None
    deviation = statistics.stdev(values)
    return math.sqrt(365) * statistics.mean(values) / deviation if deviation else None


def uncertainty(values: list[float], block: int) -> dict[str, Any]:
    """Descriptive circular-block interval; paired differences share draws."""
    require(bool(values) and block > 0, "nonempty daily block sample")
    require(all(math.isfinite(v) for v in values), "finite bootstrap sample")
    rng = random.Random(20261113)
    n = len(values)
    cumulative = [0.0]
    for value in values + values:
        cumulative.append(cumulative[-1] + value)
    length = min(n, block)
    sums = [cumulative[i + length] - cumulative[i] for i in range(n)]
    full, remainder = divmod(n, length)
    partial = [cumulative[i + remainder] - cumulative[i] for i in range(n)]
    means = []
    for _ in range(2000):
        total = math.fsum(sums[rng.randrange(n)] for _ in range(full))
        if remainder:
            total += partial[rng.randrange(n)]
        means.append(365 * total / n)
    means.sort()
    return {
        "annualized_arithmetic_mean": 365 * statistics.mean(values),
        "lower_95": means[49],
        "upper_95": means[1949],
        "requested_block_days": block,
        "effective_block_days": length,
        "resamples": 2000,
        "seed": 20261113,
        "interpretation": "descriptive_exposed_dependent_history_not_post_search_significance",
    }


class Account:
    def __init__(self) -> None:
        self.equity = INITIAL
        self.points: list[dict[str, Any]] = []
        self.episodes: list[dict[str, Any]] = []
        self.trades: list[dict[str, Any]] = []
        self.risks: list[dict[str, Any]] = []
        self.funding: list[dict[str, Any]] = []
        self.blocked = False
        self.failures: list[str] = []

    def add(self, case: dict[str, Any], frames: tuple[Frame, ...]) -> None:
        require(not self.blocked, "failed account cannot silently resume")
        require(
            abs(case["initial_equity"] - self.equity) < 1e-7,
            "source segments carry actual closing capital",
        )
        require(len(case["ledger"]) == len(frames), "complete matching ledger frames")
        quantity = 0.0
        payments = []
        for point, frame in zip(case["ledger"], frames, strict=True):
            require(point["timestamp_ms"] == frame.timestamp_ms, "matching source/ledger clocks")
            rate = frame.funding_rates[BTC]
            require(rate is not None, "explicit complete funding at every frame")
            if rate:
                payment = -quantity * frame.marks[BTC] * rate
                payments.append(payment)
                self.funding.append({"timestamp_ms": frame.timestamp_ms, "payment": payment})
            quantity = point["positions"].get(BTC, 0.0)
        require(quantity == 0, "source segment terminal flatness")
        require(
            abs(math.fsum(payments) - case["total_funding"]) < 1e-7,
            "funding events reconcile ledger",
        )
        self.points.extend(case["ledger"])
        self.episodes.extend(case["episodes"])
        self.trades.extend(case["trades"])
        self.risks.extend(case["risk_events"])
        self.equity = case["final_equity"]

    def fail(self, error: BaseException) -> None:
        self.blocked = True
        self.failures.append(type(error).__name__ + ": " + str(error)[:400])

    def summary(self) -> dict[str, Any]:
        clock = [p["timestamp_ms"] for p in self.points]
        require(clock == sorted(set(clock)), "unique source-segment ledger clocks")
        changes: dict[int, float] = {}
        for trade in self.trades:
            instant = trade["timestamp_ms"]
            changes[instant] = changes.get(instant, 0.0) + (
                trade["quantity_delta"] * (trade["mark"] - trade["fill_price"]) - trade["fee"]
            )
        peak, drawdown, gross = INITIAL, 0.0, 0.0
        for point in self.points:
            equity = float(point["equity"])
            for value in (equity - changes.get(point["timestamp_ms"], 0.0), equity):
                peak = max(peak, value)
                drawdown = max(drawdown, 1 - value / peak)
            gross = max(gross, abs(equity - point["cash"]) / equity)

        def snapshot(instant: int) -> float:
            index = bisect.bisect_right(clock, instant) - 1
            require(index >= 0, "valuation snapshot must follow source start")
            row = self.points[index]
            require(
                row["timestamp_ms"] == instant or not row["positions"],
                "missing valuation may carry only certified flat equity",
            )
            return float(row["equity"])

        folds = {}
        daily: list[dict[str, Any]] = []
        active_days = {
            day
            for row in self.episodes
            for day in range(row["entry_ms"] // DAY, (row["exit_ms"] - 1) // DAY + 1)
        }
        if not self.blocked:
            require(bool(clock) and clock[-1] == END, "complete financial endpoint")
        if clock:
            for name, (start, end) in FOLDS.items():
                if clock[-1] < end:
                    continue
                values = [
                    snapshot(t) / snapshot(t - DAY) - 1 for t in range(start + DAY, end + 1, DAY)
                ]
                daily.extend(
                    {"end_ms": t, "net_return": value}
                    for t, value in zip(range(start + DAY, end + 1, DAY), values, strict=True)
                )
                closes = [row for row in self.episodes if start < row["exit_ms"] <= end]
                risks = [row for row in self.risks if start < row["timestamp_ms"] <= end]
                folds[name] = {
                    "initial_equity": snapshot(start),
                    "final_equity": snapshot(end),
                    "net_return": snapshot(end) / snapshot(start) - 1,
                    "daily_sharpe": sharpe(values),
                    "nonflat_calendar_days": sum(
                        start // DAY <= day < end // DAY for day in active_days
                    ),
                    "closed_episodes": len(closes),
                    "risk_invalid": bool(risks),
                    "target_attainment_count": sum(row["target_met"] for row in closes),
                    "fee_cost": math.fsum(
                        t["fee"] for t in self.trades if start < t["timestamp_ms"] <= end
                    ),
                    "slippage_cost": math.fsum(
                        t["slippage_cost"] for t in self.trades if start < t["timestamp_ms"] <= end
                    ),
                    "funding": math.fsum(
                        t["payment"] for t in self.funding if start < t["timestamp_ms"] <= end
                    ),
                    "daily_returns": values,
                    "uncertainty": {str(b): uncertainty(values, b) for b in (7, 30)},
                }
        fees = math.fsum(row["fee"] for row in self.trades)
        friction = math.fsum(row["slippage_cost"] for row in self.trades)
        funding = math.fsum(row["payment"] for row in self.funding)
        return {
            "financial_scope": "completed_segments_only_failed_segment_unreconstructed"
            if self.blocked
            else "complete_all_registered_segments",
            "initial_equity": INITIAL,
            "last_completed_segment_equity": self.equity,
            "final_equity": None if self.blocked else self.equity,
            "net_return": None if self.blocked else self.equity / INITIAL - 1,
            "hourly_sampled_max_drawdown": drawdown,
            "maximum_sampled_gross_exposure": gross,
            "complete_trades": len(self.episodes),
            "nonflat_calendar_days": len(active_days),
            "no_trade_calendar_days": None
            if self.blocked
            else (END - START) // DAY - len(active_days),
            "target_attainment_count": sum(row["target_met"] for row in self.episodes),
            "fee_cost": fees,
            "slippage_cost": friction,
            "total_funding": funding,
            "same_quantity_gross_pnl": None
            if self.blocked
            else self.equity - INITIAL + fees + friction - funding,
            "long_net_pnl": math.fsum(r["net_pnl"] for r in self.episodes if r["side"] > 0),
            "short_net_pnl": math.fsum(r["net_pnl"] for r in self.episodes if r["side"] < 0),
            "risk_invalid": bool(self.risks),
            "failed": self.blocked,
            "failures": self.failures,
            "folds": folds,
            "daily_equity_returns": daily,
            "episodes": self.episodes,
        }


def finalist(trials: dict[str, dict[str, Any]]) -> str | None:
    # A legacy replay failure can prevent serialization of an earlier DEV
    # ledger in the same large segment. Never rank another configuration by
    # treating that unknown history as a known losing development score.
    if any(
        "development" not in trial[cost]["folds"]
        for trial in trials.values()
        for cost in ("base", "stress")
    ):
        return None
    eligible = []
    for name, trial in trials.items():
        scores = []
        for cost in ("base", "stress"):
            dev = trial[cost]["folds"].get("development", {})
            score = dev.get("daily_sharpe")
            if not (
                dev.get("net_return", 0) > 0
                and dev.get("nonflat_calendar_days", 0) >= 30
                and dev.get("closed_episodes", 0) >= 3
                and not dev.get("risk_invalid", True)
                and isinstance(score, (int, float))
                and math.isfinite(score)
                and score > 0
            ):
                break
            scores.append(score)
        if len(scores) == 2:
            eligible.append((-min(scores), name))
    return min(eligible)[1] if eligible else None


def assessment(trial: dict[str, Any]) -> str:
    if any(trial[c]["failed"] for c in ("base", "stress")):
        return "failed_preserved_evidence"
    if any(trial[c]["risk_invalid"] for c in ("base", "stress")):
        return "counterfactual_risk_invalid"
    for cost in ("base", "stress"):
        case = trial[cost]
        if set(case["folds"]) != set(FOLDS):
            return "insufficient_incomplete_folds"
        dd = case["hourly_sampled_max_drawdown"]
        if type(dd) not in (int, float) or not math.isfinite(dd) or not 0 <= dd <= 1:
            return "failed_invalid_financial_summary"
        if case["hourly_sampled_max_drawdown"] > 0.15:
            return "reject_drawdown"
        for fold in case["folds"].values():
            value = fold["net_return"]
            if type(value) not in (int, float) or not math.isfinite(value) or value <= -1:
                return "failed_invalid_financial_summary"
            if fold["nonflat_calendar_days"] < 30 or fold["closed_episodes"] < 3:
                return "insufficient_exposure_or_episodes"
            if fold["net_return"] <= 0:
                return "reject_nonpositive_fold"
    return "positive_retrospective_candidate_requires_fresh_oos_and_execution"


def compressed(output: PrivateOutput, name: str, payload: object) -> None:
    content = json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    output.write(name + ".json.gz", gzip.compress(content, compresslevel=1, mtime=0))


def run(sources: Sources, output: PrivateOutput) -> dict[str, Any]:
    names = [c.name for c in model.configs()]
    keys = ["cash", "persistent_long", "persistent_short", *names]
    keys += [f"{n}__{side}" for n in names for side in ("matched_long", "matched_short")]
    accounts = {key: {cost: Account() for cost in ("base", "stress")} for key in keys}
    for index, segment in enumerate(sources.segments):
        spec = segment.spec(INITIAL)
        schedules = {
            config.name: model.build_schedule(config, spec, segment.frames, sources.hourly)
            for config in model.configs()
        }
        targets = {name: schedule.targets for name, schedule in schedules.items()}
        for name in ("cash", "persistent_long", "persistent_short"):
            targets[name] = model.control_schedule(name, spec, segment.frames, sources.hourly)
        for name, schedule in schedules.items():
            for side in ("matched_long", "matched_short"):
                targets[f"{name}__{side}"] = model.control_schedule(
                    side, spec, segment.frames, sources.hourly, matched=schedule
                )
        compressed(
            output,
            f"segment-{index}-schedules",
            {
                name: {
                    "targets": [asdict(t) for t in schedule.targets],
                    "decisions": [asdict(d) for d in schedule.decisions],
                }
                for name, schedule in schedules.items()
            },
        )
        for name in keys:
            for cost, account in accounts[name].items():
                if account.blocked:
                    continue
                instrument = spec.instruments[0]
                multiplier = 1 if cost == "base" else 2
                current = replace(
                    spec,
                    initial_equity=account.equity,
                    instruments=(
                        replace(
                            instrument,
                            fee_bps=instrument.fee_bps * multiplier,
                            slippage_bps=instrument.slippage_bps * multiplier,
                        ),
                    ),
                )
                compressed(
                    output,
                    f"segment-{index}-{name}-{cost}-request",
                    {
                        "spec": asdict(current),
                        "targets": [asdict(target) for target in targets[name]],
                        "source_segment": index,
                        "input_sha256": INPUT_SHA256,
                    },
                )
                try:
                    case = model.evaluate_case(
                        current, segment.frames, targets[name], segment.minute_extremes
                    )
                    compressed(output, f"segment-{index}-{name}-{cost}", case)
                    account.add(case, segment.frames)
                except (HarnessError, ValueError, ArithmeticError) as error:
                    account.fail(error)
                    output.json(
                        f"segment-{index}-{name}-{cost}-failure.json",
                        {
                            "error": str(error),
                            "type": type(error).__name__,
                            "prior_equity": account.equity,
                            "no_resume": True,
                            "failed_segment_state": "unknown_not_serialized_by_legacy_simulator",
                            "prior_equity_scope": "last_completed_segment_not_equity_at_failure",
                        },
                    )
        print(f"Regime segment complete: {index + 1}/3", flush=True)
    summarized = {
        name: {c: a.summary() for c, a in costs.items()} for name, costs in accounts.items()
    }
    trials: dict[str, dict[str, Any]] = {name: dict(summarized[name]) for name in names}
    selected = finalist(trials)
    for name, trial in trials.items():
        trial["verdict"] = assessment(trial)
        trial["selected_on_development"] = name == selected
        trial["matched_controls"] = {}
        for side in ("matched_long", "matched_short"):
            control = summarized[f"{name}__{side}"]
            comparisons: dict[str, Any] = {}
            for cost in ("base", "stress"):
                comparisons[cost] = {}
                for fold in FOLDS:
                    left = trial[cost]["folds"].get(fold)
                    right = control[cost]["folds"].get(fold)
                    if left and right:
                        difference = [
                            a - b
                            for a, b in zip(
                                left["daily_returns"], right["daily_returns"], strict=True
                            )
                        ]
                        comparisons[cost][fold] = {
                            "control_risk_invalid": right["risk_invalid"],
                            "net_return_difference": left["net_return"] - right["net_return"],
                            "daily_return_difference_uncertainty": {
                                str(b): uncertainty(difference, b) for b in (7, 30)
                            },
                        }
            trial["matched_controls"][side] = comparisons
    return {
        "study_id": STUDY_ID,
        "data_status": "exposed_retrospective_no_fresh_oos",
        "source_environment": "DEV_VM_OFFLINE",
        "source_venue": "BINANCE_USDM_PROXY_NOT_EXECUTABLE_HYPERLIQUID",
        "isolated_leverage": 2,
        "promotion_decision": "forbidden",
        "unique_configurations": 4,
        "pilot_unique_configurations_to_date": 32,
        "development_selected_finalist": selected,
        "development_selection_status": "unavailable_incomplete_development_ledger"
        if any(
            "development" not in t[c]["folds"] for t in trials.values() for c in ("base", "stress")
        )
        else "completed_development_only",
        "trials": trials,
        "controls": {n: summarized[n] for n in keys if n not in names},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args(argv)
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    identity = code_identity(args.source_commit)
    plan_sha = hashlib.sha256(read_bytes(PLAN, 1 << 20)).hexdigest()
    require(plan_sha == args.expected_plan_sha256, "registered plan SHA")
    with PrivateOutput(args.output) as output:
        output.json(
            "claim.json",
            {
                "study_id": STUDY_ID,
                "source_commit": args.source_commit,
                "input_sha256": INPUT_SHA256,
                "plan_sha256": plan_sha,
                "code_identities": identity,
                "trading_mode": "PAPER_OFFLINE_RESEARCH_ONLY",
            },
        )
        try:
            sources = load(args.input)
            output.json("source-qualification.json", sources.source_summary)
            result = run(sources, output)
            require(
                code_identity(args.source_commit) == identity, "source identity changed during run"
            )
            require(
                hashlib.sha256(read_bytes(args.input, 8 << 20)).hexdigest() == INPUT_SHA256,
                "input identity changed during run",
            )
            result.update(
                source_commit=args.source_commit, input_sha256=INPUT_SHA256, plan_sha256=plan_sha
            )
            output.json("result.json", result)
            failed = sum(
                any(trial[c]["failed"] for c in ("base", "stress"))
                for trial in result["trials"].values()
            )
            controls_failed = sum(
                any(trial[c]["failed"] for c in ("base", "stress"))
                for trial in result["controls"].values()
            )
            output.json(
                "completion.json",
                {
                    "status": "complete_with_trial_errors"
                    if failed or controls_failed
                    else "complete",
                    "failed_configurations": failed,
                    "failed_controls": controls_failed,
                    "study_id": STUDY_ID,
                },
            )
            return 2 if failed or controls_failed else 0
        except (HarnessError, ValueError, ArithmeticError, OSError) as error:
            output.json(
                "failure.json",
                {"type": type(error).__name__, "error": str(error)[:1000], "study_id": STUDY_ID},
            )
            raise


if __name__ == "__main__":
    sys.exit(main())
