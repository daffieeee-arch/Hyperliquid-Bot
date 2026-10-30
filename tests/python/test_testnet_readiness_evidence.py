"""Evidence/guard checks only: CI does not install or exercise venue adapters."""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from fit_gates.hyperliquid_testnet_readiness.probe import (
    CREDENTIAL_NAMES,
    deny_external_io,
    probe,
    require_safe_environment,
)

from hyperliquid_bot.local_mode import UnsafeTradingModeError, require_local_paper_mode

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "docs" / "research"


def test_recorded_evidence_is_versioned_and_does_not_claim_execution_conformance() -> None:
    evidence = json.loads((RESEARCH / "hyperliquid-testnet-probe.json").read_text())
    assert evidence["schema"] == "hyperliquid-offline-readiness-probe-v1"
    assert evidence["package_version"] == "1.231.0"
    assert evidence["evidence_kind"] == "configuration-pure-identity-and-source-inventory-only"
    assert set(evidence["not_executed"]) == {
        "client-construction",
        "factory-create",
        "signing",
        "venue-requests",
        "orders",
        "cancel",
        "fill-processing",
        "account-reconciliation",
        "restart-recovery",
    }
    observation = evidence["config_observations"]
    assert observation["explicit_testnet"] is True
    assert observation["omitted_factory_environment_resolves_mainnet"] is True
    assert observation["private_key_unset"] is True
    assert observation["vault_unset"] is True
    assert observation["account_address_unset"] is True
    assert observation["builder_attribution"] is False
    assert observation["execution_type"] == "HyperliquidExecClientConfig"
    examples = evidence["pure_cloid_examples"]
    assert len(examples) == len(set(examples.values())) == 2
    assert all(re.fullmatch(r"0x[0-9a-f]{32}", value) for value in examples.values())
    assert evidence["example_mapping_repeat_matches"] is True
    assert set(evidence["installed_source_sha256"]) == {
        "__init__.py",
        "config.py",
        "factories.py",
        "execution.py",
    }
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for value in evidence["installed_source_sha256"].values()
    )
    # A method inventory is not a passing report/recovery test.
    assert {
        "generate_fill_reports",
        "generate_order_status_reports",
        "generate_position_status_reports",
    } <= set(evidence["execution_source_method_names"])


def test_package_inspection_preserves_pins_and_source_provenance() -> None:
    packages = json.loads((RESEARCH / "hyperliquid-testnet-packages.json").read_text())
    assert packages["schema"] == "hyperliquid-offline-package-inspection-v1"
    assert packages["execution_readiness"] == "not established"
    assert packages["venue_requests"] == "not performed"
    assert packages["nautilus"]["version"] == "1.231.0"
    assert packages["sdk"]["version"] == "0.24.0"
    assert packages["sdk"]["imported_or_constructed"] is False
    assert packages["nautilus"]["wheel_downloaded_for_this_assessment"] is False
    for name in ("nautilus", "sdk"):
        assert re.fullmatch(r"[0-9a-f]{64}", packages[name]["wheel_sha256"])
        assert packages[name]["metadata_url"].startswith("https://pypi.org/pypi/")
    lock = (ROOT / "fit_gates/d41_nautilus/requirements.lock").read_text()
    assert "nautilus-trader==1.231.0\n" in lock
    dependencies = (ROOT / "pyproject.toml").read_text()
    assert "nautilus-trader==" not in dependencies
    assert "hyperliquid-python-sdk==" not in dependencies


class NamesOnly(Mapping[str, str]):
    """A credential-bearing mapping whose values cannot be inspected."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __iter__(self) -> Iterator[str]:
        yield self.name

    def __len__(self) -> int:
        return 1

    def __getitem__(self, _key: str) -> str:
        raise AssertionError("Credential values must not be accessed.")


@pytest.mark.parametrize("name", CREDENTIAL_NAMES)
def test_probe_refuses_credential_presence_without_reading_values(name: str) -> None:
    with pytest.raises(RuntimeError, match="refuses credential"):
        require_safe_environment(NamesOnly(name))


def test_probe_checks_environment_before_any_optional_import_or_io_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HYPERLIQUID_TESTNET_PK", "synthetic-presence-only")
    called: list[str] = []
    module = importlib.import_module("fit_gates.hyperliquid_testnet_readiness.probe")
    monkeypatch.setattr(
        module.sys,
        "addaudithook",
        lambda _hook: called.append("hook"),
    )
    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda _name: called.append("import"),
    )
    with pytest.raises(RuntimeError, match="refuses credential"):
        probe()
    assert called == []


@pytest.mark.parametrize("mode", ["TESTNET", "SHADOW", "LIVE", "", "paper"])
def test_offline_assessment_does_not_relax_the_paper_boundary(mode: str) -> None:
    with pytest.raises(RuntimeError, match="PAPER"):
        require_safe_environment({"TRADING_MODE": mode})
    with pytest.raises(UnsafeTradingModeError):
        require_local_paper_mode(mode)


@pytest.mark.parametrize(
    "event",
    [
        "socket.__new__",
        "socket.connect",
        "socket.getaddrinfo",
        "subprocess.Popen",
        "os.system",
        "os.posix_spawn",
    ],
)
def test_python_external_io_guard_refuses_before_operation(event: str) -> None:
    with pytest.raises(RuntimeError, match="blocks Python"):
        deny_external_io(event, ())


def test_plain_offline_environment_and_source_reads_remain_available() -> None:
    require_safe_environment({})
    require_safe_environment({"TRADING_MODE": "PAPER"})
    deny_external_io("open", ("candidate-source.py", "r"))
