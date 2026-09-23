"""Read-only Phase A inventory and research smokes.

Lists run directories under ``data-capture``, checks hist-archives DuckDB
catalog globs, and summarizes Binance transport/starvation gaps, Bitvavo Pro
keepalive ping timeouts, and cross-venue tape holes.

These helpers never create, rename, or delete capture files and never talk
to tmux. Parquet parts are counted with directory listings; payloads are not
opened.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never, cast

import duckdb

from hyperliquid_bot.reconstructable_paths import (
    DATA1A_CLAIM_NAME,
    DATA1A_HEALTH_NAME,
    DATA1A_PARQUET_GLOB,
    DATA1A_RAW_DIR_NAME,
    DATA1A_RELATIVE_PREFIX,
    DATA1B_RELATIVE_PREFIX,
    DATA1D_RELATIVE_PREFIX,
    DATA1E_RELATIVE_PREFIX,
    DATA1F_RELATIVE_PREFIX,
    require_run_id,
)

_CLAIM_NAME = DATA1A_CLAIM_NAME
_HEALTH_NAME = DATA1A_HEALTH_NAME
_RAW_DIR_NAME = DATA1A_RAW_DIR_NAME
_PART_GLOB = DATA1A_PARQUET_GLOB
_LOG_LINE_LIMIT = 4000
_LOG_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+")
_STREAM_TOKEN = re.compile(r"stream=([A-Za-z0-9@._\-]+)")
_VIEW_PATTERN = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+VIEW\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s+"
    r"SELECT\s+\*\s+FROM\s+read_parquet\(\s*'([^']+)'",
    re.IGNORECASE | re.DOTALL,
)
_LIVE_VIEW_LANES: dict[str, str] = {
    "live_hl_parts": "HL",
    "live_kr_parts": "KR",
    "live_bv_std_parts": "BV-Std",
    "live_bv_parts": "BV-Pro",
    "live_bn_parts": "BN",
}
_SUGGESTED_VIEWS: dict[str, str] = {
    "HL": "live_hl_phase_a_parts",
    "KR": "live_kr_phase_a_parts",
    "BV-Std": "live_bv_std_phase_a_parts",
    "BV-Pro": "live_bv_phase_a_parts",
    "BN": "live_bn_phase_a_parts",
}
_CONTINUITY_LABELS: tuple[str, ...] = ("HL", "BN", "KR", "BV-Pro")


@dataclass(frozen=True, slots=True)
class ResearchLane:
    """One reconstructable retain lane the research helpers know how to list."""

    label: str
    relative_prefix: tuple[str, ...]


RESEARCH_LANES: tuple[ResearchLane, ...] = (
    ResearchLane("HL", DATA1A_RELATIVE_PREFIX),
    ResearchLane("KR", DATA1B_RELATIVE_PREFIX),
    ResearchLane("BV-Std", DATA1D_RELATIVE_PREFIX),
    ResearchLane("BV-Pro", DATA1E_RELATIVE_PREFIX),
    ResearchLane("BN", DATA1F_RELATIVE_PREFIX),
)


@dataclass(frozen=True, slots=True)
class RunInventory:
    """Point-in-time listing of one run directory. Parquet bytes are not read."""

    lane: str
    run_id: str
    valid_run_id: bool
    phase_a: bool
    run_dir: Path
    claim_path: Path
    health_path: Path
    claim_present: bool
    health_present: bool
    claim_state: str | None
    health_status: str | None
    retained: bool | None
    duration_seconds: float | None
    elapsed_seconds: float | None
    health_gaps: int | None
    health_reconnects: int | None
    health_parquet_files: int | None
    health_events: int | None
    disk_parquet_parts: int
    first_part_mtime: float | None
    last_part_mtime: float | None
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SegmentHole:
    """Wall-clock hole between two parquet windows on one lane."""

    lane: str
    previous_run_id: str
    next_run_id: str
    gap_seconds: float


@dataclass(frozen=True, slots=True)
class BnLogCounts:
    """Capture-log counters. Raw log lines are not retained."""

    disconnects: int
    reconnects: int
    starvation_gaps: int
    liveness_errors: int
    operator_alerts: int
    starved_streams: tuple[str, ...]
    liveness_error_streams: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BnSegment:
    """One Binance run plus the transport/starvation notes derived from its log."""

    run: RunInventory
    log_counts: BnLogCounts
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BnGapReport:
    """Binance phase-a segments, log counters, and holes before the next run."""

    segments: tuple[BnSegment, ...]
    holes: tuple[SegmentHole, ...]


@dataclass(frozen=True, slots=True)
class BvPingRun:
    """Bitvavo Pro keepalive ping-timeout lines in one capture log."""

    run_id: str
    health_status: str | None
    disk_parquet_parts: int
    ping_timeout_lines: int
    first_log_timestamp: str | None
    last_log_timestamp: str | None


@dataclass(frozen=True, slots=True)
class BvPingReport:
    """Ping-timeout visibility for DATA-1E. Absence on a phase-a run is listed."""

    runs_scanned: int
    hits: tuple[BvPingRun, ...]
    phase_a_without_ping_timeout: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OverlapWindow:
    """Interval where every requested lane has parquet coverage."""

    start_mtime: float
    end_mtime: float
    seconds: float


@dataclass(frozen=True, slots=True)
class SharedRunCoverage:
    """Which joint lanes contain the same run_id directory name."""

    run_id: str
    present_lanes: tuple[str, ...]
    missing_lanes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContinuityReport:
    """Cross-venue holes, overlap, and shared run_id coverage."""

    lanes: tuple[str, ...]
    holes: tuple[SegmentHole, ...]
    overlap_windows: tuple[OverlapWindow, ...]
    shared_runs: tuple[SharedRunCoverage, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogViewCheck:
    """Filesystem check of one ``read_parquet`` view in catalog.sql."""

    name: str
    kind: str
    parquet_pattern: str
    parquet_files: int
    pinned_run_id: str | None
    newest_phase_a_run_id: str | None
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DbProbe:
    """Read-only DuckDB catalog probe. Live views are not scanned."""

    name: str
    present: bool
    row_count: int | None
    note: str


@dataclass(frozen=True, slots=True)
class DuckdbSmokeReport:
    """Hist + live catalog smoke. Suggestions are text only; nothing is applied."""

    catalog_path: Path
    database_path: Path
    catalog_present: bool
    database_present: bool
    views: tuple[CatalogViewCheck, ...]
    probes: tuple[DbProbe, ...]
    suggested_sql: tuple[str, ...]
    fatal_notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InventoryReport:
    """Run listing plus the filesystem half of the DuckDB catalog smoke."""

    artifact_root: Path
    hist_root: Path
    runs: tuple[RunInventory, ...]
    duckdb: DuckdbSmokeReport


def default_artifact_root() -> Path:
    """Return ``ARTIFACT_ROOT`` or the VPS data-capture sibling path."""

    configured = os.environ.get("ARTIFACT_ROOT")
    if configured:
        return Path(configured)
    return Path.home() / "Hyperliquid Project" / "data-capture"


def default_hist_root() -> Path:
    """Return ``HIST_ARCHIVES_ROOT`` or the VPS hist-archives sibling path."""

    configured = os.environ.get("HIST_ARCHIVES_ROOT")
    if configured:
        return Path(configured)
    return Path.home() / "Hyperliquid Project" / "hist-archives"


def inventory_runs(artifact_root: Path) -> tuple[RunInventory, ...]:
    """List every run directory on the Phase A lanes. Missing lanes are skipped."""

    runs: list[RunInventory] = []
    for lane in RESEARCH_LANES:
        parent = artifact_root.joinpath(*lane.relative_prefix)
        if not parent.is_dir():
            continue
        for run_dir in sorted(parent.iterdir(), key=lambda path: path.name):
            if not run_dir.is_dir() or run_dir.is_symlink():
                continue
            runs.append(_inventory_run(lane, run_dir))
    return tuple(runs)


def bn_gap_report(
    runs: Sequence[RunInventory],
    *,
    hole_seconds: float,
    phase_a_only: bool,
) -> BnGapReport:
    """Describe Binance stops that were followed by a new run_id, plus tape holes."""

    selected = _select_lane(runs, "BN", phase_a_only=phase_a_only)
    ordered = _order_runs(selected)
    segments: list[BnSegment] = []
    for index, run in enumerate(ordered):
        counts = _bn_log_counts(run.run_dir)
        notes: list[str] = []
        if index + 1 < len(ordered):
            follow = ordered[index + 1]
            failed = run.health_status == "FAILED" or counts.liveness_errors > 0
            if failed:
                notes.append(_continue_note(run.run_id, follow.run_id, counts))
        segments.append(BnSegment(run=run, log_counts=counts, notes=tuple(notes)))
    return BnGapReport(
        segments=tuple(segments),
        holes=segment_holes(ordered, hole_seconds=hole_seconds),
    )


def bv_ping_report(runs: Sequence[RunInventory]) -> BvPingReport:
    """Count Bitvavo Pro keepalive ping-timeout log lines without reading Parquet."""

    selected = _select_lane(runs, "BV-Pro", phase_a_only=False)
    hits: list[BvPingRun] = []
    quiet: list[str] = []
    for run in selected:
        ping = _ping_timeouts(run.run_dir)
        if ping[0] == 0:
            if run.phase_a:
                quiet.append(run.run_id)
            continue
        hits.append(
            BvPingRun(
                run_id=run.run_id,
                health_status=run.health_status,
                disk_parquet_parts=run.disk_parquet_parts,
                ping_timeout_lines=ping[0],
                first_log_timestamp=ping[1],
                last_log_timestamp=ping[2],
            )
        )
    return BvPingReport(
        runs_scanned=len(selected),
        hits=tuple(hits),
        phase_a_without_ping_timeout=tuple(quiet),
    )


def continuity_report(
    runs: Sequence[RunInventory],
    *,
    lanes: Sequence[ResearchLane],
    hole_seconds: float,
    phase_a_only: bool,
) -> ContinuityReport:
    """Compare parquet windows across lanes. Shared run_ids are exact directory names."""

    labels = tuple(lane.label for lane in lanes)
    selected_by_lane: dict[str, tuple[RunInventory, ...]] = {}
    holes: list[SegmentHole] = []
    notes: list[str] = []
    merged: list[list[tuple[float, float]]] = []
    for lane in lanes:
        chosen = _order_runs(_select_lane(runs, lane.label, phase_a_only=phase_a_only))
        selected_by_lane[lane.label] = chosen
        holes.extend(segment_holes(chosen, hole_seconds=hole_seconds))
        intervals = _coverage_intervals(chosen)
        if not intervals:
            notes.append(f"{lane.label} has no parquet coverage in this selection")
        merged.append(_merge_intervals(intervals))
    overlap = _intersect_all(merged) if merged else []
    return ContinuityReport(
        lanes=labels,
        holes=tuple(holes),
        overlap_windows=tuple(
            OverlapWindow(start_mtime=start, end_mtime=end, seconds=end - start)
            for start, end in overlap
        ),
        shared_runs=_shared_runs(selected_by_lane, labels),
        notes=tuple(notes),
    )


def segment_holes(runs: Sequence[RunInventory], *, hole_seconds: float) -> tuple[SegmentHole, ...]:
    """Return gaps larger than ``hole_seconds`` between consecutive parquet windows."""

    if hole_seconds < 0 or not math.isfinite(hole_seconds):
        raise ValueError("hole_seconds must be a finite non-negative number.")
    ordered = _order_runs(runs)
    holes: list[SegmentHole] = []
    previous: RunInventory | None = None
    for run in ordered:
        if run.first_part_mtime is None or run.last_part_mtime is None:
            continue
        if previous is not None and previous.last_part_mtime is not None:
            gap = run.first_part_mtime - previous.last_part_mtime
            if gap > hole_seconds:
                holes.append(
                    SegmentHole(
                        lane=run.lane,
                        previous_run_id=previous.run_id,
                        next_run_id=run.run_id,
                        gap_seconds=gap,
                    )
                )
        previous = run
    return tuple(holes)


def duckdb_smoke(
    *,
    artifact_root: Path,
    hist_root: Path,
    runs: Sequence[RunInventory],
    probe_db: bool,
    strict_pins: bool,
) -> DuckdbSmokeReport:
    """Check catalog.sql globs. Optional DuckDB probe is read-only and skips live views."""

    catalog_path = hist_root / "catalog.sql"
    database_path = hist_root / "research.duckdb"
    fatal: list[str] = []
    if not hist_root.is_dir():
        fatal.append("hist-archives root is not a directory")
    if not catalog_path.is_file():
        fatal.append("catalog.sql is missing")
        return DuckdbSmokeReport(
            catalog_path=catalog_path,
            database_path=database_path,
            catalog_present=False,
            database_present=database_path.is_file(),
            views=(),
            probes=(),
            suggested_sql=(),
            fatal_notes=tuple(fatal),
        )
    sql = (
        catalog_path.read_text(encoding="utf-8")
        .replace("__HIST__", str(hist_root))
        .replace("__DC__", str(artifact_root))
    )
    views = tuple(_catalog_views(sql, runs, strict_pins=strict_pins, fatal=fatal))
    probes = _probe_database(database_path, views) if probe_db else ()
    if probe_db and not database_path.is_file():
        fatal.append("research.duckdb is missing")
    for probe in probes:
        if probe.note.startswith("duckdb_error"):
            fatal.append(f"{probe.name}: {probe.note}")
    return DuckdbSmokeReport(
        catalog_path=catalog_path,
        database_path=database_path,
        catalog_present=True,
        database_present=database_path.is_file(),
        views=views,
        probes=probes,
        suggested_sql=_suggested_sql(artifact_root, runs),
        fatal_notes=tuple(fatal),
    )


def to_jsonable(value: object) -> object:
    """Convert report dataclasses to JSON-ready objects."""

    if value is None or type(value) in {str, int, float, bool}:
        return value
    if isinstance(value, Path):
        return str(value)
    if type(value) in {tuple, list}:
        return [to_jsonable(item) for item in cast(Sequence[object], value)]
    if is_dataclass(value) and not isinstance(value, type):
        payload: dict[str, object] = {}
        for item in fields(value):
            payload[item.name] = to_jsonable(getattr(value, item.name))
        return payload
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry. Exit 1 on a failed smoke, 2 on bad arguments."""

    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    command = args.command
    if type(command) is not str:
        return 2
    hole_seconds = args.hole_seconds
    if type(hole_seconds) not in {int, float} or not math.isfinite(float(hole_seconds)):
        print("hole-seconds must be a finite number", file=sys.stderr)
        return 2
    if float(hole_seconds) < 0:
        print("hole-seconds must be >= 0", file=sys.stderr)
        return 2
    artifact_root = _path_arg(args.artifact_root, default_artifact_root())
    hist_root = _path_arg(args.hist_root, default_hist_root())
    as_json = bool(args.json)
    all_runs = bool(args.all_runs)
    probe_db = bool(getattr(args, "probe_db", False))
    strict_pins = bool(getattr(args, "strict_pins", False))
    lanes_raw = getattr(args, "lanes", ",".join(_CONTINUITY_LABELS))
    lanes_text = lanes_raw if type(lanes_raw) is str else ",".join(_CONTINUITY_LABELS)
    if not artifact_root.is_dir():
        print(f"artifact root is not a directory: {artifact_root}", file=sys.stderr)
        return 1
    runs = inventory_runs(artifact_root)
    return _dispatch(
        command,
        artifact_root=artifact_root,
        hist_root=hist_root,
        runs=runs,
        hole_seconds=float(hole_seconds),
        as_json=as_json,
        all_runs=all_runs,
        probe_db=probe_db,
        strict_pins=strict_pins,
        lanes_text=lanes_text,
    )


