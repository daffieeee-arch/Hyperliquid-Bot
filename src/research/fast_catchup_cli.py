"""Offline four-chain C100 replay; explicit source SHA and PAPER before I/O."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import UnsafeTradingModeError, require_local_paper_mode
from research.btc_flow_io import PrivateOutput, read_bytes
from research.fast_catchup import COSTS, Replay, text
from research.fast_catchup_io import (
    AMENDMENT,
    PLAN,
    PLAN_SHA,
    ROOT,
    STUDY,
    Streams,
    funding,
    load,
    merged,
    project,
    verify_projection,
)
from research.harness.errors import HarnessError
from research.two_models_io import read_record
from research.volume_repair_io import require


def identity(commit: str) -> dict[str, str]:
    require(len(commit) == 40 and set(commit) <= set("0123456789abcdef"), "actual source SHA")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    require(head == commit, "actual checkout must equal requested source SHA")
    names = subprocess.check_output(["git", "ls-files", "src"], cwd=ROOT, text=True).splitlines()
    names.extend(str(p.relative_to(ROOT)) for p in (PLAN, AMENDMENT))
    drift = subprocess.check_output(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "src",
            str(PLAN),
            str(AMENDMENT),
        ],
        cwd=ROOT,
        text=True,
    )
    require(not drift.strip(), "complete source/protocol must be committed and clean")
    result = {}
    for name in names:
        data = read_bytes(ROOT / name, 8 << 20)
        require(
            data == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT),
            "source bytes must equal committed identity: " + name,
        )
        result[name] = hashlib.sha256(data).hexdigest()
    require(result[str(PLAN.relative_to(ROOT))] == PLAN_SHA, "initial protocol pin preserved")
    return result


def recheck(index: dict[str, Any]) -> None:
    read_record(index["source_acceptance"])
    read_record(index["funding"]["source"], 1 << 20)
    for lane in index["captures"].values():
        for ref in lane["parts"]:
            read_record(ref, 4 << 20)
        for ref in lane["metadata"]:
            read_record(ref)


def execute(
    *, mode: object, source_index: Path, expected_sha: str, source_commit: str, destination: Path
) -> dict[str, Any]:
    require_local_paper_mode(mode)
    source_identity = identity(source_commit)
    index = load(source_index, expected_sha)
    with PrivateOutput(destination) as output:
        output.json(
            "request.json",
            {
                "study": STUDY,
                "input_index_sha256": expected_sha,
                "source_commit": source_commit,
                "code_and_protocol": source_identity,
                "mode": "PAPER",
                "execution_context": "OFFLINE_CONDITIONAL_MODEL",
                "image_digest": None,
                "image_provenance": "SOURCE_REPLAY_NOT_DEPLOYED_IMAGE",
                "financial_admission": False,
            },
        )
        result: dict[str, Any] = {
            "study": STUDY,
            "status": "INCOMPLETE",
            "cases": [],
            "capacity_500_2500": "NOT_RUN_UNREGISTERED",
        }
        try:
            projection = project(index, output)
            output.json("source-projection.json", asdict(projection))
            native_funding = funding(index, projection.start, projection.end)
            output.json(
                "funding-census.json",
                {"native_event_ms": native_funding, "source": index["funding"]},
            )
            verify_projection(projection)
            for availability in ("RAW", "WITNESS"):
                for cost in COSTS:
                    streams = Streams(output)
                    case = availability + "-" + cost.name + "-C100"
                    try:
                        replay = Replay(
                            cost,
                            projection.start,
                            projection.end,
                            index["clock_offset_ns"],
                            native_funding,
                            partial(streams.emit, case + ".jsonl"),
                        )
                        summary = replay.run(merged(output, projection, availability))
                    finally:
                        audit_binding = streams.close()
                    result["cases"].append(
                        {
                            "case": case,
                            "availability": availability,
                            "cost": asdict(cost),
                            "audit": audit_binding,
                            **summary,
                        }
                    )
                    output.json(case + ".json", text(result["cases"][-1]))
                    require(
                        not replay.fatal_run_error,
                        "fatal projected input/accounting error: " + str(replay.failed),
                    )
            verify_projection(projection)
            recheck(index)
            require(identity(source_commit) == source_identity, "source changed during run")
            require(load(source_index, expected_sha) == index, "input index changed during run")
            failed = [
                case["case"]
                for case in result["cases"]
                if case["status"] != "COMPLETE_CONDITIONAL_RESEARCH"
            ]
            result["status"] = (
                "FAILED_ONE_OR_MORE_CONDITIONAL_CHAINS"
                if failed
                else "COMPLETE_ALL_FOUR_CONDITIONAL_CHAINS"
            )
            result["attempted_case_count"] = len(result["cases"])
            result["failed_cases"] = failed
            result["source_projection"] = asdict(projection)
            result["statistical_profitability_or_PAPER_admission"] = False
            result["daily_return_or_frequency_extrapolation"] = None
            output.json("result.json", text(result))
        except (HarnessError, OSError, ValueError, ArithmeticError) as error:
            result["status"] = "FAILED_INPUT_OR_RUN"
            result["error"] = str(error)
            output.json("failure.json", text(result))
            raise
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--expected-index-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = execute(
            mode=os.environ.get("TRADING_MODE"),
            source_index=args.source_index,
            expected_sha=args.expected_index_sha256,
            source_commit=args.source_commit,
            destination=args.output,
        )
    except (HarnessError, OSError, ValueError, ArithmeticError, UnsafeTradingModeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0 if result["status"] == "COMPLETE_ALL_FOUR_CONDITIONAL_CHAINS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
