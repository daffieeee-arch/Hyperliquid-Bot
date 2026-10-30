"""Optional actual-adapter fragment exercise; no client, signer, event loop or venue I/O.

A narrow protocol self invokes unbound upstream Python methods. Real upstream
orders/events/reports supply the arithmetic and state transitions. Only transport,
lookup and event-delivery seams are replaced; there is no project-owned OMS.
Python audit hooks are not a sandbox for native extensions. Run in isolation.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import sys
from collections.abc import Coroutine
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

from fit_gates.hyperliquid_testnet_readiness.probe import (
    VERSION,
    deny_external_io,
    require_safe_environment,
)

ROOT = Path(__file__).parent
SCHEMA = "hyperliquid-offline-adapter-fragments-v1"
TS = 1_793_347_200_000_000_000
UUID = "00000000-0000-4000-8000-000000000000"
METHODS = (
    "_submit_order",
    "_check_time_in_force",
    "_process_submit_reports",
    "_handle_order_status_report_pyo3",
    "_handle_fill_report_pyo3",
    "_is_cloid_format",
    "_resolve_cloid",
    "_is_external_order",
    "_cleanup_cloid_mapping",
    "_drain_fill_buffer",
)


def drive_without_loop(coroutine: Coroutine[Any, Any, Any]) -> None:
    """These recorded transports complete synchronously; any I/O suspension is a fit failure."""
    try:
        try:
            coroutine.send(None)
        except StopIteration:
            return
        raise RuntimeError("Offline fragment unexpectedly suspended; no event loop is permitted.")
    finally:
        coroutine.close()


def load_candidate() -> tuple[Any, dict[str, Any], dict[str, str]]:
    distribution = importlib.metadata.distribution("nautilus-trader")
    if distribution.version != VERSION:
        raise RuntimeError("Optional fragment requires the exact assessed candidate version.")
    path = Path(str(distribution.locate_file("nautilus_trader/adapters/hyperliquid")))
    reference = json.loads((ROOT / "nautilus-probe.json").read_text())
    hashes = {
        name: hashlib.sha256((path / name).read_bytes()).hexdigest()
        for name in reference["installed_source_sha256"]
    }
    if hashes != reference["installed_source_sha256"]:
        raise RuntimeError("Candidate source differs from the reviewed exact-version inventory.")
    modules = {
        name: importlib.import_module(f"nautilus_trader.{name}")
        for name in (
            "core.nautilus_pyo3",
            "core.uuid",
            "model.identifiers",
            "model.objects",
            "model.enums",
            "model.events",
            "model.orders",
            "execution.messages",
            "execution.reports",
        )
    }
    adapter = importlib.import_module("nautilus_trader.adapters.hyperliquid.execution")
    if not isinstance(adapter.__file__, str):
        raise RuntimeError("Imported adapter has no inspectable source file.")
    if Path(adapter.__file__).resolve() != (path / "execution.py").resolve():
        raise RuntimeError("Imported adapter is not the inspected installed source.")
    return adapter.HyperliquidExecutionClient, modules, hashes


class RecordedTransport:
    """Records one invocation and returns only an injected upstream report or timeout."""

    def __init__(self, response: object, timeout: bool) -> None:
        self.response = response
        self.timeout = timeout
        self.calls: list[dict[str, Any]] = []
        self.mapping: dict[str, Any] = {}

    def cache_cloid_mapping(self, cloid: str, order_id: Any) -> None:
        self.mapping[cloid] = order_id

    def get_cloid_mapping(self, cloid: str) -> Any:
        return self.mapping.get(cloid)

    def remove_cloid_mapping(self, cloid: str) -> None:
        self.mapping.pop(cloid, None)

    async def submit_order(self, _opaque_transport: object, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.timeout:
            raise TimeoutError("synthetic lost acknowledgement")
        return self.response


def make_order(modules: dict[str, Any], case: str) -> Any:
    ids, values, enums = (
        modules[key] for key in ("model.identifiers", "model.objects", "model.enums")
    )
    return modules["model.orders"].LimitOrder(
        trader_id=ids.TraderId("OFFLINE-001"),
        strategy_id=ids.StrategyId("OFFLINE-001"),
        instrument_id=ids.InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID"),
        client_order_id=ids.ClientOrderId(f"offline-fit-{case}"),
        order_side=enums.OrderSide.BUY,
        quantity=values.Quantity.from_str("0.10000"),
        price=values.Price.from_str("100000"),
        init_id=modules["core.uuid"].UUID4.from_str(UUID),
        ts_init=TS,
        time_in_force=enums.TimeInForce.IOC,
    )


def status_report(modules: dict[str, Any], order: Any, status: str) -> Any:
    ids, values, enums = (
        modules[key] for key in ("model.identifiers", "model.objects", "model.enums")
    )
    return (
        modules["execution.reports"]
        .OrderStatusReport(
            account_id=ids.AccountId("HYPERLIQUID-OFFLINE"),
            instrument_id=order.instrument_id,
            venue_order_id=ids.VenueOrderId("900001"),
            client_order_id=ids.ClientOrderId(
                modules["core.nautilus_pyo3"].hyperliquid_cloid_from_client_order_id(
                    modules["core.nautilus_pyo3"].ClientOrderId(order.client_order_id.value)
                )
            ),
            order_side=order.side,
            order_type=order.order_type,
            time_in_force=order.time_in_force,
            order_status=getattr(enums.OrderStatus, status),
            quantity=order.quantity,
            filled_qty=values.Quantity.from_str("0.00000"),
            price=order.price,
            report_id=modules["core.uuid"].UUID4.from_str(UUID),
            ts_accepted=TS,
            ts_last=TS,
            ts_init=TS,
            cancel_reason="synthetic venue rejection" if status == "REJECTED" else None,
        )
        .to_pyo3()
    )


def partial_report(modules: dict[str, Any], order: Any) -> Any:
    ids, values, enums = (
        modules[key] for key in ("model.identifiers", "model.objects", "model.enums")
    )
    return (
        modules["execution.reports"]
        .FillReport(
            account_id=ids.AccountId("HYPERLIQUID-OFFLINE"),
            instrument_id=order.instrument_id,
            venue_order_id=ids.VenueOrderId("900001"),
            client_order_id=ids.ClientOrderId(
                modules["core.nautilus_pyo3"].hyperliquid_cloid_from_client_order_id(
                    modules["core.nautilus_pyo3"].ClientOrderId(order.client_order_id.value)
                )
            ),
            trade_id=ids.TradeId("700001"),
            order_side=order.side,
            last_qty=values.Quantity.from_str("0.04000"),
            last_px=values.Price.from_str("100000"),
            commission=values.Money.from_str("1.80000000 USDC"),
            liquidity_side=enums.LiquiditySide.TAKER,
            report_id=modules["core.uuid"].UUID4.from_str(UUID),
            ts_event=TS,
            ts_init=TS,
        )
        .to_pyo3()
    )


def event_sink(modules: dict[str, Any], order: Any, seen: list[dict[str, str]], kind: str) -> Any:
    """Deliver actual upstream domain events to the actual order model, without an event bus."""

    def emit(**kwargs: Any) -> None:
        if kind == "OrderFilled":
            kwargs["currency"] = kwargs.pop("quote_currency")
            kwargs["position_id"] = kwargs.pop("venue_position_id")
        event = getattr(modules["model.events"], kind)(
            trader_id=order.trader_id,
            account_id=modules["model.identifiers"].AccountId("HYPERLIQUID-OFFLINE"),
            event_id=modules["core.uuid"].UUID4.from_str(
                f"00000000-0000-4000-8000-{len(seen) + 1:012d}"
            ),
            ts_init=TS,
            **kwargs,
        )
        order.apply(event)
        record = {
            "type": type(event).__name__,
            "client_order_id": event.client_order_id.value,
            "instrument_id": event.instrument_id.value,
            "status_after": order.status_string(),
            "filled_quantity_after": str(order.filled_qty),
        }
        if kind in {"OrderAccepted", "OrderFilled"}:
            record["venue_order_id"] = event.venue_order_id.value
        if kind == "OrderFilled":
            record.update(
                trade_id=event.trade_id.value,
                last_quantity=str(event.last_qty),
                price=str(event.last_px),
                commission=str(event.commission),
            )
        seen.append(record)

    return emit


def exercise_case(candidate: Any, modules: dict[str, Any], case: str) -> dict[str, object]:
    order = make_order(modules, case)
    report = status_report(modules, order, "REJECTED" if case == "reject" else "ACCEPTED")
    transport = RecordedTransport(
        None if case in {"timeout", "missing_ack"} else report, timeout=case == "timeout"
    )
    events: list[dict[str, str]] = []
    warnings: list[str] = []
    seam = SimpleNamespace(
        _config=SimpleNamespace(normalize_prices=False),
        _client=object(),
        _ws_client=transport,
        _clock=SimpleNamespace(timestamp_ns=lambda: TS),
        _log=SimpleNamespace(
            warning=lambda message: warnings.append(message),
            debug=lambda *_args: None,
            error=lambda *_args: None,
        ),
        _cache=SimpleNamespace(
            order=lambda _key: order,
            instrument=lambda _key: SimpleNamespace(
                quote_currency=modules["model.objects"].Currency.from_str("USDC")
            ),
            strategy_id_for_order=lambda _key: order.strategy_id,
            venue_order_id=lambda _key: order.venue_order_id,
        ),
        _accepted_orders=set(),
        _terminal_orders=set(),
        _pending_fills={},
        _pending_filled=set(),
        _processed_trade_ids=set(),
        _pending_modify_keys={},
    )
    for name in METHODS:
        setattr(seam, name, MethodType(getattr(candidate, name), seam))
    for kind in ("OrderSubmitted", "OrderAccepted", "OrderRejected", "OrderFilled"):
        snake = {
            "OrderSubmitted": "submitted",
            "OrderAccepted": "accepted",
            "OrderRejected": "rejected",
            "OrderFilled": "filled",
        }[kind]
        setattr(seam, f"generate_order_{snake}", event_sink(modules, order, events, kind))
    command = modules["execution.messages"].SubmitOrder(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        order=order,
        command_id=modules["core.uuid"].UUID4.from_str(UUID),
        ts_init=TS,
    )
    drive_without_loop(seam._submit_order(command))
    if case == "partial":
        seam._handle_fill_report_pyo3(partial_report(modules, order))
    expected = {
        "ack": "ACCEPTED",
        "reject": "REJECTED",
        "partial": "PARTIALLY_FILLED",
        "timeout": "SUBMITTED",
        "missing_ack": "SUBMITTED",
    }[case]
    if order.status_string() != expected or len(transport.calls) != 1:
        raise RuntimeError("Upstream fragment did not produce the required observed state.")
    filled = "0.04000" if case == "partial" else "0.00000"
    if str(order.filled_qty) != filled:
        raise RuntimeError(
            "Upstream order quantity differs from independently expected arithmetic."
        )
    if str(order.leaves_qty) != ("0.06000" if case == "partial" else "0.10000"):
        raise RuntimeError("Upstream remaining quantity violates the fixture arithmetic.")
    if case == "partial" and (
        events[-1]["price"] != "100000" or events[-1]["commission"] != "1.80000000 USDC"
    ):
        raise RuntimeError("Upstream fill price or commission differs from the fixture.")
    if any(event["client_order_id"] != order.client_order_id.value for event in events):
        raise RuntimeError("Report cloid did not resolve to the intended project order.")
    if case != "partial" and any(event["type"] == "OrderFilled" for event in events):
        raise RuntimeError("Acknowledgement/unknown outcome invented a fill.")
    request = transport.calls[0]
    return {
        "case": case,
        "submit_calls": len(transport.calls),
        "final_status": order.status_string(),
        "filled_quantity": str(order.filled_qty),
        "leaves_quantity": str(order.leaves_qty),
        "events": events,
        "warnings": len(warnings),
        "request": {
            "instrument_id": request["instrument_id"].value,
            "client_order_id": request["client_order_id"].value,
            "quantity": str(request["quantity"]),
            "price": str(request["price"]),
        },
        "cloid": modules["core.nautilus_pyo3"].hyperliquid_cloid_from_client_order_id(
            request["client_order_id"]
        ),
    }


def run() -> dict[str, object]:
    require_safe_environment(os.environ)
    sys.addaudithook(deny_external_io)
    candidate, modules, hashes = load_candidate()
    cases = [
        exercise_case(candidate, modules, name)
        for name in ("ack", "reject", "partial", "timeout", "missing_ack")
    ]
    return {
        "schema": SCHEMA,
        "candidate_version": VERSION,
        "decision": "HOLD",
        "evidence_kind": "actual-unbound-python-adapter-methods-and-core-order-apply",
        "runtime": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "installed_source_sha256": hashes,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_provenance": "hand-authored synthetic report values; not recorded venue traffic",
        "published_wheel_binding_verified": False,
        "event_delivery": "actual events applied to the upstream order; event bus not exercised",
        "cases": cases,
        "executed_methods": list(METHODS),
        "not_executed": [
            "client-construction",
            "signer-construction",
            "factory-create",
            "event-bus",
            "native-transport",
            "native-retries",
            "wire-response-parser",
            "venue-requests",
            "durable-recovery",
            "reconciliation",
            "cancel-races",
            "ioc-remainder-terminal-cancel",
            "metadata-precision-validation",
            "account-balances",
            "funding",
            "signature-domain",
            "nonce-policy",
        ],
    }


def main() -> int:
    try:
        result = run()
    except Exception:
        print("Offline adapter fragment failed; execution readiness remains HOLD.", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
