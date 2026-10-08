"""Deterministic tests for the daily cross-sectional panel."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pytest
from pytest import CaptureFixture

from research.bar_tables.cli import main
from research.bar_tables.panel import (
    DAY_MS,
    DailyBar,
    PanelRow,
    PanelSpec,
    Settlement,
    build_symbol_rows,
    funding_hole_closes,
    rank_by_volume,
)
from research.bar_tables.trend import BarTableError
from research.hist_etl.universe import MonthRun, Universe, UniverseSymbol, render_universe

# 2026-01-01 23:59:59.999 UTC, the close of the first daily bar of 2026.
_FIRST_CLOSE = 1_767_311_999_999
_SPEC = PanelSpec(lookbacks=(2, 3), vol_window=2, volume_window=2, funding_window=2)


def _bars(closes: list[float], *, trades: list[int] | None = None) -> list[DailyBar]:
    counts = trades or [10] * len(closes)
    return [
        DailyBar(
            ts=_FIRST_CLOSE + index * DAY_MS, close=close, quote_volume=100.0 * count, trades=count
        )
        for index, (close, count) in enumerate(zip(closes, counts, strict=True))
    ]


def _funding(bars: list[DailyBar], rate: float = 0.0001) -> list[Settlement]:
    # 00:00, 08:00 and 16:00 of each bar's day, the three 8-hour settlements.
    day_starts = [bar.ts + 1 - DAY_MS for bar in bars]
    return [
        Settlement(ts=start + hour * 3_600_000, rate=rate, interval_hours=8)
        for start in day_starts
        for hour in (0, 8, 16)
    ]


def _rows(bars: list[DailyBar], settlements: list[Settlement] | None = None) -> list[PanelRow]:
    return build_symbol_rows(
        "AAAUSDT", bars, _funding(bars) if settlements is None else settlements, _SPEC
    )


def test_features_use_only_days_at_or_before_the_close() -> None:
    closes = [100.0, 110.0, 99.0, 120.0, 130.0, 90.0]
    rows = _rows(_bars(closes))
    changed = _rows(_bars([*closes[:-1], 500.0]))
    assert rows[:-1] == changed[:-1]
    assert rows[3].returns == (math.log(120.0 / 110.0), math.log(120.0 / 100.0))
    daily = [math.log(99.0 / 110.0), math.log(120.0 / 99.0)]
    mean = sum(daily) / 2
    assert rows[3].realized_vol == pytest.approx(
        math.sqrt(sum((value - mean) ** 2 for value in daily))
    )
    assert rows[3].mean_quote_volume == 1000.0
    assert rows[3].funding_rate == pytest.approx(0.0003)
    assert (rows[3].funding_settlements, rows[3].funding_covered) == (3, True)
    assert rows[3].mean_funding == pytest.approx(0.0003)


def test_warm_up_rows_are_kept_with_empty_features() -> None:
    rows = _rows(_bars([100.0, 101.0, 103.0, 102.0]))
    assert [row.returns for row in rows[:2]] == [(None, None), (None, None)]
    assert rows[2].returns[0] is not None and rows[2].returns[1] is None
    assert rows[0].mean_quote_volume is None and rows[1].mean_quote_volume == 1000.0
    assert [row.complete for row in rows] == [False, False, False, True]


def test_a_day_without_trades_breaks_price_windows_but_counts_as_volume() -> None:
    bars = _bars(
        [100.0, 101.0, 102.0, 102.0, 103.0, 104.0, 105.0, 106.0],
        trades=[10, 10, 10, 0, 10, 10, 10, 10],
    )
    rows = _rows(bars)
    assert rows[3].traded is False
    assert rows[3].mean_quote_volume == 500.0
    # Price features need every day of the window traded, the current one included.
    assert all(row.returns[0] is None for row in rows[3:6])
    assert rows[6].returns[0] == pytest.approx(math.log(105.0 / 103.0))
    assert rows[6].returns[1] is None
    assert rows[7].returns[1] == pytest.approx(math.log(106.0 / 103.0))
    assert rows[5].realized_vol is None and rows[6].realized_vol is not None


def test_a_gap_between_runs_restarts_every_window() -> None:
    bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0])
    relisted = [*bars[:4], *(replace(bar, ts=bar.ts + 10 * DAY_MS) for bar in bars[4:])]
    rows = _rows(relisted, _funding(relisted))
    assert rows[3].returns[1] is not None
    assert rows[4].returns == (None, None)
    assert rows[4].mean_quote_volume is None and rows[4].mean_funding is None
    assert rows[5].mean_quote_volume == 1000.0
    assert rows[6].returns[0] == pytest.approx(math.log(106.0 / 104.0))


def test_funding_needs_a_fully_covered_day() -> None:
    bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0])
    settlements = _funding(bars)
    # Drop the 08:00 settlement of the third day and every settlement of the fifth.
    partial = [item for index, item in enumerate(settlements) if index != 7 and index < 12]
    rows = _rows(bars, partial)
    assert rows[2].funding_rate == pytest.approx(0.0002)
    assert (rows[2].funding_settlements, rows[2].funding_covered) == (2, False)
    assert rows[2].mean_funding is None and rows[3].mean_funding is None
    assert (rows[4].funding_rate, rows[4].funding_settlements, rows[4].funding_covered) == (
        None,
        0,
        False,
    )
    assert rows[4].mean_funding is None
    assert rows[1].mean_funding == pytest.approx(0.0003)


def test_four_hour_funding_counts_as_covered() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    settlements = [
        Settlement(ts=bar.ts + 1 - DAY_MS + hour * 3_600_000, rate=0.00005, interval_hours=4)
        for bar in bars
        for hour in range(0, 24, 4)
    ]
    rows = _rows(bars, settlements)
    assert (rows[2].funding_settlements, rows[2].funding_covered) == (6, True)
    assert rows[2].mean_funding == pytest.approx(0.0003)


def test_a_day_that_changes_the_funding_interval_is_covered() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    opens = bars[1].ts + 1 - DAY_MS
    hours = [(0, 8), (8, 8), (12, 4), (16, 4), (20, 4)]
    settlements = [
        *[item for item in _funding(bars) if item.ts < opens],
        *(Settlement(opens + hour * 3_600_000, 0.001, interval) for hour, interval in hours),
        *(
            Settlement(bars[2].ts + 1 - DAY_MS + hour * 3_600_000, 0.001, 4)
            for hour in range(0, 24, 4)
        ),
    ]
    rows = _rows(bars, settlements)
    assert (rows[1].funding_settlements, rows[1].funding_covered) == (5, True)
    assert rows[1].funding_rate == pytest.approx(0.005)
    assert rows[2].funding_covered


def _returning_to_eight_hours(bars: list[DailyBar]) -> list[Settlement]:
    # Day two runs 4h funding and returns to 8h at the next midnight. Each
    # settlement carries the hours since the one before it.
    opens = bars[1].ts + 1 - DAY_MS
    return [
        *[item for item in _funding(bars) if item.ts < opens],
        *(Settlement(opens + hour * 3_600_000, 0.001, 4) for hour in (0, 4, 8, 12, 16)),
        *[item for item in _funding(bars) if item.ts >= opens + DAY_MS],
    ]


def test_a_day_returning_to_eight_hour_funding_is_judged_at_its_close() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    settlements = _returning_to_eight_hours(bars)
    rows = _rows(bars, settlements)
    # At the close, a missing 20:00 settlement and a return to 8h look alike.
    assert (rows[1].funding_settlements, rows[1].funding_covered) == (5, False)
    assert rows[2].funding_covered
    # With the next day known, nothing is missing.
    assert funding_hole_closes(settlements) == set()


def test_funding_features_never_read_past_the_close() -> None:
    bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0])
    settlements = _returning_to_eight_hours(bars)
    full = _rows(bars, settlements)
    for count in range(1, len(bars)):
        known = [item for item in settlements if item.ts + 60_000 <= bars[count - 1].ts]
        assert _rows(bars[:count], known) == full[:count]


def test_funding_holes_mark_the_days_a_settlement_was_due() -> None:
    bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0])
    settlements = _funding(bars)
    # Drop day two's 16:00 and day four's 00:00 to 16:00.
    dropped = [item for index, item in enumerate(settlements) if index not in {5, 9, 10, 11}]
    assert funding_hole_closes(dropped) == {bars[1].ts, bars[3].ts}
    assert funding_hole_closes(settlements) == set()


def test_a_settlement_stamped_just_before_midnight_opens_the_next_day() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    early = [
        replace(item, ts=item.ts - 5) if index == 3 else item
        for index, item in enumerate(_funding(bars))
    ]
    rows = _rows(bars, early)
    assert [(row.funding_settlements, row.funding_covered) for row in rows] == [
        (3, True),
        (3, True),
        (3, True),
    ]


def test_flat_prices_give_no_realized_vol() -> None:
    rows = _rows(_bars([100.0, 100.0, 100.0, 100.0]))
    assert rows[3].realized_vol is None
    assert not rows[3].complete


def test_rank_orders_complete_rows_by_volume_per_day() -> None:
    def row(symbol: str, volume: float | None, *, traded: bool = True) -> PanelRow:
        return PanelRow(
            ts=_FIRST_CLOSE,
            symbol=symbol,
            close=1.0,
            quote_volume=1.0,
            trades=1 if traded else 0,
            traded=traded,
            funding_rate=0.0,
            funding_settlements=3,
            funding_covered=True,
            returns=(0.1,),
            realized_vol=0.01,
            mean_quote_volume=volume,
            mean_funding=0.0,
        )

    ranked = rank_by_volume(
        [
            row("CCC", 5.0),
            row("AAA", 9.0),
            row("BBB", 9.0),
            row("DDD", None),
            row("EEE", 99.0, traded=False),
        ]
    )
    assert [(item.symbol, item.volume_rank) for item in ranked] == [
        ("AAA", 1),
        ("BBB", 2),
        ("CCC", 3),
        ("DDD", None),
        ("EEE", None),
    ]
    with pytest.raises(BarTableError, match="twice"):
        rank_by_volume([row("AAA", 1.0), row("AAA", 2.0)])


@pytest.mark.parametrize(
    "bars",
    [
        [DailyBar(_FIRST_CLOSE, 1.0, 1.0, 1), DailyBar(_FIRST_CLOSE, 1.0, 1.0, 1)],
        [DailyBar(_FIRST_CLOSE, 1.0, 1.0, 1), DailyBar(_FIRST_CLOSE + 3_600_000, 1.0, 1.0, 1)],
        [DailyBar(_FIRST_CLOSE + 1, 1.0, 1.0, 1)],
        [DailyBar(_FIRST_CLOSE, 0.0, 1.0, 1)],
        [DailyBar(_FIRST_CLOSE, math.nan, 1.0, 1)],
        [DailyBar(_FIRST_CLOSE, 1.0, -1.0, 1)],
    ],
)
def test_bad_bars_are_refused(bars: list[DailyBar]) -> None:
    with pytest.raises(BarTableError):
        build_symbol_rows("AAAUSDT", bars, [], _SPEC)


def test_bad_funding_and_specs_are_refused() -> None:
    bars = _bars([1.0, 2.0])
    with pytest.raises(BarTableError, match="order"):
        build_symbol_rows("AAAUSDT", bars, list(reversed(_funding(bars))), _SPEC)
    with pytest.raises(BarTableError, match="usable"):
        build_symbol_rows("AAAUSDT", bars, [Settlement(_FIRST_CLOSE, math.inf, 8)], _SPEC)
    for kwargs in (
        {"lookbacks": ()},
        {"lookbacks": (0,)},
        {"lookbacks": (2, 2)},
        {"vol_window": 1},
        {"funding_window": 0},
    ):
        with pytest.raises(BarTableError):
            replace(_SPEC, **kwargs)


# --- Reading a synced universe ------------------------------------------------


def _write_month(
    root: Path,
    slug: str,
    symbol: str,
    month: str,
    days: range,
    *,
    untraded_from: int = 99,
    slots: range | None = None,
    early_last_ms: int = 0,
) -> None:
    """Klines for ``days``, or 8-hourly funding for their slots (or ``slots``).

    Funding slot ``k`` is ``8k`` hours after the month opens; the last slot
    can be stamped ``early_last_ms`` early.
    """
    directory = root / "parquet" / "hist_etl" / "binance" / "um" / slug
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{symbol}-{month}.parquet"
    first = date.fromisoformat(f"{month}-01")
    start_us = (first - date(1970, 1, 1)).days * 86_400_000_000
    # DuckDB binds the parameters of a COPY out of order, so the target is a literal.
    target = "'" + str(path).replace("'", "''") + "'"
    connection = duckdb.connect()
    try:
        if slug == "klines_1d":
            connection.execute(
                "COPY (SELECT make_timestamp(? + (d - 1) * 86400000000 + 86399999000) AS ts, "
                "100.0 + d AS close, CASE WHEN d >= ? THEN 0 ELSE 10 END AS trade_count, "
                "trade_count * 1000.0 AS quote_volume, ? AS symbol "
                f"FROM range(?, ?) t(d)) TO {target} (FORMAT PARQUET)",
                [start_us, untraded_from, symbol, days.start, days.stop],
            )
        else:
            chosen = slots or range((days.start - 1) * 3, (days.stop - 1) * 3)
            connection.execute(
                "COPY (SELECT make_timestamp(? + i * 28800000000 "
                "- CASE WHEN i = ? THEN ? ELSE 0 END) AS calc_time, "
                "8 AS funding_interval_hours, 0.0001 AS last_funding_rate, ? AS symbol "
                f"FROM range(?, ?) t(i)) TO {target} (FORMAT PARQUET)",
                [
                    start_us,
                    chosen.stop - 1,
                    early_last_ms * 1000,
                    symbol,
                    chosen.start,
                    chosen.stop,
                ],
            )
    finally:
        connection.close()
    path.with_name(path.name + ".sources.json").write_text("{}", encoding="utf-8")


def _universe_root(tmp_path: Path) -> tuple[Path, Path]:
    """AAAUSDT trades through Q1 2026, NEWUSDT from Jan 20; DEADUSDT ends in January."""

    runs_aaa = (MonthRun(date(2026, 1, 1), date(2026, 3, 1)),)
    runs_dead = (MonthRun(date(2026, 1, 1), date(2026, 1, 1)),)
    universe = Universe(
        market="um",
        quote="USDT",
        interval="1d",
        as_of=date(2026, 4, 8),
        latest_month=date(2026, 3, 1),
        symbols=(
            UniverseSymbol("AAAUSDT", runs_aaa, runs_aaa),
            UniverseSymbol("DEADUSDT", runs_dead, runs_dead),
            UniverseSymbol("NEWUSDT", runs_aaa, runs_aaa),
        ),
        excluded=(),
    )
    config = tmp_path / "config"
    (config / "universe").mkdir(parents=True)
    (config / "universe" / "u.json").write_text(render_universe(universe), encoding="utf-8")
    manifest = config / "datasets.toml"
    manifest.write_text(
        '[[binance_universe]]\nid = "u"\nfile = "universe/u.json"\n', encoding="utf-8"
    )
    root = tmp_path / "root"
    for month, days in (
        ("2026-01", range(1, 32)),
        ("2026-02", range(1, 29)),
        ("2026-03", range(1, 32)),
    ):
        _write_month(root, "klines_1d", "AAAUSDT", month, days)
        _write_month(root, "funding", "AAAUSDT", month, days)
    for month, days in (
        ("2026-01", range(20, 32)),
        ("2026-02", range(1, 29)),
        ("2026-03", range(1, 32)),
    ):
        _write_month(root, "klines_1d", "NEWUSDT", month, days)
        _write_month(root, "funding", "NEWUSDT", month, days)
    # Listed on Jan 10, delisted after Jan 24: its last bars have no trades.
    # Funding starts at 16:00 on Jan 10 and stops after 08:00 on Jan 24.
    _write_month(root, "klines_1d", "DEADUSDT", "2026-01", range(10, 32), untraded_from=25)
    _write_month(root, "funding", "DEADUSDT", "2026-01", range(10, 25), slots=range(29, 71))
    return root, manifest


def _panel_args(root: Path, manifest: Path, out: Path, *, end: str = "2026-04-01") -> list[str]:
    return [
        "panel",
        "--root",
        str(root),
        "--manifest",
        str(manifest),
        "--group",
        "u",
        "--start",
        "2026-01-01",
        "--end",
        end,
        "--lookbacks",
        "3,7",
        "--vol-window",
        "5",
        "--volume-window",
        "5",
        "--funding-window",
        "3",
        "--out",
        str(out),
    ]


def test_the_cli_writes_the_panel_with_delisted_symbols(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    out = tmp_path / "panel.parquet"
    assert main(_panel_args(root, manifest, out)) == 0
    connection = duckdb.connect()
    try:
        columns = [
            row[0]
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(out)]
            ).fetchall()
        ]
        counts = connection.execute(
            "SELECT symbol, count(*), sum(CASE WHEN traded THEN 1 ELSE 0 END), "
            "count(volume_rank), bool_and(available_ts = ts) "
            "FROM read_parquet(?) GROUP BY symbol ORDER BY symbol",
            [str(out)],
        ).fetchall()
        first_ranked = connection.execute(
            "SELECT min(ts) FROM read_parquet(?) WHERE symbol = 'AAAUSDT' AND volume_rank = 1",
            [str(out)],
        ).fetchone()
    finally:
        connection.close()
    assert columns == [
        "ts",
        "available_ts",
        "symbol",
        "close",
        "quote_volume",
        "trades",
        "traded",
        "funding_rate",
        "funding_settlements",
        "funding_covered",
        "ret_3d",
        "ret_7d",
        "vol_5d",
        "qv_5d",
        "funding_3d",
        "volume_rank",
    ]
    # DEADUSDT ranks from its eighth traded day (Jan 17) to Jan 23, its last
    # with three covered funding days; Jan 10 and Jan 24 are partial.
    # NEWUSDT ranks from Jan 27, its eighth day.
    assert counts == [
        ("AAAUSDT", 90, 90, 83, True),
        ("DEADUSDT", 22, 15, 7, True),
        ("NEWUSDT", 71, 71, 64, True),
    ]
    # The 7-day return first exists on day 8.
    assert first_ranked == (_FIRST_CLOSE + 7 * DAY_MS,)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config", "panel.parquet", "root"]


def test_a_missing_month_file_fails_closed(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    root, manifest = _universe_root(tmp_path)
    (root / "parquet/hist_etl/binance/um/klines_1d/DEADUSDT-2026-01.parquet.sources.json").unlink()
    out = tmp_path / "panel.parquet"
    assert main(_panel_args(root, manifest, out)) == 2
    message = capsys.readouterr().err
    assert "1 month file(s) of u are missing" in message
    assert "DEADUSDT-2026-01.parquet" in message
    assert not out.exists()


def test_a_missing_day_inside_a_run_fails_closed(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    _write_month(root, "klines_1d", "AAAUSDT", "2026-02", range(1, 29))
    _write_month(root, "klines_1d", "AAAUSDT", "2026-02", range(1, 14))
    out = tmp_path / "panel.parquet"
    assert main(_panel_args(root, manifest, out)) == 2
    assert "AAAUSDT misses the daily bar of 2026-02-14" in capsys.readouterr().err
    assert not out.exists()


def test_a_still_published_run_must_reach_the_end(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    _write_month(root, "klines_1d", "AAAUSDT", "2026-03", range(1, 31))
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet")) == 2
    assert "AAAUSDT misses the daily bar of 2026-03-31" in capsys.readouterr().err


def test_a_run_cut_by_the_start_must_begin_on_it(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    _write_month(root, "klines_1d", "AAAUSDT", "2026-02", range(3, 29))
    args = _panel_args(root, manifest, tmp_path / "panel.parquet")
    args[args.index("--start") + 1] = "2026-02-01"
    assert main(args) == 2
    assert "AAAUSDT misses the daily bar of 2026-02-01" in capsys.readouterr().err


def test_a_funding_hole_on_a_traded_day_fails_closed(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # February funding stops after the 20th while AAAUSDT keeps trading.
    _write_month(root, "funding", "AAAUSDT", "2026-02", range(1, 21))
    out = tmp_path / "panel.parquet"
    assert main(_panel_args(root, manifest, out)) == 2
    message = capsys.readouterr().err
    assert "AAAUSDT traded on 2026-02-21 with a funding settlement missing" in message
    assert not out.exists()


def test_a_start_inside_a_listing_month_needs_no_bar_on_it(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    args = _panel_args(root, manifest, tmp_path / "panel.parquet")
    args[args.index("--start") + 1] = "2026-01-05"
    assert main(args) == 0


def test_an_end_before_a_mid_month_listing_needs_no_bar(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    # NEWUSDT lists on Jan 20, after the panel ends.
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet", end="2026-01-10")) == 0


def test_a_start_after_a_listing_still_sees_a_missing_bar_on_it(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    january = root / "parquet/hist_etl/binance/um/klines_1d/NEWUSDT-2026-01.parquet"
    # Drop Jan 25 from NEWUSDT's listing month; the panel starts on that day.
    connection = duckdb.connect()
    try:
        source = "'" + str(january).replace("'", "''") + "'"
        connection.execute(
            f"COPY (SELECT * FROM read_parquet({source}) WHERE day(ts) <> 25) "
            f"TO {source[:-1]}.tmp' (FORMAT PARQUET)"
        )
    finally:
        connection.close()
    Path(f"{january}.tmp").replace(january)
    args = _panel_args(root, manifest, tmp_path / "panel.parquet")
    args[args.index("--start") + 1] = "2026-01-25"
    assert main(args) == 2
    assert "NEWUSDT misses the daily bar of 2026-01-25" in capsys.readouterr().err


def test_the_funding_month_before_a_cut_start_is_required(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    (root / "parquet/hist_etl/binance/um/funding/AAAUSDT-2026-01.parquet.sources.json").unlink()
    args = _panel_args(root, manifest, tmp_path / "panel.parquet")
    args[args.index("--start") + 1] = "2026-02-01"
    assert main(args) == 2
    assert "funding/AAAUSDT-2026-01.parquet" in capsys.readouterr().err


def test_an_end_inside_a_delisting_month_needs_no_bar_before_it(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    # No flat bars after the delisting: the klines stop on Jan 24.
    _write_month(root, "klines_1d", "DEADUSDT", "2026-01", range(10, 25))
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet", end="2026-01-28")) == 0


def test_funding_may_stop_mid_month_at_a_closed_run_end(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    # DEADUSDT keeps trading to Jan 31, but its funding run ends after Jan 24 08:00.
    _write_month(root, "klines_1d", "DEADUSDT", "2026-01", range(10, 32))
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet")) == 0


def test_a_midnight_settlement_in_the_previous_month_file_counts(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    # Feb 1 00:00 is stamped 10 ms early, so it sits in the January file.
    _write_month(
        root, "funding", "AAAUSDT", "2026-01", range(1, 32), slots=range(0, 94), early_last_ms=10
    )
    _write_month(root, "funding", "AAAUSDT", "2026-02", range(1, 29), slots=range(1, 84))
    out = tmp_path / "panel.parquet"
    args = _panel_args(root, manifest, out)
    args[args.index("--start") + 1] = "2026-02-01"
    assert main(args) == 0
    connection = duckdb.connect()
    try:
        first = connection.execute(
            "SELECT funding_settlements, funding_covered FROM read_parquet(?) "
            "WHERE symbol = 'AAAUSDT' ORDER BY ts LIMIT 1",
            [str(out)],
        ).fetchone()
    finally:
        connection.close()
    assert first == (3, True)


def test_the_open_month_is_expected_only_inside_the_range(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    out = tmp_path / "panel.parquet"
    # AAAUSDT's run is open; April has no file, which is fine before April.
    assert main(_panel_args(root, manifest, out, end="2026-04-01")) == 0
    assert main(_panel_args(root, manifest, tmp_path / "late.parquet", end="2026-04-02")) == 2


def test_an_unknown_group_or_bad_window_is_refused(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    args = _panel_args(root, manifest, tmp_path / "panel.parquet")
    unknown = [*args]
    unknown[unknown.index("u")] = "v"
    assert main(unknown) == 2
    assert "v is not a binance_universe entry" in capsys.readouterr().err
    bad = [*args]
    bad[bad.index("--vol-window") + 1] = "1"
    assert main(bad) == 2
    assert "two daily returns" in capsys.readouterr().err
