"""Deterministic observation tests; standard CI needs no optional venue adapter."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fit_gates.hyperliquid_testnet_readiness import report_fragments
from fit_gates.hyperliquid_testnet_readiness.report_trust import (
    REPORT_TYPES,
    ReportKind,
    ReportObservation,
    ReportScope,
    assess_report,
)

EVIDENCE = Path(__file__).resolve().parents[2] / "fit_gates/hyperliquid_testnet_readiness"
KINDS: tuple[ReportKind, ...] = ("order", "orders", "fills", "positions")


def proof(kind: ReportKind = "orders", count: int = 0) -> tuple[ReportScope, ReportObservation]:
    query = (
        (("venue_order_id", "900001"), ("client_order_id", None))
        if kind == "order"
        else (("instrument_id", report_fragments.INSTRUMENT),)
    )
    scope = ReportScope(
        "invocation-1",
        "PAPER",
        "HYPERLIQUID-OFFLINE",
        "HYPERLIQUID",
        report_fragments.INSTRUMENT,
        kind,
        query,
    )
    return scope, ReportObservation(
        scope=scope,
        request_count=1,
        transport_completed=True,
        adapter_completed=True,
        bounded_response_complete=True,
        raw_count=count,
        returned_count=count,
        receipt_counts=() if kind == "order" else (count,),
        errors=(),
        report_types=(REPORT_TYPES[kind],) * count,
        report_identities=((scope.account_id, scope.instrument_id),) * count,
        report_order_ids=(("900001", "local-order-1"),) * count,
    )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("count", [0, 1])
def test_consistent_response_is_only_bounded_observation(kind: ReportKind, count: int) -> None:
    scope, observation = proof(kind, count)
    result = assess_report(scope, observation)
    assert result.state == (
        "BOUNDED_NONEMPTY" if count else "BOUNDED_NOT_FOUND" if kind == "order" else "BOUNDED_EMPTY"
    )
    assert result.account_truth == result.history_coverage == "UNKNOWN"
    assert result.authoritative_flat is result.new_risk_allowed is False
    assert result.decision == "HOLD"
    with pytest.raises(TypeError, match="init=False"):
        replace(result, new_risk_allowed=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"request_count": 0},
        {"request_count": 2},
        {"request_count": True},
        {"request_count": 1.0},
        {"request_count": None},
        {"transport_completed": False},
        {"transport_completed": 1},
        {"adapter_completed": False},
        {"adapter_completed": None},
        {"bounded_response_complete": False},
        {"bounded_response_complete": 1.0},
        {"raw_count": None},
        {"raw_count": False},
        {"raw_count": 0.0},
        {"returned_count": None},
        {"returned_count": False},
        {"returned_count": 0.0},
        {"raw_count": -1},
        {"raw_count": 1},
        {"receipt_counts": ()},
        {"receipt_counts": (0, 0)},
        {"receipt_counts": (False,)},
        {"receipt_counts": (0.0,)},
        {"receipt_counts": [0]},
        {"receipt_counts": (1,)},
        {"errors": ("TimeoutError",)},
        {"errors": None},
        {"errors": []},
        {"report_types": []},
        {"report_identities": []},
        {"report_order_ids": []},
    ],
)
def test_missing_conflicting_or_coerced_completion_is_unknown(changes: dict[str, Any]) -> None:
    scope, observation = proof()
    assert assess_report(scope, replace(observation, **changes)).state == "UNKNOWN"
    assert assess_report(scope, None).state == "UNKNOWN"


@pytest.mark.parametrize(
    "changes",
    [
        {"invocation_id": "other"},
        {"environment": "TESTNET"},
        {"environment": True},
        {"account_id": "HYPERLIQUID-OTHER"},
        {"account_id": ""},
        {"account_id": None},
        {"venue": "OTHER"},
        {"instrument_id": "ETH-USD-PERP.HYPERLIQUID"},
        {"instrument_id": "BTC-USD-PERP.OTHER"},
        {"instrument_id": 1},
        {"kind": "fills"},
        {"kind": False},
        {"query": ()},
        {"query": (("instrument_id", None),)},
        {"query": (("instrument_id", report_fragments.INSTRUMENT),) * 2},
    ],
)
def test_request_and_completion_scope_must_match_exactly(changes: dict[str, Any]) -> None:
    scope, observation = proof()
    changed = replace(scope, **changes)
    assert assess_report(scope, replace(observation, scope=changed)).state == "UNKNOWN"


@pytest.mark.parametrize(
    "changes",
    [
        {"report_types": ("FillReport",)},
        {"report_types": ()},
        {"report_identities": (("HYPERLIQUID-OTHER", report_fragments.INSTRUMENT),)},
        {"report_identities": (("HYPERLIQUID-OFFLINE", "ETH-USD-PERP.HYPERLIQUID"),)},
        {"report_identities": ((None, report_fragments.INSTRUMENT),)},
        {"report_identities": ("HYPERLIQUID-OFFLINE",)},
        {"report_order_ids": ((True, "local-order-1"),)},
        {"report_order_ids": (("900001",),)},
        {"report_order_ids": ()},
    ],
)
def test_returned_report_family_identity_and_shape_must_match(changes: dict[str, Any]) -> None:
    scope, observation = proof(count=1)
    assert assess_report(scope, replace(observation, **changes)).state == "UNKNOWN"


@pytest.mark.parametrize(
    "query,identifiers,state",
    [
        (
            (("venue_order_id", "900001"), ("client_order_id", None)),
            ("900002", "local-order-1"),
            "UNKNOWN",
        ),
        (
            (("venue_order_id", "900001"), ("client_order_id", None)),
            (None, "local-order-1"),
            "UNKNOWN",
        ),
        (
            (("venue_order_id", None), ("client_order_id", "local-order-1")),
            ("900001", "local-order-1"),
            "BOUNDED_NONEMPTY",
        ),
        (
            (("venue_order_id", None), ("client_order_id", "local-order-1")),
            ("900001", "unresolved-cloid"),
            "UNKNOWN",
        ),
        (
            (("venue_order_id", "900001"), ("client_order_id", "local-order-1")),
            ("900001", "other-order"),
            "UNKNOWN",
        ),
        (
            (("venue_order_id", None), ("client_order_id", None)),
            ("900001", "local-order-1"),
            "UNKNOWN",
        ),
    ],
)
def test_single_report_matches_all_explicit_lookup_ids(
    query: tuple[tuple[str, str | None], ...],
    identifiers: tuple[str | None, str | None],
    state: str,
) -> None:
    scope, observation = proof("order", 1)
    scope = replace(scope, query=query)
    observation = replace(observation, scope=scope, report_order_ids=(identifiers,))
    assert assess_report(scope, observation).state == state


def test_single_report_rejects_multiple_results_and_spurious_bulk_receipt() -> None:
    scope, observation = proof("order", 2)
    assert assess_report(scope, observation).state == "UNKNOWN"
    scope, observation = proof("order", 0)
    assert assess_report(scope, replace(observation, receipt_counts=(0,))).state == "UNKNOWN"


def read_evidence() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EVIDENCE / "report-evidence.json").read_text())
    return data


def test_frozen_actual_report_evidence_and_contract_are_bound_to_source() -> None:
    data = read_evidence()
    report_fragments.verify(data)
    for name, digest in data["local_source_sha256"].items():
        assert hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() == digest
    assert (
        data["installed_source_sha256"]
        == json.loads(
            (EVIDENCE / "nautilus-probe.json").read_text(),
        )["installed_source_sha256"]
    )
    assert data["published_wheel_binding_verified"] is False
    assert {
        "native-transport",
        "wire-response-parser",
        "account-authentication",
        "pagination",
        "venue-reconciliation",
        "risk-permission",
    } <= set(data["not_executed"])
    for case in data["cases"]:
        expected_data = case["expected_scope"]
        expected = ReportScope(
            **{**expected_data, "query": tuple(tuple(item) for item in expected_data["query"])},
        )
        raw = case["observation"]
        scope_data = raw["scope"]
        observed = ReportObservation(
            **{
                **raw,
                "scope": ReportScope(
                    **{**scope_data, "query": tuple(tuple(item) for item in scope_data["query"])},
                ),
                "receipt_counts": tuple(raw["receipt_counts"]),
                "errors": tuple(raw["errors"]),
                "report_types": tuple(raw["report_types"]),
                "report_identities": tuple(tuple(item) for item in raw["report_identities"]),
                "report_order_ids": tuple(tuple(item) for item in raw["report_order_ids"]),
            },
        )
        assert assess_report(expected, observed).to_dict() == case["assessment"]
    cases = {case["case"]: case for case in data["cases"]}
    for kind in KINDS:
        assert (
            cases[f"{kind}:empty"]["adapter_return"] == cases[f"{kind}:timeout"]["adapter_return"]
        )
    fill = cases["fills:nonempty"]["returned_reports"][0]
    assert (fill["last_qty"], fill["last_px"], fill["commission"]) == (
        "0.04000",
        "100000",
        "1.80000000 USDC",
    )
    position = cases["positions:nonempty"]["returned_reports"][0]
    assert position["quantity"] == "0.10000"


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "wrong_state", "wrong_error", "permission"]
)
def test_runner_verification_rejects_incomplete_or_unexpected_evidence(change: str) -> None:
    data = copy.deepcopy(read_evidence())
    if change == "missing":
        data["cases"].pop()
    elif change == "duplicate":
        data["cases"].append(data["cases"][0])
    elif change == "wrong_state":
        data["cases"][0]["assessment"]["state"] = "UNKNOWN"
    elif change == "wrong_error":
        data["cases"][2]["observation"]["errors"] = []
    else:
        data["cases"][0]["assessment"]["new_risk_allowed"] = True
    with pytest.raises(RuntimeError):
        report_fragments.verify(data)


def test_report_guard_runs_before_audit_hook_and_optional_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HYPERLIQUID_TESTNET_PK", "synthetic-presence-only")
    touched: list[str] = []
    monkeypatch.setattr(sys, "addaudithook", lambda _hook: touched.append("audit"))
    monkeypatch.setattr(report_fragments, "load_candidate", lambda: touched.append("candidate"))
    with pytest.raises(RuntimeError, match="refuses credential"):
        report_fragments.run()
    assert touched == []


def test_return_driver_preserves_empty_return_and_refuses_suspension() -> None:
    async def complete() -> list[object]:
        return []

    class Suspend:
        def __await__(self) -> Generator[object, Any]:
            yield "unexpected suspension"

    async def wait() -> None:
        await Suspend()

    assert report_fragments.drive_return_without_loop(complete()) == []
    pending = wait()
    with pytest.raises(RuntimeError, match="unexpectedly suspended"):
        report_fragments.drive_return_without_loop(pending)
    assert getattr(pending, "cr_frame", object()) is None


def test_cli_failure_does_not_print_exception_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail() -> dict[str, Any]:
        raise RuntimeError("synthetic-sensitive-value")

    monkeypatch.setattr(report_fragments, "run", fail)
    assert report_fragments.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "synthetic-sensitive-value" not in captured.err
