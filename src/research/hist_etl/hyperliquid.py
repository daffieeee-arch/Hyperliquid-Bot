"""Hyperliquid perp funding history from the public info endpoint.

``fundingHistory`` takes a coin and inclusive ``startTime`` / ``endTime`` in
milliseconds and returns ``{coin, fundingRate, premium, time}`` rows, oldest
first, at most 500 per response. No key or account is involved.
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals

The ETL reads one UTC month at a time. A month that has ended settles into
an immutable raw JSON file once its fetch has a print in every settlement
slot (or only acknowledged holes), so a re-run reads the same bytes. Until
then, and for the current month, it is fetched again on every sync and
written as provisional output, so a truncated response is never frozen; a
real venue hole is acknowledged in the manifest (``known_holes``). One
Parquet file per month follows the ``.sources.json`` sidecar contract of the
other venues.

Settlement times jitter by about a second, and a late settlement can land
minutes into its slot, so completeness is judged per settlement slot (the
``funding_interval_hours`` interval a print belongs to), not by the spacing
of consecutive timestamps. A print belongs to the slot that starts at most
``SLOT_TOLERANCE_MS`` after it, so a print stamped just before the hour still
lands in that hour's slot; a month's window is defined in those slots.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

import duckdb

from research.hist_etl.checksums import cached_sha256, sha256_file
from research.hist_etl.errors import HistEtlError
from research.hist_etl.files import atomic_write_json, atomic_write_text, decide_output, warn
from research.hist_etl.http import RETRYABLE_STATUS, RateLimiter, Sleeper
from research.hist_etl.models import (
    HYPERLIQUID_INFO_URL,
    HYPERLIQUID_MAX_REQUESTS_PER_SECOND,
    USER_AGENT,
    Gap,
    HyperliquidFundingSpec,
    SourceDigest,
)

SLOT_TOLERANCE_MS = 60_000
# The published Parquet schema, in column order. A view with no current file
# keeps this schema and returns no rows (see catalog).
FUNDING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ts", "TIMESTAMP"),
    ("slot_start", "TIMESTAMP"),
    ("funding_time_ms", "BIGINT"),
    ("coin", "VARCHAR"),
    ("funding_rate", "DOUBLE"),
    ("funding_rate_text", "VARCHAR"),
    ("premium", "DOUBLE"),
    ("premium_text", "VARCHAR"),
    ("funding_interval_hours", "INTEGER"),
    ("dataset_id", "VARCHAR"),
    ("source_name", "VARCHAR"),
)
# The venue limit is a weight budget per minute: after a 429, wait one window.
RATE_LIMIT_WAIT_SECONDS = 60.0
_MS_PER_HOUR = 3_600_000
_SOURCE = "hyperliquid-info-fundingHistory"
_ROW_KEYS = ("coin", "fundingRate", "premium", "time")
_GAP_SAMPLES = 20


class RawWindowChanged(HistEtlError):
    """A raw file was written for another window: the dataset's range changed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class JsonPoster(Protocol):
    """POST a JSON body and return the status and the whole response body."""

    def post(self, url: str, payload: bytes) -> tuple[int, bytes]: ...


class UrllibJsonPoster:
    def __init__(self, timeout: float) -> None:
        self._timeout = timeout

    def post(self, url: str, payload: bytes) -> tuple[int, bytes]:
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return int(response.status), bytes(response.read())
        except urllib.error.HTTPError as exc:
            try:
                return int(exc.code), bytes(exc.read())
            finally:
                exc.close()


def build_poster(timeout: float) -> JsonPoster:
    return UrllibJsonPoster(timeout)


def hyperliquid_rate(requests_per_second: float) -> float:
    """The manifest rate, never above the venue weight budget.

    A manifest or environment rate of 0 means unlimited for the file servers;
    here it still gets the cap, so no setting can exceed the venue budget.
    """

    if requests_per_second <= 0:
        return HYPERLIQUID_MAX_REQUESTS_PER_SECOND
    return min(requests_per_second, HYPERLIQUID_MAX_REQUESTS_PER_SECOND)


