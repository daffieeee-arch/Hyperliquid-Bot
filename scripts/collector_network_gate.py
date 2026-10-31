#!/usr/bin/env python3
"""Operator-only TC gate for one waiting collector; never changes the host NIC."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

# The operator release retains reviewed scripts/ and src/ together. No package
# installation or mutable development checkout is needed on the NAS.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hyperliquid_bot.collector_native_storage import (
    CONTAINER_ROOTS,
    approved_host_root,
    filesystem_identity,
)

CONTAINER = "hyperliquid-public-collector"
ROOTS = {
    "/home/chupa/hyperliquid-spool": False,
    "/mnt/hyperliquid-work/market-data/capture": False,
    "/mnt/hyperliquid-archive/market-data": False,
    "/control": True,
}


def command(arguments: list[str]) -> str:
    return subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    ).stdout


def validate_container(
    document: dict[str, Any],
    *,
    image: str,
    source: str,
    profile: str = "vm-nfs",
    host_roots: dict[str, Path] | None = None,
    container: str = CONTAINER,
) -> int:
    if profile not in {"vm-nfs", "truenas-zfs"}:
        raise ValueError("Unknown collector gate profile.")
    native = profile == "truenas-zfs"
    if native:
        temporary = document["HostConfig"].get("Tmpfs")
        expected_options = {"rw", "noexec", "nosuid", "size=536870912", "mode=1777"}
        if not isinstance(temporary, dict) or set(temporary) != {"/tmp"}:
            raise ValueError("Native collector requires only the bounded private tmpfs.")
        options = temporary["/tmp"]
        if (
            not isinstance(options, str)
            or len(options.split(",")) != len(expected_options)
            or set(options.split(",")) != expected_options
        ):
            raise ValueError("Native tmpfs must match the reviewed size and isolation flags.")
        if host_roots is None or set(host_roots) != set(CONTAINER_ROOTS):
            raise ValueError("All four approved native host roots are required.")
        for role, path in host_roots.items():
            approved_host_root(role, path)
        labels = document["Config"]["Labels"]
        if (
            re.fullmatch(r"ix-hyperliquid-collector-collector-[1-9][0-9]*", container) is None
            or document.get("Name", "").lstrip("/") != container
            or labels.get("com.docker.compose.project") != "ix-hyperliquid-collector"
            or labels.get("com.docker.compose.service") != "collector"
        ):
            raise ValueError("Only the explicitly managed Hyperliquid collector may be admitted.")
    elif host_roots is not None or container != CONTAINER:
        raise ValueError("VM gate keeps its fixed container and mount identity.")
    config, host = document["Config"], document["HostConfig"]
    if (
        not document["State"]["Running"]
        or config["Image"] != image
        or config["Labels"].get("org.opencontainers.image.revision") != source
        or config["User"] != "1000:1000"
        or not host["ReadonlyRootfs"]
        or host["Privileged"]
        or host["NetworkMode"] in {"host", "none"}
        or host["NetworkMode"].startswith("container:")
        or host["CapAdd"]
        or "ALL" not in host["CapDrop"]
        or host["SecurityOpt"] != ["no-new-privileges:true"]
        or host["PortBindings"]
        or host["Memory"] != (2 if native else 8) * 1024**3
        or host["MemorySwap"] != host["Memory"]
        or host["NanoCpus"] != (2 if native else 4) * 1_000_000_000
        or host["PidsLimit"] != 128
        or host["RestartPolicy"]["Name"] != "no"
    ):
        raise ValueError("Container image or isolation does not match the reviewed runtime.")
    mounts = [mount for mount in document["Mounts"] if mount["Type"] == "bind"]
    if any(
        mount["Type"] != "bind"
        and not (mount["Type"] == "tmpfs" and mount["Destination"] == "/tmp")
        for mount in document["Mounts"]
    ):
        raise ValueError("Unexpected non-bind container mount.")
    if len(mounts) != 4:
        raise ValueError("Exactly the four approved data/control mounts are required.")
    for mount in mounts:
        destination = mount["Destination"]
        if destination not in ROOTS:
            raise ValueError("Unexpected container mount destination.")
        expected_source = (
            "/home/chupa/hyperliquid-runtime" if destination == "/control" else destination
        )
        if native:
            assert host_roots is not None
            role = next(role for role, path in CONTAINER_ROOTS.items() if path == destination)
            expected_source = str(host_roots[role])
        if (
            destination not in ROOTS
            or mount["Type"] != "bind"
            or mount["Source"] != expected_source
            or mount["RW"] == ROOTS[destination]
        ):
            raise ValueError("Unexpected container storage or secret/source mount.")
    return int(document["State"]["Pid"])


def native_storage_verification(
    host_roots: dict[str, Path], container: str
) -> dict[str, dict[str, object]]:
    """Bind each host directory to the effective mount seen by this exact image."""
    identities: dict[str, dict[str, object]] = {}
    for role, path in host_roots.items():
        approved_host_root(role, path)
        identity = filesystem_identity(path)
        if identity["filesystem"] != "zfs" or identity["source"] != "/".join(path.parts[2:4]):
            raise ValueError("Native storage is not the explicitly approved ZFS dataset.")
        identities[role] = identity
    directory_ids = {(value["device"], value["inode"]) for value in identities.values()}
    if len(directory_ids) != len(CONTAINER_ROOTS):
        raise ValueError("Native roles must not alias the same actual directory.")
    code = (
        "import json;from pathlib import Path;"
        "from hyperliquid_bot.collector_native_storage import CONTAINER_ROOTS,filesystem_identity;"
        "print(json.dumps({r:filesystem_identity(Path(p)) for r,p in CONTAINER_ROOTS.items()}))"
    )
    actual = json.loads(command(["docker", "exec", container, "python", "-B", "-c", code]))
    if actual != identities:
        raise ValueError("Container mounts differ from independently verified host directories.")
    # Recheck the host paths after the container read, before producing evidence.
    if any(filesystem_identity(host_roots[role]) != value for role, value in identities.items()):
        raise ValueError("Native storage identity changed during admission.")
    return {
        role: {
            "host_path": str(host_roots[role]),
            "container_path": CONTAINER_ROOTS[role],
            "identity": value,
        }
        for role, value in identities.items()
    }


def owned_veth(host_links: list[dict[str, Any]], container_link: dict[str, Any]) -> str:
    candidates = [item for item in host_links if item["ifindex"] == container_link["link_index"]]
    if len(candidates) != 1:
        raise ValueError("Unique container-owned veth peer could not be identified.")
    peer = candidates[0]
    if (
        peer.get("linkinfo", {}).get("info_kind") != "veth"
        or peer.get("link_index") != container_link["ifindex"]
        or re.fullmatch(r"veth[0-9a-f]+", peer["ifname"]) is None
    ):
        raise ValueError("Refusing a non-veth or unrelated host network interface.")
    return str(peer["ifname"])


def verified_rate(qdiscs: list[dict[str, Any]], expected_bytes: int) -> bool:
    roots = [item for item in qdiscs if item.get("root") is True]
    return (
        len(roots) == 1
        and roots[0].get("kind") == "tbf"
        and roots[0].get("options", {}).get("rate") == expected_bytes
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--profile", choices=("vm-nfs", "truenas-zfs"), default="vm-nfs")
    parser.add_argument("--container", default=CONTAINER)
    for role in CONTAINER_ROOTS:
        parser.add_argument("--" + role + "-host", type=Path)
    args = parser.parse_args()
    if (
        os.getuid() != 0
        or re.fullmatch(r"[0-9a-f]{40}", args.source) is None
        or re.fullmatch(
            r"ghcr.io/daffieeee-arch/hyperliquid-bot/collector@sha256:[0-9a-f]{64}", args.image
        )
        is None
        or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,35}", args.campaign) is None
    ):
        parser.error("Root, exact approved source/image and new campaign are required.")
    supplied = {role: getattr(args, role + "_host") for role in CONTAINER_ROOTS}
    host_roots = None
    if args.profile == "truenas-zfs":
        if any(path is None for path in supplied.values()):
            parser.error("Native profile needs all four explicit project host roots.")
        host_roots = {role: Path(path) for role, path in supplied.items()}
        for role, path in host_roots.items():
            approved_host_root(role, path)
    elif any(path is not None for path in supplied.values()) or args.container != CONTAINER:
        parser.error("VM profile does not accept alternative host roots or containers.")
    marker = (
        host_roots["control"] if host_roots else Path("/home/chupa/hyperliquid-runtime")
    ) / "network-ready.json"
    parent = marker.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 1000
        or stat.S_IMODE(parent.st_mode) != 0o700
        or marker.exists()
        or marker.is_symlink()
    ):
        raise ValueError("Private control root and absent network marker required.")

    def inspect() -> tuple[int, int]:
        documents = json.loads(command(["docker", "inspect", args.container]))
        pid = validate_container(
            documents[0],
            image=args.image,
            source=args.source,
            profile=args.profile,
            host_roots=host_roots,
            container=args.container,
        )
        inode = Path(f"/proc/{pid}/ns/net").stat().st_ino
        if inode == Path("/proc/self/ns/net").stat().st_ino:
            raise ValueError("Collector must have its own network namespace.")
        return pid, inode

    pid, inode = inspect()
    storage = native_storage_verification(host_roots, args.container) if host_roots else None
    prefix = ["nsenter", "--target", str(pid), "--net"]
    link = json.loads(command([*prefix, "ip", "-d", "-j", "link", "show", "dev", "eth0"]))[0]
    peer = owned_veth(json.loads(command(["ip", "-d", "-j", "link", "show"])), link)
    if inspect() != (pid, inode):
        raise ValueError("Collector identity changed before network enforcement.")
    # add, not replace: an unexpected prior qdisc fails closed. The host egress
    # peer is download into this container; its eth0 egress is aggregate upload.
    command(
        [
            "tc",
            "qdisc",
            "add",
            "dev",
            peer,
            "root",
            "tbf",
            "rate",
            "100000000bit",
            "burst",
            "262144",
            "latency",
            "100ms",
        ]
    )
    command(
        [
            *prefix,
            "tc",
            "qdisc",
            "add",
            "dev",
            "eth0",
            "root",
            "tbf",
            "rate",
            "5000000bit",
            "burst",
            "65536",
            "latency",
            "100ms",
        ]
    )
    download = json.loads(command(["tc", "-j", "qdisc", "show", "dev", peer]))
    upload = json.loads(command([*prefix, "tc", "-j", "qdisc", "show", "dev", "eth0"]))
    if (
        not verified_rate(download, 12_500_000)
        or not verified_rate(upload, 625_000)
        or inspect() != (pid, inode)
    ):
        raise ValueError("Aggregate kernel TC policy could not be independently read back.")
    report = {
        "schema": "collector-container-network-v1",
        "campaign": args.campaign,
        "source_commit": args.source,
        "image_digest": args.image.split("@")[1],
        "container_netns_inode": inode,
        "container_pid": pid,
        "host_veth": peer,
        "download_bits_per_second": 100_000_000,
        "upload_bits_per_second": 5_000_000,
        "kernel_tc_verified": True,
        "download_qdisc": download,
        "upload_qdisc": upload,
    }
    if storage is not None:
        report["storage_profile"] = "truenas-zfs"
        report["storage_verification"] = storage
    content = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    publishing = marker.with_name(".network-ready.publishing")
    descriptor = os.open(publishing, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(descriptor, 1000, 1000)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.link(publishing, marker, follow_symlinks=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
