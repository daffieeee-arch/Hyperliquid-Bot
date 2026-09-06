"""Thin SQLite durability boundary around the D01 sandbox-PAPER route.

A restart rebuilds a new disposable sandbox from the same D01 dataset. It does
not restore Nautilus engine state. The project ledger deduplicates that replay
with stable IDs and is authoritative only for this bounded local PAPER proof.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, cast

from fit_gates.d41_nautilus.fit_gate import (
    INSTRUMENT_ID,
    PROTECTED_CREDENTIAL_ENV_NAMES,
    STARTING_CASH,
    D41SmokeStrategyConfig,
    assert_paper_boundary,
    build_nautilus_instrument,
    canonical_json,
    cost_overlay,
    sha256_json,
)
from nautilus_trader.adapters.hyperliquid import HYPERLIQUID
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import ClientOrderId  # type: ignore[import-not-found]

from hyperliquid_bot.paper_risk import (
    PaperOrderIntent,
    extend_d01_risk_decision,
    paper_snapshot_for_bounded_book,
)
from vertical_slices.d01_btc_perp.slice import (
    DEFAULT_CONFIG,
    DEFAULT_COSTS,
    DEFAULT_DATASET,
    PAPER_SECONDS,
    D01SmokeStrategy,
    LoadedDataset,
    SliceConfig,
    _derived_internal_paper_mode,
    _paper_node_config,
    _reports,
    _run_result,
    _validate_lifecycle,
    assert_local_boundary,
    evaluate_order_risk,
    load_bounded_dataset,
)
from vertical_slices.d01_btc_perp.slice import run_identity as d01_run_identity
from vertical_slices.d01_btc_perp.slice import source_identity as d01_source_identity

D22A_SCHEMA: Final = "d22a-paper-ledger-v1"
LEDGER_USER_VERSION: Final = 1
CRASH_EXIT_CODE: Final = 86
PRE_SUBMIT: Final = "PRE_SUBMIT"
FILL_SETTLEMENT: Final = "FILL_SETTLEMENT"


class LedgerConflictError(RuntimeError):
    """An immutable stable ID already carries different content."""


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: str
    logical_run_identity: str
    d01_run_identity: str
    dataset: LoadedDataset
    config: SliceConfig
    header: dict[str, object]


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    record_id: str
    kind: str
    intent_id: str
    client_order_id: str
    payload: dict[str, object]

    def values(self, run_identity: str) -> tuple[str, str, str, str, str, str, str]:
        payload_json = canonical_json(self.payload).decode("utf-8")
        return (
            self.record_id,
            self.kind,
            run_identity,
            self.intent_id,
            self.client_order_id,
            payload_json,
            hashlib.sha256(payload_json.encode()).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class LedgerProjection:
    status: str
    record_count: int
    intent_count: int
    order_count: int
    fill_count: int
    position_btc: str
    cash_usdc_assumed: str
    equity_usdc_assumed: str
    net_pnl_usdc_assumed: str
    intent_ids: tuple[str, ...]
    client_order_ids: tuple[str, ...]
    fill_ids: tuple[str, ...]

    @property
    def completed_flat(self) -> bool:
        return self.status == "COMPLETED_FLAT"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "record_count": self.record_count,
            "intent_count": self.intent_count,
            "order_count": self.order_count,
            "fill_count": self.fill_count,
            "position_btc": self.position_btc,
            "cash_usdc_assumed": self.cash_usdc_assumed,
            "equity_usdc_assumed": self.equity_usdc_assumed,
            "net_pnl_usdc_assumed": self.net_pnl_usdc_assumed,
            "intent_ids": list(self.intent_ids),
            "client_order_ids": list(self.client_order_ids),
            "fill_ids": list(self.fill_ids),
        }


def build_run_context(
    *,
    run_id: str,
    dataset_path: Path = DEFAULT_DATASET,
    config: SliceConfig = DEFAULT_CONFIG,
) -> RunContext:
    if config.starting_cash_usdc != STARTING_CASH:
        raise ValueError("D22-A starting cash must match the bounded D01 account.")
    dataset = load_bounded_dataset(dataset_path)
    d01_source = d01_source_identity()
    base_identity = d01_run_identity(
        run_id=run_id,
        dataset=dataset,
        config=config,
        source=d01_source,
    )
    wrapper_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    logical_identity = sha256_json(
        {
            "schema": D22A_SCHEMA,
            "d01_run_identity": base_identity,
            "wrapper_source_sha256": wrapper_sha256,
        }
    )
    header: dict[str, object] = {
        "schema": D22A_SCHEMA,
        "run_id": run_id,
        "logical_run_identity": logical_identity,
        "d01_run_identity": base_identity,
        "dataset_file_sha256": dataset.file_sha256,
        "dataset_events_sha256": dataset.events_sha256,
        "config": config.to_dict(),
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "d01_source_digest": d01_source["digest"],
        "wrapper_source_sha256": wrapper_sha256,
        "claim": "local_disposable_sandbox_rebuild_with_project_ledger_deduplication",
    }
    return RunContext(
        run_id=run_id,
        logical_run_identity=logical_identity,
        d01_run_identity=base_identity,
        dataset=dataset,
        config=config,
        header=header,
    )


def stable_intent_id(
    *,
    context: RunContext,
    intent_ordinal: int,
    reason: str,
    side: str,
    quantity: str,
    reduce_only: bool,
) -> str:
    """Timing-independent project ID for one logical D01 intent."""

    return sha256_json(
        {
            "domain": "d22a-local-paper-intent-v1",
            "run_identity": context.logical_run_identity,
            "instrument_id": INSTRUMENT_ID.value,
            "intent_ordinal": intent_ordinal,
            "reason": reason,
            "side": side,
            "quantity": quantity,
            "order_type": "MARKET",
            "reduce_only": reduce_only,
        }
    )


def stable_client_order_id(intent_id: str, *, intent_ordinal: int) -> str:
    digest = sha256_json(
        {
            "domain": "d22a-local-paper-client-order-v1",
            "intent_id": intent_id,
            "intent_ordinal": intent_ordinal,
        }
    )
    return f"D22A-{intent_ordinal:02d}-{digest}"


def _record_id(kind: str, subject_id: str) -> str:
    return sha256_json(
        {"domain": "d22a-paper-ledger-record-v1", "kind": kind, "subject_id": subject_id}
    )


def make_pre_submit_record(
    *,
    context: RunContext,
    intent_ordinal: int,
    reason: str,
    decision_ordinal: int,
    submission_ordinal: int,
    side: OrderSide,
    quantity: Decimal,
    reduce_only: bool,
    current_position: Decimal,
    risk_price: Decimal,
) -> tuple[str, str, str, dict[str, object], LedgerRecord]:
    risk = evaluate_order_risk(
        side=side,
        quantity=quantity,
        price=risk_price,
        reduce_only=reduce_only,
        current_position=current_position,
        config=context.config,
    )
    risk = extend_d01_risk_decision(
        risk,
        snapshot=paper_snapshot_for_bounded_book(
            equity_usdc=context.config.starting_cash_usdc,
            price=risk_price,
            current_position=current_position,
        ),
        intent=PaperOrderIntent(
            side=side.name,
            quantity=quantity,
            price=risk_price,
            reduce_only=reduce_only,
            stop_distance_fraction=context.config.assumed_stop_distance_fraction,
        ),
        trading_mode=os.environ.get("TRADING_MODE"),
    )
    d01_intent = {
        "run_identity": context.logical_run_identity,
        "instrument_id": INSTRUMENT_ID.value,
        "intent_ordinal": intent_ordinal,
        "reason": reason,
        "decision_ordinal": decision_ordinal,
        "submission_ordinal": submission_ordinal,
        "side": side.name,
        "quantity": str(quantity),
        "order_type": "MARKET",
        "reduce_only": reduce_only,
    }
    d01_intent_sha256 = sha256_json(d01_intent)
    risk["intent_sha256"] = d01_intent_sha256
    if risk["approved"] is not True:
        raise RuntimeError(f"D22-A refuses a risk-rejected intent: {risk['reasons']}")
    intent_id = stable_intent_id(
        context=context,
        intent_ordinal=intent_ordinal,
        reason=reason,
        side=side.name,
        quantity=str(quantity),
        reduce_only=reduce_only,
    )
    client_order_id = stable_client_order_id(intent_id, intent_ordinal=intent_ordinal)
    intent = {key: value for key, value in d01_intent.items() if key != "run_identity"}
    intent.update(
        {
            "intent_id": intent_id,
            "client_order_id": client_order_id,
            "d01_intent_sha256": d01_intent_sha256,
            "risk": risk,
        }
    )
    payload: dict[str, object] = {
        "schema": D22A_SCHEMA,
        "kind": PRE_SUBMIT,
        "run_identity": context.logical_run_identity,
        "intent": intent,
        "order": {
            "intent_id": intent_id,
            "client_order_id": client_order_id,
            "instrument_id": INSTRUMENT_ID.value,
            "side": side.name,
            "quantity": str(quantity),
            "order_type": "MARKET",
            "reduce_only": reduce_only,
            "state": "ORDER_COMMAND_RESERVED_BEFORE_LOCAL_SANDBOX_SUBMISSION",
        },
    }
    record = LedgerRecord(
        record_id=_record_id(PRE_SUBMIT, intent_id),
        kind=PRE_SUBMIT,
        intent_id=intent_id,
        client_order_id=client_order_id,
        payload=payload,
    )
    return intent_id, client_order_id, d01_intent_sha256, risk, record


def make_settlement_record(
    *,
    context: RunContext,
    intent_id: str,
    client_order_id: str,
    fill: Mapping[str, object],
    fills: list[dict[str, object]],
) -> tuple[str, LedgerRecord]:
    fill_ordinal = fill.get("fill_ordinal")
    if type(fill_ordinal) is not int:
        raise TypeError("D22-A fill ordinal must be an integer.")
    fill_values = {
        "fill_ordinal": fill_ordinal,
        "observed_tick_ordinal": fill.get("observed_tick_ordinal"),
        "side": fill.get("side"),
        "quantity": fill.get("quantity"),
        "price": fill.get("price"),
        "position_after": fill.get("position_after"),
    }
    fill_id = sha256_json(
        {
            "domain": "d22a-local-paper-fill-v1",
            "run_identity": context.logical_run_identity,
            "intent_id": intent_id,
            "client_order_id": client_order_id,
            **fill_values,
        }
    )
    overlay = cost_overlay(
        fills,
        mark_price=Decimal(str(fill["price"])),
        assumptions=DEFAULT_COSTS,
    )
    if fill_ordinal != len(fills) or Decimal(str(fill["position_after"])) != Decimal(
        overlay["final_position_btc"]
    ):
        raise LedgerConflictError("D22-A fill ordinal/position does not recompute.")
    payload: dict[str, object] = {
        "schema": D22A_SCHEMA,
        "kind": FILL_SETTLEMENT,
        "run_identity": context.logical_run_identity,
        "intent_id": intent_id,
        "client_order_id": client_order_id,
        "fill": {"fill_id": fill_id, **fill_values},
        "position": {"position_btc": overlay["final_position_btc"]},
        "cash": {
            "cash_usdc_assumed": overlay["ending_cash_usdc_assumed"],
            "equity_usdc_assumed": overlay["ending_equity_usdc_assumed"],
            "mark_price": overlay["mark_price"],
        },
        "pnl": {
            "net_pnl_usdc_assumed": overlay["net_pnl_usdc_assumed"],
            "fee_cost_usdc": overlay["fee_cost_usdc"],
            "half_spread_cost_usdc": overlay["half_spread_cost_usdc"],
            "slippage_cost_usdc": overlay["slippage_cost_usdc"],
            "funding_payment_usdc": overlay["funding_payment_usdc"],
        },
    }
    return fill_id, LedgerRecord(
        record_id=_record_id(FILL_SETTLEMENT, intent_id),
        kind=FILL_SETTLEMENT,
        intent_id=intent_id,
        client_order_id=client_order_id,
        payload=payload,
    )


class PaperLedger:
    """Concrete single-writer SQLite store for exactly this local proof."""

    def __init__(self, path: Path, context: RunContext, *, read_only: bool = False) -> None:
        self.context = context
        self.read_only = read_only
        existed = path.exists()
        if read_only:
            if not existed:
                raise FileNotFoundError(path)
            self.connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
            self.connection.execute("PRAGMA journal_mode = DELETE")
            self.connection.execute("PRAGMA synchronous = FULL")
            self.connection.execute("PRAGMA foreign_keys = ON")
            if not existed:
                self._initialize()
        self.connection.row_factory = sqlite3.Row
        try:
            self._verify_header()
        except BaseException:
            self.connection.close()
            raise

    def _initialize(self) -> None:
        header_json = canonical_json(self.context.header).decode()
        header_sha256 = hashlib.sha256(header_json.encode()).hexdigest()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """CREATE TABLE ledger_header (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema TEXT NOT NULL,
                    run_identity TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL
                ) STRICT"""
            )
            self.connection.execute(
                """CREATE TABLE ledger_records (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL CHECK (kind IN ('PRE_SUBMIT', 'FILL_SETTLEMENT')),
                    run_identity TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    UNIQUE (kind, intent_id),
                    FOREIGN KEY (run_identity) REFERENCES ledger_header(run_identity)
                ) STRICT"""
            )
            for table in ("ledger_header", "ledger_records"):
                self.connection.execute(
                    f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'D22-A ledger is append-only'); END"""
                )
                self.connection.execute(
                    f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'D22-A ledger is append-only'); END"""
                )
            self.connection.execute(f"PRAGMA user_version = {LEDGER_USER_VERSION}")
            self.connection.execute(
                "INSERT INTO ledger_header VALUES (1, ?, ?, ?, ?)",
                (D22A_SCHEMA, self.context.logical_run_identity, header_json, header_sha256),
            )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _verify_header(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version != LEDGER_USER_VERSION:
            raise RuntimeError(f"Unsupported D22-A ledger user_version: {version}")
        integrity = [row[0] for row in self.connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise RuntimeError(f"D22-A SQLite integrity check failed: {integrity}")
        rows = self.connection.execute(
            "SELECT schema, run_identity, payload_json, payload_sha256 FROM ledger_header"
        ).fetchall()
        expected_json = canonical_json(self.context.header).decode()
        expected = (
            D22A_SCHEMA,
            self.context.logical_run_identity,
            expected_json,
            hashlib.sha256(expected_json.encode()).hexdigest(),
        )
        if len(rows) != 1 or tuple(rows[0]) != expected:
            raise LedgerConflictError("D22-A ledger header does not match this logical run.")

    def append(self, records: Sequence[LedgerRecord]) -> int:
        if self.read_only:
            raise RuntimeError("Cannot append through a read-only D22-A ledger.")
        if not records or len({record.record_id for record in records}) != len(records):
            raise ValueError("D22-A transaction must contain unique records.")
        inserted = 0
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for record in records:
                if record.payload.get("run_identity") != self.context.logical_run_identity:
                    raise LedgerConflictError("D22-A record belongs to another logical run.")
                values = record.values(self.context.logical_run_identity)
                existing = self.connection.execute(
                    """SELECT record_id, kind, run_identity, intent_id, client_order_id,
                              payload_json, payload_sha256
                       FROM ledger_records WHERE record_id = ?""",
                    (record.record_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != values:
                        raise LedgerConflictError(
                            f"D22-A stable record collision for {record.record_id}."
                        )
                    continue
                self.connection.execute(
                    """INSERT INTO ledger_records (
                           record_id, kind, run_identity, intent_id, client_order_id,
                           payload_json, payload_sha256
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    values,
                )
                inserted += 1
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return inserted

    def records(self) -> tuple[LedgerRecord, ...]:
        rows = self.connection.execute(
            """SELECT sequence, record_id, kind, run_identity, intent_id, client_order_id,
                      payload_json, payload_sha256
               FROM ledger_records ORDER BY sequence"""
        ).fetchall()
        result: list[LedgerRecord] = []
        for sequence, row in enumerate(rows, start=1):
            if row["sequence"] != sequence:
                raise LedgerConflictError("D22-A ledger sequence is not contiguous.")
            payload_json = row["payload_json"]
            payload = json.loads(payload_json)
            if type(payload) is not dict or canonical_json(payload).decode() != payload_json:
                raise LedgerConflictError("D22-A ledger payload is not canonical JSON.")
            record = LedgerRecord(
                record_id=row["record_id"],
                kind=row["kind"],
                intent_id=row["intent_id"],
                client_order_id=row["client_order_id"],
                payload=cast(dict[str, object], payload),
            )
            if record.values(self.context.logical_run_identity) != tuple(row)[1:]:
                raise LedgerConflictError("D22-A ledger record does not recompute exactly.")
            result.append(record)
        return tuple(result)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> PaperLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value:
        raise TypeError(f"D22-A {field} must be non-empty text.")
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise TypeError(f"D22-A {field} must be an integer.")
    return value


