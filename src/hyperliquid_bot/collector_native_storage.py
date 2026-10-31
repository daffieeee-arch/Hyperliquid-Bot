"""Explicit native ZFS mount identities, independently attested by the host gate."""

from __future__ import annotations

import os
import stat
from pathlib import Path

CONTAINER_ROOTS = {
    "spool": "/home/chupa/hyperliquid-spool",
    "capture": "/mnt/hyperliquid-work/market-data/capture",
    "archive": "/mnt/hyperliquid-archive/market-data",
    "control": "/control",
}
HOST_SUFFIXES = {
    "spool": ("hyperliquid-runtime", "spool"),
    "capture": ("hyperliquid-work", "market-data", "capture"),
    "archive": ("hyperliquid-archive", "market-data"),
    "control": ("hyperliquid-runtime", "control"),
}


def approved_host_root(role: str, path: Path) -> None:
    """Limit operator-supplied binds to one dedicated project dataset per role."""
    suffix = HOST_SUFFIXES[role]
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path.parts[1:2] != ("mnt",)
        or len(path.parts) != 3 + len(suffix)
        or path.parts[3:] != suffix
    ):
        raise ValueError("Native host root must be the explicit project dataset/role path.")


def filesystem_identity(path: Path) -> dict[str, object]:
    """Read the effective descriptor mount, refusing symlink ancestors or public roots.

    Mount IDs differ between host and container namespaces. Filesystem source,
    device and directory inode identify the same bound directory across both.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Absolute native storage root required.")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            other = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = other
        value = os.fstat(descriptor)
        if value.st_uid != 1000 or value.st_gid != 1000 or stat.S_IMODE(value.st_mode) != 0o700:
            raise ValueError("Native project roots require UID/GID1000 and mode0700.")
        mount_id = next(
            (
                line.partition(":")[2].strip()
                for line in Path(f"/proc/self/fdinfo/{descriptor}").read_text().splitlines()
                if line.startswith("mnt_id:")
            ),
            None,
        )
        if mount_id is None:
            raise ValueError("Effective native storage descriptor mount unavailable.")
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            before, separator, after = line.partition(" - ")
            if separator and before.split()[0] == mount_id:
                fields = after.split()
                return {
                    "filesystem": fields[0],
                    "source": fields[1],
                    "device": value.st_dev,
                    "inode": value.st_ino,
                }
        raise ValueError("Effective native storage mount unavailable.")
    finally:
        os.close(descriptor)


def require_native_storage(
    verification: object, roots: dict[str, Path]
) -> dict[str, dict[str, object]]:
    if not isinstance(verification, dict) or set(verification) != set(CONTAINER_ROOTS):
        raise ValueError("Complete host-bound native storage verification required.")
    result: dict[str, dict[str, object]] = {}
    directory_ids: set[tuple[object, object]] = set()
    for role, canonical in CONTAINER_ROOTS.items():
        binding = verification[role]
        if not isinstance(binding, dict) or roots[role] != Path(canonical):
            raise ValueError("Native canonical container paths must remain explicit.")
        host = binding.get("host_path")
        if not isinstance(host, str):
            raise ValueError("Native host path binding missing.")
        approved_host_root(role, Path(host))
        actual = filesystem_identity(roots[role])
        if (
            actual["filesystem"] != "zfs"
            or binding.get("container_path") != canonical
            or binding.get("identity") != actual
            or actual["source"] != "/".join(Path(host).parts[2:4])
        ):
            raise ValueError("Native ZFS dataset/directory does not match host admission.")
        directory_id = (actual["device"], actual["inode"])
        if directory_id in directory_ids:
            raise ValueError("Native roles require distinct actual directories, not bind aliases.")
        directory_ids.add(directory_id)
        result[role] = binding
    return result