def _dispatch(
    command: str,
    *,
    artifact_root: Path,
    hist_root: Path,
    runs: tuple[RunInventory, ...],
    hole_seconds: float,
    as_json: bool,
    all_runs: bool,
    probe_db: bool,
    strict_pins: bool,
    lanes_text: str,
) -> int:
    phase_a_only = not all_runs
    match command:
        case "inventory":
            report = InventoryReport(
                artifact_root=artifact_root,
                hist_root=hist_root,
                runs=runs,
                duckdb=duckdb_smoke(
                    artifact_root=artifact_root,
                    hist_root=hist_root,
                    runs=runs,
                    probe_db=False,
                    strict_pins=False,
                ),
            )
            _emit_json_or_text(report, as_json=as_json, text=_render_inventory(report))
            return 0
        case "duckdb-smoke":
            smoke = duckdb_smoke(
                artifact_root=artifact_root,
                hist_root=hist_root,
                runs=runs,
                probe_db=probe_db,
                strict_pins=strict_pins,
            )
            _emit_json_or_text(smoke, as_json=as_json, text=_render_smoke(smoke))
            return 1 if smoke.fatal_notes else 0
        case "bn-gaps":
            gaps = bn_gap_report(runs, hole_seconds=hole_seconds, phase_a_only=phase_a_only)
            _emit_json_or_text(gaps, as_json=as_json, text=_render_bn(gaps))
            return 0
        case "bv-ping":
            pings = bv_ping_report(runs)
            _emit_json_or_text(pings, as_json=as_json, text=_render_bv(pings))
            return 0
        case "continuity":
            lanes = _parse_lanes(lanes_text)
            if lanes is None:
                print(f"unknown lane in {lanes_text}", file=sys.stderr)
                return 2
            continuity = continuity_report(
                runs,
                lanes=lanes,
                hole_seconds=hole_seconds,
                phase_a_only=phase_a_only,
            )
            _emit_json_or_text(continuity, as_json=as_json, text=_render_continuity(continuity))
            return 0
        case _:
            unexpected: Never = cast(Never, command)
            raise AssertionError(unexpected)


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--artifact-root", type=Path, default=None)
    common.add_argument("--hist-root", type=Path, default=None)
    common.add_argument("--json", action="store_true")
    common.add_argument("--hole-seconds", type=float, default=120.0)
    common.add_argument(
        "--all-runs",
        action="store_true",
        help="Include smoke, gate, and dry-check directories in bn-gaps and continuity.",
    )
    parser = argparse.ArgumentParser(
        prog="phase_a_research_tools",
        description="Read-only Phase A inventory and research smokes. Does not touch captures.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "inventory",
        parents=[common],
        help="List run_ids, claim/health, part counts, catalog gaps.",
    )
    smoke = sub.add_parser(
        "duckdb-smoke",
        parents=[common],
        help="Check hist + live catalog globs. Prints SQL suggestions.",
    )
    smoke.add_argument(
        "--probe-db",
        action="store_true",
        help="Read-only row counts for small hist views. Does not scan live_* views.",
    )
    smoke.add_argument(
        "--strict-pins",
        action="store_true",
        help="Fail when a live view pins an older phase-a run_id.",
    )
    sub.add_parser(
        "bn-gaps",
        parents=[common],
        help="Binance transport/starvation stops followed by a new run_id.",
    )
    sub.add_parser(
        "bv-ping",
        parents=[common],
        help="Bitvavo Pro keepalive ping-timeout log counts.",
    )
    continuity = sub.add_parser(
        "continuity",
        parents=[common],
        help="Cross-venue parquet holes and shared run_id coverage.",
    )
    continuity.add_argument("--lanes", default=",".join(_CONTINUITY_LABELS))
    return parser