def project_ledger(context: RunContext, records: Sequence[LedgerRecord]) -> LedgerProjection:
    """Recompute all decisive record relations and economics from the ledger."""

    cursor = 0
    intents: list[str] = []
    client_ids: list[str] = []
    fill_ids: list[str] = []
    fills: list[dict[str, object]] = []
    position = Decimal(0)
    while cursor < len(records):
        if len(intents) >= 2 or records[cursor].kind != PRE_SUBMIT:
            raise LedgerConflictError("D22-A ledger has an invalid record sequence.")
        pre_submit = records[cursor]
        intent = pre_submit.payload.get("intent")
        if type(intent) is not dict:
            raise TypeError("D22-A pre-submit intent must be an object.")
        ordinal = _integer(intent.get("intent_ordinal"), "intent ordinal")
        reason = _text(intent.get("reason"), "intent reason")
        side_name = _text(intent.get("side"), "intent side")
        if ordinal != len(intents) + 1 or reason != ("entry" if ordinal == 1 else "exit"):
            raise LedgerConflictError("D22-A intent ordinal/reason is invalid.")
        try:
            side = OrderSide[side_name]
        except KeyError as error:
            raise LedgerConflictError("D22-A intent side is invalid.") from error
        reduce_only = intent.get("reduce_only")
        if type(reduce_only) is not bool:
            raise TypeError("D22-A reduce_only must be a boolean.")
        risk = intent.get("risk")
        if type(risk) is not dict:
            raise TypeError("D22-A risk record must be an object.")
        intent_id, client_id, _, _, expected = make_pre_submit_record(
            context=context,
            intent_ordinal=ordinal,
            reason=reason,
            decision_ordinal=_integer(intent.get("decision_ordinal"), "decision ordinal"),
            submission_ordinal=_integer(intent.get("submission_ordinal"), "submission ordinal"),
            side=side,
            quantity=Decimal(_text(intent.get("quantity"), "quantity")),
            reduce_only=reduce_only,
            current_position=position,
            risk_price=Decimal(_text(risk.get("risk_price"), "risk price")),
        )
        if pre_submit != expected:
            raise LedgerConflictError("D22-A pre-submit record does not recompute exactly.")
        intents.append(intent_id)
        client_ids.append(client_id)
        cursor += 1
        if cursor == len(records):
            break
        settlement = records[cursor]
        if settlement.kind != FILL_SETTLEMENT:
            raise LedgerConflictError("D22-A expected a fill settlement record.")
        fill = settlement.payload.get("fill")
        if type(fill) is not dict:
            raise TypeError("D22-A fill settlement must contain a fill object.")
        fill_record: dict[str, object] = {
            key: fill.get(key)
            for key in (
                "fill_ordinal",
                "observed_tick_ordinal",
                "side",
                "quantity",
                "price",
                "position_after",
            )
        }
        if (
            fill_record["fill_ordinal"] != ordinal
            or fill_record["side"] != side.name
            or Decimal(str(fill_record["quantity"]))
            != Decimal(_text(intent.get("quantity"), "quantity"))
        ):
            raise LedgerConflictError("D22-A fill does not match its order intent.")
        fills.append(fill_record)
        fill_id, expected_settlement = make_settlement_record(
            context=context,
            intent_id=intent_id,
            client_order_id=client_id,
            fill=fill_record,
            fills=fills,
        )
        if settlement != expected_settlement:
            raise LedgerConflictError("D22-A settlement does not recompute exactly.")
        position = Decimal(str(fill_record["position_after"]))
        fill_ids.append(fill_id)
        cursor += 1

    if len(set(intents)) != len(intents) or len(set(client_ids)) != len(client_ids):
        raise LedgerConflictError("D22-A stable IDs are duplicated.")
    mark_price = Decimal(str(fills[-1]["price"])) if fills else Decimal(0)
    overlay = cost_overlay(fills, mark_price=mark_price, assumptions=DEFAULT_COSTS)
    complete = len(intents) == len(fill_ids) == 2 and position == 0 and cursor == len(records)
    return LedgerProjection(
        status="COMPLETED_FLAT" if complete else "RECOVERY_REQUIRED",
        record_count=len(records),
        intent_count=len(intents),
        order_count=len(intents),
        fill_count=len(fill_ids),
        position_btc=str(position),
        cash_usdc_assumed=overlay["ending_cash_usdc_assumed"],
        equity_usdc_assumed=overlay["ending_equity_usdc_assumed"],
        net_pnl_usdc_assumed=overlay["net_pnl_usdc_assumed"],
        intent_ids=tuple(intents),
        client_order_ids=tuple(client_ids),
        fill_ids=tuple(fill_ids),
    )


