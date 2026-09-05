"""Deterministic COURSE-1 exit-gate evidence verifier tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from vertical_slices.course1_exit_gate.verify import (
    DOES_NOT_PROVE,
    PROVES,
    SCHEMA,
    ExitGateError,
    main,
    verify_artifacts,
)

_FIXTURE_DIR = Path("tests/fixtures/course1_cockpit/live-public-soak")
_REQUIRED_CLAIM_ABSENCES = (
    "profitability",
    "TESTNET",
    "SHADOW",
    "LIVE",
    "24/7",
    "funding settlement",
    "D22-B",
    "live-network soak inside CI",
)


def test_committed_soak_fixture_verifies() -> None:
    result = verify_artifacts(_FIXTURE_DIR)
    assert result["schema"] == SCHEMA
    assert result["status"] == "VERIFIED"
    assert result["run_id"] == "20260904t001800z-live-paper"
    assert result["proves"] == list(PROVES)
    assert result["does_not_prove"] == list(DOES_NOT_PROVE)
    claim_boundary = result["claim_boundary"]
    assert isinstance(claim_boundary, dict)
    assert claim_boundary["proves"] == list(PROVES)
    assert claim_boundary["does_not_prove"] == list(DOES_NOT_PROVE)
    for needle in _REQUIRED_CLAIM_ABSENCES:
        assert any(needle in item for item in DOES_NOT_PROVE)
    reconstruction = result["reconstruction"]
    assert isinstance(reconstruction, dict)
    assert reconstruction["final_position_btc"] == "0.00000"
    assert reconstruction["ending_equity_usdc_assumed"] == "99999.9873416920"
    invariants = result["invariants"]
    assert isinstance(invariants, dict)
    assert invariants["mode"] == "PAPER"
    assert invariants["assumed_pnl"] is True
    assert invariants["venue_pnl"] is False
    assert invariants["signing"] is False
    assert invariants["d22b_venue_authoritative_reconciliation"] is False
    assert invariants["twenty_four_seven"] is False
    integrity = result["integrity"]
    assert isinstance(integrity, dict)
    assert integrity["hash_algorithm"] == "sha256"
    artifact_files = integrity["artifact_files"]
    assert isinstance(artifact_files, dict)
    assert "fills.json" in artifact_files
    assert "paper-pnl.json" in artifact_files


def test_tampered_pnl_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "soak"
    shutil.copytree(_FIXTURE_DIR, target)
    pnl_path = target / "paper-pnl.json"
    pnl = json.loads(pnl_path.read_text(encoding="utf-8"))
    pnl["ending_equity_usdc_assumed"] = "100000.01"
    pnl_path.write_text(json.dumps(pnl, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ExitGateError, match=r"ending_equity_usdc_assumed"):
        verify_artifacts(target)


def test_tampered_position_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "soak"
    shutil.copytree(_FIXTURE_DIR, target)
    position_path = target / "paper-position.json"
    position = json.loads(position_path.read_text(encoding="utf-8"))
    position["final_position_btc"] = "0.00013"
    position_path.write_text(json.dumps(position, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ExitGateError, match=r"paper-position\.final_position_btc"):
        verify_artifacts(target)


def test_cli_verify_exits_nonzero_on_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "soak"
    shutil.copytree(_FIXTURE_DIR, target)
    claim_path = target / "run-claim.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["mode"] = "LIVE"
    claim_path.write_text(json.dumps(claim, indent=2) + "\n", encoding="utf-8")
    code = main(["verify", "--artifact-dir", str(target)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert payload["status"] == "FAILED"
    assert "proves" in payload
    assert "does_not_prove" in payload


def test_cli_verify_happy_path(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["verify", "--artifact-dir", str(_FIXTURE_DIR)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["status"] == "VERIFIED"
    assert payload["schema"] == SCHEMA
    assert "proves" in payload
    assert "does_not_prove" in payload
