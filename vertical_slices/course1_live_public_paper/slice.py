"""Bounded COURSE-1 live-public PAPER soak.

Feeds the unchanged D01 smoke strategy from a short credentialless Hyperliquid
public BTC-PERP trade/BBO stream. D01 smoke-risk still runs first;
``paper_risk`` then applies PAPER sizing and portfolio hard limits and never
relaxes a D01 rejection. NautilusTrader stays WRAP-isolated. This is not
D22-B, 24/7 collection, funding settlement, venue reconciliation, or
promotion evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import ssl
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Protocol, cast

from fit_gates.d41_nautilus.fit_gate import (
    INSTRUMENT_ID,
    PRICE_INCREMENT,
    PROTECTED_CREDENTIAL_ENV_NAMES,
    STARTING_CASH,
    assert_exact_runtime_pin,
    assert_paper_boundary,
    build_nautilus_instrument,
    cost_overlay,
    envelope_to_trade_tick,
    existing_btc_contract,
    prepare_new_output_directory,
    sha256_json,
    write_json,
)
from nautilus_trader.adapters.hyperliquid import HYPERLIQUID
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import OrderSide

from hyperliquid_bot.course1_cockpit_artifacts import project_cockpit_artifacts
from hyperliquid_bot.hyperliquid_trades import (
    decode_hyperliquid_trades_frame,
    normalize_hyperliquid_trade,
)
from hyperliquid_bot.local_mode import require_local_paper_mode
from hyperliquid_bot.paper_risk import (
    PaperOrderIntent,
    extend_d01_risk_decision,
    paper_snapshot_for_bounded_book,
)
from hyperliquid_bot.reconstructable_paths import COURSE1_COCKPIT_FILE_NAMES
from vertical_slices.d01_btc_perp import slice as d01_slice
from vertical_slices.d01_btc_perp.slice import (
    DEFAULT_CONFIG,
    DEFAULT_COSTS,
    PAPER_SECONDS,
    D01SmokeStrategy,
    SliceConfig,
    _business,
    _derived_internal_paper_mode,
    _make_strategy,
    _paper_node_config,
    _reports,
    assert_local_boundary,
    evaluate_order_risk,
)

SOAK_SCHEMA: Final = "course1-live-public-paper-v1"
COMPLETION_SCHEMA: Final = "course1-live-public-paper-completed-v1"
COLLECTOR_VERSION: Final = "course1-live-public-paper"
HYPERLIQUID_MAINNET_WEBSOCKET_URL: Final = "wss://api.hyperliquid.xyz/ws"
DEFAULT_SOAK_SECONDS: Final = 45
MAX_SOAK_SECONDS: Final = 600
RECEIVE_IDLE_SECONDS: Final = 30.0
HEARTBEAT_INTERVAL_SECONDS: Final = 45.0
GREETING: Final = "Websocket connection established."
TRADES_SUBSCRIBE: Final = '{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}'
BBO_SUBSCRIBE: Final = '{"method":"subscribe","subscription":{"type":"bbo","coin":"BTC"}}'
EXPECTED_SUBSCRIPTIONS: Final = frozenset({"trades", "bbo"})
ALLOWED_CHANNELS: Final = frozenset(
    {"subscriptionResponse", "pong", "trades", "bbo"},
)
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
VENUE_AUTHORITATIVE_RECONCILIATION_IMPLEMENTED: Final = False


def _paper_guarded_risk(
    *,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal,
    reduce_only: bool,
    current_position: Decimal,
    config: SliceConfig,
) -> dict[str, object]:
    """Run D01 smoke-risk first, then PAPER sizing and portfolio hard limits."""

    decision = evaluate_order_risk(
        side=side,
        quantity=quantity,
        price=price,
        reduce_only=reduce_only,
        current_position=current_position,
        config=config,
    )
    return extend_d01_risk_decision(
        decision,
        snapshot=paper_snapshot_for_bounded_book(
            equity_usdc=config.starting_cash_usdc,
            price=price,
            current_position=current_position,
        ),
        intent=PaperOrderIntent(
            side=side.name,
            quantity=quantity,
            price=price,
            reduce_only=reduce_only,
            stop_distance_fraction=config.assumed_stop_distance_fraction,
        ),
        trading_mode=os.environ.get("TRADING_MODE"),
    )


@contextmanager
def _bind_paper_risk_to_d01_path() -> Iterator[None]:
    """Install PAPER hard limits on the D01 function the smoke strategy calls."""

    original = d01_slice.evaluate_order_risk
    d01_slice.evaluate_order_risk = _paper_guarded_risk
    try:
        yield
    finally:
        d01_slice.evaluate_order_risk = original


_TRANSPORT_PRIVACY_LOGGER: Final = logging.Logger(
    "hyperliquid_bot.course1_live_public_paper_transport",
    level=logging.CRITICAL + 1,
)
_TRANSPORT_PRIVACY_LOGGER.disabled = True
_TRANSPORT_PRIVACY_LOGGER.propagate = False
_TRANSPORT_PRIVACY_LOGGER.addHandler(logging.NullHandler())


class SoakBoundaryError(RuntimeError):
    """Raised before any public socket or sandbox node is constructed."""


class PublicStreamError(RuntimeError):
    """A public Hyperliquid application message violated the soak boundary."""


class WebSocketConnection(Protocol):
    """Small transport surface for the real connection and offline fakes."""

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


type ConnectionFactory = Callable[[], AbstractAsyncContextManager[WebSocketConnection]]
type Clock = Callable[[], datetime]
type MonotonicClock = Callable[[], int]


@dataclass(frozen=True, slots=True)
class PublicBboQuote:
    """One fail-closed public BTC BBO observation. Not a trade or venue fill."""

    bid_price: Decimal
    bid_size: Decimal
    ask_price: Decimal
    ask_size: Decimal
    mid_price: Decimal
    spread: Decimal
    event_time: datetime
    received_time: datetime
    received_monotonic_ns: int

    def to_record(self) -> dict[str, object]:
        return {
            "bid_price": str(self.bid_price),
            "bid_size": str(self.bid_size),
            "ask_price": str(self.ask_price),
            "ask_size": str(self.ask_size),
            "mid_price": str(self.mid_price),
            "spread": str(self.spread),
            "event_time": self.event_time.isoformat(),
            "received_time": self.received_time.isoformat(),
            "received_monotonic_ns": self.received_monotonic_ns,
        }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{path.name} must contain one JSON object.")
    return value


def _require_text(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a built-in string.")
    text = value
    if not text or text != text.strip() or not text.isprintable():
        raise ValueError(f"{field_name} must be non-empty printable text without outer whitespace.")
    return text


def _parse_positive_decimal(value: object, *, field_name: str) -> Decimal:
    text = _require_text(value, field_name=field_name)
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be a valid decimal string.") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field_name} must be finite and greater than zero.")
    return parsed


def _require_non_negative_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be a built-in integer.")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    return value


def _epoch_milliseconds_to_utc(time_ms: int) -> datetime:
    seconds, milliseconds = divmod(time_ms, 1_000)
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            seconds=seconds,
            milliseconds=milliseconds,
        )
    except OverflowError as exc:
        raise ValueError("time must be within the supported UTC datetime range.") from exc


def _require_price_increment(value: Decimal, *, field_name: str) -> Decimal:
    if value.quantize(PRICE_INCREMENT) != value:
        raise ValueError(f"{field_name} is not exactly aligned to increment {PRICE_INCREMENT}.")
    return value


def _utc_now() -> datetime:
    return datetime.now(UTC)


def assert_soak_boundary(
    *,
    environ: Mapping[str, str] | None = None,
    seconds: int = DEFAULT_SOAK_SECONDS,
) -> None:
    """Fail closed to PAPER, refuse credentials, and bound the soak duration."""

    source = os.environ if environ is None else environ
    try:
        require_local_paper_mode(source.get("TRADING_MODE"))
    except Exception as exc:
        raise SoakBoundaryError("Soak refuses a non-PAPER TRADING_MODE.") from exc
    internal_mode = source.get("D41_EXECUTION_MODE")
    if internal_mode not in {None, "PAPER"}:
        raise SoakBoundaryError("Soak refuses a non-PAPER internal execution mode.")
    if type(seconds) is not int or seconds <= 0 or seconds > MAX_SOAK_SECONDS:
        raise SoakBoundaryError(
            f"Soak duration must be an integer between 1 and {MAX_SOAK_SECONDS} seconds."
        )
    found = sorted(name for name in PROTECTED_CREDENTIAL_ENV_NAMES if source.get(name))
    if found:
        raise SoakBoundaryError(
            f"Soak refuses to start while credential environment names exist: {found}"
        )
    effective = dict(source)
    effective["D41_EXECUTION_MODE"] = "PAPER"
    try:
        assert_local_boundary(environ=effective, seconds=seconds)
        assert_paper_boundary(seconds=seconds, environ=effective)
        assert_exact_runtime_pin()
    except SoakBoundaryError:
        raise
    except Exception as exc:
        raise SoakBoundaryError(str(exc)) from exc


def source_identity() -> dict[str, object]:
    slice_root = Path(__file__).resolve().parent
    d01_root = REPOSITORY_ROOT / "vertical_slices" / "d01_btc_perp"
    d41_root = REPOSITORY_ROOT / "fit_gates" / "d41_nautilus"
    files = {
        "vertical_slices/course1_live_public_paper/slice.py": _sha256_file(slice_root / "slice.py"),
        "vertical_slices/course1_live_public_paper/run_slice.py": _sha256_file(
            slice_root / "run_slice.py"
        ),
        "vertical_slices/d01_btc_perp/slice.py": _sha256_file(d01_root / "slice.py"),
        "src/hyperliquid_bot/paper_risk.py": _sha256_file(
            REPOSITORY_ROOT / "src" / "hyperliquid_bot" / "paper_risk.py"
        ),
        "fit_gates/d41_nautilus/fit_gate.py": _sha256_file(d41_root / "fit_gate.py"),
        "fit_gates/d41_nautilus/requirements.lock": _sha256_file(d41_root / "requirements.lock"),
    }
    return {"algorithm": "sha256", "files": files, "digest": sha256_json(files)}


def run_identity(
    *,
    run_id: str,
    seconds: int,
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
    return sha256_json(
        {
            "schema": SOAK_SCHEMA,
            "run_id": run_id,
            "instrument_id": INSTRUMENT_ID.value,
            "feed": "hyperliquid-public-btc-perp-trades-bbo",
            "seconds": seconds,
            "config": config.to_dict(),
            "cost_assumptions": DEFAULT_COSTS.to_dict(),
            "source_digest": source["digest"],
            "venue_authoritative_reconciliation": (VENUE_AUTHORITATIVE_RECONCILIATION_IMPLEMENTED),
        }
    )


def decode_public_bbo_frame(
    frame: object,
    *,
    received_time: datetime,
    received_monotonic_ns: int,
) -> PublicBboQuote:
    """Decode one public Hyperliquid BTC BBO frame. Prices stay fail-closed."""

    if type(frame) is not dict:
        raise TypeError("BBO frame must be a built-in object.")
    message = cast(dict[object, object], frame)
    if _require_text(message.get("channel"), field_name="channel") != "bbo":
        raise ValueError("BBO frame channel must be exactly bbo.")
    data = message.get("data")
    if type(data) is not dict:
        raise TypeError("BBO data must be a built-in object.")
    payload = cast(dict[object, object], data)
    if _require_text(payload.get("coin"), field_name="coin") != "BTC":
        raise ValueError("Soak accepts only the public BTC BBO.")
    event_time = _epoch_milliseconds_to_utc(
        _require_non_negative_int(payload.get("time"), field_name="time")
    )
    levels = payload.get("bbo")
    if type(levels) is not list or len(levels) != 2:
        raise ValueError("BBO must contain exactly bid and ask levels.")
    bid_level, ask_level = cast(list[object], levels)
    if type(bid_level) is not dict or type(ask_level) is not dict:
        raise TypeError("Each BBO level must be a built-in object.")
    bid = cast(dict[object, object], bid_level)
    ask = cast(dict[object, object], ask_level)
    bid_price = _require_price_increment(
        _parse_positive_decimal(bid.get("px"), field_name="bid px"),
        field_name="bid px",
    )
    ask_price = _require_price_increment(
        _parse_positive_decimal(ask.get("px"), field_name="ask px"),
        field_name="ask px",
    )
    bid_size = _parse_positive_decimal(bid.get("sz"), field_name="bid sz")
    ask_size = _parse_positive_decimal(ask.get("sz"), field_name="ask sz")
    _require_non_negative_int(bid.get("n"), field_name="bid n")
    _require_non_negative_int(ask.get("n"), field_name="ask n")
    if bid_price >= ask_price:
        raise ValueError("Soak refuses a crossed or locked public BBO.")
    age = Decimal(str((received_time - event_time).total_seconds()))
    if age < 0 or age > Decimal(5):
        raise ValueError("Soak refuses future-dated or stale public BBO.")
    spread = ask_price - bid_price
    mid_price = (bid_price + ask_price) / Decimal(2)
    return PublicBboQuote(
        bid_price=bid_price,
        bid_size=bid_size,
        ask_price=ask_price,
        ask_size=ask_size,
        mid_price=mid_price,
        spread=spread,
        event_time=event_time,
        received_time=received_time,
        received_monotonic_ns=received_monotonic_ns,
    )


def _decode_json_object(raw: str) -> dict[object, object]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PublicStreamError("Public WebSocket application message is not valid JSON.") from exc
    if type(decoded) is not dict:
        raise PublicStreamError("Public WebSocket application message must be one object.")
    return cast(dict[object, object], decoded)


def _subscription_type(document: dict[object, object]) -> str:
    data = document.get("data")
    if type(data) is not dict:
        raise PublicStreamError("Subscription acknowledgement is missing data.")
    payload = cast(dict[object, object], data)
    if payload.get("method") != "subscribe":
        raise PublicStreamError("Subscription acknowledgement has an invalid method.")
    subscription = payload.get("subscription")
    if type(subscription) is not dict:
        raise PublicStreamError("Subscription acknowledgement is missing subscription.")
    spec = cast(dict[object, object], subscription)
    channel = spec.get("type")
    coin = spec.get("coin")
    if type(channel) is not str or channel not in EXPECTED_SUBSCRIPTIONS:
        raise PublicStreamError("Subscription acknowledgement is outside trades/BBO.")
    if coin != "BTC":
        raise PublicStreamError("Subscription acknowledgement escaped the BTC instrument.")
    return channel


@asynccontextmanager
async def _mainnet_connection() -> AsyncIterator[WebSocketConnection]:
    from websockets.asyncio.client import connect

    async with connect(
        HYPERLIQUID_MAINNET_WEBSOCKET_URL,
        ssl=ssl.create_default_context(),
        max_size=8 * 1024 * 1024,
        logger=_TRANSPORT_PRIVACY_LOGGER,
    ) as socket:
        yield socket


def _inbound_text(message: str | bytes) -> str:
    if type(message) is bytes:
        try:
            return message.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PublicStreamError("Public WebSocket message is not UTF-8 text.") from exc
    if type(message) is not str:
        raise PublicStreamError("Public WebSocket message must be text.")
    return message


async def _collect_and_drive(
    node: TradingNode,
    strategy: D01SmokeStrategy,
    *,
    seconds: int,
    config: SliceConfig,
    connection_factory: ConnectionFactory,
    utc_now: Clock,
    monotonic_ns: MonotonicClock,
) -> tuple[dict[str, object], dict[str, object]]:
    loop = asyncio.get_running_loop()
    trade_topic = f"data.trades.{HYPERLIQUID}.{INSTRUMENT_ID.symbol.value}"
    subscribed = False
    run_task = asyncio.create_task(node.run_async(), name="course1-live-public-paper-node")
    accepted: list[str] = []
    source_event_ids: set[str] = set()
    trade_records: list[dict[str, object]] = []
    bbo_records: list[dict[str, object]] = []
    rejected = 0
    adapter_rejected = 0
    preflight_done = False
    prior_ts_init: int | None = None
    deadline = loop.time() + seconds
    status = "BOUNDED_TIMEOUT"
    risk_error: str | None = None
    try:
        for _ in range(1_000):
            if node.is_running():
                break
            if run_task.done():
                await run_task
            await asyncio.sleep(0)
        if not node.is_running():
            raise RuntimeError("Soak sandbox PAPER node did not start.")
        node.kernel.msgbus.subscribe(trade_topic, strategy.handle_trade_tick)
        subscribed = True

        async with connection_factory() as connection:
            await connection.send(TRADES_SUBSCRIBE)
            await connection.send(BBO_SUBSCRIBE)
            next_heartbeat = loop.time() + HEARTBEAT_INTERVAL_SECONDS
            buffered_frames: list[dict[object, object]] = []
            while loop.time() < deadline and not strategy.is_complete:
                timeout = min(
                    RECEIVE_IDLE_SECONDS,
                    max(0.01, deadline - loop.time()),
                    max(0.01, next_heartbeat - loop.time()),
                )
                try:
                    raw = _inbound_text(await asyncio.wait_for(connection.recv(), timeout=timeout))
                except TimeoutError:
                    if loop.time() >= deadline:
                        break
                    if loop.time() >= next_heartbeat:
                        await connection.send('{"method":"ping"}')
                        next_heartbeat = loop.time() + HEARTBEAT_INTERVAL_SECONDS
                        continue
                    raise PublicStreamError(
                        "Public stream was idle longer than the soak receive bound."
                    ) from None
                if raw == GREETING:
                    continue
                document = _decode_json_object(raw)
                channel = document.get("channel")
                if type(channel) is not str or channel not in ALLOWED_CHANNELS:
                    raise PublicStreamError("Public stream emitted a channel outside trades/BBO.")
                if channel == "pong":
                    continue
                if channel == "subscriptionResponse":
                    acknowledged = _subscription_type(document)
                    if acknowledged in accepted:
                        raise PublicStreamError("Public stream repeated a subscription ACK.")
                    accepted.append(acknowledged)
                    continue
                if channel not in accepted:
                    raise PublicStreamError(
                        "Public market data arrived before its subscription was acknowledged."
                    )
                if frozenset(accepted) != EXPECTED_SUBSCRIPTIONS:
                    buffered_frames.append(document)
                    continue
                frames = buffered_frames
                buffered_frames = []
                frames.append(document)
                instrument = existing_btc_contract()
                for document in frames:
                    channel = document.get("channel")
                    received_time = utc_now()
                    received_monotonic = monotonic_ns()
                    if channel == "bbo":
                        try:
                            quote = decode_public_bbo_frame(
                                document,
                                received_time=received_time,
                                received_monotonic_ns=received_monotonic,
                            )
                        except ValueError as exc:
                            detail = str(exc)
                            if any(
                                token in detail for token in ("stale", "future-dated", "increment")
                            ):
                                adapter_rejected += 1
                                continue
                            raise
                        bbo_records.append(quote.to_record())
                        continue
                    trades = decode_hyperliquid_trades_frame(document)
                    for trade in trades:
                        envelope = normalize_hyperliquid_trade(
                            trade,
                            instrument_registry=(instrument,),
                            received_time=received_time,
                            received_monotonic_ns=received_monotonic,
                            collector_version=COLLECTOR_VERSION,
                            collector_commit="unspecified-soak-runtime",
                            is_gap=False,
                        )
                        if envelope.source_event_id in source_event_ids:
                            raise PublicStreamError("Public stream reused a trade source-event ID.")
                        try:
                            tick = envelope_to_trade_tick(envelope, prior_ts_init=prior_ts_init)
                        except ValueError as exc:
                            detail = str(exc)
                            if any(
                                token in detail
                                for token in ("stale", "future-dated", "increment", "gap")
                            ):
                                adapter_rejected += 1
                                continue
                            raise
                        prior_ts_init = tick.ts_init
                        source_event_ids.add(envelope.source_event_id)
                        trade_records.append(
                            {
                                "price": str(tick.price),
                                "size": str(tick.size),
                                "aggressor_side": tick.aggressor_side.name,
                                "source_event_id": envelope.source_event_id,
                                "ts_event": tick.ts_event,
                                "ts_init": tick.ts_init,
                            }
                        )
                        if not preflight_done:
                            preflight_done = True
                            current_price = Decimal(str(tick.price))
                            for side in (OrderSide.BUY, OrderSide.SELL):
                                preview = _paper_guarded_risk(
                                    side=side,
                                    quantity=config.order_quantity_btc,
                                    price=current_price,
                                    reduce_only=False,
                                    current_position=Decimal(0),
                                    config=config,
                                )
                                if preview["approved"] is not True:
                                    risk_error = (
                                        "D01 preflight risk rejected the fixed smoke size: "
                                        f"{preview}"
                                    )
                                    status = "RISK_REJECTED"
                                    rejected += 1
                                    break
                            if status == "RISK_REJECTED":
                                break
                        if status == "RISK_REJECTED":
                            break
                        node.kernel.data_engine.process(tick)
                        for _ in range(4):
                            await asyncio.sleep(0)
                        if (
                            strategy.risk_decisions
                            and strategy.risk_decisions[-1].get("approved") is not True
                        ):
                            decision = strategy.risk_decisions[-1]
                            risk_error = f"D01 risk rejected {decision.get('reasons')}"
                            status = "RISK_REJECTED"
                            rejected += 1
                            break
                    if status == "RISK_REJECTED":
                        break
                if status == "RISK_REJECTED":
                    break
            if strategy.is_complete:
                status = "COMPLETED_FLAT"
            elif status != "RISK_REJECTED":
                status = "BOUNDED_TIMEOUT"
        for _ in range(1_000):
            if strategy.is_complete or strategy.rejections or status == "RISK_REJECTED":
                break
            await asyncio.sleep(0)
        if strategy.is_complete:
            status = "COMPLETED_FLAT"
        reports = _reports(node.trader)
        stream = {
            "schema": SOAK_SCHEMA,
            "feed": "hyperliquid-public-btc-perp-trades-bbo",
            "websocket_url": HYPERLIQUID_MAINNET_WEBSOCKET_URL,
            "credentialless": True,
            "subscriptions_acknowledged": accepted,
            "trade_count": len(trade_records),
            "bbo_count": len(bbo_records),
            "trades": trade_records,
            "bbo": bbo_records,
            "risk_rejections": rejected,
            "adapter_rejected_count": adapter_rejected,
            "risk_error": risk_error,
            "status": status,
        }
        if not trade_records:
            raise PublicStreamError("Soak received no accepted public BTC trades.")
        if not bbo_records:
            raise PublicStreamError("Soak received no accepted public BTC BBO quotes.")
        return reports, stream
    finally:
        if subscribed:
            node.kernel.msgbus.unsubscribe(trade_topic, strategy.handle_trade_tick)
        if node.is_running():
            await node.stop_async()
        await run_task


def _mark_price(
    strategy: D01SmokeStrategy,
    stream: dict[str, object],
) -> Decimal:
    if strategy.inputs:
        return Decimal(str(strategy.inputs[-1]["price"]))
    trades = stream.get("trades")
    if type(trades) is list and trades:
        first = trades[-1]
        if type(first) is dict:
            return Decimal(str(first["price"]))
    raise PublicStreamError("Soak has no public trade price for the cost overlay.")


def _paper_payload(
    *,
    identity: str,
    strategy: D01SmokeStrategy,
    reports: dict[str, object],
    stream: dict[str, object],
) -> dict[str, object]:
    business = _business(strategy)
    fills = business.get("fills")
    if type(fills) is not list or any(type(fill) is not dict for fill in fills):
        raise TypeError("Soak business fills must be a list of objects.")
    overlay = cost_overlay(
        fills,
        mark_price=_mark_price(strategy, stream),
        assumptions=DEFAULT_COSTS,
    )
    deterministic_payload = {
        "schema": SOAK_SCHEMA,
        "run_identity": identity,
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "business": business,
        "cost_overlay": overlay,
        "stream_status": stream.get("status"),
        "trade_count": stream.get("trade_count"),
        "bbo_count": stream.get("bbo_count"),
    }
    return {
        "schema": SOAK_SCHEMA,
        "run_kind": "live_public_sandbox_paper",
        "run_identity": identity,
        "execution": {
            "mode": "PAPER",
            "node_environment": "SANDBOX",
            "instrument_id": INSTRUMENT_ID.value,
            "native_accounting_currency": "USD",
            "native_accounting_is_usdc_proxy": True,
            "registered_venue_execution_client": False,
            "execution_client_factories": ["SandboxLiveExecClientFactory"],
            "credential_names_present_at_start": [],
            "venue_orders_submitted": False,
            "signing": False,
            "testnet": False,
            "shadow": False,
            "live": False,
            "venue_authoritative_reconciliation": False,
        },
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "business": business,
        "cost_overlay": overlay,
        "nautilus_reports": reports,
        "deterministic_payload_sha256": sha256_json(deterministic_payload),
    }


def _run_claim_payload(
    *,
    run_id: str,
    identity: str,
    seconds: int,
    config: SliceConfig,
    source: dict[str, object],
) -> dict[str, object]:
    return {
        "schema": SOAK_SCHEMA,
        "state": "STARTED_FAIL_CLOSED",
        "run_id": run_id,
        "run_identity": identity,
        "mode": "PAPER",
        "seconds": seconds,
        "feed": "hyperliquid-public-btc-perp-trades-bbo",
        "websocket_url": HYPERLIQUID_MAINNET_WEBSOCKET_URL,
        "config_sha256": sha256_json(config.to_dict()),
        "source_sha256": source["digest"],
        "resume_policy": "never resume or overwrite an existing soak run directory",
        "d22b_venue_authoritative_reconciliation": False,
        "preflight": {
            "strategy_class": (f"{D01SmokeStrategy.__module__}.{D01SmokeStrategy.__qualname__}"),
            "order_quantity_btc": str(config.order_quantity_btc),
            "max_entry_notional_usdc": str(config.max_entry_notional_usdc),
            "max_assumed_loss_usdc": str(config.max_assumed_loss_usdc),
            "same_d01_smoke_risk": True,
        },
    }


def _require_cockpit_artifacts(
    artifact_dir: Path,
    *,
    paper: dict[str, object],
    stream: dict[str, object],
) -> dict[str, str]:
    """Recompute create-only Cockpit projections and return their digests."""

    expected = project_cockpit_artifacts(paper=paper, stream=stream)
    hashes: dict[str, str] = {}
    for name, payload in expected.items():
        path = artifact_dir / name
        stored = _read_object(path)
        if stored != payload:
            raise ValueError(f"Cockpit artifact {name} does not recompute exactly.")
        hashes[name] = _sha256_file(path)
    return hashes


def _completion_payload(
    *,
    run_id: str,
    identity: str,
    seconds: int,
    config: SliceConfig,
    source: dict[str, object],
    artifact_dir: Path,
    claim_path: Path,
    stream_path: Path,
    paper_path: Path,
    paper: dict[str, object],
    stream: dict[str, object],
) -> dict[str, object]:
    claim = _read_object(claim_path)
    expected_claim = _run_claim_payload(
        run_id=run_id,
        identity=identity,
        seconds=seconds,
        config=config,
        source=source,
    )
    if claim != expected_claim:
        raise ValueError("Soak run claim does not recompute exactly.")
    stored_stream = _read_object(stream_path)
    if stored_stream != stream:
        raise ValueError("Soak public-stream artifact does not recompute exactly.")
    _paper_payload_from_stored(identity=identity, paper=paper, stream=stream)
    if paper.get("cost_assumptions") != DEFAULT_COSTS.to_dict():
        raise ValueError("Soak cost assumptions do not match the D01 overlay.")
    business = paper.get("business")
    if type(business) is not dict:
        raise TypeError("Soak PAPER business must be an object.")
    fills = business.get("fills")
    if type(fills) is not list or any(type(fill) is not dict for fill in fills):
        raise TypeError("Soak PAPER fills must be a list of objects.")
    overlay = paper.get("cost_overlay")
    if type(overlay) is not dict:
        raise TypeError("Soak PAPER cost overlay must be an object.")
    status = stream.get("status")
    if status not in {"COMPLETED_FLAT", "BOUNDED_TIMEOUT", "RISK_REJECTED"}:
        raise ValueError("Soak status is outside the documented bound.")
    if business.get("strategy_class") != (
        f"{D01SmokeStrategy.__module__}.{D01SmokeStrategy.__qualname__}"
    ):
        raise ValueError("Soak did not use the D01 smoke strategy class.")
    return {
        "schema": COMPLETION_SCHEMA,
        "status": status,
        "decision": "BOUNDED_LIVE_PUBLIC_PAPER_SOAK",
        "run_id": run_id,
        "run_identity": identity,
        "route": (
            "Hyperliquid public trades/BBO -> v2 trade adapter -> D01SmokeStrategy -> "
            "project risk -> credentialless sandbox PAPER"
        ),
        "instrument_id": INSTRUMENT_ID.value,
        "source_identity": source,
        "config": config.to_dict(),
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "seconds": seconds,
        "artifacts": {
            "run-claim.json": _sha256_file(claim_path),
            "public-stream.json": _sha256_file(stream_path),
            "paper.json": _sha256_file(paper_path),
            **_require_cockpit_artifacts(artifact_dir, paper=paper, stream=stream),
        },
        "recomputed": {
            "same_d01_strategy_risk_code": True,
            "order_count": len(cast(list[object], business.get("order_intents"))),
            "fill_count": len(fills),
            "final_position_btc": overlay["final_position_btc"],
            "ending_cash_usdc_assumed": overlay["ending_cash_usdc_assumed"],
            "ending_equity_usdc_assumed": overlay["ending_equity_usdc_assumed"],
            "net_pnl_usdc_assumed": overlay["net_pnl_usdc_assumed"],
            "trade_count": stream.get("trade_count"),
            "bbo_count": stream.get("bbo_count"),
            "complete": business.get("complete") is True,
        },
        "limitations": [
            "This is a bounded public-data engineering soak, not 24/7 operation.",
            "Funding is zero because no settlement boundary is modeled or crossed.",
            "D22-B venue-authoritative reconciliation is not implemented.",
            "Sandbox order, cash, and position state has no venue truth.",
            "USD is an explicit 1:1 USDC accounting proxy for the D01 overlay.",
            "This is not alpha, profitability, TESTNET, SHADOW, or LIVE evidence.",
            "Cockpit paper-position/paper-pnl/orders/fills are create-only projections.",
            (
                "Nautilus 1.231.0 remains isolated behind D01 and D41 project code; "
                "it is not a root dependency."
            ),
        ],
    }


def _paper_payload_from_stored(
    *,
    identity: str,
    paper: dict[str, object],
    stream: dict[str, object],
) -> dict[str, object]:
    business = paper.get("business")
    reports = paper.get("nautilus_reports")
    if type(business) is not dict or type(reports) is not dict:
        raise TypeError("Soak PAPER business/reports must be objects.")
    fills = business.get("fills")
    if type(fills) is not list or any(type(fill) is not dict for fill in fills):
        raise TypeError("Soak PAPER fills must be a list of objects.")
    overlay = paper.get("cost_overlay")
    if type(overlay) is not dict:
        raise TypeError("Soak PAPER cost overlay must be an object.")
    mark = Decimal(str(overlay.get("mark_price")))
    expected_overlay = cost_overlay(fills, mark_price=mark, assumptions=DEFAULT_COSTS)
    if overlay != expected_overlay:
        raise ValueError("Soak cash/PnL economics do not recompute.")
    deterministic_payload = {
        "schema": SOAK_SCHEMA,
        "run_identity": identity,
        "cost_assumptions": DEFAULT_COSTS.to_dict(),
        "business": business,
        "cost_overlay": overlay,
        "stream_status": stream.get("status"),
        "trade_count": stream.get("trade_count"),
        "bbo_count": stream.get("bbo_count"),
    }
    if paper.get("deterministic_payload_sha256") != sha256_json(deterministic_payload):
        raise ValueError("Soak deterministic business digest does not recompute.")
    execution = paper.get("execution")
    expected_execution = {
        "mode": "PAPER",
        "node_environment": "SANDBOX",
        "instrument_id": INSTRUMENT_ID.value,
        "native_accounting_currency": "USD",
        "native_accounting_is_usdc_proxy": True,
        "registered_venue_execution_client": False,
        "execution_client_factories": ["SandboxLiveExecClientFactory"],
        "credential_names_present_at_start": [],
        "venue_orders_submitted": False,
        "signing": False,
        "testnet": False,
        "shadow": False,
        "live": False,
        "venue_authoritative_reconciliation": False,
    }
    if execution != expected_execution:
        raise ValueError("Soak PAPER execution boundary does not recompute.")
    return paper


def verify_soak_run(
    artifact_dir: Path,
    *,
    run_id: str | None = None,
    seconds: int | None = None,
    config: SliceConfig = DEFAULT_CONFIG,
) -> dict[str, object]:
    """Recompute claim, stream, economics, and PAPER mode from create-only artifacts."""

    paths = {
        "claim": artifact_dir / "run-claim.json",
        "stream": artifact_dir / "public-stream.json",
        "paper": artifact_dir / "paper.json",
        "completion": artifact_dir / "completed-run.json",
        **{
            name: artifact_dir / name
            for name in COURSE1_COCKPIT_FILE_NAMES
            if name != "run-claim.json"
        },
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"Soak refuses incomplete run evidence: {missing}")
    completion = _read_object(paths["completion"])
    stored_run_id = completion.get("run_id")
    if type(stored_run_id) is not str:
        raise TypeError("Soak completed run_id must be a string.")
    if run_id is not None and run_id != stored_run_id:
        raise ValueError("Soak verify run_id does not match the completed artifact.")
    stored_seconds = completion.get("seconds")
    if type(stored_seconds) is not int:
        raise TypeError("Soak completed seconds must be an integer.")
    if seconds is not None and seconds != stored_seconds:
        raise ValueError("Soak verify duration does not match the completed artifact.")
    source = source_identity()
    identity = run_identity(
        run_id=stored_run_id,
        seconds=stored_seconds,
        config=config,
        source=source,
    )
    paper = _read_object(paths["paper"])
    stream = _read_object(paths["stream"])
    _paper_payload_from_stored(identity=identity, paper=paper, stream=stream)
    expected = _completion_payload(
        run_id=stored_run_id,
        identity=identity,
        seconds=stored_seconds,
        config=config,
        source=source,
        artifact_dir=artifact_dir,
        claim_path=paths["claim"],
        stream_path=paths["stream"],
        paper_path=paths["paper"],
        paper=paper,
        stream=stream,
    )
    if completion != expected:
        raise ValueError("Soak completed-run checkpoint does not recompute exactly.")
    return {
        "schema": SOAK_SCHEMA,
        "status": "VERIFIED",
        "run_id": stored_run_id,
        "run_identity": identity,
        "decision": completion.get("status"),
        "source_completed_run_sha256": _sha256_file(paths["completion"]),
        "venue_authoritative_reconciliation": False,
    }


def run_soak(
    *,
    run_id: str,
    artifact_dir: Path,
    seconds: int = DEFAULT_SOAK_SECONDS,
    config: SliceConfig = DEFAULT_CONFIG,
    connection_factory: ConnectionFactory | None = None,
    utc_now: Clock = _utc_now,
    monotonic_ns: MonotonicClock = time.monotonic_ns,
) -> dict[str, object]:
    """Run one bounded credentialless public-data PAPER soak. Create-only."""

    assert_soak_boundary(seconds=seconds)
    if artifact_dir.exists():
        raise FileExistsError(f"Soak refuses to reuse existing run directory: {artifact_dir}")
    if config.starting_cash_usdc != STARTING_CASH:
        raise ValueError("Soak starting cash must match the bounded D01 account.")
    source = source_identity()
    identity = run_identity(run_id=run_id, seconds=seconds, config=config, source=source)
    claim = _run_claim_payload(
        run_id=run_id,
        identity=identity,
        seconds=seconds,
        config=config,
        source=source,
    )
    factory = connection_factory if connection_factory is not None else _mainnet_connection
    prepare_new_output_directory(artifact_dir)
    claim_path = artifact_dir / "run-claim.json"
    stream_path = artifact_dir / "public-stream.json"
    paper_path = artifact_dir / "paper.json"
    completion_path = artifact_dir / "completed-run.json"
    write_json(claim_path, claim)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    strategy = _make_strategy(identity=identity, config=config, subscribe_market_data=False)
    node = TradingNode(config=_paper_node_config(config), loop=loop)
    node.kernel.cache.add_instrument(build_nautilus_instrument())
    node.trader.add_strategy(strategy)
    node.add_exec_client_factory(HYPERLIQUID, SandboxLiveExecClientFactory)
    node.build()
    try:
        with _derived_internal_paper_mode(), _bind_paper_risk_to_d01_path():
            reports, stream = loop.run_until_complete(
                asyncio.wait_for(
                    _collect_and_drive(
                        node,
                        strategy,
                        seconds=seconds,
                        config=config,
                        connection_factory=factory,
                        utc_now=utc_now,
                        monotonic_ns=monotonic_ns,
                    ),
                    timeout=seconds + PAPER_SECONDS,
                )
            )
    finally:
        node.dispose()
        asyncio.set_event_loop(None)
    paper = _paper_payload(
        identity=identity,
        strategy=strategy,
        reports=reports,
        stream=stream,
    )
    write_json(stream_path, stream)
    write_json(paper_path, paper)
    for name, payload in project_cockpit_artifacts(paper=paper, stream=stream).items():
        write_json(artifact_dir / name, payload)
    completion = _completion_payload(
        run_id=run_id,
        identity=identity,
        seconds=seconds,
        config=config,
        source=source,
        artifact_dir=artifact_dir,
        claim_path=claim_path,
        stream_path=stream_path,
        paper_path=paper_path,
        paper=paper,
        stream=stream,
    )
    write_json(completion_path, completion)
    return completion