def _path_arg(value: object, default: Path) -> Path:
    if isinstance(value, Path):
        return value.expanduser()
    return default


def _parse_lanes(text: str) -> tuple[ResearchLane, ...] | None:
    labels = [part.strip() for part in text.split(",") if part.strip()]
    if not labels:
        return None
    chosen: list[ResearchLane] = []
    for label in labels:
        lane = next((item for item in RESEARCH_LANES if item.label == label), None)
        if lane is None:
            return None
        chosen.append(lane)
    return tuple(chosen)


def _inventory_run(lane: ResearchLane, run_dir: Path) -> RunInventory:
    claim_path = run_dir / _CLAIM_NAME
    health_path = run_dir / _HEALTH_NAME
    claim, claim_bad = _read_object(claim_path)
    health, health_bad = _read_object(health_path)
    part_count, first_mtime, last_mtime = _parquet_stats(run_dir / _RAW_DIR_NAME)
    health_files = _optional_int(None if health is None else health.get("parquet_files"))
    health_events = _optional_int(None if health is None else health.get("events"))
    status = _optional_str(None if health is None else health.get("status"))
    retained = _optional_bool(None if claim is None else claim.get("retained"))
    notes = _run_notes(
        run_id=run_dir.name,
        valid=_valid_run_id(run_dir.name),
        claim_present=claim_path.is_file(),
        claim_bad=claim_bad,
        health_present=health_path.is_file(),
        health_bad=health_bad,
        status=status,
        retained=retained,
        health_files=health_files,
        health_events=health_events,
        disk_parts=part_count,
    )
    return RunInventory(
        lane=lane.label,
        run_id=run_dir.name,
        valid_run_id=_valid_run_id(run_dir.name),
        phase_a="phase-a" in run_dir.name,
        run_dir=run_dir,
        claim_path=claim_path,
        health_path=health_path,
        claim_present=claim_path.is_file(),
        health_present=health_path.is_file(),
        claim_state=_optional_str(None if claim is None else claim.get("state")),
        health_status=status,
        retained=retained,
        duration_seconds=_optional_float(None if claim is None else claim.get("duration_seconds")),
        elapsed_seconds=_optional_float(None if health is None else health.get("elapsed_seconds")),
        health_gaps=_optional_int(None if health is None else health.get("gaps")),
        health_reconnects=_optional_int(None if health is None else health.get("reconnects")),
        health_parquet_files=health_files,
        health_events=health_events,
        disk_parquet_parts=part_count,
        first_part_mtime=first_mtime,
        last_part_mtime=last_mtime,
        notes=notes,
    )


