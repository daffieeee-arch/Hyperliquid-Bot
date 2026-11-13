"""Synthetic source corruption, funding clocks and quarantined-grid wiring."""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from research import two_models_io as source_io
from research.daily_momentum import DailyConfig, evaluate
from research.harness.errors import IntegrityError

BTC = "BTCUSDT"
START = 1_704_067_200_000
MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
KLINE_HEADER = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]


def native(
    tmp_path: Path,
    rows: list[list[str]],
    *,
    name: str = "BTCUSDT-1m-2024-01.zip",
    role: str = "trade_minute",
) -> dict[str, Any]:
    text = io.StringIO(newline="")
    csv.writer(text).writerows(rows)
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(name.removesuffix(".zip") + ".csv", text.getvalue())
    checksum = path.with_name(path.name + ".CHECKSUM")
    checksum.write_text(f"{source_io.sha(path.read_bytes())}  {path.name}\n", encoding="ascii")
    record = source_io.record(path)
    check = source_io.record(checksum)
    return record | {
        "month": "2024-01",
        "role": role,
        **{"checksum_" + key: value for key, value in check.items()},
    }


def minute_row(index: int) -> list[str]:
    instant = START + index * MINUTE
    return [
        str(instant),
        "100",
        "102",
        "99",
        "101",
        "1",
        str(instant + MINUTE - 1),
        "100",
        "1",
        "0.5",
        "50",
        "0",
    ]


@pytest.mark.parametrize("header", [False, True])
def test_native_trade_candles_preserve_close_clock_and_distinct_ohlc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, header: bool
) -> None:
    monkeypatch.setattr(source_io, "month_clock", lambda month: (START, START + 3 * MINUTE))
    rows = [minute_row(index) for index in range(3)]
    source = native(tmp_path, [KLINE_HEADER, *rows] if header else rows)
    values = source_io.candles(source, MINUTE)
    assert [(bar.open_ms, bar.close, bar.low, bar.high) for bar in values] == [
        (START + index * MINUTE, 101.0, 99.0, 102.0) for index in range(3)
    ]


@pytest.mark.parametrize(
    "column,value", [(2, "100"), (3, "102"), (4, "NaN"), (1, "0"), (6, str(START + 2 * MINUTE))]
)
def test_native_bad_price_bounds_or_close_availability_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, column: int, value: str
) -> None:
    monkeypatch.setattr(source_io, "month_clock", lambda month: (START, START + 3 * MINUTE))
    rows = [minute_row(index) for index in range(3)]
    rows[1][column] = value
    with pytest.raises(IntegrityError):
        source_io.candles(native(tmp_path, rows), MINUTE)


def test_only_explicit_mark_gap_mode_can_retain_missing_native_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_io, "month_clock", lambda month: (START, START + 3 * MINUTE))
    source = native(tmp_path, [minute_row(0), minute_row(2)], role="mark_minute")
    with pytest.raises(IntegrityError, match="contiguous"):
        source_io.candles(source, MINUTE)
    assert [r.open_ms for r in source_io.candles(source, MINUTE, missing=True)] == [
        START,
        START + 2 * MINUTE,
    ]


def test_missing_final_candle_is_not_mistaken_for_complete_month(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_io, "month_clock", lambda month: (START, START + 3 * MINUTE))
    with pytest.raises(IntegrityError, match="complete native"):
        source_io.candles(native(tmp_path, [minute_row(0), minute_row(1)]), MINUTE)


def test_timestamp_duplicates_fail_even_when_missing_marks_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_io, "month_clock", lambda month: (START, START + 3 * MINUTE))
    with pytest.raises(IntegrityError, match="clock"):
        source_io.candles(native(tmp_path, [minute_row(0), minute_row(0)]), MINUTE, missing=True)


def test_valid_source_hash_cannot_replace_official_checksum_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_io, "month_clock", lambda month: (START, START + MINUTE))
    source = native(tmp_path, [minute_row(0)])
    path = Path(source["checksum_path"])
    path.write_text(f"{'0' * 64}  {Path(source['path']).name}\n", encoding="ascii")
    source.update({"checksum_" + key: value for key, value in source_io.record(path).items()})
    with pytest.raises(IntegrityError, match="official archive checksum"):
        source_io.candles(source, MINUTE)


def response(
    tmp_path: Path, name: str, rows: list[dict[str, Any]], *, venue: str, request: dict[str, Any]
) -> dict[str, Any]:
    path = tmp_path / name
    path.write_text(json.dumps(rows), encoding="utf-8")
    return source_io.record(path) | {"rows": len(rows), "venue": venue, "request": request}


