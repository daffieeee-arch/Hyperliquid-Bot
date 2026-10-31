"""Comparator and scheduling guards only; optional adapter runs in isolated D01 CI."""

from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fit_gates.hyperliquid_testnet_readiness import verify_ci


def frozen(spec: verify_ci.FragmentSpec) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((verify_ci.ROOT / spec.artifact).read_text())
    return result


@pytest.mark.parametrize("spec", verify_ci.SPECS, ids=lambda spec: spec.name)
def test_every_business_field_matches_while_explicit_runtime_metadata_may_differ(
    spec: verify_ci.FragmentSpec,
) -> None:
    recorded = frozen(spec)
    observed = copy.deepcopy(recorded)
    observed["runtime"] = {"python": "3.13.16", "machine": "aarch64", "libc": ["glibc", "2.39"]}
    result = verify_ci.compare_evidence(spec, observed, recorded)
    assert result["case_count"] == len(spec.cases)
    assert result["recorded_runtime"] == recorded["runtime"]
    assert result["observed_runtime"] == observed["runtime"]
    assert result["recorded_runtime"] != result["observed_runtime"]


@pytest.mark.parametrize(
    "mutation",
    [
        "quantity",
        "fee",
        "client_id",
        "source_hash",
        "runner_hash",
        "schema",
        "version",
        "case_missing",
        "case_duplicate",
        "case_renamed",
        "extra_field",
        "nested_runtime",
        "bool_to_int",
        "int_to_bool",
        "decision",
        "limits",
    ],
)
def test_business_identity_schema_and_case_drift_cannot_be_ignored(mutation: str) -> None:
    spec = verify_ci.SPECS[0]
    recorded = frozen(spec)
    observed = copy.deepcopy(recorded)
    partial = observed["cases"][2]
    if mutation == "quantity":
        partial["filled_quantity"] = "0.05000"
    elif mutation == "fee":
        partial["events"][-1]["commission"] = "0.00000000 USDC"
    elif mutation == "client_id":
        partial["events"][-1]["client_order_id"] = "other-order"
    elif mutation == "source_hash":
        observed["installed_source_sha256"]["execution.py"] = "0" * 64
    elif mutation == "runner_hash":
        observed["harness_sha256"] = "0" * 64
    elif mutation == "schema":
        observed["schema"] = "unreviewed-v2"
    elif mutation == "version":
        observed["candidate_version"] = "2.0.0rc8"
    elif mutation == "case_missing":
        observed["cases"].pop()
    elif mutation == "case_duplicate":
        observed["cases"][1] = observed["cases"][0]
    elif mutation == "case_renamed":
        partial["case"] = "unknown"
    elif mutation == "extra_field":
        observed["ignored_business"] = "must-be-compared"
    elif mutation == "nested_runtime":
        partial["runtime"] = {"filled_quantity": "must-not-be-excluded"}
    elif mutation == "bool_to_int":
        observed["published_wheel_binding_verified"] = 0
    elif mutation == "int_to_bool":
        partial["submit_calls"] = True
    elif mutation == "decision":
        observed["decision"] = "PASS"
    else:
        observed["not_executed"].remove("reconciliation")
    with pytest.raises(ValueError):
        verify_ci.compare_evidence(spec, observed, recorded)


@pytest.mark.parametrize("side", ["observed", "recorded"])
@pytest.mark.parametrize(
    "runtime",
    [
        None,
        [],
        {},
        {"python": True, "machine": "x86_64", "libc": ["glibc", "2.39"]},
        {"python": "3.13.15", "machine": 64, "libc": ["glibc", "2.39"]},
        {"python": "3.13.15", "machine": "x86_64", "libc": ("glibc", "2.39")},
        {"python": "3.13.15", "machine": "x86_64", "libc": ["glibc", 2.39]},
        {"python": "3.13.15", "machine": "x86_64", "libc": ["glibc"]},
        {"python": "3.13.15", "machine": "x86_64", "libc": ["glibc", "2.39"], "extra": 1},
        {"python": "3.13.15", "machine": "x86_64\nextra", "libc": ["glibc", "2.39"]},
    ],
)
def test_runtime_exclusion_requires_exact_keys_and_strict_bounded_types(
    side: str,
    runtime: object,
) -> None:
    spec = verify_ci.SPECS[0]
    recorded = frozen(spec)
    observed = copy.deepcopy(recorded)
    (observed if side == "observed" else recorded)["runtime"] = runtime
    with pytest.raises(ValueError):
        verify_ci.compare_evidence(spec, observed, recorded)


def test_known_hold_results_remain_compared_and_cannot_be_relabelled_pass() -> None:
    spec = verify_ci.SPECS[1]
    recorded = frozen(spec)
    assert len(recorded["failed_cases"]) == 3
    for mutation in ("classification", "failed_check", "new_risk", "all_safety"):
        observed = copy.deepcopy(recorded)
        if mutation == "classification":
            observed["cases"][4]["bounded_verdict"] = "PASS"
        elif mutation == "failed_check":
            observed["cases"][4]["checks"]["terminal_ioc_remainder"] = True
        elif mutation == "new_risk":
            observed["new_risk_permitted"] = True
        else:
            observed["all_safety_cases_pass"] = True
        with pytest.raises(ValueError):
            verify_ci.compare_evidence(spec, observed, recorded)