def _run_notes(
    *,
    run_id: str,
    valid: bool,
    claim_present: bool,
    claim_bad: bool,
    health_present: bool,
    health_bad: bool,
    status: str | None,
    retained: bool | None,
    health_files: int | None,
    health_events: int | None,
    disk_parts: int,
) -> tuple[str, ...]:
    notes: list[str] = []
    if not valid:
        notes.append("directory name is not a valid run_id")
    if ".FAILED" in run_id:
        notes.append("directory name records a FAILED start marker")
    if not claim_present:
        notes.append("missing capture-claim.json")
    elif claim_bad:
        notes.append("capture-claim.json is not a JSON object")
    if retained is False:
        notes.append("claim retained is not true")
    if not health_present:
        notes.append("health not written yet; run may still be open")
    elif health_bad:
        notes.append("capture-health.json is not a JSON object")
    if status == "FAILED":
        notes.append("health status FAILED")
    elif status == "OPERATOR_STOP":
        notes.append("health status OPERATOR_STOP (interrupt, not a completed tape)")
    if health_files is not None and health_files != disk_parts:
        notes.append(
            f"health parquet_files={health_files} differs from disk part count={disk_parts}"
        )
    if health_events == 0 and disk_parts > 0:
        notes.append("health events=0 while disk has parquet parts")
    if "continue" in run_id:
        notes.append("continue run_id; not a resume of the previous directory")
    return tuple(notes)


