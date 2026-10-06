"""SHA256 helpers for Binance Vision ``.CHECKSUM`` files.

Official check: ``sha256sum -c FILE.zip.CHECKSUM`` (binance-public-data README).
The file is GNU coreutils format: 64 hex digits, a blank or ``*`` mode marker,
then the archive name. A bare hash is accepted as well.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from research.hist_etl.errors import HistEtlError

_LINE = re.compile(r"([0-9a-fA-F]{64})(?:\s+\*?(\S+))?")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def cached_sha256(root: Path, path: Path) -> str:
    """Hash ``path``, reusing a scratch cache keyed by size and mtime.

    The cache lives under ``staging/hist_etl/`` and is never a source archive.
    """

    stat = path.stat()
    cache_dir = root / "staging" / "hist_etl" / "sha256"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    cache = cache_dir / f"{key}.json"
    if cache.is_file():
        payload = json.loads(cache.read_text(encoding="utf-8"))
        if (
            isinstance(payload, dict)
            and payload.get("size") == stat.st_size
            and payload.get("mtime_ns") == stat.st_mtime_ns
            and isinstance(payload.get("sha256"), str)
        ):
            return str(payload["sha256"])
    digest = sha256_file(path)
    cache.write_text(
        json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest}),
        encoding="utf-8",
    )
    return digest


def parse_checksum(text: str, filename: str) -> str:
    """Return the single SHA256 hex digest for ``filename``."""

    digests: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.fullmatch(line)
        if match is None:
            raise HistEtlError(f"checksum line is not SHA256 for {filename}", exit_code=2)
        digest = match.group(1).lower()
        name = match.group(2)
        if name is not None and Path(name).name != filename:
            raise HistEtlError(
                f"checksum name {name} does not match {filename}",
                exit_code=2,
            )
        digests.append(digest)
    if len(digests) != 1:
        raise HistEtlError(f"checksum file for {filename} must contain one hash", exit_code=2)
    return digests[0]


def verify_zip(zip_path: Path, checksum_path: Path) -> str:
    """Compare a zip to its sibling checksum. Returns the expected digest."""

    expected = parse_checksum(checksum_path.read_text(encoding="utf-8"), zip_path.name)
    actual = sha256_file(zip_path)
    if actual != expected:
        raise HistEtlError(
            f"checksum mismatch for {zip_path.name}: expected {expected} actual {actual}",
            exit_code=2,
        )
    return expected
