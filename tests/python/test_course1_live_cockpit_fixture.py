"""Offline checks for the live COURSE-1 Cockpit JSON fixture the first screen reads."""

from __future__ import annotations

import json
from pathlib import Path

from hyperliquid_bot.reconstructable_paths import (
    COURSE1_COCKPIT_FILE_NAMES,
    course1_cockpit_paths,
)

_FIXTURE_DIR = Path("tests/fixtures/course1_cockpit/live-public-soak")


def test_live_soak_fixture_is_paper_assumed_overlay() -> None:
    claim = json.loads((_FIXTURE_DIR / "run-claim.json").read_text(encoding="utf-8"))
    position = json.loads((_FIXTURE_DIR / "paper-position.json").read_text(encoding="utf-8"))
    pnl = json.loads((_FIXTURE_DIR / "paper-pnl.json").read_text(encoding="utf-8"))
    health = json.loads((_FIXTURE_DIR / "capture-health.json").read_text(encoding="utf-8"))
    orders = json.loads((_FIXTURE_DIR / "orders.json").read_text(encoding="utf-8"))
    fills = json.loads((_FIXTURE_DIR / "fills.json").read_text(encoding="utf-8"))

    for name in COURSE1_COCKPIT_FILE_NAMES:
        assert (_FIXTURE_DIR / name).is_file()

    assert claim["mode"] == "PAPER"
    assert claim["run_id"] == "20260904t001800z-live-paper"
    assert claim["d22b_venue_authoritative_reconciliation"] is False
    assert position["mode"] == "PAPER"
    assert position["final_position_btc"] == "0.00000"
    assert position["venue_authoritative"] is False
    assert position["twenty_four_seven"] is False
    assert pnl["assumed"] is True
    assert pnl["venue_pnl"] is False
    assert pnl["net_pnl_usdc_assumed"] == "-0.0126583080"
    assert pnl["funding_payment_usdc"] == "0"
    assert health["status"] == "COMPLETED_FLAT"
    assert health["twenty_four_seven"] is False
    assert health["trade_count"] == 20
    assert orders["venue_orders_submitted"] is False
    assert fills["fill_count"] == 2

    paths = course1_cockpit_paths(
        Path("/home/chupa/Hyperliquid Project/data-capture"),
        "20260904t001800z-live-paper",
    )
    assert paths.run_dir.name == "20260904t001800z-live-paper"
    assert paths.paper_pnl_path.name == "paper-pnl.json"