def _parquet_stats(raw_dir: Path) -> tuple[int, float | None, float | None]:
    if not raw_dir.is_dir():
        return 0, None, None
    count = 0
    first: float | None = None
    last: float | None = None
    for path in raw_dir.glob(_PART_GLOB):
        if not path.is_file() or path.is_symlink():
            continue
        count += 1
        mtime = path.stat().st_mtime
        if first is None or mtime < first:
            first = mtime
        if last is None or mtime > last:
            last = mtime
    return count, first, last


def _read_object(path: Path) -> tuple[Mapping[str, object] | None, bool]:
    if not path.is_file():
        return None, False
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, True
    if type(parsed) is not dict:
        return None, True
    return {str(key): value for key, value in parsed.items()}, False


def _optional_str(value: object) -> str | None:
    if type(value) is str and value:
        return value
    return None


def _optional_bool(value: object) -> bool | None:
    if type(value) is bool:
        return value
    return None


def _optional_float(value: object) -> float | None:
    if type(value) is bool:
        return None
    if type(value) in {int, float}:
        number = float(cast(int | float, value))
        if math.isfinite(number):
            return number
    return None


def _optional_int(value: object) -> int | None:
    if type(value) is bool:
        return None
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _valid_run_id(name: str) -> bool:
    try:
        require_run_id(name)
    except (TypeError, ValueError):
        return False
    return True


def _is_campaign_name(run_id: str) -> bool:
    """Phase A campaign directories. Short smokes and gate checks stay in inventory only."""

    if "phase-a" not in run_id:
        return False
    parts = set(run_id.split("-"))
    if parts & {"smoke", "gate", "fgtest"}:
        return False
    return "dry-check" not in run_id


def _select_lane(
    runs: Sequence[RunInventory],
    label: str,
    *,
    phase_a_only: bool,
) -> tuple[RunInventory, ...]:
    return tuple(
        run
        for run in runs
        if run.lane == label and (not phase_a_only or _is_campaign_name(run.run_id))
    )


def _order_runs(runs: Sequence[RunInventory]) -> tuple[RunInventory, ...]:
    return tuple(
        sorted(
            runs,
            key=lambda run: (run.first_part_mtime is None, run.first_part_mtime or 0.0, run.run_id),
        )
    )


