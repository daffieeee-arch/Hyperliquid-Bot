"""Actual legacy report methods over recorded, signer-free, inline-only responses.

No client/factory is constructed. The injected response starts after native HTTP,
wire parsing and retries, so its completion is only a bounded fixture observation.
Python audit hooks are not a native-extension sandbox. Run in isolation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import sys
from collections.abc import Coroutine
from dataclasses import asdict, replace
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any
from uuid import UUID

from fit_gates.hyperliquid_testnet_readiness.lifecycle import (
    TS,
    load_candidate,
    make_order,
    partial_report,
    status_report,
)
from fit_gates.hyperliquid_testnet_readiness.lifecycle import (
    UUID as FIXTURE_UUID,
)
from fit_gates.hyperliquid_testnet_readiness.probe import (
    VERSION,
    deny_external_io,
    require_safe_environment,
)
from fit_gates.hyperliquid_testnet_readiness.report_trust import (
    ReportKind,
    ReportObservation,
    ReportScope,
    assess_report,
)

ROOT = Path(__file__).parent
SCHEMA = "hyperliquid-offline-report-observations-v1"
ACCOUNT = "HYPERLIQUID-OFFLINE"
INSTRUMENT = "BTC-USD-PERP.HYPERLIQUID"
METHODS = {
    "order": "generate_order_status_report",
    "orders": "generate_order_status_reports",
    "fills": "generate_fill_reports",
    "positions": "generate_position_status_reports",
}
HELPERS = (
    "_is_cloid_format",
    "_resolve_cloid",
    "_is_external_order",
    "_promote_replacement_if_inflight_modify",
    "_is_inflight_modify_old_leg_cancel",
)
BASE_CASES = (
    "empty",
    "nonempty",
    "timeout",
    "error",
    "cancelled",
    "conversion_failure",
    "incomplete",
    "wrong_account",
    "wrong_instrument",
)


def drive_return_without_loop(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Capture actual method return; reject suspension instead of running an event loop."""
    try:
        try:
            coroutine.send(None)
        except StopIteration as completed:
            return completed.value
        raise RuntimeError("Offline report fragment unexpectedly suspended.")
    finally:
        coroutine.close()


class RecordedReportTransport:
    def __init__(self, response: object, failure: str | None) -> None:
        self.response = response
        self.failure = failure
        self.calls: list[dict[str, Any]] = []
        self.completed = False

    async def request(self, **query: Any) -> object:
        self.calls.append(query)
        if self.failure == "timeout":
            raise TimeoutError("synthetic report timeout")
        if self.failure == "error":
            raise RuntimeError("synthetic report error")
        if self.failure == "cancelled":
            raise asyncio.CancelledError("synthetic report cancellation")
        self.completed = True
        return self.response


class StructuredObserver:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.receipts: list[int] = []

    def error(self, error: BaseException, _family: str) -> None:
        # Error type is structured evidence. Exception/log text is never interpreted.
        self.errors.append(type(error).__name__)

    def receipt(self, count: int, _family: str, *_options: object) -> None:
        self.receipts.append(count)


def make_position_report(modules: dict[str, Any], order: Any) -> Any:
    return (
        modules["execution.reports"]
        .PositionStatusReport(
            account_id=modules["model.identifiers"].AccountId(ACCOUNT),
            instrument_id=order.instrument_id,
            position_side=modules["model.enums"].PositionSide.LONG,
            quantity=order.quantity,
            report_id=modules["core.uuid"].UUID4.from_str(FIXTURE_UUID),
            ts_last=TS,
            ts_init=TS,
        )
        .to_pyo3()
    )


def make_command(modules: dict[str, Any], kind: ReportKind, case: str, order: Any) -> Any:
    ids, messages = modules["model.identifiers"], modules["execution.messages"]
    common = {
        "instrument_id": order.instrument_id,
        "command_id": modules["core.uuid"].UUID4.from_str(
            str(
                UUID(
                    bytes=hashlib.sha256(f"report-{kind}-{case}".encode()).digest()[:16], version=4
                )
            ),
        ),
        "ts_init": TS,
    }
    if kind == "order":
        return messages.GenerateOrderStatusReport(
            **common,
            venue_order_id=None if case == "missing_lookup" else ids.VenueOrderId("900001"),
            client_order_id=None,
        )
    if kind == "orders":
        return messages.GenerateOrderStatusReports(**common, start=None, end=None, open_only=False)
    if kind == "fills":
        return messages.GenerateFillReports(**common, venue_order_id=None, start=None, end=None)
    return messages.GeneratePositionStatusReports(**common, start=None, end=None)


