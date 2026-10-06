"""CLI for the PAPER research harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from research.harness.errors import HarnessError
from research.harness.run import execute, lock_spec
from research.harness.spec import load_document, spec_sha256


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.harness",
        description="PAPER-only hypothesis harness. Does not place orders.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    hash_parser = sub.add_parser("hash", help="Print the canonical sha256 without reading data.")
    hash_parser.add_argument("spec")
    lock_parser = sub.add_parser("lock", help="Validate and write the spec lock file.")
    lock_parser.add_argument("spec")
    run_parser = sub.add_parser("run", help="Run a hash-locked spec.")
    run_parser.add_argument("spec")
    run_parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    command = args.command
    if command == "hash":
        return _hash(Path(args.spec))
    if command == "lock":
        return _lock(Path(args.spec))
    if command == "run":
        return _run(Path(args.spec), Path(args.output_dir))
    print(f"Unknown command {command}", file=sys.stderr)
    return 2


def _hash(spec_path: Path) -> int:
    try:
        document = load_document(spec_path)
        digest = spec_sha256(document)
    except HarnessError as error:
        print(f"{error.failure_kind}: {error}", file=sys.stderr)
        return 2
    print(digest)
    return 0


def _lock(spec_path: Path) -> int:
    try:
        destination, digest = lock_spec(spec_path)
    except HarnessError as error:
        print(f"{error.failure_kind}: {error}", file=sys.stderr)
        return 2
    print(f"spec_sha256={digest}")
    print(f"lock={destination}")
    return 0


def _run(spec_path: Path, output_dir: Path) -> int:
    outcome = execute(spec_path, output_dir)
    document = outcome.document
    print(f"status={document.get('status')}")
    print(f"label={document.get('label')}")
    print(f"promotion_decision={document.get('promotion_decision')}")
    print(f"failure_kind={document.get('failure_kind')}")
    print(f"spec_sha256={document.get('spec_sha256')}")
    print(f"output_dir={output_dir}")
    return outcome.exit_code