def _continue_note(previous_id: str, next_id: str, counts: BnLogCounts) -> str:
    if counts.liveness_errors > 0 or counts.starvation_gaps > 0:
        cause = "required-stream starvation"
    elif counts.disconnects > 0:
        cause = "transport disconnects"
    else:
        cause = "capture stop"
    return f"{cause} on {previous_id}, then new run_id {next_id} (directories are not resumed)"


def _bn_log_counts(run_dir: Path) -> BnLogCounts:
    disconnects = 0
    reconnects = 0
    starvation = 0
    liveness = 0
    alerts = 0
    starved: set[str] = set()
    errored: set[str] = set()
    for line in _log_lines(run_dir):
        kind = _bn_line_kind(line)
        stream = _stream_token(line)
        if kind == "disconnect":
            disconnects += 1
        elif kind == "reconnect":
            reconnects += 1
        elif kind == "starvation":
            starvation += 1
            if stream is not None:
                starved.add(stream)
        elif kind == "liveness_error":
            liveness += 1
            if stream is not None:
                errored.add(stream)
        elif kind == "alert":
            alerts += 1
    return BnLogCounts(
        disconnects=disconnects,
        reconnects=reconnects,
        starvation_gaps=starvation,
        liveness_errors=liveness,
        operator_alerts=alerts,
        starved_streams=tuple(sorted(starved)),
        liveness_error_streams=tuple(sorted(errored)),
    )


def _bn_line_kind(line: str) -> str | None:
    if " liveness_error " in line and "required_stream_starved" in line:
        return "liveness_error"
    if " liveness_gap " in line and "required_stream_starved" in line:
        return "starvation"
    if " disconnect " in line:
        return "disconnect"
    if " reconnect " in line:
        return "reconnect"
    if "capture_operator_alert " in line and "webhook_failed" not in line:
        return "alert"
    return None


def _stream_token(line: str) -> str | None:
    match = _STREAM_TOKEN.search(line)
    if match is None:
        return None
    return match.group(1)


def _ping_timeouts(run_dir: Path) -> tuple[int, str | None, str | None]:
    count = 0
    first: str | None = None
    last: str | None = None
    for line in _log_lines(run_dir):
        if "keepalive ping timeout" not in line:
            continue
        count += 1
        stamp = _log_timestamp(line)
        if stamp is None:
            continue
        if first is None:
            first = stamp
        last = stamp
    return count, first, last


def _log_timestamp(line: str) -> str | None:
    match = _LOG_TIMESTAMP.match(line)
    if match is None:
        return None
    return match.group(1)


def _log_lines(run_dir: Path) -> Iterator[str]:
    logs = sorted(
        path for path in run_dir.glob("capture-*.log") if path.is_file() and not path.is_symlink()
    )
    for log_path in logs:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if len(line) > _LOG_LINE_LIMIT:
                    continue
                yield line.rstrip("\n")


def _coverage_intervals(runs: Sequence[RunInventory]) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for run in runs:
        if run.first_part_mtime is None or run.last_part_mtime is None:
            continue
        start = run.first_part_mtime
        end = run.last_part_mtime
        if end < start:
            continue
        intervals.append((start, end))
    return intervals


