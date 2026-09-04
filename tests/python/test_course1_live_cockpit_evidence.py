"""Offline checks for the live COURSE-1 Cockpit JSON evidence fixture."""

from __future__ import annotations

import json
from pathlib import Path

from hyperliquid_bot.course1_cockpit_artifacts import project_cockpit_artifacts
from hyperliquid_bot.reconstructable_paths import (
    COURSE1_COCKPIT_FILE_NAMES,
    course1_cockpit_paths,
)

_FIXTURE_DIR = Path("tests/fixtures/course1_cockpit/live-public-soak")


def test_live_soak_cockpit_files_recompute_from_paper_and_stream() -> None:
    paper = json.loads((_FIXTURE_DIR / "paper.json").read_text(encoding="utf-8"))
    stream = json.loads((_FIXTURE_DIR / "public-stream.json").read_text(encoding="utf-8"))
    claim = json.loads((_FIXTURE_DIR / "run-claim.json").read_text(encoding="utf-8"))
    expected = project_cockpit_artifacts(paper=paper, stream=stream)
    for name in COURSE1_COCKPIT_FILE_NAMES:
        if name == "run-claim.json":
            continue
        stored = json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))
        assert stored == expected[name]
    assert claim["mode"] == "PAPER"
    assert claim["run_id"] == "20260904t001800z-live-paper"
    assert claim["seconds"] == 180
    assert claim["d22b_venue_authoritative_reconciliation"] is False
    execution = paper["execution"]
    assert execution["mode"] == "PAPER"
    assert execution["live"] is False
    assert execution["testnet"] is False
    assert execution["shadow"] is False
    assert execution["signing"] is False
    assert execution["venue_orders_submitted"] is False
    assert paper["cost_overlay"]["funding_payment_usdc"] == "0"
    assert expected["paper-pnl.json"]["assumed"] is True
    assert expected["paper-pnl.json"]["venue_pnl"] is False
    assert (
        expected["paper-pnl.json"]["net_pnl_usdc_assumed"]
        == paper["cost_overlay"]["net_pnl_usdc_assumed"]
    )
    assert expected["capture-health.json"]["status"] == "COMPLETED_FLAT"
    assert expected["orders.json"]["order_count"] == 2
    assert expected["fills.json"]["fill_count"] == 2
    paths = course1_cockpit_paths(
        Path("/workspace/var/reconstructable"),
        "20260904t001800z-live-paper",
    )
    assert paths.run_dir.name == "20260904t001800z-live-paper"
