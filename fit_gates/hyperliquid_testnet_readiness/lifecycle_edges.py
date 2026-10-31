"""Optional offline edge sequences using actual pinned adapter methods and orders.

Only explicit transport, cache lookup and event delivery seams are supplied.
This is neither an execution client nor a replacement order state machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from decimal import Decimal
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

from fit_gates.hyperliquid_testnet_readiness import lifecycle
from fit_gates.hyperliquid_testnet_readiness.probe import (
    VERSION,
    deny_external_io,
    require_safe_environment,
)

SCHEMA = "hyperliquid-offline-lifecycle-edges-v1"
# Hand-authored delivery sequences and independently specified required outcomes.
# No expected value drives an upstream transition or manufactures a missing fill.
CASES = {
    "duplicate_fill": (
        ("accept", "fill1", "fill1"),
        "PARTIALLY_FILLED",
        "0.04000",
        "1.80000000",
        1,
    ),
    "fill_before_accept": (
        ("fill1", "accept", "accept"),
        "PARTIALLY_FILLED",
        "0.04000",
        "1.80000000",
        1,
    ),
    "buffered_duplicate_fill": (
        ("hide_order", "fill1", "fill1", "show_order", "accept"),
        "PARTIALLY_FILLED",
        "0.04000",
        "1.80000000",
        1,
    ),
    "ioc_partial_cancel": (("accept", "fill1", "cancel4"), "CANCELED", "0.04000", "1.80000000", 1),
    "late_partial_after_cancel": (
        ("accept", "fill1", "cancel4", "fill2", "cancel7"),
        "CANCELED",
        "0.07000",
        "3.15000000",
        2,
    ),
    "cancel_before_fill": (
        ("accept", "cancel0", "fill1", "cancel4"),
        "CANCELED",
        "0.04000",
        "1.80000000",
        1,
    ),
    "cancel_before_accept_late_fill": (
        ("cancel0", "fill1", "accept"),
        "CANCELED",
        "0.04000",
        "1.80000000",
        1,
    ),
}


TERMINAL_FAILURES = frozenset({"required_final_status", "terminal_ioc_remainder"})
EXTERNAL_HANDOFF_FAILURES = frozenset(
    {
        "filled_quantity",
        "remaining_quantity",
        "unique_fill_count",
        "core_trade_ids",
        "expected_trade_ids",
        "event_commission",
        "core_commission",
        "stable_identity",
        "no_unattributed_report",
    }
)

CHECK_NAMES = frozenset(
    {
        "required_final_status",
        "filled_quantity",
        "remaining_quantity",
        "unique_fill_count",
        "core_trade_ids",
        "expected_trade_ids",
        "event_commission",
        "core_commission",
        "stable_identity",
        "terminal_ioc_remainder",
        "no_upstream_exception",
        "no_unattributed_report",
        "buffer_drained",
        "one_submit",
        "external_handoff_content",
    }
)


def verify_observation(case: str, observation: dict[str, Any]) -> None:
    """Fail on drift; known negative observations are counted, never skipped."""
    if set(observation["checks"]) != CHECK_NAMES or any(
        type(value) is not bool for value in observation["checks"].values()
    ):
        raise RuntimeError("Lifecycle edge check schema is incomplete or invalid.")
    expected_failed = (
        TERMINAL_FAILURES
        if case in {"late_partial_after_cancel", "cancel_before_fill"}
        else EXTERNAL_HANDOFF_FAILURES
        if case == "cancel_before_accept_late_fill"
        else frozenset()
    )
    failed = {name for name, passed in observation["checks"].items() if not passed}
    if failed != expected_failed:
        raise RuntimeError("Actual lifecycle edge outcome differs from its reviewed observation.")
    expected_status = (
        "PARTIALLY_FILLED"
        if case in {"late_partial_after_cancel", "cancel_before_fill"}
        else CASES[case][1]
    )
    if observation["final_status"] != expected_status or observation["closed"] != (
        expected_status == "CANCELED"
    ):
        raise RuntimeError("Actual final status differs from the recorded observation.")
    if case == "cancel_before_accept_late_fill" and (
        observation["external_fill_reports"] != 1
        or observation["external_status_reports"] != 1
        or observation["filled_quantity"] != "0.00000"
        or observation["leaves_quantity"] != "0.10000"
        or observation["final_status"] != "CANCELED"
        or observation["venue_order_id"] is not None
        or observation["core_commissions"]
        or observation["core_trade_ids"]
    ):
        raise RuntimeError("External-report handoff observation changed.")
    verdict = "HOLD" if expected_failed else "PASS"
    if observation["bounded_verdict"] != verdict or observation["fit"] != (
        "failure" if expected_failed else "pass"
    ):
        raise RuntimeError("Lifecycle edge observation was misclassified.")


def verify_cases(cases: list[dict[str, Any]]) -> None:
    expected = {
        "duplicate_fill",
        "fill_before_accept",
        "buffered_duplicate_fill",
        "ioc_partial_cancel",
        "late_partial_after_cancel",
        "cancel_before_fill",
        "cancel_before_accept_late_fill",
    }
    if len(cases) != len(expected) or {case["case"] for case in cases} != expected:
        raise RuntimeError("Lifecycle edge observations are missing or duplicated.")
    for case in cases:
        verify_observation(case["case"], case)


class EdgeContext:
    """Explicit fixture binding; state transitions belong exclusively to upstream."""

    def __init__(self, candidate: Any, modules: dict[str, Any], case: str) -> None:
        self.modules = modules
        self.order = lifecycle.make_order(modules, case)
        self.visible = True
        self.events: list[dict[str, str]] = []
        self.errors: list[dict[str, str]] = []
        self.steps: list[dict[str, object]] = []
        self.external_fills: list[Any] = []
        self.external_statuses: list[Any] = []
        self.warnings: list[str] = []
        self.transport = lifecycle.RecordedTransport(None, timeout=False)
        native = modules["core.nautilus_pyo3"]
        self.seam = SimpleNamespace(
            _config=SimpleNamespace(normalize_prices=False),
            _client=object(),
            _ws_client=self.transport,
            _clock=SimpleNamespace(timestamp_ns=lambda: lifecycle.TS),
            _log=SimpleNamespace(
                warning=lambda message: self.warnings.append(message),
                debug=lambda *_args: None,
                error=lambda *_args: None,
            ),
            _cache=SimpleNamespace(
                order=self.cached_order,
                instrument=self.cached_instrument,
                strategy_id_for_order=self.cached_strategy,
                venue_order_id=self.cached_venue_order_id,
                client_order_id=self.cached_client_order_id,
            ),
            _accepted_orders=native.FifoCache(),
            _terminal_orders=native.FifoCache(),
            _processed_trade_ids=native.FifoCache(),
            _pending_fills={},
            _pending_filled=set(),
            _pending_modify_keys={},
            _send_fill_report=self.external_fills.append,
            _send_order_status_report=self.external_statuses.append,
        )
        for name in lifecycle.METHODS:
            setattr(self.seam, name, MethodType(getattr(candidate, name), self.seam))
        for kind in ("Submitted", "Accepted", "Rejected", "Filled", "Canceled"):
            setattr(
                self.seam,
                f"generate_order_{kind.lower()}",
                lifecycle.event_sink(modules, self.order, self.events, f"Order{kind}"),
            )

    def cached_order(self, key: Any) -> Any:
        return self.order if self.visible and key == self.order.client_order_id else None

    def cached_strategy(self, key: Any) -> Any:
        # This index remains present while order visibility is explicitly delayed.
        return self.order.strategy_id if key == self.order.client_order_id else None

    def cached_instrument(self, key: Any) -> Any:
        if key != self.order.instrument_id:
            return None
        return SimpleNamespace(
            quote_currency=self.modules["model.objects"].Currency.from_str("USDC")
        )

    def cached_venue_order_id(self, key: Any) -> Any:
        return self.order.venue_order_id if key == self.order.client_order_id else None

    def cached_client_order_id(self, key: Any) -> Any:
        # The lookup index is available only after Order.apply bound the real ID.
        return self.order.client_order_id if key == self.order.venue_order_id else None

    def submit(self) -> None:
        command = self.modules["execution.messages"].SubmitOrder(
            trader_id=self.order.trader_id,
            strategy_id=self.order.strategy_id,
            order=self.order,
            command_id=self.modules["core.uuid"].UUID4.from_str(lifecycle.UUID),
            ts_init=lifecycle.TS,
        )
        lifecycle.drive_without_loop(self.seam._submit_order(command))
        if self.order.status_string() != "SUBMITTED" or self.order.filled_qty.as_decimal() != 0:
            raise RuntimeError("Missing acknowledgement must not invent acceptance or exposure.")

    def status(self, name: str, filled: str) -> Any:
        report = self.modules["execution.reports"].OrderStatusReport.from_pyo3(
            lifecycle.status_report(self.modules, self.order, name)
        )
        # Synthetic cumulative venue field is independent of the order's result.
        report.filled_qty = self.modules["model.objects"].Quantity.from_str(filled)
        report.leaves_qty = self.modules["model.objects"].Quantity.from_str(
            f"{Decimal('0.10000') - Decimal(filled):.5f}"
        )
        return report.to_pyo3()

    def fill(self, name: str) -> Any:
        report = self.modules["execution.reports"].FillReport.from_pyo3(
            lifecycle.partial_report(self.modules, self.order)
        )
        if name == "fill2":
            report.trade_id = self.modules["model.identifiers"].TradeId("700002")
            report.last_qty = self.modules["model.objects"].Quantity.from_str("0.03000")
            report.commission = self.modules["model.objects"].Money.from_str("1.35000000 USDC")
        return report.to_pyo3()

    def deliver(self, action: str) -> None:
        report_fields: dict[str, object] = {}
        try:
            if action == "hide_order":
                self.visible = False
            elif action == "show_order":
                self.visible = True
            elif action in {"fill1", "fill2"}:
                report = self.fill(action)
                report_fields = {
                    "reported_trade_id": report.trade_id.value,
                    "reported_fill_quantity": str(report.last_qty),
                    "reported_time_ns": report.ts_event,
                }
                self.seam._handle_fill_report_pyo3(report)
            else:
                report = self.status(
                    "ACCEPTED" if action == "accept" else "CANCELED",
                    {
                        "accept": "0.00000",
                        "cancel0": "0.00000",
                        "cancel4": "0.04000",
                        "cancel7": "0.07000",
                    }[action],
                )
                report_fields = {
                    "reported_filled_quantity": str(report.filled_qty),
                    "reported_time_ns": report.ts_last,
                }
                self.seam._handle_order_status_report_pyo3(report)
        except Exception as error:
            # Record exception type only, never an upstream message/config value.
            self.errors.append({"action": action, "error_type": type(error).__name__})
        self.steps.append(
            {
                "action": action,
                **report_fields,
                "status": self.order.status_string(),
                "filled_quantity": str(self.order.filled_qty),
                "closed": self.order.is_closed,
                "buffered_fills": sum(len(items) for items in self.seam._pending_fills.values()),
                "cloid_mappings": len(self.transport.mapping),
            }
        )


def exercise_case(candidate: Any, modules: dict[str, Any], case: str) -> dict[str, object]:
    actions, expected_status, expected_qty, expected_fee, expected_unique = CASES[case]
    context = EdgeContext(candidate, modules, case)
    context.submit()
    for action in actions:
        context.deliver(action)
    order = context.order
    fills = [event for event in context.events if event["type"] == "OrderFilled"]
    commissions = [str(value) for value in order.commissions()]
    event_fee = sum((Decimal(event["commission"].split()[0]) for event in fills), Decimal(0))
    expected_leaves = Decimal("0.10000") - Decimal(expected_qty)
    external_fills = [
        {
            "client_order_id": report.client_order_id.value if report.client_order_id else None,
            "venue_order_id": report.venue_order_id.value,
            "trade_id": report.trade_id.value,
            "last_quantity": str(report.last_qty),
            "commission": str(report.commission),
            "instrument_id": report.instrument_id.value,
        }
        for report in context.external_fills
    ]
    expected_cloid = modules["core.nautilus_pyo3"].hyperliquid_cloid_from_client_order_id(
        modules["core.nautilus_pyo3"].ClientOrderId(f"offline-fit-{case}")
    )
    checks = {
        "required_final_status": order.status_string() == expected_status,
        "filled_quantity": order.filled_qty.as_decimal() == Decimal(expected_qty),
        "remaining_quantity": order.leaves_qty.as_decimal() == expected_leaves,
        "unique_fill_count": len(fills)
        == len({event["trade_id"] for event in fills})
        == expected_unique,
        "core_trade_ids": {item.value for item in order.trade_ids}
        == {event["trade_id"] for event in fills}
        and len(order.trade_ids) == expected_unique,
        "expected_trade_ids": {item.value for item in order.trade_ids}
        == ({"700001", "700002"} if expected_unique == 2 else {"700001"}),
        "event_commission": event_fee == Decimal(expected_fee),
        "core_commission": commissions == [f"{expected_fee} USDC"],
        "stable_identity": order.client_order_id.value == f"offline-fit-{case}"
        and order.instrument_id.value == "BTC-USD-PERP.HYPERLIQUID"
        and order.venue_order_id is not None
        and order.venue_order_id.value == "900001"
        and all(
            event["client_order_id"] == order.client_order_id.value
            and event["instrument_id"] == order.instrument_id.value
            and (event["type"] != "OrderFilled" or event["venue_order_id"] == "900001")
            for event in context.events
        ),
        "terminal_ioc_remainder": order.is_closed if expected_status == "CANCELED" else True,
        "no_upstream_exception": not context.errors,
        "no_unattributed_report": not context.external_fills and not context.external_statuses,
        "buffer_drained": not context.seam._pending_fills,
        "one_submit": len(context.transport.calls) == 1,
        "external_handoff_content": external_fills
        == (
            [
                {
                    "client_order_id": expected_cloid,
                    "venue_order_id": "900001",
                    "trade_id": "700001",
                    "last_quantity": "0.04000",
                    "commission": "1.80000000 USDC",
                    "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
                }
            ]
            if case == "cancel_before_accept_late_fill"
            else []
        ),
    }
    observation = {
        "case": case,
        "bounded_verdict": "PASS" if all(checks.values()) else "HOLD",
        "fit": "pass" if all(checks.values()) else "failure",
        "checks": checks,
        "required": {
            "status": expected_status,
            "filled_quantity": expected_qty,
            "commission_usdc": expected_fee,
            "unique_fills": expected_unique,
        },
        "final_status": order.status_string(),
        "filled_quantity": str(order.filled_qty),
        "leaves_quantity": str(order.leaves_qty),
        "closed": order.is_closed,
        "core_commissions": commissions,
        "core_trade_ids": [item.value for item in order.trade_ids],
        "client_order_id": order.client_order_id.value,
        "venue_order_id": order.venue_order_id.value if order.venue_order_id else None,
        "submit_calls": len(context.transport.calls),
        "events": context.events,
        "steps": context.steps,
        "upstream_errors": context.errors,
        "external_fill_reports": len(context.external_fills),
        "external_fills": external_fills,
        "external_status_reports": len(context.external_statuses),
    }

    verify_observation(case, observation)
    return observation


def run() -> dict[str, object]:
    require_safe_environment(os.environ)
    sys.addaudithook(deny_external_io)
    candidate, modules, hashes = lifecycle.load_candidate()
    cases = [exercise_case(candidate, modules, name) for name in CASES]
    verify_cases(cases)
    return {
        "schema": SCHEMA,
        "candidate_version": VERSION,
        "decision": "HOLD",
        "new_risk_permitted": False,
        "all_safety_cases_pass": False,
        "observation_contract": "counted-pass-and-hold-outcomes-v1",
        "evidence_kind": "actual-unbound-adapter-methods-real-events-core-order-apply",
        "installed_source_sha256": hashes,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "shared_harness_sha256": hashlib.sha256(Path(lifecycle.__file__).read_bytes()).hexdigest(),
        "runtime": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "published_wheel_binding_verified": False,
        "fixture_provenance": "hand-authored domain reports; not captured venue traffic",
        "cache_seam": "strict client/venue/instrument lookup; explicit order-visibility delay",
        "event_delivery": "actual events applied inline; base generators/event bus bypassed",
        "executed_methods": list(lifecycle.METHODS),
        "cases": cases,
        "failed_cases": [case["case"] for case in cases if case["fit"] == "failure"],
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
            "account-balances",
            "funding",
            "cancel-request-transport",
        ],
    }


def main() -> int:
    try:
        result = run()
    except Exception:
        print(
            "Offline lifecycle edge exercise failed; execution readiness remains HOLD.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
