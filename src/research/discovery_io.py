"""Hash-bound native inputs for an exposed-history discovery batch, never a broker."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import re
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from research.btc_flow_io import read_bytes
from research.discovery import DiscoveryBar
from research.repaired_accounting import Frame, Instrument, ReplaySpec
from research.volume_repair import frames
from research.volume_repair_io import (
    DECEMBER,
    HOUR,
    MINUTE,
    Source,
    document,
    funding,
    klines,
    load_index,
    require,
    sha256,
)
from research.volume_repair_io import (
    END as END,
)
from research.volume_repair_io import (
    START as START,
)

STUDY_ID = "discovery-real-history-20261113-b2"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
SPLIT = START + 15 * 24 * HOUR
LEGACY_SHA = "fcc687f5272ab355579f4f9dfe990a4f7553101427b87f32ebf4608e49ce7172"
METRICS_SHA = "1524a9230edde0ec36eadde406ed77df3b61345d16711375c085ed93a8c6ab0f"
METRICS_HEADER = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)
PLAN = Path(__file__).resolve().parents[2] / "docs/experiments/exp_discovery_batch_two.md"


def plan_hash() -> str:
    return sha256(read_bytes(PLAN, 1 << 20))


def _legacy(path: Path) -> tuple[bytes, dict[tuple[str, str], Source]]:
    raw = read_bytes(path, 1 << 20)
    require(sha256(raw) == LEGACY_SHA, "accepted legacy input index identity")
    return raw, load_index(document(raw))


def _metrics_inventory(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    raw = read_bytes(path, 1 << 20)
    require(sha256(raw) == METRICS_SHA, "independently accepted OI inventory identity")
    rows = json.loads(raw)
    require(type(rows) is list and len(rows) == 31, "complete January OI inventory")
    for day, row in enumerate(rows, 1):
        require(
            type(row) is dict
            and set(row)
            == {"path", "sha256", "bytes", "checksum_path", "checksum_sha256", "checksum_bytes"},
            "OI inventory fields",
        )
        expected = f"BTCUSDT-metrics-2025-01-{day:02d}.zip"
        require(
            Path(row["path"]).name == expected
            and row["checksum_path"] == row["path"] + ".CHECKSUM",
            "OI instrument/day product identity",
        )
        for key in ("sha256", "checksum_sha256"):
            require(bool(re.fullmatch("[0-9a-f]{64}", row[key])), "OI source hash")
        require(
            type(row["bytes"]) is int
            and 0 < row["bytes"] <= 1_000_000
            and type(row["checksum_bytes"]) is int
            and 0 < row["checksum_bytes"] <= 4096,
            "bounded OI source size",
        )
    return raw, rows


def _metric_sources(row: dict[str, Any]) -> tuple[Source, Source]:
    return (
        Source("BTCUSDT", "oi", Path(row["path"]), row["sha256"], row["bytes"]),
        Source(
            "BTCUSDT",
            "oi_checksum",
            Path(row["checksum_path"]),
            row["checksum_sha256"],
            row["checksum_bytes"],
        ),
    )


def verify_sources(legacy_path: Path, metrics_path: Path) -> list[dict[str, object]]:
    """Opaque bytes/checksums only; no financial values are decoded here."""
    _, sources = _legacy(legacy_path)
    _, metrics = _metrics_inventory(metrics_path)
    selected = [source for (symbol, _), source in sources.items() if symbol in SYMBOLS]
    selected += [source for row in metrics for source in _metric_sources(row)]
    for source in selected:
        source.read()
    return [source.record() for source in selected]


def prepare(legacy_path: Path, metrics_path: Path) -> dict[str, object]:
    records = verify_sources(legacy_path, metrics_path)
    return {
        "schema": "discovery-source-index-v1",
        "study_id": STUDY_ID,
        "plan_sha256": plan_hash(),
        "legacy_index": {"path": str(legacy_path), "sha256": LEGACY_SHA},
        "metrics_inventory": {"path": str(metrics_path), "sha256": METRICS_SHA},
        "sources": records,
        "universe": list(SYMBOLS),
        "start_ms": START,
        "split_ms": SPLIT,
        "end_ms": END,
        "data_status": "development_exposed",
        "features_availability_assumed": True,
    }


def check_index(body: dict[str, object]) -> tuple[Path, Path]:
    require(
        set(body)
        == {
            "schema",
            "study_id",
            "plan_sha256",
            "legacy_index",
            "metrics_inventory",
            "sources",
            "universe",
            "start_ms",
            "split_ms",
            "end_ms",
            "data_status",
            "features_availability_assumed",
        },
        "discovery index fields",
    )
    require(
        body["schema"] == "discovery-source-index-v1"
        and body["study_id"] == STUDY_ID
        and body["plan_sha256"] == plan_hash()
        and body["universe"] == list(SYMBOLS)
        and body["start_ms"] == START
        and body["split_ms"] == SPLIT
        and body["end_ms"] == END
        and body["data_status"] == "development_exposed"
        and body["features_availability_assumed"] is True,
        "discovery identity/period/exposure binding",
    )
    paths = []
    for key, digest in (("legacy_index", LEGACY_SHA), ("metrics_inventory", METRICS_SHA)):
        record = body[key]
        require(
            isinstance(record, dict)
            and set(record) == {"path", "sha256"}
            and record["sha256"] == digest
            and isinstance(record["path"], str),
            "discovery source index reference",
        )
        assert isinstance(record, dict)
        paths.append(Path(record["path"]))
    require(body["sources"] == verify_sources(*paths), "source inventory changed")
    return paths[0], paths[1]


def oi_values(source: Source, checksum: Source, day_start_ms: int) -> dict[int, float | None]:
    """Decode a complete day; no imputation or claim of historical receipt proof."""
    raw = source.read()
    check = checksum.read().decode("ascii").split()
    require(len(check) == 2 and check == [sha256(raw), source.path.name], "OI official checksum")
    out: dict[int, float | None] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        require(len(members) == 1, "single OI CSV member")
        member = members[0]
        require(
            member.filename == source.path.name.removesuffix(".zip") + ".csv"
            and not member.is_dir()
            and member.flag_bits & 1 == 0
            and member.file_size <= 1_000_000
            and member.file_size <= 128 * max(member.compress_size, 1),
            "bounded native OI CSV",
        )
        with archive.open(member) as stream:
            rows = csv.reader(io.TextIOWrapper(stream, encoding="utf-8"))
            require(tuple(next(rows, [])) == METRICS_HEADER, "native OI header")
            for index, row in enumerate(rows):
                require(
                    index < 288 and len(row) == 8 and sum(map(len, row)) <= 4096,
                    "bounded native OI rows",
                )
                instant = (
                    int(
                        dt.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                        .replace(tzinfo=dt.UTC)
                        .timestamp()
                    )
                    * 1000
                )
                require(
                    instant == day_start_ms + index * 5 * MINUTE and row[1] == "BTCUSDT",
                    "contiguous UTC five-minute BTC OI snapshots",
                )
                value = float(row[2])
                require(math.isfinite(value) and value >= 0, "nonnegative native base-unit OI")
                out[instant] = value if value > 0 else None
    require(len(out) == 288, "complete daily OI snapshot count")
    return out


def observations(
    body: dict[str, object],
) -> tuple[dict[str, list[DiscoveryBar]], list[Frame], ReplaySpec, dict[str, object]]:
    legacy_path, metrics_path = check_index(body)
    _, sources = _legacy(legacy_path)
    _, metrics = _metrics_inventory(metrics_path)
    oi = {}
    for index, row in enumerate(metrics):
        oi.update(oi_values(*_metric_sources(row), START + index * 24 * HOUR))
    hours, marks, settlements = {}, {}, {}
    for symbol in SYMBOLS:

        def source(role: str, symbol: str = symbol) -> Source:
            return sources[symbol, role]

        hours[symbol] = klines(
            source("hourly_dec"), source("hourly_dec_checksum"), DECEMBER, HOUR, 744
        )
        hours[symbol] += klines(
            source("hourly_jan"), source("hourly_jan_checksum"), START, HOUR, 744
        )
        marks[symbol] = klines(
            source("mark_jan"), source("mark_jan_checksum"), START, MINUTE, 44_640
        )
        settlements[symbol] = funding(
            source("funding_native"), source("funding_native_checksum"), source("funding_rest")
        )
    bars: dict[str, list[DiscoveryBar]] = {}
    for symbol, series in hours.items():
        bars[symbol] = []
        for hour in series:
            close = hour.open_ms + HOUR
            stamp = close - 5 * MINUTE
            value = oi.get(stamp) if symbol == "BTCUSDT" else None
            bars[symbol].append(
                DiscoveryBar(symbol, close, hour.close, value, stamp if value is not None else None)
            )
    values = frames(hours, marks, settlements, START, END)
    spec = ReplaySpec(
        START,
        END,
        MINUTE,
        10_000.0,
        tuple(Instrument(symbol, "perp", 5.0, 2.0) for symbol in SYMBOLS),
        tuple(frame.timestamp_ms for frame in values),
    )
    quality: dict[str, object] = {
        "native_oi_rows": len(oi),
        "zero_oi_unavailable_ms": sorted(t for t, value in oi.items() if value is None),
        "zero_oi_policy": "unavailable_no_imputation",
        "zero_oi_selected_by_hourly_clock": sum(
            value is None and (t + 5 * MINUTE) % HOUR == 0 for t, value in oi.items()
        ),
    }
    return bars, values, spec, quality


def source_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    names = (
        "src/research/discovery.py",
        "src/research/discovery_io.py",
        "src/research/discovery_cli.py",
        "src/research/repaired_accounting.py",
        "src/research/volume_repair.py",
        "src/research/volume_repair_io.py",
        "src/research/btc_flow_io.py",
        "docs/experiments/exp_discovery_batch_one.md",
        "docs/experiments/exp_discovery_batch_two.md",
    )
    return {name: sha256(read_bytes(root / name, 1 << 20)) for name in names}


def as_spec(spec: ReplaySpec) -> dict[str, Any]:
    value = asdict(spec)
    value.pop("event_times_ms")
    return value
