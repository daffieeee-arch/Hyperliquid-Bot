"""Minimal D01 BTC-PERP replay-to-sandbox-PAPER composition.

This is a bounded local product proof, not a production trading core. It reuses the
frozen D41 Nautilus mechanics while keeping project-owned mode, input, risk,
economics, run identity, evidence, and restart boundaries explicit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from fit_gates.d41_nautilus.fit_gate import (
    INSTRUMENT_ID,
    ORDER_QUANTITY,
    PRICE_INCREMENT,
    PROTECTED_CREDENTIAL_ENV_NAMES,
    SIZE_INCREMENT,
    STARTING_CASH,
    CostAssumptions,
    D41SmokeStrategy,
    D41SmokeStrategyConfig,
    assert_exact_runtime_pin,
    assert_paper_boundary,
    build_nautilus_instrument,
    cost_overlay,
    dataframe_records,
    envelope_to_trade_tick,
    existing_btc_contract,
    prepare_new_output_directory,
    record_to_envelope,
    sha256_json,
    write_json,
)
from nautilus_trader.adapters.hyperliquid import HYPERLIQUID, HYPERLIQUID_VENUE
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine  # type: ignore[import-not-found]
from nautilus_trader.backtest.models import FillModel, LatencyModel, MakerTakerFeeModel
from nautilus_trader.common import Environment
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import TradeTick  # type: ignore[import-not-found]
from nautilus_trader.model.enums import AccountType, BookType, OmsType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import TraderId  # type: ignore[import-not-found]
from nautilus_trader.model.objects import Money  # type: ignore[import-not-found]

from hyperliquid_bot.local_mode import require_local_paper_mode

D01_SCHEMA: Final = "d01-btc-perp-slice-v1"
DATASET_SCHEMA: Final = "d41-public-trades-v1"
COMPLETION_SCHEMA: Final = "d01-completed-flat-run-v1"
DEFAULT_DATASET_SHA256: Final = "d58ed2c701bff47d57503e554439df97c7a97e165e12c0842bc065792de372a3"
DEFAULT_EVENTS_SHA256: Final = "3ac0f4c0a59d7c1a79e017354b7c19c941c4ea97b2035b123722a71df2583088"
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_DATASET: Final = (
    REPOSITORY_ROOT / "fit_gates" / "d41_nautilus" / "runs" / "20260830T011412Z" / "dataset.json"
)
PAPER_SECONDS: Final = 30


class RiskRejectedError(RuntimeError):
    """Raised before order construction when the D01 risk boundary rejects an intent."""


@dataclass(frozen=True, slots=True)
class SliceConfig:
    """One deliberately fixed smoke configuration, not a generic strategy schema."""

    order_quantity_btc: Decimal = ORDER_QUANTITY
    warmup_ticks: int = 3
    hold_ticks: int = 5
    starting_cash_usdc: Decimal = STARTING_CASH
    max_abs_position_btc: Decimal = ORDER_QUANTITY
    max_entry_notional_usdc: Decimal = Decimal("15")
    assumed_stop_distance_fraction: Decimal = Decimal("0.02")
    max_assumed_loss_usdc: Decimal = Decimal("0.25")

    def to_dict(self) -> dict[str, object]:
        return {
            "instrument_id": INSTRUMENT_ID.value,
            "order_quantity_btc": str(self.order_quantity_btc),
            "warmup_ticks": self.warmup_ticks,
            "hold_ticks": self.hold_ticks,
            "starting_cash_usdc": str(self.starting_cash_usdc),
            "max_abs_position_btc": str(self.max_abs_position_btc),
            "max_entry_notional_usdc": str(self.max_entry_notional_usdc),
            "assumed_stop_distance_fraction": str(self.assumed_stop_distance_fraction),
            "max_assumed_loss_usdc": str(self.max_assumed_loss_usdc),
            "paper_duration_seconds": PAPER_SECONDS,
        }


DEFAULT_CONFIG: Final = SliceConfig()
DEFAULT_COSTS: Final = CostAssumptions()


@dataclass(frozen=True, slots=True)
class LoadedDataset:
    path: Path
    file_sha256: str
    events_sha256: str
    capture_harness: dict[str, object]
    ticks: tuple[TradeTick, ...]
    max_price: Decimal
    max_age_ns: int


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{path.name} must contain one JSON object.")
    return value


def _require_exact_increment(value: Decimal, *, increment: Decimal, field_name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name} must be a finite positive decimal.")
    if value.quantize(increment) != value:
        raise ValueError(f"{field_name} is not exactly aligned to increment {increment}.")


def _signed_quantity(side: OrderSide, quantity: Decimal) -> Decimal:
    return quantity if side is OrderSide.BUY else -quantity


def assert_local_boundary(
    *,
    environ: Mapping[str, str] | None = None,
    seconds: int = PAPER_SECONDS,
) -> None:
    """Validate both existing local mode and the internal D41 sandbox boundary."""

    source = os.environ if environ is None else environ
    require_local_paper_mode(source.get("TRADING_MODE"))
    internal_mode = source.get("D41_EXECUTION_MODE")
    if internal_mode not in {None, "PAPER"}:
        raise RuntimeError("D01 refuses a non-PAPER internal execution mode.")
    effective = dict(source)
    effective["D41_EXECUTION_MODE"] = "PAPER"
    assert_paper_boundary(seconds=seconds, environ=effective)
    assert_exact_runtime_pin()


@contextmanager
def _derived_internal_paper_mode() -> Iterator[None]:
    previous = os.environ.get("D41_EXECUTION_MODE")
    if previous not in {None, "PAPER"}:
        raise RuntimeError("D01 refuses a non-PAPER internal execution mode.")
    os.environ["D41_EXECUTION_MODE"] = "PAPER"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("D41_EXECUTION_MODE", None)
        else:
            os.environ["D41_EXECUTION_MODE"] = previous


def load_bounded_dataset(
    path: Path = DEFAULT_DATASET,
    *,
    expected_file_sha256: str | None = DEFAULT_DATASET_SHA256,
) -> LoadedDataset:
    """Reload the committed primary v2 records through the current thin adapter.

    The embedded D41 harness identity remains capture-time provenance. D01 binds its
    execution separately to the exact dataset bytes, primary-event digest, and its
    own source identity instead of pretending the historical capture harness is the
    current D01 runtime.
    """

    file_sha256 = _sha256_file(path)
    if expected_file_sha256 is not None and file_sha256 != expected_file_sha256:
        raise ValueError("D01 bounded dataset file digest mismatch.")
    dataset = _read_object(path)
    if dataset.get("schema") != DATASET_SCHEMA:
        raise ValueError("D01 requires the published D41 v2 dataset schema.")
    if dataset.get("instrument_id") != INSTRUMENT_ID.value:
        raise ValueError("D01 accepts only the exact BTC perpetual instrument.")
    if dataset.get("venue_neutral_contract_id") != existing_btc_contract().canonical_instrument_id:
        raise ValueError("D01 dataset has the wrong venue-neutral contract identity.")
    if dataset.get("source_subscription") != {"type": "trades", "coin": "BTC"}:
        raise ValueError("D01 dataset subscription is outside the one BTC-PERP scope.")
    if dataset.get("capture_order_preserved") is not True:
        raise ValueError("D01 requires preserved capture order.")
    if dataset.get("hyperliquid_tid_used_as_sequence") is not False:
        raise ValueError("D01 refuses an unsupported Hyperliquid sequence claim.")

    capture_integrity = dataset.get("capture_integrity")
    if type(capture_integrity) is not dict:
        raise TypeError("D01 dataset capture_integrity must be an object.")
    if capture_integrity.get("claim") != "local_capture_integrity_only":
        raise ValueError("D01 requires the bounded local-capture-integrity claim.")
    if capture_integrity.get("exchange_origin_cryptographically_proven") is not False:
        raise ValueError("D01 refuses a stronger exchange-origin claim.")

    capture_metadata = dataset.get("capture_metadata")
    if type(capture_metadata) is not dict:
        raise TypeError("D01 dataset capture_metadata must be an object.")
    if capture_metadata.get("sticky_gap") is not False:
        raise ValueError("D01 refuses a sticky-gap capture.")
    if capture_metadata.get("collector_state_after_shutdown") != "stopped":
        raise ValueError("D01 requires a cleanly stopped bounded collector.")

    records = dataset.get("events")
    if type(records) is not list or len(records) < 12:
        raise ValueError("D01 requires at least twelve primary v2 events.")
    if dataset.get("event_count") != len(records):
        raise ValueError("D01 dataset event count is inconsistent.")
    events_sha256 = sha256_json(records)
    if dataset.get("events_sha256") != events_sha256:
        raise ValueError("D01 dataset primary-event digest mismatch.")
    if expected_file_sha256 == DEFAULT_DATASET_SHA256 and events_sha256 != DEFAULT_EVENTS_SHA256:
        raise ValueError("D01 default primary-event digest mismatch.")
    if capture_metadata.get("accepted_event_count") != len(records):
        raise ValueError("D01 accepted-event count is inconsistent.")

    ticks: list[TradeTick] = []
    source_event_ids: set[str] = set()
    prior_ts_init: int | None = None
    prior_monotonic: int | None = None
    max_age_ns = 0
    max_price = Decimal(0)
    for ordinal, raw_record in enumerate(records, start=1):
        if type(raw_record) is not dict:
            raise TypeError("D01 primary event must be an object.")
        if raw_record.get("capture_ordinal") != ordinal:
            raise ValueError("D01 capture ordinals are not contiguous.")
        envelope = record_to_envelope(raw_record)
        if prior_monotonic is not None and envelope.received_monotonic_ns < prior_monotonic:
            raise ValueError("D01 receive monotonic time moved backwards.")
        if envelope.source_event_id in source_event_ids:
            raise ValueError("D01 dataset contains a duplicate source event ID.")
        tick = envelope_to_trade_tick(envelope, prior_ts_init=prior_ts_init)
        receipt_delta = envelope.received_time - envelope.event_time
        age_ns = (
            receipt_delta.days * 86_400 + receipt_delta.seconds
        ) * 1_000_000_000 + receipt_delta.microseconds * 1_000
        max_age_ns = max(max_age_ns, age_ns)
        max_price = max(max_price, Decimal(str(tick.price)))
        ticks.append(tick)
        source_event_ids.add(envelope.source_event_id)
        prior_monotonic = envelope.received_monotonic_ns
        prior_ts_init = tick.ts_init

    capture_harness = dataset.get("harness")
    if type(capture_harness) is not dict:
        raise TypeError("D01 dataset capture harness must be an object.")
    return LoadedDataset(
        path=path,
        file_sha256=file_sha256,
        events_sha256=events_sha256,
        capture_harness=capture_harness,
        ticks=tuple(ticks),
        max_price=max_price,
        max_age_ns=max_age_ns,
    )


def source_identity() -> dict[str, object]:
    d01_root = Path(__file__).resolve().parent
    d41_root = REPOSITORY_ROOT / "fit_gates" / "d41_nautilus"
    files = {
        "vertical_slices/d01_btc_perp/slice.py": _sha256_file(d01_root / "slice.py"),
        "vertical_slices/d01_btc_perp/run_slice.py": _sha256_file(d01_root / "run_slice.py"),
        "fit_gates/d41_nautilus/fit_gate.py": _sha256_file(d41_root / "fit_gate.py"),
        "fit_gates/d41_nautilus/requirements.lock": _sha256_file(d41_root / "requirements.lock"),
    }
    return {"algorithm": "sha256", "files": files, "digest": sha256_json(files)}


def run_identity(
    *,
    run_id: str,
    dataset: LoadedDataset,
    config: SliceConfig,
    source: dict[str, object],
) -> str:
    if (
        not run_id
        or len(run_id) > 64
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in run_id)
    ):
        raise ValueError(
            "run_id must be 1-64 lowercase ASCII letters, digits, dot, dash or underscore."
        )
    payload = {
        "schema": D01_SCHEMA,
        "run_id": run_id,
        "instrument_id": INSTRUMENT_ID.value,
        "dataset_file_sha256": dataset.file_sha256,
        "dataset_events_sha256": dataset.events_sha256,
        "config": config.to_dict(),
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "source_digest": source["digest"],
    }
    return sha256_json(payload)


def evaluate_order_risk(
    *,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal,
    reduce_only: bool,
    current_position: Decimal,
    config: SliceConfig,
) -> dict[str, object]:
    """Return one explicit fail-closed smoke-risk decision without normalizing values."""

    _require_exact_increment(quantity, increment=SIZE_INCREMENT, field_name="order quantity")
    _require_exact_increment(price, increment=PRICE_INCREMENT, field_name="risk price")
    if type(reduce_only) is not bool:
        raise TypeError("reduce_only must be a boolean.")
    projected_position = current_position + _signed_quantity(side, quantity)
    notional = quantity * price
    total_cost_bps = (
        DEFAULT_COSTS.taker_fee_bps + DEFAULT_COSTS.half_spread_bps + DEFAULT_COSTS.slippage_bps
    ) * Decimal(2)
    assumed_loss = (
        notional * config.assumed_stop_distance_fraction
        + notional * total_cost_bps / Decimal(10000)
        + DEFAULT_COSTS.funding_payment
    )
    reasons: list[str] = []
    if reduce_only:
        if current_position == 0:
            reasons.append("reduce-only exit requires an open position")
        if abs(quantity) != abs(current_position):
            reasons.append("reduce-only exit must flatten the exact open quantity")
        if abs(projected_position) >= abs(current_position):
            reasons.append("reduce-only exit must reduce absolute exposure")
    else:
        if current_position != 0:
            reasons.append("D01 permits only one flat-to-position entry")
        if abs(projected_position) > config.max_abs_position_btc:
            reasons.append("projected BTC position exceeds the hard exposure cap")
        if notional > config.max_entry_notional_usdc:
            reasons.append("entry notional exceeds the hard USDC exposure cap")
        if assumed_loss > config.max_assumed_loss_usdc:
            reasons.append("entry assumed stop-plus-cost loss exceeds the smoke budget")
    return {
        "approved": not reasons,
        "reasons": reasons,
        "side": side.name,
        "quantity_btc": str(quantity),
        "risk_price": str(price),
        "reduce_only": reduce_only,
        "position_before_btc": str(current_position),
        "projected_position_btc": str(projected_position),
        "notional_usdc_assumed": str(notional),
        "assumed_stop_plus_round_trip_cost_loss_usdc": str(assumed_loss),
        "max_abs_position_btc": str(config.max_abs_position_btc),
        "max_entry_notional_usdc": str(config.max_entry_notional_usdc),
        "max_assumed_loss_usdc": str(config.max_assumed_loss_usdc),
    }


class D01SmokeStrategy(D41SmokeStrategy):
    """The D41 smoke lifecycle with a project-owned pre-submit risk boundary."""

    def __init__(
        self,
        config: D41SmokeStrategyConfig,
        *,
        run_identity_value: str,
        slice_config: SliceConfig,
    ) -> None:
        super().__init__(config)
        self.run_identity_value = run_identity_value
        self.slice_config = slice_config
        self.risk_decisions: list[dict[str, object]] = []
        self.intent_keys: set[str] = set()

    def _submit_market(
        self,
        *,
        side: OrderSide,
        reduce_only: bool,
        reason: str,
        decision_ordinal: int,
    ) -> None:
        if self.instrument is None:
            raise RuntimeError("D01 strategy has no instrument.")
        quantity_value = (
            abs(self.signed_position_quantity) if reduce_only else self.config.order_quantity
        )
        current_price = Decimal(str(self.inputs[-1]["price"]))
        risk = evaluate_order_risk(
            side=side,
            quantity=quantity_value,
            price=current_price,
            reduce_only=reduce_only,
            current_position=self.signed_position_quantity,
            config=self.slice_config,
        )
        intent_payload = {
            "run_identity": self.run_identity_value,
            "instrument_id": self.config.instrument_id.value,
            "intent_ordinal": len(self.order_intents) + 1,
            "reason": reason,
            "decision_ordinal": decision_ordinal,
            "submission_ordinal": self.tick_count,
            "side": side.name,
            "quantity": str(quantity_value),
            "order_type": "MARKET",
            "reduce_only": reduce_only,
        }
        intent_key = sha256_json(intent_payload)
        risk["intent_sha256"] = intent_key
        self.risk_decisions.append(risk)
        if not risk["approved"]:
            raise RiskRejectedError(f"D01 risk rejected {reason}: {risk['reasons']}")
        if intent_key in self.intent_keys:
            raise RuntimeError("D01 refuses a duplicate project-owned intent key.")

        quantity = self.instrument.make_qty(quantity_value)
        if Decimal(str(quantity)) != quantity_value:
            raise ValueError("D01 refuses implicit order quantity normalization.")
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            time_in_force=TimeInForce.IOC,
            reduce_only=reduce_only,
        )
        self.intent_keys.add(intent_key)
        self.order_intents.append(
            {
                **{key: value for key, value in intent_payload.items() if key != "run_identity"},
                "intent_sha256": intent_key,
                "client_order_id": order.client_order_id.value,
            }
        )
        self.submit_order(order)


def _make_strategy(
    *,
    identity: str,
    config: SliceConfig,
    subscribe_market_data: bool,
) -> D01SmokeStrategy:
    if config.starting_cash_usdc != STARTING_CASH:
        raise ValueError("D01 starting cash must match the bounded Nautilus account.")
    return D01SmokeStrategy(
        D41SmokeStrategyConfig(
            order_id_tag="D01",
            instrument_id=INSTRUMENT_ID,
            order_quantity=config.order_quantity_btc,
            warmup_ticks=config.warmup_ticks,
            hold_ticks=config.hold_ticks,
            record_limit=2_000,
            subscribe_market_data=subscribe_market_data,
            log_events=False,
            log_commands=False,
        ),
        run_identity_value=identity,
        slice_config=config,
    )


def _reports(trader: Any) -> dict[str, object]:
    return {
        "orders": dataframe_records(trader.generate_orders_report()),
        "order_fills": dataframe_records(trader.generate_order_fills_report()),
        "fills": dataframe_records(trader.generate_fills_report()),
        "positions": dataframe_records(trader.generate_positions_report()),
        "account": dataframe_records(trader.generate_account_report(HYPERLIQUID_VENUE)),
    }


def _business(strategy: D01SmokeStrategy) -> dict[str, object]:
    return {
        "strategy_class": f"{type(strategy).__module__}.{type(strategy).__qualname__}",
        "strategy_config": {
            "instrument_id": strategy.config.instrument_id.value,
            "order_quantity": str(strategy.config.order_quantity),
            "warmup_ticks": strategy.config.warmup_ticks,
            "hold_ticks": strategy.config.hold_ticks,
        },
        "tick_count": strategy.tick_count,
        "input_sha256": sha256_json(strategy.inputs),
        "signals": strategy.signals,
        "risk_decisions": strategy.risk_decisions,
        "order_intents": strategy.order_intents,
        "fills": strategy.fills,
        "position_events": strategy.position_events,
        "order_terminal_events": strategy.order_terminal_events,
        "rejections": strategy.rejections,
        "final_position_quantity": str(strategy.signed_position_quantity),
        "complete": strategy.is_complete,
    }


def _selected_fill_records(business: dict[str, object]) -> list[dict[str, object]]:
    fills = business.get("fills")
    if type(fills) is not list or any(type(fill) is not dict for fill in fills):
        raise TypeError("D01 business fills must be a list of objects.")
    return fills


def _run_result(
    *,
    run_kind: str,
    dataset: LoadedDataset,
    strategy: D01SmokeStrategy,
    reports: dict[str, object],
    identity: str,
) -> dict[str, object]:
    last_price = Decimal(str(dataset.ticks[-1].price))
    business = _business(strategy)
    overlay = cost_overlay(
        _selected_fill_records(business),
        mark_price=last_price,
        assumptions=DEFAULT_COSTS,
    )
    deterministic_payload = {
        "schema": D01_SCHEMA,
        "run_identity": identity,
        "dataset_events_sha256": dataset.events_sha256,
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "business": business,
        "cost_overlay": overlay,
    }
    return {
        "schema": D01_SCHEMA,
        "run_kind": run_kind,
        "run_identity": identity,
        "dataset": {
            "file_sha256": dataset.file_sha256,
            "events_sha256": dataset.events_sha256,
            "event_count": len(dataset.ticks),
            "max_input_age_ns": dataset.max_age_ns,
            "capture_claim": "local_capture_integrity_only",
        },
        "execution": {
            "instrument_id": INSTRUMENT_ID.value,
            "native_accounting_currency": "USD",
            "native_accounting_is_usdc_proxy": True,
            "registered_venue_execution_client": False,
        },
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "business": business,
        "cost_overlay": overlay,
        "nautilus_reports": reports,
        "deterministic_payload_sha256": sha256_json(deterministic_payload),
    }


def _run_replay_once(
    *,
    label: str,
    dataset: LoadedDataset,
    identity: str,
    config: SliceConfig,
) -> dict[str, object]:
    strategy = _make_strategy(identity=identity, config=config, subscribe_market_data=True)
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("D01-001"),
            logging=LoggingConfig(log_level="ERROR", use_pyo3=False),
        )
    )
    try:
        engine.add_venue(
            venue=HYPERLIQUID_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money.from_str(f"{config.starting_cash_usdc} USD")],
            base_currency=USD,
            default_leverage=Decimal(1),
            fill_model=FillModel(
                prob_fill_on_limit=1.0,
                prob_slippage=0.0,
                random_seed=42,
            ),
            fee_model=MakerTakerFeeModel(),
            latency_model=LatencyModel(0),
            book_type=BookType.L1_MBP,
            bar_execution=False,
            trade_execution=True,
            use_random_ids=False,
        )
        engine.add_instrument(build_nautilus_instrument())
        engine.add_data(list(dataset.ticks), validate=True, sort=False)
        engine.sort_data()
        engine.add_strategy(strategy)
        engine.run()
        if not strategy.is_complete:
            raise RuntimeError(f"D01 {label} did not complete one flat lifecycle.")
        return _run_result(
            run_kind=label,
            dataset=dataset,
            strategy=strategy,
            reports=_reports(engine.trader),
            identity=identity,
        )
    finally:
        engine.dispose()


def _paper_node_config(config: SliceConfig) -> TradingNodeConfig:
    provider = InstrumentProviderConfig(load_ids=frozenset({INSTRUMENT_ID}))
    return TradingNodeConfig(
        environment=Environment.SANDBOX,
        trader_id=TraderId("D01-001"),
        logging=LoggingConfig(log_level="ERROR", use_pyo3=True),
        exec_engine=LiveExecEngineConfig(reconciliation=False),
        data_clients={},
        exec_clients={
            HYPERLIQUID: SandboxExecutionClientConfig(
                venue=HYPERLIQUID,
                starting_balances=[f"{config.starting_cash_usdc} USD"],
                base_currency="USD",
                oms_type="NETTING",
                account_type="MARGIN",
                default_leverage=Decimal(1),
                book_type="L1_MBP",
                frozen_account=False,
                bar_execution=False,
                trade_execution=True,
                reject_stop_orders=True,
                use_position_ids=True,
                use_random_ids=False,
                use_reduce_only=True,
                instrument_provider=provider,
            )
        },
        timeout_connection=20.0,
        timeout_reconciliation=5.0,
        timeout_portfolio=5.0,
        timeout_disconnection=10.0,
        timeout_post_stop=2.0,
    )


async def _drive_paper(
    node: TradingNode,
    strategy: D01SmokeStrategy,
    ticks: tuple[TradeTick, ...],
) -> dict[str, object]:
    run_task = asyncio.create_task(node.run_async(), name="d01-sandbox-paper-node")
    trade_topic = f"data.trades.{HYPERLIQUID}.{INSTRUMENT_ID.symbol.value}"
    subscribed = False
    try:
        for _ in range(1_000):
            if node.is_running():
                break
            if run_task.done():
                await run_task
            await asyncio.sleep(0)
        if not node.is_running():
            raise RuntimeError("D01 sandbox PAPER node did not start.")
        node.kernel.msgbus.subscribe(trade_topic, strategy.handle_trade_tick)
        subscribed = True
        for tick in ticks:
            node.kernel.data_engine.process(tick)
            for _ in range(4):
                await asyncio.sleep(0)
        for _ in range(1_000):
            if strategy.is_complete or strategy.rejections:
                break
            await asyncio.sleep(0)
        return _reports(node.trader)
    finally:
        if subscribed:
            node.kernel.msgbus.unsubscribe(trade_topic, strategy.handle_trade_tick)
        if node.is_running():
            await node.stop_async()
        await run_task


def _run_paper(
    *,
    dataset: LoadedDataset,
    identity: str,
    config: SliceConfig,
) -> dict[str, object]:
    assert_paper_boundary(seconds=PAPER_SECONDS)
    strategy = _make_strategy(identity=identity, config=config, subscribe_market_data=False)
    loop = asyncio.new_event_loop()
    node = TradingNode(config=_paper_node_config(config), loop=loop)
    node.kernel.cache.add_instrument(build_nautilus_instrument())
    node.trader.add_strategy(strategy)
    node.add_exec_client_factory(HYPERLIQUID, SandboxLiveExecClientFactory)
    node.build()
    try:
        reports = loop.run_until_complete(
            asyncio.wait_for(
                _drive_paper(node, strategy, dataset.ticks),
                timeout=PAPER_SECONDS,
            )
        )
    finally:
        node.dispose()
        asyncio.set_event_loop(None)
    if not strategy.is_complete:
        raise RuntimeError("D01 sandbox PAPER did not complete one flat lifecycle.")
    result = _run_result(
        run_kind="sandbox_paper",
        dataset=dataset,
        strategy=strategy,
        reports=reports,
        identity=identity,
    )
    execution = result["execution"]
    if type(execution) is not dict:
        raise TypeError("D01 execution artifact is invalid.")
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
    return result


def _records(value: object, *, field_name: str) -> list[dict[str, object]]:
    if type(value) is not list or any(type(record) is not dict for record in value):
        raise TypeError(f"{field_name} must be a list of objects.")
    return value


def _intent_semantics(intent: dict[str, object]) -> dict[str, object]:
    timing_and_identity = {
        "client_order_id",
        "intent_sha256",
        "decision_ordinal",
        "submission_ordinal",
    }
    return {key: value for key, value in intent.items() if key not in timing_and_identity}


def _fill_semantics(fill: dict[str, object]) -> dict[str, object]:
    fields = ("fill_ordinal", "side", "quantity", "price", "position_after")
    return {field: fill.get(field) for field in fields}


def _signal_semantics(signal: dict[str, object]) -> dict[str, object]:
    timing = {"decision_ordinal", "entry_fill_ordinal"}
    return {key: value for key, value in signal.items() if key not in timing}


def _risk_semantics(decision: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in decision.items() if key != "intent_sha256"}


def _validate_lifecycle(
    artifact: dict[str, object],
    *,
    label: str,
    expected_run_kind: str,
    expected_identity: str,
    dataset: LoadedDataset,
    is_paper: bool,
) -> dict[str, object]:
    expected_dataset = {
        "file_sha256": dataset.file_sha256,
        "events_sha256": dataset.events_sha256,
        "event_count": len(dataset.ticks),
        "max_input_age_ns": dataset.max_age_ns,
        "capture_claim": "local_capture_integrity_only",
    }
    expected_execution: dict[str, object] = {
        "instrument_id": INSTRUMENT_ID.value,
        "native_accounting_currency": "USD",
        "native_accounting_is_usdc_proxy": True,
        "registered_venue_execution_client": False,
    }
    if is_paper:
        expected_execution.update(
            {
                "mode": "PAPER",
                "node_environment": "SANDBOX",
                "execution_client_factories": ["SandboxLiveExecClientFactory"],
                "credential_names_present_at_start": [],
            }
        )
    if (
        artifact.get("schema") != D01_SCHEMA
        or artifact.get("run_kind") != expected_run_kind
        or artifact.get("run_identity") != expected_identity
        or artifact.get("dataset") != expected_dataset
        or artifact.get("execution") != expected_execution
        or artifact.get("cost_assumptions") != DEFAULT_COSTS.to_dict()
    ):
        raise ValueError(f"{label} run, dataset, mode, or economics metadata is invalid.")
    business = artifact.get("business")
    reports = artifact.get("nautilus_reports")
    if type(business) is not dict or type(reports) is not dict:
        raise TypeError(f"{label} business/reports must be objects.")
    intents = _records(business.get("order_intents"), field_name=f"{label}.order_intents")
    risk = _records(business.get("risk_decisions"), field_name=f"{label}.risk_decisions")
    fills = _records(business.get("fills"), field_name=f"{label}.fills")
    orders = _records(reports.get("orders"), field_name=f"{label}.orders")
    order_fills = _records(reports.get("order_fills"), field_name=f"{label}.order_fills")
    primary_fills = _records(reports.get("fills"), field_name=f"{label}.primary_fills")
    if not (
        business.get("complete") is True
        and business.get("final_position_quantity") in {"0", "0.00000"}
        and len(intents) == len(risk) == len(fills) == len(orders) == len(order_fills) == 2
        and len(primary_fills) == 2
    ):
        raise ValueError(f"{label} is not one complete two-order flat lifecycle.")
    keys = [decision.get("intent_sha256") for decision in risk]
    if any(type(key) is not str for key in keys) or len(set(keys)) != 2:
        raise ValueError(f"{label} project intent keys are invalid or duplicated.")
    if any(decision.get("approved") is not True for decision in risk):
        raise ValueError(f"{label} contains a rejected risk decision.")
    if [intent.get("intent_sha256") for intent in intents] != keys:
        raise ValueError(f"{label} risk decisions are not linked to order intents.")
    if [intent.get("reason") for intent in intents] != ["entry", "exit"]:
        raise ValueError(f"{label} intent reasons are invalid.")
    if intents[0].get("reduce_only") is not False or intents[1].get("reduce_only") is not True:
        raise ValueError(f"{label} entry/exit reduce-only semantics are invalid.")
    client_ids = [intent.get("client_order_id") for intent in intents]
    if any(type(client_id) is not str for client_id in client_ids) or len(set(client_ids)) != 2:
        raise ValueError(f"{label} client order IDs are invalid or duplicated.")
    for intent, fill, order, order_fill, primary_fill in zip(
        intents,
        fills,
        orders,
        order_fills,
        primary_fills,
        strict=True,
    ):
        if (
            order.get("client_order_id") != intent.get("client_order_id")
            or order.get("status") != "FILLED"
            or order.get("type") != "MARKET"
            or order.get("side") != intent.get("side")
            or order_fill.get("client_order_id") != intent.get("client_order_id")
            or primary_fill.get("client_order_id") != intent.get("client_order_id")
            or fill.get("side") != intent.get("side")
            or Decimal(str(fill.get("quantity"))) != Decimal(str(intent.get("quantity")))
        ):
            raise ValueError(f"{label} order/fill linkage is inconsistent.")
    overlay = artifact.get("cost_overlay")
    if type(overlay) is not dict:
        raise TypeError(f"{label} cost overlay must be an object.")
    recomputed_overlay = cost_overlay(
        fills,
        mark_price=Decimal(str(overlay.get("mark_price"))),
        assumptions=DEFAULT_COSTS,
    )
    if overlay != recomputed_overlay:
        raise ValueError(f"{label} cash/PnL economics do not recompute.")
    if recomputed_overlay["final_position_btc"] not in {"0", "0.00000"}:
        raise ValueError(f"{label} reconstructed position is not flat.")
    deterministic_payload = {
        "schema": D01_SCHEMA,
        "run_identity": expected_identity,
        "dataset_events_sha256": dataset.events_sha256,
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "business": business,
        "cost_overlay": overlay,
    }
    if artifact.get("deterministic_payload_sha256") != sha256_json(deterministic_payload):
        raise ValueError(f"{label} deterministic business digest does not recompute.")
    return {
        "intent_keys": keys,
        "client_order_ids": client_ids,
        "cost_overlay": recomputed_overlay,
        "intent_semantics": [_intent_semantics(intent) for intent in intents],
        "fill_semantics": [_fill_semantics(fill) for fill in fills],
        "risk_semantics": [_risk_semantics(decision) for decision in risk],
        "strategy_class": business.get("strategy_class"),
        "strategy_config": business.get("strategy_config"),
        "signals": business.get("signals"),
        "signal_semantics": [
            _signal_semantics(signal)
            for signal in _records(business.get("signals"), field_name=f"{label}.signals")
        ],
        "input_sha256": business.get("input_sha256"),
        "timing": {
            "signal_decision_ordinals": [
                signal.get("decision_ordinal") for signal in business["signals"]
            ],
            "intent_submission_ordinals": [intent.get("submission_ordinal") for intent in intents],
            "fill_observed_ordinals": [fill.get("observed_tick_ordinal") for fill in fills],
        },
    }


def _completion_payload(
    *,
    run_id: str,
    identity: str,
    dataset: LoadedDataset,
    config: SliceConfig,
    source: dict[str, object],
    claim_path: Path,
    replay_one_path: Path,
    replay_two_path: Path,
    paper_path: Path,
    replay_one: dict[str, object],
    replay_two: dict[str, object],
    paper: dict[str, object],
) -> dict[str, object]:
    claim = _read_object(claim_path)
    expected_claim = _run_claim_payload(
        run_id=run_id,
        identity=identity,
        dataset=dataset,
        config=config,
        source=source,
    )
    if claim != expected_claim:
        raise ValueError("D01 run claim does not recompute exactly.")
    first = _validate_lifecycle(
        replay_one,
        label="replay-1",
        expected_run_kind="deterministic_replay_1",
        expected_identity=identity,
        dataset=dataset,
        is_paper=False,
    )
    second = _validate_lifecycle(
        replay_two,
        label="replay-2",
        expected_run_kind="deterministic_replay_2",
        expected_identity=identity,
        dataset=dataset,
        is_paper=False,
    )
    live = _validate_lifecycle(
        paper,
        label="sandbox-PAPER",
        expected_run_kind="sandbox_paper",
        expected_identity=identity,
        dataset=dataset,
        is_paper=True,
    )
    if replay_one.get("deterministic_payload_sha256") != replay_two.get(
        "deterministic_payload_sha256"
    ):
        raise ValueError("D01 replay deterministic payloads differ.")
    replay_fields = (
        "intent_keys",
        "cost_overlay",
        "intent_semantics",
        "fill_semantics",
        "risk_semantics",
        "strategy_class",
        "strategy_config",
        "signals",
        "input_sha256",
    )
    for field_name in replay_fields:
        if first[field_name] != second[field_name]:
            raise ValueError(f"D01 replay {field_name} outcomes differ.")
    paper_equivalence_fields = (
        "cost_overlay",
        "intent_semantics",
        "fill_semantics",
        "risk_semantics",
        "strategy_class",
        "strategy_config",
        "signal_semantics",
        "input_sha256",
    )
    for field_name in paper_equivalence_fields:
        if first[field_name] != live[field_name]:
            raise ValueError(f"D01 replay/PAPER {field_name} outcomes differ.")
    artifacts = {
        "run-claim.json": _sha256_file(claim_path),
        "replay-1.json": _sha256_file(replay_one_path),
        "replay-2.json": _sha256_file(replay_two_path),
        "paper.json": _sha256_file(paper_path),
    }
    overlay = live["cost_overlay"]
    if type(overlay) is not dict:
        raise TypeError("D01 recomputed PAPER cost overlay must be an object.")
    return {
        "schema": COMPLETION_SCHEMA,
        "status": "COMPLETED_FLAT",
        "decision": "LOCAL_VERTICAL_SLICE_PASSED",
        "run_id": run_id,
        "run_identity": identity,
        "route": (
            "MarketEventEnvelope-v2 -> exact adapter -> D01SmokeStrategy -> "
            "project risk -> Nautilus replay and credentialless sandbox PAPER"
        ),
        "instrument_id": INSTRUMENT_ID.value,
        "source_identity": source,
        "dataset": {
            "path": str(dataset.path.relative_to(REPOSITORY_ROOT)),
            "file_sha256": dataset.file_sha256,
            "events_sha256": dataset.events_sha256,
            "event_count": len(dataset.ticks),
            "capture_claim": "local_capture_integrity_only",
            "capture_harness": dataset.capture_harness,
        },
        "config": config.to_dict(),
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "artifacts": artifacts,
        "recomputed": {
            "two_identical_replays": True,
            "same_strategy_risk_code_completed_equivalent_paper_lifecycle": True,
            "replay_intent_keys": first["intent_keys"],
            "completed_intent_keys": live["intent_keys"],
            "paper_client_order_ids": live["client_order_ids"],
            "order_count": 2,
            "fill_count": 2,
            "final_position_btc": overlay["final_position_btc"],
            "ending_cash_usdc_assumed": overlay["ending_cash_usdc_assumed"],
            "ending_equity_usdc_assumed": overlay["ending_equity_usdc_assumed"],
            "net_pnl_usdc_assumed": overlay["net_pnl_usdc_assumed"],
            "execution_timing_observation": {
                "replay": first["timing"],
                "sandbox_paper": live["timing"],
                "interpretation": (
                    "Sandbox asynchronous fill callbacks shift observed ordinals while "
                    "the strategy/risk semantics and final economics remain equivalent."
                ),
            },
        },
        "restart_boundary": {
            "completed_run_action": "NOOP_ALREADY_COMPLETE",
            "new_submission_intent_keys": [],
            "existing_or_incomplete_run_action": "FAIL_CLOSED_NO_RESUME",
            "claim": "completed-flat local run only; no mid-run recovery or venue reconciliation",
        },
        "limitations": [
            "This is engineering proof, not alpha, profitability, or promotion evidence.",
            (
                "Nautilus 1.231.0 remains isolated behind D01 and D41 project code; "
                "it is not a root dependency."
            ),
            "Sandbox order, cash, and position state is in memory and has no venue truth.",
            "USD is an explicit 1:1 USDC accounting proxy for the bounded overlay.",
            "Funding is zero because no settlement boundary is modeled or crossed.",
            (
                "Incomplete or ambiguous runs are not resumed; durable recovery and "
                "reconciliation remain D22."
            ),
        ],
    }


def _run_claim_payload(
    *,
    run_id: str,
    identity: str,
    dataset: LoadedDataset,
    config: SliceConfig,
    source: dict[str, object],
) -> dict[str, object]:
    preflight = evaluate_order_risk(
        side=OrderSide.BUY,
        quantity=config.order_quantity_btc,
        price=dataset.max_price,
        reduce_only=False,
        current_position=Decimal(0),
        config=config,
    )
    if not preflight["approved"]:
        raise RiskRejectedError(f"D01 preflight risk rejected the fixed smoke size: {preflight}")
    return {
        "schema": D01_SCHEMA,
        "state": "STARTED_FAIL_CLOSED",
        "run_id": run_id,
        "run_identity": identity,
        "dataset_file_sha256": dataset.file_sha256,
        "dataset_events_sha256": dataset.events_sha256,
        "config_sha256": sha256_json(config.to_dict()),
        "source_sha256": source["digest"],
        "mode": "PAPER",
        "preflight_entry_risk": preflight,
        "resume_policy": "never resume or overwrite an existing D01 run directory",
    }


def run_slice(
    *,
    run_id: str,
    artifact_dir: Path,
    dataset_path: Path = DEFAULT_DATASET,
    config: SliceConfig = DEFAULT_CONFIG,
) -> dict[str, object]:
    """Run two replays and one credentialless sandbox-PAPER lifecycle create-only."""

    assert_local_boundary()
    if artifact_dir.exists():
        raise FileExistsError(f"D01 refuses to reuse existing run directory: {artifact_dir}")
    dataset = load_bounded_dataset(dataset_path)
    source = source_identity()
    identity = run_identity(run_id=run_id, dataset=dataset, config=config, source=source)
    claim = _run_claim_payload(
        run_id=run_id,
        identity=identity,
        dataset=dataset,
        config=config,
        source=source,
    )

    prepare_new_output_directory(artifact_dir)
    claim_path = artifact_dir / "run-claim.json"
    replay_one_path = artifact_dir / "replay-1.json"
    replay_two_path = artifact_dir / "replay-2.json"
    paper_path = artifact_dir / "paper.json"
    completion_path = artifact_dir / "completed-run.json"
    write_json(claim_path, claim)
    with _derived_internal_paper_mode():
        replay_one = _run_replay_once(
            label="deterministic_replay_1",
            dataset=dataset,
            identity=identity,
            config=config,
        )
        write_json(replay_one_path, replay_one)
        replay_two = _run_replay_once(
            label="deterministic_replay_2",
            dataset=dataset,
            identity=identity,
            config=config,
        )
        write_json(replay_two_path, replay_two)
        paper = _run_paper(dataset=dataset, identity=identity, config=config)
        write_json(paper_path, paper)
    completion = _completion_payload(
        run_id=run_id,
        identity=identity,
        dataset=dataset,
        config=config,
        source=source,
        claim_path=claim_path,
        replay_one_path=replay_one_path,
        replay_two_path=replay_two_path,
        paper_path=paper_path,
        replay_one=replay_one,
        replay_two=replay_two,
        paper=paper,
    )
    write_json(completion_path, completion)
    return completion


def probe_completed_run(
    artifact_dir: Path,
    *,
    dataset_path: Path = DEFAULT_DATASET,
    config: SliceConfig = DEFAULT_CONFIG,
) -> dict[str, object]:
    """Verify a completed flat run and return a no-submit restart decision.

    This function intentionally constructs no engine, strategy, factory, or order.
    """

    paths = {
        "claim": artifact_dir / "run-claim.json",
        "replay_one": artifact_dir / "replay-1.json",
        "replay_two": artifact_dir / "replay-2.json",
        "paper": artifact_dir / "paper.json",
        "completion": artifact_dir / "completed-run.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"D01 refuses to resume incomplete run evidence: {missing}")
    completion = _read_object(paths["completion"])
    run_id = completion.get("run_id")
    if type(run_id) is not str:
        raise TypeError("D01 completed run_id must be a string.")
    dataset = load_bounded_dataset(dataset_path)
    source = source_identity()
    identity = run_identity(run_id=run_id, dataset=dataset, config=config, source=source)
    expected = _completion_payload(
        run_id=run_id,
        identity=identity,
        dataset=dataset,
        config=config,
        source=source,
        claim_path=paths["claim"],
        replay_one_path=paths["replay_one"],
        replay_two_path=paths["replay_two"],
        paper_path=paths["paper"],
        replay_one=_read_object(paths["replay_one"]),
        replay_two=_read_object(paths["replay_two"]),
        paper=_read_object(paths["paper"]),
    )
    if completion != expected:
        raise ValueError("D01 completed-run checkpoint does not recompute exactly.")
    recomputed = completion.get("recomputed")
    if type(recomputed) is not dict:
        raise TypeError("D01 completed-run recomputed section must be an object.")
    intent_keys = recomputed.get("completed_intent_keys")
    if type(intent_keys) is not list:
        raise TypeError("D01 completed intent keys must be a list.")
    return {
        "schema": D01_SCHEMA,
        "run_id": run_id,
        "run_identity": identity,
        "source_completed_run_sha256": _sha256_file(paths["completion"]),
        "decision": "NOOP_ALREADY_COMPLETE",
        "completed_intent_keys": intent_keys,
        "new_submission_intent_keys": [],
        "engine_or_order_construction": False,
        "limitation": "completed-flat local runs only; no mid-run recovery or reconciliation",
    }
