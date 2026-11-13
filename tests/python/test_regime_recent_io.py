"""Synthetic source-admission counterexamples; no market strategy evaluation."""

from __future__ import annotations

import csv
import hashlib
import io as text_io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from research import regime_recent_io as source_io
from research.harness.errors import IntegrityError
from research.regime_io import midnight
from research.two_models_io import BTC, DAY, HOUR, MINUTE
from research.volume_repair_io import KLINE_HEADER

START = midnight("2026-11-01")
END = START + 2 * DAY


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def record(path: Path, url: str, status: int = 200) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "url": url,
        "method": "GET",
        "status": status,
        "truncated": False,
        "path": str(path),
        "bytes": len(raw),
        "sha256": sha(raw),
    }


def repin(path: Path, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.dumps(body).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(source_io, "INPUT_SHA256", sha(raw))


def archive(root: Path, role: str, period: str, contents: list[list[str]]) -> dict[str, Any]:
    url = source_io._official_url(role, period)
    directory = root / role / period
    directory.mkdir(parents=True)
    path = directory / url.rsplit("/", 1)[-1]
    stream = text_io.StringIO(newline="")
    csv.writer(stream).writerows(contents)
    payload = stream.getvalue().encode()
    with zipfile.ZipFile(path, "w") as zipped:
        zipped.writestr(path.name.removesuffix(".zip") + ".csv", payload)
    checksum = path.with_suffix(".zip.CHECKSUM")
    checksum.write_text(f"{sha(path.read_bytes())}  {path.name}\n")
    first, last = source_io.period_clock(period)
    return {
        "role": role,
        "period": period,
        "start_ms": first,
        "end_ms": last,
        "official_url": url,
        "official_checksum_url": url + ".CHECKSUM",
        "status": "qualified",
        "existing_nas_file_reused": False,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha(path.read_bytes()),
        "checksum_path": str(checksum),
        "checksum_bytes": checksum.stat().st_size,
        "checksum_sha256": sha(checksum.read_bytes()),
        "checksum_request": record(checksum, url + ".CHECKSUM"),
        "download_request": record(path, url),
        "zip_member": path.name.removesuffix(".zip") + ".csv",
        "uncompressed_bytes": len(payload),
    }


def candle_rows(role: str, period: str) -> list[list[str]]:
    first, last = source_io.period_clock(period)
    step = HOUR if role == "hourly" else MINUTE
    rows = [[next(iter(allowed)) for allowed in KLINE_HEADER]]
    for instant in range(first, last, step):
        # Exact decimal closes align H1 and 1m, independent of binary floats.
        minutes = (instant + step - START) // MINUTE
        value = f"{100 + minutes // 10000}.{minutes % 10000:04d}"
        rows.append(
            [
                str(instant),
                value,
                value,
                value,
                value,
                "1",
                str(instant + step - 1),
                "1",
                "1",
                "0",
                "0",
                "0",
            ]
        )
    return rows


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    monkeypatch.setattr(source_io, "START", START)
    monkeypatch.setattr(source_io, "END", END)
    monkeypatch.setattr(source_io, "REST_ONLY_START", START + DAY)
    monkeypatch.setattr(source_io, "WARMUP_PERIOD", "2026-10")
    monkeypatch.setattr(source_io, "PERIODS", ("2026-11-01", "2026-11-02"))
    # A reduced synthetic matrix exercises the native-witness/open-period split.
    monkeypatch.setattr(source_io, "ARCHIVE_FUNDING_PERIODS", ("2026-11-01",))
    warmup = archive(tmp_path, "hourly", "2026-10", candle_rows("hourly", "2026-10"))
    warmup.update({"symbol": BTC, "month": "2026-10"})
    monkeypatch.setattr(source_io, "WARMUP_SHA256", warmup["sha256"])
    rows = []
    for period in source_io.PERIODS:
        first, last = source_io.period_clock(period)
        for role in source_io.ROLES:
            if role == "funding":
                if period in source_io.ARCHIVE_FUNDING_PERIODS:
                    native = [["calc_time", "funding_interval_hours", "last_funding_rate"]]
                    native.extend(
                        [[str(t + 15), "8", "0.0001"] for t in range(first, last, 8 * HOUR)]
                    )
                    rows.append(archive(tmp_path, role, period, native))
                else:
                    url = source_io._official_url(role, period)
                    zip_error = tmp_path / "zip404.xml"
                    checksum_error = tmp_path / "checksum404.xml"
                    for error in (zip_error, checksum_error):
                        error.write_bytes(b"<Error><Code>NoSuchKey</Code></Error>")
                    rows.append(
                        {
                            "role": role,
                            "period": period,
                            "start_ms": first,
                            "end_ms": last,
                            "official_url": url,
                            "official_checksum_url": url + ".CHECKSUM",
                            "status": "unavailable_official_archive_or_checksum",
                            "checksum_request": record(checksum_error, url + ".CHECKSUM", 404),
                            "zip_probe": record(zip_error, url, 404),
                        }
                    )
            else:
                rows.append(archive(tmp_path, role, period, candle_rows(role, period)))
    rates: list[dict[str, Any]] = [
        {
            "symbol": BTC,
            "fundingTime": t + 15,
            "fundingRate": "0.0001",
            "markPrice": "107",
            "rateType": "Regular",
        }
        for t in range(START - 8 * HOUR, END + 8 * HOUR + 1, 8 * HOUR)
    ]
    raw = tmp_path / "funding-rest.json"
    raw.write_text(json.dumps(rates))
    request = {
        "symbol": BTC,
        "startTime": START - 8 * HOUR,
        "endTime": END + 8 * HOUR + MINUTE,
        "limit": 1000,
    }
    url = "https://fapi.binance.com/fapi/v1/fundingRate?" + urlencode(request)
    body = {
        "schema": "locked-regime-recent-source-qualification-v1",
        "exposure": "EXPOSED_RECENT_DIAGNOSTIC_NOT_FRESH_OOS",
        "requested_start_ms": START,
        "requested_end_ms": END,
        "calendar_days": 2,
        "candidate_lock_sha256": source_io.CANDIDATE_LOCK_SHA256,
        "financial_scoring": False,
        "warmup_source": warmup,
        "sources": rows,
        "funding": {
            "request": request,
            "raw": record(raw, url),
            "rows": len(rates),
            "primary_rows": 6,
            "native_archive_records_matching_REST": 3,
            "REST_only_primary_native_events": [
                r for r in rates if START + DAY <= r["fundingTime"] < END
            ],
            "terminal_event": rates[-2],
        },
    }
    path = tmp_path / "input.json"
    repin(path, body, monkeypatch)
    return path, body


def replace_archive(row: dict[str, Any], change: Callable[[list[list[str]]], None]) -> None:
    path = Path(row["path"])
    with zipfile.ZipFile(path) as zipped:
        member = zipped.namelist()[0]
        rows = list(csv.reader(text_io.StringIO(zipped.read(member).decode())))
    change(rows)
    stream = text_io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    with zipfile.ZipFile(path, "w") as zipped:
        zipped.writestr(member, stream.getvalue())
    row.update({"bytes": path.stat().st_size, "sha256": sha(path.read_bytes())})
    row["download_request"] = record(path, row["official_url"])
    checksum = Path(row["checksum_path"])
    checksum.write_text(f"{row['sha256']}  {path.name}\n")
    row.update(
        {"checksum_bytes": checksum.stat().st_size, "checksum_sha256": sha(checksum.read_bytes())}
    )
    row["checksum_request"] = record(checksum, row["official_checksum_url"])


def replace_funding(body: dict[str, Any], change: Callable[[list[dict[str, Any]]], None]) -> None:
    old = body["funding"]["raw"]
    path = Path(old["path"])
    rows = json.loads(path.read_text())
    change(rows)
    path.write_text(json.dumps(rows))
    body["funding"]["raw"] = record(path, old["url"])


def test_projection_keeps_full_history_and_native_execution_funding_and_mark_identity(
    source: tuple[Path, dict[str, Any]],
) -> None:
    path, _ = source
    result = source_io.load(path)
    assert len(result.segments) == 1
    segment = result.segments[0]
    assert (segment.start_ms, segment.end_ms) == (START, END)
    assert segment.spec(1234).initial_equity == 1234
    assert len(segment.minute_extremes) == 2880
    assert len(result.hourly) == 792
    assert all(t in result.hourly for t in range(START - 720 * HOUR, END + 1, HOUR))
    by_time = {frame.timestamp_ms: frame for frame in segment.frames}
    funding = by_time[START + 15]
    assert funding.marks == {BTC: 107}
    assert funding.funding_rates == {BTC: 0.0001}
    assert not funding.execution_prices
    assert by_time[START + MINUTE].execution_prices == {BTC: 100.0001}
    assert by_time[START + DAY].execution_prices == {BTC: 100.144}
    assert END + 15 not in by_time
    assert result.source_summary["funding_primary_records"] == 6
    assert result.source_summary["funding_archive_witness_records"] == 3
    assert result.source_summary["funding_rest_only_records"] == 3
    assert result.source_summary["data_status"] == "exposed_recent_diagnostic_not_fresh_oos"


def test_changed_outer_input_cannot_reuse_the_source_pin(
    source: tuple[Path, dict[str, Any]],
) -> None:
    path, _ = source
    path.write_bytes(b"{}")
    with pytest.raises(IntegrityError, match="Pinned recent"):
        source_io.load(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("exposure", "fresh_oos"),
        ("candidate_lock_sha256", "0" * 64),
        ("financial_scoring", True),
        ("requested_end_ms", END - DAY),
    ],
)
def test_window_lock_and_exposure_cannot_be_relabeled(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    path, body = source
    body[field] = value
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match="Locked candidate"):
        source_io.load(path)