class LedgerBackedD01Strategy(D01SmokeStrategy):
    """D01 decisions/risk with only a durable submit/settlement seam."""

    def __init__(self, *, context: RunContext, ledger: PaperLedger) -> None:
        super().__init__(
            D41SmokeStrategyConfig(
                order_id_tag="D01",
                instrument_id=INSTRUMENT_ID,
                order_quantity=context.config.order_quantity_btc,
                warmup_ticks=context.config.warmup_ticks,
                hold_ticks=context.config.hold_ticks,
                record_limit=2_000,
                subscribe_market_data=False,
                log_events=False,
                log_commands=False,
            ),
            run_identity_value=context.logical_run_identity,
            slice_config=context.config,
        )
        self.context = context
        self.ledger = ledger
        self.project_ids: list[tuple[str, str]] = []
        self.entry_fill_durable = False

    def _submit_market(
        self,
        *,
        side: OrderSide,
        reduce_only: bool,
        reason: str,
        decision_ordinal: int,
    ) -> None:
        if self.instrument is None:
            raise RuntimeError("D22-A strategy has no instrument.")
        quantity_value = (
            abs(self.signed_position_quantity) if reduce_only else self.config.order_quantity
        )
        intent_ordinal = len(self.order_intents) + 1
        intent_id, client_id, d01_intent_sha256, risk, record = make_pre_submit_record(
            context=self.context,
            intent_ordinal=intent_ordinal,
            reason=reason,
            decision_ordinal=decision_ordinal,
            submission_ordinal=self.tick_count,
            side=side,
            quantity=quantity_value,
            reduce_only=reduce_only,
            current_position=self.signed_position_quantity,
            risk_price=Decimal(str(self.inputs[-1]["price"])),
        )
        self.risk_decisions.append(risk)
        if d01_intent_sha256 in self.intent_keys:
            raise RuntimeError("D22-A refuses a duplicate D01 attempt intent.")
        quantity = self.instrument.make_qty(quantity_value)
        if Decimal(str(quantity)) != quantity_value:
            raise ValueError("D22-A refuses implicit quantity normalization.")

        self.ledger.append((record,))  # durable before construction/submission
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            time_in_force=TimeInForce.IOC,
            reduce_only=reduce_only,
            client_order_id=ClientOrderId(client_id),
        )
        if order.client_order_id.value != client_id:
            raise RuntimeError("Nautilus changed the stable D22-A client order ID.")
        self.intent_keys.add(d01_intent_sha256)
        self.project_ids.append((intent_id, client_id))
        self.order_intents.append(
            {
                "instrument_id": self.config.instrument_id.value,
                "intent_ordinal": intent_ordinal,
                "reason": reason,
                "decision_ordinal": decision_ordinal,
                "submission_ordinal": self.tick_count,
                "side": side.name,
                "quantity": str(quantity_value),
                "order_type": "MARKET",
                "reduce_only": reduce_only,
                "intent_sha256": d01_intent_sha256,
                "client_order_id": client_id,
            }
        )
        self.submit_order(order)

    def on_order_filled(self, event: OrderFilled) -> None:
        super().on_order_filled(event)
        index = len(self.fills) - 1
        if index >= len(self.project_ids):
            raise RuntimeError("D22-A received a fill without a durable intent.")
        intent_id, client_id = self.project_ids[index]
        if event.client_order_id.value != client_id:
            raise RuntimeError("D22-A fill has the wrong stable client order ID.")
        _, record = make_settlement_record(
            context=self.context,
            intent_id=intent_id,
            client_order_id=client_id,
            fill=self.fills[index],
            fills=self.fills[: index + 1],
        )
        self.ledger.append((record,))
        self.entry_fill_durable = len(self.fills) == 1


