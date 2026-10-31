"""Published operator scope stays traceable and is consumed by existing planners."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from pytest import MonkeyPatch

from research.bar_tables import panel_io
from research.bar_tables.panel import PanelSpec
from research.hist_etl.manifest import default_manifest_path, load_manifest, select_binance
from research.hist_etl.universe import load_universe

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/hist_etl/universe/binance-um-usdt-1d-2026-10-08.json"
SOURCE_SHA256 = "add8d0f26d6b7c2bdf18963cf1f77752f0b1aa8a943a64c868e097951f4ec520"
DIRECTORY = ROOT / "config/research/operator-universe-2026-10-31"
MANIFEST = DIRECTORY / "datasets.toml"
SELECTED = DIRECTORY / "universe/binance-um-usdt-1d-operator-2026-10-31.json"
GROUP = "bn-um-usdt-1d-operator-2026-10-31"


def test_policy_binds_exact_frozen_inputs_and_operator_decision() -> None:
    policy = json.loads((DIRECTORY / "policy.json").read_text())
    assert policy["format"] == 1
    assert policy["decision_date"] == "2026-10-31"
    assert policy["excluded_symbols"] == {"ICPUSDT": "operator_trading_preference"}
    assert policy["source_universe"] == {
        "path": str(SOURCE.relative_to(ROOT)),
        "sha256": SOURCE_SHA256,
    }
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA256
    assert DIRECTORY / policy["selected_universe"]["path"] == SELECTED
    assert (
        policy["selected_universe"]["sha256"] == hashlib.sha256(SELECTED.read_bytes()).hexdigest()
    )
    assert policy["selected_universe"]["member_count"] == 895
    assert policy["manifest"] == {
        "path": "datasets.toml",
        "group": GROUP,
        "sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
    }
    observations = policy["observations_before_decision"]
    assert observations["icp_archive_availability_gaps_observed"] is True
    assert observations["s4_strategy_scoring_in_this_continuation"] is False


def test_selected_snapshot_changes_only_operator_selected_membership() -> None:
    source = json.loads(SOURCE.read_text())
    selected = json.loads(SELECTED.read_text())
    assert len(source["symbols"]) == 896
    assert selected["symbols"] == {
        symbol: runs for symbol, runs in source["symbols"].items() if symbol != "ICPUSDT"
    }
    assert {key: value for key, value in selected.items() if key != "symbols"} == {
        key: value for key, value in source.items() if key != "symbols"
    }
    assert len(selected["excluded"]) == 5
    assert "ICPUSDT" not in selected["excluded"]
    assert len(load_universe(SELECTED).symbols) == 895


def test_existing_loader_requires_opt_in_and_expands_both_scoped_datasets() -> None:
    manifest = load_manifest(MANIFEST)
    assert manifest.binance_groups == (GROUP,)
    assert dict(manifest.binance_universe_files) == {
        GROUP: "universe/binance-um-usdt-1d-operator-2026-10-31.json"
    }
    assert select_binance(manifest, None) == ()
    selected = select_binance(manifest, (GROUP,))
    assert selected and all(not spec.enabled and spec.group == GROUP for spec in selected)
    assert {spec.dataset for spec in selected} == {"klines", "fundingRate"}
    assert "ICPUSDT" not in {spec.symbol for spec in selected}
    assert len({spec.symbol for spec in selected}) == 895
    assert len({spec.symbol for spec in selected if spec.dataset == "klines"}) == 894
    assert len({spec.symbol for spec in selected if spec.dataset == "fundingRate"}) == 895
    assert manifest.kraken == ()
    assert manifest.hyperliquid == ()


def test_original_default_manifest_retains_its_full_source_scope(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("HIST_ETL_MANIFEST", raising=False)
    original = ROOT / "config/hist_etl/datasets.toml"
    assert default_manifest_path() == original
    manifest = load_manifest(original)
    assert GROUP not in manifest.binance_groups
    assert "ICPUSDT" in {spec.symbol for spec in select_binance(manifest, ("bn-um-usdt-1d",))}


def test_panel_input_coverage_selection_never_checks_icp(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    checked: list[Path] = []

    def completed(path: Path) -> bool:
        checked.append(path)
        return True

    # Only completion is simulated; the actual manifest, warm-up, run and
    # following-funding-month planning logic builds the required file set.
    monkeypatch.setattr(panel_io, "_synced", completed)
    root = tmp_path / "no-archive-is-opened"
    spec = PanelSpec((14,), 30, 30, 7)
    selected = panel_io.universe_files(
        root, load_manifest(MANIFEST), GROUP, date(2020, 1, 1), date(2026, 9, 1), spec
    )
    assert checked and all(not path.name.startswith("ICPUSDT-") for path in checked)
    selected_checks = set(checked)
    original = panel_io.universe_files(
        root,
        load_manifest(ROOT / "config/hist_etl/datasets.toml"),
        "bn-um-usdt-1d",
        date(2020, 1, 1),
        date(2026, 9, 1),
        spec,
    )
    assert selected.klines == tuple(
        path for path in original.klines if not path.name.startswith("ICPUSDT-")
    )
    assert selected.funding == tuple(
        path for path in original.funding if not path.name.startswith("ICPUSDT-")
    )
    assert selected.kline_runs == tuple(
        run for run in original.kline_runs if run.symbol != "ICPUSDT"
    )
    assert selected.funding_runs == tuple(
        run for run in original.funding_runs if run.symbol != "ICPUSDT"
    )
    assert selected_checks < set(checked)
    assert all(path.name.startswith("ICPUSDT-") for path in set(checked) - selected_checks)
    assert not root.exists()