def exercise(
    candidate: Any, modules: dict[str, Any], kind: ReportKind, case: str
) -> dict[str, Any]:
    order = make_order(modules, f"report-{kind}-{case}")
    command = make_command(modules, kind, case, order)
    query = (
        (
            ("venue_order_id", command.venue_order_id.value if command.venue_order_id else None),
            ("client_order_id", command.client_order_id.value if command.client_order_id else None),
        )
        if kind == "order"
        else (("instrument_id", INSTRUMENT),)
    )
    expected = ReportScope(
        str(command.id), "PAPER", ACCOUNT, "HYPERLIQUID", INSTRUMENT, kind, query
    )
    report = (
        partial_report(modules, order)
        if kind == "fills"
        else make_position_report(modules, order)
        if kind == "positions"
        else status_report(modules, order, "CANCELED" if case == "suppressed" else "ACCEPTED")
    )
    if case in {"wrong_account", "wrong_instrument", "wrong_order"}:
        python_report = getattr(modules["execution.reports"], type(report).__name__).from_pyo3(
            report
        )
        if case == "wrong_account":
            python_report.account_id = modules["model.identifiers"].AccountId("HYPERLIQUID-OTHER")
        elif case == "wrong_instrument":
            python_report.instrument_id = modules["model.identifiers"].InstrumentId.from_str(
                "ETH-USD-PERP.HYPERLIQUID",
            )
        else:
            python_report.venue_order_id = modules["model.identifiers"].VenueOrderId("900002")
        report = python_report.to_pyo3()
    if case in {"conversion_failure", "partial_conversion_failure"}:
        # An actual report of the wrong upstream/PyO3 family, not a replacement parser.
        wrong_family = (
            status_report(modules, order, "ACCEPTED")
            if kind == "fills"
            else partial_report(modules, order)
        )
    response = (
        (None if case in {"empty", "incomplete"} else report)
        if kind == "order"
        else (
            [] if case in {"empty", "incomplete"} else None if case == "unavailable" else [report]
        )
    )
    if case == "conversion_failure":
        response = wrong_family if kind == "order" else [wrong_family]
    elif case == "partial_conversion_failure":
        response = [report, wrong_family]
    transport = RecordedReportTransport(response, case)
    observer = StructuredObserver()
    mapping = {
        modules["core.nautilus_pyo3"].hyperliquid_cloid_from_client_order_id(
            modules["core.nautilus_pyo3"].ClientOrderId(order.client_order_id.value),
        ): modules["core.nautilus_pyo3"].ClientOrderId(order.client_order_id.value),
    }
    client = SimpleNamespace(
        _client=SimpleNamespace(
            request_order_status_report=transport.request,
            request_order_status_reports=transport.request,
            request_fill_reports=transport.request,
            request_position_status_reports=transport.request,
        ),
        _ws_client=SimpleNamespace(get_cloid_mapping=mapping.get),
        _cache=SimpleNamespace(
            strategy_id_for_order=lambda _identifier: order.strategy_id,
            client_order_id=lambda _identifier: order.client_order_id,
        ),
        _pending_modify_keys=(
            {order.client_order_id.value: "900001"} if case == "suppressed" else {}
        ),
        _log=SimpleNamespace(debug=lambda *_args: None, warning=lambda *_args: None),
        _log_report_error=observer.error,
        _log_report_receipt=observer.receipt,
    )
    for name in HELPERS:
        setattr(client, name, MethodType(getattr(candidate, name), client))
    result = drive_return_without_loop(getattr(candidate, METHODS[kind])(client, command))
    returned = ([] if result is None else [result]) if kind == "order" else result
    raw_count = (
        (0 if response is None else 1)
        if kind == "order"
        else len(response)
        if type(response) is list
        else None
    )
    actual_query = tuple(transport.calls[0].items()) if len(transport.calls) == 1 else ()
    observed = ReportObservation(
        scope=replace(expected, query=actual_query),
        request_count=len(transport.calls),
        transport_completed=transport.completed,
        adapter_completed=True,
        bounded_response_complete=case != "incomplete",
        raw_count=raw_count,
        returned_count=len(returned),
        receipt_counts=tuple(observer.receipts),
        errors=tuple(observer.errors),
        report_types=tuple(type(item).__name__ for item in returned),
        report_identities=tuple(
            (item.account_id.value, item.instrument_id.value) for item in returned
        ),
        report_order_ids=tuple(
            (
                item.venue_order_id.value if getattr(item, "venue_order_id", None) else None,
                item.client_order_id.value if getattr(item, "client_order_id", None) else None,
            )
            for item in returned
        ),
    )
    return {
        "case": f"{kind}:{case}",
        "method": METHODS[kind],
        "expected_scope": asdict(expected),
        "observation": asdict(observed),
        "assessment": assess_report(expected, observed).to_dict(),
        "adapter_return": "None"
        if result is None
        else "list"
        if type(result) is list
        else "report",
        "returned_reports": [item.to_dict() for item in returned],
    }


