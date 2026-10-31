"""Operator selection versions preregistration identity without retuning the study."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from research.bar_tables.panel_io import load_exclusions, load_panel_manifest
from research.harness.spec import load_document, spec_sha256, validate_spec

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/experiments"
AMENDMENT = DOCS / "exp_xs_usdm.operator-inputs.json"
SCOPE = ROOT / "config/research/operator-universe-2026-10-31"
GROUP = "bn-um-usdt-1d-operator-2026-10-31"
PROTECTED = {
    "exp_xsmom_usdm": "908155ee02f9af0e9d8a4a2fade4644e8b1b13f96c320db2eb8cd6cd7c56ff0d",
    "exp_xscarry_usdm": "7b9c2727ba6f00ab49df84cf3e0db3e1a0dc97a87914ab91cad3474ea74b98f0",
}


@pytest.mark.parametrize("name", PROTECTED)
def test_all_preexisting_nonidentity_spec_fields_remain_frozen(name: str) -> None:
    document = load_document(DOCS / f"{name}.spec.yaml")
    validate_spec(document)
    protected = {
        key: value
        for key, value in document.items()
        if key not in {"hypothesis_id", "universe", "dataset_version"}
    }
    digest = hashlib.sha256(
        json.dumps(protected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    record = json.loads(AMENDMENT.read_text())["spec_versions"][name]
    assert digest == record["unchanged_contract_sha256"] == PROTECTED[name]
    assert spec_sha256(document) == record["selected_spec_sha256"]
    assert document["hypothesis_id"] == record["selected_hypothesis_id"]
    hypothesis_id = document["hypothesis_id"]
    universe = document["universe"]
    dataset_version = document["dataset_version"]
    assert isinstance(hypothesis_id, str) and "ex-icp" in hypothesis_id
    assert isinstance(universe, str) and "ICPUSDT" in universe
    assert isinstance(dataset_version, str) and "native1d-repair297-v1" in dataset_version
    assert record["previous_spec_sha256"] != record["selected_spec_sha256"]
    assert record["selected_spec_sha256"] in (DOCS / f"{name}.md").read_text()


def test_selected_noncrypto_changes_pointer_only_and_loads_real_selected_manifest() -> None:
    old = json.loads(
        (ROOT / "config/research/binance-um-usdt-non-crypto-2026-10-08.json").read_text()
    )
    path = ROOT / "config/research/binance-um-usdt-non-crypto-operator-2026-10-31.json"
    new = json.loads(path.read_text())
    assert new == {**old, "universe": "binance-um-usdt-1d-operator-2026-10-31.json"}
    amendment = json.loads(AMENDMENT.read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == amendment["selected_noncrypto_sha256"]
    manifest_path = SCOPE / "datasets.toml"
    manifest = load_panel_manifest(manifest_path, GROUP)
    exclusions = load_exclusions(path, manifest, manifest_path, GROUP)
    assert len(exclusions.symbols) == 221
    assert "ICPUSDT" not in exclusions.symbols
    assert all(spec.symbol != "ICPUSDT" for spec in manifest.binance)


def test_selected_gap_identity_preserves_full_ledger_and_filters_only_icp() -> None:
    old = json.loads((DOCS / "exp_xs_usdm.coverage.json").read_text())
    new = json.loads(AMENDMENT.read_text())
    selected = new["selected_missing_days"]
    assert selected == [row for row in old["interior_missing_days"] if row["symbol"] != "ICPUSDT"]
    assert len(old["interior_missing_days"]) == 323
    assert len(selected) == 297
    assert len({row["symbol"] for row in selected}) == 51
    assert len({(row["symbol"], row["date"][:7]) for row in selected}) == 102
    assert (
        hashlib.sha256(
            json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == new["selected_pairs_sha256"]
    )
    assert new["recovery_executed"] is False
    assert new["panel_or_scoring_results"] is False
