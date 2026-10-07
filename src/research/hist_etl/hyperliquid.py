"""Hyperliquid perp funding history from the public info endpoint.

``fundingHistory`` takes a coin and inclusive ``startTime`` / ``endTime`` in
milliseconds and returns ``{coin, fundingRate, premium, time}`` rows, oldest
first, at most 500 per response. No key or account is involved.
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals

The ETL pages through one UTC month at a time and keeps each completed month
as an immutable raw JSON file, so a re-run reads the same bytes. The current
month is fetched again on every sync and written as provisional output. One
Parquet file per month follows the ``.sources.json`` sidecar contract of the
other venues.

Settlement times jitter by about a second, and a late settlement can land
minutes into its slot, so completeness is judged per settlement slot (the
``funding_interval_hours`` interval a print falls in), not by the spacing of
consecutive timestamps.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
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

_MS_PER_HOUR = 3_600_000
_SOURCE = "hyperliquid-info-fundingHistory"
_ROW_KEYS = ("coin", "fundingRate", "premium", "time")
_GAP_SAMPLES = 20


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
    """The manifest rate, capped by the venue weight budget. 0 keeps unlimited."""

    if requests_per_second <= 0:
        return requests_per_second
    return min(requests_per_second, HYPERLIQUID_MAX_REQUESTS_PER_SECOND)


@dataclass(frozen=True, slots=True)
class FundingWindow:
    """One month of one dataset: ``[start_ms, end_ms)``.

    ``complete`` means no later sync can add rows to this window: the month
    (or the dataset's range inside it) ended before the first incomplete UTC day.
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


def raw_funding_path(root: Path, window: FundingWindow) -> Path:
    """Immutable for a complete window; ``.open.json`` is rewritten while it is open."""

    suffix = ".json" if window.complete else ".open.json"
    name = f"{window.coin}-funding-{window.month}{suffix}"
    return root / "hyperliquid-api" / "funding" / window.dataset_id / name


def hyperliquid_parquet_path(root: Path, window: FundingWindow) -> Path:
    name = f"{window.month}.{window.dataset_id}.parquet"
    return root / "parquet" / "hist_etl" / "hyperliquid" / "funding" / window.coin / name


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
            sleeper(delay)
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
) -> tuple[dict[str, object], ...]:
    """Every row with ``start_ms <= time < end_ms``, oldest first.

    Pages advance from the last returned time, so the loop does not depend on
    the 500-row page size. A page outside the window, out of order, or that
    does not advance fails closed.
    """

    rows: list[dict[str, object]] = []
    cursor = window.start_ms
    last_inclusive = window.end_ms - 1
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
        times = [_row_time(row) for row in page]
        if times != sorted(times) or times[0] < cursor or times[-1] > last_inclusive:
            raise HistEtlError(
                f"fundingHistory page for {window.coin} {window.month} is out of order "
                "or outside the requested window",
                exit_code=2,
            )
        rows.extend(page)
        cursor = times[-1] + 1
    return tuple(rows)


def write_raw(path: Path, window: FundingWindow, rows: Sequence[dict[str, object]]) -> None:
    """Canonical JSON, so two fetches of a settled month give the same bytes."""

    payload = {
        "source": _SOURCE,
        "coin": window.coin,
        "start_ms": window.start_ms,
        "end_ms": window.end_ms,
        "rows": list(rows),
    }
    atomic_write_text(path, json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def materialize_funding_month(
    spec: HyperliquidFundingSpec,
    window: FundingWindow,
    raw_path: Path,
    *,
    root: Path,
    rebuild: bool,
) -> tuple[Gap, ...]:
    """Write one month of Parquet from its raw file, or report why not.

    A month whose prints conflict is not written. Missing settlement slots are
    reported but do not block the write, like kline holes on other venues.
    """

    try:
        rows = load_raw(raw_path, window)
    except HistEtlError as exc:
        return (Gap("hyperliquid_schema", spec.id, str(exc)),)
    conflicts = slot_conflicts(spec, rows)
    if conflicts:
        return conflicts
    holes = funding_holes(spec, window, rows)
    source = SourceDigest(name=raw_path.name, sha256=cached_sha256(root, raw_path), path=raw_path)
    destination = hyperliquid_parquet_path(root, window)
    action = _decide(destination, source, rebuild=rebuild)
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
    _write_parquet(destination, spec, window, rows, source)
    return holes


def audit_funding_month(
    spec: HyperliquidFundingSpec, window: FundingWindow, *, root: Path
) -> tuple[Gap, ...]:
    """Re-check a month on disk against its raw file. Does not fetch or rewrite."""

    raw_path = raw_funding_path(root, window)
    if not raw_path.is_file():
        return (Gap("missing_hyperliquid_raw", spec.id, raw_path.name),)
    destination = hyperliquid_parquet_path(root, window)
    if not destination.is_file():
        return (Gap("missing_parquet", spec.id, destination.name),)
    try:
        rows = load_raw(raw_path, window)
    except HistEtlError as exc:
        return (Gap("hyperliquid_schema", spec.id, str(exc)),)
    # A fresh hash, not the size/mtime cache: verify must see any edit.
    source = SourceDigest(name=raw_path.name, sha256=sha256_file(raw_path), path=raw_path)
    if _decide(destination, source, rebuild=False) != "audit":
        return (
            Gap(
                "hyperliquid_sidecar",
                spec.id,
                f"{destination.name} was not built from {raw_path.name}",
            ),
        )
    return (*slot_conflicts(spec, rows), *funding_holes(spec, window, rows))


def load_raw(raw_path: Path, window: FundingWindow) -> tuple[FundingRow, ...]:
    """Typed, deduplicated rows. Disagreeing duplicates of one time fail closed."""

    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistEtlError(f"{raw_path.name} is not readable JSON: {exc}", exit_code=2) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("source") != _SOURCE
        or payload.get("coin") != window.coin
        or payload.get("start_ms") != window.start_ms
        or payload.get("end_ms") != window.end_ms
        or not isinstance(payload.get("rows"), list)
    ):
        raise HistEtlError(f"{raw_path.name} does not describe this window", exit_code=2)
    by_time: dict[int, FundingRow] = {}
    for item in payload["rows"]:
        row = _typed_row(item, window, raw_path.name)
        known = by_time.get(row.time_ms)
        if known is not None and known != row:
            raise HistEtlError(
                f"{raw_path.name} has two different prints at {row.time_ms}", exit_code=2
            )
        by_time[row.time_ms] = row
    return tuple(by_time[key] for key in sorted(by_time))


