"""Frozen actual-run observations and guards; normal CI has no optional adapter."""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fit_gates.hyperliquid_testnet_readiness import lifecycle, lifecycle_edges

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "fit_gates" / "hyperliquid_testnet_readiness"


def evidence() -> dict[str, Any]:
    result: dict[str, Any] = json.loads((EVIDENCE / "lifecycle-edges-evidence.json").read_text())
    return result


def test_edges_bind_exact_candidate_and_unchanged_historical_harness() -> None:
    data = evidence()
    assert data["schema"] == "hyperliquid-offline-lifecycle-edges-v1"
    assert data["candidate_version"] == "1.231.0"
    assert data["decision"] == "HOLD"
    assert data["published_wheel_binding_verified"] is False
    assert (
        data["harness_sha256"]
        == hashlib.sha256((EVIDENCE / "lifecycle_edges.py").read_bytes()).hexdigest()
    )
    original = json.loads((EVIDENCE / "lifecycle-evidence.json").read_text())
    assert data["shared_harness_sha256"] == original["harness_sha256"]
    assert data["installed_source_sha256"] == original["installed_source_sha256"]
    assert {
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
        "cancel-request-transport",
    } <= set(data["not_executed"])


def test_actual_successes_count_unique_fills_fees_and_real_core_trade_ids() -> None:
    cases = {item["case"]: item for item in evidence()["cases"]}
    assert set(cases) == {
        "duplicate_fill",
        "fill_before_accept",
        "buffered_duplicate_fill",
        "ioc_partial_cancel",
        "late_partial_after_cancel",
        "cancel_before_fill",
        "cancel_before_accept_late_fill",
    }
    for name in (
        "duplicate_fill",
        "fill_before_accept",
        "buffered_duplicate_fill",
        "ioc_partial_cancel",
    ):
        case = cases[name]
        assert case["fit"] == "pass"
        assert all(case["checks"].values())
        fills = [event for event in case["events"] if event["type"] == "OrderFilled"]
        assert len(fills) == 1
        assert fills[0]["trade_id"] == "700001"
        assert fills[0]["last_quantity"] == case["filled_quantity"] == "0.04000"
        assert case["leaves_quantity"] == "0.06000"
        assert fills[0]["commission"] == "1.80000000 USDC"
        assert case["core_commissions"] == ["1.80000000 USDC"]
        assert case["core_trade_ids"] == ["700001"]
        assert case["submit_calls"] == 1
        assert case["upstream_errors"] == []
        assert case["venue_order_id"] == "900001"
    assert cases["ioc_partial_cancel"]["final_status"] == "CANCELED"
    assert cases["ioc_partial_cancel"]["closed"] is True
    assert cases["duplicate_fill"]["steps"][0]["filled_quantity"] == "0.00000"
    buffered = cases["buffered_duplicate_fill"]
    assert [step["buffered_fills"] for step in buffered["steps"]] == [0, 1, 2, 2, 0]
    reordered = cases["fill_before_accept"]
    assert [event["type"] for event in reordered["events"]] == [
        "OrderSubmitted",
        "OrderAccepted",
        "OrderFilled",
    ]


def test_late_partial_fills_preserve_exposure_but_cannot_reterminalize_ioc_remainder() -> None:
    cases = {item["case"]: item for item in evidence()["cases"]}
    for name, quantity, fee, trades in (
        ("late_partial_after_cancel", "0.07000", "3.15000000 USDC", ["700001", "700002"]),
        ("cancel_before_fill", "0.04000", "1.80000000 USDC", ["700001"]),
    ):
        case = cases[name]
        assert case["fit"] == "failure"
        assert case["filled_quantity"] == quantity
        assert case["core_commissions"] == [fee]
        assert case["core_trade_ids"] == trades
        assert case["final_status"] == "PARTIALLY_FILLED" and case["closed"] is False
        assert case["checks"]["terminal_ioc_remainder"] is False
        assert case["checks"]["required_final_status"] is False
        assert all(
            value
            for key, value in case["checks"].items()
            if key not in {"terminal_ioc_remainder", "required_final_status"}
        )
        assert case["steps"][-1]["action"] in {"cancel4", "cancel7"}
        assert case["steps"][-1]["status"] == "PARTIALLY_FILLED"
        assert Decimal(case["filled_quantity"]) + Decimal(case["leaves_quantity"]) == Decimal("0.1")
        assert len([event for event in case["events"] if event["type"] == "OrderCanceled"]) == 1


