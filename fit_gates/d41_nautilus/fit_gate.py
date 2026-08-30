"""Minimal D41 NautilusTrader BTC-PERP replay-to-PAPER fit gate.

This module is deliberately outside the production package. It composes the existing
venue-neutral v2 trade contract with NautilusTrader without changing the collector,
provenance/v3, 3B1C-2, or 1H paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import platform
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import nautilus_trader
import pandas as pd
import websockets
from nautilus_trader.adapters.hyperliquid import (
    HYPERLIQUID,
    HYPERLIQUID_VENUE,
)
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, LatencyModel, MakerTakerFeeModel
from nautilus_trader.common import Environment
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    StrategyConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.currencies import BTC, USD, USDC
from nautilus_trader.model.data import FundingRateUpdate, TradeTick
from nautilus_trader.model.enums import (
    AccountType,
    BookType,
    OmsType,
    OrderSide,
    TimeInForce,
)
from nautilus_trader.model.enums import AggressorSide as NautilusAggressorSide
from nautilus_trader.model.events import OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, TraderId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.instruments import Instrument as NautilusInstrument
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from hyperliquid_bot.contracts import (
    MARKET_EVENT_SCHEMA_VERSION,
    AggressorSide,
    Instrument,
    InstrumentType,
    MarketEventEnvelope,
    TradeEvent,
    Venue,
)

D41_SCHEMA: Final = "d41-nautilus-fit-v1"
NAUTILUS_PIN: Final = "1.231.0"
INSTRUMENT_ID: Final = InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID")
STARTING_CASH: Final = Decimal(100000)
ORDER_QUANTITY: Final = Decimal("0.00013")
MAX_PAPER_SECONDS: Final = 600
STALE_AFTER_SECONDS: Final = Decimal(5)
STALE_AFTER_NS: Final = int(STALE_AFTER_SECONDS * Decimal(1_000_000_000))
PRICE_INCREMENT: Final = Decimal("0.1")
SIZE_INCREMENT: Final = Decimal("0.00001")
PROTECTED_CREDENTIAL_ENV_NAMES: Final = (
    "HYPERLIQUID_PK",
    "HYPERLIQUID_TESTNET_PK",
    "HYPERLIQUID_VAULT",
    "HYPERLIQUID_TESTNET_VAULT",
    "HYPERLIQUID_ACCOUNT_ADDRESS",
)


@dataclass(frozen=True, slots=True)
class CostAssumptions:
    """Project-owned explicit economics missing from the generic sandbox."""

    taker_fee_bps: Decimal = Decimal("4.5")
    half_spread_bps: Decimal = Decimal("0.5")
    slippage_bps: Decimal = Decimal("1.0")
    funding_payment: Decimal = Decimal(0)
    funding_reason: str = (
        "No hourly funding settlement boundary is modeled or crossed by the bounded run; "
        "Nautilus sandbox ignores FundingRateUpdate for settlement."
    )

    def to_dict(self) -> dict[str, str]:
        return {
            "taker_fee_bps": str(self.taker_fee_bps),
            "half_spread_bps_per_fill": str(self.half_spread_bps),
            "slippage_bps_per_fill": str(self.slippage_bps),
            "funding_payment_usdc": str(self.funding_payment),
            "funding_reason": self.funding_reason,
            "usd_usdc_parity_assumption": (
                "1 USD = 1 USDC for the external D41 cost overlay only; "
                "the Nautilus sandbox account is an explicit USD accounting proxy"
            ),
            "native_sandbox_fee_model": "MakerTakerFeeModel with Hyperliquid metadata fee=0",
            "native_sandbox_latency_ns": "0",
            "native_sandbox_extra_slippage": "0",
        }


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_json(path: Path, value: object) -> None:
    """Write one new artifact without ever replacing existing evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2)
            + "\n"
        )


def prepare_new_output_directory(path: Path) -> None:
    """Reserve a new run directory before capture; an existing path is never reused."""

    path.mkdir(parents=True, exist_ok=False)


def assert_new_files(paths: list[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise FileExistsError(f"D41 refuses to overwrite existing evidence: {existing}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def harness_identity() -> dict[str, object]:
    root = Path(__file__).resolve().parent
    names = (
        "capture_public_dataset.py",
        "fit_gate.py",
        "requirements.lock",
        "run_fit_gate.py",
    )
    return {
        "algorithm": "sha256",
        "files": {name: sha256_file(root / name) for name in names},
    }


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("D41 JSON root must be an object.")
    return value


def current_environment() -> dict[str, object]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "nautilus_trader": nautilus_trader.__version__,
        "websockets": websockets.__version__,
    }


def assert_exact_runtime_pin() -> None:
    if nautilus_trader.__version__ != NAUTILUS_PIN:
        raise RuntimeError(
            f"D41 requires nautilus_trader=={NAUTILUS_PIN}, found {nautilus_trader.__version__}."
        )


def present_credential_names(
    environ: dict[str, str] | os._Environ[str] | None = None,
) -> list[str]:
    source = os.environ if environ is None else environ
    return sorted(name for name in PROTECTED_CREDENTIAL_ENV_NAMES if source.get(name))


def assert_paper_boundary(*, seconds: int, environ: dict[str, str] | None = None) -> None:
    source = dict(os.environ) if environ is None else environ
    if source.get("D41_EXECUTION_MODE") != "PAPER":
        raise RuntimeError("D41 fails closed unless D41_EXECUTION_MODE is exactly PAPER.")
    if seconds <= 0 or seconds > MAX_PAPER_SECONDS:
        raise ValueError(f"PAPER duration must be between 1 and {MAX_PAPER_SECONDS} seconds.")
    found = present_credential_names(source)
    if found:
        raise RuntimeError(
            f"D41 refuses to start while credential environment names exist: {found}"
        )


def existing_btc_contract() -> Instrument:
    """Return the existing v2 venue-neutral identity without changing its contract."""

    return Instrument(
        venue=Venue.HYPERLIQUID,
        instrument_type=InstrumentType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDC",
        venue_market_id="BTC",
        native_symbol="BTC",
    )


def build_nautilus_instrument() -> CryptoPerpetual:
    """Reconstruct the public BTC perp precision observed from Hyperliquid `/info`."""

    return CryptoPerpetual(
        instrument_id=INSTRUMENT_ID,
        raw_symbol=Symbol("BTC"),
        base_currency=BTC,
        quote_currency=USD,
        settlement_currency=USDC,
        is_inverse=False,
        price_precision=1,
        size_precision=5,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.00001"),
        ts_event=0,
        ts_init=0,
        multiplier=Quantity.from_int(1),
        lot_size=Quantity.from_int(1),
        min_notional=Money.from_str("10.00 USD"),
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
        info={"d41_source": "credentialless Hyperliquid MAINNET public /info"},
    )


def _datetime_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware.")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value.astimezone(UTC) - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _parse_utc_datetime(value: object, *, field_name: str) -> datetime:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be an ISO-8601 string.")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC.")
    return parsed.astimezone(UTC)


def _exact_increment_text(value: Decimal, *, increment: Decimal, field_name: str) -> str:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name} must be a finite positive decimal.")
    quantized = value.quantize(increment)
    if quantized != value:
        raise ValueError(f"{field_name} is not exactly aligned to increment {increment}.")
    decimal_places = max(0, -increment.as_tuple().exponent)
    return format(quantized, f".{decimal_places}f")


