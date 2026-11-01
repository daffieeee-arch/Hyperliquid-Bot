"""Real host/image admission functions accept only matching scoped campaign roots."""

from __future__ import annotations

import copy
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import collector_network_gate as gate

from hyperliquid_bot import collector_native_storage as native
from hyperliquid_bot import collector_runtime as runtime
from test_collector_native_storage import (
    CONTAINER,
    HOSTS,
    IMAGE,
    SOURCE,
    identities,
    native_document,
)

CAMPAIGN = "hl-native-public-20261101e"
CANONICAL = {role: Path(path) for role, path in native.CONTAINER_ROOTS.items()}


def campaign_hosts(campaign: str = CAMPAIGN) -> dict[str, Path]:
    return HOSTS | {
        role: HOSTS[role].with_name(f"{role}-{campaign}") for role in ("spool", "control")
    }


def document(hosts: dict[str, Path]) -> dict[str, Any]:
    result = native_document()
    for mount in result["Mounts"]:
        role = next(r for r, path in CANONICAL.items() if str(path) == mount["Destination"])
        mount["Source"] = str(hosts[role])
    return result


def boundary_fakes(
    monkeypatch: pytest.MonkeyPatch, hosts: dict[str, Path]
) -> dict[str, dict[str, object]]:
    values = identities()
    monkeypatch.setattr(
        gate, "filesystem_identity", lambda p: values[next(r for r in hosts if hosts[r] == p)]
    )
    monkeypatch.setattr(
        native,
        "filesystem_identity",
        lambda p: values[next(r for r in CANONICAL if CANONICAL[r] == p)],
    )
    monkeypatch.setattr(gate, "command", lambda _args: json.dumps(values))
    return values


@pytest.mark.parametrize(
    "campaign,legacy", [(CAMPAIGN, False), (CAMPAIGN, True), ("x", False), ("a" * 36, False)]
)
def test_actual_host_gate_and_image_admission_roundtrip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, campaign: str, legacy: bool
) -> None:
    hosts = HOSTS if legacy else campaign_hosts(campaign)
    boundary_fakes(monkeypatch, hosts)
    assert (
        gate.validate_container(
            document(hosts),
            image=IMAGE,
            source=SOURCE,
            profile="truenas-zfs",
            host_roots=hosts,
            container=CONTAINER,
            campaign=campaign,
        )
        == 123
    )
    proof = gate.native_storage_verification(hosts, CONTAINER, campaign=campaign)
    for role in hosts:
        assert proof[role]["host_path"] == str(hosts[role])
    plan = runtime.RuntimePlan(
        campaign,
        ("hyperliquid", "binance", "kraken", "bitvavo-standard"),
        600,
        1,
        True,
        SOURCE,
        IMAGE.split("@")[1],
        storage_profile="truenas-zfs",
    )
    marker = {
        "schema": "collector-container-network-v1",
        "campaign": campaign,
        "source_commit": SOURCE,
        "image_digest": plan.image_digest,
        "container_netns_inode": Path("/proc/self/ns/net").stat().st_ino,
        "download_bits_per_second": 100_000_000,
        "upload_bits_per_second": 5_000_000,
        "kernel_tc_verified": True,
        "storage_verification": proof,
    }
    path = tmp_path / "network-ready.json"
    path.write_text(json.dumps(marker))
    received = runtime.require_network_marker(path, plan)
    assert (
        native.require_native_storage(
            received["storage_verification"], CANONICAL, campaign=plan.campaign
        )
        == proof
    )
    path.write_text(json.dumps(marker | {"campaign": "different"}))
    with pytest.raises(ValueError, match="network enforcement"):
        runtime.require_network_marker(path, plan)


def test_actual_gate_cli_propagates_campaign_through_both_storage_checks_before_tc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hosts = campaign_hosts()
    values = boundary_fakes(monkeypatch, hosts)
    image = "ghcr.io/daffieeee-arch/hyperliquid-bot/collector@sha256:" + "b" * 64
    inspected = document(hosts)
    inspected["Config"]["Image"] = image
    marker = hosts["control"] / "network-ready.json"
    actual_lstat, actual_stat, actual_exists = Path.lstat, Path.stat, Path.exists
    own_inode = Path("/proc/self/ns/net").stat().st_ino
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda p: (
            SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
            if p == marker.parent
            else actual_lstat(p)
        ),
    )
    monkeypatch.setattr(Path, "exists", lambda p: False if p == marker else actual_exists(p))

    def stat_path(path: Path, *, follow_symlinks: bool = True) -> os.stat_result | SimpleNamespace:
        if path == Path("/proc/123/ns/net"):
            return SimpleNamespace(st_ino=own_inode + 1)
        return actual_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", stat_path)

    def command(arguments: list[str]) -> str:
        if arguments == ["docker", "inspect", CONTAINER]:
            return json.dumps([inspected])
        assert arguments[:3] == ["docker", "exec", CONTAINER]
        return json.dumps(values)

    monkeypatch.setattr(gate, "command", command)
    original_verification = gate.native_storage_verification
    verified: list[dict[str, dict[str, object]]] = []

    class StopBeforeNetworkMutation(Exception):
        pass

    def verify(
        paths: dict[str, Path], container: str, *, campaign: str | None = None
    ) -> dict[str, dict[str, object]]:
        verified.append(original_verification(paths, container, campaign=campaign))
        raise StopBeforeNetworkMutation

    monkeypatch.setattr(gate, "native_storage_verification", verify)
    arguments = [
        "collector_network_gate.py",
        "--source",
        SOURCE,
        "--image",
        image,
        "--campaign",
        CAMPAIGN,
        "--profile",
        "truenas-zfs",
        "--container",
        CONTAINER,
    ]
    for role, path in hosts.items():
        arguments.extend([f"--{role}-host", str(path)])
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(StopBeforeNetworkMutation):
        gate.main()
    assert len(verified) == 1
    assert {role: row["host_path"] for role, row in verified[0].items()} == {
        role: str(path) for role, path in hosts.items()
    }


