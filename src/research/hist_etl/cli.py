"""CLI for ``python -m research.hist_etl``."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from research.hist_etl.errors import HistEtlError
from research.hist_etl.manifest import default_manifest_path
from research.hist_etl.pipeline import parse_today, run_catalog, run_plan, run_sync, run_verify


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(None if argv is None else list(argv))
    try:
        return _dispatch(args, os.environ if env is None else env)
    except HistEtlError as exc:
        print(f"error\t{exc}", file=sys.stderr)
        return exc.exit_code


def _dispatch(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    root_text = args.root if isinstance(args.root, str) else env.get("HIST_ARCHIVES_ROOT")
    if not isinstance(root_text, str) or not root_text:
        raise HistEtlError("set --root or HIST_ARCHIVES_ROOT")
    manifest_text = args.manifest if isinstance(args.manifest, str) else None
    manifest_path = Path(manifest_text) if manifest_text is not None else default_manifest_path()
    dataset_ids = tuple(args.dataset) if isinstance(args.dataset, list) else None
    today = parse_today(args.today if isinstance(args.today, str) else None)
    root = Path(root_text)
    command = args.command
    if command == "plan":
        return run_plan(
            root=root,
            manifest_path=manifest_path,
            today=today,
            dataset_ids=dataset_ids,
            env=env,
        )
    if command == "sync":
        return run_sync(
            root=root,
            manifest_path=manifest_path,
            today=today,
            dataset_ids=dataset_ids,
            env=env,
            dry_run=bool(args.dry_run),
            rebuild=bool(args.rebuild),
            replace_legacy_views=bool(args.replace_legacy_views),
        )
    if command == "verify":
        return run_verify(
            root=root,
            manifest_path=manifest_path,
            today=today,
            dataset_ids=dataset_ids,
            env=env,
        )
    if command == "catalog":
        return run_catalog(
            root=root,
            manifest_path=manifest_path,
            dataset_ids=dataset_ids,
            env=env,
            replace_legacy_views=bool(args.replace_legacy_views),
        )
    raise HistEtlError(f"unknown command {command}")


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="Archive root. Defaults to HIST_ARCHIVES_ROOT.")
    common.add_argument(
        "--manifest", help="Dataset manifest. Defaults to config/hist_etl/datasets.toml."
    )
    common.add_argument("--today", help="UTC date YYYY-MM-DD used for publication lag.")
    common.add_argument(
        "--dataset",
        action="append",
        help="Limit the run to this dataset id. Repeat to select several. "
        "Explicit ids include disabled manifest entries.",
    )
    parser = argparse.ArgumentParser(
        prog="research.hist_etl",
        description=(
            "Download, verify, and catalog offline Binance Vision and Kraken OHLCVT history."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", parents=[common], help="Print the file list and size estimate.")
    sync = sub.add_parser(
        "sync", parents=[common], help="Download missing files and refresh Parquet."
    )
    sync.add_argument("--dry-run", action="store_true", help="Same as plan; write nothing.")
    sync.add_argument(
        "--rebuild",
        action="store_true",
        help="Replace a month file whose sidecar is missing or does not match.",
    )
    sync.add_argument(
        "--replace-legacy-views",
        action="store_true",
        help="Back up catalog.sql, print a diff, and replace legacy hist_* views.",
    )
    sub.add_parser("verify", parents=[common], help="Re-check checksums, schemas, and gaps.")
    catalog = sub.add_parser("catalog", parents=[common], help="Regenerate pipeline DuckDB views.")
    catalog.add_argument(
        "--replace-legacy-views",
        action="store_true",
        help="Back up catalog.sql, print a diff, and replace legacy hist_* views.",
    )
    return parser
