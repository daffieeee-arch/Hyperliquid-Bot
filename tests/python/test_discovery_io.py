"""Bounded, synthetic native OI archives; no durable market data is opened."""

from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from research.discovery import MINUTE
from research.discovery_io import METRICS_HEADER, START, oi_values
from research.harness.errors import IntegrityError
from research.volume_repair_io import Source, sha256


def native_rows() -> list[list[str]]:
    return [
        [
            dt.datetime.fromtimestamp((START + index * 5 * MINUTE) / 1000, dt.UTC).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "BTCUSDT",
            str(1000 + index),
            "100000000",
            "1",
            "1",
            "1",
            "1",
        ]
        for index in range(288)
    ]


def archive_sources(
    tmp_path: Path,
    rows: list[list[str]],
    *,
    header: tuple[str, ...] = METRICS_HEADER,
    members: tuple[str, ...] = ("BTCUSDT-metrics-2025-01-01.csv",),
    checksum_text: str | None = None,
    empty_csv: bool = False,
) -> tuple[Source, Source]:
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    if not empty_csv:
        writer.writerow(header)
        writer.writerows(rows)
    path = tmp_path / "BTCUSDT-metrics-2025-01-01.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for member in members:
            archive.writestr(member, text.getvalue())
    content = path.read_bytes()
    checksum_path = path.with_name(path.name + ".CHECKSUM")
    checksum_path.write_text(checksum_text or f"{sha256(content)}  {path.name}\n", encoding="ascii")
    checksum = checksum_path.read_bytes()
    return (
        Source("BTCUSDT", "oi", path, sha256(content), len(content)),
        Source("BTCUSDT", "oi_checksum", checksum_path, sha256(checksum), len(checksum)),
    )


def test_native_oi_preserves_complete_grid_and_base_units(tmp_path: Path) -> None:
    source, checksum = archive_sources(tmp_path, native_rows())
    values = oi_values(source, checksum, START)
    assert list(values) == [START + index * 5 * MINUTE for index in range(288)]
    assert values[START] == 1000.0
    assert values[START + 287 * 5 * MINUTE] == 1287.0
    assert START + 288 * 5 * MINUTE not in values


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        (0, "2025-01-01 00:00:00", "contiguous UTC"),
        (0, "2025-01-01 00:11:00", "contiguous UTC"),
        (1, "ETHUSDT", "contiguous UTC"),
        (2, "NaN", "positive native"),
        (2, "inf", "positive native"),
        (2, "0", "positive native"),
        (2, "-1", "positive native"),
    ],
)
def test_native_oi_rejects_bad_snapshot(
    tmp_path: Path, column: int, value: str, message: str
) -> None:
    rows = native_rows()
    rows[1][column] = value
    source, checksum = archive_sources(tmp_path, rows)
    with pytest.raises(IntegrityError, match=message):
        oi_values(source, checksum, START)


@pytest.mark.parametrize("count", [0, 287, 289])
def test_native_oi_rejects_incomplete_or_excess_daily_grid(tmp_path: Path, count: int) -> None:
    rows = native_rows()
    rows = rows[:count] if count <= 288 else [*rows, rows[-1]]
    source, checksum = archive_sources(tmp_path, rows)
    with pytest.raises(IntegrityError, match=r"count|bounded native OI rows"):
        oi_values(source, checksum, START)


@pytest.mark.parametrize("empty", [False, True])
def test_native_oi_rejects_header_and_empty_member(tmp_path: Path, empty: bool) -> None:
    source, checksum = archive_sources(
        tmp_path, native_rows(), header=tuple(reversed(METRICS_HEADER)), empty_csv=empty
    )
    with pytest.raises(IntegrityError, match="native OI header"):
        oi_values(source, checksum, START)


@pytest.mark.parametrize(
    "members",
    [
        (),
        ("wrong.csv",),
        ("../BTCUSDT-metrics-2025-01-01.csv",),
        ("BTCUSDT-metrics-2025-01-01.csv", "extra.csv"),
    ],
)
def test_native_oi_rejects_wrong_archive_members(tmp_path: Path, members: tuple[str, ...]) -> None:
    source, checksum = archive_sources(tmp_path, native_rows(), members=members)
    with pytest.raises(IntegrityError, match=r"single OI|bounded native OI"):
        oi_values(source, checksum, START)


@pytest.mark.parametrize(
    "checksum_text",
    ["0" * 64 + "  BTCUSDT-metrics-2025-01-01.zip\n", "incomplete\n", "a b c\n"],
)
def test_native_oi_rejects_official_checksum_mismatch(tmp_path: Path, checksum_text: str) -> None:
    source, checksum = archive_sources(tmp_path, native_rows(), checksum_text=checksum_text)
    with pytest.raises(IntegrityError, match="OI official checksum"):
        oi_values(source, checksum, START)


@pytest.mark.parametrize("which", ["archive", "checksum"])
def test_native_oi_rejects_changed_pinned_source(tmp_path: Path, which: str) -> None:
    source, checksum = archive_sources(tmp_path, native_rows())
    if which == "archive":
        source = replace(source, sha256="0" * 64)
    else:
        checksum = replace(checksum, size=checksum.size + 1)
    with pytest.raises(IntegrityError, match="source identity"):
        oi_values(source, checksum, START)
