"""Focused tests for the bounded D22-A local PAPER ledger."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import patch

from fit_gates.d41_nautilus.fit_gate import PROTECTED_CREDENTIAL_ENV_NAMES
from nautilus_trader.model.enums import OrderSide

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from vertical_slices.d01_btc_perp.slice import DEFAULT_CONFIG, D01SmokeStrategy
from vertical_slices.d22a_paper_ledger.slice import (
    CRASH_EXIT_CODE,
    FILL_SETTLEMENT,
    PRE_SUBMIT,
    LedgerBackedD01Strategy,
    LedgerConflictError,
    LedgerRecord,
    PaperLedger,
    RunContext,
    build_run_context,
    make_pre_submit_record,
    make_settlement_record,
    project_ledger,
    run_recoverable_paper,
    verify_ledger,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def entry_record(
    context: RunContext,
) -> tuple[str, str, str, dict[str, object], LedgerRecord]:
    return make_pre_submit_record(
        context=context,
        intent_ordinal=1,
        reason="entry",
        decision_ordinal=3,
        submission_ordinal=4,
        side=OrderSide.BUY,
        quantity=Decimal("0.00013"),
        reduce_only=False,
        current_position=Decimal(0),
        risk_price=Decimal("78047.0"),
    )


def entry_fill() -> dict[str, object]:
    return {
        "fill_ordinal": 1,
        "observed_tick_ordinal": 5,
        "side": "BUY",
        "quantity": "0.00013",
        "price": "78047.0",
        "position_after": "0.00013",
    }


class D22ALedgerUnitTests(unittest.TestCase):
    temporary: tempfile.TemporaryDirectory[str]
    root: Path
    context: RunContext

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="d22a-ledger-")
        self.root = Path(self.temporary.name)
        self.context = build_run_context(run_id="d22a-unit")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_duplicate_is_noop_conflict_rolls_back_and_tables_are_append_only(self) -> None:
        ledger_path = self.root / "paper-ledger.sqlite3"
        intent_id, client_id, _, _, pre_submit = entry_record(self.context)
        _, settlement = make_settlement_record(
            context=self.context,
            intent_id=intent_id,
            client_order_id=client_id,
            fill=entry_fill(),
            fills=[entry_fill()],
        )
        with PaperLedger(ledger_path, self.context) as ledger:
            self.assertEqual(
                ledger.connection.execute("PRAGMA journal_mode").fetchone()[0], "delete"
            )
            self.assertEqual(ledger.connection.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(ledger.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(
                ledger.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
            self.assertEqual(ledger.append((pre_submit,)), 1)
            self.assertEqual(ledger.append((pre_submit,)), 0)
            conflict = replace(
                pre_submit,
                payload={**pre_submit.payload, "tampered": True},
            )
            with self.assertRaises(LedgerConflictError):
                ledger.append((settlement, conflict))
            self.assertEqual(ledger.records(), (pre_submit,))
            self.assertEqual(ledger.append((settlement,)), 1)
            changed_fill = {**entry_fill(), "observed_tick_ordinal": 6}
            _, conflicting_settlement = make_settlement_record(
                context=self.context,
                intent_id=intent_id,
                client_order_id=client_id,
                fill=changed_fill,
                fills=[changed_fill],
            )
            self.assertEqual(settlement.record_id, conflicting_settlement.record_id)
            with self.assertRaises(LedgerConflictError):
                ledger.append((conflicting_settlement,))
            self.assertEqual(ledger.records(), (pre_submit, settlement))
            for statement in (
                "UPDATE ledger_records SET kind = kind",
                "DELETE FROM ledger_records",
            ):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    ledger.connection.execute(statement)

    def test_stable_project_ids_exclude_scheduler_ordinals(self) -> None:
        first = entry_record(self.context)
        second = make_pre_submit_record(
            context=self.context,
            intent_ordinal=1,
            reason="entry",
            decision_ordinal=300,
            submission_ordinal=400,
            side=OrderSide.BUY,
            quantity=Decimal("0.00013"),
            reduce_only=False,
            current_position=Decimal(0),
            risk_price=Decimal("78047.0"),
        )
        self.assertEqual(first[:2], second[:2])
        self.assertNotEqual(first[2], second[2])
        self.assertNotEqual(first[4], second[4])

    def test_projector_recomputes_economics_and_rejects_tampering(self) -> None:
        intent_id, client_id, _, _, pre_submit = entry_record(self.context)
        fill = entry_fill()
        _, settlement = make_settlement_record(
            context=self.context,
            intent_id=intent_id,
            client_order_id=client_id,
            fill=fill,
            fills=[fill],
        )
        projection = project_ledger(self.context, (pre_submit, settlement))
        self.assertEqual(projection.status, "RECOVERY_REQUIRED")
        self.assertEqual(projection.position_btc, "0.00013")
        self.assertEqual(projection.net_pnl_usdc_assumed, "-0.0060876660")
        payload = dict(settlement.payload)
        payload["pnl"] = {"net_pnl_usdc_assumed": "999"}
        with self.assertRaisesRegex(LedgerConflictError, "does not recompute"):
            project_ledger(self.context, (pre_submit, replace(settlement, payload=payload)))

    def test_wrong_run_and_read_only_append_fail_closed(self) -> None:
        ledger_path = self.root / "paper-ledger.sqlite3"
        pre_submit = entry_record(self.context)[4]
        with PaperLedger(ledger_path, self.context) as ledger:
            ledger.append((pre_submit,))
        other = build_run_context(run_id="d22a-other")
        with self.assertRaisesRegex(LedgerConflictError, "header"):
            PaperLedger(ledger_path, other)
        with PaperLedger(ledger_path, self.context, read_only=True) as ledger:
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                ledger.append((pre_submit,))

    def test_nonpaper_mode_fails_before_creating_a_ledger(self) -> None:
        ledger_path = self.root / "forbidden.sqlite3"
        with (
            patch.dict(os.environ, {"TRADING_MODE": "LIVE"}, clear=False),
            self.assertRaises(UnsafeTradingModeError),
        ):
            run_recoverable_paper(run_id="d22a-forbidden", ledger_path=ledger_path)
        self.assertFalse(ledger_path.exists())

    def test_wrong_starting_cash_fails_before_creating_a_ledger(self) -> None:
        ledger_path = self.root / "wrong-cash.sqlite3"
        config = replace(DEFAULT_CONFIG, starting_cash_usdc=Decimal("50000"))
        with self.assertRaisesRegex(ValueError, "starting cash"):
            run_recoverable_paper(
                run_id="d22a-wrong-cash",
                ledger_path=ledger_path,
                config=config,
            )
        self.assertFalse(ledger_path.exists())


class D22AProcessRecoveryTest(unittest.TestCase):
    def test_real_process_crash_recovers_without_duplicate_durable_records(self) -> None:
        self.assertIs(LedgerBackedD01Strategy.on_trade_tick, D01SmokeStrategy.on_trade_tick)
        with tempfile.TemporaryDirectory(prefix="d22a-process-") as temporary:
            ledger_path = Path(temporary) / "paper-ledger.sqlite3"
            run_id = "d22a-process-recovery"
            base_command = [
                sys.executable,
                "-m",
                "vertical_slices.d22a_paper_ledger.run_slice",
                "run",
                "--run-id",
                run_id,
                "--ledger",
                str(ledger_path),
            ]
            environment = os.environ.copy()
            for name in (*PROTECTED_CREDENTIAL_ENV_NAMES, "D41_EXECUTION_MODE"):
                environment.pop(name, None)
            environment.update(
                {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": "src:.",
                    "TRADING_MODE": "PAPER",
                }
            )
            crashed = subprocess.run(
                [*base_command, "--crash-process-after-entry-fill"],
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(crashed.returncode, CRASH_EXIT_CODE, crashed.stderr)
            interrupted = cast(
                dict[str, object],
                verify_ledger(run_id=run_id, ledger_path=ledger_path)["projection"],
            )
            self.assertEqual(interrupted["record_count"], 2)
            self.assertEqual(interrupted["intent_count"], 1)
            self.assertEqual(interrupted["order_count"], 1)
            self.assertEqual(interrupted["fill_count"], 1)
            self.assertEqual(interrupted["position_btc"], "0.00013")
            stable_entry_ids = (
                cast(list[str], interrupted["intent_ids"])[0],
                cast(list[str], interrupted["client_order_ids"])[0],
                cast(list[str], interrupted["fill_ids"])[0],
            )

            recovered_process = subprocess.run(
                base_command,
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(recovered_process.returncode, 0, recovered_process.stderr)
            recovered = json.loads(recovered_process.stdout)
            self.assertEqual(recovered["decision"], "RECOVERED_COMPLETED_FLAT")
            self.assertEqual(recovered["before"]["record_count"], 2)
            self.assertEqual(recovered["after"]["record_count"], 4)
            self.assertEqual(recovered["after"]["intent_count"], 2)
            self.assertEqual(recovered["after"]["order_count"], 2)
            self.assertEqual(recovered["after"]["fill_count"], 2)
            self.assertEqual(recovered["after"]["position_btc"], "0.00000")
            self.assertEqual(recovered["after"]["net_pnl_usdc_assumed"], "-0.0121753320")
            self.assertEqual(
                (
                    recovered["after"]["intent_ids"][0],
                    recovered["after"]["client_order_ids"][0],
                    recovered["after"]["fill_ids"][0],
                ),
                stable_entry_ids,
            )

            completed = subprocess.run(
                base_command,
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            no_op = json.loads(completed.stdout)
            self.assertEqual(no_op["decision"], "NOOP_ALREADY_COMPLETE")
            self.assertEqual(no_op["before"], no_op["after"])
            context = build_run_context(run_id=run_id)
            with PaperLedger(ledger_path, context, read_only=True) as ledger:
                records = ledger.records()
            self.assertEqual(
                [record.kind for record in records],
                [PRE_SUBMIT, FILL_SETTLEMENT, PRE_SUBMIT, FILL_SETTLEMENT],
            )


if __name__ == "__main__":
    unittest.main()