def test_changed_raw_archive_or_sidecar_is_rejected(source: tuple[Path, dict[str, Any]]) -> None:
    path, body = source
    sidecar = Path(body["sources"][0]["checksum_path"])
    sidecar.write_text("0" * 64 + "  unexpected.zip\n")
    with pytest.raises(IntegrityError, match="HTTP response"):
        source_io.load(path)


def test_official_sidecar_binds_native_filename_even_after_new_test_input_pin(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, body = source
    row = body["sources"][0]
    sidecar = Path(row["checksum_path"])
    sidecar.write_text(row["sha256"] + "  other.zip\n")
    row["checksum_bytes"], row["checksum_sha256"] = (
        sidecar.stat().st_size,
        sha(sidecar.read_bytes()),
    )
    row["checksum_request"] = record(sidecar, row["official_checksum_url"])
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match="official archive checksum"):
        source_io.load(path)


def test_source_matrix_cannot_drop_or_duplicate_a_role(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, body = source
    body["sources"][0] = dict(body["sources"][1])
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match="source-period matrix"):
        source_io.load(path)


def test_spot_or_mark_url_cannot_masquerade_as_a_trade_source(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, body = source
    body["sources"][0]["official_url"] = body["sources"][0]["official_url"].replace(
        "/futures/um/", "/spot/"
    )
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match="instrument/product"):
        source_io.load(path)


