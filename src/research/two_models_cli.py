"""Run two preregistered offline models; no broker or trading authorization."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from research import daily_momentum as daily
from research import micro_flow as micro
from research.btc_flow_io import PrivateOutput, read_bytes
from research.harness.errors import HarnessError
from research.micro_flow_io import capture_windows
from research.two_models_io import (
    PLAN,
    ROOT,
    check_index,
    daily_months,
    daily_spec,
    funding_censuses,
    implementation_hashes,
    native_source,
    prepare,
    read_record,
    sha,
)
from research.volume_repair_io import document, require

INITIAL = 10_000.0


def verify_code(commit: str) -> dict[str, str]:
    require(bool(re.fullmatch("[0-9a-f]{40}", commit)), "full actual source commit")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    require(head == commit, "declared source must be checkout HEAD")
    drift = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "src"],
        cwd=ROOT,
        text=True,
    ).strip()
    require(not drift, "clean complete imported source tree")
    identities = implementation_hashes()
    for name, digest in identities.items():
        require(
            sha(subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)) == digest,
            "source differs from reviewed commit: " + name,
        )
    return identities


def verify_sources(body: dict[str, Any], model: str) -> None:
    check_index(body, verify_raw=False)
    if model == "daily":
        for row in body["daily_sources"]:
            native_source(row).read()
            native_source(row, True).read()
    else:
        for capture in body["captures"]:
            for part in capture["parts"]:
                read_record(part, 4 << 20)
    for row in body["funding_sources"]:
        read_record(row)


def artifact(output: PrivateOutput, name: str, body: object) -> None:
    raw = json.dumps(body, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    output.write(name + ".json.gz", gzip.compress(raw, compresslevel=1, mtime=0))


class Totals:
    """Carry one actual simulated account; never reset after a losing window."""

    def __init__(self) -> None:
        self.equity = self.peak = INITIAL
        self.max_drawdown = 0.0
        self.fees = self.friction = self.funding = self.turnover = 0.0
        self.gross_pnl = self.spread_cost = 0.0
        self.folds: dict[str, dict[str, Any]] = {}
        self.years: dict[str, dict[str, Any]] = {}
        self.episodes: list[dict[str, Any]] = []
        self.calendar_days = self.unavailable_days = 0
        self.observed_hours = 0.0
        self.failures: list[str] = []
        self.risk_invalid = False
        self.blocked = False
        self.counts: dict[str, int] = {}

    def add(self, case: dict[str, Any], group: str, year: str, *, is_micro: bool) -> None:
        require(not self.blocked, "unresolved account cannot restart at next window")
        require(
            abs(case["initial_equity"] - self.equity) <= 1e-8 * max(1.0, self.equity),
            "continuous independent case equity carry",
        )
        fold = self.folds.setdefault(
            group,
            {
                "initial_equity": self.equity,
                "final_equity": self.equity,
                "peak": self.equity,
                "max_drawdown": 0.0,
                "trades": 0,
            },
        )
        annual = self.years.setdefault(
            year,
            {
                "initial_equity": self.equity,
                "final_equity": self.equity,
                "peak": self.equity,
                "max_drawdown": 0.0,
                "trades": 0,
            },
        )
        execution_change: dict[int, float] = {}
        if not is_micro:
            for trade in case["trades"]:
                instant = trade["timestamp_ms"]
                change = (
                    trade["quantity_delta"] * (trade["mark"] - trade["fill_price"]) - trade["fee"]
                )
                execution_change[instant] = execution_change.get(instant, 0.0) + change
        for point in case["ledger"]:
            equity = float(point["equity"])
            before = (
                equity if is_micro else equity - execution_change.get(point["timestamp_ms"], 0.0)
            )
            for value in (before, equity):
                require(math.isfinite(value), "finite carried mark equity")
                self.peak = max(self.peak, value)
                self.max_drawdown = max(self.max_drawdown, 1 - value / self.peak)
                for segment in (fold, annual):
                    segment["peak"] = max(segment["peak"], value)
                    segment["max_drawdown"] = max(
                        segment["max_drawdown"], 1 - value / segment["peak"]
                    )
        episodes = case["trades"] if is_micro else case["episodes"]
        for row in episodes:
            self.episodes.append(
                {
                    "entry_ms": row["entry_utc_ns"] // 1_000_000 if is_micro else row["entry_ms"],
                    "side": row["side"],
                    "net_pnl": row["net_pnl"],
                    "net_margin_roi": row["net_margin_return"]
                    if is_micro
                    else row["net_margin_roi"],
                    "target_met": bool(
                        row["realized_target_met"] if is_micro else row["realized_target_success"]
                    ),
                    "fold": group,
                }
            )
        fold["trades"] += len(episodes)
        annual["trades"] += len(episodes)
        self.equity = float(case["final_equity"])
        fold["final_equity"] = annual["final_equity"] = self.equity
        for segment in (fold, annual):
            for key in (
                "fee_cost",
                "slippage_cost",
                "total_funding",
                "calendar_days",
                "unavailable_data_days",
            ):
                segment[key] = segment.get(key, 0) + (case.get(key) or 0)
            segment["long_net_pnl"] = segment.get("long_net_pnl", 0.0) + math.fsum(
                row["net_pnl"] for row in episodes if row["side"] == 1
            )
            segment["short_net_pnl"] = segment.get("short_net_pnl", 0.0) + math.fsum(
                row["net_pnl"] for row in episodes if row["side"] == -1
            )
            segment["target_attainment_count"] = segment.get("target_attainment_count", 0) + sum(
                bool(row["realized_target_met"] if is_micro else row["realized_target_success"])
                for row in episodes
            )
        self.fees += case["fee_cost"]
        self.friction += case["slippage_cost"]
        self.funding += case["total_funding"] or 0.0
        if is_micro:
            self.gross_pnl += case.get("same_quantity_gross_mid_pnl") or 0.0
            self.spread_cost += case["execution_spread_and_friction_cost"] - case["slippage_cost"]
        else:
            self.gross_pnl += case["same_quantity_gross_price_return"] * case["initial_equity"]
        fills = case["fills"] if is_micro else case["trades"]
        self.turnover += math.fsum(
            abs(fill["quantity"] * fill["price"])
            if is_micro
            else abs(fill["quantity_delta"] * fill["fill_price"])
            for fill in fills
        )
        self.calendar_days += int(case.get("calendar_days", 0))
        self.unavailable_days += int(case.get("unavailable_data_days", 0))
        self.observed_hours += float(case.get("observed_hours", 0))
        for key, value in case.get("counts", {}).items():
            self.counts[key] = self.counts.get(key, 0) + value
        local_risk = bool(
            case.get("liquidation_possible", False)
            or case.get("liquidation_possible_on_observed_book", False)
            or case.get("reference_margin_exhausted", False)
        )
        self.risk_invalid |= local_risk
        fold["risk_invalid"] = bool(fold.get("risk_invalid", False) or local_risk)
        annual["risk_invalid"] = bool(annual.get("risk_invalid", False) or local_risk)
        if case.get("status") == "failed":
            self.failures.append(str(case.get("error", "unknown failed case")))
            self.blocked = True
            fold["failed"] = annual["failed"] = True

    def fail(self, error: BaseException, group: str) -> None:
        self.failures.append(type(error).__name__ + ": " + str(error)[:400])
        self.blocked = True
        fold = self.folds.setdefault(
            group,
            {
                "initial_equity": self.equity,
                "final_equity": self.equity,
                "peak": self.equity,
                "max_drawdown": 0.0,
                "trades": 0,
            },
        )
        fold["failed"] = True

    def result(self) -> dict[str, Any]:
        def segments(values: dict[str, dict[str, Any]]) -> dict[str, Any]:
            return {
                name: {k: v for k, v in row.items() if k != "peak"}
                | {
                    "net_return": row["final_equity"] / row["initial_equity"] - 1,
                    "low_sample": row["trades"] < 30,
                }
                for name, row in values.items()
            }

        wins = losses = max_wins = max_losses = 0
        for row in self.episodes:
            wins = wins + 1 if row["net_pnl"] > 0 else 0
            losses = losses + 1 if row["net_pnl"] < 0 else 0
            max_wins, max_losses = max(max_wins, wins), max(max_losses, losses)
        n = len(self.episodes)
        hits = sum(row["target_met"] for row in self.episodes)
        return {
            "initial_equity": INITIAL,
            "final_equity": self.equity,
            "net_return": self.equity / INITIAL - 1,
            "max_drawdown": self.max_drawdown,
            "fee_cost": self.fees,
            "slippage_cost": self.friction,
            "total_funding": None if self.blocked else self.funding,
            "executed_turnover_notional": self.turnover,
            "same_quantity_gross_pnl": None if self.blocked else self.gross_pnl,
            "observed_spread_cost": self.spread_cost,
            "net_pnl_per_completed_trade": (self.equity - INITIAL) / n
            if n and not self.blocked
            else None,
            "break_even_all_execution_cost_bps_on_actual_quantities": (
                (self.equity - INITIAL + self.fees + self.friction + self.spread_cost)
                / self.turnover
                * 10_000
            )
            if self.turnover and not self.blocked
            else None,
            "complete_trades": n,
            "target_attainment_count": hits,
            "target_attainment_fraction_of_trades": hits / n if n else None,
            "calendar_days": self.calendar_days,
            "unavailable_data_days": self.unavailable_days,
            "no_trade_days": self.calendar_days - n if self.calendar_days else None,
            "target_attainment_fraction_of_calendar_days": hits / self.calendar_days
            if self.calendar_days
            else None,
            "mean_net_margin_roi": math.fsum(row["net_margin_roi"] for row in self.episodes) / n
            if n
            else None,
            "worst_net_margin_roi": min(
                (row["net_margin_roi"] for row in self.episodes), default=None
            ),
            "max_winning_trade_streak": max_wins,
            "max_losing_trade_streak": max_losses,
            "long_net_pnl": math.fsum(row["net_pnl"] for row in self.episodes if row["side"] == 1),
            "short_net_pnl": math.fsum(
                row["net_pnl"] for row in self.episodes if row["side"] == -1
            ),
            "observed_hours": self.observed_hours,
            "completed_trades_per_observed_hour": n / self.observed_hours
            if self.observed_hours and not self.blocked
            else None,
            "folds": segments(self.folds),
            "years": segments(self.years),
            "risk_invalid": self.risk_invalid,
            "failed": self.blocked,
            "failures": self.failures,
            "counts": self.counts,
            "episodes": self.episodes,
        }


def choose_finalist(trials: dict[str, Any]) -> str | None:
    ranked = []
    for name, row in trials.items():
        cases = [row.get(cost, {}) for cost in ("base", "stress")]
        development = [case.get("folds", {}).get("development") for case in cases]
        if any(
            d is None
            or d.get("failed", False)
            or d.get("risk_invalid", False)
            or d["trades"] == 0
            or d["net_return"] <= 0
            for d in development
        ):
            continue
        ranked.append((-min(d["net_return"] for d in development if d is not None), name))
    return min(ranked)[1] if ranked else None


def assess(
    trials: dict[str, Any], references: dict[str, Any], model: str
) -> tuple[dict[str, Any], str | None]:
    finalist = choose_finalist(trials)
    groups: tuple[str, ...] = ("development", "validation", "diagnostic")
    if model == "micro":
        groups += ("recent_diagnostic",)
    for name, row in trials.items():
        required: tuple[str, ...]
        if model == "daily":
            suffix = "bracket" if name.endswith("-bracket") else "time"
            required = (f"always_long_{suffix}", f"always_short_{suffix}")
        else:
            required = ("momentum",)
        matched = [references[key] for key in required if key in references]
        qualified_reference = len(matched) == len(required) and all(
            not ref[c]["failed"]
            and not ref[c]["risk_invalid"]
            and not ref[c]["folds"].get("development", {}).get("failed", False)
            and not ref[c]["folds"].get("development", {}).get("risk_invalid", False)
            and ref[c]["folds"].get("development", {}).get("trades", 0) > 0
            for ref in matched
            for c in ("base", "stress")
        )
        if any(row[c]["failed"] for c in ("base", "stress")):
            verdict, reason = "failed", "retained_calculation_or_execution_failure"
        elif any(row[c]["risk_invalid"] for c in ("base", "stress")):
            verdict, reason = (
                "counterfactual_risk_invalid",
                "possible_liquidation_or_margin_failure",
            )
        elif any(
            row[c]["folds"].get(g, {}).get("trades", 0) == 0
            for c in ("base", "stress")
            for g in groups
        ):
            verdict, reason = "insufficient", "missing_trades_in_required_fold"
        elif not qualified_reference:
            verdict, reason = "insufficient", "matching_benchmark_unqualified"
        else:
            positive = all(
                row[c]["folds"][g]["net_return"] > 0 for c in ("base", "stress") for g in groups
            )
            better = all(
                row[c]["folds"]["development"]["net_return"]
                > ref[c]["folds"]["development"]["net_return"]
                for ref in matched
                for c in ("base", "stress")
            )
            admitted = positive and better and name == finalist
            verdict = "exploratory_candidate" if admitted else "reject"
            reason = (
                "positive_development_selected_candidate"
                if admitted
                else "fold_cost_or_matched_control_test_failed"
            )
        row["verdict"], row["assessment_reason"] = verdict, reason
    return trials, finalist


def summaries(states: dict[str, dict[str, Totals]]) -> dict[str, Any]:
    return {
        name: {cost: state.result() for cost, state in cases.items()}
        for name, cases in states.items()
    }


def run_daily(index: dict[str, Any], output: PrivateOutput) -> dict[str, Any]:
    states = {c.name: {cost: Totals() for cost in ("base", "stress")} for c in daily.configs()}
    refs = {name: {cost: Totals() for cost in ("base", "stress")} for name in daily.BENCHMARK_NAMES}
    errors = []
    for month, frames, hourly, extremes, eligible in daily_months(index):
        group = (
            "development" if month < "2025" else "validation" if month < "2026" else "diagnostic"
        )
        for config in daily.configs():
            pair = states[config.name]
            if any(state.blocked for state in pair.values()):
                continue
            try:
                spec = daily_spec(month, frames, pair["base"].equity)
                result = daily.evaluate(
                    config,
                    spec,
                    frames,
                    hourly,
                    extremes,
                    stress_initial_equity=pair["stress"].equity,
                    eligible_days=eligible,
                )
                artifact(output, f"{month}-{config.name}", result)
                for cost in ("base", "stress"):
                    pair[cost].add(result[cost], group, month[:4], is_micro=False)
            except (HarnessError, ValueError, KeyError, TypeError, ArithmeticError) as error:
                errors.append({"config": config.name, "month": month, "error": str(error)[:500]})
                for state in pair.values():
                    state.fail(error, group)
        initial = {
            name: (cases["base"].equity, cases["stress"].equity) for name, cases in refs.items()
        }
        reference = daily.benchmarks(
            daily_spec(month, frames, INITIAL),
            frames,
            extremes,
            initial_equities=initial,
            eligible_days=eligible,
        )
        artifact(output, f"{month}-benchmarks", reference)
        for name, cases in refs.items():
            for cost, state in cases.items():
                state.add(reference[name][cost], group, month[:4], is_micro=False)
        print("Daily month complete:", month, flush=True)
    baseline = summaries(refs)
    trials, finalist = assess(
        summaries(states), {n: r for n, r in baseline.items() if n != "cash"}, "daily"
    )
    return {
        "model": "daily",
        "study_id": "daily-momentum-20261113-v1",
        "trials": trials,
        "benchmarks": baseline,
        "development_selected_finalist": finalist,
        "trial_errors": errors,
        "unique_configurations": 8,
        "configurations": {c.name: asdict(c) for c in daily.configs()},
    }


def flat_short_window(events: list[micro.MicroEvent], equity: float) -> dict[str, Any]:
    require(
        bool(events) and events[-1].mono_ns - events[0].mono_ns <= 333 * micro.SECOND,
        "provably too short for every registered entry",
    )
    return {
        "status": "complete",
        "initial_equity": equity,
        "final_equity": equity,
        "fee_cost": 0.0,
        "slippage_cost": 0.0,
        "total_funding": 0.0,
        "same_quantity_gross_mid_pnl": 0.0,
        "execution_spread_and_friction_cost": 0.0,
        "ledger": [],
        "trades": [],
        "fills": [],
        "open_position": None,
        "counts": {"provably_precluded_short_windows": 1},
        "observed_hours": (events[-1].mono_ns - events[0].mono_ns) / micro.SECOND / 3600,
        "proof": "Minimum warmup plus full holding/deferred exits precludes entry at <=333s.",
    }


def run_micro(index: dict[str, Any], output: PrivateOutput) -> dict[str, Any]:
    _, funding = funding_censuses(index)
    configurations = {c.name: c for c in micro.configs()}
    configurations["momentum-benchmark"] = micro.MicroConfig("momentum-benchmark", 30, 0.0, 0.02)
    states = {name: {cost: Totals() for cost in ("base", "stress")} for name in configurations}
    coverage = []
    for capture in index["captures"]:
        windows = 0
        for name, events, metadata in capture_windows(capture):
            if metadata.get("quality_only"):
                coverage.append(metadata)
                output.json(name + ".json", metadata)
                continue
            windows += 1
            output.json(name + "-admission.json", metadata)
            if len(events) < 2 or events[-1].mono_ns <= events[0].mono_ns:
                continue
            year = str(dt.datetime.fromtimestamp(events[0].utc_ns / 1e9, dt.UTC).year)
            if events[-1].mono_ns - events[0].mono_ns <= 333 * micro.SECOND:
                carried: dict[str, dict[str, float]] = {}
                for config_name, pair in states.items():
                    carried[config_name] = {}
                    for cost, state in pair.items():
                        if not state.blocked:
                            flat = flat_short_window(events, state.equity)
                            state.add(flat, metadata["group"], year, is_micro=True)
                            carried[config_name][cost] = state.equity
                artifact(
                    output,
                    name + "-flat-proof",
                    {
                        "admission": metadata,
                        "proof": "Fixed horizon precludes entry in this short window.",
                        "unchanged_accounts": carried,
                    },
                )
                continue
            for config_name, config in configurations.items():
                for cost, state in states[config_name].items():
                    if state.blocked:
                        continue
                    try:
                        result = micro.replay(
                            config,
                            events,
                            funding,
                            state.equity,
                            fee_bps=4.5 if cost == "base" else 9.0,
                            slippage_bps=1.0 if cost == "base" else 2.0,
                            latency_ns=500_000_000 if cost == "base" else 1_000_000_000,
                            end_mono_ns=events[-1].mono_ns,
                            benchmark=config_name == "momentum-benchmark",
                        )
                        artifact(output, name + "-" + config_name + "-" + cost, result)
                        state.add(result, metadata["group"], year, is_micro=True)
                    except (
                        HarnessError,
                        ValueError,
                        KeyError,
                        TypeError,
                        ArithmeticError,
                    ) as error:
                        output.json(
                            name + "-" + config_name + "-" + cost + "-failure.json",
                            {"status": "failed", "error": str(error)[:500]},
                        )
                        state.fail(error, metadata["group"])
        print("Micro capture complete:", capture["name"], "windows", windows, flush=True)
    results = summaries(states)
    reference = results.pop("momentum-benchmark")
    trials, finalist = assess(results, {"momentum": reference}, "micro")
    return {
        "model": "micro",
        "study_id": "micro-flow-20261113-v1",
        "trials": trials,
        "benchmarks": {"momentum": reference, "cash": {"net_return": 0.0}},
        "development_selected_finalist": finalist,
        "source_coverage": coverage,
        "unique_configurations": 8,
        "configurations": {c.name: asdict(c) for c in micro.configs()},
        "conditional_intact_windows": True,
    }


def run(model: str, path: Path, expected: str, commit: str, destination: Path) -> dict[str, Any]:
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    require(model in {"daily", "micro"}, "registered model")
    identity = verify_code(commit)
    content = read_bytes(path, 8 << 20)
    require(sha(content) == expected, "operator-pinned input index")
    index = document(content)
    verify_sources(index, model)
    with PrivateOutput(destination) as output:
        output.write("input.json", content)
        output.write("plan.md", read_bytes(PLAN, 1 << 20))
        output.json(
            "claim.json",
            {
                "model": model,
                "source_venue": "BINANCE_USDM_PROXY" if model == "daily" else "HYPERLIQUID",
                "quote_currency": "USDT" if model == "daily" else "USDC",
                "source_environment": "DEV_VM_OFFLINE",
                "source_commit": commit,
                "input_sha256": expected,
                "implementation_sha256": identity,
                "plan_sha256": index["plan_sha256"],
                "mode": "PAPER",
                "data_status": "exposed_retrospective_no_fresh_oos",
                "promotion_decision": "forbidden",
            },
        )
        try:
            result = run_daily(index, output) if model == "daily" else run_micro(index, output)
            result.update(
                source_commit=commit,
                input_sha256=expected,
                plan_sha256=index["plan_sha256"],
                promotion_decision="forbidden",
                data_status="exposed_retrospective_no_fresh_oos",
                source_environment="DEV_VM_OFFLINE",
                source_venue="BINANCE_USDM_PROXY" if model == "daily" else "HYPERLIQUID",
                quote_currency="USDT" if model == "daily" else "USDC",
                reference_leverage=10,
                pilot_unique_configurations_to_date=28,
                image_digest=None,
            )
            require(verify_code(commit) == identity, "source changed during research")
            require(read_bytes(path, 8 << 20) == content, "input changed during research")
            verify_sources(index, model)
            output.json("result.json", result)
            failures = sum(
                any(row[c]["failed"] for c in ("base", "stress"))
                for row in result["trials"].values()
            )
            output.json(
                "completion.json",
                {
                    "status": "complete_with_trial_errors" if failures else "complete",
                    "failed_configurations": failures,
                    "study_id": result["study_id"],
                },
            )
            return result
        except BaseException as error:
            output.json("failure.json", {"status": "failed", "error_type": type(error).__name__})
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    bind = commands.add_parser("prepare-inputs")
    for name in ("daily", "older", "recent", "funding", "bad-days"):
        bind.add_argument("--" + name, type=Path, required=True)
    bind.add_argument("--output", type=Path, required=True)
    execute = commands.add_parser("run")
    execute.add_argument("--model", choices=("daily", "micro"), required=True)
    execute.add_argument("--input", type=Path, required=True)
    execute.add_argument("--expected-input-sha256", required=True)
    execute.add_argument("--source-commit", required=True)
    execute.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == "prepare-inputs":
            paths = {
                name: getattr(args, name).absolute()
                for name in ("daily", "older", "recent", "funding", "bad_days")
            }
            result = prepare(paths)
            with PrivateOutput(args.output.absolute()) as output:
                output.json("input.json", result)
                output.json("completion.json", {"status": "prepared_no_financial_scoring"})
            print("Source identities prepared; no financial scoring.")
        else:
            result = run(
                args.model,
                args.input.absolute(),
                args.expected_input_sha256,
                args.source_commit,
                args.output.absolute(),
            )
            print(
                json.dumps(
                    {
                        "model": args.model,
                        "verdicts": {n: t["verdict"] for n, t in result["trials"].items()},
                    },
                    sort_keys=True,
                )
            )
            if any(t["verdict"] == "failed" for t in result["trials"].values()):
                return 2
        return 0
    except (
        HarnessError,
        ValueError,
        OSError,
        KeyError,
        TypeError,
        ArithmeticError,
        subprocess.SubprocessError,
    ) as error:
        print("Research refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