@dataclass(frozen=True, slots=True)
class FundingWindow:
    """One month of one dataset: settlement slots ``[start_ms, end_ms)``.

    ``complete`` means the month (or the dataset's range inside it) ended
    before the first incomplete UTC day, so no later sync can add a slot.
    """

    dataset_id: str
    coin: str
    month: str
    start_ms: int
    end_ms: int
    complete: bool


@dataclass(frozen=True, slots=True)
class FundingRow:
    time_ms: int
    funding_rate_text: str
    premium_text: str


def funding_windows(spec: HyperliquidFundingSpec, today: date) -> tuple[FundingWindow, ...]:
    """Month windows over complete UTC days only, so a day is never half-read."""

    cutoff = _utc_midnight(today)
    range_start = _utc_midnight(spec.start)
    range_end = None if spec.end is None else _utc_midnight(spec.end + timedelta(days=1))
    windows: list[FundingWindow] = []
    month = date(spec.start.year, spec.start.month, 1)
    while True:
        month_start = _utc_midnight(month)
        if month_start >= cutoff or (range_end is not None and month_start >= range_end):
            break
        following = _next_month(month)
        target_end = _utc_midnight(following)
        if range_end is not None:
            target_end = min(target_end, range_end)
        start = max(month_start, range_start)
        end = min(target_end, cutoff)
        if start < end:
            windows.append(
                FundingWindow(
                    dataset_id=spec.id,
                    coin=spec.coin,
                    month=f"{month.year:04d}-{month.month:02d}",
                    start_ms=_ms(start),
                    end_ms=_ms(end),
                    complete=end == target_end,
                )
            )
        month = following
    return tuple(windows)


def raw_funding_path(root: Path, window: FundingWindow, *, settled: bool) -> Path:
    """``.json`` is a settled, immutable month; ``.open.json`` is rewritten each sync."""

    suffix = ".json" if settled else ".open.json"
    name = f"{window.coin}-funding-{window.month}{suffix}"
    return root / "hyperliquid-api" / "funding" / window.dataset_id / name


def hyperliquid_parquet_path(root: Path, window: FundingWindow) -> Path:
    name = f"{window.month}.{window.dataset_id}.parquet"
    return root / "parquet" / "hist_etl" / "hyperliquid" / "funding" / window.coin / name


def slot_of(time_ms: int, interval_ms: int) -> int:
    """The settlement slot a print belongs to, tolerating a print just before it."""

    return (time_ms + SLOT_TOLERANCE_MS) // interval_ms


def post_with_retries(
    poster: JsonPoster,
    url: str,
    payload: bytes,
    *,
    limiter: RateLimiter,
    max_retries: int,
    sleeper: Sleeper,
) -> bytes:
    delay = 0.5
    status = 0
    for attempt in range(1, max_retries + 1):
        limiter.wait()
        try:
            status, body = poster.post(url, payload)
        except OSError as exc:
            if attempt >= max_retries:
                raise HistEtlError(f"request failed for {url}: {exc}") from exc
            sleeper(delay)
            delay *= 2
            continue
        if status in RETRYABLE_STATUS and attempt < max_retries:
            # A 429 means the minute's weight budget is spent (perhaps by
            # another process on this IP): short backoffs would all land
            # inside the same window.
            sleeper(max(delay, RATE_LIMIT_WAIT_SECONDS) if status == 429 else delay)
            delay *= 2
            continue
        if status != 200:
            break
        return body
    raise HistEtlError(f"POST {url} returned HTTP {status}")


