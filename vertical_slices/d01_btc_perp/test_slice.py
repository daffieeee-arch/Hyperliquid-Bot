"""Focused tests for the bounded D01 local vertical slice."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import patch

from fit_gates.d41_nautilus.fit_gate import (
    envelope_to_trade_tick,
    existing_btc_contract,
    sha256_json,
)
from nautilus_trader.model.enums import OrderSide

from hyperliquid_bot.contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    AggressorSide,
    MarketEventEnvelope,
    TradeEvent,
)
from hyperliquid_bot.local_mode import UnsafeTradingModeError
from vertical_slices.d01_btc_perp.slice import (
    DEFAULT_CONFIG,
    DEFAULT_DATASET,
    DEFAULT_DATASET_SHA256,
    DEFAULT_EVENTS_SHA256,
    RiskRejectedError,
    assert_local_boundary,
    evaluate_order_risk,
    load_bounded_dataset,
    probe_completed_run,
    run_identity,
    run_slice,
    source_identity,
)


def envelope(
    *,
    price: str = "78047.0",
    quantity: str = "0.00013",
    gap: bool = False,
    delay: timedelta = timedelta(milliseconds=100),
) -> MarketEventEnvelope:
    event_time = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    return MarketEventEnvelope(
        schema_version=MARKET_EVENT_SCHEMA_VERSION,
        instrument=existing_btc_contract(),
        event=TradeEvent(
            price=Decimal(price),
            quantity=Decimal(quantity),
            aggressor_side=AggressorSide.BUY,
        ),
        event_time=event_time,
        received_time=event_time + delay,
        received_monotonic_ns=1_000_000_000,
        collector_version="d01-test",
        collector_commit="cf50a77d99893f1f90cd8707ff41e0ce8c8211d9",
        is_gap=gap,
        source_event_id='["hyperliquid-trade-v1",1,"BTC",2]',
        source_transaction_id="0xpublic",
    )


class D01BoundaryTests(unittest.TestCase):
    def test_default_dataset_is_exact_and_reenters_current_v2_adapter(self) -> None:
        dataset = load_bounded_dataset()
        self.assertEqual(dataset.path, DEFAULT_DATASET)
        self.assertEqual(dataset.file_sha256, DEFAULT_DATASET_SHA256)
        self.assertEqual(dataset.events_sha256, DEFAULT_EVENTS_SHA256)
        self.assertEqual(len(dataset.ticks), 27)
        self.assertEqual(str(dataset.max_price), "78047.0")
        self.assertLessEqual(dataset.max_age_ns, 5_000_000_000)

    def test_adapter_fails_closed_for_gap_stale_future_and_precision(self) -> None:
        envelope_to_trade_tick(envelope(delay=timedelta(seconds=5)))
        with self.assertRaisesRegex(ValueError, "gap-tainted"):
            envelope_to_trade_tick(envelope(gap=True))
        with self.assertRaisesRegex(ValueError, "stale"):
            envelope_to_trade_tick(envelope(delay=timedelta(seconds=5, microseconds=1)))
        with self.assertRaisesRegex(ValueError, "future-dated"):
            envelope_to_trade_tick(envelope(delay=timedelta(microseconds=-1)))
        with self.assertRaisesRegex(ValueError, "price.*increment"):
            envelope_to_trade_tick(envelope(price="78047.01"))
        with self.assertRaisesRegex(ValueError, "quantity.*increment"):
            envelope_to_trade_tick(envelope(quantity="0.000131"))

    def test_dataset_loader_rejects_gap_even_if_event_digest_is_rewritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="d01-gap-") as temporary:
            copied = Path(temporary) / "dataset.json"
            value = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
            value["events"][0]["is_gap"] = True
            value["events_sha256"] = sha256_json(value["events"])
            copied.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "gap-tainted"):
                load_bounded_dataset(copied, expected_file_sha256=None)

    def test_mode_and_credentials_fail_before_composition(self) -> None:
        assert_local_boundary(environ={})
        assert_local_boundary(environ={"TRADING_MODE": "PAPER"})
        with self.assertRaises(UnsafeTradingModeError):
            assert_local_boundary(environ={"TRADING_MODE": "LIVE"})
        with self.assertRaisesRegex(RuntimeError, "non-PAPER internal"):
            assert_local_boundary(environ={"D41_EXECUTION_MODE": "TESTNET"})
        with self.assertRaisesRegex(RuntimeError, "credential environment names"):
            assert_local_boundary(environ={"HYPERLIQUID_PK": "not-read"})


class D01RiskTests(unittest.TestCase):
    def test_entry_and_exact_flattening_exit_are_approved(self) -> None:
        entry = evaluate_order_risk(
            side=OrderSide.BUY,
            quantity=Decimal("0.00013"),
            price=Decimal("78047.0"),
            reduce_only=False,
            current_position=Decimal(0),
            config=DEFAULT_CONFIG,
        )
        exit_decision = evaluate_order_risk(
            side=OrderSide.SELL,
            quantity=Decimal("0.00013"),
            price=Decimal("78047.0"),
            reduce_only=True,
            current_position=Decimal("0.00013"),
            config=DEFAULT_CONFIG,
        )
        self.assertTrue(entry["approved"])
        self.assertTrue(exit_decision["approved"])
        self.assertEqual(entry["assumed_stop_plus_round_trip_cost_loss_usdc"], "0.215097532")
        self.assertEqual(exit_decision["projected_position_btc"], "0.00000")

    def test_risk_and_order_precision_reject_fail_closed(self) -> None:
        rejected = evaluate_order_risk(
            side=OrderSide.BUY,
            quantity=Decimal("0.00013"),
            price=Decimal("78047.0"),
            reduce_only=False,
            current_position=Decimal(0),
            config=replace(DEFAULT_CONFIG, max_entry_notional_usdc=Decimal("10")),
        )
        self.assertFalse(rejected["approved"])
        reasons = cast(list[object], rejected["reasons"])
        self.assertIn("entry notional exceeds the hard USDC exposure cap", reasons)
        with self.assertRaisesRegex(ValueError, "order quantity.*increment"):
            evaluate_order_risk(
                side=OrderSide.BUY,
                quantity=Decimal("0.000131"),
                price=Decimal("78047.0"),
                reduce_only=False,
                current_position=Decimal(0),
                config=DEFAULT_CONFIG,
            )

    def test_run_identity_changes_with_explicit_run_id(self) -> None:
        dataset = load_bounded_dataset()
        source = source_identity()
        first = run_identity(
            run_id="proof-a",
            dataset=dataset,
            config=DEFAULT_CONFIG,
            source=source,
        )
        second = run_identity(
            run_id="proof-b",
            dataset=dataset,
            config=DEFAULT_CONFIG,
            source=source,
        )
        self.assertNotEqual(first, second)


class D01IntegratedRouteTests(unittest.TestCase):
    temporary: tempfile.TemporaryDirectory[str]
    run_dir: Path
    completion: dict[str, object]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="d01-integration-")
        cls.run_dir = Path(cls.temporary.name) / "completed"
        with patch.dict(os.environ, {"TRADING_MODE": "PAPER"}, clear=False):
            cls.completion = run_slice(
                run_id="unittest-completed",
                artifact_dir=cls.run_dir,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_two_replays_and_sandbox_paper_complete_same_bounded_route(self) -> None:
        self.assertEqual(self.completion["status"], "COMPLETED_FLAT")
        recomputed = cast(dict[str, object], self.completion["recomputed"])
        self.assertTrue(recomputed["two_identical_replays"])
        self.assertTrue(recomputed["same_strategy_risk_code_completed_equivalent_paper_lifecycle"])
        self.assertEqual(recomputed["order_count"], 2)
        self.assertEqual(recomputed["fill_count"], 2)
        self.assertEqual(recomputed["final_position_btc"], "0.00000")
        self.assertEqual(recomputed["net_pnl_usdc_assumed"], "-0.0121753320")
        self.assertEqual(
            sorted(path.name for path in self.run_dir.iterdir()),
            [
                "completed-run.json",
                "paper.json",
                "replay-1.json",
                "replay-2.json",
                "run-claim.json",
            ],
        )

    def test_completed_restart_probe_is_read_only_and_constructs_nothing(self) -> None:
        before = {path.name: path.read_bytes() for path in self.run_dir.iterdir()}
        with (
            patch("vertical_slices.d01_btc_perp.slice._run_replay_once") as replay,
            patch("vertical_slices.d01_btc_perp.slice._run_paper") as paper,
        ):
            decision = probe_completed_run(self.run_dir)
        after = {path.name: path.read_bytes() for path in self.run_dir.iterdir()}
        replay.assert_not_called()
        paper.assert_not_called()
        self.assertEqual(before, after)
        self.assertEqual(decision["decision"], "NOOP_ALREADY_COMPLETE")
        self.assertEqual(decision["new_submission_intent_keys"], [])
        self.assertFalse(decision["engine_or_order_construction"])

    def test_existing_run_directory_refuses_second_run_before_engine(self) -> None:
        with (
            patch("vertical_slices.d01_btc_perp.slice._run_replay_once") as replay,
            patch("vertical_slices.d01_btc_perp.slice._run_paper") as paper,
            patch.dict(os.environ, {"TRADING_MODE": "PAPER"}, clear=False),
            self.assertRaises(FileExistsError),
        ):
            run_slice(
                run_id="unittest-completed",
                artifact_dir=self.run_dir,
            )
        replay.assert_not_called()
        paper.assert_not_called()

    def test_risk_rejection_creates_no_run_or_engine(self) -> None:
        rejected_dir = Path(self.temporary.name) / "risk-rejected"
        config = replace(DEFAULT_CONFIG, max_entry_notional_usdc=Decimal("10"))
        with (
            patch("vertical_slices.d01_btc_perp.slice._run_replay_once") as replay,
            patch("vertical_slices.d01_btc_perp.slice._run_paper") as paper,
            patch.dict(os.environ, {"TRADING_MODE": "PAPER"}, clear=False),
            self.assertRaises(RiskRejectedError),
        ):
            run_slice(
                run_id="risk-rejected",
                artifact_dir=rejected_dir,
                config=config,
            )
        replay.assert_not_called()
        paper.assert_not_called()
        self.assertFalse(rejected_dir.exists())

    def test_incomplete_or_tampered_run_is_not_restartable(self) -> None:
        incomplete = Path(self.temporary.name) / "incomplete"
        incomplete.mkdir()
        with self.assertRaisesRegex(RuntimeError, "incomplete run evidence"):
            probe_completed_run(incomplete)

        tampered = Path(self.temporary.name) / "tampered"
        shutil.copytree(self.run_dir, tampered)
        paper_path = tampered / "paper.json"
        paper = json.loads(paper_path.read_text(encoding="utf-8"))
        paper["cost_overlay"]["net_pnl_usdc_assumed"] = "0"
        paper_path.write_text(json.dumps(paper), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "economics"):
            probe_completed_run(tampered)

    def test_restart_verifier_rebinds_paper_mode_and_start_claim(self) -> None:
        mode_tampered = Path(self.temporary.name) / "mode-tampered"
        shutil.copytree(self.run_dir, mode_tampered)
        paper_path = mode_tampered / "paper.json"
        paper = json.loads(paper_path.read_text(encoding="utf-8"))
        paper["execution"]["mode"] = "LIVE"
        paper_path.write_text(json.dumps(paper), encoding="utf-8")
        completion_path = mode_tampered / "completed-run.json"
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        completion["artifacts"]["paper.json"] = hashlib.sha256(paper_path.read_bytes()).hexdigest()
        completion_path.write_text(json.dumps(completion), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mode"):
            probe_completed_run(mode_tampered)

        claim_tampered = Path(self.temporary.name) / "claim-tampered"
        shutil.copytree(self.run_dir, claim_tampered)
        claim_path = claim_tampered / "run-claim.json"
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        claim["state"] = "COMPLETED"
        claim_path.write_text(json.dumps(claim), encoding="utf-8")
        completion_path = claim_tampered / "completed-run.json"
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        completion["artifacts"]["run-claim.json"] = hashlib.sha256(
            claim_path.read_bytes()
        ).hexdigest()
        completion_path.write_text(json.dumps(completion), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "run claim"):
            probe_completed_run(claim_tampered)


if __name__ == "__main__":
    unittest.main()
