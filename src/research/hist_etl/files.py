"""Small file helpers shared by the archive writers."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from research.hist_etl.models import SourceDigest


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` via a sibling temporary file, then rename it into place."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_json(path: Path, payload: object) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def warn(detail: str) -> None:
    print(f"warn\t{detail}", file=sys.stderr)


def decide_output(
    destination: Path,
    sources: Sequence[SourceDigest],
    *,
    rebuild: bool,
) -> str:
    """Whether an existing month file may be replaced.

    ``write`` creates or replaces the file. ``audit`` means the sidecar already
    lists these sources. ``untracked`` and ``changed`` leave the file alone
    unless the caller passed ``rebuild``.
    """

    if not destination.is_file():
        return "write"
    recorded = _recorded_sources(destination)
    if recorded is None:
        return "write" if rebuild else "untracked"
    expected = [{"name": source.name, "sha256": source.sha256} for source in sources]
    if recorded == expected:
        return "audit"
    if rebuild:
        return "write"
    return "changed"


def _recorded_sources(destination: Path) -> list[object] | None:
    sidecar = destination.with_name(destination.name + ".sources.json")
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    recorded = payload.get("sources")
    if not isinstance(recorded, list):
        return None
    return recorded
