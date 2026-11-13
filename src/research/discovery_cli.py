"""Prepare or execute the first bounded, explicitly exposed-history discovery batch."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from research.btc_flow_io import PrivateOutput, read_bytes
from research.discovery import benchmarks, configs, evaluate_config
from research.discovery_io import (
    END,
    PLAN,
    SPLIT,
    START,
    STUDY_ID,
    SYMBOLS,
    as_spec,
    check_index,
    observations,
    plan_hash,
    prepare,
    source_identity,
)
from research.harness.errors import HarnessError
from research.volume_repair_io import document, require, sha256


def verify_code(commit: str) -> dict[str, str]:
    require(bool(re.fullmatch("[0-9a-f]{40}", commit)), "full source commit required")
    root = Path(__file__).resolve().parents[2]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    require(head == commit, "declared source must be actual checkout HEAD")
    drift = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "src"],
        cwd=root,
        text=True,
    ).strip()
    require(not drift, "reviewed source tree must be clean, including imported dependencies")
    identities = source_identity()
    for name, digest in identities.items():
        content = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=root)
        require(sha256(content) == digest, "reviewed source file changed: " + name)
    return identities


def compact(result: dict[str, Any]) -> dict[str, Any]:
    """Keep complete ledgers in per-trial artifacts, not every leaderboard row."""
    out = dict(result)
    for key in ("base", "stress"):
        if key in out:
            out[key] = {k: v for k, v in out[key].items() if k not in {"ledger", "trades"}}
    return out


def development_rank(rows: list[dict[str, Any]]) -> list[str]:
    """Diagnostic outcomes cannot influence the predeclared finalist ranking."""

    def key(row: dict[str, Any]) -> tuple[float, str]:
        score = min(
            row[cost]["segments"]["development"]["net_return"] for cost in ("base", "stress")
        )
        return -float(score), str(row["config"]["name"])

    usable = [row for row in rows if "base" in row and "stress" in row]
    return [str(row["config"]["name"]) for row in sorted(usable, key=key)[:2]]


def run_batch(
    input_path: Path,
    expected: str,
    commit: str,
    output_path: Path,
) -> dict[str, object]:
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    before = verify_code(commit)
    content = read_bytes(input_path, 2 << 20)
    require(sha256(content) == expected, "operator-pinned discovery input index")
    index = document(content)
    check_index(index)
    with PrivateOutput(output_path) as output:
        output.write("input.json", content)
        output.write("plan.md", read_bytes(PLAN, 1 << 20))
        output.json(
            "run-claim.json",
            {
                "study_id": STUDY_ID,
                "source_commit": commit,
                "implementation_sha256": before,
                "input_sha256": expected,
                "plan_sha256": plan_hash(),
                "mode": "PAPER",
                "data_status": "development_exposed",
                "features_availability_assumed": True,
                "promotion_decision": "forbidden",
            },
        )
        try:
            bars, frames, spec, quality = observations(index)
            output.json("data-quality.json", quality)
            rows: list[dict[str, Any]] = []
            for config in configs():
                try:
                    result = evaluate_config(config, spec, frames, bars, SPLIT)
                except (HarnessError, ValueError, KeyError, TypeError, ArithmeticError) as error:
                    result = {
                        "config": asdict(config),
                        "verdict": "failed",
                        "error": {"type": type(error).__name__, "message": str(error)[:500]},
                    }
                output.json(f"trial-{config.name}.json", result)
                rows.append(compact(result))
                print(f"Trial {config.name}: {result['verdict']}", flush=True)
                del result
            reference = benchmarks(spec, frames, SPLIT)
            output.json("benchmarks.json", reference)
            require(verify_code(commit) == before, "source changed during experiment")
            require(read_bytes(input_path, 2 << 20) == content, "input index changed")
            check_index(index)  # Rehash every selected original source before finalization.
            final = {
                "schema": "discovery-result-v1",
                "study_id": STUDY_ID,
                "source_commit": commit,
                "input_sha256": expected,
                "plan_sha256": plan_hash(),
                "data_status": "development_exposed",
                "venue": "BINANCE_USDM",
                "quote_currency": "USDT",
                "features_availability_assumed": True,
                "promotion_decision": "forbidden",
                "universe": list(SYMBOLS),
                "start_ms": START,
                "split_ms": SPLIT,
                "end_ms": END,
                "spec": as_spec(spec),
                "data_quality": quality,
                "configuration_count": len(rows),
                "max_discovery_configurations": 40,
                "failed_count": sum(row["verdict"] == "failed" for row in rows),
                "development_ranked_finalists": development_rank(rows),
                "trials": rows,
                "limitations": [
                    "Both chronological halves are exposed history, not fresh OOS.",
                    "Selected survivor assets and a convenient accounting-complete month.",
                    "Historical archive timestamps and five-minute OI availability are assumed.",
                    "Close references, minute marks and costs lack executable-quote proof.",
                    "Native funding; unvalidated collateral, liquidation and intraminute risk.",
                    "Positive exploratory candidates do not establish a Hyperliquid edge.",
                    "Two paired cost checks; twelve fixed configurations, no extra search.",
                ],
            }
            output.json("leaderboard.json", final)
            output.json(
                "completion.json",
                {
                    "status": "complete_with_errors" if final["failed_count"] else "complete",
                    "study_id": STUDY_ID,
                    "input_sha256": expected,
                    "configuration_count": len(rows),
                    "failed_count": sum(row["verdict"] == "failed" for row in rows),
                },
            )
            return final
        except BaseException as error:
            output.json("failure.json", {"status": "failed", "type": type(error).__name__})
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    bind = commands.add_parser("prepare-inputs")
    bind.add_argument("--legacy-index", type=Path, required=True)
    bind.add_argument("--metrics-inventory", type=Path, required=True)
    bind.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--expected-input-sha256", required=True)
    run.add_argument("--source-commit", required=True)
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == "prepare-inputs":
            body = prepare(args.legacy_index.absolute(), args.metrics_inventory.absolute())
            with PrivateOutput(args.output.absolute()) as output:
                output.json("input.json", body)
                output.json("completion.json", {"status": "prepared_no_financial_decoding"})
            print("Prepared discovery input identities without financial decoding.")
        else:
            result = run_batch(
                args.input.absolute(),
                args.expected_input_sha256,
                args.source_commit,
                args.output.absolute(),
            )
            print(
                json.dumps(
                    {
                        "study_id": STUDY_ID,
                        "trials": result["configuration_count"],
                        "output": str(args.output),
                    },
                    sort_keys=True,
                )
            )
            if result["failed_count"]:
                return 2
        return 0
    except (
        HarnessError,
        OSError,
        ValueError,
        KeyError,
        csv.Error,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ) as error:
        print("Discovery refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
