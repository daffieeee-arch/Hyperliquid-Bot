"""CLI for the isolated D41 NautilusTrader fit gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fit_gates.d41_nautilus.fit_gate import (
    run_paper,
    run_two_replays,
    verify_gate,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    subcommands = value.add_subparsers(dest="command", required=True)

    paper = subcommands.add_parser("paper")
    paper.add_argument("--seconds", type=int, default=30)
    paper.add_argument("--dataset", type=Path, required=True)
    paper.add_argument("--artifact-dir", type=Path, required=True)

    replay = subcommands.add_parser("replay-twice")
    replay.add_argument("--dataset", type=Path, required=True)
    replay.add_argument("--artifact-dir", type=Path, required=True)

    verify = subcommands.add_parser("verify")
    verify.add_argument("--dataset", type=Path, required=True)
    verify.add_argument("--artifact-dir", type=Path, required=True)
    return value


def main() -> None:
    args = parser().parse_args()
    root = Path(__file__).resolve().parent
    if args.command == "paper":
        result = run_paper(
            seconds=args.seconds,
            dataset_path=args.dataset,
            artifact_dir=args.artifact_dir,
        )
    elif args.command == "replay-twice":
        result = run_two_replays(args.dataset, artifact_dir=args.artifact_dir)
    else:
        result = verify_gate(
            dataset_path=args.dataset,
            artifact_dir=args.artifact_dir,
            source_path=root / "fit_gate.py",
        )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
