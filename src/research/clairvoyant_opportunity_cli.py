"""CLAIRVOYANT_NONTRADABLE offline diagnostic; never orders or a strategy selector."""

from __future__ import annotations

import argparse
import gzip
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from hyperliquid_bot.local_mode import require_local_paper_mode
from research import clairvoyant_opportunity as model
from research.btc_flow_io import PrivateOutput, read_bytes
from research.clairvoyant_opportunity_io import (
    AMENDMENT,
    INDEX_SHA256,
    LABEL,
    PLAN,
    ROOT,
    STUDY_ID,
    days,
    encoded,
    load_index,
    qualify,
    sha,
    verify_sources,
)
from research.harness.errors import HarnessError
from research.regime_cli import code_identity
from research.two_models_io import DAY, END, START
from research.volume_repair_io import require


class Evidence:
    def __init__(self, output: PrivateOutput) -> None:
        self.output = output
        self.total_bytes = 0

    def write(self, name: str, raw: bytes) -> None:
        require(
            len(raw) <= 64 << 20 and self.total_bytes + len(raw) <= 1 << 30,
            "bounded private diagnostic artifacts",
        )
        self.output.write(name, raw)
        os.fsync(self.output.fd)
        self.total_bytes += len(raw)

    def json(self, name: str, value: object) -> None:
        self.write(name, encoded(value))


def identity(commit: str) -> dict[str, str]:
    result = code_identity(commit)
    for path in (PLAN, AMENDMENT):
        raw = read_bytes(path, 1 << 20)
        name = str(path.relative_to(ROOT))
        require(
            raw == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT),
            "committed clairvoyant protocol bytes",
        )
        result[name] = sha(raw)
    return result


def key(case: dict[str, Any]) -> str:
    prefix = f"grid-{case['grid_minutes']}-{case['cost']}-reference-"
    return prefix + str(case["reference_notional_fraction"])


def calculate(
    rows: list[dict[str, Any]], admission: dict[str, Any], output: Evidence
) -> dict[str, Any]:
    collected: dict[str, list[dict[str, Any]]] = {}
    completed = 0
    for day in days(rows):
        require(
            day.source_hash() == admission["daily_source_hashes"][completed]["source_hash"],
            "actual financial day matches complete pre-label source admission",
        )
        date = str(day.start_ms)
        output.json(
            "attempted-day.json" if not completed else f"attempted-day-{date}.json",
            {
                "label": LABEL,
                "day_start_ms": day.start_ms,
                "case_count": 24,
                "source_hash": day.source_hash(),
            },
        )
        try:
            result = model.score_day(day)
            require(len(result["cases"]) == 24, "all24fixed grid/cost/notional cases")
            output.write(
                f"day-{date}.json.gz", gzip.compress(encoded(result), compresslevel=1, mtime=0)
            )
            for case in result["cases"]:
                collected.setdefault(key(case), []).append(case)
        except (HarnessError, ArithmeticError, ValueError, MemoryError) as error:
            output.json(
                f"failed-day-{date}.json",
                {
                    "label": LABEL,
                    "status": "FAILED_ALL24CASES_UNCERTIFIED",
                    "day_start_ms": day.start_ms,
                    "error": str(error)[:1000],
                    "complete_days": completed,
                    "unprocessed_calendar_days": (END - START) // DAY - completed - 1,
                    "no_flat_or_zero_substitution": True,
                },
            )
            raise
        completed += 1
    require(
        completed == (END - START) // DAY
        and len(collected) == 24
        and all(len(r) == completed for r in collected.values()),
        "complete all-calendar24case diagnostic",
    )
    return {
        "label": LABEL,
        "study_id": STUDY_ID,
        "calendar_days": completed,
        "cases": {name: model.statistics(values) for name, values in collected.items()},
        "case_count": 24,
        "selector": None,
        "new_alpha_configurations": 0,
        "programme_unique_configurations": 36,
        "funding_modeled": False,
        "current_paper_compliant": False,
        "native_HL_profitability_proven": False,
        "universal_upper_bound": False,
        "actual_total_account_net_return": False,
        "fresh_oos": False,
        "promotion_decision": "FORBIDDEN",
        "source_index_sha256": INDEX_SHA256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-amendment-sha256", required=True)
    parser.add_argument("--qualify-only", action="store_true")
    args = parser.parse_args(argv)
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    code = identity(args.source_commit)
    require(
        code[str(PLAN.relative_to(ROOT))] == args.expected_plan_sha256
        and code[str(AMENDMENT.relative_to(ROOT))] == args.expected_amendment_sha256,
        "registered protocol/amendment hashes",
    )
    with PrivateOutput(args.output) as private:
        output = Evidence(private)
        output.json(
            "claim.json",
            {
                "label": LABEL,
                "study_id": STUDY_ID,
                "source_commit": args.source_commit,
                "code_identities": code,
                "source_index_sha256": INDEX_SHA256,
                "qualify_only": args.qualify_only,
                "trading_mode": "PAPER_OFFLINE_RESEARCH",
                "quote_currency": "USDT",
                "reference_equity": "1",
                "source_environment": "DEV_VM",
                "image_digest": None,
            },
        )
        try:
            rows = load_index(args.index)
            admission = qualify(rows)
            output.json("source-qualification.json", admission)
            result = None if args.qualify_only else calculate(rows, admission, output)
            require(identity(args.source_commit) == code, "source drift during diagnostic")
            verify_sources(args.index, rows)
            if result is not None:
                result.update(
                    source_commit=args.source_commit,
                    plan_sha256=args.expected_plan_sha256,
                    amendment_sha256=args.expected_amendment_sha256,
                    minute_clock_sha256=admission["minute_clock_sha256"],
                    native_close_chain_sha256=admission["native_close_chain_sha256"],
                )
                output.json("result.json", result)
            output.json(
                "completion.json",
                {
                    "label": LABEL,
                    "status": "SOURCE_ONLY_COMPLETE" if args.qualify_only else "COMPLETE",
                    "calendar_days": admission["calendar_days"],
                    "financial_labels_computed": not args.qualify_only,
                    "case_count": 0 if args.qualify_only else 24,
                },
            )
            return 0
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
                    "label": LABEL,
                    "status": "INCOMPLETE_NOT_RESUMED",
                    "type": type(error).__name__,
                    "error": str(error)[:1000],
                    "financial_study_certified": False,
                },
            )
            raise


if __name__ == "__main__":
    sys.exit(main())
