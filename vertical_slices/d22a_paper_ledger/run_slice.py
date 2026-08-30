"""CLI for the bounded D22-A durable-ledger recovery proof."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vertical_slices.d22a_paper_ledger.slice import run_recoverable_paper, verify_ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run")
    run.add_argument("--run-id", required=True)
    run.add_argument("--ledger", type=Path, required=True)
    run.add_argument(
        "--crash-process-after-entry-fill",
        action="store_true",
        help="test-only: exit the process immediately after the committed entry fill",
    )
    verify = subcommands.add_parser("verify")
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--ledger", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "run":
        result = run_recoverable_paper(
            run_id=arguments.run_id,
            ledger_path=arguments.ledger,
            crash_process_after_entry_fill=arguments.crash_process_after_entry_fill,
        )
    else:
        result = verify_ledger(run_id=arguments.run_id, ledger_path=arguments.ledger)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
