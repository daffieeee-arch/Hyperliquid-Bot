"""Qualified immutable native sources for the registered two-model study."""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import math
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from research.btc_flow_io import read_bytes
from research.repaired_accounting import Frame, Instrument, ReplaySpec
from research.volume_repair_io import Source, archive_rows, json_value, require

MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
BTC = "BTCUSDT"
START = 1_704_067_200_000
END = 1_788_220_800_000
ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/experiments/exp_two_models_20261113.md"
INPUT_PINS = {
    "daily": "184c0d05f3f21d17539f4bafb78727b7665b60f878cc5572f992ce6e4af5fe21",
    "older": "edabb70e9b2e4d84ac5e863ff2dd0aee13a273993ce725172d24c25dd60af9d9",
    "recent": "9ebe259b4115e13013fa338c1454ce1a9362464c50e9d307bd2b861ab7d3eb14",
    "funding": "4ddc0a9f6a9c5b0fe417841e99584bf632912e342e915bfa123e9c865c4ff8a0",
    "bad_days": "652d19893e232043973ab1645825c524eee66b14415bb7c35c224e31eaef4c13",
}


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def record(path: Path, maximum: int = 8 << 20) -> dict[str, Any]:
    content = read_bytes(path, maximum)
    return {"path": str(path), "bytes": len(content), "sha256": sha(content)}


def read_record(row: dict[str, Any], maximum: int = 8 << 20) -> bytes:
    require(set(row) >= {"path", "bytes", "sha256"}, "source identity fields")
    require(type(row["bytes"]) is int and 0 < row["bytes"] <= maximum, "source byte bound")
    raw = read_bytes(Path(row["path"]), maximum)
    require(len(raw) == row["bytes"] and sha(raw) == row["sha256"], "source identity changed")
    return raw


def bound_metadata(paths: dict[str, Path]) -> dict[str, Any]:
    require(set(paths) == set(INPUT_PINS), "all registered metadata inventories")
    data = {}
    for name, path in paths.items():
        content = read_bytes(path, 8 << 20)
        require(sha(content) == INPUT_PINS[name], "registered inventory: " + name)
        data[name] = json_value(content)
    return data


def months() -> tuple[str, ...]:
    return tuple(
        f"{year:04d}-{month:02d}"
        for year in (2024, 2025, 2026)
        for month in range(1, 13)
        if year < 2026 or month <= 8
    )


def month_clock(month: str) -> tuple[int, int]:
    year, number = map(int, month.split("-"))
    first = int(dt.datetime(year, number, 1, tzinfo=dt.UTC).timestamp()) * 1000
    return first, first + calendar.monthrange(year, number)[1] * DAY


def native_source(row: dict[str, Any], checksum: bool = False) -> Source:
    prefix = "checksum_" if checksum else ""
    return Source(
        BTC,
        row["role"] + ("_checksum" if checksum else ""),
        Path(row[prefix + "path"]),
        row[prefix + "sha256"],
        row[prefix + "bytes"],
    )


def capture_group(origin: str, name: str) -> str:
    if origin == "recent":
        return "recent_diagnostic"
    if name.startswith("20260918"):
        return "development"
    if name.startswith("20260923"):
        return "validation"
    return "diagnostic"