def fetch_funding(
    poster: JsonPoster,
    window: FundingWindow,
    *,
    limiter: RateLimiter,
    max_retries: int,
    sleeper: Sleeper,
    url: str = HYPERLIQUID_INFO_URL,
) -> tuple[FundingRow, ...]:
    """Every print whose slot is in the window, oldest first.

    Pages advance from the last returned time, so the loop does not depend on
    the 500-row page size. A page outside the window, out of order, or that
    does not advance fails closed.
    """

    first, last_inclusive = _print_bounds(window)
    rows: list[FundingRow] = []
    cursor = first
    while cursor <= last_inclusive:
        payload = json.dumps(
            {
                "type": "fundingHistory",
                "coin": window.coin,
                "startTime": cursor,
                "endTime": last_inclusive,
            },
            separators=(",", ":"),
        ).encode("ascii")
        body = post_with_retries(
            poster, url, payload, limiter=limiter, max_retries=max_retries, sleeper=sleeper
        )
        page = _decode_page(body, window)
        if not page:
            break
        times = [row.time_ms for row in page]
        if times != sorted(times) or times[0] < cursor:
            raise HistEtlError(
                f"fundingHistory page for {window.coin} {window.month} is out of order",
                exit_code=2,
            )
        rows.extend(page)
        cursor = times[-1] + 1
    return tuple(rows)


def write_raw(path: Path, text: str) -> None:
    atomic_write_text(path, text)


