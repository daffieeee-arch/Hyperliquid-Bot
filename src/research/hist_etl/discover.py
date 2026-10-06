"""Find archives already on disk without deleting duplicates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from research.hist_etl.errors import HistEtlError
from research.hist_etl.models import ArchivePlan


def index_zips(root: Path) -> dict[str, list[Path]]:
    base = root / "binance-vision"
    found: dict[str, list[Path]] = {}
    if not base.is_dir():
        return found
    for path in base.rglob("*.zip"):
        if path.is_file() and not path.is_symlink():
            found.setdefault(path.name, []).append(path)
    return found


def checksum_beside(zip_path: Path) -> Path:
    return zip_path.with_name(zip_path.name + ".CHECKSUM")


def resolve_local(plan: ArchivePlan, index: Mapping[str, list[Path]]) -> ArchivePlan:
    matches: list[Path] = []
    if plan.canonical_path.is_file() and not plan.canonical_path.is_symlink():
        matches.append(plan.canonical_path)
    for candidate in index.get(plan.filename, []):
        if not _matches(candidate, plan):
            continue
        if candidate.resolve() == plan.canonical_path.resolve():
            continue
        matches.append(candidate)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in matches:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    if len(unique) > 1:
        listed = ", ".join(str(path) for path in unique)
        raise HistEtlError(f"ambiguous archives for {plan.filename}: {listed}", exit_code=2)
    if not unique:
        return plan
    local = unique[0]
    action = "present" if checksum_beside(local).is_file() else "needs_checksum"
    return replace(plan, local_path=local, action=action, size_bytes=local.stat().st_size)


def _matches(path: Path, plan: ArchivePlan) -> bool:
    """Reuse a zip whose directories name the market, dataset, and interval.

    USD-M is a path part ``um`` or ``futures-um``. Spot requires ``spot`` and
    neither of those markers. A matching filename alone is not reused.
    """

    if path.name != plan.filename:
        return False
    parts = set(path.parts)
    if plan.dataset not in parts:
        return False
    if plan.interval is not None and plan.interval not in parts:
        return False
    um = "um" in parts or "futures-um" in parts
    if plan.market == "spot":
        return "spot" in parts and not um
    return um
