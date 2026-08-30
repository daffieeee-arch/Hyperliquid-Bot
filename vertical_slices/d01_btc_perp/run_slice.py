"""CLI for the bounded D01 local vertical slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vertical_slices.d01_btc_perp.slice import probe_completed_run, run_slice


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    subcommands = value.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run")
    run.add_argument("--run-id", required=True)
    run.add_argument("--artifact-dir", type=Path, required=True)

    probe = subcommands.add_parser("restart-probe")
    probe.add_argument("--artifact-dir", type=Path, required=True)
    return value


def main() -> None:
    args = parser().parse_args()
    if args.command == "run":
        result = run_slice(run_id=args.run_id, artifact_dir=args.artifact_dir)
    else:
        result = probe_completed_run(args.artifact_dir)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