def slot_conflicts(spec: HyperliquidFundingSpec, rows: Sequence[FundingRow]) -> tuple[Gap, ...]:
    """Two settlements in one slot would double a bar's funding: refuse the month."""

    interval = spec.funding_interval_hours * _MS_PER_HOUR
    seen: dict[int, int] = {}
    doubled: list[str] = []
    for row in rows:
        slot = row.time_ms // interval
        if slot in seen:
            doubled.append(_iso(slot * interval))
        seen[slot] = row.time_ms
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
    present = {row.time_ms // interval for row in rows}
    acknowledged = {_ms(moment) // interval for moment in spec.known_holes}
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


def _decide(destination: Path, source: SourceDigest, *, rebuild: bool) -> str:
    """A provisional (open-month) file is always replaced; others follow the sidecar rule."""

    if destination.is_file() and _is_provisional(destination):
        recorded = decide_output(destination, (source,), rebuild=False)
        return "audit" if recorded == "audit" else "write"
    return decide_output(destination, (source,), rebuild=rebuild)


def _is_provisional(destination: Path) -> bool:
    sidecar = destination.with_name(destination.name + ".sources.json")
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("provisional") is True


def _write_parquet(
    destination: Path,
    spec: HyperliquidFundingSpec,
    window: FundingWindow,
    rows: Sequence[FundingRow],
    source: SourceDigest,
) -> None:
    interval = spec.funding_interval_hours * _MS_PER_HOUR
    records = [
        (
            row.time_ms,
            (row.time_ms // interval) * interval,
            window.coin,
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
            "provisional": not window.complete,
            "funding_interval_hours": spec.funding_interval_hours,
        },
    )


def _decode_page(body: bytes, window: FundingWindow) -> list[dict[str, object]]:
    try:
        page = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistEtlError(
            f"fundingHistory for {window.coin} returned invalid JSON", exit_code=2
        ) from exc
    if not isinstance(page, list):
        raise HistEtlError(f"fundingHistory for {window.coin} did not return a list", exit_code=2)
    rows: list[dict[str, object]] = []
    for item in page:
        row = _typed_row(item, window, "fundingHistory response")
        rows.append(
            {
                "coin": window.coin,
                "fundingRate": row.funding_rate_text,
                "premium": row.premium_text,
                "time": row.time_ms,
            }
        )
    return rows


def _typed_row(item: object, window: FundingWindow, origin: str) -> FundingRow:
    if not isinstance(item, dict) or any(key not in item for key in _ROW_KEYS):
        raise HistEtlError(f"{origin} has a row without {', '.join(_ROW_KEYS)}", exit_code=2)
    if item["coin"] != window.coin:
        raise HistEtlError(f"{origin} has a row for coin {item['coin']!r}", exit_code=2)
    time_ms = item["time"]
    if type(time_ms) is not int or not window.start_ms <= time_ms < window.end_ms:
        raise HistEtlError(f"{origin} has a row outside {window.month}", exit_code=2)
    return FundingRow(
        time_ms=time_ms,
        funding_rate_text=_decimal_text(item["fundingRate"], origin),
        premium_text=_decimal_text(item["premium"], origin),
    )


def _row_time(row: dict[str, object]) -> int:
    value = row["time"]
    if type(value) is not int:
        raise HistEtlError("fundingHistory row time is not an integer", exit_code=2)
    return value


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
