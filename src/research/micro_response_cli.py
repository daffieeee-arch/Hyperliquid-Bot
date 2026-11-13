"""Create-only bounded native price-response diagnostic; no trading admission."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from research.btc_flow_io import PrivateOutput, read_bytes
from research.harness.errors import HarnessError
from research.micro_response import COSTS, HORIZONS, POLICIES, Window
from research.micro_response_io import (
    INPUT_SHA256,
    PLAN,
    ROOT,
    STUDY_ID,
    funding_ranges,
    funding_times,
    load,
    observations,
    qualify,
    verify_coverage,
    verify_sources,
)
from research.micro_response_stats import Summary
from research.volume_repair_io import require

FROZEN_SOURCE = "a52eeee4aec9743d1673ccae4f7ff711295216ee"
MAX_ROWS = 10_000_000
MAX_OUTPUT_BYTES = 4 << 30
CHUNK_BYTES = 4 << 20


def identity(commit: str) -> dict[str, str]:
    require(len(commit) == 40 and all(c in "0123456789abcdef" for c in commit), "actual Git SHA")
    require(
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        == commit,
        "actual clean reviewed source HEAD",
    )
    names = subprocess.check_output(["git", "ls-files", "src"], cwd=ROOT, text=True).splitlines()
    names += [str(PLAN.relative_to(ROOT)), "docs/experiments/exp_two_models_20261113.md"]
    require(
        not subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", "src", str(PLAN)],
            cwd=ROOT,
            text=True,
        ).strip(),
        "clean imported source and new protocol",
    )
    identities = {}
    for name in names:
        raw = read_bytes(ROOT / name, 8 << 20)
        require(
            raw == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT),
            "reviewed source bytes changed: " + name,
        )
        identities[name] = hashlib.sha256(raw).hexdigest()
    for name in (
        "src/research/micro_flow.py",
        "src/research/micro_flow_io.py",
        "src/hyperliquid_bot/paper_engine/precision.py",
    ):
        require(
            read_bytes(ROOT / name, 1 << 20)
            == subprocess.check_output(["git", "show", f"{FROZEN_SOURCE}:{name}"], cwd=ROOT),
            "original signal/source/grid implementation must remain frozen: " + name,
        )
    return identities


class Records:
    def __init__(self, output: PrivateOutput, summary: Summary) -> None:
        self.output, self.summary = output, summary
        self.buffer = bytearray()
        self.rows: Counter[str] = Counter()
        self.total_bytes = 0
        self.chunks: list[dict[str, Any]] = []

    def emit(self, row: dict[str, Any]) -> None:
        raw = (
            json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        )
        require(len(raw) <= 16 << 10, "bounded diagnostic record")
        self.rows[row["kind"]] += 1
        self.total_bytes += len(raw)
        require(
            sum(self.rows.values()) <= MAX_ROWS and self.total_bytes <= MAX_OUTPUT_BYTES,
            "complete diagnostic output count/byte bound; never drop labels",
        )
        self.summary.consume(row)
        if len(self.buffer) + len(raw) > CHUNK_BYTES:
            self.flush()
        self.buffer.extend(raw)

    def flush(self) -> None:
        if self.buffer:
            require(len(self.chunks) < 2048, "bounded diagnostic chunk manifest")
            name = f"observations-{len(self.chunks):05d}.jsonl.gz"
            raw = gzip.compress(bytes(self.buffer), compresslevel=1, mtime=0)
            self.output.write(name, raw)
            self.chunks.append(
                {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            )
            self.buffer.clear()


def run(body: dict[str, Any], output: PrivateOutput) -> dict[str, Any]:
    funding = funding_times(body)
    ranges = funding_ranges(body)
    coverage = []
    for capture in body["captures"]:
        metadata = qualify(capture)
        coverage.append(metadata)
        output.json(capture["name"] + "-source.json", metadata)
        print("Source-only capture qualified:", capture["name"], flush=True)
    coverage_sha = verify_coverage(coverage)
    output.json(
        "source-qualification.json", {"coverage_sha256": coverage_sha, "captures": coverage}
    )
    summary = Summary()
    records = Records(output, summary)
    try:
        for capture, metadata in zip(body["captures"], coverage, strict=True):
            windows = {row["id"]: row for row in metadata["windows"]}
            current: Window | None = None
            finished: set[int] = set()
            for item in observations(capture):
                if not item.window:
                    continue
                if current is None:
                    require(item.window not in finished, "closed source window cannot resume")
                    current = Window(
                        windows[item.window],
                        capture["name"],
                        capture["group"],
                        funding,
                        ranges,
                        records.emit,
                    )
                require(item.window == current.meta["id"], "complete chronological source windows")
                if item.end_reason:
                    current.finish()
                    finished.add(item.window)
                    current = None
                else:
                    current.receive(item)
            require(current is None and finished == set(windows), "complete second source pass")
            print("Diagnostic capture complete:", capture["name"], flush=True)
    finally:
        records.flush()
    return {
        "study_id": STUDY_ID,
        "source_venue": "HYPERLIQUID_NATIVE_PUBLIC",
        "source_environment": "DEV_VM_OFFLINE",
        "image_digest": None,
        "data_status": "exposed_retrospective_no_fresh_oos",
        "fresh_oos": False,
        "primary_horizon_seconds": 300,
        "horizons_seconds": HORIZONS,
        "cost_scenarios": list(COSTS),
        "policies": [name for name, _, _ in POLICIES],
        "new_alpha_configurations": 0,
        "programme_unique_configurations": 32,
        "selector": None,
        "promotion_decision": "forbidden",
        "interpretation": "conditional_price_response_not_fills_trade_profit_or_account_return",
        "funding_cash_certified": False,
        "qualified_funding_request_ranges_ms": ranges,
        "capacity_certified": False,
        "coverage_sha256": coverage_sha,
        "conditional_observation_ns": sum(c["observation_ns"] for c in coverage),
        "capture_count": len(coverage),
        "window_count": sum(len(c["windows"]) for c in coverage),
        "record_counts": dict(records.rows),
        "uncompressed_record_bytes": records.total_bytes,
        "record_chunks": records.chunks,
        "cells": summary.result(),
        "limitations": [
            "Changes-only quote eligibility and missing future labels may be MNAR.",
            "Overlapping forecasts are dependent; day-cluster intervals are descriptive only.",
            "Measured native timestamp age is not network latency or NTP certification.",
            "Capacity, queueing, funding cash, partial exits and venue fills remain unqualified.",
            "Source liveness, receipt quote freshness and native timestamp age are distinct.",
            "No target, horizon, direction, fee, leverage or strategy selection is performed.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args(argv)
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    code = identity(args.source_commit)
    plan_sha = hashlib.sha256(read_bytes(PLAN, 1 << 20)).hexdigest()
    require(plan_sha == args.expected_plan_sha256, "registered response protocol hash")
    with PrivateOutput(args.output) as output:
        output.json(
            "claim.json",
            {
                "study_id": STUDY_ID,
                "source_commit": args.source_commit,
                "plan_sha256": plan_sha,
                "input_sha256": INPUT_SHA256,
                "code_identities": code,
                "trading_mode": "PAPER_OFFLINE_DIAGNOSTIC",
            },
        )
        try:
            body = load(args.input)
            verify_sources(body)
            result = run(body, output)
            require(identity(args.source_commit) == code, "imported source changed during study")
            require(
                hashlib.sha256(read_bytes(args.input, 8 << 20)).hexdigest() == INPUT_SHA256,
                "pinned source index changed during study",
            )
            verify_sources(body)
            result.update(
                source_commit=args.source_commit, plan_sha256=plan_sha, input_sha256=INPUT_SHA256
            )
            output.json("result.json", result)
            output.json(
                "completion.json",
                {
                    "study_id": STUDY_ID,
                    "status": "complete",
                    "captured_streams": 11,
                    "source_parts": 9323,
                    "strategy_selection_or_trading_admission": False,
                },
            )
            return 0
        except (HarnessError, ArithmeticError, ValueError, KeyError, TypeError, OSError) as error:
            output.json(
                "failure.json",
                {
                    "study_id": STUDY_ID,
                    "status": "failed",
                    "type": type(error).__name__,
                    "error": str(error)[:1000],
                    "financial_result": None,
                    "no_resume": True,
                },
            )
            raise


if __name__ == "__main__":
    sys.exit(main())
