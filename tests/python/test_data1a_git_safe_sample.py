"""Offline tests for the git-safe DATA-1A sample exporter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyperliquid_bot.data1a_git_safe_sample import (
    GIT_SAFE_SAMPLE_SCHEMA,
    export_git_safe_sample,
)
from hyperliquid_bot.hyperliquid_raw_research import (
    MAX_CAPTURE_SECONDS,
    data1a_capture_claim,
    data1a_capture_health,
)
from hyperliquid_bot.parquet_research import ParquetResearchWriter, ParquetRotation
from hyperliquid_bot.raw_research import (
    RAW_RESEARCH_SCHEMA_VERSION,
    FrameType,
    MessageDirection,
    PayloadEncoding,
    RawResearchRecord,
)
from hyperliquid_bot.reconstructable_paths import DATA1A_PATH_CONTRACT_ID, data1a_run_paths


def _marker_record(ordinal: int) -> RawResearchRecord:
    return RawResearchRecord(
        schema_version=RAW_RESEARCH_SCHEMA_VERSION,
        venue="hyperliquid",
        product="BTC-PERP",
        channel="session",
        session_id="session-one",
        message_ordinal=ordinal,
        received_utc_ns=1_000 + ordinal,
        received_monotonic_ns=2_000 + ordinal,
        direction=MessageDirection.LOCAL,
        frame_type=FrameType.MARKER,
        payload_encoding=PayloadEncoding.UTF8_JSON,
        payload_bytes=b'{"event":"session_started"}',
    )


async def _write_two_part_run(root: Path, run_id: str) -> None:
    paths = data1a_run_paths(root, run_id)
    paths.run_dir.mkdir(parents=True)
    paths.raw_dir.mkdir()
    claim = data1a_capture_claim(run_id=run_id, duration_seconds=14_400, paths=paths)
    paths.capture_claim_path.write_text(
        json.dumps(claim, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    writer = ParquetResearchWriter(
        paths.raw_dir,
        rotation=ParquetRotation(max_records=2, max_payload_bytes=1024, max_interval_seconds=60),
    )
    await writer.append(_marker_record(1))
    await writer.append(_marker_record(2))
    await writer.append(_marker_record(3))
    await writer.aclose()
    health = data1a_capture_health(
        run_id=run_id,
        duration_seconds=14_400,
        status="COMPLETED",
        report={
            "events": 3,
            "payload_bytes": 75,
            "parquet_files": 2,
            "parquet_bytes": 1,
            "gaps": 0,
            "reconnects": 0,
        },
    )
    paths.capture_health_path.write_text(
        json.dumps(health, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_git_safe_sample_omits_payload_bytes(tmp_path: Path) -> None:
    root = tmp_path / "reconstructable"
    await _write_two_part_run(root, "sample-run")
    output = tmp_path / "git-safe"
    summary = export_git_safe_sample(
        artifact_root=root,
        run_id="sample-run",
        output_dir=output,
        max_rows=8,
    )
    assert summary["schema"] == GIT_SAFE_SAMPLE_SCHEMA
    assert summary["path_contract"] == DATA1A_PATH_CONTRACT_ID
    assert summary["retained"] is True
    assert summary["duration_seconds"] == 14_400.0
    assert summary["payloads_included"] is False
    assert summary["parquet_files"] == 2
    assert summary["sample_row_count"] == 3
    sample_rows = json.loads((output / "sample-rows.json").read_text(encoding="utf-8"))
    assert {row["message_ordinal"] for row in sample_rows} == {1, 2, 3}
    for row in sample_rows:
        assert "payload_bytes" not in row
        assert row["payload_bytes_len"] == len(b'{"event":"session_started"}')
        assert row["payload_sha256"]
    dumped = json.dumps(sample_rows)
    assert "session_started" not in dumped
    with pytest.raises(FileExistsError, match="refuses to reuse"):
        export_git_safe_sample(artifact_root=root, run_id="sample-run", output_dir=output)


def test_data1a_docs_state_the_604800_contract() -> None:
    data_doc = Path("docs/DATA.md").read_text(encoding="utf-8")
    runbook = Path("docs/runbooks/data1a-vps-retained-capture.md").read_text(encoding="utf-8")
    soak_readme = Path("vertical_slices/course1_live_public_paper/README.md").read_text(
        encoding="utf-8"
    )
    assert "1 through 604800 seconds" in data_doc
    assert "from 1 through 600 seconds" not in data_doc
    assert "1 through 604800 seconds" in runbook
    assert MAX_CAPTURE_SECONDS == 604800
    assert "DATA-1A retained-capture contract (1-604800s)" in soak_readme
    assert "Duration is an integer between 1 and 600 seconds" in soak_readme


def test_live_evidence_fixture_is_retained_and_payload_free() -> None:
    fixture = Path("tests/fixtures/data_1a_live_evidence/20260904t001700z-live-retained")
    claim = json.loads((fixture / "capture-claim.json").read_text(encoding="utf-8"))
    summary = json.loads((fixture / "run-summary.json").read_text(encoding="utf-8"))
    health = json.loads((fixture / "capture-health.json").read_text(encoding="utf-8"))
    sample_rows = json.loads((fixture / "sample-rows.json").read_text(encoding="utf-8"))
    assert claim["run_id"] == "20260904t001700z-live-retained"
    assert claim["retained"] is True
    assert claim["duration_seconds"] == 14_400.0
    assert claim["signing"] is False
    assert claim["credentialless"] is True
    assert claim["twenty_four_seven"] is False
    assert claim["websocket_url"] == "wss://api.hyperliquid.xyz/ws"
    assert 1.0 <= float(claim["duration_seconds"]) <= float(MAX_CAPTURE_SECONDS)
    assert health["status"] == "OPERATOR_STOP"
    assert health["retained"] is True
    assert health["twenty_four_seven"] is False
    assert health["events"] == 19_404
    assert health["parquet_files"] == 41
    assert summary["payloads_included"] is False
    assert summary["retained"] is True
    assert summary["health_present"] is True
    assert summary["sample_row_count"] == len(sample_rows)
    assert summary["parquet_files"] == 41
    for row in sample_rows:
        assert "payload_bytes" not in row
        assert row["venue"] == "hyperliquid"
        assert row["product"] == "BTC-PERP"
    assert not list(fixture.glob("*.parquet"))
    assert not (fixture / "research.duckdb").exists()
