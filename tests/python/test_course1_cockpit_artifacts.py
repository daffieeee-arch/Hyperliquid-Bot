"""Offline tests for create-only COURSE-1 Cockpit PAPER projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from hyperliquid_bot.course1_cockpit_artifacts import (
    capture_health_artifact,
    paper_pnl_artifact,
    paper_position_artifact,
    project_cockpit_artifacts,
)
from hyperliquid_bot.reconstructable_paths import COURSE1_PATH_CONTRACT_ID

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "course1_cockpit" / "sample-run"


def _paper(*, pnl: str = "-0.012661896", position: str = "0.00000") -> dict[str, object]:
    return {
        "execution": {
            "mode": "PAPER",
            "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
            "venue_orders_submitted": False,
        },
        "business": {
            "final_position_quantity": position,
            "order_intents": [
                {"client_order_id": "O-TEST-1", "side": "BUY", "quantity": "0.00013"}
            ],
            "fills": [
                {"fill_ordinal": 1, "side": "BUY", "quantity": "0.00013", "price": "78047.0"}
            ],
        },
        "cost_overlay": {
            "starting_cash_usdc_assumed": "100",
            "ending_cash_usdc_assumed": "99.987338104",
            "ending_equity_usdc_assumed": "99.987338104",
            "net_pnl_usdc_assumed": pnl,
            "fee_cost_usdc": "0.004",
            "half_spread_cost_usdc": "0.004",
            "slippage_cost_usdc": "0.004",
            "funding_payment_usdc": "0",
            "mark_price": "78047.0",
            "final_position_btc": position,
        },
    }


def _stream() -> dict[str, object]:
    return {
        "status": "BOUNDED_TIMEOUT",
        "feed": "hyperliquid-public-btc-perp-trades-bbo",
        "credentialless": True,
        "trade_count": 20,
        "bbo_count": 33,
        "adapter_rejected_count": 24,
        "risk_rejections": 0,
        "subscriptions_acknowledged": ["trades", "bbo"],
    }


def test_projections_copy_assumed_overlay_and_refuse_fake_pnl() -> None:
    paper = _paper()
    artifacts = project_cockpit_artifacts(paper=paper, stream=_stream())
    assert artifacts["paper-pnl.json"]["net_pnl_usdc_assumed"] == "-0.012661896"
    assert artifacts["paper-pnl.json"]["assumed"] is True
    assert artifacts["paper-pnl.json"]["venue_pnl"] is False
    assert artifacts["paper-pnl.json"]["funding_payment_usdc"] == "0"
    assert artifacts["paper-position.json"]["final_position_btc"] == "0.00000"
    assert artifacts["orders.json"]["order_count"] == 1
    assert artifacts["fills.json"]["fill_count"] == 1
    assert artifacts["capture-health.json"]["twenty_four_seven"] is False
    assert artifacts["capture-health.json"]["path_contract"] == COURSE1_PATH_CONTRACT_ID


def test_empty_orders_and_fills_are_honest() -> None:
    paper = _paper()
    business = cast(dict[str, object], paper["business"])
    business["order_intents"] = []
    business["fills"] = []
    artifacts = project_cockpit_artifacts(paper=paper, stream=_stream())
    assert artifacts["orders.json"]["orders"] == []
    assert artifacts["fills.json"]["fills"] == []


def test_missing_overlay_or_non_paper_mode_fails_closed() -> None:
    paper = _paper()
    del cast(dict[str, object], paper["cost_overlay"])["net_pnl_usdc_assumed"]
    with pytest.raises(ValueError, match="net_pnl_usdc_assumed"):
        paper_pnl_artifact(paper)
    live = _paper()
    cast(dict[str, object], live["execution"])["mode"] = "LIVE"
    with pytest.raises(ValueError, match="PAPER"):
        paper_position_artifact(live)
    funded = _paper()
    cast(dict[str, object], funded["cost_overlay"])["funding_payment_usdc"] = "1.25"
    with pytest.raises(ValueError, match="funding"):
        paper_pnl_artifact(funded)
    with pytest.raises(ValueError, match="outside the COURSE-1 soak bound"):
        capture_health_artifact({**_stream(), "status": "ALWAYS_ON"})


def test_committed_sample_fixture_matches_the_path_contract() -> None:
    paper = json.loads((_FIXTURE_DIR / "paper.json").read_text(encoding="utf-8"))
    stream = json.loads((_FIXTURE_DIR / "public-stream.json").read_text(encoding="utf-8"))
    expected = project_cockpit_artifacts(paper=paper, stream=stream)
    for name, payload in expected.items():
        stored = json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))
        assert stored == payload
    claim = json.loads((_FIXTURE_DIR / "run-claim.json").read_text(encoding="utf-8"))
    assert claim["mode"] == "PAPER"
    assert claim["twenty_four_seven"] is False
