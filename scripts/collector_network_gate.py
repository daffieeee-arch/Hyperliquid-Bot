#!/usr/bin/env python3
"""Operator-only TC gate for one waiting collector; never changes the host NIC."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

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


def validate_container(document: dict[str, Any], *, image: str, source: str) -> int:
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
        or host["Memory"] != 8 * 1024**3
        or host["MemorySwap"] != host["Memory"]
        or host["NanoCpus"] != 4_000_000_000
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
        expected_source = (
            "/home/chupa/hyperliquid-runtime" if destination == "/control" else destination
        )
        if (
            destination not in ROOTS
            or mount["Type"] != "bind"
            or mount["Source"] != expected_source
            or mount["RW"] == ROOTS[destination]
        ):
            raise ValueError("Unexpected container storage or secret/source mount.")
    return int(document["State"]["Pid"])


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
    marker = Path("/home/chupa/hyperliquid-runtime/network-ready.json")
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
        documents = json.loads(command(["docker", "inspect", CONTAINER]))
        pid = validate_container(documents[0], image=args.image, source=args.source)
        inode = Path(f"/proc/{pid}/ns/net").stat().st_ino
        if inode == Path("/proc/self/ns/net").stat().st_ino:
            raise ValueError("Collector must have its own network namespace.")
        return pid, inode

    pid, inode = inspect()
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