@pytest.mark.parametrize(
    "kind", ["missing_first", "missing_last", "reordered", "microsecond", "bad_ohlc"]
)
def test_native_clock_and_OHLC_corruption_are_not_repaired(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path, body = source
    row = next(r for r in body["sources"] if r["role"] == "mark_minute")

    def change(rows: list[list[str]]) -> None:
        if kind == "missing_first":
            rows.pop(1)
        elif kind == "missing_last":
            rows.pop()
        elif kind == "reordered":
            rows[1], rows[2] = rows[2], rows[1]
        elif kind == "microsecond":
            rows[1][0] = str(int(rows[1][0]) * 1000)
        else:
            rows[1][3] = "999"

    replace_archive(row, change)
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match=r"candle grid|candle count|candle OHLC"):
        source_io.load(path)


def test_hourly_close_must_equal_its_native_minute_trade_close(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, body = source
    row = next(r for r in body["sources"] if r["role"] == "hourly")

    def change(rows: list[list[str]]) -> None:
        rows[1][1:5] = ["101", "101", "101", "101"]

    replace_archive(row, change)
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match="hourly versus minute"):
        source_io.load(path)


@pytest.mark.parametrize("kind", ["rate", "time"])
def test_native_archive_and_REST_funding_match_exactly_without_time_flooring(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path, body = source
    row = next(r for r in body["sources"] if r["role"] == "funding" and r["status"] == "qualified")

    def change(rows: list[list[str]]) -> None:
        if kind == "rate":
            rows[1][2] = "0.0002"
        else:
            rows[1][0] = str(int(rows[1][0]) + 1)

    replace_archive(row, change)
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match="native timestamp/rate"):
        source_io.load(path)


