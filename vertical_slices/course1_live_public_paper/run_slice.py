"""CLI for the bounded COURSE-1 live-public PAPER soak."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vertical_slices.course1_live_public_paper.slice import (
    DEFAULT_SOAK_SECONDS,
    run_soak,
    verify_soak_run,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    subcommands = value.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run")
    run.add_argument("--run-id", required=True)
    run.add_argument("--artifact-dir", type=Path, required=True)
    run.add_argument("--seconds", type=int, default=DEFAULT_SOAK_SECONDS)

    verify = subcommands.add_parser("verify")
    verify.add_argument("--artifact-dir", type=Path, required=True)
    return value


def main() -> None:
    args = parser().parse_args()
    if args.command == "run":
        result = run_soak(
            run_id=args.run_id,
            artifact_dir=args.artifact_dir,
            seconds=args.seconds,
        )
    else:
        result = verify_soak_run(args.artifact_dir)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