def _merge_intervals(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def _intersect_two(
    left: Sequence[tuple[float, float]],
    right: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        start = max(left[left_index][0], right[right_index][0])
        end = min(left[left_index][1], right[right_index][1])
        if end > start:
            result.append((start, end))
        if left[left_index][1] < right[right_index][1]:
            left_index += 1
        else:
            right_index += 1
    return result


def _intersect_all(groups: Sequence[Sequence[tuple[float, float]]]) -> list[tuple[float, float]]:
    if not groups:
        return []
    current = list(groups[0])
    for group in groups[1:]:
        current = _intersect_two(current, group)
    return current


def _shared_runs(
    selected_by_lane: Mapping[str, Sequence[RunInventory]],
    labels: Sequence[str],
) -> tuple[SharedRunCoverage, ...]:
    names = sorted({run.run_id for runs in selected_by_lane.values() for run in runs})
    coverage: list[SharedRunCoverage] = []
    for name in names:
        present = tuple(
            label
            for label in labels
            if any(run.run_id == name for run in selected_by_lane.get(label, ()))
        )
        missing = tuple(label for label in labels if label not in present)
        coverage.append(
            SharedRunCoverage(run_id=name, present_lanes=present, missing_lanes=missing)
        )
    return tuple(coverage)


def _catalog_views(
    sql: str,
    runs: Sequence[RunInventory],
    *,
    strict_pins: bool,
    fatal: list[str],
) -> Iterator[CatalogViewCheck]:
    for match in _VIEW_PATTERN.finditer(sql):
        name = match.group(1)
        pattern = match.group(2)
        kind = _view_kind(name)
        parquet_files = _count_pattern(pattern)
        pinned = _pinned_run_id(pattern) if kind == "live" else None
        newest = _newest_phase_a_run(runs, _LIVE_VIEW_LANES.get(name, ""))
        notes = _view_notes(
            name=name,
            kind=kind,
            parquet_files=parquet_files,
            pinned=pinned,
            newest=newest,
        )
        if parquet_files == 0:
            fatal.append(f"{name} matches 0 parquet files")
        if strict_pins and any(note.startswith("live view pins") for note in notes):
            fatal.append(f"{name} pins a stale phase-a run_id")
        newest_id = None if newest is None else newest.run_id
        yield CatalogViewCheck(
            name=name,
            kind=kind,
            parquet_pattern=pattern,
            parquet_files=parquet_files,
            pinned_run_id=pinned,
            newest_phase_a_run_id=newest_id,
            notes=notes,
        )


def _view_kind(name: str) -> str:
    if name.startswith("hist_"):
        return "hist"
    if name.startswith("live_"):
        return "live"
    return "other"


def _view_notes(
    *,
    name: str,
    kind: str,
    parquet_files: int,
    pinned: str | None,
    newest: RunInventory | None,
) -> tuple[str, ...]:
    notes: list[str] = []
    if parquet_files == 0:
        notes.append(f"{name} parquet glob is empty or missing")
    if kind == "live" and name not in _LIVE_VIEW_LANES:
        notes.append("live view has no lane mapping")
    if kind == "live" and pinned is not None and newest is not None and pinned != newest.run_id:
        notes.append(
            f"live view pins {pinned}; newest phase-a parquet activity is {newest.run_id} "
            "(continue segments stay separate run_ids)"
        )
    return tuple(notes)


def _newest_phase_a_run(runs: Sequence[RunInventory], label: str) -> RunInventory | None:
    if not label:
        return None
    candidates = [
        run
        for run in runs
        if run.lane == label
        and _is_campaign_name(run.run_id)
        and run.disk_parquet_parts > 0
        and run.last_part_mtime is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda run: (run.last_part_mtime or 0.0, run.run_id))


def _pinned_run_id(pattern: str) -> str | None:
    parts = Path(pattern).parts
    if "raw" not in parts:
        return None
    index = parts.index("raw")
    if index == 0:
        return None
    return parts[index - 1]


def _count_pattern(pattern: str) -> int:
    if any(token in pattern for token in ("*", "?", "[")):
        star = pattern.find("*")
        if star < 0:
            star = pattern.find("?")
        if star < 0:
            star = pattern.find("[")
        slash = pattern.rfind("/", 0, star)
        base = Path(pattern[:slash] if slash >= 0 else ".")
        relative = pattern[slash + 1 :] if slash >= 0 else pattern
        if not base.is_dir():
            return 0
        return sum(1 for match in base.glob(relative) if match.is_file() and not match.is_symlink())
    return 1 if Path(pattern).is_file() else 0


def _suggested_sql(artifact_root: Path, runs: Sequence[RunInventory]) -> tuple[str, ...]:
    suggestions: list[str] = []
    for lane in RESEARCH_LANES:
        statement = _union_statement(artifact_root, lane, runs)
        if statement is not None:
            suggestions.append(statement)
    return tuple(suggestions)


def _union_statement(
    artifact_root: Path,
    lane: ResearchLane,
    runs: Sequence[RunInventory],
) -> str | None:
    view = _SUGGESTED_VIEWS.get(lane.label)
    if view is None:
        return None
    selected = [
        run
        for run in runs
        if run.lane == lane.label
        and _is_campaign_name(run.run_id)
        and run.valid_run_id
        and run.disk_parquet_parts > 0
    ]
    if len(selected) < 2:
        return None
    globs: list[str] = []
    for run in sorted(selected, key=lambda item: item.run_id):
        pattern = artifact_root.joinpath(*lane.relative_prefix, run.run_id, "raw", "*.parquet")
        rendered = pattern.as_posix()
        if "'" in rendered or "\n" in rendered:
            return None
        globs.append(rendered)
    lines = [f"CREATE OR REPLACE VIEW {view} AS", "  SELECT * FROM read_parquet(["]
    for index, glob in enumerate(globs):
        suffix = "," if index < len(globs) - 1 else ""
        lines.append(f"    '{glob}'{suffix}")
    lines.append("  ], union_by_name := true);")
    return "\n".join(lines)


def _probe_database(database_path: Path, views: Sequence[CatalogViewCheck]) -> tuple[DbProbe, ...]:
    if not database_path.is_file():
        return (DbProbe(name="research.duckdb", present=False, row_count=None, note="missing"),)
    try:
        connection = duckdb.connect(str(database_path), read_only=True)
    except duckdb.Error as error:
        return (
            DbProbe(
                name="research.duckdb",
                present=False,
                row_count=None,
                note=f"duckdb_error:{type(error).__name__}",
            ),
        )
    try:
        present = _database_relation_names(connection)
        probes: list[DbProbe] = []
        for view in views:
            if view.kind != "hist" or not _cheap_hist_view(view.name):
                continue
            if view.name not in present:
                probes.append(
                    DbProbe(
                        name=view.name,
                        present=False,
                        row_count=None,
                        note=(
                            "view missing from research.duckdb; filesystem glob checked separately"
                        ),
                    )
                )
                continue
            quoted = _quote_ident(view.name)
            if quoted is None:
                probes.append(
                    DbProbe(
                        name=view.name,
                        present=True,
                        row_count=None,
                        note="unsafe view name skipped",
                    )
                )
                continue
            try:
                row = connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()
            except duckdb.Error as error:
                probes.append(
                    DbProbe(
                        name=view.name,
                        present=True,
                        row_count=None,
                        note=f"duckdb_error:{type(error).__name__}",
                    )
                )
                continue
            count = None if row is None else _optional_int(row[0])
            probes.append(DbProbe(name=view.name, present=True, row_count=count, note="counted"))
        return tuple(probes)
    finally:
        connection.close()


def _database_relation_names(connection: duckdb.DuckDBPyConnection) -> set[str]:
    rows = connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    names: set[str] = set()
    for row in rows:
        if row and type(row[0]) is str:
            names.add(row[0])
    return names


def _cheap_hist_view(name: str) -> bool:
    if not name.startswith("hist_") or "aggtrades" in name:
        return False
    return name.endswith(("_1h", "_4h", "_12h", "_1d", "_funding"))


def _quote_ident(name: str) -> str | None:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
        return None
    return f'"{name}"'


def _emit_json_or_text(value: object, *, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(to_jsonable(value), indent=2, sort_keys=True))
        return
    print(text)


def _utc(timestamp: float | None) -> str:
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_inventory(report: InventoryReport) -> str:
    lines = [f"artifact_root={report.artifact_root}", f"hist_root={report.hist_root}", ""]
    for run in report.runs:
        note = "; ".join(run.notes) if run.notes else "-"
        lines.append(
            " ".join(
                (
                    run.lane,
                    run.run_id,
                    f"parts={run.disk_parquet_parts}",
                    f"claim={run.claim_path.name if run.claim_present else 'missing'}",
                    "health="
                    + (run.health_status or ("missing" if not run.health_present else "present")),
                    f"gaps={run.health_gaps if run.health_gaps is not None else '-'}",
                    f"window={_utc(run.first_part_mtime)}..{_utc(run.last_part_mtime)}",
                    f"notes={note}",
                )
            )
        )
    lines.append("")
    lines.append(_render_smoke(report.duckdb))
    return "\n".join(lines)


def _render_smoke(report: DuckdbSmokeReport) -> str:
    lines = [
        f"catalog={report.catalog_path} present={report.catalog_present}",
        f"database={report.database_path} present={report.database_present}",
    ]
    for view in report.views:
        note = "; ".join(view.notes) if view.notes else "-"
        pin = view.pinned_run_id or "-"
        lines.append(f"{view.kind} {view.name} files={view.parquet_files} pin={pin} notes={note}")
    for probe in report.probes:
        lines.append(
            f"probe {probe.name} present={probe.present} rows={probe.row_count} note={probe.note}"
        )
    if report.fatal_notes:
        lines.append("fatal=" + "; ".join(report.fatal_notes))
    if report.suggested_sql:
        lines.append("")
        lines.append("suggested SQL (not applied; rebuild catalog only under hist-archives):")
        lines.extend(report.suggested_sql)
    return "\n".join(lines)


def _render_bn(report: BnGapReport) -> str:
    lines: list[str] = []
    for segment in report.segments:
        counts = segment.log_counts
        lines.append(
            " ".join(
                (
                    segment.run.run_id,
                    f"status={segment.run.health_status or '-'}",
                    f"parts={segment.run.disk_parquet_parts}",
                    f"disconnects={counts.disconnects}",
                    f"reconnects={counts.reconnects}",
                    f"starvation_gaps={counts.starvation_gaps}",
                    f"liveness_errors={counts.liveness_errors}",
                    f"alerts={counts.operator_alerts}",
                )
            )
        )
        for note in segment.notes:
            lines.append(f"  {note}")
    for hole in report.holes:
        lines.append(
            f"hole {hole.previous_run_id} -> {hole.next_run_id} gap_seconds={hole.gap_seconds:.1f}"
        )
    return "\n".join(lines)


def _render_bv(report: BvPingReport) -> str:
    lines = [f"runs_scanned={report.runs_scanned}"]
    for hit in report.hits:
        lines.append(
            " ".join(
                (
                    hit.run_id,
                    f"status={hit.health_status or '-'}",
                    f"ping_timeout_lines={hit.ping_timeout_lines}",
                    f"first={hit.first_log_timestamp or '-'}",
                    f"last={hit.last_log_timestamp or '-'}",
                    f"parts={hit.disk_parquet_parts}",
                )
            )
        )
    if report.phase_a_without_ping_timeout:
        lines.append("phase-a without keepalive ping timeout:")
        lines.extend(f"  {run_id}" for run_id in report.phase_a_without_ping_timeout)
    return "\n".join(lines)


def _render_continuity(report: ContinuityReport) -> str:
    lines = [f"lanes={','.join(report.lanes)}"]
    for note in report.notes:
        lines.append(f"note {note}")
    for hole in report.holes:
        lines.append(
            f"hole {hole.lane} {hole.previous_run_id} -> {hole.next_run_id} "
            f"gap_seconds={hole.gap_seconds:.1f}"
        )
    for window in report.overlap_windows:
        lines.append(
            f"overlap {_utc(window.start_mtime)}..{_utc(window.end_mtime)} "
            f"seconds={window.seconds:.1f}"
        )
    for shared in report.shared_runs:
        missing = ",".join(shared.missing_lanes) if shared.missing_lanes else "-"
        present = ",".join(shared.present_lanes)
        lines.append(f"run {shared.run_id} present={present} missing={missing}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