def test_cancel_before_accept_exposes_external_routing_not_a_claim_of_global_loss() -> None:
    data = evidence()
    case = next(item for item in data["cases"] if item["case"] == "cancel_before_accept_late_fill")
    assert case["fit"] == "failure"
    assert case["filled_quantity"] == "0.00000"
    assert case["required"]["filled_quantity"] == "0.04000"
    assert case["core_commissions"] == case["core_trade_ids"] == []
    assert case["external_fills"][0]["last_quantity"] == "0.04000"
    assert case["external_fills"][0]["trade_id"] == "700001"
    assert case["external_fills"][0]["venue_order_id"] == "900001"
    assert case["external_fills"][0]["commission"] == "1.80000000 USDC"
    assert case["external_fill_reports"] == 1
    assert case["external_status_reports"] == 1
    assert case["venue_order_id"] is None
    assert case["upstream_errors"] == []
    assert case["steps"][0]["cloid_mappings"] == 0
    assert data["failed_cases"] == [
        "late_partial_after_cancel",
        "cancel_before_fill",
        "cancel_before_accept_late_fill",
    ]
    assert "reconciliation" in data["not_executed"]


def test_new_runner_refuses_credentials_before_audit_or_candidate_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HYPERLIQUID_TESTNET_PK", "synthetic-presence-only")
    touched: list[str] = []
    monkeypatch.setattr(sys, "addaudithook", lambda _hook: touched.append("audit"))
    monkeypatch.setattr(lifecycle, "load_candidate", lambda: touched.append("candidate"))
    with pytest.raises(RuntimeError, match="refuses credential"):
        lifecycle_edges.run()
    assert touched == []


@pytest.mark.parametrize("mode", ["TESTNET", "SHADOW", "LIVE", "", "paper"])
def test_new_runner_refuses_unsafe_modes_before_optional_code(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    touched: list[str] = []
    monkeypatch.setattr(sys, "addaudithook", lambda _hook: touched.append("audit"))
    monkeypatch.setattr(lifecycle, "load_candidate", lambda: touched.append("candidate"))
    with pytest.raises(RuntimeError, match="PAPER"):
        lifecycle_edges.run()
    assert touched == []


def test_runner_error_does_not_print_upstream_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail() -> dict[str, object]:
        raise RuntimeError("synthetic private detail")

    monkeypatch.setattr(lifecycle_edges, "run", fail)
    assert lifecycle_edges.main() == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "private" not in output.err
    assert "HOLD" in output.err


@pytest.mark.parametrize("change", ["outcome", "classification", "handoff"])
def test_observation_drift_or_false_pass_cannot_be_a_successful_run(change: str) -> None:
    case = next(
        item for item in evidence()["cases"] if item["case"] == "cancel_before_accept_late_fill"
    )
    if change == "outcome":
        case["checks"]["filled_quantity"] = True
    elif change == "classification":
        case["bounded_verdict"] = "PASS"
    else:
        case["external_fill_reports"] = 0
    with pytest.raises(RuntimeError):
        lifecycle_edges.verify_observation(case["case"], case)


@pytest.mark.parametrize("change", ["missing", "duplicate", "status"])
def test_missing_cases_and_unexpected_terminal_outcomes_fail(change: str) -> None:
    cases = evidence()["cases"]
    if change == "missing":
        cases.pop()
    elif change == "duplicate":
        cases[-1] = cases[0]
    else:
        cases[4]["final_status"] = "ACCEPTED"
    with pytest.raises(RuntimeError):
        lifecycle_edges.verify_cases(cases)


@pytest.mark.parametrize("change", ["missing_check", "non_boolean"])
def test_successful_check_removal_or_malformed_result_fails(change: str) -> None:
    case = evidence()["cases"][0]
    if change == "missing_check":
        case["checks"].pop("one_submit")
    else:
        case["checks"]["one_submit"] = 1
    with pytest.raises(RuntimeError, match="check schema"):
        lifecycle_edges.verify_observation(case["case"], case)


def test_synthetic_status_cumulative_quantities_and_timestamps_are_explicit() -> None:
    expected = {
        "accept": "0.00000",
        "cancel0": "0.00000",
        "cancel4": "0.04000",
        "cancel7": "0.07000",
    }
    for case in evidence()["cases"]:
        for step in case["steps"]:
            if step["action"] in expected:
                assert step["reported_filled_quantity"] == expected[step["action"]]
            if "reported_time_ns" in step:
                assert step["reported_time_ns"] == 1_793_347_200_000_000_000
