"""Small mid-run status file for the cockpit. Not terminal capture-health.

A fresh file proves the publisher is alive. Feed timestamps inside the file
are what prove market data is fresh. Readers must check both.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

CAPTURE_LIVE_SCHEMA: Final = "capture-live-v1"
CAPTURE_LIVE_NAME: Final = "capture-live.json"
_CODE_SHA_ENV: Final = "CAPTURE_CODE_SHA"


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def process_code_version() -> str:
    """SHA recorded by this process at start. Not a later checkout HEAD."""

    explicit = os.environ.get(_CODE_SHA_ENV, "").strip()
    if _looks_like_sha(explicit):
        return explicit
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
            cwd=os.getcwd(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    sha = completed.stdout.strip()
    if not _looks_like_sha(sha):
        return "unknown"
    return sha


def config_identity(fields: Mapping[str, object]) -> str:
    """Stable id of non-secret feed settings. Callers must omit credentials."""

    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stamp_start_metadata(
    claim: dict[str, object],
    *,
    config_fields: Mapping[str, object],
) -> dict[str, object]:
    claim["code_version"] = process_code_version()
    claim["config_identity"] = config_identity(config_fields)
    return claim


def write_capture_live(
    path: Path,
    *,
    run_id: str,
    writer_pending_records: int | None,
    writer_published_parts: int | None,
    feeds: Sequence[Mapping[str, object]],
    definitive_outage: Mapping[str, object] | None,
) -> None:
    """Atomically replace capture-live.json. Never raises into the capture loop."""

    document = {
        "schema": CAPTURE_LIVE_SCHEMA,
        "kind": "capture-live",
        "run_id": run_id,
        "published_utc": utc_now_text(),
        "writer_pending_records": writer_pending_records,
        "writer_published_parts": writer_published_parts,
        "feeds": [dict(feed) for feed in feeds],
        "definitive_outage": None if definitive_outage is None else dict(definitive_outage),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _looks_like_sha(value: str) -> bool:
    if len(value) < 7 or len(value) > 64:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value)
