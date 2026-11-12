"""Bounded native Binance inputs for the single January accounting repair.

No network, extraction to disk, Parquet SQL or recovered executable code is used.
The acquisition's pinned manifests bind bytes before financial decoding starts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from research.btc_flow_io import read_bytes
from research.harness.errors import IntegrityError
from research.repaired_replay import fields, mapping, sequence, text

HOUR = 3_600_000
MINUTE = 60_000
START = 1_735_689_600_000
END = 1_738_364_400_000
DECEMBER = START - 744 * HOUR
JANUARY_END = START + 744 * HOUR
STUDY_ID = "volume-repair-jan2025-delayed-v1"
PLAN_SHA256 = "ee60c7a415a7f80fd0cc6cb6f6c8f6e071f533a3c5122cd837608ad494516a67"
BUNDLE_SHA256 = "7f523c1a1e68fbd894cdd409c9d69dcbb70f3205b1125231c9eb30b63e90ae9b"
SYMBOLS = tuple(
    "1000SHIBUSDT AAVEUSDT ADAUSDT ALGOUSDT ATOMUSDT AVAXUSDT AXSUSDT BANDUSDT "
    "BCHUSDT BNBUSDT BTCUSDT C98USDT CRVUSDT DASHUSDT DOGEUSDT DOTUSDT ETCUSDT ETHUSDT "
    "FILUSDT GALAUSDT GTCUSDT HBARUSDT LINKUSDT LTCUSDT MANAUSDT NEARUSDT OGNUSDT "
    "ONEUSDT RLCUSDT RVNUSDT SANDUSDT SOLUSDT TRBUSDT TRXUSDT UNIUSDT XLMUSDT "
    "XMRUSDT XRPUSDT ZECUSDT".split()
)
ROLES = (
    "hourly_dec",
    "hourly_dec_checksum",
    "hourly_jan",
    "hourly_jan_checksum",
    "mark_jan",
    "mark_jan_checksum",
    "funding_native",
    "funding_native_checksum",
    "funding_rest",
)
KLINE_HEADER = (
    {"open_time"},
    {"open"},
    {"high"},
    {"low"},
    {"close"},
    {"volume"},
    {"close_time"},
    {"quote_volume", "quote_asset_volume"},
    {"count", "number_of_trades"},
    {"taker_buy_volume", "taker_buy_base_asset_volume"},
    {"taker_buy_quote_volume", "taker_buy_quote_asset_volume"},
    {"ignore"},
)


def source_name(symbol: str, role: str) -> str:
    base_role = role.removesuffix("_checksum")
    name = {
        "hourly_dec": f"{symbol}-1h-2024-12.zip",
        "hourly_jan": f"{symbol}-1h-2025-01.zip",
        "mark_jan": f"{symbol}-1m-2025-01.zip",
        "funding_native": f"{symbol}-fundingRate-2025-01.zip",
        "funding_rest": f"{symbol}-2025-01.raw.json",
    }[base_role]
    return name + (".CHECKSUM" if role.endswith("_checksum") else "")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError("volume_repair_input", message)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def json_value(content: bytes) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    return json.loads(content, object_pairs_hook=unique)


def document(content: bytes) -> dict[str, object]:
    return mapping(json_value(content))


@dataclass(frozen=True, slots=True)
class Source:
    symbol: str
    role: str
    path: Path
    sha256: str
    size: int

    def read(self) -> bytes:
        limit = (
            4096
            if self.role.endswith("checksum")
            else 1_000_000
            if self.role == "funding_rest"
            else 4_000_000
        )
        content = read_bytes(self.path, limit)
        require(len(content) == self.size and sha256(content) == self.sha256, "source identity")
        return content

    def record(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "role": self.role,
            "path": str(self.path),
            "sha256": self.sha256,
            "bytes": self.size,
        }


def prepare_index(archive: Path, acquisition: Path) -> dict[str, object]:
    """Bind an index to existing immutable manifests without decoding market data."""
    manifest = read_bytes(acquisition / "bundle-manifest.json", 1 << 20)
    require(sha256(manifest) == BUNDLE_SHA256, "frozen acquisition bundle identity")
    inventory = {
        text(row["path"]): row
        for item in sequence(document(manifest)["files"])
        for row in [mapping(item)]
    }
    plan = read_bytes(acquisition / "intake-plan.json", 1 << 20)
    require(sha256(plan) == PLAN_SHA256, "frozen acquisition plan identity")
    require(sequence(document(plan)["selected_universe"]) == list(SYMBOLS), "frozen universe")
    evidence_path = "evidence/existing-source-manifest.json"
    evidence = read_bytes(acquisition / evidence_path, 1 << 20)
    require(sha256(evidence) == inventory[evidence_path]["sha256"], "original source inventory")
    original = {
        text(row["path"]): row
        for item in sequence(document(evidence)["sources"])
        for row in [mapping(item)]
    }
    sources: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        root = archive / "binance-vision/xs-um/data/futures/um/monthly"
        paths = {
            "hourly_dec": root / f"klines/{symbol}/1h/{symbol}-1h-2024-12.zip",
            "hourly_jan": root / f"klines/{symbol}/1h/{symbol}-1h-2025-01.zip",
            "funding_native": root / f"fundingRate/{symbol}/{symbol}-fundingRate-2025-01.zip",
            "mark_jan": acquisition / f"marks/{symbol}-1m-2025-01.zip",
            "funding_rest": acquisition / f"funding/{symbol}-2025-01.raw.json",
        }
        if symbol == "BTCUSDT":
            btc = archive / "binance-vision/vision-prio1/futures/um/monthly"
            paths["mark_jan"] = btc / f"markPriceKlines/{symbol}/1m/{symbol}-1m-2025-01.zip"
            paths["funding_native"] = btc / f"fundingRate/{symbol}/{symbol}-fundingRate-2025-01.zip"
        for role in ("hourly_dec", "hourly_jan", "mark_jan", "funding_native"):
            paths[role + "_checksum"] = Path(str(paths[role]) + ".CHECKSUM")
        for role in ROLES:
            path = paths[role]
            if path.is_relative_to(acquisition):
                bound = inventory[str(path.relative_to(acquisition))]
            else:
                bound = original[str(path)]
            size = bound["bytes"]
            require(type(size) is int, "integer source size")
            assert isinstance(size, int)
            source = Source(symbol, role, path, text(bound["sha256"]), size)
            source.read()
            sources.append(source.record())
    return {"schema_version": 1, "study_id": STUDY_ID, "sources": sources}


def load_index(body: dict[str, object]) -> dict[tuple[str, str], Source]:
    fields(body, "schema_version study_id sources")
    require(type(body.get("schema_version")) is int and body["schema_version"] == 1, "schema")
    require(body.get("study_id") == STUDY_ID, "frozen study identity")
    rows = sequence(body.get("sources"))
    require(len(rows) == len(SYMBOLS) * len(ROLES), "complete bounded source inventory")
    result: dict[tuple[str, str], Source] = {}
    paths: set[str] = set()
    total = 0
    for item in rows:
        row = mapping(item)
        fields(row, "symbol role path sha256 bytes")
        symbol, role = text(row["symbol"]), text(row["role"])
        require(symbol in SYMBOLS and role in ROLES and (symbol, role) not in result, "source role")
        path = text(row["path"])
        require(Path(path).name == source_name(symbol, role), "source product/month filename")
        require(path not in paths, "duplicate source path")
        paths.add(path)
        digest = text(row["sha256"])
        require(bool(re.fullmatch("[0-9a-f]{64}", digest)), "source SHA-256")
        size = row["bytes"]
        require(type(size) is int and size > 0, "source size")
        assert isinstance(size, int)
        total += size
        source = Source(symbol, role, Path(path), digest, size)
        require(size <= (4096 if role.endswith("checksum") else 4_000_000), "source byte limit")
        result[symbol, role] = source
    require(total <= 100_000_000, "source inventory byte limit")
    return result


def archive_rows(source: Source, checksum: Source, maximum_rows: int) -> Iterator[list[str]]:
    content = source.read()
    check = checksum.read().decode("ascii").split()
    require(
        len(check) == 2 and check[0] == sha256(content) and check[1] == source.path.name,
        "official archive checksum",
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        require(len(members) == 1, "single CSV archive member")
        member = members[0]
        require(
            member.filename == source.path.name.removesuffix(".zip") + ".csv"
            and not member.is_dir()
            and member.file_size <= 32_000_000
            and member.file_size <= 128 * max(member.compress_size, 1)
            and member.flag_bits & 1 == 0,
            "bounded unencrypted native CSV member",
        )
        # Reading to EOF also verifies the archive's CRC before any result can finish.
        with archive.open(member) as binary, io.TextIOWrapper(binary, encoding="utf-8") as stream:
            for count, row in enumerate(csv.reader(stream)):
                require(count <= maximum_rows, "native row limit")
                require(sum(map(len, row)) <= 4096, "native row width limit")
                yield row


def decimal(value: object) -> Decimal:
    require(isinstance(value, str), "native numeric string")
    assert isinstance(value, str)
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise IntegrityError("volume_repair_input", "native number") from error
    require(number.is_finite(), "finite native number")
    return number


def integer(value: str) -> int:
    require(bool(re.fullmatch("[0-9]{1,16}", value)), "native integer")
    return int(value)


def finite_float(value: Decimal) -> float:
    result = float(value)
    require(
        math.isfinite(result) and (value == 0 or result != 0),
        "finite representable native number",
    )
    return result


@dataclass(frozen=True, slots=True)
class Hour:
    open_ms: int
    close: float
    quote_volume: Fraction
    taker_quote: Fraction


def klines(source: Source, checksum: Source, start: int, step: int, count: int) -> list[Hour]:
    out: list[Hour] = []
    for index, row in enumerate(archive_rows(source, checksum, count)):
        if index == 0 and row and row[0] == "open_time":
            require(
                len(row) == 12
                and all(value in allowed for value, allowed in zip(row, KLINE_HEADER, strict=True)),
                "native kline header and field identity",
            )
            continue
        require(len(row) == 12, "native twelve-field kline")
        instant = integer(row[0])
        require(
            instant == start + len(out) * step and integer(row[6]) == instant + step - 1,
            "complete contiguous millisecond candles",
        )
        prices = [decimal(value) for value in row[1:5]]
        require(all(price > 0 for price in prices), "positive OHLC")
        require(min(prices[0], prices[3]) >= prices[2], "OHLC low")
        require(max(prices[0], prices[3]) <= prices[1], "OHLC high")
        quote, taker = decimal(row[7]), decimal(row[10])
        require(quote >= 0 and 0 <= taker <= quote, "quote/taker volume bounds")
        volume, taker_base = decimal(row[5]), decimal(row[9])
        require(volume >= 0 and 0 <= taker_base <= volume, "base/taker volume bounds")
        integer(row[8])
        decimal(row[11])
        close = finite_float(prices[3])
        require(close > 0, "representable positive close")
        # Reject pathological exponents before constructing bounded rationals,
        # but never round the native quote/taker strings used by thresholds.
        finite_float(quote)
        finite_float(taker)
        out.append(Hour(instant, close, Fraction(quote), Fraction(taker)))
    require(len(out) == count, "complete native candle count")
    return out


@dataclass(frozen=True, slots=True)
class Funding:
    timestamp_ms: int
    rate: float
    mark: float


def funding(native: Source, checksum: Source, response: Source) -> list[Funding]:
    interval = 4 if native.symbol == "TRBUSDT" else 8
    expected_count = 744 // interval
    rates: dict[int, Decimal] = {}
    for index, row in enumerate(archive_rows(native, checksum, expected_count)):
        if index == 0 and row and row[0] == "calc_time":
            require(
                row == ["calc_time", "funding_interval_hours", "last_funding_rate"],
                "native funding header",
            )
            continue
        require(len(row) == 3, "native funding width")
        instant = integer(row[0])
        expected = START + len(rates) * interval * HOUR
        require(
            expected <= instant <= expected + 15
            and instant not in rates
            and integer(row[1]) == interval,
            "complete native funding census and cadence",
        )
        rates[instant] = decimal(row[2])
    require(len(rates) == expected_count, "native funding count")
    raw = json_value(response.read())
    rows = sequence(raw)
    require(len(rows) == expected_count, "REST funding count")
    out: list[Funding] = []
    for item, (instant, rate) in zip(rows, rates.items(), strict=True):
        event = mapping(item)
        fields(event, "symbol fundingTime fundingRate markPrice rateType")
        require(event.get("symbol") == native.symbol, "funding instrument")
        require(
            type(event.get("fundingTime")) is int and event["fundingTime"] == instant,
            "funding time",
        )
        require(decimal(event.get("fundingRate")) == rate, "native/REST funding rate identity")
        mark = decimal(event.get("markPrice"))
        require(mark > 0, "positive actual settlement mark")
        actual_mark = finite_float(mark)
        require(actual_mark > 0, "representable settlement mark")
        out.append(Funding(instant, finite_float(rate), actual_mark))
    return out