async def _drive_paper(
    node: TradingNode,
    strategy: LedgerBackedD01Strategy,
    ticks: tuple[Any, ...],
    *,
    crash_after_entry_fill: bool,
) -> dict[str, object]:
    run_task = asyncio.create_task(node.run_async(), name="d22a-sandbox-paper-node")
    topic = f"data.trades.{HYPERLIQUID}.{INSTRUMENT_ID.symbol.value}"
    subscribed = False
    try:
        for _ in range(1_000):
            if node.is_running():
                break
            if run_task.done():
                await run_task
            await asyncio.sleep(0)
        if not node.is_running():
            raise RuntimeError("D22-A sandbox PAPER node did not start.")
        node.kernel.msgbus.subscribe(topic, strategy.handle_trade_tick)
        subscribed = True
        for tick in ticks:
            node.kernel.data_engine.process(tick)
            for _ in range(4):
                await asyncio.sleep(0)
            if crash_after_entry_fill and strategy.entry_fill_durable:
                os._exit(CRASH_EXIT_CODE)
        for _ in range(1_000):
            if strategy.is_complete or strategy.rejections:
                break
            await asyncio.sleep(0)
        return _reports(node.trader)
    finally:
        if subscribed:
            node.kernel.msgbus.unsubscribe(topic, strategy.handle_trade_tick)
        if node.is_running():
            await node.stop_async()
        await run_task