def verify(data: dict[str, Any]) -> None:
    """An execution is evidence only if the complete fixed matrix behaved as expected.

    This is also usable by an external CI comparison. No unexpected case or UNKNOWN
    outcome can replace an expected successful observation and still pass the run.
    """
    expected_cases = {f"{kind}:{case}" for kind in METHODS for case in BASE_CASES}
    expected_cases.update(
        f"{kind}:{case}"
        for kind in ("orders", "fills", "positions")
        for case in ("unavailable", "partial_conversion_failure")
    )
    expected_cases.update(
        {"order:suppressed", "orders:suppressed", "order:missing_lookup", "order:wrong_order"},
    )
    cases = data["cases"]
    names = [case["case"] for case in cases]
    if len(names) != len(expected_cases) or set(names) != expected_cases:
        raise RuntimeError("Offline report case matrix is incomplete or duplicated.")
    if (
        data["schema"] != SCHEMA
        or data["candidate_version"] != VERSION
        or data["decision"] != "HOLD"
    ):
        raise RuntimeError("Offline report identity or decision is inconsistent.")
    for case in cases:
        kind, name = case["case"].split(":")
        expected_state = (
            "BOUNDED_NONEMPTY"
            if name == "nonempty"
            else "BOUNDED_NOT_FOUND"
            if name == "empty" and kind == "order"
            else "BOUNDED_EMPTY"
            if name == "empty"
            else "UNKNOWN"
        )
        assessment = case["assessment"]
        observation = case["observation"]
        if (
            assessment["state"] != expected_state
            or assessment["account_truth"] != "UNKNOWN"
            or assessment["history_coverage"] != "UNKNOWN"
            or assessment["authoritative_flat"] is not False
            or assessment["new_risk_allowed"] is not False
            or assessment["decision"] != "HOLD"
            or case["method"] != METHODS[kind]
        ):
            raise RuntimeError("Offline report outcome differs from the required bounded contract.")
        expected_error = {
            "timeout": "TimeoutError",
            "error": "RuntimeError",
            "cancelled": "CancelledError",
            "conversion_failure": "AttributeError",
            "partial_conversion_failure": "AttributeError",
            "unavailable": "TypeError",
        }.get(name)
        if tuple(observation["errors"]) != ((expected_error,) if expected_error else ()):
            raise RuntimeError("Offline report did not observe the expected structured error.")
        if name in {"empty", "incomplete", "nonempty"}:
            count = 1 if name == "nonempty" else 0
            if observation["raw_count"] != count or observation["returned_count"] != count:
                raise RuntimeError("Offline report successful-response counts differ.")
        if name in {"suppressed", "partial_conversion_failure"}:
            if observation["raw_count"] != (1 if name == "suppressed" else 2):
                raise RuntimeError("Offline report did not exercise discarded response items.")
            if observation["returned_count"] != 0:
                raise RuntimeError("Offline report unexpectedly retained discarded response items.")
        if observation["request_count"] != (0 if name == "missing_lookup" else 1):
            raise RuntimeError("Offline report request count differs.")


def run() -> dict[str, Any]:
    require_safe_environment(os.environ)
    sys.addaudithook(deny_external_io)
    candidate, modules, hashes = load_candidate()
    kinds: tuple[ReportKind, ...] = ("order", "orders", "fills", "positions")
    cases = [exercise(candidate, modules, kind, case) for kind in kinds for case in BASE_CASES]
    cases.extend(
        exercise(candidate, modules, kind, "unavailable") for kind in kinds if kind != "order"
    )
    cases.extend(
        exercise(candidate, modules, kind, "partial_conversion_failure")
        for kind in kinds
        if kind != "order"
    )
    cases.extend(exercise(candidate, modules, kind, "suppressed") for kind in ("order", "orders"))
    cases.append(exercise(candidate, modules, "order", "missing_lookup"))
    cases.append(exercise(candidate, modules, "order", "wrong_order"))
    result = {
        "schema": SCHEMA,
        "candidate_version": VERSION,
        "decision": "HOLD",
        "evidence_kind": "actual-unbound-python-report-methods-and-upstream-domain-conversions",
        "fixture_provenance": (
            "nominal-synthetic-ids-and-injected-PyO3-reports-not-venue-wire-fixtures"
        ),
        "completion_scope": "one-injected-response-not-account-pagination-or-history-completion",
        "account_namespace": "synthetic-caller-scope-not-authenticated-client-configuration",
        "published_wheel_binding_verified": False,
        "runtime": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "installed_source_sha256": hashes,
        "local_source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("report_fragments.py", "report_trust.py", "lifecycle.py", "probe.py")
        },
        "executed_methods": [*METHODS.values(), *HELPERS],
        "structured_observation_hooks": ["_log_report_error", "_log_report_receipt"],
        "not_forwarded_by_actual_methods": {
            "order": ["instrument_id"],
            "orders": ["start", "end", "open_only"],
            "fills": ["start", "end", "venue_order_id"],
            "positions": ["start", "end"],
        },
        "not_executed": [
            "client-construction",
            "factory-create",
            "signer-construction",
            "event-loop",
            "native-transport",
            "native-retries",
            "wire-response-parser",
            "venue-requests",
            "event-bus",
            "account-authentication",
            "pagination",
            "history-coverage",
            "venue-reconciliation",
            "risk-permission",
            "runtime-deployment",
        ],
        "cases": cases,
    }
    verify(result)
    return result


def main() -> int:
    try:
        result = run()
    except Exception:
        print(
            "Offline report fragment failed; no credentials or venue I/O are permitted.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
