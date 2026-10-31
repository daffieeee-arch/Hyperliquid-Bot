"""Bounded immutable local input snapshots for the offline PAPER command.

Only explicit regular parts are read. Remote inputs require the existing
mount verifier; local fixtures cannot stand in for a missing remote mount.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from hyperliquid_bot.migration_verify import (
    MountIdentity,
    _bound_mount,
    _descriptor_mount_id,
    _read_mountinfo,
    parse_mountinfo,
)
from hyperliquid_bot.paper_engine.errors import PaperTapeError
from hyperliquid_bot.paper_engine.replay import ReplayResources

LOCAL_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs", "tmpfs", "overlay", "zfs"})
RAW_COLUMNS = (
    ("schema_version", "INTEGER"),
    ("venue", "VARCHAR"),
    ("product", "VARCHAR"),
    ("channel", "VARCHAR"),
    ("session_id", "VARCHAR"),
    ("message_ordinal", "BIGINT"),
    ("received_utc_ns", "BIGINT"),
    ("received_monotonic_ns", "BIGINT"),
    ("direction", "VARCHAR"),
    ("frame_type", "VARCHAR"),
    ("payload_encoding", "VARCHAR"),
    ("payload_bytes", "BLOB"),
    ("payload_sha256", "VARCHAR"),
)


class OfflineInputError(PaperTapeError):
    """A safe, constant local guard diagnostic; never carries a raw venue payload."""


@dataclass(frozen=True, slots=True)
class ReplayLimits:
    files: int = 4
    physical_bytes: int = 16 * 1024 * 1024
    uncompressed_bytes: int = 64 * 1024 * 1024
    payload_bytes: int = 16 * 1024 * 1024
    rows: int = 5000
    events: int = 2000

    def __post_init__(self) -> None:
        maxima = (4, 16 * 1024 * 1024, 64 * 1024 * 1024, 16 * 1024 * 1024, 5000, 2000)
        for name, maximum in zip(self.__dataclass_fields__, maxima, strict=True):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise OfflineInputError(f"{name} must be a positive integer at most {maximum}.")


def absolute(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise OfflineInputError("Explicit absolute roots without parent traversal are required.")
    return path


def relative_part(value: str) -> Path:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix != ".parquet"
        or len(value) > 512
        or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._=-]*", part) or ".partial" in part.lower()
            for part in path.parts
        )
    ):
        raise OfflineInputError(
            "Parts must be explicit relative completed non-hidden .parquet paths."
        )
    return path


@contextmanager
def directory(path: Path) -> Iterator[int]:
    absolute(path)
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def descriptor_identity(descriptor: int) -> tuple[int, int, str]:
    metadata = os.fstat(descriptor)
    return metadata.st_dev, metadata.st_ino, _descriptor_mount_id(descriptor)


def descriptor_mount(descriptor: int) -> MountIdentity:
    value = os.fstat(descriptor)
    device = f"{os.major(value.st_dev)}:{os.minor(value.st_dev)}"
    mount_id = _descriptor_mount_id(descriptor)
    matches = [
        mount
        for mount in parse_mountinfo(_read_mountinfo())
        if mount.mount_id == mount_id and mount.device == device
    ]
    if len(matches) != 1:
        raise OfflineInputError("Directory descriptor has no unique kernel mount identity.")
    return matches[0]


def require_local(descriptor: int) -> MountIdentity:
    mount = descriptor_mount(descriptor)
    if mount.filesystem not in LOCAL_FILESYSTEMS:
        raise OfflineInputError(
            "Local fixture/scratch storage must use an explicitly supported local filesystem."
        )
    return mount


@contextmanager
def part_descriptor(root_descriptor: int, relative: Path) -> Iterator[int]:
    expected = descriptor_identity(root_descriptor)
    descriptor = os.dup(root_descriptor)
    try:
        for index, component in enumerate(relative.parts):
            flags = (
                os.O_RDONLY | os.O_NONBLOCK
                if index == len(relative.parts) - 1
                else os.O_PATH | os.O_DIRECTORY
            )
            next_descriptor = os.open(component, flags | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            value = os.fstat(descriptor)
            actual = (value.st_dev, _descriptor_mount_id(descriptor))
            if actual != (expected[0], expected[2]):
                raise OfflineInputError("Input crosses a changed or nested mount.")
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OfflineInputError("Input part must be a regular file.")
        yield descriptor
    finally:
        os.close(descriptor)


def signature(descriptor: int) -> tuple[int, int, int, int, int]:
    value = os.fstat(descriptor)
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def hash_descriptor(descriptor: int, size: int, destination: Path | None = None) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    count = 0
    handle = destination.open("xb") if destination is not None else None
    try:
        while block := os.read(descriptor, min(64 * 1024, size + 1 - count)):
            count += len(block)
            if count > size:
                raise OfflineInputError("Input grew during the bounded snapshot.")
            digest.update(block)
            if handle is not None:
                handle.write(block)
        if count != size:
            raise OfflineInputError("Input was truncated during the snapshot.")
    finally:
        if handle is not None:
            handle.close()
    return digest.hexdigest()


@dataclass(frozen=True)
class InputSnapshot:
    relative: Path
    path: Path
    source_signature: tuple[int, int, int, int, int]
    sha256: str

    def claim(self) -> dict[str, object]:
        return {
            "part": self.relative.as_posix(),
            "bytes": self.source_signature[2],
            "sha256": self.sha256,
        }


def snapshot_parts(
    root_descriptor: int,
    parts: Sequence[Path],
    staging: Path,
    limits: ReplayLimits,
) -> tuple[InputSnapshot, ...]:
    if not 1 <= len(parts) <= limits.files or len(set(parts)) != len(parts):
        raise OfflineInputError("Explicit part count/uniqueness exceeds the input bound.")
    seen: set[tuple[int, int]] = set()
    hashes: set[str] = set()
    snapshots: list[InputSnapshot] = []
    total = 0
    for index, relative in enumerate(parts):
        with part_descriptor(root_descriptor, relative) as descriptor:
            before = signature(descriptor)
            if before[:2] in seen or before[2] < 1:
                raise OfflineInputError("Duplicate file identity or empty input part.")
            total += before[2]
            if total > limits.physical_bytes:
                raise OfflineInputError("Physical input byte bound exceeded.")
            path = staging / f"part-{index:06}.parquet"
            digest = hash_descriptor(descriptor, before[2], path)
            if signature(descriptor) != before:
                raise OfflineInputError("Input changed while copying to local staging.")
            if digest in hashes:
                raise OfflineInputError("Duplicate input content is not a second part.")
            seen.add(before[:2])
            hashes.add(digest)
            snapshots.append(InputSnapshot(relative, path, before, digest))
    return tuple(snapshots)


def revalidate_parts(root_descriptor: int, snapshots: Sequence[InputSnapshot]) -> None:
    for snapshot in snapshots:
        with part_descriptor(root_descriptor, snapshot.relative) as descriptor:
            if signature(descriptor) != snapshot.source_signature:
                raise OfflineInputError("Input identity changed after the snapshot.")
            digest = hash_descriptor(descriptor, snapshot.source_signature[2])
            if digest != snapshot.sha256 or signature(descriptor) != snapshot.source_signature:
                raise OfflineInputError("Input bytes changed after the snapshot.")


def bind_remote(root: Path, expected: MountIdentity) -> MountIdentity:
    return _bound_mount(root, expected)[0]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OfflineInputError(
                "Duplicate JSON object keys make replay interpretation ambiguous."
            )
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise OfflineInputError("Non-standard JSON constants are not replay input.")


def preflight(
    snapshots: Sequence[InputSnapshot],
    limits: ReplayLimits,
    resources: ReplayResources,
) -> dict[str, int]:
    files = [str(item.path) for item in snapshots]
    connection = duckdb.connect(":memory:", config=resources.connection_config())
    try:
        footer = connection.execute(
            "SELECT sum(num_rows), bool_and(num_rows IS NOT NULL AND num_rows >= 0) "
            "FROM parquet_file_metadata(?)",
            [files],
        ).fetchone()
        uncompressed = connection.execute(
            "SELECT sum(total_uncompressed_size), "
            "bool_and(total_uncompressed_size IS NOT NULL AND total_uncompressed_size >= 0) "
            "FROM parquet_metadata(?)",
            [files],
        ).fetchone()
        if (
            footer is None
            or footer[1] is not True
            or type(footer[0]) is not int
            or not 1 <= footer[0] <= limits.rows
        ):
            raise OfflineInputError("Raw row bound exceeded or unavailable in Parquet footer.")
        if (
            uncompressed is None
            or uncompressed[1] is not True
            or type(uncompressed[0]) is not int
            or not 1 <= uncompressed[0] <= limits.uncompressed_bytes
        ):
            raise OfflineInputError("Uncompressed byte bound exceeded or unavailable.")
        for path in files:
            columns = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [path]
            ).fetchall()
            if tuple((row[0], row[1]) for row in columns) != RAW_COLUMNS:
                raise OfflineInputError("Every part must have the exact DATA-1A raw schema.")
        totals = connection.execute(
            "SELECT count(*), sum(octet_length(payload_bytes)) FROM read_parquet(?)",
            [files],
        ).fetchone()
        if (
            totals is None
            or totals[0] != footer[0]
            or type(totals[1]) is not int
            or not 1 <= totals[1] <= limits.payload_bytes
        ):
            raise OfflineInputError("Raw row/payload byte bounds disagree or are exceeded.")
        rows = connection.execute("SELECT * FROM read_parquet(?)", [files]).fetchall()
    finally:
        connection.close()
    events = 0
    identities: set[tuple[str, int]] = set()
    for row in rows:
        if any(value is None for value in row):
            raise OfflineInputError("Raw input contains missing fields.")
        if row[:3] != (1, "hyperliquid", "BTC-PERP"):
            raise OfflineInputError("Input schema/venue/product identity does not match BTC-PERP.")
        if any(
            type(row[index]) is not str or not row[index] or row[index].strip() != row[index]
            for index in (3, 4, 8, 9, 10)
        ):
            raise OfflineInputError("Raw text identity is missing or contains outer whitespace.")
        if row[9] not in {"text", "binary", "marker"} or row[10] not in {
            "utf-8",
            "utf-8-json",
            "binary",
        }:
            raise OfflineInputError("Raw frame/encoding identity is unsupported.")
        if not row[4] or len(row[4]) > 128 or row[5] <= 0 or min(row[6:8]) < 0:
            raise OfflineInputError("Input session/ordinal/time identity is invalid.")
        identity = (row[4], row[5])
        if identity in identities:
            raise OfflineInputError("Repeated raw session/ordinal identity is ambiguous.")
        identities.add(identity)
        if (
            row[8] not in {"inbound", "outbound", "local"}
            or row[12] != hashlib.sha256(row[11]).hexdigest()
        ):
            raise OfflineInputError("Input direction or exact payload hash is invalid.")
        if row[8] != "inbound" or row[3] not in {"trades", "bbo", "activeAssetCtx"}:
            continue
        if row[9] != "text" or row[10] not in {"utf-8", "utf-8-json"}:
            raise OfflineInputError("Selected market payload must be UTF-8 text.")
        payload: Any = json.loads(
            row[11].decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if type(payload) is not dict or payload.get("channel") != row[3]:
            raise OfflineInputError("Payload channel does not match the raw envelope.")
        data = payload.get("data")
        if row[3] == "trades":
            if type(data) is not list:
                raise OfflineInputError("Trades must contain a data array.")
            events += len(data)
        else:
            if type(data) is not dict:
                raise OfflineInputError("BBO/context must contain a data object.")
            # Conservatively count contexts without marks, too: the loader may
            # skip them, but no event expansion can escape this pre-dedup bound.
            events += 1
        if events > limits.events:
            raise OfflineInputError("Expanded event bound exceeded before dedup/replay.")
    if not events:
        raise OfflineInputError("Input contains no supported market events.")
    return {
        "raw_rows": len(rows),
        "payload_bytes": totals[1],
        "uncompressed_bytes": uncompressed[0],
        "expanded_event_upper_bound": events,
    }