def _run_paper_attempt(
    context: RunContext,
    ledger: PaperLedger,
    *,
    crash_after_entry_fill: bool,
) -> dict[str, object]:
    assert_paper_boundary(seconds=PAPER_SECONDS)
    strategy = LedgerBackedD01Strategy(context=context, ledger=ledger)
    loop = asyncio.new_event_loop()
    node = TradingNode(config=_paper_node_config(context.config), loop=loop)
    node.kernel.cache.add_instrument(build_nautilus_instrument())
    node.trader.add_strategy(strategy)
    node.add_exec_client_factory(HYPERLIQUID, SandboxLiveExecClientFactory)
    node.build()
    try:
        reports = loop.run_until_complete(
            asyncio.wait_for(
                _drive_paper(
                    node,
                    strategy,
                    context.dataset.ticks,
                    crash_after_entry_fill=crash_after_entry_fill,
                ),
                timeout=PAPER_SECONDS,
            )
        )
    finally:
        node.dispose()
        loop.close()
        asyncio.set_event_loop(None)
    if not strategy.is_complete:
        raise RuntimeError("D22-A sandbox did not complete one flat D01 lifecycle.")
    result = _run_result(
        run_kind="sandbox_paper",
        dataset=context.dataset,
        strategy=strategy,
        reports=reports,
        identity=context.logical_run_identity,
    )
    execution = result.get("execution")
    if type(execution) is not dict:
        raise TypeError("D22-A PAPER execution result is invalid.")
    execution.update(
        {
            "mode": "PAPER",
            "node_environment": "SANDBOX",
            "execution_client_factories": ["SandboxLiveExecClientFactory"],
            "credential_names_present_at_start": sorted(
                name for name in PROTECTED_CREDENTIAL_ENV_NAMES if os.environ.get(name)
            ),
        }
    )
    _validate_lifecycle(
        result,
        label="D22-A sandbox-PAPER",
        expected_run_kind="sandbox_paper",
        expected_identity=context.logical_run_identity,
        dataset=context.dataset,
        is_paper=True,
    )
    return result


