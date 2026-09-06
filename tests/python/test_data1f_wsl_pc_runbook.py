"""Offline tests for the DATA-1F operator PC/WSL runbook and create-only helpers."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
WSL_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "data1f-wsl-pc-retained-capture.md"
VPS_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "data1f-vps-retained-capture.md"
SECRET_PROBE = "super-secret-value-must-never-be-printed"


def _script_env(
    tmp_path: Path,
    *,
    extra: Mapping[str, str] | None = None,
    run_id: str = "20260904t000000z-live-retained",
    duration: str = "259200",
    tmux_session: str = "bn-capture",
) -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "HYPERLIQUID_PK",
        "HYPERLIQUID_TESTNET_PK",
        "HYPERLIQUID_VAULT",
        "HYPERLIQUID_TESTNET_VAULT",
        "HYPERLIQUID_ACCOUNT_ADDRESS",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_SECRET",
        "BINANCE_API_KEY_TESTNET",
        "BINANCE_TESTNET_API_SECRET",
        "TRADING_MODE",
        "D41_EXECUTION_MODE",
        "RUN_ID",
        "DURATION_SECONDS",
        "ARTIFACT_ROOT",
        "REPO_ROOT",
        "TMUX_SESSION",
    ):
        env.pop(name, None)
    env.update(
        {
            "ARTIFACT_ROOT": str(tmp_path / "reconstructable"),
            "REPO_ROOT": str(REPO_ROOT),
            "RUN_ID": run_id,
            "DURATION_SECONDS": duration,
            "TMUX_SESSION": tmux_session,
            "HOME": str(tmp_path / "home"),
        }
    )
    if extra:
        env.update(extra)
    return env


def _run(
    script: str,
    tmp_path: Path,
    *,
    extra: Mapping[str, str] | None = None,
    args: tuple[str, ...] = (),
    run_id: str = "20260904t000000z-live-retained",
    duration: str = "259200",
    tmux_session: str = "bn-capture",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / script), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=_script_env(
            tmp_path,
            extra=extra,
            run_id=run_id,
            duration=duration,
            tmux_session=tmux_session,
        ),
    )


def _write_run_tree(
    tmp_path: Path,
    run_id: str,
    *,
    parts: int = 2,
    health: bool = False,
    health_status: str = "OPERATOR_STOP",
    health_extra: dict[str, Any] | None = None,
    stale_seconds: float | None = None,
    log_text: str | None = None,
) -> Path:
    run_dir = tmp_path / "reconstructable" / "data-1f" / "binance" / "BTCUSDT" / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "capture-claim.json").write_text(
        json.dumps({"run_id": run_id, "retained": True, "signing": False}),
        encoding="utf-8",
    )
    if health:
        payload: dict[str, Any] = {"status": health_status, "run_id": run_id}
        if health_extra:
            payload.update(health_extra)
        (run_dir / "capture-health.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
    for index in range(parts):
        (raw_dir / f"part-{index:05d}.parquet").write_bytes(b"not-a-real-parquet")
    (raw_dir / ".partial-ignored.parquet").write_bytes(b"hidden")
    if stale_seconds is not None:
        stamp = time.time() - stale_seconds
        for part in raw_dir.glob("part-*.parquet"):
            os.utime(part, (stamp, stamp))
    if log_text is not None:
        (run_dir / f"capture-{run_id}.log").write_text(log_text, encoding="utf-8")
    return run_dir


def test_wsl_runbook_documents_known_good_operator_paths() -> None:
    text = WSL_RUNBOOK.read_text(encoding="utf-8")
    assert "~/code/Hyperliquid-Bot-main" in text
    assert "~/hyperliquid-artifacts/reconstructable" in text
    assert "data-1f/binance/BTCUSDT/<run_id>/" in text
    assert "bn-capture" in text
    assert "hl-capture" in text
    assert "259200" in text
    assert "tmux send-keys -t bn-capture C-c" in text
    assert "never resume" in text.lower() or "Never resume" in text
    assert "python -m hyperliquid_bot.binance_public_research" in text
    assert "python -m hyperliquid_bot.hypothesis_research" in text
    assert "--baseline basis" in text
    assert "--binance-parquet-dir" in text
    assert "Cloud Agents are **unsuitable**" in text
    assert "must not SSH" in text
    assert "Do not start a multi-day DATA-1F retain now" in text
    assert "elapsed_seconds" in text
    assert "capture-<run_id>.log" in text
    assert "Protect a 72h evidence window" in text
    assert "usdm_market" in text
    assert "usdm_public" in text
    assert "/public" in text
    assert "liveness_error" in text
    assert "required_stream_starvation_seconds" in text
    assert "forceOrder" in text
    assert "ping_interval=None" in text
    assert "1011" in text
    assert "BN process restart" in text or "BN-only restart" in text
    vps_text = VPS_RUNBOOK.read_text(encoding="utf-8")
    assert "liveness_error" in vps_text
    assert "forceOrder" in vps_text
    assert "ping_interval=None" in vps_text
    assert "1011" in vps_text
    assert "Do **not** send `C-c`" in text
    assert "CoS assigns" in text or "CoS assign" in text
    assert "freshest live retain" in text
    assert "20260906t101559z-live-retained" in text
    assert "20260905t235830z-live-retained" in text
    assert "LIVE" in text
    assert "No keys" in text
    assert "standby-timeout-ac 0" in text
    assert "wsl --shutdown" in text
    assert "tens of GB" in text
    vps = VPS_RUNBOOK.read_text(encoding="utf-8")
    assert "1 through 604800 seconds" in vps
    assert "Do not start a multi-day DATA-1F retain now" in vps


def test_operator_scripts_are_executable_create_only_and_secret_free() -> None:
    sourced = SCRIPTS / "data1f_lib.sh"
    wrappers = (
        "data1f_status.sh",
        "data1f_start.sh",
        "data1f_stop.sh",
    )
    assert sourced.is_file()
    sourced_text = sourced.read_text(encoding="utf-8")
    assert "Never print secret values" in sourced_text
    assert "Never attach to, resume, or stop the DATA-1A tmux session hl-capture" in sourced_text
    assert "BEGIN PRIVATE KEY" not in sourced_text
    completed_lib = subprocess.run(
        ["bash", "-n", str(sourced)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed_lib.returncode == 0, completed_lib.stderr
    for name in wrappers:
        path = SCRIPTS / name
        assert path.is_file()
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR
        text = path.read_text(encoding="utf-8")
        assert "Create-only" in text or "create-only" in text
        assert "hl-capture" in text
        assert "BEGIN PRIVATE KEY" not in text
        completed = subprocess.run(
            ["bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_status_reports_tmux_and_parquet_part_count(tmp_path: Path) -> None:
    _write_run_tree(tmp_path, "20260904t000000z-live-retained", parts=3)
    completed = _run("data1f_status.sh", tmp_path)
    assert completed.returncode == 0, completed.stderr
    stdout = completed.stdout
    assert "tmux_session=bn-capture" in stdout
    assert "tmux_alive=" in stdout
    assert "hl_capture_tmux_alive=" in stdout
    assert "run_id=20260904t000000z-live-retained" in stdout
    assert "run_id_source=explicit" in stdout
    assert "claim_present=yes" in stdout
    assert "transport_reconnects=n/a" in stdout
    assert "health_present=no" in stdout
    assert "parquet_parts=3" in stdout
    assert "never_touch=hl-capture" in stdout
    assert SECRET_PROBE not in stdout
    assert SECRET_PROBE not in completed.stderr


def test_status_reads_health_status_without_payloads(tmp_path: Path) -> None:
    _write_run_tree(tmp_path, "20260904t000000z-live-retained", parts=1, health=True)
    completed = _run("data1f_status.sh", tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "health_present=yes" in completed.stdout
    assert "health_status=OPERATOR_STOP" in completed.stdout


def test_start_check_only_is_create_only(tmp_path: Path) -> None:
    artifact_root = tmp_path / "reconstructable"
    artifact_root.mkdir()
    completed = _run("data1f_start.sh", tmp_path, args=("--check-only",))
    assert completed.returncode == 0, completed.stderr
    assert "status=CHECK_ONLY" in completed.stdout
    assert "do_not_start_now=true" in completed.stdout
    assert "wait_for_hl_and_cos=true" in completed.stdout
    assert "run_id=20260904t000000z-live-retained" in completed.stdout
    assert "signing=false" in completed.stdout
    run_dir = artifact_root / "data-1f" / "binance" / "BTCUSDT" / "20260904t000000z-live-retained"
    assert not run_dir.exists()


def test_start_refuses_existing_run_directory(tmp_path: Path) -> None:
    _write_run_tree(tmp_path, "20260904t000000z-live-retained", parts=1)
    completed = _run("data1f_start.sh", tmp_path, args=("--check-only",))
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "already exists" in combined
    assert "create-only" in combined


def test_start_refuses_live_mode_without_starting(tmp_path: Path) -> None:
    completed = _run(
        "data1f_start.sh",
        tmp_path,
        args=("--check-only",),
        extra={"TRADING_MODE": "LIVE"},
    )
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "LIVE" in combined
    assert "PAPER" in combined


def test_start_refuses_protected_key_names_and_does_not_print_values(tmp_path: Path) -> None:
    completed = _run(
        "data1f_start.sh",
        tmp_path,
        args=("--check-only",),
        extra={"BINANCE_API_SECRET": SECRET_PROBE},
    )
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "BINANCE_API_SECRET" in combined
    assert "Value not printed" in combined
    assert SECRET_PROBE not in combined


def test_start_refuses_hl_capture_tmux_session(tmp_path: Path) -> None:
    completed = _run(
        "data1f_start.sh",
        tmp_path,
        args=("--check-only",),
        tmux_session="hl-capture",
    )
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "hl-capture" in combined
    assert "bn-capture" in combined


def test_start_refuses_duration_above_seven_days(tmp_path: Path) -> None:
    completed = _run(
        "data1f_start.sh",
        tmp_path,
        args=("--check-only",),
        duration="604800.1",
    )
    assert completed.returncode != 0
    assert "604800" in completed.stderr


def test_stop_does_not_resume_and_refuses_hl_capture(tmp_path: Path) -> None:
    completed = _run("data1f_stop.sh", tmp_path)
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "Never resume" in combined or "never resume" in combined
    assert "hl-capture" in combined
    assert SECRET_PROBE not in combined

    forbidden = _run("data1f_stop.sh", tmp_path, tmux_session="hl-capture")
    assert forbidden.returncode != 0
    forbidden_text = forbidden.stdout + forbidden.stderr
    assert "hl-capture" in forbidden_text
    assert "must not use" in forbidden_text or "Refuse" in forbidden_text


def test_status_prefers_live_binance_retain_over_stopped_older_run(tmp_path: Path) -> None:
    stopped_id = "20260905t235830z-live-retained"
    live_id = "20260906t101559z-live-retained"
    stopped = _write_run_tree(
        tmp_path,
        stopped_id,
        parts=4,
        health=True,
        health_status="OPERATOR_STOP",
    )
    _write_run_tree(tmp_path, live_id, parts=2)
    later = time.time() + 30
    for part in (stopped / "raw").glob("part-*.parquet"):
        os.utime(part, (later, later))
    completed = _run("data1f_status.sh", tmp_path, extra={"RUN_ID": ""})
    assert completed.returncode == 0, completed.stderr
    assert f"run_id={live_id}" in completed.stdout
    assert "run_id_source=auto-detect" in completed.stdout
    chosen = completed.stdout.split("run_id=", 1)[1].split("\n", 1)[0]
    assert chosen == live_id


def test_status_keeps_explicit_run_id_when_another_live_retain_exists(tmp_path: Path) -> None:
    stopped_id = "20260905t235830z-live-retained"
    live_id = "20260906t101559z-live-retained"
    _write_run_tree(tmp_path, stopped_id, parts=1, health=True, health_status="COMPLETED")
    _write_run_tree(tmp_path, live_id, parts=1)
    completed = _run("data1f_status.sh", tmp_path, extra={"RUN_ID": stopped_id})
    assert completed.returncode == 0, completed.stderr
    assert f"run_id={stopped_id}" in completed.stdout
    assert "run_id_source=explicit" in completed.stdout
    assert "health_status=COMPLETED" in completed.stdout


def test_status_prints_usdm_public_reconnect_hints_from_log(tmp_path: Path) -> None:
    live_id = "20260906t101559z-live-retained"
    _write_run_tree(
        tmp_path,
        live_id,
        parts=1,
        log_text=(
            "2026-09-06 10:16:00 INFO binance session_start transport_profile=spot\n"
            "2026-09-06 10:16:00 INFO binance session_start transport_profile=usdm_market\n"
            "2026-09-06 10:16:00 INFO binance session_start transport_profile=usdm_public\n"
            "2026-09-06 10:20:00 INFO binance reconnect transport_profile=usdm_public "
            "attempt=1\n"
            "2026-09-06 10:21:00 INFO binance reconnect transport_profile=usdm_public "
            "attempt=2\n"
        ),
    )
    completed = _run("data1f_status.sh", tmp_path, extra={"RUN_ID": ""})
    assert completed.returncode == 0, completed.stderr
    assert f"run_id={live_id}" in completed.stdout
    assert "transport_hints_source=capture-log" in completed.stdout
    assert "transport_reconnects=" in completed.stdout
    assert "usdm_public:2" in completed.stdout
    assert "spot:0" in completed.stdout
    assert "usdm_market:0" in completed.stdout
    assert "transport_gaps=n/a" in completed.stdout


def test_status_uses_health_transport_profiles_when_present(tmp_path: Path) -> None:
    run_id = "20260905t235830z-live-retained"
    _write_run_tree(
        tmp_path,
        run_id,
        parts=1,
        health=True,
        health_status="OPERATOR_STOP",
        health_extra={
            "transport_profiles": [
                {"transport_profile": "usdm_public", "reconnects": 3, "gaps": 3},
                {"transport_profile": "spot", "reconnects": 0, "gaps": 0},
            ]
        },
    )
    completed = _run("data1f_status.sh", tmp_path, run_id=run_id)
    assert completed.returncode == 0, completed.stderr
    assert "transport_hints_source=health" in completed.stdout
    assert "transport_reconnects=spot:0,usdm_public:3" in completed.stdout
    assert "transport_gaps=spot:0,usdm_public:3" in completed.stdout
