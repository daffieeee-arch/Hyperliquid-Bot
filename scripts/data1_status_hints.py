"""Read-only reconnect/gap hints for DATA-1 status scripts.

PAPER ops only. Never invent counts. Missing or unparseable evidence prints n/a.
Does not open DuckDB, read Parquet payloads, or print secrets.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Final

_PROFILE: Final = r"([A-Za-z0-9._-]+)"
_SESSION_START: Final = re.compile(rf"session_start transport_profile={_PROFILE}")
_RECONNECT_ATTEMPT: Final = re.compile(
    rf"session_start transport_profile={_PROFILE} reason=reconnect_attempt"
)
_RECONNECT_LINE: Final = re.compile(rf"reconnect(?:ed)? transport_profile={_PROFILE}")
_GAP_LINE: Final = re.compile(
    rf"(?:gap_detected|\bgap\b).*transport_profile={_PROFILE}"
    rf"|transport_profile={_PROFILE}.*(?:gap_detected|\bgap\b)"
)


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "n/a"
    return ",".join(f"{name}:{counts[name]}" for name in sorted(counts))


def _non_negative_int(value: object) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def counts_from_health(health_path: Path) -> tuple[dict[str, int], dict[str, int]] | None:
    """Return per-profile counts from capture-health.json, or None if unknown."""

    if not health_path.is_file():
        return None
    try:
        payload = json.loads(health_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    profiles = payload.get("transport_profiles")
    if not isinstance(profiles, list) or not profiles:
        return None
    reconnects: dict[str, int] = {}
    gaps: dict[str, int] = {}
    for item in profiles:
        if not isinstance(item, dict):
            return None
        name = item.get("transport_profile")
        reconnect_count = _non_negative_int(item.get("reconnects"))
        gap_count = _non_negative_int(item.get("gaps"))
        if not isinstance(name, str) or not name or reconnect_count is None or gap_count is None:
            return None
        reconnects[name] = reconnect_count
        gaps[name] = gap_count
    return reconnects, gaps


def counts_from_capture_log(
    log_path: Path,
) -> tuple[dict[str, int], dict[str, int] | None] | None:
    """Count reconnect (and gap if labeled) lines. Unknown logs stay n/a."""

    if not log_path.is_file():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.strip():
        return None

    seen_session: set[str] = set()
    reconnects: dict[str, int] = {}
    gaps: dict[str, int] = {}
    recognized = False
    for line in text.splitlines():
        session = _SESSION_START.search(line)
        if session is not None:
            seen_session.add(session.group(1))
            recognized = True
        reconnect_attempt = _RECONNECT_ATTEMPT.search(line)
        reconnect_line = _RECONNECT_LINE.search(line)
        if reconnect_attempt is not None:
            name = reconnect_attempt.group(1)
            reconnects[name] = reconnects.get(name, 0) + 1
            recognized = True
        elif reconnect_line is not None:
            name = reconnect_line.group(1)
            reconnects[name] = reconnects.get(name, 0) + 1
            recognized = True
        gap = _GAP_LINE.search(line)
        if gap is not None:
            name = gap.group(1) or gap.group(2)
            if name:
                gaps[name] = gaps.get(name, 0) + 1
                recognized = True
    if not recognized:
        return None
    for name in seen_session:
        reconnects.setdefault(name, 0)
    return reconnects, (gaps if gaps else None)


def render_transport_hints(
    run_dir: Path,
    run_id: str,
    artifact_root: Path | None = None,
) -> list[str]:
    health = counts_from_health(run_dir / "capture-health.json")
    if health is not None:
        reconnects, gaps = health
        return [
            "transport_hints_source=health",
            f"transport_reconnects={_format_counts(reconnects)}",
            f"transport_gaps={_format_counts(gaps)}",
        ]

    log_candidates = [run_dir / f"capture-{run_id}.log"]
    if artifact_root is not None:
        log_candidates.append(artifact_root / "logs" / f"capture-{run_id}.log")
    for log_path in log_candidates:
        parsed = counts_from_capture_log(log_path)
        if parsed is None:
            continue
        reconnects, gaps = parsed
        gap_text = _format_counts(gaps) if gaps is not None else "n/a"
        return [
            "transport_hints_source=capture-log",
            f"transport_reconnects={_format_counts(reconnects)}",
            f"transport_gaps={gap_text}",
        ]

    return [
        "transport_hints_source=n/a",
        "transport_reconnects=n/a",
        "transport_gaps=n/a",
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifact-root", default="")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_dir = Path(args.run_dir)
    artifact_root = Path(args.artifact_root) if args.artifact_root else None
    if not run_dir.is_dir() or not args.run_id:
        print("transport_hints_source=n/a")
        print("transport_reconnects=n/a")
        print("transport_gaps=n/a")
        return
    for line in render_transport_hints(run_dir, args.run_id, artifact_root):
        print(line)


if __name__ == "__main__":
    main()