def capture_metadata(origin: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    directory = Path(item["run"] if origin == "older" else item["run_path"])
    records = [record(directory / leaf) for leaf in ("capture-claim.json", "capture-health.json")]
    if origin == "older":
        require(
            records[0]["sha256"] == item["claim_sha256"]
            and records[1]["sha256"] == item["health_sha256"],
            "original capture claim/health provenance",
        )
        return records
    manifest_record = record(directory / "publication-manifest.json")
    require(manifest_record["sha256"] == item["manifest_sha256"], "original publication manifest")
    manifest = json_value(read_record(manifest_record))
    require(
        isinstance(manifest, dict) and manifest.get("schema") == "public-collector-closed-chunk-v1",
        "closed publication schema",
    )
    assert isinstance(manifest, dict)
    for reference in records:
        linked = manifest["files"][Path(reference["path"]).name]
        require(
            linked["sha256"] == reference["sha256"] and linked["bytes"] == reference["bytes"],
            "manifest-linked native metadata",
        )
    complete_record = record(directory / "COMPLETE.json")
    complete = json_value(read_record(complete_record))
    require(
        isinstance(complete, dict)
        and complete.get("closed_chunk_verified") is True
        and complete.get("manifest_sha256") == item["manifest_sha256"]
        and complete.get("research_ready") is item["research_ready"],
        "original closed publication receipt",
    )
    return [*records, complete_record, manifest_record]


def prepare(paths: dict[str, Path]) -> dict[str, Any]:
    data = bound_metadata(paths)
    selected = [
        row
        for row in data["daily"]["sources"]
        if row["month"] in months() or (row["month"] == "2023-12" and row["role"] == "hourly")
    ]
    require(len(selected) == 129, "32 complete product months plus hourly warm-up")
    captures = []
    for origin in ("older", "recent"):
        for item in data[origin]:
            directory = Path(item["run"] if origin == "older" else item["run_path"])
            name = directory.name
            group = capture_group(origin, name)
            metadata = capture_metadata(origin, item)
            captures.append(
                {"name": name, "group": group, "parts": item["parts"], "metadata": metadata}
            )
    require(len(captures) == 11, "registered closed captures")
    for row in selected:
        native_source(row).read()
        native_source(row, True).read()
    for capture in captures:
        require(0 < len(capture["parts"]) <= 5000, "bounded raw part count")
        for part in capture["parts"]:
            read_record(part, 4 << 20)
    funding_sources = data["funding"]["sources"]
    for row in funding_sources:
        read_record(row)
    return {
        "schema": "two-model-source-index-v1",
        "study_ids": ["daily-momentum-20261113-v1", "micro-flow-20261113-v1"],
        "plan_sha256": sha(read_bytes(PLAN, 1 << 20)),
        "inventories": {name: record(path) for name, path in paths.items()},
        "daily_sources": selected,
        "captures": captures,
        "funding_sources": funding_sources,
        "data_status": "exposed_retrospective_no_fresh_oos",
    }


def check_index(body: dict[str, Any], *, verify_raw: bool = True) -> dict[str, Any]:
    require(
        set(body)
        == {
            "schema",
            "study_ids",
            "plan_sha256",
            "inventories",
            "daily_sources",
            "captures",
            "funding_sources",
            "data_status",
        },
        "input index fields",
    )
    require(body["schema"] == "two-model-source-index-v1", "source schema")
    require(
        body["study_ids"] == ["daily-momentum-20261113-v1", "micro-flow-20261113-v1"],
        "study identities",
    )
    require(body["data_status"] == "exposed_retrospective_no_fresh_oos", "exposure identity")
    require(body["plan_sha256"] == sha(read_bytes(PLAN, 1 << 20)), "registered plan identity")
    refs = body["inventories"]
    require(isinstance(refs, dict) and set(refs) == set(INPUT_PINS), "inventory reference set")
    paths = {}
    for name, digest in INPUT_PINS.items():
        require(refs[name]["sha256"] == digest, "pinned metadata identity")
        read_record(refs[name])
        paths[name] = Path(refs[name]["path"])
    data = bound_metadata(paths)
    selected = [
        r
        for r in data["daily"]["sources"]
        if r["month"] in months() or (r["month"] == "2023-12" and r["role"] == "hourly")
    ]
    require(body["daily_sources"] == selected and len(selected) == 129, "native source selection")
    require(body["funding_sources"] == data["funding"]["sources"], "funding source selection")
    expected_parts = [(origin, item) for origin in ("older", "recent") for item in data[origin]]
    require(len(body["captures"]) == len(expected_parts) == 11, "capture count")
    for capture, (origin, item) in zip(body["captures"], expected_parts, strict=True):
        directory = Path(item["run"] if origin == "older" else item["run_path"])
        require(
            capture["name"] == directory.name and capture["parts"] == item["parts"],
            "raw capture selection",
        )
        require(capture["group"] == capture_group(origin, directory.name), "capture fold")
        require(
            capture["metadata"] == capture_metadata(origin, item),
            "complete original capture metadata binding",
        )
        if verify_raw:
            for part in item["parts"]:
                read_record(part, 4 << 20)
    if verify_raw:
        for row in selected:
            native_source(row).read()
            native_source(row, True).read()
        for row in body["funding_sources"]:
            read_record(row)
    return data


def number(text: str, *, positive: bool = False) -> float:
    value = float(text)
    require(math.isfinite(value) and (not positive or value > 0), "finite native number")
    return value


@dataclass(frozen=True, slots=True)
class Candle:
    open_ms: int
    close: float
    low: float
    high: float


def candles(row: dict[str, Any], step: int, *, missing: bool = False) -> list[Candle]:
    start, end = month_clock(row["month"])
    values: list[Candle] = []
    last = start - step
    for raw in archive_rows(native_source(row), native_source(row, True), 50_000):
        require(len(raw) == 12, "native twelve-column candle")
        if raw[0] == "open_time":
            require(not values, "header only before rows")
            continue
        instant = int(raw[0])
        require(
            start <= instant < end and instant % step == 0 and instant > last, "native candle clock"
        )
        require(missing or instant == last + step, "contiguous native candle grid")
        require(int(raw[6]) == instant + step - 1, "native close availability")
        opening, high, low, close = [number(raw[i], positive=True) for i in (1, 2, 3, 4)]
        require(low <= min(opening, close) <= max(opening, close) <= high, "native OHLC bounds")
        values.append(Candle(instant, close, low, high))
        last = instant
    require(bool(values), "nonempty native candles")
    require(missing or len(values) == (end - start) // step, "complete native candle count")
    return values


def funding_censuses(
    body: dict[str, Any],
) -> tuple[dict[int, tuple[float, float]], tuple[int, ...]]:
    rest: dict[int, tuple[Decimal, float | None]] = {}
    hl: dict[int, Decimal] = {}
    previous_cursor = 1_577_836_800_000
    for source in body["funding_sources"]:
        rows = json_value(read_record(source))
        require(isinstance(rows, list) and len(rows) == source["rows"], "funding response census")
        assert isinstance(rows, list)
        request = source["request"]
        if source["venue"] == "BINANCE_USDM":
            require(
                request["symbol"] == BTC
                and request["startTime"] == previous_cursor
                and request["limit"] == 1000,
                "complete funding pagination",
            )
            times = []
            for row in rows:
                require(row["symbol"] == BTC, "funding instrument")
                instant = int(row["fundingTime"])
                require(
                    request["startTime"] <= instant <= request["endTime"] and instant not in rest,
                    "funding time uniqueness/bounds",
                )
                rate = Decimal(row["fundingRate"])
                require(rate.is_finite(), "finite funding rate")
                price = number(row["markPrice"], positive=True) if row.get("markPrice") else None
                require(
                    instant < START or price is not None,
                    "exact settlement prices required for primary study",
                )
                rest[instant] = (rate, price)
                times.append(instant)
            require(times == sorted(times) and bool(times), "ordered nonempty funding page")
            previous_cursor = times[-1] + 1
        else:
            require(
                source["venue"] == "HYPERLIQUID"
                and request["coin"] == "BTC"
                and 0 < len(rows) < 500,
                "bounded complete HL funding census",
            )
            times = []
            for row in rows:
                instant = int(row["time"])
                require(
                    row["coin"] == "BTC" and request["startTime"] <= instant <= request["endTime"],
                    "native HL funding identity",
                )
                rate = Decimal(row["fundingRate"])
                require(
                    rate.is_finite() and (instant not in hl or hl[instant] == rate),
                    "HL funding uniqueness",
                )
                hl[instant] = rate
                times.append(instant)
            require(times == sorted(set(times)), "ordered HL funding census")
            expected_hours = set(
                range(
                    (request["startTime"] + HOUR - 1) // HOUR,
                    (request["endTime"] + HOUR - 1) // HOUR,
                )
            )
            require(
                {t // HOUR for t in times} == expected_hours, "complete actual hourly funding slots"
            )
            require(
                all(abs(t - round(t / HOUR) * HOUR) < MINUTE for t in times),
                "native funding calendar clock bound",
            )
    native: dict[int, Decimal] = {}
    for source in body["daily_sources"]:
        if source["role"] != "funding":
            continue
        start, end = month_clock(source["month"])
        for row in archive_rows(native_source(source), native_source(source, True), 200):
            require(len(row) == 3, "funding native columns")
            if row[0] == "calc_time":
                continue
            instant, interval, rate = int(row[0]), int(row[1]), Decimal(row[2])
            require(
                start <= instant < end
                and interval == 8
                and rate.is_finite()
                and instant not in native,
                "native funding interval/identity",
            )
            native[instant] = rate
    required = {t: rate for t, (rate, _) in rest.items() if START <= t < END}
    require(native == required, "complete native versus REST funding time/rate census")
    expected_slots = set(range(START // (8 * HOUR), END // (8 * HOUR)))
    require(
        {round(t / (8 * HOUR)) for t in native} == expected_slots
        and len(native) == len(expected_slots)
        and all(abs(t - round(t / (8 * HOUR)) * 8 * HOUR) < MINUTE for t in native),
        "complete native eight-hour funding slots",
    )
    terminal = [
        (t, price) for t, (_, price) in rest.items() if round(t / (8 * HOUR)) == END // (8 * HOUR)
    ]
    require(
        len(terminal) == 1 and terminal[0][1] is not None and abs(terminal[0][0] - END) < MINUTE,
        "complete actual terminal funding slot",
    )
    result = {
        t: (float(rate), float(price))
        for t, (rate, price) in rest.items()
        if START <= t <= END and price is not None
    }
    require(bool(result) and any(t > END for t in rest), "funding boundary pad")
    return result, tuple(sorted(hl))


def daily_months(
    body: dict[str, Any],
) -> Iterator[
    tuple[str, list[Frame], dict[int, float], dict[int, tuple[float, float]], frozenset[int]]
]:
    data = check_index(body, verify_raw=False)
    funding, _ = funding_censuses(body)
    sources = {(row["month"], row["role"]): row for row in body["daily_sources"]}
    bad = {
        int(dt.datetime.fromisoformat(row["date_utc"]).replace(tzinfo=dt.UTC).timestamp())
        * 1000
        // DAY
        for row in data["bad_days"]["bad_days"]
    }
    hours = {bar.open_ms + HOUR: bar.close for bar in candles(sources["2023-12", "hourly"], HOUR)}
    for month in months():
        start, end = month_clock(month)
        hourly = candles(sources[month, "hourly"], HOUR)
        hours.update({bar.open_ms + HOUR: bar.close for bar in hourly})
        trades = candles(sources[month, "trade_minute"], MINUTE)
        marks = candles(sources[month, "mark_minute"], MINUTE, missing=True)
        by_trade = {bar.open_ms + MINUTE: bar.close for bar in trades}
        by_mark = {bar.open_ms + MINUTE: bar for bar in marks}
        expected_missing = {t for t in range(start + MINUTE, end + 1, MINUTE) if t not in by_mark}
        require(
            all((t - MINUTE) // DAY in bad for t in expected_missing),
            "unregistered missing mark day",
        )
        require(
            bool({t // DAY for t in range(start, end, DAY)} - bad), "at least one qualified day"
        )
        clock = sorted(
            set(range(start, end + 1, MINUTE)) | {t for t in funding if start <= t <= end}
        )
        frames = []
        for instant in clock:
            regular = instant // MINUTE * MINUTE
            bar = by_mark.get(regular)
            values = {BTC: bar.close} if bar is not None else {}
            rate = 0.0
            if instant in funding:
                rate, settlement = funding[instant]
                values = {BTC: settlement}
            execution = {BTC: by_trade[instant]} if instant in by_trade else {}
            frames.append(Frame(instant, values, {BTC: rate}, execution))
        extremes = {instant: (bar.low, bar.high) for instant, bar in by_mark.items()}
        eligible = frozenset(t // DAY for t in range(start, end, DAY) if t // DAY not in bad)
        yield month, frames, dict(hours), extremes, eligible
        hours = {t: price for t, price in hours.items() if t >= end - 80 * HOUR}


def daily_spec(month: str, frames: list[Frame], equity: float) -> ReplaySpec:
    start, end = month_clock(month)
    return ReplaySpec(
        start,
        end,
        MINUTE,
        equity,
        (Instrument(BTC, "perp", 4.5, 2.0),),
        tuple(frame.timestamp_ms for frame in frames),
    )


def implementation_hashes() -> dict[str, str]:
    names = (
        "src/research/daily_momentum.py",
        "src/research/micro_flow.py",
        "src/research/two_models_io.py",
        "src/research/micro_flow_io.py",
        "src/research/two_models_cli.py",
        "src/research/repaired_accounting.py",
        "src/research/volume_repair_io.py",
        "src/research/repaired_replay.py",
        "src/research/btc_flow_io.py",
        "src/hyperliquid_bot/paper_engine/precision.py",
        "docs/experiments/exp_two_models_20261113.md",
    )
    return {name: sha(read_bytes(ROOT / name, 1 << 20)) for name in names}
