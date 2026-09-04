"""Export a git-safe DATA-1A sample from published Parquet parts.

This helper never prints or copies raw WebSocket payloads. It reads already
published ``part-*.parquet`` files so a live writer can keep the DuckDB catalog
exclusive. Existing run directories remain create-only and are never resumed.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

import duckdb

from .reconstructable_paths import (
    DATA1A_PARQUET_GLOB,
    DATA1A_PATH_CONTRACT_ID,
    data1a_run_paths,
)

GIT_SAFE_SAMPLE_SCHEMA: Final = "data-1a-git-safe-sample-v1"
DEFAULT_SAMPLE_ROWS: Final = 8
MAX_SAMPLE_ROWS: Final = 32
_METADATA_COLUMNS: Final = (
    "schema_version",
    "venue",
    "product",
    "channel",
    "session_id",
    "message_ordinal",
    "received_utc_ns",
    "received_monotonic_ns",
    "direction",
    "frame_type",
    "payload_encoding",
    "payload_bytes_len",
    "payload_sha256",
)


def _require_positive_row_limit(max_rows: object) -> int:
    if type(max_rows) is not int or max_rows <= 0 or max_rows > MAX_SAMPLE_ROWS:
        raise ValueError(f"max_rows must be an integer between 1 and {MAX_SAMPLE_ROWS}.")
    return max_rows


def _write_create_only_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n")


def _read_object(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if type(loaded) is not dict:
        raise TypeError(f"{path.name} must be a JSON object.")
    return cast(dict[str, object], loaded)


def published_parquet_parts(raw_dir: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in raw_dir.glob(DATA1A_PARQUET_GLOB) if path.is_file()))


def _parquet_source_sql(parts: Sequence[Path]) -> str:
    quoted = ", ".join("'" + str(path).replace("'", "''") + "'" for path in parts)
    return f"read_parquet([{quoted}])"


def summarize_published_parts(raw_dir: Path) -> dict[str, object]:
    """Count published parts without opening payload bytes in Python."""

    parts = published_parquet_parts(raw_dir)
    return {
        "parquet_files": len(parts),
        "parquet_bytes": sum(path.stat().st_size for path in parts),
        "part_names": [path.name for path in parts],
        "part_bytes": [path.stat().st_size for path in parts],
    }


def _query_metadata_rows(parts: Sequence[Path], *, max_rows: int) -> list[dict[str, object]]:
    if not parts:
        return []
    connection = duckdb.connect(database=":memory:")
    try:
        rows = connection.execute(
            f"""
            SELECT
                schema_version,
                venue,
                product,
                channel,
                session_id,
                message_ordinal,
                received_utc_ns,
                received_monotonic_ns,
                direction,
                frame_type,
                payload_encoding,
                octet_length(payload_bytes) AS payload_bytes_len,
                payload_sha256
            FROM {_parquet_source_sql(parts)}
            ORDER BY message_ordinal
            LIMIT {max_rows}
            """
        ).fetchall()
    finally:
        connection.close()
    samples: list[dict[str, object]] = []
    for row in rows:
        samples.append(dict(zip(_METADATA_COLUMNS, row, strict=True)))
    return samples


def _query_channel_counts(parts: Sequence[Path]) -> list[dict[str, object]]:
    if not parts:
        return []
    connection = duckdb.connect(database=":memory:")
    try:
        rows = connection.execute(
            f"""
            SELECT channel, direction, count(*) AS row_count
            FROM {_parquet_source_sql(parts)}
            GROUP BY ALL
            ORDER BY ALL
            """
        ).fetchall()
    finally:
        connection.close()
    return [
        {"channel": channel, "direction": direction, "row_count": row_count}
        for channel, direction, row_count in rows
    ]


def export_git_safe_sample(
    *,
    artifact_root: Path,
    run_id: str,
    output_dir: Path,
    max_rows: int = DEFAULT_SAMPLE_ROWS,
) -> dict[str, object]:
    """Copy claim/health and metadata-only rows. Refuses raw payload copies."""

    row_limit = _require_positive_row_limit(max_rows)
    if output_dir.exists():
        raise FileExistsError(f"Git-safe sample refuses to reuse existing directory: {output_dir}")
    paths = data1a_run_paths(artifact_root, run_id)
    if not paths.capture_claim_path.is_file():
        raise FileNotFoundError(f"DATA-1A capture claim is missing: {paths.capture_claim_path}")
    claim = _read_object(paths.capture_claim_path)
    health: dict[str, object] | None = None
    if paths.capture_health_path.is_file():
        health = _read_object(paths.capture_health_path)
    parts = published_parquet_parts(paths.raw_dir)
    part_summary = summarize_published_parts(paths.raw_dir)
    sample_rows = _query_metadata_rows(parts, max_rows=row_limit)
    channel_counts = _query_channel_counts(parts)
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_create_only_json(output_dir / "capture-claim.json", claim)
    if health is not None:
        _write_create_only_json(output_dir / "capture-health.json", health)
    _write_create_only_json(output_dir / "sample-rows.json", sample_rows)
    summary = {
        "schema": GIT_SAFE_SAMPLE_SCHEMA,
        "path_contract": DATA1A_PATH_CONTRACT_ID,
        "run_id": paths.run_id,
        "artifact_root": str(paths.artifact_root),
        "run_dir": str(paths.run_dir),
        "retained": claim.get("retained") is True,
        "duration_seconds": claim.get("duration_seconds"),
        "twenty_four_seven": False,
        "credentialless": True,
        "signing": False,
        "payloads_included": False,
        "sample_row_count": len(sample_rows),
        "channel_counts": channel_counts,
        **part_summary,
        "health_present": health is not None,
        "resume_policy": "never resume or overwrite an existing DATA-1A run directory",
        "limitations": [
            "Sample rows omit payload_bytes; only SHA-256 and length are kept.",
            "Published parts remain outside git on the selected runtime store.",
            "This is not 24/7 service evidence or a trading edge.",
        ],
    }
    _write_create_only_json(output_dir / "run-summary.json", summary)
    return summary


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export a git-safe DATA-1A sample from published Parquet parts. "
            "Raw payloads are never copied or printed."
        )
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-rows", type=int, default=DEFAULT_SAMPLE_ROWS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    summary = export_git_safe_sample(
        artifact_root=cast(Path, args.artifact_root),
        run_id=cast(str, args.run_id),
        output_dir=cast(Path, args.output_dir),
        max_rows=cast(int, args.max_rows),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
