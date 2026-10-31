"""Create-only publication of completed collector chunks; originals are never removed."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

MAX_FILE_BYTES = 512 * 1024**2
MAX_FILES = 50_000


class CollectorStorageError(ValueError):
    """A source, destination or publication invariant was not satisfied."""


def checked_relative(text: str) -> Path:
    path = Path(text)
    if (
        not path.parts
        or path.is_absolute()
        or "//" in text
        or any(p in {".", ".."} for p in text.split("/"))
    ):
        raise CollectorStorageError("Unsafe relative collector path.")
    return path


def identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def directory_fd(path: Path) -> int:
    """Refuse symlink ancestors and require an existing private owned directory."""
    if not path.is_absolute() or ".." in path.parts:
        raise CollectorStorageError("Absolute private storage path required.")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            other = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = other
        value = os.fstat(descriptor)
        if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o700:
            raise CollectorStorageError("Storage directory must be owned and mode0700.")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def private_directory(path: Path) -> None:
    os.close(directory_fd(path))


def read_bytes(path: Path, *, limit: int) -> bytes:
    parent = directory_fd(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise CollectorStorageError("Invalid collector JSON evidence.")
        content = os.read(descriptor, limit + 1)
        if len(content) > limit or identity(before) != identity(os.fstat(descriptor)):
            raise CollectorStorageError("Collector JSON evidence changed or exceeded its bound.")
        return content
    finally:
        os.close(descriptor)


def read_json(path: Path, *, limit: int = 16_384) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise CollectorStorageError("Duplicate collector evidence key.")
            result[key] = value
        return result

    def invalid_constant(_text: str) -> object:
        raise CollectorStorageError("Non-finite collector evidence number.")

    result: object = json.loads(
        read_bytes(path, limit=limit), object_pairs_hook=unique, parse_constant=invalid_constant
    )
    if not isinstance(result, dict):
        raise CollectorStorageError("Collector evidence must be an object.")
    return result


def exclusive_json(path: Path, document: object) -> str:
    content = (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    parent = directory_fd(path.parent)
    try:
        descriptor = os.open(
            path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
    finally:
        os.close(parent)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return hashlib.sha256(content).hexdigest()


def snapshot_files(root: Path) -> dict[str, tuple[int, ...]]:
    private_directory(root)
    records: dict[str, tuple[int, ...]] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        for directory in directories:
            value = (Path(current) / directory).lstat()
            if (
                not stat.S_ISDIR(value.st_mode)
                or value.st_uid != os.getuid()
                or stat.S_IMODE(value.st_mode) != 0o700
            ):
                raise CollectorStorageError("Symlink or non-directory in collector chunk.")
        for name in files:
            path = Path(current) / name
            value = path.lstat()
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_uid != os.getuid()
                or value.st_nlink != 1
                or value.st_size > MAX_FILE_BYTES
            ):
                raise CollectorStorageError("Unexpected collector source file.")
            records[str(path.relative_to(root))] = identity(value)
            if len(records) > MAX_FILES:
                raise CollectorStorageError("Collector chunk file count exceeds its bound.")
    return records


def file_hash(path: Path) -> tuple[str, os.stat_result]:
    parent = directory_fd(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
            raise CollectorStorageError("Unexpected publication file.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if identity(os.fstat(descriptor)) != identity(before):
            raise CollectorStorageError("Publication file changed while hashing.")
        return digest, before
    finally:
        os.close(descriptor)


def _copy(
    source: Path, destination: Path, expected: tuple[int, ...], progress: Callable[[], None]
) -> dict[str, str | int]:
    source_parent = directory_fd(source.parent)
    try:
        reader = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_parent)
    finally:
        os.close(source_parent)
    writer = -1
    digest = hashlib.sha256()
    count = 0
    try:
        parent = directory_fd(destination.parent)
        try:
            writer = os.open(
                destination.name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
        finally:
            os.close(parent)
        if identity(os.fstat(reader)) != expected:
            raise CollectorStorageError("Closed collector source changed before publication.")
        while block := os.read(reader, 1024**2):
            progress()
            count += len(block)
            if count > MAX_FILE_BYTES:
                raise CollectorStorageError("Collector file exceeds publication bound.")
            digest.update(block)
            view = memoryview(block)
            while view:
                written = os.write(writer, view)
                if written <= 0:
                    raise CollectorStorageError("Short collector publication write.")
                view = view[written:]
        if count != expected[3] or identity(os.fstat(reader)) != expected:
            raise CollectorStorageError("Closed collector source changed while publishing.")
        os.fsync(writer)
        os.lseek(writer, 0, os.SEEK_SET)
        readback = hashlib.sha256()
        while block := os.read(writer, 1024**2):
            progress()
            readback.update(block)
        if readback.digest() != digest.digest():
            raise CollectorStorageError("Collector publication readback mismatch.")
        return {"bytes": count, "sha256": digest.hexdigest()}
    finally:
        os.close(reader)
        if writer >= 0:
            os.close(writer)


def publish_closed_chunk(
    source: Path,
    destination: Path,
    *,
    provenance: object,
    catalogue: Callable[[Path, Path], None] | None,
    progress: Callable[[], None] = lambda: None,
) -> dict[str, object]:
    """Publish copied raw/metadata and optionally a fresh destination-bound catalog.

    The destination is an owned new reservation; COMPLETE is published last.
    A failed reservation is retained and never resumed or overwritten.
    """
    private_directory(destination.parent)
    original = snapshot_files(source)
    if any(
        name in original
        for name in ("COMPLETE.json", ".COMPLETE.publishing", "publication-manifest.json")
    ):
        raise CollectorStorageError("Native source contains reserved publication files.")
    destination.mkdir(mode=0o700)
    records: dict[str, dict[str, str | int]] = {}
    for relative, expected in sorted(original.items()):
        # Catalogs contain local absolute paths. Regenerate from trusted code,
        # never execute or publish the copied local database as a NAS catalog.
        if relative == "research.duckdb":
            continue
        path = checked_relative(relative)
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        records[relative] = _copy(source / path, target, expected, progress)
    if snapshot_files(source) != original:
        raise CollectorStorageError("Closed collector inventory changed.")
    if catalogue is not None:
        catalogue(destination / "raw", destination / "research.duckdb")
        digest, value = file_hash(destination / "research.duckdb")
        records["research.duckdb"] = {"bytes": value.st_size, "sha256": digest}
    # Re-read every copied byte after all writes/catalog work. Native claims are
    # retained verbatim, including their original spool paths; this manifest is
    # the relocation binding, not a rewritten claim of an original NAS capture.
    for relative, record in records.items():
        digest, value = file_hash(destination / checked_relative(relative))
        if digest != record["sha256"] or value.st_size != record["bytes"]:
            raise CollectorStorageError("Published collector bytes changed before sealing.")
    if set(snapshot_files(destination)) != set(records) or snapshot_files(source) != original:
        raise CollectorStorageError("Publication inventory changed before sealing.")
    manifest = {
        "schema": "public-collector-closed-chunk-v1",
        "files": records,
        "provenance": provenance,
        "catalogue_regenerated": catalogue is not None,
        "raw_immutable": True,
        "power_loss_durability_claimed": False,
        "research_ready": False,
        "source_root": str(source),
        "destination_root": str(destination),
        "source_identities": original,
        "native_claims_preserved_verbatim": True,
        "source_catalogue_omitted": "research.duckdb" in original,
    }
    manifest_sha = exclusive_json(destination / "publication-manifest.json", manifest)
    marker = {
        "manifest_sha256": manifest_sha,
        "closed_chunk_verified": True,
        "research_ready": False,
    }
    exclusive_json(destination / ".COMPLETE.publishing", marker)
    receipt: dict[str, object] = {
        "destination": str(destination),
        "manifest_sha256": manifest_sha,
        "source_identity": original,
        "files": records,
    }
    os.link(
        destination / ".COMPLETE.publishing", destination / "COMPLETE.json", follow_symlinks=False
    )
    return receipt


def remove_verified_spool_duplicate(
    source: Path, first: dict[str, object], second: dict[str, object], *, campaign: Path
) -> None:
    """Remove only this runtime's closed local duplicate after two verified copies."""
    if (
        source == campaign
        or not source.is_relative_to(campaign)
        or not (campaign / "campaign.json").is_file()
        or first["destination"] == second["destination"]
    ):
        raise CollectorStorageError("Cleanup requires a runtime-owned chunk and two destinations.")
    original = snapshot_files(source)
    if first["source_identity"] != original or second["source_identity"] != original:
        raise CollectorStorageError("Spool cleanup identity mismatch.")
    for publication in (first, second):
        destination = Path(str(publication["destination"]))
        private_directory(destination)
        complete = read_json(destination / "COMPLETE.json")
        manifest = read_json(destination / "publication-manifest.json", limit=16 * 1024**2)
        manifest_sha, _value = file_hash(destination / "publication-manifest.json")
        if (
            complete["manifest_sha256"] != publication["manifest_sha256"]
            or complete.get("closed_chunk_verified") is not True
            or manifest_sha != publication["manifest_sha256"]
            or manifest.get("files") != publication["files"]
        ):
            raise CollectorStorageError("Spool cleanup requires both sealed NAS copies.")
        records = publication["files"]
        if not isinstance(records, dict):
            raise CollectorStorageError("Malformed publication receipt.")
        expected_files = set(original) - {"research.duckdb"}
        if set(records) - {"research.duckdb"} != expected_files:
            raise CollectorStorageError("Published inventory differs from closed source.")
        for relative, record in records.items():
            if not isinstance(relative, str) or not isinstance(record, dict):
                raise CollectorStorageError("Malformed publication receipt record.")
            path = destination / checked_relative(relative)
            digest, value = file_hash(path)
            if digest != record["sha256"] or value.st_size != record["bytes"]:
                raise CollectorStorageError("Spool cleanup NAS copy changed.")
    # A source-local catalog is derived and may be discarded only after the
    # destination-bound catalog and both raw copies have been accepted.
    first_files = first["files"]
    if not isinstance(first_files, dict) or "research.duckdb" not in first_files:
        raise CollectorStorageError("Destination catalog is required before cleanup.")
    for relative in sorted(original, key=lambda p: len(Path(p).parts), reverse=True):
        path = source / checked_relative(relative)
        parent = directory_fd(path.parent)
        try:
            if (
                identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
                != original[relative]
            ):
                raise CollectorStorageError("Spool source changed before duplicate cleanup.")
            os.unlink(path.name, dir_fd=parent)
        finally:
            os.close(parent)
    for current, directories, _files in os.walk(source, topdown=False, followlinks=False):
        parent = directory_fd(Path(current))
        try:
            for name in directories:
                os.rmdir(name, dir_fd=parent)
        finally:
            os.close(parent)
    parent = directory_fd(source.parent)
    try:
        os.rmdir(source.name, dir_fd=parent)
    finally:
        os.close(parent)
