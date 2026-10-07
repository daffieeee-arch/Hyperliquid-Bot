"""Deterministic tests for the point-in-time trend bar table."""

from __future__ import annotations

import math
from pathlib import Path

import duckdb
import pytest

from research.bar_tables.cli import main
from research.bar_tables.trend import BarTableError, TrendRow, build_trend_rows

_HOUR = 3_600_000
_START = 1_577_836_799_999  # 2019-12-31 23:59:59.999 UTC, a kline close time


def _closes(prices: list[float]) -> list[tuple[int, float]]:
    return [(_START + index * _HOUR, price) for index, price in enumerate(prices)]


def _hourly_funding(closes: list[tuple[int, float]], rate: float) -> list[tuple[int, float]]:
    # One settlement on the hour after each close, inside the next bar.
    return [(ts + 1, rate) for ts, _close in closes]


def _build(
    prices: list[float],
    funding: list[tuple[int, float]] | None = None,
    *,
    lookbacks: tuple[int, ...] = (2, 4),
    vol_window: int = 3,
) -> list[TrendRow]:
    closes = _closes(prices)
    return build_trend_rows(
        closes,
        _hourly_funding(closes, 0.0001) if funding is None else funding,
        lookbacks=lookbacks,
        vol_window=vol_window,
        bar_ms=_HOUR,
        max_funding_gap_ms=9 * _HOUR,
    )


def test_features_use_only_closes_at_or_before_the_bar() -> None:
    prices = [100.0, 101.0, 99.0, 102.0, 104.0, 103.0, 105.0]
    rows = _build(prices)
    # Warm-up is the longest lookback (4 bars): the first row is bar 4.
    assert [row.ts for row in rows] == [_START + index * _HOUR for index in range(4, 7)]
    first = rows[0]
    assert first.close == 104.0
    assert first.returns == (math.log(104.0 / 99.0), math.log(104.0 / 100.0))
    assert first.trend_score == 1.0
    one_bar = [math.log(prices[index] / prices[index - 1]) for index in (2, 3, 4)]
    mean = sum(one_bar) / 3
    expected = math.sqrt(sum((value - mean) ** 2 for value in one_bar) / 2)
    assert first.realized_vol == pytest.approx(expected)
    # A vol window longer than every lookback sets the warm-up instead.
    assert _build(prices, lookbacks=(1,), vol_window=5)[0].ts == _START + 5 * _HOUR
    # Changing a later price leaves every earlier row unchanged.
    later = _build([*prices[:-1], 50.0])
    assert later[:-1] == rows[:-1]
    assert later[-1] != rows[-1]


def test_trend_score_is_the_mean_sign_of_the_lookback_returns() -> None:
    rows = _build([100.0, 90.0, 95.0, 80.0, 92.0, 70.0], lookbacks=(1, 2, 4), vol_window=2)
    # Bar 4: up 1 bar (80 -> 92), down 2 bars (95 -> 92), down 4 bars (100 -> 92).
    assert rows[0].trend_score == pytest.approx(-1.0 / 3.0)
    # Bar 5: down over 1, 2 and 4 bars.
    assert rows[1].trend_score == -1.0
    flat = _build([100.0, 100.0, 101.0, 100.0, 100.0], lookbacks=(2,), vol_window=2)
    assert [row.trend_score for row in flat] == [1.0, 0.0, -1.0]


def test_funding_lands_in_the_bar_whose_interval_holds_the_settlement() -> None:
    prices = [100.0, 101.0, 99.0, 102.0, 104.0, 103.0, 105.0]
    closes = _closes(prices)
    funding = [
        (closes[3][0], 0.5),  # at the last warm-up close: before the first row's interval
        (closes[3][0] + 1, 0.001),  # just after it: the first row's bar
        (closes[4][0], 0.002),  # exactly at a close: that bar
        (closes[5][0] + 10, 0.003),
        (closes[5][0] + 20, 0.004),  # two settlements in one bar are summed
        (closes[6][0], 0.0),
        (closes[6][0] + 1, 0.9),  # after the last close: a future bar
    ]
    rows = _build(prices, funding)
    assert [row.funding_rate for row in rows] == pytest.approx([0.003, 0.0, 0.007])


def test_a_missing_bar_fails_closed() -> None:
    closes = _closes([100.0, 101.0, 99.0, 102.0, 104.0, 103.0])
    del closes[3]
    with pytest.raises(BarTableError, match="not contiguous"):
        build_trend_rows(
            closes,
            _hourly_funding(closes, 0.0),
            lookbacks=(2,),
            vol_window=2,
            bar_ms=_HOUR,
            max_funding_gap_ms=9 * _HOUR,
        )


