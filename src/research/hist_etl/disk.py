"""Free-space guard. Source archives are never deleted to make room."""

from __future__ import annotations

import shutil
from pathlib import Path

from research.hist_etl.errors import HistEtlError


def assert_safe_root(root: Path) -> Path:
    """Resolve the archive root and refuse live capture trees."""

    resolved = root.expanduser().resolve()
    parts = {part.lower() for part in resolved.parts}
    if "data-capture" in parts:
        raise HistEtlError(
            "hist root must not be inside data-capture; keep the warehouse in its own tree",
            exit_code=2,
        )
    return resolved


def nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return current
        current = parent
    return current


def free_bytes(path: Path) -> int:
    """Return free bytes on the filesystem that would hold ``path``."""

    return int(shutil.disk_usage(nearest_existing(path)).free)


def assert_free(path: Path, minimum: int) -> int:
    """Refuse work when free space is below ``minimum`` bytes."""

    if minimum < 0:
        raise HistEtlError("min_free_bytes must be >= 0")
    free = free_bytes(path)
    if free < minimum:
        raise HistEtlError(
            f"free space {free} bytes is below the minimum {minimum} bytes",
            exit_code=3,
        )
    return free