def envelope_to_trade_tick(
    envelope: MarketEventEnvelope,
    *,
    prior_ts_init: int | None = None,
) -> TradeTick:
    """Thin fail-closed v2 envelope -> Nautilus TradeTick boundary.

    Capture order is preserved with a one-nanosecond tie-break only when multiple
    trades share one receive timestamp. `source_sequence` and Hyperliquid `tid` are
    deliberately not treated as a venue sequence.
    """

    if type(envelope) is not MarketEventEnvelope:
        raise TypeError("envelope must be a MarketEventEnvelope.")
    expected = existing_btc_contract()
    if envelope.instrument != expected:
        raise ValueError("D41 accepts only the exact Hyperliquid BTC perpetual contract.")
    if envelope.is_gap:
        raise ValueError("D41 refuses gap-tainted input.")
    age = Decimal(str((envelope.received_time - envelope.event_time).total_seconds()))
    if age < 0 or age > STALE_AFTER_SECONDS:
        raise ValueError("D41 refuses future-dated or stale input.")

    ts_event = _datetime_ns(envelope.event_time)
    ts_init = _datetime_ns(envelope.received_time)
    if prior_ts_init is not None and ts_init <= prior_ts_init:
        ts_init = prior_ts_init + 1
    trade_id = TradeId(hashlib.sha256(envelope.source_event_id.encode("utf-8")).hexdigest()[:32])
    aggressor_side = (
        NautilusAggressorSide.BUYER
        if envelope.event.aggressor_side is AggressorSide.BUY
        else NautilusAggressorSide.SELLER
    )
    price = _exact_increment_text(
        envelope.event.price,
        increment=PRICE_INCREMENT,
        field_name="price",
    )
    size = _exact_increment_text(
        envelope.event.quantity,
        increment=SIZE_INCREMENT,
        field_name="quantity",
    )
    return TradeTick(
        instrument_id=INSTRUMENT_ID,
        price=Price.from_str(price),
        size=Quantity.from_str(size),
        aggressor_side=aggressor_side,
        trade_id=trade_id,
        ts_event=ts_event,
        ts_init=ts_init,
    )


def trade_tick_to_record(tick: TradeTick) -> dict[str, object]:
    return {
        "instrument_id": tick.instrument_id.value,
        "price": str(tick.price),
        "size": str(tick.size),
        "aggressor_side": tick.aggressor_side.name,
        "trade_id": tick.trade_id.value,
        "ts_event": tick.ts_event,
        "ts_init": tick.ts_init,
    }


def envelope_to_record(
    envelope: MarketEventEnvelope,
    *,
    capture_ordinal: int,
) -> dict[str, object]:
    """Persist the primary v2 fields needed to rerun the exact input boundary."""

    return {
        "capture_ordinal": capture_ordinal,
        "schema_version": envelope.schema_version,
        "venue_neutral_contract_id": envelope.instrument.canonical_instrument_id,
        "price": str(envelope.event.price),
        "quantity": str(envelope.event.quantity),
        "aggressor_side": envelope.event.aggressor_side.name,
        "event_time": envelope.event_time.isoformat(),
        "received_time": envelope.received_time.isoformat(),
        "received_monotonic_ns": envelope.received_monotonic_ns,
        "collector_version": envelope.collector_version,
        "collector_commit": envelope.collector_commit,
        "is_gap": envelope.is_gap,
        "source_event_id": envelope.source_event_id,
        "source_transaction_id": envelope.source_transaction_id,
        "source_sequence": envelope.source_sequence,
        "correlation_id": envelope.correlation_id,
    }


def record_to_envelope(record: dict[str, object]) -> MarketEventEnvelope:
    if record.get("schema_version") != MARKET_EVENT_SCHEMA_VERSION:
        raise ValueError("Dataset event is not a MarketEventEnvelope-v2 record.")
    expected = existing_btc_contract()
    if record.get("venue_neutral_contract_id") != expected.canonical_instrument_id:
        raise ValueError("Dataset contains an out-of-scope instrument.")
    side_text = record.get("aggressor_side")
    if side_text not in {"BUY", "SELL"}:
        raise ValueError("Dataset contains an invalid aggressor side.")
    source_sequence = record.get("source_sequence")
    if source_sequence is not None and type(source_sequence) is not int:
        raise TypeError("source_sequence must be an integer or null.")
    correlation_id = record.get("correlation_id")
    if correlation_id is not None and type(correlation_id) is not str:
        raise TypeError("correlation_id must be a string or null.")
    source_transaction_id = record.get("source_transaction_id")
    if source_transaction_id is not None and type(source_transaction_id) is not str:
        raise TypeError("source_transaction_id must be a string or null.")
    required_text_fields = (
        "price",
        "quantity",
        "collector_version",
        "collector_commit",
        "source_event_id",
    )
    for field_name in required_text_fields:
        if type(record.get(field_name)) is not str or not str(record[field_name]).strip():
            raise TypeError(f"{field_name} must be a non-empty string.")
    received_monotonic_ns = record.get("received_monotonic_ns")
    if type(received_monotonic_ns) is not int:
        raise TypeError("received_monotonic_ns must be an integer.")
    return MarketEventEnvelope(
        schema_version=MARKET_EVENT_SCHEMA_VERSION,
        instrument=expected,
        event=TradeEvent(
            price=Decimal(str(record["price"])),
            quantity=Decimal(str(record["quantity"])),
            aggressor_side=AggressorSide[side_text],
        ),
        event_time=_parse_utc_datetime(record.get("event_time"), field_name="event_time"),
        received_time=_parse_utc_datetime(
            record.get("received_time"),
            field_name="received_time",
        ),
        received_monotonic_ns=received_monotonic_ns,
        collector_version=record["collector_version"],
        collector_commit=record["collector_commit"],
        is_gap=record.get("is_gap"),
        source_event_id=record["source_event_id"],
        source_transaction_id=source_transaction_id,
        source_sequence=source_sequence,
        correlation_id=correlation_id,
    )


def decide_entry(previous_price: Decimal, current_price: Decimal) -> OrderSide | None:
    """Transparent no-alpha baseline using only the previous and current observed trade."""

    if current_price > previous_price:
        return OrderSide.BUY
    if current_price < previous_price:
        return OrderSide.SELL
    return None


class D41SmokeStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    order_quantity: Decimal
    warmup_ticks: int = 3
    hold_ticks: int = 5
    record_limit: int = 2_000
    subscribe_market_data: bool = True