def funding_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, change: str | None = None
) -> dict[str, Any]:
    end = START + 16 * HOUR
    monkeypatch.setattr(source_io, "START", START)
    monkeypatch.setattr(source_io, "END", end)
    monkeypatch.setattr(source_io, "month_clock", lambda month: (START, end))
    times = [START - 8 * HOUR, START + 15, START + 8 * HOUR + 17, end, end + 8 * HOUR]
    rates = ["0.1", "0.00010000", "-0.00020000", "0.0003", "0"]
    rows: list[dict[str, Any]] = [
        {
            "symbol": BTC,
            "fundingTime": instant,
            "fundingRate": rate,
            "markPrice": "" if index == 0 else str(100 + index),
        }
        for index, (instant, rate) in enumerate(zip(times, rates, strict=True))
    ]
    if change == "time":
        rows[1]["fundingTime"] += 1
    elif change == "rate":
        rows[1]["fundingRate"] = "0.00011"
    elif change == "empty_mark":
        rows[1]["markPrice"] = ""
    elif change == "zero_mark":
        rows[1]["markPrice"] = "0"
    elif change == "symbol":
        rows[1]["symbol"] = "ETHUSDT"
    elif change == "duplicate":
        rows.insert(2, dict(rows[1]))
    elif change == "no_pad":
        rows.pop()
    request = {"symbol": BTC, "startTime": 1_577_836_800_000, "endTime": end + DAY, "limit": 1000}
    rest = response(tmp_path, "funding.json", rows, venue="BINANCE_USDM", request=request)
    census = native(
        tmp_path,
        [
            ["calc_time", "funding_interval_hours", "last_funding_rate"],
            [str(times[1]), "8", rates[1]],
            [str(times[2]), "8", rates[2]],
        ],
        name="BTCUSDT-fundingRate-2024-01.zip",
        role="funding",
    )
    return {"funding_sources": [rest], "daily_sources": [census]}


def test_funding_matches_exact_native_ms_rates_and_settlement_price_not_trade_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = funding_fixture(tmp_path, monkeypatch)
    values, hl = source_io.funding_censuses(body)
    assert values == {
        START + 15: (0.0001, 101),
        START + 8 * HOUR + 17: (-0.0002, 102),
        START + 16 * HOUR: (0.0003, 103),
    }
    assert not hl
    assert START not in values  # The native fifteen-millisecond offset is retained.


@pytest.mark.parametrize(
    "change", ["time", "rate", "empty_mark", "zero_mark", "symbol", "duplicate", "no_pad"]
)
def test_funding_corruption_or_missing_primary_settlement_mark_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    with pytest.raises(IntegrityError):
        source_io.funding_censuses(funding_fixture(tmp_path, monkeypatch, change=change))


def test_hl_funding_slots_retain_actual_offsets_and_cover_partial_terminal_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = funding_fixture(tmp_path, monkeypatch)
    times = [START + 15, START + HOUR + 21, START + 2 * HOUR + 7]
    rows = [{"coin": "BTC", "time": t, "fundingRate": "0.00001"} for t in times]
    body["funding_sources"].append(
        response(
            tmp_path,
            "hl.json",
            rows,
            venue="HYPERLIQUID",
            request={"coin": "BTC", "startTime": START, "endTime": START + 2 * HOUR + MINUTE},
        )
    )
    assert source_io.funding_censuses(body)[1] == tuple(times)


def test_hl_missing_actual_hour_is_not_zero_padded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = funding_fixture(tmp_path, monkeypatch)
    rows = [{"coin": "BTC", "time": START + 15, "fundingRate": "0"}]
    body["funding_sources"].append(
        response(
            tmp_path,
            "hl-gap.json",
            rows,
            venue="HYPERLIQUID",
            request={"coin": "BTC", "startTime": START, "endTime": START + 2 * HOUR},
        )
    )
    with pytest.raises(IntegrityError, match="complete actual hourly"):
        source_io.funding_censuses(body)


def test_metadata_hash_binding_refuses_same_path_after_contents_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "inventory.json"
    path.write_text('{"sources":[]}', encoding="utf-8")
    monkeypatch.setattr(source_io, "INPUT_PINS", {"fixture": source_io.sha(path.read_bytes())})
    assert source_io.bound_metadata({"fixture": path}) == {"fixture": {"sources": []}}
    path.write_text('{"sources":["different"]}', encoding="utf-8")
    with pytest.raises(IntegrityError, match="registered inventory"):
        source_io.bound_metadata({"fixture": path})


