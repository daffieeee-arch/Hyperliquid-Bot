"""CI checks frozen fragment evidence and fail-closed guards, not optional venue adapters."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import sys
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fit_gates.hyperliquid_testnet_readiness import lifecycle

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "fit_gates" / "hyperliquid_testnet_readiness"


def test_fragment_evidence_preserves_actual_core_outcomes_and_explicit_limits() -> None:
    data = json.loads((EVIDENCE / "lifecycle-evidence.json").read_text())
    assert data["schema"] == lifecycle.SCHEMA
    assert data["candidate_version"] == "1.231.0"
    assert data["decision"] == "HOLD"
    assert data["evidence_kind"] == "actual-unbound-python-adapter-methods-and-core-order-apply"
    assert data["published_wheel_binding_verified"] is False
    assert (
        data["harness_sha256"]
        == hashlib.sha256((EVIDENCE / "lifecycle.py").read_bytes()).hexdigest()
    )
    cases = {case["case"]: case for case in data["cases"]}
    assert set(cases) == {"ack", "reject", "partial", "timeout", "missing_ack"}
    expected = {
        "ack": ("ACCEPTED", ["OrderSubmitted", "OrderAccepted"]),
        "reject": ("REJECTED", ["OrderSubmitted", "OrderRejected"]),
        "partial": ("PARTIALLY_FILLED", ["OrderSubmitted", "OrderAccepted", "OrderFilled"]),
        "timeout": ("SUBMITTED", ["OrderSubmitted"]),
        "missing_ack": ("SUBMITTED", ["OrderSubmitted"]),
    }
    for name, (status, events) in expected.items():
        case = cases[name]
        assert case["final_status"] == status
        assert case["submit_calls"] == 1
        assert [event["type"] for event in case["events"]] == events
        assert case["filled_quantity"] == ("0.04000" if name == "partial" else "0.00000")
        assert case["leaves_quantity"] == ("0.06000" if name == "partial" else "0.10000")
        assert case["request"]["quantity"] == "0.10000"
        assert case["request"]["price"] == "100000"
        assert all(
            event["client_order_id"] == case["request"]["client_order_id"]
            for event in case["events"]
        )
    fill = cases["partial"]["events"][-1]
    assert (fill["last_quantity"], fill["price"], fill["commission"]) == (
        "0.04000",
        "100000",
        "1.80000000 USDC",
    )
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
        "cancel-races",
        "ioc-remainder-terminal-cancel",
    } <= set(data["not_executed"])
    assert (
        data["installed_source_sha256"]
        == json.loads((EVIDENCE / "nautilus-probe.json").read_text())["installed_source_sha256"]
    )


def test_maintenance_observation_does_not_upgrade_the_candidate_or_claim_support() -> None:
    data = json.loads((EVIDENCE / "maintenance-observation.json").read_text())
    assert data["schema"] == "hyperliquid-offline-candidate-maintenance-v1"
    assert data["assessed_candidate"] == "1.231.0"
    assert data["latest_published_2_0_rc_observed"] == "2.0.0rc8"
    assert data["stable_2_0_0_published"] is False
    assert data["sources"]["pypi"] == "https://pypi.org/pypi/nautilus_trader/json"
    assert data["sources"]["rc_release"].endswith("/releases/tag/v2.0.0rc8")
    assert "HOLD" in data["decision"]
    assert (
        "nautilus-trader==1.231.0\n"
        in (ROOT / "fit_gates/d41_nautilus/requirements.lock").read_text()
    )


def test_fragment_refuses_credentials_before_audit_installation_or_candidate_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HYPERLIQUID_TESTNET_PK", "synthetic-presence-only")
    touched: list[str] = []
    monkeypatch.setattr(sys, "addaudithook", lambda _hook: touched.append("audit"))
    monkeypatch.setattr(lifecycle, "load_candidate", lambda: touched.append("candidate"))
    with pytest.raises(RuntimeError, match="refuses credential"):
        lifecycle.run()
    assert touched == []


def test_wrong_version_and_source_drift_fail_before_optional_imports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = "2.0.0rc8"
    monkeypatch.setattr(
        importlib.metadata,
        "distribution",
        lambda _name: SimpleNamespace(
            version=version,
            locate_file=lambda path: tmp_path / path,
        ),
    )
    imports: list[str] = []
    monkeypatch.setattr(importlib, "import_module", lambda name: imports.append(name))
    with pytest.raises(RuntimeError, match="exact assessed"):
        lifecycle.load_candidate()
    version = "1.231.0"
    source = tmp_path / "nautilus_trader/adapters/hyperliquid"
    source.mkdir(parents=True)
    for name in ("__init__.py", "config.py", "execution.py", "factories.py"):
        (source / name).write_text("# changed candidate source\n")
    with pytest.raises(RuntimeError, match="source differs"):
        lifecycle.load_candidate()
    assert imports == []


def test_coroutine_driver_completes_inline_and_refuses_suspension() -> None:
    async def complete() -> None:
        return None

    class Suspend:
        def __await__(self) -> Generator[object, Any]:
            yield "unexpected transport suspension"

    async def wait() -> None:
        await Suspend()

    lifecycle.drive_without_loop(complete())
    pending = wait()
    with pytest.raises(RuntimeError, match="unexpectedly suspended"):
        lifecycle.drive_without_loop(pending)
    assert getattr(pending, "cr_frame", object()) is None


def test_command_errors_do_not_disclose_upstream_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail() -> dict[str, object]:
        raise RuntimeError("synthetic private exception detail")

    monkeypatch.setattr(lifecycle, "run", fail)
    assert lifecycle.main() == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "HOLD" in output.err
    assert "private" not in output.err
