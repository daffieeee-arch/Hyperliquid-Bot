"""External PAPER capture checker. Independent of the collector process.

Reads capture-claim, capture-health, capture-live, and published Parquet mtimes.
Optionally asks tmux whether the session still exists (``has-session`` only)
and whether a live process command line contains the run id. It never attaches
to a session, sends keys, or restarts a collector.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from .capture_operator_alert import sanitize_alert_error_message
from .reconstructable_paths import (
    DATA1A_CLAIM_NAME,
    DATA1A_HEALTH_NAME,
    DATA1A_RAW_DIR_NAME,
    DATA1A_RELATIVE_PREFIX,
    DATA1B_RELATIVE_PREFIX,
    DATA1D_RELATIVE_PREFIX,
    DATA1E_RELATIVE_PREFIX,
    DATA1F_RELATIVE_PREFIX,
    require_run_id,
)

CAPTURE_LIVE_NAME: Final = "capture-live.json"
DEFAULT_STALE_SECONDS: Final = 180.0
DEFAULT_LIVE_STALE_SECONDS: Final = 45.0
DEFAULT_STARTUP_GRACE_SECONDS: Final = 180.0
DEFAULT_FAILED_LOOKBACK_SECONDS: Final = 86_400.0
_TERMINAL_OK: Final = frozenset({"COMPLETED", "OPERATOR_STOP"})
_SESSION_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class WatchLane:
    """One reconstructable retain lane the checker knows how to read."""

    venue: str
    relative_prefix: tuple[str, ...]
    tmux_session: str


WATCH_LANES: Final = (
    WatchLane("hyperliquid", DATA1A_RELATIVE_PREFIX, "hl-capture"),
    WatchLane("kraken", DATA1B_RELATIVE_PREFIX, "kr-capture"),
    WatchLane("bitvavo", DATA1D_RELATIVE_PREFIX, "bv-std-capture"),
    WatchLane("bitvavo", DATA1E_RELATIVE_PREFIX, "bv-capture"),
    WatchLane("binance", DATA1F_RELATIVE_PREFIX, "bn-capture"),
)


@dataclass(frozen=True, slots=True)
class CaptureWatchSnapshot:
    """Facts about one run. Probes are None when that check was not performed."""

    venue: str
    run_id: str
    run_dir: Path
    health_status: str | None
    health_mtime: datetime | None
    health_reason: str
    claim_mtime: datetime | None
    code_version: str
    last_parquet_mtime: datetime | None
    live_published: datetime | None
    outage_reason: str | None
    tmux_alive: bool | None
    process_alive: bool | None


@dataclass(frozen=True, slots=True)
class CaptureWatchFinding:
    """One alert the checker should deliver."""

    venue: str
    run_id: str
    state: str
    reason: str
    code_version: str
    last_write: datetime | None
    run_dir: Path


def parse_utc_timestamp(value: object) -> datetime | None:
    if type(value) is not str or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if type(value) is not dict:
        return None
    return cast(dict[str, object], value)


def _mtime(path: Path) -> datetime | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def _newest_parquet_mtime(raw_dir: Path) -> datetime | None:
    try:
        if not raw_dir.is_dir():
            return None
        names = list(raw_dir.glob("part-*.parquet"))
    except OSError:
        return None
    latest: datetime | None = None
    for path in names:
        moment = _mtime(path)
        if moment is None:
            continue
        if latest is None or moment > latest:
            latest = moment
    return latest


def _health_reason(health: Mapping[str, object] | None) -> str:
    if health is None:
        return "capture_health_failed"
    kind = health.get("exception_class")
    message = health.get("preserved_profile_error_message")
    if type(kind) is str and kind.isidentifier():
        if type(message) is str and message.strip():
            return sanitize_alert_error_message(f"{kind}: {message}")
        return kind
    return "capture_health_failed"


def _outage_reason(live: Mapping[str, object] | None) -> str | None:
    if live is None:
        return None
    outage = live.get("definitive_outage")
    if type(outage) is not dict:
        return None
    kind = outage.get("error_class")
    message = outage.get("error_message")
    if type(kind) is str and kind.isidentifier():
        if type(message) is str and message.strip():
            return sanitize_alert_error_message(f"{kind}: {message}")
        return kind
    return "definitive_outage"


def _code_version(claim: Mapping[str, object] | None) -> str:
    if claim is None:
        return "unknown"
    version = claim.get("code_version")
    if type(version) is not str or not version.strip():
        return "unknown"
    return version.strip()


def load_capture_watch_snapshot(
    *,
    venue: str,
    run_dir: Path,
    tmux_alive: bool | None,
    process_alive: bool | None,
) -> CaptureWatchSnapshot | None:
    """Load one run directory. Returns None when there is no claim and no health."""

    try:
        run_id = require_run_id(run_dir.name)
    except (TypeError, ValueError):
        return None
    claim_path = run_dir / DATA1A_CLAIM_NAME
    health_path = run_dir / DATA1A_HEALTH_NAME
    if not claim_path.is_file() and not health_path.is_file():
        return None
    claim = _read_json(claim_path)
    health = _read_json(health_path)
    live = _read_json(run_dir / CAPTURE_LIVE_NAME)
    health_status = health.get("status") if health is not None else None
    status = health_status if type(health_status) is str else None
    published = live.get("published_utc") if live is not None else None
    return CaptureWatchSnapshot(
        venue=venue,
        run_id=run_id,
        run_dir=run_dir,
        health_status=status,
        health_mtime=_mtime(health_path),
        health_reason=_health_reason(health),
        claim_mtime=_mtime(claim_path),
        code_version=_code_version(claim),
        last_parquet_mtime=_newest_parquet_mtime(run_dir / DATA1A_RAW_DIR_NAME),
        live_published=parse_utc_timestamp(published),
        outage_reason=_outage_reason(live),
        tmux_alive=tmux_alive,
        process_alive=process_alive,
    )


def _age_seconds(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    return (now - moment.astimezone(UTC)).total_seconds()


def _within(moment: datetime | None, now: datetime, window_seconds: float) -> bool:
    age = _age_seconds(moment, now)
    return age is not None and 0 <= age <= window_seconds


def evaluate_capture_watch(
    snapshot: CaptureWatchSnapshot,
    *,
    now: datetime,
    stale_seconds: float = DEFAULT_STALE_SECONDS,
    live_stale_seconds: float = DEFAULT_LIVE_STALE_SECONDS,
    startup_grace_seconds: float = DEFAULT_STARTUP_GRACE_SECONDS,
    failed_lookback_seconds: float = DEFAULT_FAILED_LOOKBACK_SECONDS,
    explicit_run: bool = False,
) -> CaptureWatchFinding | None:
    """Decide whether this run needs an alert. Does not send one."""

    if snapshot.health_status in _TERMINAL_OK:
        return None
    recent = (
        _within(snapshot.claim_mtime, now, failed_lookback_seconds)
        or _within(snapshot.last_parquet_mtime, now, failed_lookback_seconds)
        or _within(snapshot.live_published, now, failed_lookback_seconds)
        or _within(snapshot.health_mtime, now, failed_lookback_seconds)
    )
    if not explicit_run and not recent:
        return None
    last_write = snapshot.last_parquet_mtime or snapshot.live_published
    if snapshot.health_status == "FAILED":
        return _finding(
            snapshot,
            state="FAILED",
            reason=snapshot.health_reason,
            last_write=last_write,
        )
    if snapshot.outage_reason is not None:
        return _finding(
            snapshot,
            state="FAILED",
            reason=snapshot.outage_reason,
            last_write=last_write,
        )
    process_dead = snapshot.process_alive is False
    tmux_dead = snapshot.tmux_alive is False and snapshot.process_alive is not True
    if process_dead or tmux_dead:
        reason = "process_absent" if process_dead else "tmux_session_absent"
        return _finding(snapshot, state="DEAD", reason=reason, last_write=last_write)
    claim_age = _age_seconds(snapshot.claim_mtime, now)
    if (
        snapshot.last_parquet_mtime is None
        and snapshot.live_published is None
        and claim_age is not None
        and claim_age <= startup_grace_seconds
    ):
        return None
    live_age = _age_seconds(snapshot.live_published, now)
    parquet_age = _age_seconds(snapshot.last_parquet_mtime, now)
    if live_age is not None and live_age > live_stale_seconds:
        return _finding(
            snapshot,
            state="STALE",
            reason="live_status_stale",
            last_write=snapshot.live_published,
        )
    if parquet_age is not None and parquet_age > stale_seconds:
        return _finding(
            snapshot,
            state="STALE",
            reason="parquet_stale",
            last_write=snapshot.last_parquet_mtime,
        )
    if snapshot.last_parquet_mtime is None and snapshot.live_published is None:
        return _finding(snapshot, state="STALE", reason="no_market_data", last_write=None)
    return None


def _finding(
    snapshot: CaptureWatchSnapshot,
    *,
    state: str,
    reason: str,
    last_write: datetime | None,
) -> CaptureWatchFinding:
    return CaptureWatchFinding(
        venue=snapshot.venue,
        run_id=snapshot.run_id,
        state=state,
        reason=reason,
        code_version=snapshot.code_version,
        last_write=last_write,
        run_dir=snapshot.run_dir,
    )


def iter_watch_run_dirs(artifact_root: Path, lane: WatchLane) -> Iterator[Path]:
    parent = artifact_root.joinpath(*lane.relative_prefix)
    try:
        if not parent.is_dir():
            return
        children = sorted(parent.iterdir(), key=lambda path: path.name)
    except OSError:
        return
    for run_dir in children:
        if not run_dir.is_dir() or run_dir.is_symlink():
            continue
        yield run_dir


def tmux_session_alive(session: str) -> bool | None:
    """``tmux has-session`` only. None when tmux is missing or the name is unsafe."""

    if _SESSION_NAME.fullmatch(session) is None:
        return None
    try:
        completed = subprocess.run(
            ["tmux", "has-session", "-t", session],
            check=False,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def process_alive_for_run(run_id: str, *, own_pid: int | None = None) -> bool | None:
    """True when another process command line contains this run and the package.

    None when ``/proc`` cannot be read. Never signals the other process.
    """

    proc = Path("/proc")
    if not proc.is_dir():
        return None
    skip = os.getpid() if own_pid is None else own_pid
    saw_cmdline = False
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        if int(entry.name) == skip:
            continue
        cmdline_path = entry / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            continue
        saw_cmdline = True
        text = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
        if run_id in text and "hyperliquid_bot" in text:
            return True
    if not saw_cmdline:
        return None
    return False
