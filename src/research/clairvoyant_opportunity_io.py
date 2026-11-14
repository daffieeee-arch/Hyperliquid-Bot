"""Pinned native TRADE1m admission only; no oracle choices or financial labels."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.btc_flow_io import read_bytes
from research.two_models_io import BTC, DAY, END, MINUTE, START, month_clock, months, native_source
from research.volume_repair_io import archive_rows, decimal, json_value, klines, require

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/experiments/exp_clairvoyant_minute_opportunity_20261113.md"
AMENDMENT = ROOT / "docs/experiments/clairvoyant_minute_numeric_amendment_20261113.md"
STUDY_ID = "btc-clairvoyant-minute-opportunity-20261113-v1"
LABEL = "CLAIRVOYANT_NONTRADABLE"
INVENTORY_SHA256 = "184c0d05f3f21d17539f4bafb78727b7665b60f878cc5572f992ce6e4af5fe21"
INDEX_SHA256 = "4a19eeced1b487b723136dc6cc05a60a3333f465eac7fa186231045665a3e0f8"


def encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def prepare_index(inventory: Path) -> bytes:
    raw = read_bytes(inventory, 1 << 20)
    require(sha(raw) == INVENTORY_SHA256, "original frozen metadata inventory")
    body = json_value(raw)
    require(isinstance(body, dict), "metadata inventory object")
    assert isinstance(body, dict)
    rows = [
        r
        for r in body["sources"]
        if r["role"] == "trade_minute" and r["month"] in ("2023-12", *months())
    ]
    result = encoded(rows)
    require(sha(result) == INDEX_SHA256, "exact33existing monthly trade archive records")
    return result


def load_index(path: Path) -> list[dict[str, Any]]:
    raw = read_bytes(path, 1 << 20)
    require(sha(raw) == INDEX_SHA256, "registered33month TRADE1m source index")
    rows = json_value(raw)
    require(isinstance(rows, list) and len(rows) == 33, "exact33month source count")
    assert isinstance(rows, list)
    for row, month in zip(rows, ("2023-12", *months()), strict=True):
        require(
            row["symbol"] == BTC
            and row["role"] == "trade_minute"
            and row["month"] == month
            and Path(row["path"]).name == f"{BTC}-1m-{month}.zip"
            and row["checksum_path"] == row["path"] + ".CHECKSUM"
            and row["zip_member"] == f"{BTC}-1m-{month}.csv",
            "native trade instrument/filename/month/sidecar",
        )
    return rows


def month_closes(row: dict[str, Any]) -> list[tuple[int, str]]:
    start, end = month_clock(row["month"])
    count = (end - start) // MINUTE
    verified = klines(native_source(row), native_source(row, True), start, MINUTE, count)
    exact: list[tuple[int, str]] = []
    for raw in archive_rows(native_source(row), native_source(row, True), count):
        if raw and raw[0] == "open_time":
            continue
        number = len(exact)
        require(
            number < count and int(raw[0]) == verified[number].open_ms,
            "exact second-pass row identity",
        )
        require(
            float(decimal(raw[4])) == verified[number].close,
            "native exact close binds strict validator",
        )
        exact.append((int(raw[0]) + MINUTE, raw[4]))
    require(len(exact) == count, "all exact native closes retained")
    return exact


@dataclass(frozen=True)
class NativeDay:
    start_ms: int
    prices: tuple[str, ...]

    def __post_init__(self) -> None:
        require(
            type(self.start_ms) is int and self.start_ms % DAY == 0 and len(self.prices) == 1441,
            "whole UTC day requires1441minute boundaries",
        )
        require(all(decimal(p) > 0 for p in self.prices), "positive native day closes")

    def source_hash(self) -> str:
        return sha(encoded([[self.start_ms + i * MINUTE, p] for i, p in enumerate(self.prices)]))


def days(rows: list[dict[str, Any]]) -> Iterator[NativeDay]:
    require(len(rows) == 33, "complete minute source partitions before day iteration")
    december = month_closes(rows[0])
    require(december[-1][0] == START, "Dec31final close is Jan1T00 endpoint")
    previous_time, previous_close = december[-1]
    del december
    pending = [previous_close]
    emitted = 0
    for row in rows[1:]:
        cache = month_closes(row)
        for instant, close in cache:
            require(
                instant == previous_time + MINUTE,
                "global native minute clock has no gaps/duplicates",
            )
            pending.append(close)
            previous_time = instant
            if instant % DAY == 0:
                day = NativeDay(instant - DAY, tuple(pending))
                require(
                    day.start_ms == START + emitted * DAY, "every calendar day retained in order"
                )
                emitted += 1
                yield day
                pending = [close]
        del cache
    require(
        previous_time == END and emitted == (END - START) // DAY and len(pending) == 1,
        "complete974day/exclusive-end source census",
    )


def qualify(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clock = hashlib.sha256()
    prices = hashlib.sha256()
    daily = []
    for day in days(rows):
        daily.append(
            {
                "start_ms": day.start_ms,
                "boundaries": len(day.prices),
                "source_hash": day.source_hash(),
            }
        )
        # Each shared midnight is hashed once in the global native chain.
        for i, p in enumerate(day.prices):
            if len(daily) > 1 and i == 0:
                continue
            instant = day.start_ms + i * MINUTE
            clock.update(f"{instant}\n".encode())
            prices.update(encoded([instant, p]))
    return {
        "label": LABEL,
        "source_only_no_financial_labels": True,
        "calendar_days": len(daily),
        "day_boundaries": 1441,
        "distinct_minute_boundaries": len(daily) * 1440 + 1,
        "minute_clock_sha256": clock.hexdigest(),
        "native_close_chain_sha256": prices.hexdigest(),
        "daily_source_hashes": daily,
        "marks_required": False,
        "known_mark_gap_days_included": True,
        "source_months": len(rows),
        "source_index_sha256": INDEX_SHA256,
    }


def verify_sources(path: Path, rows: list[dict[str, Any]]) -> None:
    require(load_index(path) == rows, "source index unchanged")
    for row in rows:
        native_source(row).read()
        native_source(row, True).read()
