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
