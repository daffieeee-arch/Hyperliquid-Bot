"""New timestamp-index admission for pinned native BTC OI; legacy inputs stay frozen."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.btc_flow_io import read_bytes
from research.discovery import DiscoveryBar
from research.discovery_io import METRICS_HEADER
from research.regime_io import Sources, midnight
from research.regime_io import load as load_prices
from research.two_models_io import BTC, DAY, END, MINUTE, START
from research.volume_repair_io import Source, archive_rows, decimal, json_value, require

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/experiments/exp_oi_recovery_broader_20261113.md"
STUDY_ID = "btc-oi-recovery-broader-20261113-v1"
INVENTORY_SHA256 = "fabf9fad203c0190d766f082ec9ab026b651f22544870ccd0f396cb1055c093a"
QUALITY_SHA256 = "aa8162fbfa4d748ef725bde0e0b5b80371e39e9125744534a0b52156d075ef62"
MASK_SHA256 = "920e1bc1ac89bea41653c1745e18db5df261ddc4db1fa7c09b3bfac595792844"
FOLDS = {
    "development": (START, midnight("2025-01-01")),
    "diagnostic_2025": (midnight("2025-01-01"), midnight("2026-01-01")),
    "diagnostic_2026": (midnight("2026-01-01"), END),
}
SPLITS = tuple(end for _, end in list(FOLDS.values())[:-1])
MaskRow = tuple[int, str | None, str | None, int | None]


def encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


@dataclass(frozen=True)
class OpenInterest:
    values: dict[int, float | None]
    # Timestamp, available decimal text, original decimal text, physical CSV row.
    mask: tuple[MaskRow, ...]
    daily_quality: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def source(row: dict[str, Any], checksum: bool = False) -> Source:
    prefix = "checksum_" if checksum else ""
    return Source(
        BTC,
        "oi_checksum" if checksum else "oi",
        Path(row[prefix + "path"]),
        row[prefix + "sha256"],
        row[prefix + "bytes"],
    )


def iso(instant: int) -> str:
    return dt.datetime.fromtimestamp(instant // 1000, dt.UTC).isoformat()


def read_day(
    row: dict[str, Any], start: int
) -> tuple[dict[int, float | None], list[MaskRow], dict[str, Any]]:
    expected = tuple(range(start, start + DAY, 5 * MINUTE))
    observed: dict[int, tuple[float | None, str, int]] = {}
    stamps, texts, zeros = [], [], []
    with zipfile.ZipFile(io.BytesIO(source(row).read())) as archive:
        require(
            len(archive.infolist()) == 1 and archive.infolist()[0].file_size <= 1_000_000,
            "retain original one-megabyte OI CSV admission bound",
        )
    header_seen = False
    for number, raw in enumerate(archive_rows(source(row), source(row, True), 288), 1):
        if number == 1:
            require(tuple(raw) == METRICS_HEADER, "exact native eight-column OI header")
            header_seen = True
            continue
        require(len(raw) == 8 and raw[1] == BTC, "native BTC OI row identity")
        parsed = dt.datetime.strptime(raw[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.UTC)
        require(
            parsed.strftime("%Y-%m-%d %H:%M:%S") == raw[0], "literal native OI timestamp format"
        )
        instant = int(parsed.timestamp()) * 1000
        require(
            start <= instant < start + DAY and instant % (5 * MINUTE) == 0,
            "native OI row must be on its own exact UTC five-minute grid",
        )
        require(instant not in observed, "duplicate OI timestamp, even with identical values")
        value = decimal(raw[2])
        require(value >= 0, "negative OI cannot become unavailable")
        converted = float(value)
        require(math.isfinite(converted), "finite base-BTC OI quantity")
        observed[instant] = (converted if value > 0 else None, raw[2], number)
        stamps.append(instant)
        texts.append(raw[0])
        if value == 0:
            zeros.append(raw[0])
    require(header_seen, "native OI header is mandatory even with unavailable rows")
    missing = [t for t in expected if t not in observed]
    values = {t: observed[t][0] if t in observed else None for t in expected}
    mask: list[MaskRow] = [
        (t, observed[t][1] if values[t] is not None else None, observed[t][1], observed[t][2])
        if t in observed
        else (t, None, None, None)
        for t in expected
    ]
    quality = {
        "day": row["day"],
        "rows": len(stamps),
        "first": texts[0] if texts else None,
        "last": texts[-1] if texts else None,
        "strict_old_helper_compatible": tuple(stamps) == expected,
        "order_mismatches": sum(a != b for a, b in zip(stamps, expected, strict=False)),
        "duplicate_rows": 0,
        "missing_expected": [iso(t) for t in missing],
        "unexpected_timestamps": [],
        "zero_timestamps": zeros,
    }
    return values, mask, quality


def load_oi(path: Path) -> OpenInterest:
    raw = read_bytes(path, 1 << 20)
    require(hashlib.sha256(raw).hexdigest() == INVENTORY_SHA256, "pinned 974-day OI inventory")
    rows = json_value(raw)
    require(isinstance(rows, list) and len(rows) == (END - START) // DAY, "complete OI source days")
    assert isinstance(rows, list)
    values: dict[int, float | None] = {}
    mask = []
    quality = []
    for index, row in enumerate(rows):
        require(isinstance(row, dict), "native OI source inventory record")
        assert isinstance(row, dict)
        instant = START + index * DAY
        day = iso(instant)[:10]
        require(
            row["day"] == day
            and Path(row["path"]).name == f"BTCUSDT-metrics-{day}.zip"
            and row["checksum_path"] == row["path"] + ".CHECKSUM",
            "exact instrument/day and official sidecar binding",
        )
        decoded, native_mask, daily = read_day(row, instant)
        require(not (set(values) & set(decoded)), "unique day partitions")
        values.update(decoded)
        mask.extend(native_mask)
        quality.append(daily)
    require(
        hashlib.sha256(encoded(quality)).hexdigest() == QUALITY_SHA256,
        "full prequalified native anomaly/row-order census must agree",
    )
    missing = sum(native is None for _, _, native, _ in mask)
    zero = sum(available is None and native is not None for _, available, native, _ in mask)
    require(
        len(mask) == 280_512 and missing == 130 and zero == 330,
        "exact missing/zero OI availability mask",
    )
    mask_sha256 = hashlib.sha256(encoded(mask)).hexdigest()
    require(mask_sha256 == MASK_SHA256, "full registered native OI availability/row mask")
    return OpenInterest(
        values,
        tuple(mask),
        tuple(quality),
        {
            "inventory_sha256": INVENTORY_SHA256,
            "daily_quality_sha256": QUALITY_SHA256,
            "full_mask_sha256": mask_sha256,
            "expected_slots": len(mask),
            "native_rows": len(mask) - missing,
            "missing_slots": missing,
            "zero_slots": zero,
            "unordered_physical_days": sum(q["order_mismatches"] > 0 for q in quality),
            "complete_permuted_days": sum(
                q["rows"] == 288 and q["order_mismatches"] > 0 for q in quality
            ),
            "missing_days": [q["day"] for q in quality if q["missing_expected"]],
            "features_availability_assumed": True,
            "clock_policy": "literal_native_UTC_T_minus_5m_no_receipt_or_revision_proof",
            "unit": "base_BTC_sum_open_interest_never_quote_value",
            "unavailable_policy": "missing_and_zero_None_no_imputation_or_old_file_repair",
        },
    )


def bars(prices: Sources, oi: OpenInterest) -> dict[str, list[DiscoveryBar]]:
    result = []
    for instant, price in sorted(prices.hourly.items()):
        stamp = instant - 5 * MINUTE
        value = oi.values.get(stamp)
        result.append(
            DiscoveryBar(BTC, instant, price, value, stamp if value is not None else None)
        )
    return {BTC: result}


def load(price_path: Path, oi_path: Path) -> tuple[Sources, OpenInterest]:
    oi = load_oi(oi_path)
    prices = load_prices(price_path)
    return prices, oi
