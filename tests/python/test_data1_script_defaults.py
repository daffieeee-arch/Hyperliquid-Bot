"""Regression tests for the primary VPS capture paths."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_HOME = "/home/chupa"


@pytest.mark.parametrize("lane", ("a", "b", "e", "f"))
@pytest.mark.parametrize(
    ("function_suffix", "relative_path"),
    (
        ("default_repo_root", "Hyperliquid Project/Hyperliquid-Bot"),
        ("default_artifact_root", "Hyperliquid Project/data-capture"),
    ),
)
def test_capture_helpers_default_to_primary_vps_paths(
    lane: str,
    function_suffix: str,
    relative_path: str,
) -> None:
    script = REPO_ROOT / "scripts" / f"data1{lane}_lib.sh"
    function_name = f"data1{lane}_{function_suffix}"
    completed = subprocess.run(
        ["bash", "-c", 'source "$1"; "$2"', "bash", str(script), function_name],
        check=True,
        capture_output=True,
        env={**os.environ, "HOME": TEST_HOME},
        text=True,
    )

    assert completed.stdout.strip() == f"{TEST_HOME}/{relative_path}"


@pytest.mark.parametrize("lane", ("a", "b", "e", "f"))
def test_start_helpers_accept_spaced_vps_defaults(tmp_path: Path, lane: str) -> None:
    home = tmp_path / "home"
    repo_root = home / "Hyperliquid Project" / "Hyperliquid-Bot"
    repo_root.mkdir(parents=True)
    artifact_root = home / "Hyperliquid Project" / "data-capture"
    script = REPO_ROOT / "scripts" / f"data1{lane}_start.sh"
    environment = {
        **os.environ,
        "HOME": str(home),
        "DURATION_SECONDS": "60",
        "RUN_ID": f"test-data1{lane}-vps-defaults",
    }

    completed = subprocess.run(
        ["bash", str(script), "--check-only"],
        check=True,
        capture_output=True,
        cwd=repo_root,
        env=environment,
        text=True,
    )

    assert f"repo_root={repo_root}" in completed.stdout
    assert f"artifact_root={artifact_root}" in completed.stdout
    assert "status=CHECK_ONLY" in completed.stdout


@pytest.mark.parametrize("lane", ("a", "b", "e", "f"))
def test_start_helpers_shell_quote_spaced_vps_paths(lane: str) -> None:
    text = (REPO_ROOT / "scripts" / f"data1{lane}_start.sh").read_text(encoding="utf-8")

    assert '$(printf \'%q\' "${REPO_ROOT}")' in text
    assert '$(printf \'%q\' "${ARTIFACT_ROOT}")' in text
