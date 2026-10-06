"""The capture-alert wrapper must import src without a caller PYTHONPATH."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_HIDDEN_ENV = {
    "PYTHONPATH",
    "CAPTURE_ALERT_WEBHOOK_URL",
    "CAPTURE_ALERT_WEBHOOK_AUTHORIZATION",
}


def _spaced_checkout(tmp_path: Path) -> Path:
    """Point a spaced path at this repo so cd quoting is part of the test."""
    spaced = tmp_path / "Hyperliquid Project" / "Hyperliquid-Bot"
    spaced.mkdir(parents=True)
    for name in ("src", "scripts", "pyproject.toml", "uv.lock", ".venv"):
        (spaced / name).symlink_to(REPO_ROOT / name)
    python_version = REPO_ROOT / ".python-version"
    if python_version.exists():
        (spaced / ".python-version").symlink_to(python_version)
    return spaced


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _HIDDEN_ENV}
    env.setdefault("PYTEST_CURRENT_TEST", "test_capture_alert_wrapper")
    return env


def test_wrapper_imports_from_other_cwd_and_propagates_python_failure(tmp_path: Path) -> None:
    spaced = _spaced_checkout(tmp_path)
    other_cwd = tmp_path / "not-the-repo"
    other_cwd.mkdir()
    script = spaced / "scripts" / "capture_alert.sh"
    env = _env()
    assert "PYTHONPATH" not in env

    imported = subprocess.run(
        [
            "bash",
            str(script),
            "test",
            "--venue",
            "binance",
            "--run-id",
            "wrapper-import",
        ],
        check=False,
        capture_output=True,
        cwd=other_cwd,
        env=env,
        text=True,
    )

    assert imported.returncode == 0, imported.stderr
    assert "ModuleNotFoundError" not in imported.stderr
    assert "delivery=dry_run" in imported.stdout
    assert "state=TEST" in imported.stdout

    failed = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        cwd=other_cwd,
        env=env,
        text=True,
    )
    combined = failed.stdout + failed.stderr
    assert failed.returncode != 0
    assert "ModuleNotFoundError" not in combined
    assert "error:" in combined
