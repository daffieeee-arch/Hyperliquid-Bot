"""Native storage binding and managed collector isolation, without NAS operations."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import collector_network_gate as gate

from hyperliquid_bot import collector_native_storage as native
from hyperliquid_bot import collector_runtime as runtime
from test_collector_runtime import container_document, plan, prepare_controller

SOURCE = "a" * 40
IMAGE = "fixture@sha256:" + "b" * 64
CONTAINER = "ix-hyperliquid-collector-collector-1"
HOSTS = {
    "spool": Path("/mnt/apps/hyperliquid-runtime/spool"),
    "control": Path("/mnt/apps/hyperliquid-runtime/control"),
    "capture": Path("/mnt/ssd/hyperliquid-work/market-data/capture"),
    "archive": Path("/mnt/hdd/hyperliquid-archive/market-data"),
}


def identities() -> dict[str, dict[str, object]]:
    return {
        role: {
            "filesystem": "zfs",
            "source": "/".join(path.parts[2:4]),
            "device": index + 40,
            "inode": index + 400,
        }
        for index, (role, path) in enumerate(HOSTS.items())
    }


def bindings() -> dict[str, dict[str, Any]]:
    return {
        role: {
            "host_path": str(path),
            "container_path": native.CONTAINER_ROOTS[role],
            "identity": identities()[role],
        }
        for role, path in HOSTS.items()
    }


def native_document() -> dict[str, Any]:
    doc = container_document()
    doc["Name"] = "/" + CONTAINER
    doc["Config"]["Labels"].update(
        {
            "com.docker.compose.project": "ix-hyperliquid-collector",
            "com.docker.compose.service": "collector",
        }
    )
    doc["HostConfig"].update(
        Memory=2 * 1024**3,
        MemorySwap=2 * 1024**3,
        NanoCpus=2_000_000_000,
        Tmpfs={"/tmp": "rw,noexec,nosuid,size=536870912,mode=1777"},
    )
    for mount in doc["Mounts"]:
        role = next(r for r, p in native.CONTAINER_ROOTS.items() if p == mount["Destination"])
        mount["Source"] = str(HOSTS[role])
    return doc


def test_native_profile_is_explicit_and_vm_defaults_are_preserved() -> None:
    assert plan().storage_profile == "vm-nfs"
    assert plan(storage_profile="truenas-zfs").storage_profile == "truenas-zfs"
    invalid: list[object] = ["zfs", "native", "", [], True]
    for value in invalid:
        with pytest.raises(ValueError):
            plan(storage_profile=value)
    assert gate.validate_container(container_document(), image=IMAGE, source=SOURCE) == 123
    assert (
        gate.validate_container(
            native_document(),
            image=IMAGE,
            source=SOURCE,
            profile="truenas-zfs",
            host_roots=HOSTS,
            container=CONTAINER,
        )
        == 123
    )
    with pytest.raises(ValueError):
        gate.validate_container(native_document(), image=IMAGE, source=SOURCE)


@pytest.mark.parametrize(
    "temporary",
    [
        None,
        {},
        {"/tmp": "rw,size=536870912,mode=1777"},
        {"/tmp": "rw,noexec,nosuid,size=1073741824,mode=1777"},
        {"/tmp": "rw,noexec,nosuid,exec,size=536870912,mode=1777"},
        {"/tmp": "rw,noexec,nosuid,suid,size=536870912,mode=1777"},
        {"/tmp": "rw,noexec,nosuid,noexec,size=536870912,mode=1777"},
        {"/tmp": "rw,noexec,nosuid,size=536870912,mode=1777", "/other": "rw"},
        {"/tmp": 536870912},
    ],
)
def test_native_gate_rejects_unbounded_or_executable_temporary_mount(temporary: object) -> None:
    doc = native_document()
    doc["HostConfig"]["Tmpfs"] = temporary
    with pytest.raises(ValueError, match="tmpfs"):
        gate.validate_container(
            doc,
            image=IMAGE,
            source=SOURCE,
            profile="truenas-zfs",
            host_roots=HOSTS,
            container=CONTAINER,
        )


def test_native_tmpfs_policy_is_order_independent() -> None:
    doc = native_document()
    doc["HostConfig"]["Tmpfs"] = {"/tmp": "mode=1777,nosuid,rw,size=536870912,noexec"}
    assert (
        gate.validate_container(
            doc,
            image=IMAGE,
            source=SOURCE,
            profile="truenas-zfs",
            host_roots=HOSTS,
            container=CONTAINER,
        )
        == 123
    )


def test_read_only_control_cannot_alias_writable_spool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = identities()
    values["control"] = dict(values["spool"])
    proof = bindings()
    for role in proof:
        proof[role]["identity"] = values[role]
    roots = {r: Path(p) for r, p in native.CONTAINER_ROOTS.items()}
    monkeypatch.setattr(
        native, "filesystem_identity", lambda p: values[next(r for r, x in roots.items() if x == p)]
    )
    with pytest.raises(ValueError, match="distinct actual directories"):
        native.require_native_storage(proof, roots)


def test_host_gate_refuses_control_alias_before_querying_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = identities()
    values["control"] = dict(values["spool"])
    monkeypatch.setattr(
        gate, "filesystem_identity", lambda p: values[next(r for r, x in HOSTS.items() if x == p)]
    )
    monkeypatch.setattr(
        gate, "command", lambda _args: pytest.fail("Must reject before Docker exec")
    )
    with pytest.raises(ValueError, match="alias the same actual directory"):
        gate.native_storage_verification(HOSTS, CONTAINER)


@pytest.mark.parametrize(
    "path",
    [
        "/mnt/apps",
        "/mnt/apps/unrelated/spool",
        "/mnt/apps/hyperliquid-runtime",
        "/mnt/apps/hyperliquid-runtime/../spool",
        "relative/spool",
        "/home/hyperliquid-runtime/spool",
    ],
)
def test_only_scoped_native_project_roots_are_accepted(path: str) -> None:
    with pytest.raises(ValueError):
        native.approved_host_root("spool", Path(path))


def test_native_binding_checks_all_effective_directory_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {r: Path(p) for r, p in native.CONTAINER_ROOTS.items()}
    by_path = {roots[r]: identity for r, identity in identities().items()}
    monkeypatch.setattr(native, "filesystem_identity", lambda p: by_path[p])
    approved = bindings()
    assert native.require_native_storage(approved, roots) == approved
    for role in native.CONTAINER_ROOTS:
        for field, wrong in (
            ("filesystem", "ext4"),
            ("source", "other/dataset"),
            ("device", 999),
            ("inode", 998),
        ):
            changed = copy.deepcopy(approved)
            changed[role]["identity"][field] = wrong
            with pytest.raises(ValueError):
                native.require_native_storage(changed, roots)
        absent = copy.deepcopy(approved)
        absent.pop(role)
        with pytest.raises(ValueError):
            native.require_native_storage(absent, roots)
    with pytest.raises(ValueError):
        native.require_native_storage(approved, roots | {"capture": Path("/other")})
    by_path[roots["capture"]] = identities()["capture"] | {"filesystem": "nfs4"}
    changed = copy.deepcopy(approved)
    changed["capture"]["identity"] = by_path[roots["capture"]]
    with pytest.raises(ValueError):
        native.require_native_storage(changed, roots)


def test_effective_mount_reader_refuses_symlink_ancestors_and_reads_fd_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "private"
    target.mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        native.filesystem_identity(tmp_path / "link")
    original = os.fstat

    def fixture_owner(fd: int) -> SimpleNamespace:
        value = original(fd)
        return SimpleNamespace(
            st_uid=1000,
            st_gid=1000,
            st_mode=value.st_mode,
            st_dev=value.st_dev,
            st_ino=value.st_ino,
        )

    monkeypatch.setattr(native, "os", SimpleNamespace(**(vars(os) | {"fstat": fixture_owner})))
    identity = native.filesystem_identity(target)
    assert identity["device"] == target.stat().st_dev
    assert identity["inode"] == target.stat().st_ino
    assert identity["filesystem"] and identity["source"]


@pytest.mark.parametrize(
    "change", ["resources", "labels", "name", "write-control", "extra-mount", "host-path"]
)
def test_native_gate_refuses_wrong_resources_app_identity_and_mounts(change: str) -> None:
    doc = native_document()
    if change == "resources":
        doc["HostConfig"]["Memory"] = 8 * 1024**3
    elif change == "labels":
        doc["Config"]["Labels"]["com.docker.compose.project"] = "ix-other-app"
    elif change == "name":
        doc["Name"] = "/another-collector"
    elif change == "write-control":
        doc["Mounts"][-1]["RW"] = True
    elif change == "extra-mount":
        doc["Mounts"].append(
            {"Type": "bind", "Destination": "/secrets", "Source": "/secrets", "RW": False}
        )
    else:
        doc["Mounts"][0]["Source"] = "/mnt/apps/other/spool"
    with pytest.raises(ValueError):
        gate.validate_container(
            doc,
            image=IMAGE,
            source=SOURCE,
            profile="truenas-zfs",
            host_roots=HOSTS,
            container=CONTAINER,
        )


def test_host_and_container_effective_mount_proofs_must_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = identities()
    monkeypatch.setattr(
        gate, "filesystem_identity", lambda p: values[next(r for r, x in HOSTS.items() if x == p)]
    )
    calls: list[list[str]] = []

    def command(args: list[str]) -> str:
        calls.append(args)
        return json.dumps(values)

    monkeypatch.setattr(gate, "command", command)
    assert gate.native_storage_verification(HOSTS, CONTAINER) == bindings()
    assert calls[0][:4] == ["docker", "exec", CONTAINER, "python"]
    monkeypatch.setattr(
        gate,
        "command",
        lambda _args: json.dumps(values | {"spool": values["spool"] | {"inode": 999}}),
    )
    with pytest.raises(ValueError):
        gate.native_storage_verification(HOSTS, CONTAINER)


@pytest.mark.parametrize("marker_profile", [None, "vm-nfs", "truenas-zfs"])
def test_native_runtime_requires_profile_and_storage_before_any_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker_profile: str | None
) -> None:
    roots = prepare_controller(tmp_path, monkeypatch)
    data = json.loads(roots["network_marker"].read_text())
    data["storage_profile"] = marker_profile
    roots["network_marker"].write_text(json.dumps(data))
    calls: list[object] = []

    def reject(verification: object, paths: dict[str, Path]) -> dict[str, dict[str, object]]:
        calls.append(verification)
        assert paths["control"] == roots["clock_path"].parent
        raise ValueError("synthetic missing native proof")

    monkeypatch.setattr(runtime, "require_native_storage", reject)
    monkeypatch.setattr(
        "hyperliquid_bot.collector_runtime.subprocess.Popen",
        lambda *_a, **_k: pytest.fail("adapter started without storage proof"),
    )
    with pytest.raises(ValueError):
        runtime.collect_campaign(plan(storage_profile="truenas-zfs"), **roots)
    assert len(calls) == (1 if marker_profile == "truenas-zfs" else 0)
    assert not (roots["spool"] / "fixture").exists()