@pytest.mark.parametrize(
    "kind",
    ["missing_primary", "missing_terminal", "duplicate", "zero_mark", "bad_symbol", "special"],
)
def test_REST_funding_census_and_settlement_identity_fail_closed(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path, body = source

    def change(rows: list[dict[str, Any]]) -> None:
        if kind == "missing_primary":
            rows.pop(4)
        elif kind == "missing_terminal":
            rows.pop(-2)
        elif kind == "duplicate":
            rows.insert(2, dict(rows[1]))
        elif kind == "zero_mark":
            rows[1]["markPrice"] = "0"
        elif kind == "bad_symbol":
            rows[1]["symbol"] = "ETHUSDT"
        else:
            rows[1]["rateType"] = "Special"

    replace_funding(body, change)
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match=r"funding slots|settlement mark|funding identity"):
        source_io.load(path)


def test_terminal_zero_ms_event_is_kept_before_the_native_terminal_close(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, body = source

    def change(rows: list[dict[str, Any]]) -> None:
        rows[-2]["fundingTime"] = END
        body["funding"]["terminal_event"] = dict(rows[-2])

    replace_funding(body, change)
    repin(path, body, monkeypatch)
    segment = source_io.load(path).segments[0]
    assert segment.frames[-1].timestamp_ms == END
    assert segment.frames[-1].funding_rates == {BTC: 0.0001}
    assert segment.frames[-1].marks == {BTC: 107}
    assert segment.frames[-1].execution_prices == {BTC: 100.288}


def test_daily_funding_404_is_not_silently_turned_into_an_archive(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, body = source
    absent = next(r for r in body["sources"] if r["status"] != "qualified")
    absent["status"] = "qualified"
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match="Missing daily funding disposition"):
        source_io.load(path)


def test_404_raw_evidence_is_rechecked(source: tuple[Path, dict[str, Any]]) -> None:
    path, body = source
    absent = next(r for r in body["sources"] if r["status"] != "qualified")
    Path(absent["zip_probe"]["path"]).write_bytes(b"changed")
    with pytest.raises(IntegrityError, match="HTTP response"):
        source_io.load(path)


def test_a_declared_REST_only_count_cannot_hide_another_source_missing_slot(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, body = source
    body["funding"]["REST_only_primary_native_events"] = []
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match="qualification agrees"):
        source_io.load(path)


def test_warmup_cannot_be_replaced_with_a_different_archive(
    source: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, body = source
    body["warmup_source"]["sha256"] = "0" * 64
    repin(path, body, monkeypatch)
    with pytest.raises(IntegrityError, match="warm-up identity"):
        source_io.load(path)
