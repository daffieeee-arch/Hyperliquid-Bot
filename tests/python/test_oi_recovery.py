from __future__ import annotations

import csv
import hashlib
import io
import math
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from research import discovery
from research import oi_recovery_cli as cli
from research import oi_recovery_io as inputs
from research.btc_flow_io import PrivateOutput
from research.discovery_io import METRICS_HEADER, oi_values
from research.regime_io import Segment, Sources
from research.regime_momentum import evaluate_case
from research.repaired_accounting import Frame, Target
from research.two_models_io import BTC, DAY, HOUR, MINUTE, START


def native(
    tmp_path: Path,
    timestamps: list[int],
    values: list[str] | None = None,
    *,
    symbol: str = BTC,
    header: tuple[str, ...] = METRICS_HEADER,
) -> dict[str, Any]:
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(header)
    for index, t in enumerate(timestamps):
        timestamp = inputs.iso(t).replace("T", " ").removesuffix("+00:00")
        writer.writerow(
            [
                timestamp,
                symbol,
                values[index] if values else str(1000 + index),
                "999999999999999",
                "1",
                "1",
                "1",
                "1",
            ]
        )
    p = tmp_path / "BTCUSDT-metrics-2024-01-01.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(p.stem + ".csv", text.getvalue())
    raw = p.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    check = Path(str(p) + ".CHECKSUM")
    check.write_text(f"{digest} {p.name}\n")
    return {
        "day": "2024-01-01",
        "path": str(p),
        "bytes": len(raw),
        "sha256": digest,
        "checksum_path": str(check),
        "checksum_bytes": check.stat().st_size,
        "checksum_sha256": hashlib.sha256(check.read_bytes()).hexdigest(),
    }


def test_timestamp_index_admits_permutation_without_repairing_old_bytes_or_loader(
    tmp_path: Path,
) -> None:
    expected = list(range(START, START + DAY, 5 * MINUTE))
    stamps = expected[17:] + expected[:17]
    row = native(tmp_path, stamps)
    before = Path(row["path"]).read_bytes()
    values, mask, quality = inputs.read_day(row, START)
    assert len(values) == len(mask) == 288 and quality["order_mismatches"] == 288
    assert values[expected[17]] == 1000
    assert next(m for m in mask if m[0] == expected[17])[3] == 2
    assert Path(row["path"]).read_bytes() == before
    with pytest.raises(Exception, match="contiguous UTC"):
        oi_values(inputs.source(row), inputs.source(row, True), START)


def test_missing_and_zero_are_distinct_unavailable_slots_not_quote_value(tmp_path: Path) -> None:
    row = native(tmp_path, [START, START + 10 * MINUTE], ["0E-16", "123.45"])
    values, mask, quality = inputs.read_day(row, START)
    assert values[START] is None and values[START + 5 * MINUTE] is None
    assert values[START + 10 * MINUTE] == 123.45  # Never column3's huge quote value.
    assert mask[0] == (START, None, "0E-16", 2)
    assert mask[1] == (START + 5 * MINUTE, None, None, None)
    assert len(quality["missing_expected"]) == 286
    assert quality["zero_timestamps"] == ["2024-01-01 00:00:00"]


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "1e999"])
def test_negative_or_nonfinite_native_oi_refuses(tmp_path: Path, value: str) -> None:
    row = native(tmp_path, [START], [value])
    with pytest.raises(Exception, match=r"negative|finite"):
        inputs.read_day(row, START)


@pytest.mark.parametrize(
    "stamps", [[START, START], [START - 5 * MINUTE], [START + DAY], [START + 60_000]]
)
def test_duplicates_wrong_day_or_off_grid_refuse(tmp_path: Path, stamps: list[int]) -> None:
    row = native(tmp_path, stamps)
    with pytest.raises(Exception, match=r"duplicate|grid"):
        inputs.read_day(row, START)


def test_wrong_header_instrument_or_changed_bytes_refuse(tmp_path: Path) -> None:
    row = native(tmp_path, [START], symbol="ETHUSDT")
    with pytest.raises(Exception, match="identity"):
        inputs.read_day(row, START)
    row = native(tmp_path, [START], header=("wrong", *METRICS_HEADER[1:]))
    with pytest.raises(Exception, match="header"):
        inputs.read_day(row, START)
    Path(row["path"]).write_bytes(b"changed")
    with pytest.raises(Exception, match=r"changed|size|identity|checksum"):
        inputs.read_day(row, START)


def test_outer_inventory_pin_refuses_a_partial_or_replaced_inventory(tmp_path: Path) -> None:
    p = tmp_path / "input.json"
    p.write_text("[]")
    with pytest.raises(Exception, match="pinned 974-day"):
        inputs.load_oi(p)