def _summary(
    context: RunContext,
    ledger_path: Path,
    decision: str,
    before: LedgerProjection,
    after: LedgerProjection,
) -> dict[str, object]:
    return {
        "schema": D22A_SCHEMA,
        "decision": decision,
        "run_id": context.run_id,
        "logical_run_identity": context.logical_run_identity,
        "ledger_path": str(ledger_path),
        "before": before.to_dict(),
        "after": after.to_dict(),
        "claim": {
            "proved": [
                "append-only local intent/order/fill/position/cash/PnL records",
                "transactional idempotent writes and stable project-owned IDs",
                "durable-ledger recovery by rebuilding a disposable D01 sandbox",
            ],
            "not_proved": [
                "Nautilus engine-state restoration",
                "no duplicate execution across disposable sandbox attempts",
                "exactly-once venue submission or venue-authoritative reconciliation",
                "deployment, credentials, TESTNET, SHADOW, LIVE, or profitability",
            ],
        },
    }


def run_recoverable_paper(
    *,
    run_id: str,
    ledger_path: Path,
    dataset_path: Path = DEFAULT_DATASET,
    config: SliceConfig = DEFAULT_CONFIG,
    crash_process_after_entry_fill: bool = False,
) -> dict[str, object]:
    assert_local_boundary()
    context = build_run_context(run_id=run_id, dataset_path=dataset_path, config=config)
    with PaperLedger(ledger_path, context) as ledger:
        before = project_ledger(context, ledger.records())
        if before.completed_flat:
            return _summary(context, ledger_path, "NOOP_ALREADY_COMPLETE", before, before)
        with _derived_internal_paper_mode():
            paper = _run_paper_attempt(
                context,
                ledger,
                crash_after_entry_fill=crash_process_after_entry_fill,
            )
        after = project_ledger(context, ledger.records())
        overlay = paper.get("cost_overlay")
        if type(overlay) is not dict or not after.completed_flat:
            raise RuntimeError("D22-A PAPER and ledger did not complete flat.")
        if (
            after.cash_usdc_assumed != overlay.get("ending_cash_usdc_assumed")
            or after.equity_usdc_assumed != overlay.get("ending_equity_usdc_assumed")
            or after.net_pnl_usdc_assumed != overlay.get("net_pnl_usdc_assumed")
        ):
            raise RuntimeError("D22-A ledger and D01 PAPER economics do not reconcile.")
        decision = "RECOVERED_COMPLETED_FLAT" if before.record_count else "COMPLETED_FLAT"
        return _summary(context, ledger_path, decision, before, after)


def verify_ledger(
    *,
    run_id: str,
    ledger_path: Path,
    dataset_path: Path = DEFAULT_DATASET,
    config: SliceConfig = DEFAULT_CONFIG,
) -> dict[str, object]:
    context = build_run_context(run_id=run_id, dataset_path=dataset_path, config=config)
    with PaperLedger(ledger_path, context, read_only=True) as ledger:
        projection = project_ledger(context, ledger.records())
    return {
        "schema": D22A_SCHEMA,
        "run_id": run_id,
        "logical_run_identity": context.logical_run_identity,
        "engine_or_order_construction": False,
        "projection": projection.to_dict(),
    }