class D41SmokeStrategy(Strategy):
    """One non-promotable engineering baseline shared by replay and PAPER."""

    def __init__(self, config: D41SmokeStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: NautilusInstrument | None = None
        self.inputs: list[dict[str, object]] = []
        self.signals: list[dict[str, object]] = []
        self.order_intents: list[dict[str, object]] = []
        self.fills: list[dict[str, object]] = []
        self.position_events: list[dict[str, object]] = []
        self.order_terminal_events: list[dict[str, object]] = []
        self.rejections: list[dict[str, object]] = []
        self.funding_updates: list[dict[str, object]] = []
        self.tick_count = 0
        self._previous_price: Decimal | None = None
        self._entry_requested = False
        self._exit_requested = False
        self._entry_fill_tick: int | None = None
        self._position_qty = Decimal(0)
        self._pending_order: dict[str, object] | None = None

    @property
    def is_complete(self) -> bool:
        return (
            self._exit_requested
            and self._pending_order is None
            and self._position_qty == 0
            and len(self.order_intents) == 2
            and len(self.fills) >= 2
            and not self.rejections
        )

    @property
    def signed_position_quantity(self) -> Decimal:
        return self._position_qty

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"D41 instrument unavailable: {self.config.instrument_id}")
            self.stop()
            return
        if self.config.subscribe_market_data:
            self.subscribe_trade_ticks(self.config.instrument_id)
            self.subscribe_funding_rates(self.config.instrument_id)

    def on_trade_tick(self, tick: TradeTick) -> None:
        self.tick_count += 1
        current_price = Decimal(str(tick.price))
        if len(self.inputs) < self.config.record_limit:
            record = trade_tick_to_record(tick)
            record["observed_ordinal"] = self.tick_count
            self.inputs.append(record)

        previous_price = self._previous_price
        self._previous_price = current_price

        if self._pending_order is not None:
            pending = self._pending_order
            self._pending_order = None
            self._submit_market(
                side=pending["side"],
                reduce_only=bool(pending["reduce_only"]),
                reason=str(pending["reason"]),
                decision_ordinal=int(pending["decision_ordinal"]),
            )
            return

        if previous_price is None:
            return

        if not self._entry_requested and self.tick_count >= self.config.warmup_ticks:
            side = decide_entry(previous_price, current_price)
            if side is None:
                return
            self.signals.append(
                {
                    "kind": "two_trade_direction",
                    "decision_ordinal": self.tick_count,
                    "previous_price": str(previous_price),
                    "current_price": str(current_price),
                    "side": side.name,
                    "lookahead_events": 0,
                }
            )
            self._pending_order = {
                "side": side,
                "reduce_only": False,
                "reason": "entry",
                "decision_ordinal": self.tick_count,
            }
            self._entry_requested = True
            return

        if (
            self._entry_fill_tick is not None
            and not self._exit_requested
            and self._position_qty != 0
            and self.tick_count - self._entry_fill_tick >= self.config.hold_ticks
        ):
            side = OrderSide.SELL if self._position_qty > 0 else OrderSide.BUY
            self.signals.append(
                {
                    "kind": "fixed_observed_tick_exit",
                    "decision_ordinal": self.tick_count,
                    "entry_fill_ordinal": self._entry_fill_tick,
                    "current_price": str(current_price),
                    "side": side.name,
                    "lookahead_events": 0,
                }
            )
            self._pending_order = {
                "side": side,
                "reduce_only": True,
                "reason": "exit",
                "decision_ordinal": self.tick_count,
            }
            self._exit_requested = True

    def _submit_market(
        self,
        *,
        side: OrderSide,
        reduce_only: bool,
        reason: str,
        decision_ordinal: int,
    ) -> None:
        if self.instrument is None:
            raise RuntimeError("D41 strategy has no instrument.")
        quantity_value = abs(self._position_qty) if reduce_only else self.config.order_quantity
        quantity = self.instrument.make_qty(quantity_value)
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            time_in_force=TimeInForce.IOC,
            reduce_only=reduce_only,
        )
        self.order_intents.append(
            {
                "reason": reason,
                "decision_ordinal": decision_ordinal,
                "submission_ordinal": self.tick_count,
                "side": side.name,
                "quantity": str(quantity),
                "order_type": "MARKET",
                "reduce_only": reduce_only,
            }
        )
        self.submit_order(order)

    def on_order_filled(self, event: OrderFilled) -> None:
        quantity = Decimal(str(event.last_qty))
        signed = quantity if event.order_side is OrderSide.BUY else -quantity
        self._position_qty += signed
        if self._entry_fill_tick is None:
            self._entry_fill_tick = self.tick_count
        self.fills.append(
            {
                "fill_ordinal": len(self.fills) + 1,
                "observed_tick_ordinal": self.tick_count,
                "side": event.order_side.name,
                "quantity": str(event.last_qty),
                "price": str(event.last_px),
                "native_commission": str(event.commission),
                "liquidity_side": event.liquidity_side.name,
                "ts_event": event.ts_event,
                "position_after": str(self._position_qty),
            }
        )

    def on_order_rejected(self, event: OrderRejected) -> None:
        self.rejections.append(
            {
                "kind": "ORDER_REJECTED",
                "ts_event": event.ts_event,
                "reason": str(event.reason),
            }
        )

    def on_order_canceled(self, event: object) -> None:
        self.order_terminal_events.append({"kind": "ORDER_CANCELED", "value": str(event)})

    def on_position_opened(self, event: object) -> None:
        self.position_events.append({"kind": "POSITION_OPENED", "value": str(event)})

    def on_position_changed(self, event: object) -> None:
        self.position_events.append({"kind": "POSITION_CHANGED", "value": str(event)})

    def on_position_closed(self, event: object) -> None:
        self.position_events.append({"kind": "POSITION_CLOSED", "value": str(event)})

    def on_funding_rate(self, update: FundingRateUpdate) -> None:
        if len(self.funding_updates) >= 20:
            return
        self.funding_updates.append(
            {
                "rate": str(update.rate),
                "interval_minutes": update.interval,
                "next_funding_ns": update.next_funding_ns,
                "ts_event": update.ts_event,
                "ts_init": update.ts_init,
            }
        )

    def on_stop(self) -> None:
        if self.config.subscribe_market_data:
            self.unsubscribe_trade_ticks(self.config.instrument_id)
            self.unsubscribe_funding_rates(self.config.instrument_id)


def make_strategy(*, subscribe_market_data: bool = True) -> D41SmokeStrategy:
    return D41SmokeStrategy(
        D41SmokeStrategyConfig(
            order_id_tag="D41",
            instrument_id=INSTRUMENT_ID,
            order_quantity=ORDER_QUANTITY,
            warmup_ticks=3,
            hold_ticks=5,
            record_limit=2_000,
            subscribe_market_data=subscribe_market_data,
            log_events=False,
            log_commands=False,
        )
    )


