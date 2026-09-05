"""Deterministic tests for the DATA-1A / COURSE-1 reconstructable path contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from hyperliquid_bot.reconstructable_paths import (
    COURSE1_COCKPIT_FILE_NAMES,
    COURSE1_PATH_CONTRACT,
    COURSE1_PATH_CONTRACT_ID,
    DATA1A_PATH_CONTRACT,
    DATA1A_PATH_CONTRACT_ID,
    DATA1B_PATH_CONTRACT,
    DATA1B_PATH_CONTRACT_ID,
    DATA1B_PRODUCT,
    DATA1B_RETIRED_PATH_CONTRACT_ID,
    DATA1B_RETIRED_PRODUCT,
    DATA1B_WIRE_PRODUCT,
    DATA1E_PATH_CONTRACT,
    DATA1E_PATH_CONTRACT_ID,
    DATA1F_PATH_CONTRACT,
    DATA1F_PATH_CONTRACT_ID,
    course1_cockpit_paths,
    data1a_run_paths,
    data1b_retired_eur_run_dir,
    data1b_run_paths,
    data1e_run_paths,
    data1f_run_paths,
    require_run_id,
)


def test_data1a_path_contract_is_stable() -> None:
    root = Path("/var/reconstructable")
    paths = data1a_run_paths(root, "sample-run")
    assert paths.contract_id == DATA1A_PATH_CONTRACT_ID
    assert paths.run_dir == root / "data-1a" / "hyperliquid" / "BTC-PERP" / "sample-run"
    assert paths.raw_dir == paths.run_dir / "raw"
    assert paths.database_path == paths.run_dir / "research.duckdb"
    assert paths.capture_claim_path == paths.run_dir / "capture-claim.json"
    assert paths.capture_health_path == paths.run_dir / "capture-health.json"
    assert paths.parquet_glob == "part-*.parquet"
    assert "<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/" in DATA1A_PATH_CONTRACT
    assert "raw/part-*.parquet" in DATA1A_PATH_CONTRACT
    assert "research.duckdb" in DATA1A_PATH_CONTRACT


def test_data1b_path_contract_is_stable() -> None:
    root = Path("/var/reconstructable")
    paths = data1b_run_paths(root, "sample-run")
    assert paths.contract_id == DATA1B_PATH_CONTRACT_ID
    assert DATA1B_PATH_CONTRACT_ID == "data-1b-kraken-btc-usd-v1"
    assert DATA1B_RETIRED_PATH_CONTRACT_ID == "data-1b-kraken-btc-eur-v1"
    assert DATA1B_PRODUCT == "BTC-USD"
    assert DATA1B_WIRE_PRODUCT == "BTC/USD"
    assert DATA1B_RETIRED_PRODUCT == "BTC-EUR"
    assert paths.run_dir == root / "data-1b" / "kraken" / "BTC-USD" / "sample-run"
    assert data1b_retired_eur_run_dir(root, "sample-run") == (
        root / "data-1b" / "kraken" / "BTC-EUR" / "sample-run"
    )
    assert data1b_retired_eur_run_dir(root, "sample-run") != paths.run_dir
    assert paths.raw_dir == paths.run_dir / "raw"
    assert paths.database_path == paths.run_dir / "research.duckdb"
    assert paths.capture_claim_path == paths.run_dir / "capture-claim.json"
    assert paths.capture_health_path == paths.run_dir / "capture-health.json"
    assert paths.parquet_glob == "part-*.parquet"
    assert "<artifact-root>/data-1b/kraken/BTC-USD/<run_id>/" in DATA1B_PATH_CONTRACT
    assert "raw/part-*.parquet" in DATA1B_PATH_CONTRACT
    assert "research.duckdb" in DATA1B_PATH_CONTRACT


def test_data1e_path_contract_is_stable() -> None:
    root = Path("/var/reconstructable")
    paths = data1e_run_paths(root, "sample-run")
    assert paths.contract_id == DATA1E_PATH_CONTRACT_ID
    assert paths.run_dir == root / "data-1e" / "bitvavo" / "BTC-EUR" / "sample-run"
    assert paths.raw_dir == paths.run_dir / "raw"
    assert paths.database_path == paths.run_dir / "research.duckdb"
    assert paths.capture_claim_path == paths.run_dir / "capture-claim.json"
    assert paths.capture_health_path == paths.run_dir / "capture-health.json"
    assert paths.parquet_glob == "part-*.parquet"
    assert "<artifact-root>/data-1e/bitvavo/BTC-EUR/<run_id>/" in DATA1E_PATH_CONTRACT
    assert "raw/part-*.parquet" in DATA1E_PATH_CONTRACT
    assert "research.duckdb" in DATA1E_PATH_CONTRACT


def test_data1f_path_contract_is_stable() -> None:
    root = Path("/var/reconstructable")
    paths = data1f_run_paths(root, "sample-run")
    assert paths.contract_id == DATA1F_PATH_CONTRACT_ID
    assert paths.run_dir == root / "data-1f" / "binance" / "BTCUSDT" / "sample-run"
    assert paths.raw_dir == paths.run_dir / "raw"
    assert paths.database_path == paths.run_dir / "research.duckdb"
    assert paths.capture_claim_path == paths.run_dir / "capture-claim.json"
    assert paths.capture_health_path == paths.run_dir / "capture-health.json"
    assert paths.parquet_glob == "part-*.parquet"
    assert "<artifact-root>/data-1f/binance/BTCUSDT/<run_id>/" in DATA1F_PATH_CONTRACT
    assert "raw/part-*.parquet" in DATA1F_PATH_CONTRACT
    assert "research.duckdb" in DATA1F_PATH_CONTRACT


def test_course1_cockpit_path_contract_is_stable() -> None:
    root = Path("/var/reconstructable")
    paths = course1_cockpit_paths(root, "sample-run")
    assert paths.contract_id == COURSE1_PATH_CONTRACT_ID
    assert paths.run_dir == root / "course1" / "live-public-paper" / "sample-run"
    assert paths.run_claim_path.name == "run-claim.json"
    assert paths.paper_position_path.name == "paper-position.json"
    assert paths.paper_pnl_path.name == "paper-pnl.json"
    assert paths.orders_path.name == "orders.json"
    assert paths.fills_path.name == "fills.json"
    assert paths.capture_health_path.name == "capture-health.json"
    assert COURSE1_COCKPIT_FILE_NAMES == (
        "run-claim.json",
        "paper-position.json",
        "paper-pnl.json",
        "orders.json",
        "fills.json",
        "capture-health.json",
    )
    assert "<artifact-root>/course1/live-public-paper/<run_id>/" in COURSE1_PATH_CONTRACT


@pytest.mark.parametrize("run_id", ["", "SAMPLE", "has space", "a" * 65, "Upper_Case"])
def test_run_id_is_fail_closed(run_id: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        require_run_id(run_id)
    with pytest.raises((TypeError, ValueError)):
        data1a_run_paths(Path("/tmp"), run_id)
    with pytest.raises((TypeError, ValueError)):
        data1b_run_paths(Path("/tmp"), run_id)
    with pytest.raises((TypeError, ValueError)):
        data1e_run_paths(Path("/tmp"), run_id)
    with pytest.raises((TypeError, ValueError)):
        data1f_run_paths(Path("/tmp"), run_id)


def test_path_helpers_do_not_create_directories(tmp_path: Path) -> None:
    paths = data1a_run_paths(tmp_path / "missing-root", "sample-run")
    assert not paths.run_dir.exists()
    assert not paths.raw_dir.exists()
    binance_paths = data1f_run_paths(tmp_path / "missing-root", "sample-run")
    assert not binance_paths.run_dir.exists()
    assert not binance_paths.raw_dir.exists()
    kraken_paths = data1b_run_paths(tmp_path / "missing-root", "sample-run")
    assert not kraken_paths.run_dir.exists()
    bitvavo_paths = data1e_run_paths(tmp_path / "missing-root", "sample-run")
    assert not bitvavo_paths.run_dir.exists()