@pytest.mark.parametrize("change", ["exposure", "plan"])
def test_index_cannot_claim_fresh_oos_or_silently_change_registered_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    plan = tmp_path / "plan.txt"
    plan.write_bytes(b"registered fixture plan")
    monkeypatch.setattr(source_io, "PLAN", plan)
    body = {
        "schema": "two-model-source-index-v1",
        "study_ids": ["daily-momentum-20261113-v1", "micro-flow-20261113-v1"],
        "plan_sha256": source_io.sha(plan.read_bytes()),
        "inventories": {},
        "daily_sources": [],
        "captures": [],
        "funding_sources": [],
        "data_status": "exposed_retrospective_no_fresh_oos",
    }
    if change == "exposure":
        body["data_status"] = "fresh_oos"
    else:
        plan.write_bytes(b"amended after registration")
    with pytest.raises(IntegrityError, match=r"exposure identity|registered plan"):
        source_io.check_index(body)


def test_daily_source_wiring_quarantines_known_missing_mark_day_without_price_imputation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_io, "months", lambda: ("2024-01",))
    monkeypatch.setattr(source_io, "month_clock", lambda month: (START, START + 2 * DAY))
    body = {
        "daily_sources": [
            {"month": month, "role": role}
            for month, role in (
                ("2023-12", "hourly"),
                ("2024-01", "hourly"),
                ("2024-01", "trade_minute"),
                ("2024-01", "mark_minute"),
            )
        ]
    }
    missing_close = START + DAY + 2 * MINUTE

    def candles(row: dict[str, Any], step: int, *, missing: bool = False) -> list[source_io.Candle]:
        if row["role"] == "hourly":
            indexes = range(-80, 0) if row["month"] == "2023-12" else range(48)
            return [
                source_io.Candle(START + i * HOUR, 100 * math.exp(i * 0.001), 90, 110)
                for i in indexes
            ]
        return [
            source_io.Candle(START + i * MINUTE, 100, 100, 100)
            for i in range(2880)
            if not (row["role"] == "mark_minute" and START + (i + 1) * MINUTE == missing_close)
        ]

    monkeypatch.setattr(source_io, "candles", candles)
    monkeypatch.setattr(
        source_io,
        "check_index",
        lambda body, verify_raw=False: {
            "bad_days": {"bad_days": [{"date_utc": "2024-01-02"}]},
        },
    )
    event = START + HOUR + 17
    monkeypatch.setattr(source_io, "funding_censuses", lambda body: ({event: (0.001, 108)}, ()))
    month, frames, hourly, extremes, eligible = next(source_io.daily_months(body))
    by_time = {frame.timestamp_ms: frame for frame in frames}
    assert len(frames) == 2882
    assert by_time[missing_close].marks == {}
    assert missing_close not in extremes
    assert by_time[event].marks == {BTC: 108}
    assert by_time[event].funding_rates == {BTC: 0.001}
    assert by_time[event].execution_prices == {}
    assert by_time[START + HOUR + MINUTE].marks == {BTC: 100}
    assert eligible == frozenset({START // DAY})
    result = evaluate(
        DailyConfig("fixture", 24, False, False),
        source_io.daily_spec(month, frames, 10000),
        frames,
        hourly,
        extremes,
        eligible_days=eligible,
    )
    assert result["counts"]["calendar_days"] == 2
    assert result["counts"]["unavailable_data_days"] == 1
    assert result["base"]["complete_episodes"] == 1
    assert all(
        not point["positions"]
        for point in result["base"]["ledger"]
        if point["timestamp_ms"] >= START + DAY
    )


def test_unregistered_mark_hole_cannot_be_silently_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise the source iterator's hole admission without requiring an enormous
    # native month or reading any runtime store.
    monkeypatch.setattr(source_io, "months", lambda: ("2024-01",))
    monkeypatch.setattr(source_io, "month_clock", lambda month: (START, START + DAY))
    body = {
        "daily_sources": [
            {"month": month, "role": role}
            for month, role in (
                ("2023-12", "hourly"),
                ("2024-01", "hourly"),
                ("2024-01", "trade_minute"),
                ("2024-01", "mark_minute"),
            )
        ]
    }

    def candles(row: dict[str, Any], step: int, *, missing: bool = False) -> list[source_io.Candle]:
        if row["role"] == "mark_minute":
            return [source_io.Candle(START, 100, 100, 100)]
        return [source_io.Candle(START + i * step, 100, 100, 100) for i in range(DAY // step)]

    monkeypatch.setattr(source_io, "candles", candles)
    monkeypatch.setattr(
        source_io,
        "check_index",
        lambda body, verify_raw=False: {
            "bad_days": {"bad_days": []},
        },
    )
    monkeypatch.setattr(source_io, "funding_censuses", lambda body: ({}, ()))
    with pytest.raises(IntegrityError, match="unregistered missing mark day"):
        next(source_io.daily_months(deepcopy(body)))


@pytest.mark.parametrize(
    "index,wrong_fold",
    [
        (0, "diagnostic"),
        (1, "development"),
        (8, "validation"),
        (0, "metadata_removed"),
        (0, "metadata_wrong_path"),
    ],
)
def test_capture_fold_and_metadata_are_bound_to_original_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, index: int, wrong_fold: str
) -> None:
    plan = tmp_path / "plan.txt"
    plan.write_bytes(b"registered chronological folds")
    monkeypatch.setattr(source_io, "PLAN", plan)
    monkeypatch.setattr(source_io, "INPUT_PINS", {"fixture": "0" * 64})
    selected = [
        {"month": month, "role": role}
        for month in source_io.months()
        for role in ("hourly", "trade_minute", "mark_minute", "funding")
    ] + [{"month": "2023-12", "role": "hourly"}]
    names = ["20260918-a", "20260923-b", *[f"20260924-{i}" for i in range(6)]]
    older = [{"run": "/synthetic/" + name, "parts": []} for name in names]
    recent = [{"run_path": f"/synthetic/20261110-{i}", "parts": []} for i in range(3)]
    qualified = {
        "daily": {"sources": selected},
        "funding": {"sources": []},
        "older": older,
        "recent": recent,
    }
    monkeypatch.setattr(source_io, "bound_metadata", lambda paths: qualified)
    monkeypatch.setattr(source_io, "read_record", lambda row, maximum=8 << 20: b"{}")

    def metadata(origin: str, item: dict[str, Any]) -> list[dict[str, Any]]:
        directory = item["run"] if origin == "older" else item["run_path"]
        return [
            {"path": str(Path(directory) / leaf), "bytes": 2, "sha256": "1" * 64}
            for leaf in ("capture-claim.json", "capture-health.json")
        ]

    monkeypatch.setattr(source_io, "capture_metadata", metadata)
    captures: list[dict[str, Any]] = [
        {"name": name, "parts": [], "metadata": [], "group": group}
        for name, group in [
            (names[0], "development"),
            (names[1], "validation"),
            *[(name, "diagnostic") for name in names[2:]],
            *[(f"20261110-{i}", "recent_diagnostic") for i in range(3)],
        ]
    ]
    for capture, (origin, item) in zip(
        captures, [("older", r) for r in older] + [("recent", r) for r in recent], strict=True
    ):
        capture["metadata"] = metadata(origin, item)
    body: dict[str, Any] = {
        "schema": "two-model-source-index-v1",
        "study_ids": ["daily-momentum-20261113-v1", "micro-flow-20261113-v1"],
        "plan_sha256": source_io.sha(plan.read_bytes()),
        "inventories": {
            "fixture": {"path": "/synthetic/inventory.json", "bytes": 2, "sha256": "0" * 64}
        },
        "daily_sources": selected,
        "captures": captures,
        "funding_sources": [],
        "data_status": "exposed_retrospective_no_fresh_oos",
    }
    assert source_io.check_index(body, verify_raw=False) is qualified
    if wrong_fold == "metadata_removed":
        body["captures"][index]["metadata"] = []
    elif wrong_fold == "metadata_wrong_path":
        body["captures"][index]["metadata"][0]["path"] = "/other/capture-claim.json"
    else:
        body["captures"][index]["group"] = wrong_fold
    with pytest.raises(IntegrityError, match=r"capture fold|capture metadata binding"):
        source_io.check_index(body, verify_raw=False)


@pytest.mark.parametrize("omit", [START + 15, START + 8 * HOUR + 17])
def test_jointly_missing_native_and_rest_funding_slot_cannot_certify_zero_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, omit: int
) -> None:
    body = funding_fixture(tmp_path, monkeypatch)
    source = body["funding_sources"][0]
    rows = [row for row in json.loads(source_io.read_record(source)) if row["fundingTime"] != omit]
    path = Path(source["path"])
    path.write_text(json.dumps(rows), encoding="utf-8")
    source.update(source_io.record(path) | {"rows": len(rows)})
    times_rates = [(START + 15, "0.0001"), (START + 8 * HOUR + 17, "-0.0002")]
    body["daily_sources"] = [
        native(
            tmp_path,
            [
                ["calc_time", "funding_interval_hours", "last_funding_rate"],
                *[[str(instant), "8", rate] for instant, rate in times_rates if instant != omit],
            ],
            name="BTCUSDT-fundingRate-2024-01.zip",
            role="funding",
        )
    ]
    with pytest.raises(IntegrityError, match=r"funding|eight.hour|8.hour|slots"):
        source_io.funding_censuses(body)


@pytest.mark.parametrize("leaf", ["capture-claim.json", "capture-health.json"])
def test_original_old_capture_metadata_hashes_cannot_be_rebound_after_mutation(
    tmp_path: Path,
    leaf: str,
) -> None:
    claim = tmp_path / "capture-claim.json"
    health = tmp_path / "capture-health.json"
    claim.write_text('{"coin":"BTC"}', encoding="utf-8")
    health.write_text('{"integrity_events":0}', encoding="utf-8")
    item = {
        "run": str(tmp_path),
        "claim_sha256": source_io.sha(claim.read_bytes()),
        "health_sha256": source_io.sha(health.read_bytes()),
    }
    original = source_io.capture_metadata("older", item)
    assert [Path(row["path"]).name for row in original] == [claim.name, health.name]
    (tmp_path / leaf).write_text('{"changed_after_inventory":true}', encoding="utf-8")
    with pytest.raises(IntegrityError, match="original capture claim/health"):
        source_io.capture_metadata("older", item)


def recent_capture(tmp_path: Path) -> dict[str, Any]:
    claim = tmp_path / "capture-claim.json"
    health = tmp_path / "capture-health.json"
    claim.write_text('{"coin":"BTC"}', encoding="utf-8")
    health.write_text('{"closed":true}', encoding="utf-8")
    manifest = tmp_path / "publication-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "public-collector-closed-chunk-v1",
                "files": {path.name: source_io.record(path) for path in (claim, health)},
            }
        ),
        encoding="utf-8",
    )
    digest = source_io.sha(manifest.read_bytes())
    (tmp_path / "COMPLETE.json").write_text(
        json.dumps(
            {
                "closed_chunk_verified": True,
                "manifest_sha256": digest,
                "research_ready": True,
            }
        ),
        encoding="utf-8",
    )
    return {"run_path": str(tmp_path), "manifest_sha256": digest, "research_ready": True}


