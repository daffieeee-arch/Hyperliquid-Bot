"""Focused tests for the bounded COURSE-1 live-public PAPER soak."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import patch

from nautilus_trader.model.enums import OrderSide

from hyperliquid_bot.hyperliquid_trades import decode_hyperliquid_trades_frame
from hyperliquid_bot.local_mode import UnsafeTradingModeError
from vertical_slices.course1_live_public_paper.slice import (
    DEFAULT_SOAK_SECONDS,
    MAX_SOAK_SECONDS,
    VENUE_AUTHORITATIVE_RECONCILIATION_IMPLEMENTED,
    PublicStreamError,
    SoakBoundaryError,
    assert_soak_boundary,
    decode_public_bbo_frame,
    run_soak,
    verify_soak_run,
)
from vertical_slices.d01_btc_perp.slice import (
    DEFAULT_CONFIG,
    D01SmokeStrategy,
    evaluate_order_risk,
)

BASE_TIME = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
EVENT_MS = int(BASE_TIME.timestamp() * 1000) - 100


def _trade_frame(price: str, tid: int, *, side: str = "B") -> str:
    return json.dumps(
        {
            "channel": "trades",
            "data": [
                {
                    "coin": "BTC",
                    "side": side,
                    "px": price,
                    "sz": "0.00100",
                    "hash": f"0x{'ab' * 32}",
                    "time": EVENT_MS,
                    "tid": tid,
                    "users": [
                        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    ],
                }
            ],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _bbo_frame(bid: str = "78046.9", ask: str = "78047.1") -> str:
    return json.dumps(
        {
            "channel": "bbo",
            "data": {
                "coin": "BTC",
                "time": EVENT_MS,
                "bbo": [
                    {"px": bid, "sz": "1.20000", "n": 3},
                    {"px": ask, "sz": "0.80000", "n": 2},
                ],
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _ack(channel: str) -> str:
    return json.dumps(
        {
            "channel": "subscriptionResponse",
            "data": {
                "method": "subscribe",
                "subscription": {"type": channel, "coin": "BTC"},
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def public_lifecycle_frames(*, prices: tuple[str, ...] | None = None) -> list[str]:
    sequence = (
        prices
        if prices is not None
        else (
            *("78047.0",) * 2,
            *("78047.1",) * 18,
        )
    )
    inbound = [
        "Websocket connection established.",
        _ack("trades"),
        _ack("bbo"),
        _bbo_frame(),
    ]
    inbound.extend(_trade_frame(price, tid) for tid, price in enumerate(sequence, start=1))
    inbound.append(_bbo_frame())
    return inbound


class FakePublicSocket:
    def __init__(self, inbound: list[str]) -> None:
        self.inbound = list(inbound)
        self.sent: list[str] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message if type(message) is str else message.decode("utf-8"))

    async def recv(self) -> str:
        if not self.inbound:
            await asyncio.sleep(3600)
            raise PublicStreamError("Fake public socket has no further frames.")
        return self.inbound.pop(0)


def _factory(inbound: list[str], connects: list[int] | None = None):
    @asynccontextmanager
    async def factory():
        if connects is not None:
            connects.append(1)
        yield FakePublicSocket(inbound)

    return factory


def _fixed_now() -> datetime:
    return BASE_TIME


def _fixed_monotonic() -> int:
    return 2_000_000_000


class SoakBoundaryTests(unittest.TestCase):
    def test_paper_mode_passes_and_unsafe_modes_fail(self) -> None:
        assert_soak_boundary(environ={}, seconds=DEFAULT_SOAK_SECONDS)
        assert_soak_boundary(environ={"TRADING_MODE": "PAPER"}, seconds=30)
        for mode in ("LIVE", "SHADOW", "TESTNET", "paper", "Paper"):
            with self.assertRaises(SoakBoundaryError):
                assert_soak_boundary(environ={"TRADING_MODE": mode}, seconds=30)
        with self.assertRaises(SoakBoundaryError):
            assert_soak_boundary(environ={"D41_EXECUTION_MODE": "TESTNET"}, seconds=30)
        with self.assertRaises(UnsafeTradingModeError):
            from hyperliquid_bot.local_mode import require_local_paper_mode

            require_local_paper_mode("LIVE")

    def test_credentials_and_duration_fail_before_network(self) -> None:
        connects: list[int] = []
        factory = _factory(public_lifecycle_frames(), connects)
        with self.assertRaises(SoakBoundaryError):
            assert_soak_boundary(environ={"HYPERLIQUID_PK": "not-read"}, seconds=30)
        with self.assertRaises(SoakBoundaryError):
            assert_soak_boundary(environ={}, seconds=0)
        with self.assertRaises(SoakBoundaryError):
            assert_soak_boundary(environ={}, seconds=MAX_SOAK_SECONDS + 1)
        with (
            patch.dict(os.environ, {"TRADING_MODE": "LIVE"}, clear=False),
            tempfile.TemporaryDirectory(prefix="course1-soak-live-") as temporary,
            self.assertRaises(SoakBoundaryError),
        ):
            run_soak(
                run_id="must-not-connect",
                artifact_dir=Path(temporary) / "run",
                seconds=5,
                connection_factory=factory,
                utc_now=_fixed_now,
                monotonic_ns=_fixed_monotonic,
            )
        self.assertEqual(connects, [])

    def test_public_bbo_and_trade_rules_fail_closed(self) -> None:
        quote = decode_public_bbo_frame(
            json.loads(_bbo_frame()),
            received_time=BASE_TIME,
            received_monotonic_ns=1,
        )
        self.assertEqual(quote.spread, Decimal("0.2"))
        with self.assertRaisesRegex(ValueError, "increment"):
            decode_public_bbo_frame(
                json.loads(_bbo_frame(bid="78046.95", ask="78047.1")),
                received_time=BASE_TIME,
                received_monotonic_ns=1,
            )
        with self.assertRaisesRegex(ValueError, "crossed"):
            decode_public_bbo_frame(
                json.loads(_bbo_frame(bid="78047.2", ask="78047.1")),
                received_time=BASE_TIME,
                received_monotonic_ns=1,
            )
        stale = json.loads(_bbo_frame())
        with self.assertRaisesRegex(ValueError, "stale"):
            decode_public_bbo_frame(
                stale,
                received_time=BASE_TIME + timedelta(seconds=6),
                received_monotonic_ns=1,
            )
        trades = decode_hyperliquid_trades_frame(json.loads(_trade_frame("78047.0", 1)))
        self.assertEqual(trades[0].coin, "BTC")

    def test_same_d01_strategy_and_risk_helpers(self) -> None:
        self.assertFalse(VENUE_AUTHORITATIVE_RECONCILIATION_IMPLEMENTED)
        decision = evaluate_order_risk(
            side=OrderSide.BUY,
            quantity=Decimal("0.00013"),
            price=Decimal("78047.0"),
            reduce_only=False,
            current_position=Decimal(0),
            config=DEFAULT_CONFIG,
        )
        self.assertTrue(decision["approved"])
        self.assertEqual(decision["assumed_stop_plus_round_trip_cost_loss_usdc"], "0.215097532")
        self.assertEqual(
            f"{D01SmokeStrategy.__module__}.{D01SmokeStrategy.__qualname__}",
            "vertical_slices.d01_btc_perp.slice.D01SmokeStrategy",
        )

    def test_d22b_is_not_implemented(self) -> None:
        import vertical_slices.course1_live_public_paper.slice as soak

        self.assertFalse(soak.VENUE_AUTHORITATIVE_RECONCILIATION_IMPLEMENTED)
        self.assertFalse(hasattr(soak, "reconcile_venue_state"))
        self.assertFalse(hasattr(soak, "venue_authoritative_reconciliation"))


class SoakIntegratedTests(unittest.TestCase):
    temporary: tempfile.TemporaryDirectory[str]
    run_dir: Path
    completion: dict[str, object]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="course1-soak-")
        cls.run_dir = Path(cls.temporary.name) / "completed"
        with patch.dict(os.environ, {"TRADING_MODE": "PAPER"}, clear=False):
            cls.completion = run_soak(
                run_id="unittest-completed",
                artifact_dir=cls.run_dir,
                seconds=5,
                connection_factory=_factory(public_lifecycle_frames()),
                utc_now=_fixed_now,
                monotonic_ns=_fixed_monotonic,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_fixture_stream_completes_flat_paper(self) -> None:
        self.assertEqual(self.completion["status"], "COMPLETED_FLAT")
        recomputed = cast(dict[str, object], self.completion["recomputed"])
        self.assertTrue(recomputed["same_d01_strategy_risk_code"])
        self.assertEqual(recomputed["order_count"], 2)
        self.assertEqual(recomputed["fill_count"], 2)
        self.assertEqual(recomputed["final_position_btc"], "0.00000")
        self.assertTrue(recomputed["complete"])
        self.assertGreaterEqual(int(cast(int, recomputed["trade_count"])), 12)
        self.assertGreaterEqual(int(cast(int, recomputed["bbo_count"])), 1)
        self.assertEqual(
            sorted(path.name for path in self.run_dir.iterdir()),
            [
                "capture-health.json",
                "completed-run.json",
                "fills.json",
                "orders.json",
                "paper-pnl.json",
                "paper-position.json",
                "paper.json",
                "public-stream.json",
                "run-claim.json",
            ],
        )

    def test_artifacts_recompute_and_contain_no_credentials(self) -> None:
        verified = verify_soak_run(self.run_dir, run_id="unittest-completed", seconds=5)
        self.assertEqual(verified["status"], "VERIFIED")
        self.assertEqual(verified["decision"], "COMPLETED_FLAT")
        self.assertFalse(verified["venue_authoritative_reconciliation"])
        paper = json.loads((self.run_dir / "paper.json").read_text(encoding="utf-8"))
        execution = paper["execution"]
        self.assertEqual(execution["mode"], "PAPER")
        self.assertEqual(execution["credential_names_present_at_start"], [])
        self.assertFalse(execution["registered_venue_execution_client"])
        self.assertFalse(execution["venue_orders_submitted"])
        self.assertFalse(execution["signing"])
        self.assertFalse(execution["testnet"])
        self.assertFalse(execution["live"])
        self.assertEqual(
            paper["business"]["strategy_class"],
            "vertical_slices.d01_btc_perp.slice.D01SmokeStrategy",
        )
        overlay = paper["cost_overlay"]
        self.assertEqual(overlay["funding_payment_usdc"], "0")
        pnl = json.loads((self.run_dir / "paper-pnl.json").read_text(encoding="utf-8"))
        self.assertEqual(pnl["net_pnl_usdc_assumed"], overlay["net_pnl_usdc_assumed"])
        self.assertTrue(pnl["assumed"])
        self.assertFalse(pnl["venue_pnl"])
        self.assertEqual(pnl["funding_payment_usdc"], "0")
        health = json.loads((self.run_dir / "capture-health.json").read_text(encoding="utf-8"))
        self.assertFalse(health["twenty_four_seven"])
        self.assertTrue(health["credentialless"])
        blob = json.dumps(paper) + json.dumps(
            json.loads((self.run_dir / "public-stream.json").read_text(encoding="utf-8"))
        )
        for forbidden in (
            "HYPERLIQUID_PK",
            "private_key",
            "secret",
            "wallet",
            "TESTNET",
            "SHADOW",
            "LIVE",
        ):
            self.assertNotIn(forbidden, blob)

    def test_existing_directory_refused(self) -> None:
        connects: list[int] = []
        with (
            patch.dict(os.environ, {"TRADING_MODE": "PAPER"}, clear=False),
            self.assertRaises(FileExistsError),
        ):
            run_soak(
                run_id="unittest-completed",
                artifact_dir=self.run_dir,
                seconds=5,
                connection_factory=_factory(public_lifecycle_frames(), connects),
                utc_now=_fixed_now,
                monotonic_ns=_fixed_monotonic,
            )
        self.assertEqual(connects, [])

    def test_risk_rejection_writes_no_orders(self) -> None:
        rejected_dir = Path(self.temporary.name) / "risk-rejected"
        with patch.dict(os.environ, {"TRADING_MODE": "PAPER"}, clear=False):
            completion = run_soak(
                run_id="risk-rejected",
                artifact_dir=rejected_dir,
                seconds=5,
                config=replace(DEFAULT_CONFIG, max_entry_notional_usdc=Decimal("1")),
                connection_factory=_factory(public_lifecycle_frames()),
                utc_now=_fixed_now,
                monotonic_ns=_fixed_monotonic,
            )
        self.assertEqual(completion["status"], "RISK_REJECTED")
        paper = json.loads((rejected_dir / "paper.json").read_text(encoding="utf-8"))
        self.assertEqual(paper["business"]["order_intents"], [])
        self.assertEqual(paper["business"]["fills"], [])
        self.assertEqual(paper["cost_overlay"]["final_position_btc"], "0")
        self.assertFalse(paper["execution"]["venue_orders_submitted"])

    def test_stale_public_trade_fails_closed(self) -> None:
        stale = json.loads(_trade_frame("78047.0", 1))
        stale["data"][0]["time"] = EVENT_MS - 10_000
        inbound = [
            "Websocket connection established.",
            _ack("trades"),
            _ack("bbo"),
            _bbo_frame(),
            json.dumps(stale, separators=(",", ":")),
        ]
        failed_dir = Path(self.temporary.name) / "stale"
        with (
            patch.dict(os.environ, {"TRADING_MODE": "PAPER"}, clear=False),
            self.assertRaisesRegex(PublicStreamError, "no accepted public BTC trades"),
        ):
            run_soak(
                run_id="stale-trade",
                artifact_dir=failed_dir,
                seconds=5,
                connection_factory=_factory(inbound),
                utc_now=_fixed_now,
                monotonic_ns=_fixed_monotonic,
            )
        self.assertTrue((failed_dir / "run-claim.json").is_file())
        self.assertFalse((failed_dir / "paper.json").exists())


if __name__ == "__main__":
    unittest.main()
