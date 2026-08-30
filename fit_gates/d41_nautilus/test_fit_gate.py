"""Targeted deterministic and tamper tests for the D41 fit gate."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model.enums import OrderSide

from fit_gates.d41_nautilus.fit_gate import (
    INSTRUMENT_ID,
    CostAssumptions,
    assert_paper_boundary,
    captured_public_records_to_dataset,
    cost_overlay,
    decide_entry,
    envelope_to_trade_tick,
    existing_btc_contract,
    prepare_new_output_directory,
    run_paper,
    run_two_replays,
    sha256_json,
    verify_gate,
    write_json,
)
from hyperliquid_bot.contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    AggressorSide,
    MarketEventEnvelope,
    TradeEvent,
)


def envelope(
    *,
    ordinal: int = 1,
    price: str = "111111.1",
    quantity: str = "0.00100",
    gap: bool = False,
    delay_seconds: int = 0,
    delay_microseconds: int = 0,
) -> MarketEventEnvelope:
    event_time = datetime(2026, 8, 30, 12, 0, tzinfo=UTC) + timedelta(milliseconds=ordinal * 100)
    return MarketEventEnvelope(
        schema_version=MARKET_EVENT_SCHEMA_VERSION,
        instrument=existing_btc_contract(),
        event=TradeEvent(
            price=Decimal(price),
            quantity=Decimal(quantity),
            aggressor_side=AggressorSide.BUY,
        ),
        event_time=event_time,
        received_time=event_time
        + timedelta(seconds=delay_seconds, microseconds=delay_microseconds),
        received_monotonic_ns=1_000_000_000 + ordinal,
        collector_version="d41-test",
        collector_commit="be51fba535af2956613f238bcdb0e213053952e9",
        is_gap=gap,
        source_event_id=f'["hyperliquid-trade-v1",{ordinal},"BTC",2]',
        source_transaction_id=f"0xpublic{ordinal}",
    )


def capture_metadata(event_count: int) -> dict[str, object]:
    return {
        "collector_route": (
            "HyperliquidTradesCollector -> MarketEventEnvelope-v2 -> envelope_to_trade_tick"
        ),
        "collector_state_after_shutdown": "stopped",
        "sticky_gap": False,
        "volatile_sinks": True,
        "events_received_total": event_count,
        "stale_or_future_events_rejected": 0,
        "invalid_precision_events_rejected": 0,
        "accepted_event_count": event_count,
    }


def synthetic_envelopes() -> list[MarketEventEnvelope]:
    prices = (
        "100000.0",
        "100000.0",
        "100000.1",
        "100000.2",
        "100000.3",
        "100000.4",
        "100000.5",
        "100000.6",
        "100000.7",
        "100000.8",
        "100000.9",
        "100001.0",
        "100001.1",
        "100001.2",
    )
    return [
        envelope(
            ordinal=ordinal,
            price=price,
            delay_microseconds=200_000,
        )
        for ordinal, price in enumerate(prices, start=1)
    ]


class D41FitGateTests(unittest.TestCase):
    def test_thin_boundary_is_exact_and_deterministic(self) -> None:
        first = envelope_to_trade_tick(envelope())
        second = envelope_to_trade_tick(envelope())
        self.assertEqual(first, second)
        self.assertEqual(first.instrument_id, INSTRUMENT_ID)
        self.assertEqual(str(first.price), "111111.1")
        self.assertEqual(str(first.size), "0.00100")

    def test_boundary_preserves_capture_order_without_tid_sequence_claim(self) -> None:
        first = envelope_to_trade_tick(envelope())
        second = envelope_to_trade_tick(
            envelope(ordinal=2),
            prior_ts_init=first.ts_init + 1_000_000_000,
        )
        self.assertEqual(second.ts_init, first.ts_init + 1_000_000_001)

    def test_boundary_rejects_gap_stale_future_and_over_five_seconds(self) -> None:
        envelope_to_trade_tick(envelope(delay_seconds=5))
        with self.assertRaisesRegex(ValueError, "gap-tainted"):
            envelope_to_trade_tick(envelope(gap=True))
        with self.assertRaisesRegex(ValueError, "stale"):
            envelope_to_trade_tick(envelope(delay_seconds=5, delay_microseconds=1))
        with self.assertRaisesRegex(ValueError, "future-dated"):
            envelope_to_trade_tick(envelope(delay_microseconds=-1))

    def test_boundary_rejects_invalid_price_and_quantity_precision(self) -> None:
        with self.assertRaisesRegex(ValueError, "price.*increment"):
            envelope_to_trade_tick(envelope(price="111111.14"))
        with self.assertRaisesRegex(ValueError, "quantity.*increment"):
            envelope_to_trade_tick(envelope(quantity="0.001006"))

    def test_baseline_uses_only_previous_and_current_prices(self) -> None:
        self.assertIs(decide_entry(Decimal(100), Decimal(101)), OrderSide.BUY)
        self.assertIs(decide_entry(Decimal(101), Decimal(100)), OrderSide.SELL)
        self.assertIsNone(decide_entry(Decimal(100), Decimal(100)))

    def test_paper_boundary_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exactly PAPER"):
            assert_paper_boundary(seconds=10, environ={})
        with self.assertRaisesRegex(RuntimeError, "credential environment names"):
            assert_paper_boundary(
                seconds=10,
                environ={"D41_EXECUTION_MODE": "PAPER", "HYPERLIQUID_PK": "not-read"},
            )
        assert_paper_boundary(seconds=10, environ={"D41_EXECUTION_MODE": "PAPER"})

    def test_cost_overlay_recomputes_exact_economics(self) -> None:
        result = cost_overlay(
            [
                {"side": "BUY", "quantity": "0.001", "price": "100000"},
                {"side": "SELL", "quantity": "0.001", "price": "100010"},
            ],
            mark_price=Decimal(100010),
            assumptions=CostAssumptions(),
        )
        self.assertEqual(result["final_position_btc"], "0.000")
        self.assertEqual(result["fee_cost_usdc"], "0.0900045")
        self.assertEqual(result["half_spread_cost_usdc"], "0.0100005")
        self.assertEqual(result["slippage_cost_usdc"], "0.020001")
        self.assertEqual(result["net_pnl_usdc_assumed"], "-0.1100060")

    def test_evidence_writes_are_exclusive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="d41-output-safety-") as temporary:
            output_dir = Path(temporary) / "new-run"
            prepare_new_output_directory(output_dir)
            with self.assertRaises(FileExistsError):
                prepare_new_output_directory(output_dir)
            artifact = output_dir / "artifact.json"
            write_json(artifact, {"first": True})
            with self.assertRaises(FileExistsError):
                write_json(artifact, {"second": True})

    def test_source_has_no_venue_execution_or_paper_dataset_fallback(self) -> None:
        root = Path(os.path.dirname(__file__))
        source = (root / "fit_gate.py").read_text(encoding="utf-8")
        runner = (root / "run_fit_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("HyperliquidExecClientConfig", source)
        self.assertNotIn("HyperliquidLiveExecClientFactory", source)
        self.assertNotIn("dataset-from-paper", runner)


class D41EvidenceTamperTests(unittest.TestCase):
    temporary: tempfile.TemporaryDirectory
    base_dir: Path
    source_path: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="d41-tamper-tests-")
        cls.base_dir = Path(cls.temporary.name) / "base"
        cls.base_dir.mkdir()
        envelopes = synthetic_envelopes()
        dataset = captured_public_records_to_dataset(
            envelopes,
            capture_metadata=capture_metadata(len(envelopes)),
        )
        dataset_path = cls.base_dir / "dataset.json"
        write_json(dataset_path, dataset)
        run_two_replays(dataset_path, artifact_dir=cls.base_dir)
        previous_mode = os.environ.get("D41_EXECUTION_MODE")
        os.environ["D41_EXECUTION_MODE"] = "PAPER"
        try:
            run_paper(
                seconds=10,
                dataset_path=dataset_path,
                artifact_dir=cls.base_dir,
            )
        finally:
            if previous_mode is None:
                os.environ.pop("D41_EXECUTION_MODE", None)
            else:
                os.environ["D41_EXECUTION_MODE"] = previous_mode
        cls.source_path = Path(__file__).resolve().parent / "fit_gate.py"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def copy_run(self, label: str) -> Path:
        destination = Path(self.temporary.name) / label
        shutil.copytree(self.base_dir, destination)
        return destination

    @staticmethod
    def mutate(path: Path, mutation: Callable[[dict[str, object]], None]) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        mutation(value)
        path.write_text(
            json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def verify(self, run_dir: Path) -> dict[str, object]:
        return verify_gate(
            dataset_path=run_dir / "dataset.json",
            artifact_dir=run_dir,
            source_path=self.source_path,
        )

    def test_verifier_recomputes_clean_evidence(self) -> None:
        run_dir = self.copy_run("clean")
        result = self.verify(run_dir)
        self.assertTrue(result["all_hard_gates_passed"])
        self.assertEqual(result["recommendation"], "WRAP")

    def test_tampered_replay_two_is_rejected(self) -> None:
        run_dir = self.copy_run("replay-two")
        self.mutate(
            run_dir / "replay-2.json",
            lambda value: value["business"]["signals"][0].__setitem__("current_price", "99999.9"),
        )
        with self.assertRaisesRegex(ValueError, "replay-2 signal price"):
            self.verify(run_dir)

    def test_tampered_dataset_is_rejected(self) -> None:
        run_dir = self.copy_run("dataset")
        self.mutate(
            run_dir / "dataset.json",
            lambda value: value["events"][0].__setitem__("source_event_id", "tampered"),
        )
        with self.assertRaisesRegex(ValueError, "event digest mismatch"):
            self.verify(run_dir)

    def test_tampered_staleness_is_rejected_even_with_updated_digest(self) -> None:
        run_dir = self.copy_run("staleness")

        def make_stale(value: dict[str, object]) -> None:
            event = value["events"][0]
            event_time = datetime.fromisoformat(event["event_time"])
            event["received_time"] = (event_time + timedelta(seconds=6)).isoformat()
            value["events_sha256"] = sha256_json(value["events"])

        self.mutate(run_dir / "dataset.json", make_stale)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.verify(run_dir)

    def test_tampered_primary_fill_is_rejected(self) -> None:
        run_dir = self.copy_run("fills")
        self.mutate(
            run_dir / "paper.json",
            lambda value: value["nautilus_reports"]["fills"][0].__setitem__("last_qty", "0.00012"),
        )
        with self.assertRaisesRegex(ValueError, "primary fill report"):
            self.verify(run_dir)

    def test_tampered_economics_are_rejected(self) -> None:
        run_dir = self.copy_run("economics")
        self.mutate(
            run_dir / "paper.json",
            lambda value: value["cost_overlay"].__setitem__("fee_cost_usdc", "0"),
        )
        with self.assertRaisesRegex(ValueError, "economics"):
            self.verify(run_dir)

    def test_tampered_verdict_fields_are_rejected(self) -> None:
        run_dir = self.copy_run("verdict")
        self.mutate(
            run_dir / "determinism.json",
            lambda value: value.__setitem__("pass", False),
        )
        with self.assertRaisesRegex(ValueError, "verdict fields"):
            self.verify(run_dir)


if __name__ == "__main__":
    unittest.main()