def _scalar(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def dataframe_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    rows = frame.reset_index().to_dict(orient="records")
    return [
        {
            str(key): _scalar(value)
            for key, value in sorted(row.items(), key=lambda item: str(item[0]))
        }
        for row in rows
    ]


def strategy_business_result(strategy: D41SmokeStrategy) -> dict[str, object]:
    return {
        "strategy_class": f"{type(strategy).__module__}.{type(strategy).__qualname__}",
        "strategy_config": {
            "instrument_id": strategy.config.instrument_id.value,
            "order_quantity": str(strategy.config.order_quantity),
            "warmup_ticks": strategy.config.warmup_ticks,
            "hold_ticks": strategy.config.hold_ticks,
        },
        "tick_count": strategy.tick_count,
        "signals": strategy.signals,
        "order_intents": strategy.order_intents,
        "fills": strategy.fills,
        "position_events": strategy.position_events,
        "order_terminal_events": strategy.order_terminal_events,
        "rejections": strategy.rejections,
        "final_position_quantity": str(strategy.signed_position_quantity),
        "complete": strategy.is_complete,
    }


def cost_overlay(
    fills: list[dict[str, object]],
    *,
    mark_price: Decimal,
    assumptions: CostAssumptions,
) -> dict[str, str]:
    cash = STARTING_CASH
    position = Decimal(0)
    total_fee = Decimal(0)
    total_spread = Decimal(0)
    total_slippage = Decimal(0)
    for fill in fills:
        quantity = Decimal(str(fill["quantity"]))
        price = Decimal(str(fill["price"]))
        signed = quantity if fill["side"] == "BUY" else -quantity
        notional = quantity * price
        fee = notional * assumptions.taker_fee_bps / Decimal(10000)
        spread = notional * assumptions.half_spread_bps / Decimal(10000)
        slippage = notional * assumptions.slippage_bps / Decimal(10000)
        costs = fee + spread + slippage
        cash -= signed * price
        cash -= costs
        position += signed
        total_fee += fee
        total_spread += spread
        total_slippage += slippage
    cash -= assumptions.funding_payment
    equity = cash + position * mark_price
    net_pnl = equity - STARTING_CASH
    return {
        "starting_cash_usdc_assumed": str(STARTING_CASH),
        "ending_cash_usdc_assumed": str(cash),
        "mark_price": str(mark_price),
        "final_position_btc": str(position),
        "ending_equity_usdc_assumed": str(equity),
        "net_pnl_usdc_assumed": str(net_pnl),
        "fee_cost_usdc": str(total_fee),
        "half_spread_cost_usdc": str(total_spread),
        "slippage_cost_usdc": str(total_slippage),
        "funding_payment_usdc": str(assumptions.funding_payment),
    }


def _load_dataset(path: Path) -> tuple[dict[str, Any], list[TradeTick]]:
    dataset = read_json(path)
    if dataset.get("schema") != "d41-public-trades-v1":
        raise ValueError("Unsupported D41 dataset schema.")
    if dataset.get("instrument_id") != INSTRUMENT_ID.value:
        raise ValueError("Dataset is not the exact BTC perpetual.")
    expected_contract = existing_btc_contract().canonical_instrument_id
    if dataset.get("venue_neutral_contract_id") != expected_contract:
        raise ValueError("Dataset has the wrong venue-neutral contract identity.")
    if dataset.get("harness") != harness_identity():
        raise ValueError("Dataset is not bound to the current D41 harness and lockfile.")
    capture_integrity = dataset.get("capture_integrity")
    if capture_integrity != {
        "boundary": "MarketEventEnvelope-v2 -> envelope_to_trade_tick",
        "claim": "local_capture_integrity_only",
        "exchange_origin_cryptographically_proven": False,
        "receipt_clock_semantics": (
            "received_time is the collector UTC wall-clock receipt timestamp; "
            "event age is received_time minus exchange event_time"
        ),
        "monotonic_clock_semantics": (
            "received_monotonic_ns proves local receive ordering and bounded duration only; "
            "it is never subtracted from exchange event_time"
        ),
    }:
        raise ValueError("Dataset lacks the exact local capture-integrity declaration.")
    capture_metadata = dataset.get("capture_metadata")
    if type(capture_metadata) is not dict:
        raise TypeError("Dataset capture_metadata must be an object.")
    if capture_metadata.get("collector_route") != (
        "HyperliquidTradesCollector -> MarketEventEnvelope-v2 -> envelope_to_trade_tick"
    ):
        raise ValueError("Dataset did not use the existing collector/v2 boundary.")
    if capture_metadata.get("collector_state_after_shutdown") != "stopped":
        raise ValueError("Dataset collector did not stop cleanly.")
    if capture_metadata.get("sticky_gap") is not False:
        raise ValueError("Dataset capture is gap-tainted.")
    if capture_metadata.get("volatile_sinks") is not True:
        raise ValueError("Dataset does not identify its volatile local sinks.")
    records = dataset.get("events")
    if type(records) is not list or len(records) < 12:
        raise ValueError("D41 requires at least twelve accepted public trade events.")
    if dataset.get("event_count") != len(records):
        raise ValueError("Dataset event_count does not match its primary event records.")
    expected_hash = dataset.get("events_sha256")
    if expected_hash != sha256_json(records):
        raise ValueError("Dataset event digest mismatch.")
    received_total = capture_metadata.get("events_received_total")
    if type(received_total) is not int or received_total < len(records):
        raise ValueError("Dataset received-event count is inconsistent.")
    rejected_stale = capture_metadata.get("stale_or_future_events_rejected")
    rejected_precision = capture_metadata.get("invalid_precision_events_rejected")
    if type(rejected_stale) is not int or rejected_stale < 0:
        raise ValueError("Dataset stale rejection count is invalid.")
    if type(rejected_precision) is not int or rejected_precision < 0:
        raise ValueError("Dataset precision rejection count is invalid.")
    if capture_metadata.get("accepted_event_count") != len(records):
        raise ValueError("Dataset accepted-event count is inconsistent.")
    if received_total != len(records) + rejected_stale + rejected_precision:
        raise ValueError("Dataset received and rejected event counts do not reconcile.")
    if dataset.get("capture_order_preserved") is not True:
        raise ValueError("Dataset does not preserve capture order.")
    if dataset.get("hyperliquid_tid_used_as_sequence") is not False:
        raise ValueError("Dataset makes an unsupported Hyperliquid sequence claim.")
    if dataset.get("source_subscription") != {"type": "trades", "coin": "BTC"}:
        raise ValueError("Dataset subscription metadata is out of scope.")

    ticks: list[TradeTick] = []
    prior_ts_init: int | None = None
    prior_monotonic: int | None = None
    source_event_ids: set[str] = set()
    for ordinal, raw_record in enumerate(records, start=1):
        if type(raw_record) is not dict:
            raise TypeError("Dataset primary event must be an object.")
        if raw_record.get("capture_ordinal") != ordinal:
            raise ValueError("Dataset capture ordinals are not contiguous.")
        envelope = record_to_envelope(raw_record)
        if (
            prior_monotonic is not None
            and envelope.received_monotonic_ns < prior_monotonic
        ):
            raise ValueError("Dataset receive monotonic time moved backwards.")
        if envelope.source_event_id in source_event_ids:
            raise ValueError("Dataset contains a duplicate source event ID.")
        tick = envelope_to_trade_tick(envelope, prior_ts_init=prior_ts_init)
        ticks.append(tick)
        prior_ts_init = tick.ts_init
        prior_monotonic = envelope.received_monotonic_ns
        source_event_ids.add(envelope.source_event_id)
    if any(tick.instrument_id != INSTRUMENT_ID for tick in ticks):
        raise ValueError("Dataset escaped the one-instrument boundary.")
    return dataset, ticks


def dataset_input_ages_ns(dataset: dict[str, Any]) -> list[int]:
    records = dataset.get("events")
    if type(records) is not list:
        raise TypeError("Dataset primary events must be a list.")
    ages: list[int] = []
    for record in records:
        if type(record) is not dict:
            raise TypeError("Dataset primary event must be an object.")
        envelope = record_to_envelope(record)
        ages.append(_datetime_ns(envelope.received_time) - _datetime_ns(envelope.event_time))
    return ages


def expected_strategy_inputs(ticks: list[TradeTick]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for ordinal, tick in enumerate(ticks, start=1):
        record = trade_tick_to_record(tick)
        record["observed_ordinal"] = ordinal
        records.append(record)
    return records


def _engine_reports(trader: object) -> dict[str, object]:
    return {
        "orders": dataframe_records(trader.generate_orders_report()),
        "order_fills": dataframe_records(trader.generate_order_fills_report()),
        "fills": dataframe_records(trader.generate_fills_report()),
        "positions": dataframe_records(trader.generate_positions_report()),
        "account": dataframe_records(trader.generate_account_report(HYPERLIQUID_VENUE)),
    }


def nonterminal_order_statuses(reports: dict[str, object]) -> list[str]:
    terminal = {"CANCELED", "EXPIRED", "FILLED", "REJECTED"}
    orders = reports.get("orders", [])
    if type(orders) is not list:
        return ["INVALID_REPORT"]
    return sorted(
        {
            str(order.get("status"))
            for order in orders
            if type(order) is dict and str(order.get("status")) not in terminal
        }
    )


def run_replay_once(dataset_path: Path, *, label: str) -> dict[str, object]:
    assert_exact_runtime_pin()
    dataset, ticks = _load_dataset(dataset_path)
    instrument = build_nautilus_instrument()
    strategy = make_strategy()
    config = BacktestEngineConfig(
        trader_id=TraderId("D41-001"),
        logging=LoggingConfig(log_level="ERROR", use_pyo3=False),
    )
    engine = BacktestEngine(config=config)
    try:
        engine.add_venue(
            venue=HYPERLIQUID_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money.from_str(f"{STARTING_CASH} USD")],
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
        engine.add_instrument(instrument)
        engine.add_data(ticks, validate=True, sort=False)
        engine.sort_data()
        engine.add_strategy(strategy)
        engine.run()
        reports = _engine_reports(engine.trader)
        business = strategy_business_result(strategy)
        last_price = Decimal(str(ticks[-1].price))
        overlay = cost_overlay(
            strategy.fills,
            mark_price=last_price,
            assumptions=CostAssumptions(),
        )
        deterministic_payload = {
            "schema": D41_SCHEMA,
            "dataset_events_sha256": dataset["events_sha256"],
            "cost_assumptions": CostAssumptions().to_dict(),
            "business": business,
            "cost_overlay": overlay,
        }
        return {
            "schema": D41_SCHEMA,
            "run_kind": "deterministic_replay",
            "run_label": label,
            "created_at_utc": utc_now_text(),
            "environment": current_environment(),
            "harness": harness_identity(),
            "dataset": {
                "path": str(dataset_path),
                "event_count": len(ticks),
                "events_sha256": dataset["events_sha256"],
                "capture_claim": "local_capture_integrity_only",
            },
            "engine": {
                "venue": HYPERLIQUID_VENUE.value,
                "account_type": "MARGIN",
                "book_type": "L1_MBP",
                "trade_execution": True,
                "random_seed": 42,
                "native_fee_metadata": {"maker": "0", "taker": "0"},
                "native_latency_ns": 0,
                "native_extra_slippage": 0,
            },
            "cost_assumptions": CostAssumptions().to_dict(),
            "business": business,
            "cost_overlay": overlay,
            "nautilus_reports": reports,
            "deterministic_payload_sha256": sha256_json(deterministic_payload),
        }
    finally:
        engine.dispose()


def run_two_replays(
    dataset_path: Path,
    *,
    artifact_dir: Path,
) -> dict[str, object]:
    output_paths = [
        artifact_dir / "replay-1.json",
        artifact_dir / "replay-2.json",
        artifact_dir / "determinism.json",
    ]
    assert_new_files(output_paths)
    replay_one = run_replay_once(dataset_path, label="replay-1")
    replay_two = run_replay_once(dataset_path, label="replay-2")
    write_json(output_paths[0], replay_one)
    write_json(output_paths[1], replay_two)
    digest_one = replay_one["deterministic_payload_sha256"]
    digest_two = replay_two["deterministic_payload_sha256"]
    result = {
        "schema": D41_SCHEMA,
        "gate": "two_identical_replays",
        "pass": digest_one == digest_two,
        "replay_1_sha256": digest_one,
        "replay_2_sha256": digest_two,
    }
    write_json(output_paths[2], result)
    if not result["pass"]:
        raise RuntimeError("Two fresh D41 replays were not deterministic.")
    return result


def _paper_node_config() -> TradingNodeConfig:
    provider = InstrumentProviderConfig(load_ids=frozenset({INSTRUMENT_ID}))
    return TradingNodeConfig(
        environment=Environment.SANDBOX,
        trader_id=TraderId("D41-001"),
        logging=LoggingConfig(log_level="ERROR", use_pyo3=True),
        exec_engine=LiveExecEngineConfig(reconciliation=False),
        data_clients={},
        exec_clients={
            HYPERLIQUID: SandboxExecutionClientConfig(
                venue=HYPERLIQUID,
                starting_balances=[f"{STARTING_CASH} USD"],
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


async def _drive_sandbox_paper(
    node: TradingNode,
    strategy: D41SmokeStrategy,
    ticks: list[TradeTick],
) -> dict[str, object]:
    run_task = asyncio.create_task(node.run_async(), name="d41-sandbox-paper-node")
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
            raise RuntimeError("D41 sandbox node did not start.")
        node.kernel.msgbus.subscribe(trade_topic, strategy.handle_trade_tick)
        subscribed = True

        for tick in ticks:
            node.kernel.data_engine.process(tick)
            # Risk and execution use asynchronous queues. Yielding after every accepted
            # input preserves input order without inventing wall-clock receipt times.
            for _ in range(4):
                await asyncio.sleep(0)

        for _ in range(1_000):
            if strategy.is_complete or strategy.rejections:
                break
            await asyncio.sleep(0)
        return _engine_reports(node.trader)
    finally:
        if subscribed:
            node.kernel.msgbus.unsubscribe(trade_topic, strategy.handle_trade_tick)
        if node.is_running():
            await node.stop_async()
        await run_task


def run_paper(
    *,
    seconds: int,
    dataset_path: Path,
    artifact_dir: Path,
) -> dict[str, object]:
    """Feed the accepted v2 capture into Nautilus sandbox PAPER execution."""

    assert_exact_runtime_pin()
    assert_paper_boundary(seconds=seconds)
    artifact_path = artifact_dir / "paper.json"
    assert_new_files([artifact_path])
    dataset, ticks = _load_dataset(dataset_path)
    ages_ns = dataset_input_ages_ns(dataset)
    expected_inputs = expected_strategy_inputs(ticks)
    strategy = make_strategy(subscribe_market_data=False)
    loop = asyncio.new_event_loop()
    node = TradingNode(config=_paper_node_config(), loop=loop)
    node.kernel.cache.add_instrument(build_nautilus_instrument())
    node.trader.add_strategy(strategy)
    node.add_exec_client_factory(HYPERLIQUID, SandboxLiveExecClientFactory)
    node.build()

    started_monotonic = time.monotonic()
    try:
        reports = loop.run_until_complete(
            asyncio.wait_for(
                _drive_sandbox_paper(node, strategy, ticks),
                timeout=seconds,
            )
        )
    finally:
        node.dispose()
        asyncio.set_event_loop(None)
    elapsed = time.monotonic() - started_monotonic

    gate_errors: list[str] = []
    if strategy.inputs != expected_inputs:
        gate_errors.append("PAPER strategy inputs differ from the accepted v2 dataset.")
    if max(ages_ns) > STALE_AFTER_NS or min(ages_ns) < 0:
        gate_errors.append("PAPER input contains future-dated or stale events.")
    if strategy.rejections:
        gate_errors.append(f"PAPER produced order rejections: {strategy.rejections}")
    open_statuses = nonterminal_order_statuses(reports)
    if open_statuses:
        gate_errors.append(f"PAPER ended with nonterminal orders: {open_statuses}")
    if not strategy.is_complete:
        gate_errors.append("PAPER did not complete one flat entry/exit lifecycle.")
    last_price = Decimal(str(strategy.inputs[-1]["price"]))
    overlay = cost_overlay(
        strategy.fills,
        mark_price=last_price,
        assumptions=CostAssumptions(),
    )
    result = {
        "schema": D41_SCHEMA,
        "run_kind": "bounded_capture_sandbox_paper",
        "created_at_utc": utc_now_text(),
        "environment": current_environment(),
        "harness": harness_identity(),
        "execution_boundary": {
            "mode": "PAPER",
            "node_environment": "SANDBOX",
            "data_route": (
                "MarketEventEnvelope-v2 dataset -> envelope_to_trade_tick -> "
                "Nautilus DataEngine"
            ),
            "execution_client_factory": "SandboxLiveExecClientFactory",
            "registered_execution_client_factories": ["SandboxLiveExecClientFactory"],
            "credential_names_present_at_start": present_credential_names(),
            "duration_limit_seconds": seconds,
            "elapsed_seconds": round(elapsed, 6),
            "instrument_id": INSTRUMENT_ID.value,
            "native_accounting_currency": "USD",
            "native_accounting_is_proxy": True,
        },
        "input_boundary": {
            "dataset_events_sha256": dataset["events_sha256"],
            "primary_event_count": len(dataset["events"]),
            "strategy_input_count": len(strategy.inputs),
            "stale_after_ns": STALE_AFTER_NS,
            "max_input_age_ns": max(ages_ns),
            "receipt_clock": "UTC wall clock at collector ingress",
            "monotonic_clock": "local ordering and duration only",
        },
        "cost_assumptions": CostAssumptions().to_dict(),
        "business": strategy_business_result(strategy),
        "cost_overlay": overlay,
        "public_input_sample": strategy.inputs,
        "public_input_sample_sha256": sha256_json(strategy.inputs),
        "nautilus_reports": reports,
        "gate_errors": gate_errors,
        "limitations": [
            "Capture integrity is local; exchange origin is not cryptographically proven.",
            "SandboxExecutionClient hardcodes FillModel, MakerTakerFeeModel and zero latency.",
            "Hyperliquid instrument metadata exposes zero maker/taker fees.",
            "Sandbox execution does not settle funding.",
            "Sandbox execution reconciliation report methods return empty results.",
            (
                "Native sandbox accounting uses USD as an explicit proxy because the adapter "
                "publishes USD quote with USDC settlement and no USD/USDC conversion data."
            ),
        ],
    }
    write_json(artifact_path, result)
    if gate_errors:
        raise RuntimeError(" ".join(gate_errors))
    return result


def captured_public_records_to_dataset(
    envelopes: list[MarketEventEnvelope],
    *,
    capture_metadata: dict[str, object],
) -> dict[str, object]:
    if not envelopes:
        raise ValueError("No public events captured.")
    prior_ts_init: int | None = None
    prior_monotonic: int | None = None
    source_event_ids: set[str] = set()
    for envelope in envelopes:
        tick = envelope_to_trade_tick(envelope, prior_ts_init=prior_ts_init)
        if (
            prior_monotonic is not None
            and envelope.received_monotonic_ns < prior_monotonic
        ):
            raise ValueError("Capture receive monotonic time moved backwards.")
        if envelope.source_event_id in source_event_ids:
            raise ValueError("Capture contains a duplicate source event ID.")
        prior_ts_init = tick.ts_init
        prior_monotonic = envelope.received_monotonic_ns
        source_event_ids.add(envelope.source_event_id)
    records = [
        envelope_to_record(envelope, capture_ordinal=ordinal)
        for ordinal, envelope in enumerate(envelopes, start=1)
    ]
    return {
        "schema": "d41-public-trades-v1",
        "created_at_utc": utc_now_text(),
        "source_endpoint": "wss://api.hyperliquid.xyz/ws",
        "source_subscription": {"type": "trades", "coin": "BTC"},
        "instrument_id": INSTRUMENT_ID.value,
        "venue_neutral_contract_id": existing_btc_contract().canonical_instrument_id,
        "capture_order_preserved": True,
        "hyperliquid_tid_used_as_sequence": False,
        "capture_integrity": {
            "boundary": "MarketEventEnvelope-v2 -> envelope_to_trade_tick",
            "claim": "local_capture_integrity_only",
            "exchange_origin_cryptographically_proven": False,
            "receipt_clock_semantics": (
                "received_time is the collector UTC wall-clock receipt timestamp; "
                "event age is received_time minus exchange event_time"
            ),
            "monotonic_clock_semantics": (
                "received_monotonic_ns proves local receive ordering and bounded duration only; "
                "it is never subtracted from exchange event_time"
            ),
        },
        "harness": harness_identity(),
        "event_count": len(records),
        "events_sha256": sha256_json(records),
        "capture_metadata": capture_metadata,
        "events": records,
    }


def _dict_records(value: object, *, field_name: str) -> list[dict[str, object]]:
    if type(value) is not list or any(type(item) is not dict for item in value):
        raise TypeError(f"{field_name} must be a list of objects.")
    return value


def _artifact_deterministic_payload(artifact: dict[str, Any]) -> dict[str, object]:
    return {
        "schema": artifact.get("schema"),
        "dataset_events_sha256": artifact.get("dataset", {}).get("events_sha256"),
        "cost_assumptions": artifact.get("cost_assumptions"),
        "business": artifact.get("business"),
        "cost_overlay": artifact.get("cost_overlay"),
    }


def _validate_lifecycle(
    artifact: dict[str, Any],
    *,
    ticks: list[TradeTick],
    label: str,
) -> None:
    business = artifact.get("business")
    reports = artifact.get("nautilus_reports")
    if type(business) is not dict or type(reports) is not dict:
        raise TypeError(f"{label} lacks business or primary Nautilus reports.")
    signals = _dict_records(business.get("signals"), field_name=f"{label}.signals")
    intents = _dict_records(
        business.get("order_intents"),
        field_name=f"{label}.order_intents",
    )
    fills = _dict_records(business.get("fills"), field_name=f"{label}.fills")
    rejections = _dict_records(
        business.get("rejections"),
        field_name=f"{label}.rejections",
    )
    if len(signals) != 2 or len(intents) != 2 or len(fills) != 2 or rejections:
        raise ValueError(f"{label} does not contain one clean two-order lifecycle.")
    if business.get("tick_count") != len(ticks):
        raise ValueError(f"{label} tick count differs from primary events.")

    for index, signal in enumerate(signals):
        decision = signal.get("decision_ordinal")
        if type(decision) is not int or decision < 2 or decision > len(ticks):
            raise ValueError(f"{label} signal has an invalid decision ordinal.")
        if signal.get("lookahead_events") != 0:
            raise ValueError(f"{label} signal claims lookahead.")
        current_price = str(ticks[decision - 1].price)
        if signal.get("current_price") != current_price:
            raise ValueError(f"{label} signal price does not match primary input.")
        if index == 0:
            if signal.get("kind") != "two_trade_direction":
                raise ValueError(f"{label} entry signal kind is invalid.")
            if signal.get("previous_price") != str(ticks[decision - 2].price):
                raise ValueError(f"{label} entry signal previous price is invalid.")
            expected_side = decide_entry(
                Decimal(str(ticks[decision - 2].price)),
                Decimal(str(ticks[decision - 1].price)),
            )
            if expected_side is None or signal.get("side") != expected_side.name:
                raise ValueError(f"{label} entry signal side is not reproducible.")
        elif signal.get("kind") != "fixed_observed_tick_exit":
            raise ValueError(f"{label} exit signal kind is invalid.")

    position = Decimal(0)
    for index, (signal, intent, fill) in enumerate(zip(signals, intents, fills, strict=True)):
        decision = int(signal["decision_ordinal"])
        if intent.get("decision_ordinal") != decision:
            raise ValueError(f"{label} intent is not linked to its signal.")
        if intent.get("submission_ordinal") != decision + 1:
            raise ValueError(f"{label} intent was not deferred by exactly one input.")
        if intent.get("side") != signal.get("side"):
            raise ValueError(f"{label} intent side differs from its signal.")
        if intent.get("order_type") != "MARKET" or intent.get("reduce_only") is not (index == 1):
            raise ValueError(f"{label} intent execution fields are invalid.")
        quantity = Decimal(str(fill.get("quantity")))
        _exact_increment_text(quantity, increment=SIZE_INCREMENT, field_name="fill quantity")
        if Decimal(str(intent.get("quantity"))) != quantity:
            raise ValueError(f"{label} fill quantity differs from its intent.")
        if fill.get("fill_ordinal") != index + 1 or fill.get("side") != intent.get("side"):
            raise ValueError(f"{label} fill is not linked to its intent.")
        observed = fill.get("observed_tick_ordinal")
        if (
            type(observed) is not int
            or observed < int(intent["submission_ordinal"])
            or observed > len(ticks)
        ):
            raise ValueError(f"{label} fill ordinal is invalid.")
        fill_ts_event = fill.get("ts_event")
        matching_inputs = [
            (ordinal, tick)
            for ordinal, tick in enumerate(ticks, start=1)
            if tick.ts_init == fill_ts_event
        ]
        if len(matching_inputs) != 1:
            raise ValueError(f"{label} fill timestamp does not identify one primary input.")
        execution_ordinal, execution_tick = matching_inputs[0]
        if (
            execution_ordinal < int(intent["submission_ordinal"])
            or execution_ordinal > observed
            or Decimal(str(fill.get("price"))) != Decimal(str(execution_tick.price))
        ):
            raise ValueError(f"{label} fill does not match its execution input.")
        position += quantity if fill.get("side") == "BUY" else -quantity
        if Decimal(str(fill.get("position_after"))) != position:
            raise ValueError(f"{label} fill position chain is inconsistent.")
    if position != 0 or Decimal(str(business.get("final_position_quantity"))) != position:
        raise ValueError(f"{label} did not recompute to a flat position.")
    if business.get("complete") is not True:
        raise ValueError(f"{label} complete verdict differs from the recomputed lifecycle.")
    if signals[1].get("entry_fill_ordinal") != fills[0].get("observed_tick_ordinal"):
        raise ValueError(f"{label} exit signal is not linked to the entry fill.")

    orders = _dict_records(reports.get("orders"), field_name=f"{label}.reports.orders")
    order_fills = _dict_records(
        reports.get("order_fills"),
        field_name=f"{label}.reports.order_fills",
    )
    primary_fills = _dict_records(
        reports.get("fills"),
        field_name=f"{label}.reports.fills",
    )
    positions = _dict_records(
        reports.get("positions"),
        field_name=f"{label}.reports.positions",
    )
    if len(orders) != 2 or len(order_fills) != 2 or len(primary_fills) != 2:
        raise ValueError(f"{label} primary order/fill reports do not contain two fills.")
    if nonterminal_order_statuses(reports):
        raise ValueError(f"{label} has nonterminal primary orders.")
    for index, (intent, fill, order, order_fill, primary_fill) in enumerate(
        zip(intents, fills, orders, order_fills, primary_fills, strict=True)
    ):
        expected_reduce_only = index == 1
        if (
            order.get("status") != "FILLED"
            or order.get("type") != "MARKET"
            or order.get("time_in_force") != "IOC"
            or order.get("instrument_id") != INSTRUMENT_ID.value
            or order.get("side") != intent.get("side")
            or order.get("is_reduce_only") is not expected_reduce_only
            or Decimal(str(order.get("quantity"))) != Decimal(str(fill.get("quantity")))
            or Decimal(str(order.get("filled_qty"))) != Decimal(str(fill.get("quantity")))
        ):
            raise ValueError(f"{label} primary order report is inconsistent.")
        if order_fill.get("client_order_id") != order.get("client_order_id"):
            raise ValueError(f"{label} order-fill report is not linked to its order.")
        if (
            primary_fill.get("client_order_id") != order.get("client_order_id")
            or primary_fill.get("order_side") != fill.get("side")
            or Decimal(str(primary_fill.get("last_qty")))
            != Decimal(str(fill.get("quantity")))
            or Decimal(str(primary_fill.get("last_px"))) != Decimal(str(fill.get("price")))
        ):
            raise ValueError(f"{label} primary fill report is inconsistent.")
    if not positions or not any(
        position_record.get("side") == "FLAT"
        and Decimal(str(position_record.get("quantity"))) == 0
        for position_record in positions
    ):
        raise ValueError(f"{label} primary position report is not flat.")

    assumptions = CostAssumptions()
    if artifact.get("cost_assumptions") != assumptions.to_dict():
        raise ValueError(f"{label} cost assumptions were altered.")
    recomputed_overlay = cost_overlay(
        fills,
        mark_price=Decimal(str(ticks[-1].price)),
        assumptions=assumptions,
    )
    if artifact.get("cost_overlay") != recomputed_overlay:
        raise ValueError(f"{label} economics do not recompute from primary fills.")


def verify_gate(
    *,
    dataset_path: Path,
    artifact_dir: Path,
    source_path: Path,
) -> dict[str, object]:
    summary_path = artifact_dir / "gate-summary.json"
    assert_new_files([summary_path])
    dataset, ticks = _load_dataset(dataset_path)
    determinism = read_json(artifact_dir / "determinism.json")
    paper = read_json(artifact_dir / "paper.json")
    replay_one = read_json(artifact_dir / "replay-1.json")
    replay_two = read_json(artifact_dir / "replay-2.json")
    identity = harness_identity()
    if replay_one.get("run_kind") != "deterministic_replay" or replay_one.get(
        "run_label"
    ) != "replay-1":
        raise ValueError("Replay-1 run identity is invalid.")
    if replay_two.get("run_kind") != "deterministic_replay" or replay_two.get(
        "run_label"
    ) != "replay-2":
        raise ValueError("Replay-2 run identity is invalid.")

    for label, artifact in (
        ("replay-1", replay_one),
        ("replay-2", replay_two),
        ("paper", paper),
    ):
        if artifact.get("schema") != D41_SCHEMA:
            raise ValueError(f"{label} schema is invalid.")
        if artifact.get("harness") != identity:
            raise ValueError(f"{label} is not bound to the current harness and lockfile.")
        environment = artifact.get("environment")
        if type(environment) is not dict or environment.get("nautilus_trader") != NAUTILUS_PIN:
            raise ValueError(f"{label} did not use the exact Nautilus pin.")
        artifact_dataset = artifact.get("dataset")
        if label.startswith("replay"):
            if type(artifact_dataset) is not dict or (
                artifact_dataset.get("events_sha256") != dataset["events_sha256"]
                or artifact_dataset.get("event_count") != len(ticks)
                or artifact_dataset.get("capture_claim") != "local_capture_integrity_only"
            ):
                raise ValueError(f"{label} is not linked to the primary dataset.")
        _validate_lifecycle(artifact, ticks=ticks, label=label)

    payload_one = _artifact_deterministic_payload(replay_one)
    payload_two = _artifact_deterministic_payload(replay_two)
    digest_one = sha256_json(payload_one)
    digest_two = sha256_json(payload_two)
    if replay_one.get("deterministic_payload_sha256") != digest_one:
        raise ValueError("Replay-1 deterministic payload digest mismatch.")
    if replay_two.get("deterministic_payload_sha256") != digest_two:
        raise ValueError("Replay-2 deterministic payload digest mismatch.")
    replay_equal = payload_one == payload_two and digest_one == digest_two
    if not replay_equal:
        raise ValueError("The two replay business payloads are not identical.")
    if (
        determinism.get("schema") != D41_SCHEMA
        or determinism.get("gate") != "two_identical_replays"
        or determinism.get("pass") is not replay_equal
        or determinism.get("replay_1_sha256") != digest_one
        or determinism.get("replay_2_sha256") != digest_two
    ):
        raise ValueError("Determinism verdict fields do not match recomputed replay evidence.")

    expected_inputs = expected_strategy_inputs(ticks)
    if paper.get("run_kind") != "bounded_capture_sandbox_paper":
        raise ValueError("PAPER run kind is invalid.")
    if paper.get("public_input_sample") != expected_inputs:
        raise ValueError("PAPER did not consume the exact accepted v2 events.")
    if paper.get("public_input_sample_sha256") != sha256_json(expected_inputs):
        raise ValueError("PAPER input digest mismatch.")
    ages_ns = dataset_input_ages_ns(dataset)
    if min(ages_ns) < 0 or max(ages_ns) > STALE_AFTER_NS:
        raise ValueError("PAPER primary input contains future-dated or stale events.")
    expected_input_boundary = {
        "dataset_events_sha256": dataset["events_sha256"],
        "primary_event_count": len(ticks),
        "strategy_input_count": len(expected_inputs),
        "stale_after_ns": STALE_AFTER_NS,
        "max_input_age_ns": max(ages_ns),
        "receipt_clock": "UTC wall clock at collector ingress",
        "monotonic_clock": "local ordering and duration only",
    }
    if paper.get("input_boundary") != expected_input_boundary:
        raise ValueError("PAPER input-boundary fields do not match recomputed events.")
    paper_boundary = paper.get("execution_boundary")
    if type(paper_boundary) is not dict:
        raise TypeError("PAPER execution boundary must be an object.")
    if (
        paper_boundary.get("mode") != "PAPER"
        or paper_boundary.get("node_environment") != "SANDBOX"
        or paper_boundary.get("data_route")
        != "MarketEventEnvelope-v2 dataset -> envelope_to_trade_tick -> Nautilus DataEngine"
        or paper_boundary.get("execution_client_factory") != "SandboxLiveExecClientFactory"
        or paper_boundary.get("registered_execution_client_factories")
        != ["SandboxLiveExecClientFactory"]
        or paper_boundary.get("credential_names_present_at_start") != []
        or paper_boundary.get("instrument_id") != INSTRUMENT_ID.value
        or paper_boundary.get("native_accounting_currency") != "USD"
        or paper_boundary.get("native_accounting_is_proxy") is not True
    ):
        raise ValueError("PAPER execution boundary is not the credentialless sandbox route.")
    duration_limit = paper_boundary.get("duration_limit_seconds")
    elapsed = paper_boundary.get("elapsed_seconds")
    if (
        type(duration_limit) is not int
        or duration_limit <= 0
        or duration_limit > MAX_PAPER_SECONDS
        or type(elapsed) not in {int, float}
        or elapsed < 0
        or elapsed > duration_limit
    ):
        raise ValueError("PAPER duration evidence is outside the hard limit.")
    if paper.get("gate_errors") != []:
        raise ValueError("PAPER artifact contains gate errors.")
    if present_credential_names():
        raise RuntimeError("Verifier refuses credential-bearing environment state.")

    runtime_config = _paper_node_config()
    configured_exec = runtime_config.exec_clients
    if runtime_config.data_clients or set(configured_exec) != {HYPERLIQUID} or not isinstance(
        configured_exec[HYPERLIQUID],
        SandboxExecutionClientConfig,
    ):
        raise RuntimeError("Current PAPER runtime is not sandbox-only.")

    forbidden_names = (
        "Hyperliquid" + "ExecClientConfig",
        "Hyperliquid" + "LiveExecClientFactory",
    )
    source_files = (
        source_path,
        source_path.parent / "capture_public_dataset.py",
        source_path.parent / "run_fit_gate.py",
    )
    forbidden_runtime_imports = [
        f"{path.name}:{name}"
        for path in source_files
        for name in forbidden_names
        if name in path.read_text(encoding="utf-8")
    ]
    if forbidden_runtime_imports:
        raise RuntimeError(f"Forbidden venue execution imports found: {forbidden_runtime_imports}")

    gates = {
        "exact_pin": current_environment()["nautilus_trader"] == NAUTILUS_PIN,
        "local_capture_integrity": True,
        "one_instrument": True,
        "same_fail_closed_input_boundary": True,
        "no_stale_or_future_strategy_input": True,
        "two_recomputed_identical_replays": True,
        "primary_order_fill_position_lifecycle": True,
        "recomputed_economics": True,
        "credentialless_sandbox_paper": True,
        "no_venue_execution_factory": True,
        "paper_within_10_minutes": True,
        "no_open_sandbox_orders": True,
    }
    result = {
        "schema": D41_SCHEMA,
        "created_at_utc": utc_now_text(),
        "gates": gates,
        "all_hard_gates_passed": all(gates.values()),
        "recomputed": {
            "dataset_event_count": len(ticks),
            "dataset_events_sha256": dataset["events_sha256"],
            "max_input_age_ns": max(ages_ns),
            "paper_input_sample_sha256": sha256_json(expected_inputs),
            "replay_business_sha256": digest_one,
        },
        "forbidden_runtime_imports": forbidden_runtime_imports,
        "recommendation": "WRAP",
        "recommendation_basis": [
            (
                "The same accepted MarketEventEnvelope-v2 records drive deterministic replay "
                "and credentialless sandbox PAPER."
            ),
            "Project-owned economics overlay is required for fees, spread, slippage and funding.",
            "Project-owned persistence/restart/reconciliation boundary is still required.",
            "v1.231.0 is Beta and the final legacy-v1 line before v2 migration.",
        ],
    }
    write_json(summary_path, result)
    return result