@pytest.mark.parametrize(
    "campaign", [None, "different", "", "UPPER", "x" * 37, "../escape", "-prefix"]
)
def test_scoped_roots_without_matching_valid_campaign_are_refused_before_io(
    monkeypatch: pytest.MonkeyPatch, campaign: str | None
) -> None:
    hosts = campaign_hosts()
    proof = {role: {"host_path": str(path)} for role, path in hosts.items()}
    monkeypatch.setattr(gate, "filesystem_identity", lambda _p: pytest.fail("unapproved host read"))
    monkeypatch.setattr(
        native, "filesystem_identity", lambda _p: pytest.fail("unapproved image read")
    )
    monkeypatch.setattr(gate, "command", lambda _args: pytest.fail("unapproved container query"))
    with pytest.raises(ValueError):
        gate.validate_container(
            document(hosts),
            image=IMAGE,
            source=SOURCE,
            profile="truenas-zfs",
            host_roots=hosts,
            container=CONTAINER,
            campaign=campaign,
        )
    with pytest.raises(ValueError):
        gate.native_storage_verification(hosts, CONTAINER, campaign=campaign)
    with pytest.raises(ValueError):
        native.require_native_storage(proof, CANONICAL, campaign=campaign)


@pytest.mark.parametrize(
    "role,path",
    [
        ("spool", f"/mnt/apps/hyperliquid-runtime/control-{CAMPAIGN}"),
        ("control", f"/mnt/apps/hyperliquid-runtime/spool-{CAMPAIGN}"),
        ("spool", f"/mnt/apps/hyperliquid-runtime/spool-{CAMPAIGN}/nested"),
        ("control", f"/mnt/apps/public/control-{CAMPAIGN}"),
        ("spool", f"/mnt/apps/hyperliquid-runtime/../spool-{CAMPAIGN}"),
        ("control", "/mnt/apps/hyperliquid-runtime/control"),
        ("control", f"/mnt/other/hyperliquid-runtime/control-{CAMPAIGN}"),
        ("capture", f"/mnt/ssd/hyperliquid-work/market-data/capture-{CAMPAIGN}"),
        ("archive", f"/mnt/hdd/hyperliquid-archive/market-data-{CAMPAIGN}"),
    ],
)
def test_wrong_role_public_nested_traversal_mixed_or_other_dataset_roots_are_refused(
    monkeypatch: pytest.MonkeyPatch, role: str, path: str
) -> None:
    hosts = campaign_hosts() | {role: Path(path)}
    monkeypatch.setattr(gate, "filesystem_identity", lambda _p: pytest.fail("unapproved host read"))
    proof = {name: {"host_path": str(value)} for name, value in hosts.items()}
    with pytest.raises(ValueError):
        gate.validate_container(
            document(hosts),
            image=IMAGE,
            source=SOURCE,
            profile="truenas-zfs",
            host_roots=hosts,
            container=CONTAINER,
            campaign=CAMPAIGN,
        )
    with pytest.raises(ValueError):
        gate.native_storage_verification(hosts, CONTAINER, campaign=CAMPAIGN)
    with pytest.raises(ValueError):
        native.require_native_storage(proof, CANONICAL, campaign=CAMPAIGN)


def test_campaign_roots_still_refuse_bind_aliases_and_wrong_canonical_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hosts = campaign_hosts()
    values = boundary_fakes(monkeypatch, hosts)
    proof = gate.native_storage_verification(hosts, CONTAINER, campaign=CAMPAIGN)
    with pytest.raises(ValueError, match="canonical"):
        native.require_native_storage(
            proof, CANONICAL | {"spool": Path("/other")}, campaign=CAMPAIGN
        )
    wrong = copy.deepcopy(proof)
    wrong_identity = wrong["spool"]["identity"]
    assert isinstance(wrong_identity, dict)
    wrong_identity["inode"] = 999
    with pytest.raises(ValueError, match="does not match"):
        native.require_native_storage(wrong, CANONICAL, campaign=CAMPAIGN)
    values["control"] = values["spool"].copy()
    alias = copy.deepcopy(proof)
    alias["control"]["identity"] = values["control"]
    with pytest.raises(ValueError, match="alias"):
        gate.native_storage_verification(hosts, CONTAINER, campaign=CAMPAIGN)
    with pytest.raises(ValueError, match="distinct actual directories"):
        native.require_native_storage(alias, CANONICAL, campaign=CAMPAIGN)
