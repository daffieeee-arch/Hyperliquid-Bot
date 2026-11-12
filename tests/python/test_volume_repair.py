"""Cash counterexamples and native-byte admission for the fixed volume repair."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import replace
from decimal import localcontext
from fractions import Fraction
from pathlib import Path

import pytest

import research.volume_repair as repair
from research.harness.errors import HarnessError, IntegrityError
from research.repaired_accounting import Frame, Instrument, ReplaySpec, Target, simulate
from research.volume_repair import (
    Window,
    candidates,
    frames,
    main,
    select_windows,
    summary,
    targets,
)
from research.volume_repair_io import (
    DECEMBER,
    HOUR,
    MINUTE,
    ROLES,
    START,
    STUDY_ID,
    SYMBOLS,
    Funding,
    Hour,
    Source,
    archive_rows,
    decimal,
    document,
    funding,
    klines,
    load_index,
    sha256,
    source_name,
)


def archive(
    root: Path, name: str, rows: list[list[str]], role: str = "hourly_jan", symbol: str = "BTCUSDT"
) -> tuple[Source, Source]:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerows(rows)
    path = root / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(name.removesuffix(".zip") + ".csv", stream.getvalue())
    raw = path.read_bytes()
    check = Path(str(path) + ".CHECKSUM")
    content = f"{sha256(raw)}  {name}\n".encode()
    check.write_bytes(content)
    return (
        Source(symbol, role, path, sha256(raw), len(raw)),
        Source(symbol, role + "_checksum", check, sha256(content), len(content)),
    )


def kline(instant: int, step: int = HOUR, price: str = "100") -> list[str]:
    return [
        str(instant),
        price,
        price,
        price,
        price,
        "1",
        str(instant + step - 1),
        "100",
        "1",
        "1",
        "60",
        "0",
    ]


def test_native_kline_schema_grid_and_official_checksum(tmp_path: Path) -> None:
    native, checksum = archive(
        tmp_path, "BTCUSDT-1h-2025-01.zip", [kline(START), kline(START + HOUR)]
    )
    result = klines(native, checksum, START, HOUR, 2)
    assert result == [
        Hour(START, 100, Fraction(100), Fraction(60)),
        Hour(START + HOUR, 100, Fraction(100), Fraction(60)),
    ]
    checksum.path.write_text("0" * 64 + " " + native.path.name)
    wrong = replace(
        checksum, sha256=sha256(checksum.path.read_bytes()), size=checksum.path.stat().st_size
    )
    with pytest.raises(IntegrityError, match="official archive checksum"):
        list(archive_rows(native, wrong, 2))


@pytest.mark.parametrize(
    "change", ["gap", "duplicate", "close_clock", "volume", "nan", "infinity", "overflow", "zero"]
)
def test_native_invalid_observation_refuses_whole_input(tmp_path: Path, change: str) -> None:
    rows = [kline(START), kline(START + HOUR)]
    if change == "gap":
        rows[1] = kline(START + 2 * HOUR)
    elif change == "duplicate":
        rows[1] = kline(START)
    elif change == "close_clock":
        rows[1][6] = str(START + 2 * HOUR)
    elif change == "volume":
        rows[1][10] = "101"
    elif change in ("nan", "infinity", "overflow", "zero"):
        value = {"nan": "NaN", "infinity": "Infinity", "overflow": "1e999", "zero": "0"}[change]
        rows[1][1:5] = [value] * 4
    native, checksum = archive(tmp_path, "BTCUSDT-1h-2025-01.zip", rows)
    with pytest.raises(IntegrityError):
        klines(native, checksum, START, HOUR, 2)


def test_source_changed_and_symlink_are_refused(tmp_path: Path) -> None:
    native, _ = archive(tmp_path, "BTCUSDT-1h-2025-01.zip", [kline(START)])
    original = native.path.read_bytes()
    native.path.write_bytes(original + b"changed")
    with pytest.raises(HarnessError, match="source identity"):
        native.read()
    link = tmp_path / "link"
    link.symlink_to(native.path)
    with pytest.raises(OSError):
        replace(native, path=link).read()


def funding_sources(
    tmp_path: Path, change: str = "", symbol: str = "BTCUSDT"
) -> tuple[Source, Source, Source]:
    interval = 4 if symbol == "TRBUSDT" else 8
    count = 744 // interval
    rows = [["calc_time", "funding_interval_hours", "last_funding_rate"]]
    response: list[dict[str, object]] = []
    for index in range(count):
        instant = START + index * interval * HOUR + (15 if index % 2 == 0 else 0)
        rows.append([str(instant), str(interval), "0.001"])
        response.append(
            {"symbol": symbol, "fundingTime": instant, "fundingRate": "0.001", "markPrice": "120"}
        )
    if change == "missing_native":
        rows.pop(20)
    elif change == "floor_rest":
        response[0]["fundingTime"] = START
    elif change == "bad_mark":
        response[0]["markPrice"] = "NaN"
    elif change == "rate":
        response[0]["fundingRate"] = "0.002"
    elif change == "missing_rest":
        response.pop()
    elif change == "cadence":
        rows[1][1] = "1"
    native, checksum = archive(
        tmp_path, f"{symbol}-fundingRate-2025-01.zip", rows, "funding_native", symbol
    )
    path = tmp_path / "response.json"
    content = json.dumps(response).encode()
    if change == "duplicate_key":
        content = content.replace(
            b'"markPrice": "120"', b'"markPrice": "NaN", "markPrice": "120"', 1
        )
    path.write_bytes(content)
    return native, checksum, Source(symbol, "funding_rest", path, sha256(content), len(content))


@pytest.mark.parametrize("symbol,count", [("BTCUSDT", 93), ("TRBUSDT", 186)])
def test_native_funding_census_preserves_jitter_and_actual_mark(
    tmp_path: Path, symbol: str, count: int
) -> None:
    result = funding(*funding_sources(tmp_path, symbol=symbol))
    assert len(result) == count
    assert result[0] == Funding(START + 15, 0.001, 120.0)
    assert result[1].timestamp_ms % HOUR == 0


@pytest.mark.parametrize(
    "change",
    [
        "missing_native",
        "floor_rest",
        "bad_mark",
        "rate",
        "missing_rest",
        "cadence",
        "duplicate_key",
    ],
)
def test_unknown_funding_or_transformed_event_times_are_refused(
    tmp_path: Path, change: str
) -> None:
    with pytest.raises(IntegrityError):
        funding(*funding_sources(tmp_path, change))


def test_duplicate_json_and_nonfinite_decimal_are_refused() -> None:
    with pytest.raises(IntegrityError, match="duplicate"):
        document(b'{"schema_version": 1, "schema_version": 2}')
    with pytest.raises(IntegrityError, match="finite"):
        decimal("NaN")


def feature_hours() -> list[Hour]:
    return [
        Hour(START + (index - 720) * HOUR, 100.0, Fraction(100), Fraction(50))
        for index in range(740)
    ]


def test_current_spike_is_excluded_from_median_and_positive_latency_is_fixed() -> None:
    hours = feature_hours()
    hours[720] = replace(hours[720], quote_volume=Fraction(500), taker_quote=Fraction(400))
    windows, counts = candidates("X", hours, START, START + 10 * HOUR)
    assert windows == [Window(START + 2 * HOUR, START + 6 * HOUR, "X", 1)]
    assert counts == {"spikes": 1, "busy": 0, "boundary": 0}
    assert targets(windows, START, START + 10 * HOUR) == [
        Target(START + HOUR, START + 2 * HOUR, {"X": 0.1}, False),
        Target(START + 5 * HOUR, START + 6 * HOUR, {"X": 0.0}, False),
    ]


@pytest.mark.parametrize("taker,direction", [(300.0, 1), (200.0, -1)])
def test_exact_inclusive_imbalance_boundary_is_not_lost_to_float_division(
    taker: float, direction: int
) -> None:
    hours = feature_hours()
    hours[720] = replace(hours[720], quote_volume=Fraction(500), taker_quote=Fraction(taker))
    windows, _ = candidates("X", hours, START, START + 10 * HOUR)
    assert len(windows) == 1
    assert windows[0].direction == direction


@pytest.mark.parametrize(
    "prior_low,prior_high,quote,taker,direction",
    [
        ("100.02", "100.02", "500.1", "300.06", 1),
        ("100.02", "100.02", "500.1", "200.04", -1),
        ("24.69", "24.69", "123.45", "74.070", 1),
        ("24.69", "24.69", "123.45", "49.380", -1),
        ("100.01", "100.03", "500.1", "300.06", 1),
        ("100.0199999999999999999999", "100.0200000000000000000001", "500.1", "300.06", 1),
        ("100.02", "100.02", "500.1", "300.059999999999999999999999999999", 0),
        ("100.02", "100.02", "500.1", "200.040000000000000000000000000001", 0),
        ("100.02", "100.02", "500.099999999999999999999999999999", "400", 0),
    ],
)
def test_native_decimal_volume_thresholds_preserve_exact_median_and_boundaries(
    tmp_path: Path,
    prior_low: str,
    prior_high: str,
    quote: str,
    taker: str,
    direction: int,
) -> None:
    december = [kline(DECEMBER + index * HOUR) for index in range(744)]
    for index, row in enumerate(december):
        row[7], row[10] = (prior_low if index % 2 == 0 else prior_high), "0"
    january = [kline(START + index * HOUR) for index in range(8)]
    for row in january:
        row[7], row[10] = prior_low, "0"
    january[0][7], january[0][10] = quote, taker
    dec_zip, dec_check = archive(tmp_path, "BTCUSDT-1h-2024-12.zip", december, "hourly_dec")
    jan_zip, jan_check = archive(tmp_path, "BTCUSDT-1h-2025-01.zip", january)
    # Exact selection must not depend on the caller's Decimal arithmetic context.
    with localcontext() as context:
        context.prec = 6
        parsed = klines(dec_zip, dec_check, DECEMBER, HOUR, 744)
        parsed += klines(jan_zip, jan_check, START, HOUR, 8)
        windows, counts = candidates("BTCUSDT", parsed, START, START + 8 * HOUR)
    assert parsed[744].quote_volume == Fraction(quote)
    assert parsed[744].taker_quote == Fraction(taker)
    assert counts["spikes"] == (1 if direction else 0)
    assert windows == (
        [Window(START + 2 * HOUR, START + 6 * HOUR, "BTCUSDT", direction)] if direction else []
    )


def test_busy_and_boundary_filters_do_not_use_future_returns() -> None:
    hours = feature_hours()
    for index in (720, 721, 725, 726, 738):
        hours[index] = replace(hours[index], quote_volume=Fraction(500), taker_quote=Fraction(100))
    windows, counts = candidates("X", hours, START, START + 20 * HOUR)
    assert windows == [
        Window(START + 2 * HOUR, START + 6 * HOUR, "X", -1),
        Window(START + 8 * HOUR, START + 12 * HOUR, "X", -1),
    ]
    assert counts == {"spikes": 5, "busy": 2, "boundary": 1}
    with pytest.raises(IntegrityError, match="contiguous"):
        candidates("X", hours[:10] + hours[11:])


def test_capacity_filter_ties_are_deterministic_and_exits_free_slots() -> None:
    windows = [Window(1, 5, f"X{index:02}", 1) for index in range(11)]
    accepted, refused = select_windows([*reversed(windows), Window(5, 9, "Y", -1)])
    assert [window.symbol for window in accepted] == [f"X{index:02}" for index in range(10)] + ["Y"]
    assert refused == 1
    with pytest.raises(IntegrityError, match="overlap"):
        select_windows([Window(1, 5, "X", 1), Window(2, 6, "X", -1)])


def test_terminal_full_holding_window_has_a_charged_close() -> None:
    windows = [Window(START + HOUR, START + 5 * HOUR, "X", 1)]
    execution = targets(windows, START, START + 5 * HOUR)
    assert len(execution) == 1
    market = [Frame(START + index * HOUR, {"X": 100.0}, {"X": 0.0}) for index in range(6)]
    result = simulate(
        ReplaySpec(START, START + 5 * HOUR, HOUR, 1000, (Instrument("X", "perp", 5, 2),)),
        market,
        execution,
    )
    assert len(result.trades) == 2
    assert result.final_equity == pytest.approx(999.86)
    assert result.points[-1].positions == {}
    with pytest.raises(IntegrityError, match="complete holding"):
        targets(windows, START, START + 4 * HOUR)


def test_asynchronous_funding_uses_actual_settlement_and_causal_other_marks() -> None:
    end = START + 2 * MINUTE
    hours = {s: [Hour(START - HOUR, 100, Fraction(0), Fraction(0))] for s in ("X", "Y")}
    marks = {
        s: [
            Hour(START, 100, Fraction(0), Fraction(0)),
            Hour(START + MINUTE, 900, Fraction(0), Fraction(0)),
        ]
        for s in ("X", "Y")
    }
    payments = {"X": [Funding(START + MINUTE + 15, 0.01, 120)], "Y": []}
    output = frames(hours, marks, payments, START, end)
    event = next(frame for frame in output if frame.timestamp_ms == START + MINUTE + 15)
    assert event.marks == {"X": 120, "Y": 100}
    assert event.funding_rates == {"X": 0.01, "Y": 0.0}
    assert event.execution_prices == {}
    assert output[0].marks == {}  # Flat initial frame does not invent a December minute mark.
    missing = {**marks, "Y": marks["Y"][1:]}
    with pytest.raises(IntegrityError, match="complete minute"):
        frames(hours, missing, payments, START, end)


def test_event_clock_retains_regular_marks_and_funding_entry_exit_order() -> None:
    clock = (0, 60_000, 60_015, 120_000)
    spec = ReplaySpec(0, 120_000, 60_000, 1000, (Instrument("X", "perp", 0, 0),), clock)
    market = [
        Frame(
            t, {"X": 120.0 if t == 60_015 else 100.0}, {"X": 0.01 if t in (60_000, 60_015) else 0.0}
        )
        for t in clock
    ]
    entering = simulate(spec, market, [Target(0, 60_000, {"X": 0.1})])
    assert entering.total_funding == pytest.approx(
        -1.2
    )  # Exact entry excludes 60_000, includes 60_015.
    exiting = simulate(spec, market, [Target(0, 0, {"X": 0.1}), Target(0, 60_000, {})])
    assert exiting.total_funding == pytest.approx(-1.0)
    with pytest.raises(IntegrityError, match="retain every regular"):
        simulate(
            replace(spec, event_times_ms=(0, 60_015, 120_000)),
            [market[0], market[2], market[3]],
            [],
        )


def test_execution_price_is_separate_from_mark_and_basis_is_not_slippage() -> None:
    spec = ReplaySpec(0, 60_000, 60_000, 1000, (Instrument("X", "perp", 5, 2),))
    market = [
        Frame(0, {"X": 110}, {"X": 0.0}, {"X": 100}),
        Frame(60_000, {"X": 100}, {"X": 0.0}, {"X": 100}),
    ]
    result = simulate(spec, market, [Target(0, 0, {"X": 0.1})])
    assert result.trades[0].quantity_delta == 1.0
    assert result.trades[0].mark == 110
    assert result.trades[0].reference_price == 100
    assert result.trades[0].fill_price == pytest.approx(100.02)
    assert result.total_cost == pytest.approx(0.14)
    assert result.final_equity == pytest.approx(999.86)
    assert result.points[0].equity == pytest.approx(1009.92999)
    with pytest.raises(IntegrityError, match="execution reference"):
        simulate(
            spec, [replace(market[0], execution_prices={}), market[1]], [Target(0, 0, {"X": 0.1})]
        )


def test_full_bound_index_and_fail_closed_cli(tmp_path: Path) -> None:
    sources = [
        {
            "symbol": symbol,
            "role": role,
            "path": str(tmp_path / source_name(symbol, role)),
            "sha256": "a" * 64,
            "bytes": 10,
        }
        for symbol in SYMBOLS
        for role in ROLES
    ]
    body = {"schema_version": 1, "study_id": STUDY_ID, "sources": sources}
    assert len(load_index(body)) == 351
    with pytest.raises(IntegrityError, match="complete bounded"):
        load_index({**body, "sources": sources[:-1]})
    with pytest.raises(IntegrityError, match="source role"):
        load_index({**body, "sources": [sources[0], *sources[1:-1], sources[0]]})
    source = tmp_path / "input.json"
    source.write_text(json.dumps(body))
    out = tmp_path / "out"
    assert (
        main(
            [
                "run",
                "--input",
                str(source),
                "--expected-input-sha256",
                "0" * 64,
                "--source-commit",
                "a" * 40,
                "--output",
                str(out),
            ]
        )
        == 2
    )
    assert not out.exists()


def test_daily_returns_come_from_marked_equity_endpoints() -> None:
    day = 24 * HOUR
    market = [Frame(i * HOUR, {"X": 100.0 + i}, {"X": 0.0}) for i in range(49)]
    result = simulate(
        ReplaySpec(START, START + 2 * day, HOUR, 1000, (Instrument("X", "perp", 0, 0),)),
        [replace(frame, timestamp_ms=frame.timestamp_ms + START) for frame in market],
        [Target(START, START, {"X": 0.1})],
    )
    daily = summary(result)["daily_returns_from_equity_endpoints"]
    assert daily == [
        {"end_ms": START + day, "net_return": pytest.approx(1024 / 1000 - 1)},
        {"end_ms": START + 2 * day, "net_return": pytest.approx(1048 / 1024 - 1)},
    ]


def test_offline_cli_keeps_a_create_only_complete_fixture_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Native schema/census admission is exercised above. Inject already-admitted
    # tiny observations here to exercise the actual scoring/output path cheaply.
    fixture_bytes = b"synthetic admitted source"
    bindings = []
    for symbol in SYMBOLS:
        for role in ROLES:
            path = tmp_path / source_name(symbol, role)
            path.write_bytes(fixture_bytes)
            bindings.append(
                {
                    "symbol": symbol,
                    "role": role,
                    "path": str(path),
                    "sha256": sha256(fixture_bytes),
                    "bytes": len(fixture_bytes),
                }
            )
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"schema_version": 1, "study_id": STUDY_ID, "sources": bindings}))
    hours = {symbol: feature_hours() for symbol in SYMBOLS}
    hours["BTCUSDT"][720] = replace(
        hours["BTCUSDT"][720], quote_volume=Fraction(500), taker_quote=Fraction(400)
    )
    marks = {
        symbol: [Hour(START + i * MINUTE, 100, Fraction(0), Fraction(0)) for i in range(8 * 60)]
        for symbol in SYMBOLS
    }
    payments: dict[str, list[Funding]] = {symbol: [] for symbol in SYMBOLS}
    monkeypatch.setattr(repair, "END", START + 8 * HOUR)
    monkeypatch.setattr(repair, "load_observations", lambda sources: (hours, marks, payments))
    output = tmp_path / "result"
    args = [
        "run",
        "--input",
        str(index),
        "--expected-input-sha256",
        sha256(index.read_bytes()),
        "--source-commit",
        "a" * 40,
        "--output",
        str(output),
    ]
    assert main(args) == 0
    result = json.loads((output / "result.json").read_bytes())
    assert result["counts"]["accepted_windows"] == 1
    assert result["base"]["final_equity_usdt"] == pytest.approx(9998.6)
    assert result["double_cost_stress_same_windows"]["final_equity_usdt"] == pytest.approx(9997.2)
    assert result["ledger"]["points"][-1]["positions"] == {}
    assert result["promotion_decision"] == "forbidden"
    assert result["original_primary_equivalent"] is False
    assert (output / "completion.json").is_file()
    original = (output / "result.json").read_bytes()
    assert main(args) == 2
    assert (output / "result.json").read_bytes() == original
