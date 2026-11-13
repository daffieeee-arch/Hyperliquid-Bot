"""Pinned recent Binance inputs for one locked candidate diagnostic.

September/October funding has native CSV witnesses. November deliberately uses
an independently complete authoritative REST census, never a waiver of the old
study's different all-period CSV contract. No network or financial scoring.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from research.btc_flow_io import read_bytes
from research.regime_io import Segment, Sources, midnight
from research.repaired_accounting import Frame
from research.two_models_io import BTC, DAY, HOUR, MINUTE
from research.volume_repair_io import (
    KLINE_HEADER,
    Source,
    archive_rows,
    decimal,
    finite_float,
    integer,
    json_value,
    require,
)

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/experiments/exp_regime_recent_diagnostic_20261113.md"
STUDY_ID = "btc-locked-regime-recent-diagnostic-20261113-v1"
INPUT_SHA256 = "470a23eebf9555f93670ae73aded702131c89cceb37b30bc05c6db4ab7cf32d3"
CANDIDATE_LOCK_SHA256 = "9d3f752b7124a50497d6f2954409b802fdb08b32f997f68cb1884d571c71d9e9"
START = midnight("2026-09-01")
END = midnight("2026-11-13")
REST_ONLY_START = midnight("2026-11-01")
WARMUP_PERIOD = "2026-08"
WARMUP_SHA256 = "b594301740cbb39818ba60f2835e4636aa06ad53308e25b1b312f1443fe0ccfe"
PERIODS = ("2026-09", "2026-10", *(f"2026-11-{day:02d}" for day in range(1, 13)))
ARCHIVE_FUNDING_PERIODS = ("2026-09", "2026-10")
ROLES = ("hourly", "trade_minute", "mark_minute", "funding")


@dataclass(frozen=True, slots=True)
class NativeCandle:
    close: Decimal
    low: Decimal
    high: Decimal


def period_clock(period: str) -> tuple[int, int]:
    require(type(period) is str and len(period) in (7, 10), "Registered source period.")
    if len(period) == 10:
        first = midnight(period)
        return first, first + DAY
    year, month = map(int, period.split("-"))
    first = int(dt.datetime(year, month, 1, tzinfo=dt.UTC).timestamp()) * 1000
    return first, first + calendar.monthrange(year, month)[1] * DAY


def _source(row: dict[str, Any], *, checksum: bool = False) -> Source:
    prefix = "checksum_" if checksum else ""
    return Source(
        BTC,
        row["role"] + ("_checksum" if checksum else ""),
        Path(row[prefix + "path"]),
        row[prefix + "sha256"],
        row[prefix + "bytes"],
    )


def _http(row: dict[str, Any], url: str, status: int) -> bytes:
    require(
        row["url"] == url
        and row["method"] == "GET"
        and type(row["status"]) is int
        and row["status"] == status
        and row["truncated"] is False,
        "Original bounded source HTTP identity/status.",
    )
    require(type(row["bytes"]) is int and 0 < row["bytes"] <= 4_000_000, "HTTP byte bound.")
    raw = read_bytes(Path(row["path"]), 4_000_000)
    require(
        len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"],
        "Original HTTP response bytes.",
    )
    return raw


def _official_url(role: str, period: str) -> str:
    cadence = "monthly" if len(period) == 7 else "daily"
    prefix = f"https://data.binance.vision/data/futures/um/{cadence}"
    if role == "funding":
        return f"{prefix}/fundingRate/{BTC}/{BTC}-fundingRate-{period}.zip"
    product = "markPriceKlines" if role == "mark_minute" else "klines"
    interval = "1h" if role == "hourly" else "1m"
    return f"{prefix}/{product}/{BTC}/{interval}/{BTC}-{interval}-{period}.zip"


def _candle_rows(row: dict[str, Any], start: int, end: int, step: int) -> dict[int, NativeCandle]:
    values: dict[int, NativeCandle] = {}
    for index, raw in enumerate(archive_rows(_source(row), _source(row, checksum=True), 50_000)):
        if index == 0 and raw and raw[0] == "open_time":
            require(
                len(raw) == 12
                and all(value in allowed for value, allowed in zip(raw, KLINE_HEADER, strict=True)),
                "Native candle header field identity.",
            )
            continue
        require(len(raw) == 12, "Native twelve-column candle.")
        instant = integer(raw[0])
        require(
            instant == start + len(values) * step
            and instant < end
            and integer(raw[6]) == instant + step - 1,
            "Complete physical millisecond candle grid.",
        )
        opening, high, low, close = [decimal(value) for value in raw[1:5]]
        require(
            0 < low <= min(opening, close) <= max(opening, close) <= high,
            "Finite positive ordered native candle OHLC.",
        )
        values[instant + step] = NativeCandle(close, low, high)
    require(len(values) == (end - start) // step, "Complete native candle count.")
    return values


def _native_funding(row: dict[str, Any], start: int, end: int) -> dict[int, Decimal]:
    values: dict[int, Decimal] = {}
    for index, raw in enumerate(archive_rows(_source(row), _source(row, checksum=True), 200)):
        if index == 0:
            require(
                raw == ["calc_time", "funding_interval_hours", "last_funding_rate"],
                "Native funding header.",
            )
            continue
        require(len(raw) == 3, "Native funding columns.")
        instant, interval = integer(raw[0]), integer(raw[1])
        rate = decimal(raw[2])
        require(
            start <= instant < end
            and interval == 8
            and (not values or instant > next(reversed(values))),
            "Ordered native funding time/interval.",
        )
        values[instant] = rate
    expected = set(range(start // (8 * HOUR), end // (8 * HOUR)))
    require(
        len(values) == len(expected)
        and {(t + 4 * HOUR) // (8 * HOUR) for t in values} == expected
        and all(abs(t - ((t + 4 * HOUR) // (8 * HOUR)) * 8 * HOUR) < MINUTE for t in values),
        "Complete native archive funding slots.",
    )
    return values


def _archives(
    body: dict[str, Any],
) -> tuple[
    dict[int, NativeCandle],
    dict[int, NativeCandle],
    dict[int, NativeCandle],
    dict[int, Decimal],
    list[dict[str, Any]],
]:
    warmup = body["warmup_source"]
    require(
        warmup["role"] == "hourly"
        and warmup["month"] == WARMUP_PERIOD
        and warmup["symbol"] == BTC
        and warmup["sha256"] == WARMUP_SHA256,
        "Previously pinned August hourly warm-up identity.",
    )
    start, end = period_clock(WARMUP_PERIOD)
    require(end == START, "Warm-up terminates at the diagnostic start.")
    hourly = _candle_rows(warmup, start, end, HOUR)
    trades: dict[int, NativeCandle] = {}
    marks: dict[int, NativeCandle] = {}
    native: dict[int, Decimal] = {}
    identities: list[dict[str, Any]] = [
        _source(warmup).record()
        | {
            "period": WARMUP_PERIOD,
            "use": "warmup_only",
            "checksum_path": warmup["checksum_path"],
            "checksum_bytes": warmup["checksum_bytes"],
            "checksum_sha256": warmup["checksum_sha256"],
        }
    ]
    rows = body["sources"]
    expected = {(period, role) for period in PERIODS for role in ROLES}
    actual = [(row["period"], row["role"]) for row in rows]
    require(len(actual) == len(expected) and set(actual) == expected, "Exact source-period matrix.")
    for row in rows:
        period, role = row["period"], row["role"]
        first, last = period_clock(period)
        require(
            row["start_ms"] == first and row["end_ms"] == last and START <= first < last <= END,
            "Registered source period bounds.",
        )
        url = _official_url(role, period)
        require(
            row["official_url"] == url and row["official_checksum_url"] == url + ".CHECKSUM",
            "Native instrument/product URL identity.",
        )
        absent = role == "funding" and period not in ARCHIVE_FUNDING_PERIODS
        _http(row["checksum_request"], url + ".CHECKSUM", 404 if absent else 200)
        if absent:
            require(
                row["status"] == "unavailable_official_archive_or_checksum",
                "Missing daily funding disposition remains explicit.",
            )
            _http(row["zip_probe"], url, 404)
            continue
        require(row["status"] == "qualified", "Only complete qualified archives are admitted.")
        require(
            all(
                row["checksum_" + field] == row["checksum_request"][field]
                for field in ("path", "bytes", "sha256")
            ),
            "Current official sidecar reference binding.",
        )
        if row["existing_nas_file_reused"] is not True:
            _http(row["download_request"], url, 200)
            require(
                all(
                    row[field] == row["download_request"][field]
                    for field in ("path", "bytes", "sha256")
                ),
                "Downloaded native archive binding.",
            )
        if role == "funding":
            values = _native_funding(row, first, last)
            require(not (set(native) & set(values)), "Unique native funding archive events.")
            native.update(values)
        else:
            step = HOUR if role == "hourly" else MINUTE
            candles = _candle_rows(row, first, last, step)
            destination = (
                hourly if role == "hourly" else trades if role == "trade_minute" else marks
            )
            require(not (set(destination) & set(candles)), "Unique complete candle partitions.")
            destination.update(candles)
        identities.append(
            _source(row).record()
            | {
                "period": period,
                "checksum_path": row["checksum_path"],
                "checksum_bytes": row["checksum_bytes"],
                "checksum_sha256": row["checksum_sha256"],
            }
        )
    minute_clock = set(range(START + MINUTE, END + 1, MINUTE))
    require(set(trades) == set(marks) == minute_clock, "Complete native minute trade/mark clocks.")
    require(
        all(t in hourly for t in range(START - 720 * HOUR, END + 1, HOUR)),
        "Complete 721-close history and target hourly clock.",
    )
    require(
        all(hourly[t].close == trades[t].close for t in range(START + HOUR, END + 1, HOUR)),
        "Native hourly versus minute trade closes agree exactly.",
    )
    return hourly, trades, marks, native, identities


def _funding(
    body: dict[str, Any], native: dict[int, Decimal]
) -> tuple[dict[int, tuple[float, float]], dict[str, Any]]:
    claim = body["funding"]
    request = claim["request"]
    expected_request = {
        "symbol": BTC,
        "startTime": START - 8 * HOUR,
        "endTime": END + 8 * HOUR + MINUTE,
        "limit": 1000,
    }
    require(request == expected_request, "Complete padded funding request bounds.")
    url = claim["raw"]["url"]
    parsed = urlsplit(url)
    require(
        parsed.scheme == "https"
        and parsed.netloc == "fapi.binance.com"
        and parsed.path == "/fapi/v1/fundingRate"
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True)
        == {key: [str(value)] for key, value in request.items()},
        "Original authoritative REST funding URL.",
    )
    rows = json_value(_http(claim["raw"], url, 200))
    require(isinstance(rows, list) and 0 < len(rows) < 1000, "Complete uncapped funding response.")
    assert isinstance(rows, list)
    records: dict[int, dict[str, Any]] = {}
    rates: dict[int, Decimal] = {}
    prices: dict[int, float] = {}
    for row in rows:
        require(isinstance(row, dict), "Native funding record object.")
        assert isinstance(row, dict)
        instant = row["fundingTime"]
        require(
            type(instant) is int
            and request["startTime"] <= instant <= request["endTime"]
            and (not records or instant > next(reversed(records)))
            and row["symbol"] == BTC
            and row.get("rateType", "Regular") == "Regular",
            "Unique ordered native regular funding identity.",
        )
        rate, price = decimal(row["fundingRate"]), decimal(row["markPrice"])
        require(price > 0, "Positive actual funding settlement mark, never a candle proxy.")
        records[instant], rates[instant], prices[instant] = row, rate, finite_float(price)
    expected_slots = set(range(START // (8 * HOUR) - 1, END // (8 * HOUR) + 2))
    slots = [(instant + 4 * HOUR) // (8 * HOUR) for instant in records]
    require(
        len(slots) == len(set(slots))
        and set(slots) == expected_slots
        and all(
            abs(instant - slot * 8 * HOUR) < MINUTE
            for instant, slot in zip(records, slots, strict=True)
        ),
        "Complete actual primary, terminal and padded funding slots.",
    )
    require(
        native == {t: rate for t, rate in rates.items() if START <= t < REST_ONLY_START},
        "Every September/October native timestamp/rate matches REST exactly.",
    )
    rest_only = [row for t, row in records.items() if REST_ONLY_START <= t < END]
    require(
        claim["rows"] == len(records)
        and claim["primary_rows"] == sum(START <= t < END for t in records)
        and claim["native_archive_records_matching_REST"] == len(native)
        and claim["REST_only_primary_native_events"] == rest_only,
        "Declared funding qualification agrees with independently decoded sources.",
    )
    terminal = [
        row for t, row in records.items() if (t + 4 * HOUR) // (8 * HOUR) == END // (8 * HOUR)
    ]
    require(
        len(terminal) == 1 and claim["terminal_event"] == terminal[0],
        "Actual terminal funding slot.",
    )
    selected = {t: (finite_float(rates[t]), prices[t]) for t in records if START <= t <= END}
    return selected, {
        "funding_primary_records": sum(START <= t < END for t in records),
        "funding_archive_witness_records": len(native),
        "funding_rest_only_records": len(rest_only),
        "funding_padded_records": len(records),
        "funding_contract": "native_REST_census_with_complete_SeptOct_CSV_witness_Nov_REST_only",
        "funding_source": claim["raw"],
        "actual_terminal_funding_event": terminal[0],
    }


def load(path: Path) -> Sources:
    raw = read_bytes(path, 1 << 20)
    require(
        hashlib.sha256(raw).hexdigest() == INPUT_SHA256, "Pinned recent diagnostic input bytes."
    )
    body = json_value(raw)
    require(isinstance(body, dict), "Recent source inventory object.")
    assert isinstance(body, dict)
    require(
        body["schema"] == "locked-regime-recent-source-qualification-v1"
        and body["exposure"] == "EXPOSED_RECENT_DIAGNOSTIC_NOT_FRESH_OOS"
        and body["requested_start_ms"] == START
        and body["requested_end_ms"] == END
        and body["calendar_days"] == (END - START) // DAY
        and body["candidate_lock_sha256"] == CANDIDATE_LOCK_SHA256
        and body["financial_scoring"] is False,
        "Locked candidate, exact diagnostic window and exposed-data identity.",
    )
    hours, trades, marks, native, identities = _archives(body)
    funding, funding_summary = _funding(body, native)
    clock = set(range(START, END + 1, HOUR)) | set(funding)
    for day in range(START, END, DAY):
        clock.update((day + MINUTE, day + 2 * MINUTE))
    frames = []
    for instant in sorted(clock):
        regular = instant // MINUTE * MINUTE
        mark = marks.get(regular)
        values = {BTC: finite_float(mark.close)} if mark is not None else {}
        rate = 0.0
        if instant in funding:
            rate, settlement = funding[instant]
            values = {BTC: settlement}
        execution = {BTC: finite_float(trades[instant].close)} if instant in trades else {}
        frames.append(Frame(instant, values, {BTC: rate}, execution))
    extremes = {
        instant: (finite_float(bar.low), finite_float(bar.high)) for instant, bar in marks.items()
    }
    require(
        all(
            BTC in frame.marks and frame.execution_prices
            for frame in frames
            if frame.timestamp_ms > START and frame.timestamp_ms % MINUTE == 0
        ),
        "Native marks and trade references for every regular/decision execution frame.",
    )
    return Sources(
        (Segment(START, END, tuple(frames), extremes),),
        {instant: finite_float(bar.close) for instant, bar in hours.items()},
        {
            "input_sha256": INPUT_SHA256,
            "candidate_lock_sha256": CANDIDATE_LOCK_SHA256,
            "data_status": "exposed_recent_diagnostic_not_fresh_oos",
            "source_venue": "BINANCE_USDM_PROXY_NOT_EXECUTABLE_HYPERLIQUID",
            "calendar_days": (END - START) // DAY,
            "unavailable_days": 0,
            "conditional_admitted_days": (END - START) // DAY,
            "intervals": [{"start_ms": START, "end_ms": END}],
            "hourly_observations": len(hours),
            "target_hourly_observations": (END - START) // HOUR,
            "minute_trade_observations": len(trades),
            "minute_mark_observations": len(marks),
            "qualified_archive_sources": identities,
            "explicit_unavailable_daily_funding_archives": len(PERIODS)
            - len(ARCHIVE_FUNDING_PERIODS),
        }
        | funding_summary,
    )