def test_a_funding_gap_fails_closed() -> None:
    prices = [100.0 + index for index in range(30)]
    closes = _closes(prices)
    every_eight = [(ts + 1, 0.0001) for ts, _close in closes[::8]]
    rows = _build(prices, every_eight, lookbacks=(2,), vol_window=2)
    assert len(rows) == 28
    missing = [entry for entry in every_eight if entry != every_eight[2]]
    with pytest.raises(BarTableError, match="No funding settlement"):
        _build(prices, missing, lookbacks=(2,), vol_window=2)
    # Funding that starts late, or stops before the last bar, is a gap too.
    with pytest.raises(BarTableError, match="No funding settlement"):
        _build(prices, every_eight[2:], lookbacks=(2,), vol_window=2)
    with pytest.raises(BarTableError, match="No funding settlement"):
        _build(prices, every_eight[:2], lookbacks=(2,), vol_window=2)


def test_bad_inputs_are_refused() -> None:
    with pytest.raises(BarTableError, match="warm-up"):
        _build([100.0, 101.0, 102.0, 103.0])
    with pytest.raises(BarTableError, match="positive number"):
        _build([100.0, 101.0, 0.0, 103.0, 104.0, 105.0])
    with pytest.raises(BarTableError, match="repeat"):
        _build([100.0 + index for index in range(8)], lookbacks=(2, 2))
    with pytest.raises(BarTableError, match="vol window"):
        _build([100.0 + index for index in range(8)], vol_window=1)
    with pytest.raises(BarTableError, match="not positive"):
        _build([100.0] * 8, lookbacks=(2,), vol_window=2)
    closes = _closes([100.0 + index for index in range(8)])
    unordered = [(closes[5][0], 0.0), (closes[4][0], 0.0)]
    with pytest.raises(BarTableError, match="strictly increasing"):
        _build([100.0 + index for index in range(8)], unordered)
    # Only settlements inside the output bars' intervals are read or checked.
    prices = [100.0 + index for index in range(8)]
    valid = _hourly_funding(_closes(prices), 0.0001)
    assert _build(prices, [(_START - 1, math.nan), *valid])
    with pytest.raises(BarTableError, match="finite"):
        _build(prices, [*valid[:5], (valid[5][0], math.nan), *valid[6:]])


def test_the_cli_writes_a_harness_ready_table(tmp_path: Path) -> None:
    root = tmp_path / "root"
    klines = root / "parquet" / "hist_etl" / "binance" / "um" / "klines_1h"
    funding = root / "parquet" / "hist_etl" / "binance" / "um" / "funding"
    klines.mkdir(parents=True)
    funding.mkdir(parents=True)
    connection = duckdb.connect()
    try:
        # 2020-01-01 00:00 to 2020-01-02 23:00 opens; funding every 8 hours.
        connection.execute(
            "COPY (SELECT make_timestamp(1577836800000000 + i * 3600000000 + 3599999000) AS ts, "
            "100.0 + i + (i % 3) AS close FROM range(48) t(i)) TO ? (FORMAT PARQUET)",
            [str(klines / "BTCUSDT-2020-01.parquet")],
        )
        connection.execute(
            "COPY (SELECT make_timestamp(1577836800000000 + i * 28800000000) AS calc_time, "
            "0.0001 AS last_funding_rate FROM range(7) t(i)) TO ? (FORMAT PARQUET)",
            [str(funding / "BTCUSDT-2020-01.parquet")],
        )
    finally:
        connection.close()
    out = tmp_path / "bars.parquet"
    code = main(
        [
            "trend",
            "--root",
            str(root),
            "--symbol",
            "BTCUSDT",
            "--start",
            "2020-01-01",
            "--end",
            "2020-01-03",
            "--lookbacks",
            "4,8",
            "--vol-window",
            "6",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    connection = duckdb.connect()
    try:
        columns = [
            row[0]
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(out)]
            ).fetchall()
        ]
        stats = connection.execute(
            "SELECT count(*), min(ts), bool_and(available_ts = ts), sum(funding_rate) "
            "FROM read_parquet(?)",
            [str(out)],
        ).fetchone()
    finally:
        connection.close()
    assert columns == [
        "ts",
        "available_ts",
        "close",
        "ret_4",
        "ret_8",
        "trend_score",
        "realized_vol",
        "funding_rate",
    ]
    assert stats is not None
    # 48 bars minus an 8-bar warm-up; the first close is 08:59:59.999.
    assert stats[0] == 40
    assert stats[1] == 1_577_869_199_999
    assert stats[2] is True
    # Jan 1 08:00 and 16:00, Jan 2 00:00, 08:00 and 16:00 fall inside the output bars;
    # Jan 1 00:00 is before the first one and Jan 3 00:00 after the last close.
    assert stats[3] == pytest.approx(0.0005)
    # The scratch CSV and Parquet are gone; only the table and the input root remain.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["bars.parquet", "root"]


def test_the_cli_reports_a_failure_and_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "bars.parquet"
    code = main(
        [
            "trend",
            "--root",
            str(tmp_path / "missing"),
            "--symbol",
            "BTCUSDT",
            "--start",
            "2020-01-01",
            "--end",
            "2020-01-03",
            "--lookbacks",
            "4",
            "--vol-window",
            "6",
            "--out",
            str(out),
        ]
    )
    assert code == 2
    assert not out.exists()