def render_raw(window: FundingWindow, rows: Sequence[FundingRow]) -> str:
    """Canonical JSON, so two fetches of the same month give the same bytes."""

    payload = {
        "source": _SOURCE,
        "coin": window.coin,
        "start_ms": window.start_ms,
        "end_ms": window.end_ms,
        "rows": [
            {
                "coin": window.coin,
                "fundingRate": row.funding_rate_text,
                "premium": row.premium_text,
                "time": row.time_ms,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def raw_status(raw_path: Path, window: FundingWindow, *, settled: bool) -> str:
    """``match``, ``window_changed`` or ``invalid``, read by ``load_raw`` itself."""

    try:
        load_raw(raw_path, window, settled=settled)
    except RawWindowChanged:
        return "window_changed"
    except HistEtlError:
        return "invalid"
    return "match"


def ready_to_settle(
    spec: HyperliquidFundingSpec, window: FundingWindow, rows: Sequence[FundingRow]
) -> bool:
    """A month is frozen only when it has ended and every slot is accounted for.

    Anything else stays provisional and is fetched again on the next sync, so
    a truncated answer is never kept as an immutable file. A real venue hole
    costs a request or two per sync until it is listed in ``known_holes``.
    """

    return (
        window.complete and not slot_conflicts(spec, rows) and not funding_holes(spec, window, rows)
    )


def materialize_funding_month(
    spec: HyperliquidFundingSpec,
    window: FundingWindow,
    raw_path: Path,
    *,
    root: Path,
    rebuild: bool,
    provisional: bool,
) -> tuple[Gap, ...]:
    """Write one month of Parquet from its raw file, or report why not.

    A month whose prints conflict is not written. Missing settlement slots are
    reported but do not block the write, like kline holes on other venues.
    """

    try:
        rows, covered = load_raw(raw_path, window, settled=not provisional)
    except RawWindowChanged as exc:
        return (Gap("hyperliquid_window_changed", spec.id, str(exc)),)
    except HistEtlError as exc:
        return (Gap("hyperliquid_schema", spec.id, str(exc)),)
    conflicts = slot_conflicts(spec, rows)
    if conflicts:
        return conflicts
    holes = funding_holes(spec, _hole_window(window, covered), rows)
    source = SourceDigest(name=raw_path.name, sha256=cached_sha256(root, raw_path), path=raw_path)
    destination = hyperliquid_parquet_path(root, window)
    action = _decide(destination, source, covered, rebuild=rebuild)
    if action == "audit":
        return holes
    if action != "write":
        reason = (
            "no .sources.json sidecar"
            if action == "untracked"
            else "sources differ from .sources.json"
        )
        detail = f"refusing to overwrite {destination.name}: {reason}; pass --rebuild to replace it"
        warn(detail)
        return (Gap("refused_overwrite", spec.id, detail), *holes)
    _write_parquet(destination, spec, covered, rows, source, provisional=provisional)
    return holes


def audit_funding_month(
    spec: HyperliquidFundingSpec, window: FundingWindow, *, root: Path
) -> tuple[Gap, ...]:
    """Re-check a month on disk against its raw file. Does not fetch or rewrite.

    The settled raw file is checked when the month has one; otherwise the
    provisional one, over the slots it covered when it was fetched.
    """

    settled = raw_funding_path(root, window, settled=True)
    use_settled = window.complete and settled.is_file()
    raw_path = settled if use_settled else raw_funding_path(root, window, settled=False)
    if not raw_path.is_file():
        return (Gap("missing_hyperliquid_raw", spec.id, raw_path.name),)
    destination = hyperliquid_parquet_path(root, window)
    if not destination.is_file():
        return (Gap("missing_parquet", spec.id, destination.name),)
    try:
        rows, covered = load_raw(raw_path, window, settled=use_settled)
    except RawWindowChanged as exc:
        return (Gap("hyperliquid_window_changed", spec.id, str(exc)),)
    except HistEtlError as exc:
        return (Gap("hyperliquid_schema", spec.id, str(exc)),)
    # A fresh hash, not the size/mtime cache: verify must see any edit.
    source = SourceDigest(name=raw_path.name, sha256=sha256_file(raw_path), path=raw_path)
    if _decide(destination, source, covered, rebuild=False) != "audit":
        return (
            Gap(
                "hyperliquid_sidecar",
                spec.id,
                f"{destination.name} was not built from {raw_path.name} for the window "
                "it covers; run sync to rewrite it",
            ),
        )
    return (
        *slot_conflicts(spec, rows),
        *funding_holes(spec, _hole_window(window, covered), rows),
    )


def load_raw(
    raw_path: Path, window: FundingWindow, *, settled: bool
) -> tuple[tuple[FundingRow, ...], FundingWindow]:
    """Typed, deduplicated rows and the window the file covers.

    A settled file must cover exactly this window. A provisional file covers
    the slots up to the day it was fetched, which may end before today's
    window does. Disagreeing duplicates fail closed.
    """

    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistEtlError(f"{raw_path.name} is not readable JSON: {exc}", exit_code=2) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("source") != _SOURCE
        or payload.get("coin") != window.coin
        or type(payload.get("start_ms")) is not int
        or type(payload.get("end_ms")) is not int
        or not isinstance(payload.get("rows"), list)
    ):
        raise HistEtlError(f"{raw_path.name} is not a fundingHistory month file", exit_code=2)
    start_ms = payload["start_ms"]
    end_ms = payload["end_ms"]
    fits = end_ms == window.end_ms if settled else window.start_ms < end_ms <= window.end_ms
    if start_ms != window.start_ms or not fits:
        raise RawWindowChanged(
            f"{raw_path.name} covers another window than the manifest now asks for; "
            "move it aside to refetch the month"
        )
    covered = replace(window, end_ms=end_ms)
    by_time: dict[int, FundingRow] = {}
    for item in payload["rows"]:
        row = _typed_row(item, covered, raw_path.name)
        known = by_time.get(row.time_ms)
        if known is not None and known != row:
            raise HistEtlError(
                f"{raw_path.name} has two different prints at {row.time_ms}", exit_code=2
            )
        by_time[row.time_ms] = row
    return tuple(by_time[key] for key in sorted(by_time)), covered


def slot_conflicts(spec: HyperliquidFundingSpec, rows: Sequence[FundingRow]) -> tuple[Gap, ...]:
    """Two settlements in one slot would double a bar's funding: refuse the month."""

    interval = spec.funding_interval_hours * _MS_PER_HOUR
    seen: set[int] = set()
    doubled: list[str] = []
    for row in rows:
        slot = slot_of(row.time_ms, interval)
        if slot in seen:
            doubled.append(_iso(slot * interval))
        seen.add(slot)
    if not doubled:
        return ()
    return (
        Gap(
            "funding_conflict",
            spec.id,
            f"{len(doubled)} settlement slots hold more than one print",
            tuple(doubled[:_GAP_SAMPLES]),
        ),
    )


def funding_holes(
    spec: HyperliquidFundingSpec, window: FundingWindow, rows: Sequence[FundingRow]
) -> tuple[Gap, ...]:
    """Settlement slots inside the window with no print and no acknowledgement."""

    interval = spec.funding_interval_hours * _MS_PER_HOUR
    present = {slot_of(row.time_ms, interval) for row in rows}
    acknowledged = {slot_of(_ms(moment), interval) for moment in spec.known_holes}
    first = -(-window.start_ms // interval)
    last = (window.end_ms - 1) // interval
    missing = [
        slot for slot in range(first, last + 1) if slot not in present and slot not in acknowledged
    ]
    if not missing:
        return ()
    return (
        Gap(
            "funding_hole",
            spec.id,
            f"{len(missing)} settlement slots in {window.month} have no print",
            tuple(_iso(slot * interval) for slot in missing[:_GAP_SAMPLES]),
        ),
    )


def _hole_window(window: FundingWindow, covered: FundingWindow) -> FundingWindow:
    """An ended month is judged over all its slots, whatever its file covered.

    The current month is judged only up to the day it was last fetched.
    """

    return window if window.complete else covered


def _print_bounds(window: FundingWindow) -> tuple[int, int]:
    """Inclusive print times whose slot lies in the window."""

    return window.start_ms - SLOT_TOLERANCE_MS, window.end_ms - SLOT_TOLERANCE_MS - 1


def _decide(
    destination: Path, source: SourceDigest, covered: FundingWindow, *, rebuild: bool
) -> str:
    """A provisional file is always replaced; others follow the sidecar rule.

    A file built from this same raw file is rewritten when its sidecar does not
    record the window it covers: the view selects month files by that window.
    """

    if destination.is_file() and _is_provisional(destination):
        recorded = decide_output(destination, (source,), rebuild=False)
        action = "audit" if recorded == "audit" else "write"
    else:
        action = decide_output(destination, (source,), rebuild=rebuild)
    if action == "audit" and _recorded_window(destination) != (covered.start_ms, covered.end_ms):
        return "write"
    return action


def _is_provisional(destination: Path) -> bool:
    payload = _sidecar_payload(destination)
    return payload is not None and payload.get("provisional") is True


def _recorded_window(destination: Path) -> tuple[int, int] | None:
    """The ``start_ms`` / ``end_ms`` a month file's sidecar says it covers."""

    payload = _sidecar_payload(destination)
    if payload is None:
        return None
    start_ms = payload.get("start_ms")
    end_ms = payload.get("end_ms")
    if type(start_ms) is not int or type(end_ms) is not int:
        return None
    return start_ms, end_ms


def _sidecar_payload(destination: Path) -> dict[str, object] | None:
    sidecar = destination.with_name(destination.name + ".sources.json")
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_parquet(
    destination: Path,
    spec: HyperliquidFundingSpec,
    covered: FundingWindow,
    rows: Sequence[FundingRow],
    source: SourceDigest,
    *,
    provisional: bool,
) -> None:
    interval = spec.funding_interval_hours * _MS_PER_HOUR
    records = [
        (
            row.time_ms,
            slot_of(row.time_ms, interval) * interval,
            spec.coin,
            float(Decimal(row.funding_rate_text)),
            row.funding_rate_text,
            float(Decimal(row.premium_text)),
            row.premium_text,
            spec.funding_interval_hours,
            spec.id,
            source.name,
        )
        for row in rows
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone='UTC'")
        connection.execute(
            """
            CREATE TABLE staged (
                time_ms BIGINT,
                slot_ms BIGINT,
                coin VARCHAR,
                funding_rate DOUBLE,
                funding_rate_text VARCHAR,
                premium DOUBLE,
                premium_text VARCHAR,
                funding_interval_hours INTEGER,
                dataset_id VARCHAR,
                source_name VARCHAR
            )
            """
        )
        if records:
            connection.executemany(
                "INSERT INTO staged VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", records
            )
        connection.execute(
            """
            CREATE TABLE published AS
            SELECT
                CAST(make_timestamp(time_ms * 1000) AS TIMESTAMP) AS ts,
                CAST(make_timestamp(slot_ms * 1000) AS TIMESTAMP) AS slot_start,
                time_ms AS funding_time_ms,
                coin,
                funding_rate,
                funding_rate_text,
                premium,
                premium_text,
                funding_interval_hours,
                dataset_id,
                source_name
            FROM staged
            ORDER BY time_ms
            """
        )
        connection.execute("COPY published TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(partial)])
    finally:
        connection.close()
    os.replace(partial, destination)
    sidecar = destination.with_name(destination.name + ".sources.json")
    atomic_write_json(
        sidecar,
        {
            "sources": [{"name": source.name, "sha256": source.sha256}],
            "provisional": provisional,
            "funding_interval_hours": spec.funding_interval_hours,
            "start_ms": covered.start_ms,
            "end_ms": covered.end_ms,
        },
    )


def funding_view_files(
    root: Path, specs: Sequence[HyperliquidFundingSpec], today: date
) -> dict[str, tuple[Path, ...]]:
    """Per coin, the month files the manifest selects on ``today``, in month order.

    A file is in the view only when its sidecar covers the month the manifest
    asks for: a settled month exactly, a provisional one up to the day it was
    fetched. Files of a renamed, removed, or re-ranged dataset stay on disk but
    out of the view, so no bar is charged twice.
    """

    by_coin: dict[str, list[tuple[str, Path]]] = {spec.coin: [] for spec in specs}
    for spec in specs:
        for window in funding_windows(spec, today):
            path = hyperliquid_parquet_path(root, window)
            if _sidecar_covers(path, window):
                by_coin[spec.coin].append((window.month, path))
    return {coin: tuple(path for _month, path in sorted(items)) for coin, items in by_coin.items()}


def _sidecar_covers(path: Path, window: FundingWindow) -> bool:
    recorded = _recorded_window(path) if path.is_file() else None
    if recorded is None or recorded[0] != window.start_ms:
        return False
    end_ms = recorded[1]
    if _is_provisional(path):
        return window.start_ms < end_ms <= window.end_ms
    return end_ms == window.end_ms


def _decode_page(body: bytes, window: FundingWindow) -> list[FundingRow]:
    try:
        page = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistEtlError(
            f"fundingHistory for {window.coin} returned invalid JSON", exit_code=2
        ) from exc
    if not isinstance(page, list):
        raise HistEtlError(f"fundingHistory for {window.coin} did not return a list", exit_code=2)
    return [_typed_row(item, window, "fundingHistory response") for item in page]


def _typed_row(item: object, window: FundingWindow, origin: str) -> FundingRow:
    if not isinstance(item, dict) or any(key not in item for key in _ROW_KEYS):
        raise HistEtlError(f"{origin} has a row without {', '.join(_ROW_KEYS)}", exit_code=2)
    if item["coin"] != window.coin:
        raise HistEtlError(f"{origin} has a row for coin {item['coin']!r}", exit_code=2)
    time_ms = item["time"]
    first, last_inclusive = _print_bounds(window)
    if type(time_ms) is not int or not first <= time_ms <= last_inclusive:
        raise HistEtlError(f"{origin} has a row outside {window.month}", exit_code=2)
    return FundingRow(
        time_ms=time_ms,
        funding_rate_text=_decimal_text(item["fundingRate"], origin),
        premium_text=_decimal_text(item["premium"], origin),
    )


def _decimal_text(value: object, origin: str) -> str:
    """Keep the published text; refuse anything that is not a finite decimal."""

    if not isinstance(value, str):
        raise HistEtlError(f"{origin} has a non-text rate", exit_code=2)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise HistEtlError(f"{origin} has an unparseable rate {value!r}", exit_code=2) from exc
    if not parsed.is_finite() or not math.isfinite(float(parsed)):
        raise HistEtlError(f"{origin} has a non-finite rate {value!r}", exit_code=2)
    return value


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _next_month(month: date) -> date:
    if month.month == 12:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