def test_canonical_json_preserves_scalar_types_and_rejects_non_json_keys_and_values() -> None:
    assert verify_ci._canonical(True) != verify_ci._canonical(1)
    assert verify_ci._canonical(1) != verify_ci._canonical(1.0)
    # Existing report dataclass tuples have the same JSON-array encoding as its CLI.
    assert verify_ci._canonical(("TimeoutError",)) == verify_ci._canonical(["TimeoutError"])
    for invalid in ({1: "numeric-key"}, {"bad": object()}, {"bad": float("nan")}, float("inf")):
        with pytest.raises((TypeError, ValueError)):
            verify_ci._canonical(invalid)


@pytest.fixture
def fake_runners(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []
    specs = {spec.name: spec for spec in verify_ci.SPECS}
    monkeypatch.setattr(sys, "addaudithook", lambda _hook: called.append("audit"))

    def import_runner(name: str) -> SimpleNamespace:
        short = name.rsplit(".", 1)[1]
        assert name == f"fit_gates.hyperliquid_testnet_readiness.{short}"
        called.append(f"import:{short}")

        def run() -> dict[str, Any]:
            called.append(f"run:{short}")
            return frozen(specs[short])

        return SimpleNamespace(run=run)

    monkeypatch.setattr(importlib, "import_module", import_runner)
    return called


def test_registry_executes_all_three_required_runners_and_58_counted_observations(
    fake_runners: list[str],
) -> None:
    result = verify_ci.run()
    assert fake_runners == [
        "audit",
        "import:lifecycle",
        "run:lifecycle",
        "import:lifecycle_edges",
        "run:lifecycle_edges",
        "import:report_fragments",
        "run:report_fragments",
    ]
    assert [item["case_count"] for item in result["runners"]] == [5, 7, 46]
    assert sum(len(item["counted_cases"]) for item in result["runners"]) == 58
    assert result["runners"][1]["known_conformance_hold_cases"] == [
        "late_partial_after_cancel",
        "cancel_before_fill",
        "cancel_before_accept_late_fill",
    ]
    assert result["decision"] == "HOLD"
    assert result["new_risk_permitted"] is False and result["all_safety_cases_pass"] is False


@pytest.mark.parametrize("mode", ["TESTNET", "SHADOW", "LIVE", ""])
def test_unsafe_mode_stops_before_any_audit_import_or_actual_run(
    monkeypatch: pytest.MonkeyPatch,
    fake_runners: list[str],
    mode: str,
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    with pytest.raises(RuntimeError, match="PAPER"):
        verify_ci.run()
    assert fake_runners == []


def test_credential_presence_stops_before_any_audit_import_or_actual_run(
    monkeypatch: pytest.MonkeyPatch,
    fake_runners: list[str],
) -> None:
    monkeypatch.setenv("HYPERLIQUID_TESTNET_PK", "synthetic-presence-only")
    with pytest.raises(RuntimeError, match="refuses credential"):
        verify_ci.run()
    assert fake_runners == []


@pytest.mark.parametrize("bad_registry", [(), verify_ci.SPECS[:2], verify_ci.SPECS[::-1]])
def test_missing_or_reordered_registry_never_becomes_zero_run_success(
    monkeypatch: pytest.MonkeyPatch,
    fake_runners: list[str],
    bad_registry: tuple[verify_ci.FragmentSpec, ...],
) -> None:
    monkeypatch.setattr(verify_ci, "SPECS", bad_registry)
    with pytest.raises(RuntimeError, match="registry"):
        verify_ci.run()
    assert fake_runners == ["audit"]


def test_missing_or_duplicate_evidence_fails_before_optional_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_runners: list[str],
) -> None:
    monkeypatch.setattr(verify_ci, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        verify_ci.run()
    assert fake_runners == ["audit"]
    (tmp_path / verify_ci.SPECS[0].artifact).write_text('{"schema":"one","schema":"two"}')
    with pytest.raises(ValueError, match="Duplicate"):
        verify_ci.run()
    assert fake_runners == ["audit", "audit"]


@pytest.mark.parametrize("failure", ["dependency", "runner_error", "no_result"])
def test_actual_runner_or_import_failure_is_nonzero_and_does_not_disclose_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_runners: list[str],
    failure: str,
) -> None:
    def import_runner(_name: str) -> SimpleNamespace:
        if failure == "dependency":
            raise ModuleNotFoundError("synthetic private dependency detail")

        def run() -> None:
            if failure == "runner_error":
                raise RuntimeError("synthetic private runner detail")
            return None

        return SimpleNamespace(run=run)

    monkeypatch.setattr(importlib, "import_module", import_runner)
    assert verify_ci.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private" not in captured.err and "HOLD" in captured.err
