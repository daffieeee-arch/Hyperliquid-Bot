"""Read-only Phase A inventory and research-smoke tests. No capture processes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
from pytest import CaptureFixture

from hyperliquid_bot.phase_a_research_tools import (
    RESEARCH_LANES,
    bn_gap_report,
    bv_ping_report,
    continuity_report,
    duckdb_smoke,
    inventory_runs,
    main,
)
from hyperliquid_bot.reconstructable_paths import (
    DATA1A_RELATIVE_PREFIX,
    DATA1E_RELATIVE_PREFIX,
    DATA1F_RELATIVE_PREFIX,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = REPO_ROOT / "src" / "hyperliquid_bot" / "phase_a_research_tools.py"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "phase-a-research-inventory.md"


def _part(raw: Path, name: str, mtime: float) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / name
    path.write_bytes(b"parquet-bytes-not-opened")
    os.utime(path, (mtime, mtime))


def _claim(
    run_dir: Path, *, state: str = "STARTED_FAIL_CLOSED", duration: float = 259200.0
) -> None:
    payload = {
        "retained": True,
        "state": state,
        "duration_seconds": duration,
        "run_id": run_dir.name,
    }
    (run_dir / "capture-claim.json").write_text(json.dumps(payload), encoding="utf-8")


def _health(
    run_dir: Path,
    *,
    status: str,
    elapsed: float,
    gaps: int = 0,
    reconnects: int = 0,
    parquet_files: int = 1,
    events: int = 10,
) -> None:
    payload = {
        "status": status,
        "elapsed_seconds": elapsed,
        "gaps": gaps,
        "reconnects": reconnects,
        "parquet_files": parquet_files,
        "events": events,
    }
    (run_dir / "capture-health.json").write_text(json.dumps(payload), encoding="utf-8")


def _bn_tree(root: Path) -> Path:
    lane = root.joinpath(*DATA1F_RELATIVE_PREFIX)
    failed = lane / "20260920t010000z-phase-a-bn"
    follow = lane / "20260920t020000z-phase-a-bn-continue"
    _part(failed / "raw", "part-000001.parquet", 1_000.0)
    _part(failed / "raw", "part-000002.parquet", 2_000.0)
    _claim(failed)
    _health(
        failed, status="FAILED", elapsed=1000.0, gaps=2, reconnects=2, parquet_files=0, events=0
    )
    (failed / "capture-20260920t010000z-phase-a-bn.log").write_text(
        "\n".join(
            (
                "2026-09-20 01:00:00,000 INFO binance disconnect transport_profile=spot",
                "2026-09-20 01:00:01,000 INFO binance reconnect transport_profile=spot attempt=1",
                "2026-09-20 01:10:00,000 INFO binance liveness_gap transport_profile=spot "
                "reason=required_stream_starved stream=btcusdt@depth@100ms",
                "2026-09-20 01:11:00,000 INFO binance liveness_error transport_profile=spot "
                "reason=required_stream_starved stream=btcusdt@bookTicker",
                "2026-09-20 01:11:01,000 ERROR capture_operator_alert venue=binance status=FAILED",
                "2026-09-20 01:11:02,000 WARNING capture_operator_alert_webhook_failed "
                "error_class=HTTPError",
            )
        ),
        encoding="utf-8",
    )
    _part(follow / "raw", "part-000001.parquet", 2_500.0)
    _claim(follow)
    return lane


def _bv_tree(root: Path) -> None:
    lane = root.joinpath(*DATA1E_RELATIVE_PREFIX)
    run = lane / "20260919t010000z-phase-a-bv"
    quiet = lane / "20260919t020000z-phase-a-bv-continue"
    _part(run / "raw", "part-000001.parquet", 1_000.0)
    _part(run / "raw", "part-000002.parquet", 2_000.0)
    _claim(run)
    _health(run, status="FAILED", elapsed=10.0, parquet_files=1, events=3)
    (run / "capture-bv.log").write_text(
        "2026-09-19 02:53:32,028 INFO bitvavo disconnect transport_profile=market_data_pro "
        "close_code_sent=1011 close_reason_sent=keepalive ping timeout errno=None\n",
        encoding="utf-8",
    )
    _part(quiet / "raw", "part-000001.parquet", 2_500.0)
    _claim(quiet)
    _health(quiet, status="COMPLETED", elapsed=10.0, parquet_files=1, events=4)
    (quiet / "capture-bv.log").write_text(
        "2026-09-19 04:00:00,000 INFO bitvavo session_start\n",
        encoding="utf-8",
    )


def test_inventory_reports_claim_health_parts_and_gap_notes(tmp_path: Path) -> None:
    _bn_tree(tmp_path)
    marker = tmp_path.joinpath(*DATA1A_RELATIVE_PREFIX, "not-a-valid-RUN.FAILED-marker")
    marker.mkdir(parents=True)
    (marker / "raw").mkdir()
    runs = inventory_runs(tmp_path)
    by_id = {run.run_id: run for run in runs}
    failed = by_id["20260920t010000z-phase-a-bn"]
    assert failed.claim_path.name == "capture-claim.json"
    assert failed.health_path.name == "capture-health.json"
    assert failed.claim_present and failed.health_present
    assert failed.health_status == "FAILED"
    assert failed.disk_parquet_parts == 2
    assert failed.health_parquet_files == 0
    assert any("differs from disk part count" in note for note in failed.notes)
    assert any("events=0" in note for note in failed.notes)
    follow = by_id["20260920t020000z-phase-a-bn-continue"]
    assert any("health not written yet" in note for note in follow.notes)
    assert any("not a resume" in note for note in follow.notes)
    invalid = by_id["not-a-valid-RUN.FAILED-marker"]
    assert invalid.valid_run_id is False
    assert any("not a valid run_id" in note for note in invalid.notes)
    assert any("FAILED start marker" in note for note in invalid.notes)
    assert "not-a-valid-RUN.FAILED-marker" in {run.run_id for run in runs}


def test_symlink_run_dir_is_not_listed(tmp_path: Path) -> None:
    lane = _bn_tree(tmp_path)
    target = lane / "20260920t010000z-phase-a-bn"
    link = lane / "20260920t030000z-phase-a-link"
    link.symlink_to(target, target_is_directory=True)
    names = {run.run_id for run in inventory_runs(tmp_path)}
    assert "20260920t030000z-phase-a-link" not in names


def test_bn_gaps_describe_starvation_then_continue_without_log_bodies(tmp_path: Path) -> None:
    _bn_tree(tmp_path)
    report = bn_gap_report(inventory_runs(tmp_path), hole_seconds=120.0, phase_a_only=True)
    assert report.segments[0].log_counts.disconnects == 1
    assert report.segments[0].log_counts.reconnects == 1
    assert report.segments[0].log_counts.starvation_gaps == 1
    assert report.segments[0].log_counts.liveness_errors == 1
    assert report.segments[0].log_counts.operator_alerts == 1
    assert report.segments[0].log_counts.liveness_error_streams == ("btcusdt@bookTicker",)
    note = report.segments[0].notes[0]
    assert "required-stream starvation" in note
    assert "20260920t020000z-phase-a-bn-continue" in note
    assert "not resumed" in note
    assert "HTTPError" not in note
    assert report.holes[0].gap_seconds == 500.0
    encoded = json.dumps(json.loads(json.dumps({"note": note})))
    assert "close_reason" not in encoded


def test_bn_gaps_skip_phase_a_smoke_unless_all_runs(tmp_path: Path) -> None:
    _bn_tree(tmp_path)
    smoke = tmp_path.joinpath(*DATA1F_RELATIVE_PREFIX, "20260920t030000z-phase-a-smoke")
    _part(smoke / "raw", "part-000001.parquet", 4_000.0)
    _claim(smoke)
    runs = inventory_runs(tmp_path)
    assert any(run.run_id.endswith("phase-a-smoke") for run in runs)
    campaign = bn_gap_report(runs, hole_seconds=120.0, phase_a_only=True)
    assert all("smoke" not in segment.run.run_id for segment in campaign.segments)
    everything = bn_gap_report(runs, hole_seconds=120.0, phase_a_only=False)
    assert any("smoke" in segment.run.run_id for segment in everything.segments)


def test_bv_ping_counts_keepalive_line_once(tmp_path: Path) -> None:
    _bv_tree(tmp_path)
    report = bv_ping_report(inventory_runs(tmp_path))
    assert report.runs_scanned == 2
    assert len(report.hits) == 1
    hit = report.hits[0]
    assert hit.ping_timeout_lines == 1
    assert hit.first_log_timestamp == "2026-09-19 02:53:32"
    assert hit.health_status == "FAILED"
    assert report.phase_a_without_ping_timeout == ("20260919t020000z-phase-a-bv-continue",)


def test_continuity_reports_holes_overlap_and_missing_lanes(tmp_path: Path) -> None:
    _bn_tree(tmp_path)
    _bv_tree(tmp_path)
    hl = tmp_path.joinpath(*DATA1A_RELATIVE_PREFIX, "20260920t010000z-phase-a-bn")
    _part(hl / "raw", "part-000001.parquet", 1_000.0)
    _part(hl / "raw", "part-000002.parquet", 3_000.0)
    _claim(hl)
    lanes = tuple(lane for lane in RESEARCH_LANES if lane.label in {"HL", "BN", "BV-Pro"})
    report = continuity_report(
        inventory_runs(tmp_path),
        lanes=lanes,
        hole_seconds=120.0,
        phase_a_only=True,
    )
    assert any(hole.lane == "BN" and hole.gap_seconds == 500.0 for hole in report.holes)
    assert report.overlap_windows
    assert report.overlap_windows[0].seconds == 1000.0
    shared = {item.run_id: item for item in report.shared_runs}
    joint = shared["20260920t010000z-phase-a-bn"]
    assert joint.present_lanes == ("HL", "BN")
    assert "BV-Pro" in joint.missing_lanes


def test_duckdb_smoke_flags_empty_hist_and_stale_live_pin(tmp_path: Path) -> None:
    artifact = tmp_path / "data-capture"
    hist = tmp_path / "hist-archives"
    _bn_tree(artifact)
    newer = artifact.joinpath(*DATA1F_RELATIVE_PREFIX, "20260921t010000z-phase-a-bn-continue2")
    _part(newer / "raw", "part-000001.parquet", 9_000.0)
    _claim(newer)
    hist.mkdir()
    (hist / "catalog.sql").write_text(
        "\n".join(
            (
                "CREATE OR REPLACE VIEW hist_kr_xbtusd_1d AS",
                "  SELECT * FROM read_parquet('__HIST__/missing_1d.parquet');",
                "CREATE OR REPLACE VIEW live_bn_parts AS",
                "  SELECT * FROM read_parquet(",
                "    '__DC__/data-1f/binance/BTCUSDT/20260920t010000z-phase-a-bn/raw/*.parquet',",
                "    union_by_name := true",
                "  );",
            )
        ),
        encoding="utf-8",
    )
    pinned = artifact.joinpath(
        *DATA1F_RELATIVE_PREFIX, "20260920t010000z-phase-a-bn", "raw", "part-000001.parquet"
    )
    assert pinned.is_file()
    report = duckdb_smoke(
        artifact_root=artifact,
        hist_root=hist,
        runs=inventory_runs(artifact),
        probe_db=False,
        strict_pins=True,
    )
    assert any("hist_kr_xbtusd_1d matches 0" in note for note in report.fatal_notes)
    live = next(view for view in report.views if view.name == "live_bn_parts")
    assert live.pinned_run_id == "20260920t010000z-phase-a-bn"
    assert live.newest_phase_a_run_id == "20260921t010000z-phase-a-bn-continue2"
    assert live.parquet_files == 2
    assert any(note.startswith("live view pins") for note in live.notes)
    assert any("live_bn_phase_a_parts" in sql for sql in report.suggested_sql)
    assert any("stale phase-a run_id" in note for note in report.fatal_notes)


def test_duckdb_probe_counts_small_hist_view_read_only(tmp_path: Path) -> None:
    artifact = tmp_path / "data-capture"
    hist = tmp_path / "hist-archives"
    artifact.mkdir()
    hist.mkdir()
    parquet = hist / "xbtusd_1d.parquet"
    database = hist / "research.duckdb"
    parquet_sql = str(parquet)
    assert "'" not in parquet_sql
    built = duckdb.connect(str(database))
    built.execute(
        f"COPY (SELECT 1 AS n, TIMESTAMP '2020-01-01' AS ts) TO '{parquet_sql}' (FORMAT PARQUET)"
    )
    built.execute(f"CREATE VIEW hist_kr_xbtusd_1d AS SELECT * FROM read_parquet('{parquet_sql}')")
    built.close()
    (hist / "catalog.sql").write_text(
        "CREATE OR REPLACE VIEW hist_kr_xbtusd_1d AS\n"
        "  SELECT * FROM read_parquet('__HIST__/xbtusd_1d.parquet');\n",
        encoding="utf-8",
    )
    before = database.stat().st_mtime
    report = duckdb_smoke(
        artifact_root=artifact,
        hist_root=hist,
        runs=(),
        probe_db=True,
        strict_pins=False,
    )
    assert database.stat().st_mtime == before
    probe = report.probes[0]
    assert probe.name == "hist_kr_xbtusd_1d"
    assert probe.present is True
    assert probe.row_count == 1
    assert report.fatal_notes == ()


def test_cli_json_inventory_and_argument_errors(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    artifact = tmp_path / "data-capture"
    hist = tmp_path / "hist"
    _bn_tree(artifact)
    hist.mkdir()
    code = main(
        [
            "inventory",
            "--artifact-root",
            str(artifact),
            "--hist-root",
            str(hist),
            "--json",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["runs"][0]["lane"] == "BN"
    assert payload["duckdb"]["catalog_present"] is False
    assert main(["bn-gaps", "--hole-seconds", "-1", "--artifact-root", str(artifact)]) == 2
    assert main(["inventory", "--artifact-root", str(tmp_path / "missing")]) == 1
    assert (
        main(
            [
                "continuity",
                "--artifact-root",
                str(artifact),
                "--lanes",
                "NOPE",
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "duckdb-smoke",
                "--artifact-root",
                str(artifact),
                "--hist-root",
                str(hist),
                "--probe-db",
            ]
        )
        == 1
    )


def test_module_does_not_control_captures() -> None:
    text = MODULE.read_text(encoding="utf-8")
    assert "send-keys" not in text
    assert "kill-server" not in text
    assert "data1f_start" not in text
    assert "data1e_stop" not in text
    assert "read_only=True" in text


def test_runbook_documents_vps_commands() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "phase_a_research_tools" in text
    assert "inventory" in text
    assert "duckdb-smoke" in text
    assert "bn-gaps" in text
    assert "bv-ping" in text
    assert "continuity" in text
    assert "--probe-db" in text
    assert "Do not stop" in text
    assert "hist-archives" in text
    assert "build_catalog.sh" in text
