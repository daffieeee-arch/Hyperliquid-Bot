"""Optional installed-package probe: configs, pure cloid mapping and source hashes.

Run only with the already installed isolated Nautilus 1.231.0 environment.
No client/factory is constructed, no signer is called, and no SDK is imported.
Python socket/subprocess audit events fail closed; this is not an OS sandbox
for native extensions. The allowed native call is the pure cloid conversion.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

VERSION = "1.231.0"
SCHEMA = "hyperliquid-offline-readiness-probe-v1"
CREDENTIAL_NAMES = (
    "HYPERLIQUID_PK",
    "HYPERLIQUID_TESTNET_PK",
    "HYPERLIQUID_VAULT",
    "HYPERLIQUID_TESTNET_VAULT",
    "HYPERLIQUID_ACCOUNT_ADDRESS",
)


def require_safe_environment(environment: Mapping[str, str]) -> None:
    """Check presence only; never read, copy or print credential values."""

    names = set(environment)
    if any(name in names for name in CREDENTIAL_NAMES):
        raise RuntimeError("Offline probe refuses credential/account configuration.")
    if environment.get("TRADING_MODE", "PAPER") != "PAPER":
        raise RuntimeError("Offline probe requires the unchanged PAPER development boundary.")


def deny_external_io(event: str, _arguments: tuple[object, ...]) -> None:
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
        raise RuntimeError("Offline probe blocks Python network and subprocess operations.")


def probe() -> dict[str, object]:
    require_safe_environment(os.environ)
    # Install before importing third-party code. This guard cannot claim to
    # intercept sockets opened directly by Rust/native code.
    sys.addaudithook(deny_external_io)
    distribution = importlib.metadata.distribution("nautilus-trader")
    if distribution.version != VERSION:
        raise RuntimeError("Installed candidate differs from the assessed exact version.")
    config = importlib.import_module("nautilus_trader.adapters.hyperliquid.config")
    factories = importlib.import_module("nautilus_trader.adapters.hyperliquid.factories")
    native = importlib.import_module("nautilus_trader.core.nautilus_pyo3")
    execution_config = config.HyperliquidExecClientConfig(
        environment=native.HyperliquidEnvironment.TESTNET,
        include_builder_attribution=False,
    )
    data_config = config.HyperliquidDataClientConfig(
        environment=native.HyperliquidEnvironment.TESTNET,
    )
    identifiers = ["offline-readiness-000001", "offline-readiness-000002"]
    mappings = {
        identifier: native.hyperliquid_cloid_from_client_order_id(native.ClientOrderId(identifier))
        for identifier in identifiers
    }
    repeated = native.hyperliquid_cloid_from_client_order_id(native.ClientOrderId(identifiers[0]))
    root = Path(str(distribution.locate_file("nautilus_trader/adapters/hyperliquid")))
    sources = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("__init__.py", "config.py", "factories.py", "execution.py")
    }
    tree = ast.parse((root / "execution.py").read_text(encoding="utf-8"))
    execution = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "HyperliquidExecutionClient"
    )
    methods = sorted(
        node.name
        for node in execution.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    return {
        "schema": SCHEMA,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "evidence_kind": "configuration-pure-identity-and-source-inventory-only",
        "package_version": distribution.version,
        "runtime": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "config_observations": {
            "execution_type": type(execution_config).__name__,
            "data_type": type(data_config).__name__,
            "explicit_testnet": execution_config.environment
            == data_config.environment
            == native.HyperliquidEnvironment.TESTNET,
            "private_key_unset": execution_config.private_key is None,
            "account_address_unset": execution_config.account_address is None,
            "vault_unset": execution_config.vault_address is None,
            "builder_attribution": execution_config.include_builder_attribution,
            "omitted_environment_is_none": config.HyperliquidExecClientConfig().environment is None,
            "omitted_factory_environment_resolves_mainnet": factories._resolve_environment(None)
            == native.HyperliquidEnvironment.MAINNET,
        },
        "pure_cloid_examples": mappings,
        "example_mapping_repeat_matches": repeated == mappings[identifiers[0]],
        "execution_source_method_names": methods,
        "installed_source_sha256": sources,
        "not_executed": [
            "client-construction",
            "factory-create",
            "signing",
            "venue-requests",
            "orders",
            "cancel",
            "fill-processing",
            "account-reconciliation",
            "restart-recovery",
        ],
    }


def main() -> int:
    try:
        result = probe()
    except Exception:
        # Upstream exceptions can include operator configuration: keep output bounded.
        print(
            "Offline candidate probe failed; no execution readiness is established.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