def synthetic_sources() -> tuple[Sources, inputs.OpenInterest]:
    end = START + 5 * DAY
    hours = {t: 100 * 1.01 ** ((t - START) // HOUR) for t in range(START - HOUR, end + 1, HOUR)}
    oi: dict[int, float | None] = {
        t - 5 * MINUTE: 1000 * 0.98 ** ((t - START) // HOUR)
        for t in range(START + HOUR, end + HOUR, HOUR)
    }
    segments = []
    for day in (0, 2, 4):
        start, finish = START + day * DAY, START + (day + 1) * DAY
        frames = tuple(
            Frame(t, {BTC: hours[t]}, {BTC: 0.0}, {BTC: 9999.0})
            for t in range(start, finish + 1, HOUR)
        )
        extremes = {
            t: (hours[((t + HOUR - 1) // HOUR) * HOUR],) * 2
            for t in range(start + MINUTE, finish + 1, MINUTE)
        }
        segments.append(Segment(start, finish, frames, extremes))
    return Sources(tuple(segments), hours, {}), inputs.OpenInterest(oi, (), (), {})


def test_first_two_decision_hours_are_unavailable_and_end_is_not_a_decision() -> None:
    prices, oi = synthetic_sources()
    series = inputs.bars(prices, oi)
    by_time = {bar.close_ms: bar for bar in series[BTC]}
    assert by_time[START].open_interest is None
    assert by_time[START + HOUR].open_interest is not None
    config = cli.configurations()[0]
    episodes, counts = discovery.generate_episodes(config, series, START, START + DAY)
    assert counts["unavailable"] == 2
    assert episodes[0].decision_ms == START + 2 * HOUR
    assert all(e.decision_ms < START + DAY for e in episodes)


def test_new_missing_oi_does_not_close_an_already_held_episode() -> None:
    prices, oi = synthetic_sources()
    data = inputs.bars(prices, oi)
    old, _ = discovery.generate_episodes(cli.configurations()[0], data, START, START + DAY)
    first = old[0]
    altered = replace(oi, values=oi.values | {START + 3 * HOUR - 5 * MINUTE: None})
    new, _ = discovery.generate_episodes(
        cli.configurations()[0], inputs.bars(prices, altered), START, START + DAY
    )
    assert new[0] == first and first.exit_ms == first.entry_ms + 4 * HOUR


def test_split_crossing_is_excluded_but_exit_exactly_on_split_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prices, oi = synthetic_sources()
    config = cli.configurations()[0]
    monkeypatch.setattr(cli, "SPLITS", (START + 7 * HOUR,))
    _, information = cli.schedule(config, prices.segments[0], inputs.bars(prices, oi))
    assert information["episodes"][0]["exit_ms"] == START + 7 * HOUR
    monkeypatch.setattr(cli, "SPLITS", (START + 6 * HOUR,))
    _, changed = cli.schedule(config, prices.segments[0], inputs.bars(prices, oi))
    assert all(
        not (e["decision_ms"] < START + 6 * HOUR < e["exit_ms"]) for e in changed["episodes"]
    )
    assert changed["episodes"][0]["decision_ms"] >= START + 6 * HOUR


def test_hourly_trade_reference_never_inherits_minute_trade_or_mark() -> None:
    prices, _ = synthetic_sources()
    segment = prices.segments[0]
    before = segment.frames[1]
    updated = cli.hourly_segment(segment, prices.hourly)
    after = updated.frames[1]
    assert before.execution_prices == {BTC: 9999.0}
    assert after.execution_prices == {BTC: prices.hourly[START + HOUR]}
    assert after.marks == before.marks and after.funding_rates == before.funding_rates
    assert updated.minute_extremes == segment.minute_extremes
    assert cli.spec(updated, 10000, "base").instruments[0].fee_bps == 5
    assert cli.spec(updated, 10000, "base").instruments[0].slippage_bps == 2
    assert cli.spec(updated, 10000, "stress").instruments[0].fee_bps == 10
    assert cli.spec(updated, 10000, "stress").instruments[0].slippage_bps == 4


def test_exact_funding_ownership_and_paid_terminal_close_use_shared_ledger() -> None:
    end = START + DAY
    frames = tuple(
        Frame(START + i * HOUR, {BTC: 100.0}, {BTC: rate}, {BTC: 100.0})
        for i, rate in enumerate(
            0.01 if i == 20 else 0.001 if i == 21 else 0.002 if i == 24 else 0.0 for i in range(25)
        )
    )
    extremes = {t: (100.0, 100.0) for t in range(START + MINUTE, end + 1, MINUTE)}
    segment = Segment(START, end, frames, extremes)
    result = evaluate_case(
        cli.spec(segment, 10000, "base"),
        frames,
        (Target(START + 19 * HOUR, START + 20 * HOUR, {BTC: 0.5}),),
        extremes,
    )
    quantity = result["trades"][0]["quantity_delta"]
    assert result["total_funding"] == pytest.approx(-quantity * 100 * 0.003)
    assert result["trades"][-1]["timestamp_ms"] == end
    assert result["trades"][-1]["fee"] > 0
    assert result["episodes"][0]["initial_margin"] == pytest.approx(abs(quantity) * 100 / 2)
    assert result["ledger"][-1]["positions"] == {}


def fixture_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "END", START + 5 * DAY)
    monkeypatch.setattr(cli, "SPLITS", (START + 2 * DAY, START + 4 * DAY))
    monkeypatch.setattr(
        cli,
        "FOLDS",
        {
            "development": (START, START + 2 * DAY),
            "diagnostic_2025": (START + 2 * DAY, START + 4 * DAY),
            "diagnostic_2026": (START + 4 * DAY, START + 5 * DAY),
        },
    )


def test_all_fifteen_accounts_ninety_cases_carry_capital_and_never_select(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture_context(monkeypatch)
    prices, oi = synthetic_sources()
    with PrivateOutput(tmp_path / "run") as output:
        result = cli.run(prices, oi, output)
    assert result["case_attempts"] == result["expected_segment_cost_cases"] == 90
    assert len(result["accounts"]) == 15 and len(result["configurations"]) == 4
    assert result["selector"] is None and result["new_alpha_configurations"] == 0
    assert result["promotion_decision"] == "forbidden"
    for pair in result["accounts"].values():
        for account in pair.values():
            assert not account["failed"]
            assert len(account["daily_equity_returns"]) == 5
            assert math.isfinite(account["net_return"])
    import gzip
    import json

    path = tmp_path / "run" / "segment-1-persistent_long-base-request.json.gz"
    second = json.loads(gzip.decompress(path.read_bytes()))
    assert second["spec"]["initial_equity"] != 10000  # No year/gap reset.


def test_failed_account_retains_unknown_segment_blocks_resume_and_nulls_final_return(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture_context(monkeypatch)
    prices, oi = synthetic_sources()
    original = evaluate_case
    first = True

    def fail_once(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal first
        if first:
            first = False
            raise ValueError("synthetic unknown held state")
        return original(*args, **kwargs)

    monkeypatch.setattr(cli, "evaluate_case", fail_once)
    with PrivateOutput(tmp_path / "run") as output:
        result = cli.run(prices, oi, output)
    failed = result["accounts"][cli.configurations()[0].name]["base"]
    assert failed["failed"] and failed["final_equity"] is None and failed["net_return"] is None
    assert failed["no_trade_calendar_days"] is None
    assert result["case_attempts"] == 88
    assert len(list((tmp_path / "run").glob("*-blocked.json"))) == 2


@pytest.mark.parametrize("mode", ["LIVE", "TESTNET", "SHADOW", "PAPER "])
def test_mode_gate_precedes_source_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setattr(cli, "identity", lambda commit: pytest.fail("source read reached"))
    with pytest.raises(Exception, match="PAPER"):
        cli.main(
            [
                "--prices",
                str(tmp_path / "prices.json"),
                "--oi-index",
                str(tmp_path / "oi.json"),
                "--output",
                str(tmp_path / "run"),
                "--source-commit",
                "a" * 40,
                "--expected-plan-sha256",
                "b" * 64,
            ]
        )
    assert not (tmp_path / "run").exists()


def test_pinned_price_bytes_refuse_before_decoder_or_financial_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setattr(cli, "identity", lambda commit: {})
    monkeypatch.setattr(cli, "load", lambda *args: pytest.fail("decoder reached before price pin"))
    price_path = tmp_path / "prices.json"
    price_path.write_text("{}")
    output = tmp_path / "run"
    with pytest.raises(Exception, match="original qualified price input"):
        cli.main(
            [
                "--prices",
                str(price_path),
                "--oi-index",
                str(tmp_path / "oi.json"),
                "--output",
                str(output),
                "--source-commit",
                "a" * 40,
                "--expected-plan-sha256",
                hashlib.sha256(inputs.PLAN.read_bytes()).hexdigest(),
            ]
        )
    assert (output / "failure.json").is_file()
    assert not (output / "result.json").exists()
    with pytest.raises(Exception, match="exist"):
        cli.main(
            [
                "--prices",
                str(price_path),
                "--oi-index",
                str(tmp_path / "oi.json"),
                "--output",
                str(output),
                "--source-commit",
                "a" * 40,
                "--expected-plan-sha256",
                hashlib.sha256(inputs.PLAN.read_bytes()).hexdigest(),
            ]
        )
