"""Four frozen OI hypotheses on broader exposed history; no selection or broker."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from research import discovery
from research.btc_flow_io import PrivateOutput, read_bytes
from research.harness.errors import HarnessError
from research.oi_recovery_io import (
    FOLDS,
    INVENTORY_SHA256,
    PLAN,
    ROOT,
    SPLITS,
    STUDY_ID,
    OpenInterest,
    bars,
    encoded,
    load,
    load_oi,
)
from research.regime_cli import Account, code_identity, compressed, uncertainty
from research.regime_io import Segment, Sources
from research.regime_momentum import evaluate_case
from research.repaired_accounting import Instrument, ReplaySpec, Target
from research.two_models_io import BTC, END, HOUR, START, check_index, native_source, read_record
from research.volume_repair_io import json_value, require

FROZEN_SOURCE = "bd4bf7c316daed18fbaf46429b579c6266ef494a"
PRICE_SHA256 = "ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518"


def identity(commit: str) -> dict[str, str]:
    result = code_identity(commit)
    for name in (
        str(PLAN.relative_to(ROOT)),
        "docs/experiments/exp_discovery_batch_one.md",
        "docs/experiments/exp_discovery_batch_two.md",
    ):
        raw = read_bytes(ROOT / name, 1 << 20)
        require(
            raw == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT),
            "committed OI protocol bytes",
        )
        result[name] = hashlib.sha256(raw).hexdigest()
    for name in ("discovery.py", "discovery_io.py", "repaired_accounting.py", "regime_momentum.py"):
        source = "src/research/" + name
        require(
            read_bytes(ROOT / source, 1 << 20)
            == subprocess.check_output(["git", "show", f"{FROZEN_SOURCE}:{source}"], cwd=ROOT),
            "frozen original signal/loader/ledger/risk-helper bytes: " + name,
        )
    return result


def verify_prices(path: Path) -> None:
    raw = read_bytes(path, 8 << 20)
    require(hashlib.sha256(raw).hexdigest() == PRICE_SHA256, "original qualified price input")
    body = json_value(raw)
    require(isinstance(body, dict), "price source index object")
    assert isinstance(body, dict)
    check_index(body, verify_raw=False)
    for row in body["daily_sources"]:
        native_source(row).read()
        native_source(row, True).read()
    for row in body["funding_sources"]:
        read_record(row)


def configurations() -> tuple[discovery.DiscoveryConfig, ...]:
    selected = tuple(c for c in discovery.configs() if c.family == "oi_deleveraging")
    require(len(selected) == 4, "exact four original OI alpha configurations")
    return selected


def hourly_segment(segment: Segment, hourly: dict[int, float]) -> Segment:
    """Native trade H1 CLOSE executes; source marks/funding/extremes stay distinct."""
    frames = tuple(
        replace(frame, execution_prices={BTC: hourly[frame.timestamp_ms]})
        if frame.timestamp_ms % HOUR == 0
        else frame
        for frame in segment.frames
    )
    require(
        all(
            BTC in frame.execution_prices
            for frame in frames
            if frame.timestamp_ms % HOUR == 0 and frame.execution_prices is not None
        ),
        "all regular OI execution clocks use closed trade-hour references",
    )
    return replace(segment, frames=frames)


def spec(segment: Segment, equity: float, cost: str) -> ReplaySpec:
    require(cost in ("base", "stress"), "fixed OI execution cost scenario")
    scale = 1 if cost == "base" else 2
    return ReplaySpec(
        segment.start_ms,
        segment.end_ms,
        HOUR,
        equity,
        (Instrument(BTC, "perp", scale * 5.0, scale * 2.0),),
        tuple(frame.timestamp_ms for frame in segment.frames),
    )


def schedule(
    config: discovery.DiscoveryConfig,
    segment: Segment,
    data: dict[str, list[discovery.DiscoveryBar]],
) -> tuple[tuple[Target, ...], dict[str, Any]]:
    boundaries = tuple(t for t in SPLITS if segment.start_ms < t < segment.end_ms)
    episodes, counts = discovery.generate_episodes(
        config, data, segment.start_ms, segment.end_ms, boundaries
    )
    targets = tuple(t for t in discovery.targets(episodes) if t.execution_ms < segment.end_ms)
    return targets, {
        "config": asdict(config),
        "episodes": [asdict(e) for e in episodes],
        "source_exclusion_counts": counts,
        "split_boundaries_ms": boundaries,
        "source_segment": [segment.start_ms, segment.end_ms],
    }


def directional(targets: tuple[Target, ...], side: int) -> tuple[Target, ...]:
    require(side in (-1, 1), "fixed directional control")
    return tuple(replace(t, weights={BTC: side * 0.5} if t.weights else {}) for t in targets)


def run(prices: Sources, oi: OpenInterest, output: PrivateOutput) -> dict[str, Any]:
    configs = configurations()
    names = [c.name for c in configs] + ["cash", "persistent_long", "persistent_short"]
    names += [f"{c.name}-matched-{side}" for c in configs for side in ("long", "short")]
    accounts = {
        name: {
            cost: Account(start_ms=START, end_ms=END, folds=FOLDS) for cost in ("base", "stress")
        }
        for name in names
    }
    data = bars(prices, oi)
    attempts = 0
    for number, original in enumerate(prices.segments):
        segment = hourly_segment(original, prices.hourly)
        all_targets: dict[str, tuple[Target, ...]] = {"cash": ()}
        for name, persistent_sign in (("persistent_long", 1), ("persistent_short", -1)):
            all_targets[name] = (
                Target(segment.start_ms, segment.start_ms + HOUR, {BTC: persistent_sign * 0.5}),
            )
        for config in configs:
            targets, information = schedule(config, segment, data)
            all_targets[config.name] = targets
            compressed(output, f"segment-{number}-{config.name}-schedule", information)
            for side, sign in (("long", 1), ("short", -1)):
                all_targets[f"{config.name}-matched-{side}"] = directional(targets, sign)
        for name in names:
            for cost, account in accounts[name].items():
                if account.blocked:
                    output.json(
                        f"segment-{number}-{name}-{cost}-blocked.json",
                        {"status": "not_processed_prior_failure", "no_resume": True},
                    )
                    continue
                request = spec(segment, account.equity, cost)
                compressed(
                    output,
                    f"segment-{number}-{name}-{cost}-request",
                    {"spec": asdict(request), "targets": [asdict(t) for t in all_targets[name]]},
                )
                attempts += 1
                try:
                    case = evaluate_case(
                        request, segment.frames, all_targets[name], segment.minute_extremes
                    )
                    compressed(output, f"segment-{number}-{name}-{cost}-case", case)
                    account.add(case, segment.frames)
                except (HarnessError, ArithmeticError, ValueError) as error:
                    account.fail(error)
                    output.json(
                        f"segment-{number}-{name}-{cost}-failure.json",
                        {
                            "status": "failed",
                            "error": str(error)[:1000],
                            "failed_segment_state": "unknown_not_serialized_by_legacy_simulator",
                            "prior_completed_segment_equity": account.equity,
                            "not_failure_equity": True,
                            "no_resume": True,
                        },
                    )
        print("OI source segment complete:", number, flush=True)
    results = {
        name: {cost: account.summary() for cost, account in pair.items()}
        for name, pair in accounts.items()
    }
    comparisons: dict[str, Any] = {}
    for config in configs:
        comparisons[config.name] = {}
        for side in ("long", "short"):
            comparisons[config.name][side] = {}
            for cost in ("base", "stress"):
                left = results[config.name][cost]
                right = results[f"{config.name}-matched-{side}"][cost]
                available = not any(c["failed"] or c["risk_invalid"] for c in (left, right))
                row: dict[str, Any] = {"available": available}
                if available:
                    a = [r["net_return"] for r in left["daily_equity_returns"]]
                    b = [r["net_return"] for r in right["daily_equity_returns"]]
                    difference = [x - y for x, y in zip(a, b, strict=True)]
                    row["daily_return_difference_uncertainty"] = {
                        str(block): uncertainty(difference, block) for block in (7, 30)
                    }
                comparisons[config.name][side][cost] = row
    return {
        "study_id": STUDY_ID,
        "accounts": results,
        "comparisons": comparisons,
        "configurations": [asdict(c) for c in configs],
        "selector": None,
        "new_alpha_configurations": 0,
        "programme_unique_configurations": 32,
        "fresh_oos": False,
        "data_status": "exposed_retrospective_no_fresh_oos",
        "source_venue": "BINANCE_USDM_PROXY_NOT_EXECUTABLE_HYPERLIQUID",
        "source_environment": "DEV_VM_OFFLINE",
        "quote_currency": "USDT",
        "image_digest": None,
        "promotion_decision": "forbidden",
        "case_attempts": attempts,
        "expected_segment_cost_cases": len(names) * 2 * len(prices.segments),
        "collateral_convention": "additional_2x_entry_MARK_margin_1.25pct_minute_extreme_screen",
        "margin_ROI_not_comparable_to_old_unqualified_collateral_batch": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--oi-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args(argv)
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    code = identity(args.source_commit)
    plan_sha = hashlib.sha256(read_bytes(PLAN, 1 << 20)).hexdigest()
    require(plan_sha == args.expected_plan_sha256, "registered broader OI protocol hash")
    with PrivateOutput(args.output) as output:
        output.json(
            "claim.json",
            {
                "study_id": STUDY_ID,
                "source_commit": args.source_commit,
                "plan_sha256": plan_sha,
                "price_input_sha256": PRICE_SHA256,
                "oi_inventory_sha256": INVENTORY_SHA256,
                "code_identities": code,
                "trading_mode": "PAPER_OFFLINE_RESEARCH_ONLY",
            },
        )
        try:
            verify_prices(args.prices)
            prices, oi = load(args.prices, args.oi_index)
            output.write("oi-availability-mask.json.gz", gzip.compress(encoded(oi.mask), mtime=0))
            output.json(
                "source-qualification.json",
                {"prices": prices.source_summary, "oi": oi.summary, "folds": FOLDS},
            )
            output.json("oi-native-daily-quality.json", oi.daily_quality)
            result = run(prices, oi, output)
            require(identity(args.source_commit) == code, "source identity changed during OI study")
            verified = load_oi(args.oi_index)
            require(verified.summary == oi.summary, "OI source/mask changed during study")
            verify_prices(args.prices)
            result.update(
                source_commit=args.source_commit,
                plan_sha256=plan_sha,
                price_input_sha256=PRICE_SHA256,
                oi_inventory_sha256=INVENTORY_SHA256,
                oi_mask_sha256=oi.summary["full_mask_sha256"],
            )
            output.json("result.json", result)
            failed = sum(
                account["failed"]
                for pair in result["accounts"].values()
                for account in pair.values()
            )
            output.json(
                "completion.json",
                {
                    "study_id": STUDY_ID,
                    "status": "complete_with_trial_errors" if failed else "complete",
                    "failed_account_cost_chains": failed,
                    "case_attempts": result["case_attempts"],
                    "expected_segment_cost_cases": result["expected_segment_cost_cases"],
                },
            )
            return 2 if failed else 0
        except (HarnessError, ArithmeticError, ValueError, KeyError, TypeError, OSError) as error:
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
