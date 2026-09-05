"""Offline tests for the DATA-1B operator PC/WSL runbook and create-only helpers."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
WSL_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "data1b-wsl-pc-retained-capture.md"
VPS_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "data1b-vps-retained-capture.md"
SECRET_PROBE = "super-secret-value-must-never-be-printed"


def _script_env(
    tmp_path: Path,
    *,
    extra: Mapping[str, str] | None = None,
    run_id: str = "20260904t000000z-live-retained",
    duration: str = "259200",
    tmux_session: str = "kr-capture",
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
        "BITVAVO_API_KEY",
        "BITVAVO_API_SECRET",
        "KRAKEN_API_KEY",
        "KRAKEN_API_SECRET",
        "KRAKEN_PRIVATE_KEY",
        "KRAKEN_WS_API_KEY",
        "KRAKEN_WS_API_SECRET",
        "OKX_API_KEY",
        "OKX_SECRET_KEY",
        "OKX_PASSPHRASE",
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
    tmux_session: str = "kr-capture",
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


def _write_run_tree(tmp_path: Path, run_id: str, *, parts: int = 2, health: bool = False) -> Path:
    run_dir = tmp_path / "reconstructable" / "data-1b" / "kraken" / "BTC-USD" / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "capture-claim.json").write_text(
        json.dumps({"run_id": run_id, "retained": True, "signing": False}),
        encoding="utf-8",
    )
    if health:
        (run_dir / "capture-health.json").write_text(
            json.dumps({"status": "OPERATOR_STOP", "run_id": run_id}),
            encoding="utf-8",
        )
    for index in range(parts):
        (raw_dir / f"part-{index:05d}.parquet").write_bytes(b"not-a-real-parquet")
    (raw_dir / ".partial-ignored.parquet").write_bytes(b"hidden")
    return run_dir


def test_wsl_runbook_documents_known_good_operator_paths() -> None:
    text = WSL_RUNBOOK.read_text(encoding="utf-8")
    assert "~/code/Hyperliquid-Bot-main" in text
    assert "~/hyperliquid-artifacts/reconstructable" in text
    assert "data-1b/kraken/BTC-USD/<run_id>/" in text
    assert "BTC/USD" in text
    assert "EUR microstructure" in text
    assert "no silent EUR fallback" in text.lower() or "not a silent EUR fallback" in text
    assert "kr-capture" in text
    assert "hl-capture" in text
    assert "bn-capture" in text
    assert "bv-capture" in text
    assert "259200" in text
    assert "tmux send-keys -t kr-capture C-c" in text
    assert "never resume" in text.lower() or "Never resume" in text
    assert "python -m hyperliquid_bot.kraken_l3_research" in text
    assert "KRAKEN_WS_API_KEY" in text
    assert "KRAKEN_WS_API_SECRET" in text
    assert "Cloud Agents are **unsuitable**" in text
    assert "must not SSH" in text
    assert "Do not start a multi-day DATA-1B retain now" in text
    assert "LIVE" in text
    assert "standby-timeout-ac 0" in text
    assert "wsl --shutdown" in text
    assert "--l2-depth" in text
    assert "--l3-depth" in text
    assert "depth 100" in text
    assert "best 10" in text
    assert "CRC32" in text or "CRC" in text
    assert "disk" in text.lower()
    assert "bandwidth" in text.lower()
    assert "elapsed_seconds" in text
    assert "capture-<run_id>.log" in text
    assert "Protect a 72h evidence window" in text
    assert "Do **not** send `C-c`" in text
    vps = VPS_RUNBOOK.read_text(encoding="utf-8")
    assert "1 through 604800 seconds" in vps
    assert "Do not start a multi-day DATA-1B retain now" in vps
    assert "depth 100" in vps
    assert "best 10" in vps
    assert "CRC32" in vps or "CRC" in vps
    assert "disk" in vps.lower()
    assert "bandwidth" in vps.lower()
    data_md = (REPO_ROOT / "docs" / "DATA.md").read_text(encoding="utf-8")
    assert "depth-100" in data_md or "depth 100" in data_md
    assert "CRC32 always covers the best ten price levels" in data_md
    assert "even at subscribed depth 100" in data_md
    assert "Do not start a multi-day DATA-1B retain" in data_md


def test_operator_scripts_are_executable_create_only_and_secret_free() -> None:
    sourced = SCRIPTS / "data1b_lib.sh"
    wrappers = (
        "data1b_status.sh",
        "data1b_start.sh",
        "data1b_stop.sh",
    )
    assert sourced.is_file()
    sourced_text = sourced.read_text(encoding="utf-8")
    assert "Never print secret values" in sourced_text
    assert "Never attach to, resume, or stop hl-capture, bn-capture, or bv-capture" in sourced_text
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
    completed = _run("data1b_status.sh", tmp_path)
    assert completed.returncode == 0, completed.stderr
    stdout = completed.stdout
    assert "tmux_session=kr-capture" in stdout
    assert "tmux_alive=" in stdout
    assert "hl_capture_tmux_alive=" in stdout
    assert "bn_capture_tmux_alive=" in stdout
    assert "bv_capture_tmux_alive=" in stdout
    assert "run_id=20260904t000000z-live-retained" in stdout
    assert "claim_present=yes" in stdout
    assert "health_present=no" in stdout
    assert "parquet_parts=3" in stdout
    assert "never_touch=hl-capture,bn-capture,bv-capture" in stdout
    assert SECRET_PROBE not in stdout
    assert SECRET_PROBE not in completed.stderr


def test_status_reads_health_status_without_payloads(tmp_path: Path) -> None:
    _write_run_tree(tmp_path, "20260904t000000z-live-retained", parts=1, health=True)
    completed = _run("data1b_status.sh", tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "health_present=yes" in completed.stdout
    assert "health_status=OPERATOR_STOP" in completed.stdout


def test_start_check_only_is_create_only(tmp_path: Path) -> None:
    artifact_root = tmp_path / "reconstructable"
    artifact_root.mkdir()
    completed = _run("data1b_start.sh", tmp_path, args=("--check-only",))
    assert completed.returncode == 0, completed.stderr
    assert "status=CHECK_ONLY" in completed.stdout
    assert "do_not_start_now=true" in completed.stdout
    assert "wait_for_cos=true" in completed.stdout
    assert "l3_keys_present=no" in completed.stdout
    assert "credentialless=true" in completed.stdout
    assert "run_id=20260904t000000z-live-retained" in completed.stdout
    assert "signing=false" in completed.stdout
    assert "l2_depth=100" in completed.stdout
    assert "l3_depth=100" in completed.stdout
    assert "checksum_price_levels=10" in completed.stdout
    assert "path_contract=data-1b-kraken-btc-usd-v1" in completed.stdout
    assert "product=BTC-USD" in completed.stdout
    assert "wire_product=BTC/USD" in completed.stdout
    assert "retired_eur_fallback=false" in completed.stdout
    run_dir = artifact_root / "data-1b" / "kraken" / "BTC-USD" / "20260904t000000z-live-retained"
    assert not run_dir.exists()


def test_start_refuses_retired_eur_path_as_current_contract(tmp_path: Path) -> None:
    run_id = "20260904t000000z-live-retained"
    retired = tmp_path / "reconstructable" / "data-1b" / "kraken" / "BTC-EUR" / run_id
    retired.mkdir(parents=True)
    (retired / "capture-claim.json").write_text("{}", encoding="utf-8")
    completed = _run("data1b_start.sh", tmp_path, args=("--check-only",))
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "retired" in combined.lower()
    assert "BTC-EUR" in combined
    assert "no silent EUR fallback" in combined
    usd = tmp_path / "reconstructable" / "data-1b" / "kraken" / "BTC-USD" / run_id
    assert not usd.exists()


def test_status_does_not_treat_retired_eur_runs_as_current(tmp_path: Path) -> None:
    retired = tmp_path / "reconstructable" / "data-1b" / "kraken" / "BTC-EUR" / "old-eur-run"
    retired.mkdir(parents=True)
    completed = _run("data1b_status.sh", tmp_path, run_id="")
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "BTC-USD" in combined
    assert "old-eur-run" not in combined


def test_start_refuses_existing_run_directory(tmp_path: Path) -> None:
    _write_run_tree(tmp_path, "20260904t000000z-live-retained", parts=1)
    completed = _run("data1b_start.sh", tmp_path, args=("--check-only",))
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "already exists" in combined
    assert "create-only" in combined


def test_start_refuses_live_mode_without_starting(tmp_path: Path) -> None:
    completed = _run(
        "data1b_start.sh",
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
        "data1b_start.sh",
        tmp_path,
        args=("--check-only",),
        extra={"KRAKEN_API_SECRET": SECRET_PROBE},
    )
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "KRAKEN_API_SECRET" in combined
    assert "Value not printed" in combined
    assert SECRET_PROBE not in combined


def test_start_refuses_protected_tmux_sessions(tmp_path: Path) -> None:
    for session in ("hl-capture", "bn-capture", "bv-capture"):
        completed = _run(
            "data1b_start.sh",
            tmp_path,
            args=("--check-only",),
            tmux_session=session,
        )
        assert completed.returncode != 0
        combined = completed.stdout + completed.stderr
        assert session in combined
        assert "kr-capture" in combined


def test_start_refuses_duration_above_seven_days(tmp_path: Path) -> None:
    completed = _run(
        "data1b_start.sh",
        tmp_path,
        args=("--check-only",),
        duration="604800.1",
    )
    assert completed.returncode != 0
    assert "604800" in completed.stderr


def test_stop_does_not_resume_and_refuses_protected_sessions(tmp_path: Path) -> None:
    completed = _run("data1b_stop.sh", tmp_path)
    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "Never resume" in combined or "never resume" in combined
    assert "hl-capture" in combined
    assert SECRET_PROBE not in combined

    forbidden = _run("data1b_stop.sh", tmp_path, tmux_session="bv-capture")
    assert forbidden.returncode != 0
    forbidden_text = forbidden.stdout + forbidden.stderr
    assert "bv-capture" in forbidden_text
    assert "must not use" in forbidden_text or "Refuse" in forbidden_text