@pytest.mark.parametrize(
    "defect",
    [
        "linked_claim",
        "manifest",
        "unverified_complete",
        "wrong_manifest_receipt",
        "changed_readiness",
    ],
)
def test_recent_publication_requires_original_manifest_linked_metadata_and_closed_receipt(
    tmp_path: Path,
    defect: str,
) -> None:
    item = recent_capture(tmp_path)
    records = source_io.capture_metadata("recent", item)
    assert [Path(row["path"]).name for row in records] == [
        "capture-claim.json",
        "capture-health.json",
        "COMPLETE.json",
        "publication-manifest.json",
    ]
    if defect == "linked_claim":
        (tmp_path / "capture-claim.json").write_text('{"coin":"ETH"}', encoding="utf-8")
    elif defect == "manifest":
        path = tmp_path / "publication-manifest.json"
        value = json.loads(path.read_text())
        value["changed_after_inventory"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        path = tmp_path / "COMPLETE.json"
        value = json.loads(path.read_text())
        if defect == "unverified_complete":
            value["closed_chunk_verified"] = False
        elif defect == "wrong_manifest_receipt":
            value["manifest_sha256"] = "0" * 64
        else:
            value["research_ready"] = False
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(IntegrityError):
        source_io.capture_metadata("recent", item)


def test_missing_terminal_settlement_cannot_hide_behind_later_rest_pad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = funding_fixture(tmp_path, monkeypatch)
    row = body["funding_sources"][0]
    path = Path(row["path"])
    values = json.loads(path.read_text())
    values = [item for item in values if item["fundingTime"] != source_io.END]
    path.write_text(json.dumps(values))
    row.update(source_io.record(path), rows=len(values))
    with pytest.raises(IntegrityError, match="complete actual terminal funding slot"):
        source_io.funding_censuses(body)


def test_terminal_settlement_after_exit_is_qualified_without_flooring_into_payment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = funding_fixture(tmp_path, monkeypatch)
    row = body["funding_sources"][0]
    path = Path(row["path"])
    values = json.loads(path.read_text())
    for item in values:
        if item["fundingTime"] == source_io.END:
            item["fundingTime"] += 15
    path.write_text(json.dumps(values))
    row.update(source_io.record(path), rows=len(values))
    admitted, _ = source_io.funding_censuses(body)
    assert source_io.END not in admitted
    assert source_io.END + 15 not in admitted
    assert len(admitted) == 2
