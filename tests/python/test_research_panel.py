"""Deterministic tests for the daily cross-sectional panel."""

from __future__ import annotations

import hashlib
import json
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
    RankRule,
    Settlement,
    build_symbol_rows,
    carry_value,
    funding_hole_closes,
    rank_by_volume,
)
from research.bar_tables.panel_io import (
    load_exclusions,
    load_panel_manifest,
    universe_files,
)
from research.bar_tables.trend import BarTableError
from research.hist_etl.manifest import load_manifest
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


def test_a_relisting_day_is_judged_like_a_fresh_listing() -> None:
    bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0])
    relisted = [*bars[:2], *(replace(bar, ts=bar.ts + 30 * DAY_MS) for bar in bars[2:])]
    settlements = _funding(relisted)
    with_history = _rows(relisted, settlements)
    fresh = build_symbol_rows("AAAUSDT", relisted[2:], settlements[6:], _SPEC)
    assert with_history[2:] == fresh
    assert with_history[2].funding_covered


def test_a_hole_at_the_end_of_a_day_leaves_the_next_day_covered() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    # Day two misses its 16:00 settlement; day three has all of its own.
    settlements = [item for index, item in enumerate(_funding(bars)) if index != 5]
    rows = _rows(bars, settlements)
    assert [row.funding_covered for row in rows] == [True, False, True]


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


def test_volume_without_trades_counts_as_zero() -> None:
    bars = [
        *_bars([100.0, 101.0]),
        DailyBar(_FIRST_CLOSE + 2 * DAY_MS, 102.0, 5_000.0, 0),
    ]
    rows = _rows(bars)
    assert rows[2].traded is False
    assert rows[2].mean_quote_volume == 500.0


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
        [DailyBar(_FIRST_CLOSE, 1.0, math.nan, 1)],
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
        {"lookbacks": (999_999_999,)},
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
            "count(volume_rank), count(*) FILTER (WHERE traded AND ret_7d IS NOT NULL "
            "AND vol_5d IS NOT NULL AND funding_3d IS NOT NULL), bool_and(available_ts = ts) "
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
        "carry_3d",
        "volume_rank",
    ]
    # A symbol ranks once it traded with five days of volume: DEADUSDT from
    # Jan 14 to its last traded day, Jan 24; NEWUSDT from Jan 24.
    # Every feature is there from the eighth traded day; for DEADUSDT up to
    # Jan 23, its last with three covered funding days.
    assert counts == [
        ("AAAUSDT", 90, 90, 86, 83, True),
        ("DEADUSDT", 22, 15, 11, 7, True),
        ("NEWUSDT", 71, 71, 67, 64, True),
    ]
    # The 5-day volume first exists on day 5.
    assert first_ranked == (_FIRST_CLOSE + 4 * DAY_MS,)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config", "panel.parquet", "root"]


def test_a_row_does_not_depend_on_where_the_panel_starts(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    early = tmp_path / "early.parquet"
    late = tmp_path / "late.parquet"
    assert main(_panel_args(root, manifest, early)) == 0
    args = _panel_args(root, manifest, late)
    args[args.index("--start") + 1] = "2026-02-01"
    assert main(args) == 0
    connection = duckdb.connect()
    try:
        differing = connection.execute(
            "SELECT count(*) FROM (SELECT * FROM read_parquet(?) EXCEPT "
            "SELECT * FROM read_parquet(?) WHERE ts >= ?)",
            [str(late), str(early), _FIRST_CLOSE + 31 * DAY_MS],
        ).fetchone()
        first_ranked = connection.execute(
            "SELECT min(ts) FROM read_parquet(?) WHERE volume_rank IS NOT NULL", [str(late)]
        ).fetchone()
    finally:
        connection.close()
    assert differing == (0,)
    # January is the warm-up, so February's first day already ranks.
    assert first_ranked == (_FIRST_CLOSE + 31 * DAY_MS,)


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


def test_funding_that_stops_early_in_a_still_published_run_fails(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # March funding stops after Mar 10 while AAAUSDT trades to Mar 31.
    _write_month(root, "funding", "AAAUSDT", "2026-03", range(1, 11))
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet")) == 2
    message = capsys.readouterr().err
    assert "AAAUSDT traded on 2026-03-11 with a funding settlement missing" in message


def test_funding_that_starts_late_in_a_cut_window_fails(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # February funding starts on Feb 5; the panel starts in February.
    _write_month(root, "funding", "AAAUSDT", "2026-02", range(5, 29))
    args = _panel_args(root, manifest, tmp_path / "panel.parquet")
    args[args.index("--start") + 1] = "2026-02-10"
    assert main(args) == 2
    message = capsys.readouterr().err
    assert "AAAUSDT traded on 2026-02-01 with a funding settlement missing" in message


def test_a_listing_month_window_may_end_before_the_first_settlement(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    # NEWUSDT trades from Jan 20, but its funding starts on Feb 1.
    _write_month(root, "funding", "NEWUSDT", "2026-01", range(32, 32))
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet", end="2026-02-01")) == 0


def test_a_window_ending_on_a_return_to_eight_hours_is_not_a_hole(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    # Feb 28 runs 4h funding to 16:00; Mar 1 opens with an 8h settlement.
    directory = root / "parquet" / "hist_etl" / "binance" / "um" / "funding"
    feb = directory / "AAAUSDT-2026-02.parquet"
    feb_open_us = (date(2026, 2, 1) - date(1970, 1, 1)).days * 86_400_000_000
    last_day_us = feb_open_us + 27 * 86_400_000_000
    connection = duckdb.connect()
    try:
        target = "'" + str(feb).replace("'", "''") + "'"
        connection.execute(
            "COPY (SELECT make_timestamp(? + i * 28800000000) AS calc_time, 8 AS "
            "funding_interval_hours, 0.0001 AS last_funding_rate, 'AAAUSDT' AS symbol "
            "FROM range(0, 81) t(i) UNION ALL SELECT make_timestamp(? + h * 3600000000), 4, "
            f"0.0001, 'AAAUSDT' FROM unnest([0, 4, 8, 12, 16]) t(h)) TO {target} (FORMAT PARQUET)",
            [feb_open_us, last_day_us],
        )
    finally:
        connection.close()
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet", end="2026-03-01")) == 0


def test_an_edge_gap_is_caught_past_an_untraded_day(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # Funding stops after Mar 10; Mar 11 has no trades, Mar 12 on trade again.
    _write_month(root, "funding", "AAAUSDT", "2026-03", range(1, 11))
    directory = root / "parquet" / "hist_etl" / "binance" / "um" / "klines_1d"
    march = directory / "AAAUSDT-2026-03.parquet"
    connection = duckdb.connect()
    try:
        source = "'" + str(march).replace("'", "''") + "'"
        connection.execute(
            f"COPY (SELECT * REPLACE (CASE WHEN day(ts) = 11 THEN 0 ELSE trade_count END "
            f"AS trade_count) FROM read_parquet({source})) TO {source[:-1]}.tmp' (FORMAT PARQUET)"
        )
    finally:
        connection.close()
    Path(f"{march}.tmp").replace(march)
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet")) == 2
    assert (
        "AAAUSDT traded on 2026-03-12 with a funding settlement missing" in capsys.readouterr().err
    )


def test_a_bad_funding_interval_fails_instead_of_hanging(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    directory = root / "parquet" / "hist_etl" / "binance" / "um" / "funding"
    february = directory / "AAAUSDT-2026-02.parquet"
    connection = duckdb.connect()
    try:
        source = "'" + str(february).replace("'", "''") + "'"
        connection.execute(
            f"COPY (SELECT * REPLACE (0 AS funding_interval_hours) FROM read_parquet({source})) "
            f"TO {source[:-1]}.tmp' (FORMAT PARQUET)"
        )
    finally:
        connection.close()
    Path(f"{february}.tmp").replace(february)
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet")) == 2
    assert "not 1 to 24 hours" in capsys.readouterr().err


def test_a_malformed_manifest_is_reported(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    root, manifest = _universe_root(tmp_path)
    manifest.write_text("[[binance_universe]\n", encoding="utf-8")
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet")) == 2
    assert "Manifest:" in capsys.readouterr().err


def test_funding_may_start_late_only_inside_the_listing_month(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # NEWUSDT trades from Jan 20, but its funding only begins on Feb 10.
    _write_month(root, "funding", "NEWUSDT", "2026-01", range(32, 32))
    _write_month(root, "funding", "NEWUSDT", "2026-02", range(10, 29))
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet")) == 2
    message = capsys.readouterr().err
    assert "NEWUSDT traded on 2026-02-01 with a funding settlement missing" in message


def test_a_hole_after_end_in_a_closed_run_month_still_fails(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # DEADUSDT's closed run is checked through January; the panel ends on
    # Jan 15, and the Jan 20 08:00 settlement is missing on a traded day.
    directory = root / "parquet" / "hist_etl" / "binance" / "um" / "funding"
    january = directory / "DEADUSDT-2026-01.parquet"
    connection = duckdb.connect()
    try:
        source = "'" + str(january).replace("'", "''") + "'"
        connection.execute(
            f"COPY (SELECT * FROM read_parquet({source}) WHERE NOT (day(calc_time) = 20 "
            f"AND hour(calc_time) = 8)) TO {source[:-1]}.tmp' (FORMAT PARQUET)"
        )
    finally:
        connection.close()
    Path(f"{january}.tmp").replace(january)
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet", end="2026-01-15")) == 2
    message = capsys.readouterr().err
    assert "DEADUSDT traded on 2026-01-20 with a funding settlement missing" in message


def test_funding_may_not_stop_early_inside_a_listing_month(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # NEWUSDT lists on Jan 20 and stays published; its funding stops after Jan 25.
    _write_month(root, "funding", "NEWUSDT", "2026-01", range(20, 26))
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet", end="2026-02-01")) == 2
    message = capsys.readouterr().err
    assert "NEWUSDT traded on 2026-01-26 with a funding settlement missing" in message


def test_the_last_day_needs_no_next_month_to_be_covered(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    # Mar 31 runs 4h funding to 16:00 and April is not published yet.
    directory = root / "parquet" / "hist_etl" / "binance" / "um" / "funding"
    march = directory / "AAAUSDT-2026-03.parquet"
    march_open_us = (date(2026, 3, 1) - date(1970, 1, 1)).days * 86_400_000_000
    last_day_us = march_open_us + 30 * 86_400_000_000
    connection = duckdb.connect()
    try:
        target = "'" + str(march).replace("'", "''") + "'"
        connection.execute(
            "COPY (SELECT make_timestamp(? + i * 28800000000) AS calc_time, 8 AS "
            "funding_interval_hours, 0.0001 AS last_funding_rate, 'AAAUSDT' AS symbol "
            "FROM range(0, 90) t(i) UNION ALL SELECT make_timestamp(? + h * 3600000000), 4, "
            f"0.0001, 'AAAUSDT' FROM unnest([0, 4, 8, 12, 16]) t(h)) TO {target} (FORMAT PARQUET)",
            [march_open_us, last_day_us],
        )
    finally:
        connection.close()
    assert main(_panel_args(root, manifest, tmp_path / "panel.parquet")) == 0


def test_a_listing_month_file_without_bars_in_reach_fails(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # NEWUSDT's January file was synced on Jan 21 and holds Jan 20 only;
    # the panel ends on Jan 25, after the listing.
    _write_month(root, "klines_1d", "NEWUSDT", "2026-01", range(20, 21))
    args = _panel_args(root, manifest, tmp_path / "panel.parquet", end="2026-01-25")
    assert main(args) == 2
    assert "NEWUSDT misses the daily bar of 2026-01-21" in capsys.readouterr().err


def test_an_empty_window_before_a_listing_needs_later_bars(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # The January file holds no bar at all after the listing day it promises.
    _write_month(root, "klines_1d", "NEWUSDT", "2026-01", range(1, 1))
    args = _panel_args(root, manifest, tmp_path / "panel.parquet", end="2026-01-10")
    assert main(args) == 2
    assert "NEWUSDT has no daily bar" in capsys.readouterr().err


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
    for flag, value in (("--start", "0005-01-01"), ("--end", "9999-12-31")):
        extreme = [*args]
        extreme[extreme.index(flag) + 1] = value
        assert main(extreme) == 2
        assert "Panel dates must lie from 2000-01-01 to 2100-01-01" in capsys.readouterr().err


# --- Carry and the rank rule ---------------------------------------------------


def test_carry_takes_every_day_with_a_rate_at_its_recorded_sum() -> None:
    bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0])
    rows = _rows(bars, _returning_to_eight_hours(bars))
    # Day two cannot be proven whole at its close, so funding_2d is empty for
    # two days; carry_2d takes the day at its recorded sum, what a holder paid.
    assert rows[1].mean_funding is None and rows[2].mean_funding is None
    assert rows[1].carry == pytest.approx(-(0.0003 + 0.005) / 2, abs=1e-10)
    assert rows[2].carry == pytest.approx(-(0.005 + 0.0003) / 2, abs=1e-10)
    assert rows[0].carry is None
    # A partial day (one settlement of three) enters at what it charged.
    partial = [item for item in _funding(bars) if settlements_index(item, bars) not in (4, 5)]
    assert _rows(bars, partial)[1].carry == pytest.approx(-(0.0003 + 0.0001) / 2, abs=1e-10)
    # A day without any settlement empties carry for the window's length.
    settlements = [item for item in _funding(bars) if not 6 <= settlements_index(item, bars) < 9]
    gapped = _rows(bars, settlements)
    assert [row.carry is None for row in gapped] == [True, False, True, True, False]
    # A gap between runs restarts the window.
    relisted = [*bars[:3], *(replace(bar, ts=bar.ts + 10 * DAY_MS) for bar in bars[3:])]
    assert _rows(relisted, _funding(relisted))[3].carry is None


def settlements_index(item: Settlement, bars: list[DailyBar]) -> int:
    """The index of an 8-hourly settlement of ``_funding(bars)``."""

    return (item.ts - (bars[0].ts + 1 - DAY_MS)) // (8 * 3_600_000)


def test_carry_orders_distinct_sums_as_minus_funding() -> None:
    sums = [0.0063, -0.0028, 0.0021, 0.0021 + 1e-8, 0.0, -0.0028 - 1e-8]
    carries = [
        carry_value(f"S{index}USDT", _FIRST_CLOSE, total, 7) for index, total in enumerate(sums)
    ]
    by_funding = sorted(range(len(sums)), key=lambda index: sums[index])
    by_carry = sorted(range(len(sums)), key=lambda index: -carries[index])
    assert by_carry == by_funding
    for total, carry in zip(sums, carries, strict=True):
        assert carry == pytest.approx(-total / 7, abs=1e-10)
    # A zero sum never writes -0.0.
    assert math.copysign(1.0, carry_value("AAAUSDT", _FIRST_CLOSE, 0.0, 7)) == 1.0
    assert math.copysign(1.0, carry_value("ZZZUSDT", _FIRST_CLOSE, -0.0, 7)) == 1.0


def test_carry_keeps_8_decimal_sums_apart_and_tied_for_any_window() -> None:
    for window in (2, 7, 8, 200, 3_650):
        for count in range(1, 400, 7):
            # The same 8-decimal sum, added up from different settlement splits.
            whole = count * 1e-8
            split = math.fsum(
                [(count // 3) * 1e-8, (count // 3) * 1e-8, (count - 2 * (count // 3)) * 1e-8]
            )
            naive = sum([1e-8] * count)
            values = {
                carry_value("AAAUSDT", _FIRST_CLOSE, total, window)
                for total in (whole, split, naive)
            }
            assert len(values) == 1
            # The next 8-decimal sum stays below it, whatever the draws.
            for symbol in ("BBBUSDT", "CCCUSDT", "1000XUSDT"):
                above = carry_value(symbol, _FIRST_CLOSE, whole + 1e-8, window)
                assert above < min(values)


def test_carry_ties_are_broken_by_symbol_and_day_not_by_the_alphabet() -> None:
    # Sums a floating-point rounding apart, all at the neutral rate.
    sums = [0.0003, 0.0001 + 0.0001 + 0.0001, 0.0002 + 0.0001, math.fsum([0.0000125] * 24)]
    assert len(set(sums)) > 1
    symbols = [f"{name}USDT" for name in ("1000PEPE", "AAA", "MMM", "ZZZ")]
    orders = set()
    for day in range(20):
        ts = _FIRST_CLOSE + day * DAY_MS
        carries = {
            symbol: carry_value(symbol, ts, total, 1)
            for symbol, total in zip(symbols, sums, strict=True)
        }
        # Equal to the step, so the draw alone decides the order...
        assert max(carries.values()) - min(carries.values()) < 1e-10
        order = tuple(sorted(symbols, key=lambda symbol: -carries[symbol]))
        draws = {
            symbol: int.from_bytes(hashlib.sha256(f"{symbol}|{ts}".encode()).digest()[:8], "big")
            for symbol in symbols
        }
        assert order == tuple(sorted(symbols, key=lambda symbol: -draws[symbol]))
        orders.add(order)
    # ...and it changes from day to day.
    assert len(orders) > 1


def test_carry_is_built_the_same_way_twice() -> None:
    bars = _bars([100.0, 101.0, 102.0, 103.0])
    assert [row.carry for row in _rows(bars)] == [row.carry for row in _rows(bars)]


def _ranked_row(symbol: str, volume: float, rate: float | None) -> PanelRow:
    return PanelRow(
        ts=_FIRST_CLOSE,
        symbol=symbol,
        close=1.0,
        quote_volume=volume,
        trades=1,
        traded=True,
        funding_rate=rate,
        funding_settlements=0 if rate is None else 3,
        funding_covered=rate is not None,
        returns=(0.1,),
        realized_vol=0.01,
        mean_quote_volume=volume,
        mean_funding=None,
    )


def test_the_rank_rule_moves_the_next_symbol_up() -> None:
    rows = [
        _ranked_row("AAAUSDT", 9.0, 0.0001),
        _ranked_row("BBBUSDT", 8.0, None),
        _ranked_row("CCCUSDT", 7.0, 0.0001),
        _ranked_row("DDDUSDT", 6.0, 0.0001),
    ]

    def ranks(rule: RankRule | None) -> list[tuple[str, int | None]]:
        return [(row.symbol, row.volume_rank) for row in rank_by_volume(rows, rule)]

    assert ranks(None) == [("AAAUSDT", 1), ("BBBUSDT", 2), ("CCCUSDT", 3), ("DDDUSDT", 4)]
    assert ranks(RankRule(excluded=frozenset({"AAAUSDT"}))) == [
        ("AAAUSDT", None),
        ("BBBUSDT", 1),
        ("CCCUSDT", 2),
        ("DDDUSDT", 3),
    ]
    assert ranks(RankRule(require_funding=True)) == [
        ("AAAUSDT", 1),
        ("BBBUSDT", None),
        ("CCCUSDT", 2),
        ("DDDUSDT", 3),
    ]
    assert ranks(RankRule(excluded=frozenset({"CCCUSDT"}), require_funding=True)) == [
        ("AAAUSDT", 1),
        ("BBBUSDT", None),
        ("CCCUSDT", None),
        ("DDDUSDT", 2),
    ]
    # Excluded rows keep every value but the rank.
    excluded = rank_by_volume(rows, RankRule(excluded=frozenset({"AAAUSDT"})))[0]
    assert replace(excluded, volume_rank=1) == rank_by_volume(rows)[0]


def _exclusions(tmp_path: Path, body: object, *, raw: str | None = None) -> Path:
    path = tmp_path / "exclude.json"
    path.write_text(raw if raw is not None else json.dumps(body), encoding="utf-8")
    return path


def _exclusion_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "format": 1,
        "universe": "u.json",
        "rule": "Contracts whose underlying is not a crypto asset.",
        "symbols": {"AAAUSDT": "commodity"},
    }
    body.update(overrides)
    return body


def _ranks(path: Path) -> dict[str, list[int | None]]:
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            "SELECT symbol, volume_rank FROM read_parquet(?) ORDER BY ts, symbol", [str(path)]
        ).fetchall()
    finally:
        connection.close()
    ranks: dict[str, list[int | None]] = {}
    for symbol, rank in rows:
        assert isinstance(symbol, str)
        assert rank is None or isinstance(rank, int)
        ranks.setdefault(symbol, []).append(rank)
    return ranks


def test_excluded_symbols_keep_their_rows_and_never_rank(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    plain = tmp_path / "plain.parquet"
    narrowed = tmp_path / "narrowed.parquet"
    assert main(_panel_args(root, manifest, plain)) == 0
    exclusions = _exclusions(tmp_path, _exclusion_body())
    args = [*_panel_args(root, manifest, narrowed), "--exclude-symbols", str(exclusions)]
    assert main(args) == 0
    digest = hashlib.sha256(exclusions.read_bytes()).hexdigest()
    assert capsys.readouterr().out.endswith(f"{narrowed}\t1\texcluded\tsha256:{digest}\n")
    before = _ranks(plain)
    after = _ranks(narrowed)
    assert after["AAAUSDT"] == [None] * len(before["AAAUSDT"])
    # AAAUSDT out-trades the others, so each of them moves up by one where
    # AAAUSDT ranked, and the ranks of a day stay 1..n without a hole.
    for symbol in ("DEADUSDT", "NEWUSDT"):
        assert after[symbol] == [None if rank is None else rank - 1 for rank in before[symbol]]
    connection = duckdb.connect()
    try:
        holes = connection.execute(
            "SELECT count(*) FROM (SELECT ts, max(volume_rank) AS top, count(volume_rank) AS n "
            "FROM read_parquet(?) GROUP BY ts) WHERE top <> n",
            [str(narrowed)],
        ).fetchone()
        unchanged = connection.execute(
            "SELECT count(*) FROM (SELECT * EXCLUDE (volume_rank) FROM read_parquet(?) "
            "EXCEPT SELECT * EXCLUDE (volume_rank) FROM read_parquet(?))",
            [str(narrowed), str(plain)],
        ).fetchone()
    finally:
        connection.close()
    assert holes == (0,)
    assert unchanged == (0,)


@pytest.mark.parametrize(
    ("body", "raw", "message"),
    [
        (_exclusion_body(extra=1), None, "exactly the keys"),
        ({key: value for key, value in _exclusion_body().items() if key != "rule"}, None, "keys"),
        (_exclusion_body(format=2), None, "format 1"),
        (_exclusion_body(format=True), None, "format 1"),
        (_exclusion_body(rule=" "), None, "rule"),
        (_exclusion_body(symbols=["AAAUSDT"]), None, "categories"),
        (_exclusion_body(symbols={"AAAUSDT": "metal"}), None, "unknown categories"),
        (_exclusion_body(symbols={"XXXUSDT": "index"}), None, "does not hold: XXXUSDT"),
        (_exclusion_body(universe="universe/u.json"), None, "reads 'u.json'"),
        (
            None,
            '{"format": 1, "universe": "u.json", "rule": "r", '
            '"symbols": {"AAAUSDT": "index", "AAAUSDT": "equity"}}',
            "duplicate keys: AAAUSDT",
        ),
        (None, "{", "Exclusion list"),
    ],
)
def test_a_bad_exclusion_list_fails_closed_and_writes_nothing(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    body: object,
    raw: str | None,
    message: str,
) -> None:
    root, manifest = _universe_root(tmp_path)
    out = tmp_path / "panel.parquet"
    exclusions = _exclusions(tmp_path, body, raw=raw)
    args = [*_panel_args(root, manifest, out), "--exclude-symbols", str(exclusions)]
    assert main(args) == 2
    assert message in capsys.readouterr().err
    assert not out.exists()


def test_the_manifest_keeps_each_universe_file_as_written(tmp_path: Path) -> None:
    _root, manifest = _universe_root(tmp_path)
    loaded = load_manifest(manifest)
    assert loaded.binance_universe_files == (("u", "universe/u.json"),)
    assert loaded.binance_groups == ("u",)


def test_an_excluded_symbol_is_checked_against_the_universe_file(tmp_path: Path) -> None:
    _root, manifest = _universe_root(tmp_path)
    # A start after DEADUSDT's only month drops its datasets, not its universe entry.
    manifest.write_text(
        '[[binance_universe]]\nid = "u"\nfile = "universe/u.json"\nstart = "2026-02-01"\n',
        encoding="utf-8",
    )
    assert not any(spec.symbol == "DEADUSDT" for spec in load_manifest(manifest).binance)
    path = _exclusions(tmp_path, _exclusion_body(symbols={"DEADUSDT": "index"}))
    loaded = load_exclusions(path, load_panel_manifest(manifest, "u"), manifest, "u")
    assert loaded.symbols == frozenset({"DEADUSDT"})
    assert loaded.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(BarTableError, match="not a binance_universe entry"):
        load_panel_manifest(manifest, "v")
    loaded_manifest = load_panel_manifest(manifest, "u")
    with pytest.raises(BarTableError, match="not a binance_universe entry"):
        universe_files(tmp_path, loaded_manifest, "v", date(2026, 1, 1), date(2026, 2, 1), _SPEC)


def test_an_empty_exclusion_path_fails_closed(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    root, manifest = _universe_root(tmp_path)
    out = tmp_path / "panel.parquet"
    assert main([*_panel_args(root, manifest, out), "--exclude-symbols", ""]) == 2
    assert "Exclusion list" in capsys.readouterr().err
    assert not out.exists()


def test_a_traded_day_without_funding_ranks_only_without_the_rule(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    root, manifest = _universe_root(tmp_path)
    # NEWUSDT lists on Jan 20 but its funding starts only on Jan 25 (slot 72
    # opens that day), which a listing month allows.
    _write_month(root, "funding", "NEWUSDT", "2026-01", range(20, 32), slots=range(72, 93))
    plain = tmp_path / "plain.parquet"
    funded = tmp_path / "funded.parquet"
    assert main(_panel_args(root, manifest, plain)) == 0
    assert main([*_panel_args(root, manifest, funded), "--rank-requires-funding"]) == 0
    assert capsys.readouterr().out.endswith(f"{funded}\trank requires funding\n")
    connection = duckdb.connect()
    try:
        query = (
            "SELECT count(*) FILTER (WHERE funding_rate IS NULL AND volume_rank IS NOT NULL), "
            "count(*) FILTER (WHERE funding_rate IS NULL AND traded) "
            "FROM read_parquet(?) WHERE symbol = 'NEWUSDT'"
        )
        plain_counts = connection.execute(query, [str(plain)]).fetchone()
        funded_counts = connection.execute(query, [str(funded)]).fetchone()
        same_elsewhere = connection.execute(
            "SELECT count(*) FROM (SELECT * FROM read_parquet(?) WHERE funding_rate IS NOT NULL "
            "AND symbol = 'NEWUSDT' EXCEPT SELECT * FROM read_parquet(?))",
            [str(funded), str(plain)],
        ).fetchone()
    finally:
        connection.close()
    # Jan 24 is the only unfunded day with five days of volume: it ranks
    # without the rule and not with it.
    assert plain_counts == (1, 5)
    assert funded_counts == (0, 5)
    assert same_elsewhere == (0,)


def test_two_builds_with_both_rules_are_identical(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    exclusions = _exclusions(tmp_path, _exclusion_body(symbols={"DEADUSDT": "index"}))
    outputs = []
    for name in ("one.parquet", "two.parquet"):
        out = tmp_path / name
        args = [
            *_panel_args(root, manifest, out),
            "--exclude-symbols",
            str(exclusions),
            "--rank-requires-funding",
        ]
        assert main(args) == 0
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]


def test_a_day_the_data_range_cut_counts_only_when_covered() -> None:
    bars = _bars([100.0, 101.0, 102.0, 103.0])
    # Day two lost its 00:00 settlement, stamped just before the cut.
    partial = [item for item in _funding(bars) if settlements_index(item, bars) != 3]
    rows = build_symbol_rows("AAAUSDT", bars, partial, _SPEC, frozenset({bars[1].ts}))
    assert [row.carry is None for row in rows] == [True, True, True, False]
    # Covered, the same day counts despite the cut.
    whole = build_symbol_rows("AAAUSDT", bars, _funding(bars), _SPEC, frozenset({bars[1].ts}))
    assert [row.carry is None for row in whole] == [True, False, False, False]


def test_an_uncovered_first_day_at_a_manifest_start_is_not_a_carry_day(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    # hist_etl dropped AAAUSDT's Feb 1 00:00 settlement, stamped before the start.
    _write_month(root, "funding", "AAAUSDT", "2026-02", range(1, 29), slots=range(1, 84))
    manifest.write_text(
        '[[binance_universe]]\nid = "u"\nfile = "universe/u.json"\nstart = "2026-02-01"\n',
        encoding="utf-8",
    )
    out = tmp_path / "panel.parquet"
    args = _panel_args(root, manifest, out)
    args[args.index("--start") + 1] = "2026-02-01"
    assert main(args) == 0
    connection = duckdb.connect()
    try:
        first_carry = connection.execute(
            "SELECT min(ts) FROM read_parquet(?) WHERE symbol = 'AAAUSDT' AND carry_3d IS NOT NULL",
            [str(out)],
        ).fetchone()
    finally:
        connection.close()
    # Feb 1 holds no settlement stamped before it, so carry_3d starts on Feb 4.
    assert first_carry == (_FIRST_CLOSE + (31 + 3) * DAY_MS,)


def test_tied_carries_keep_distinct_draws_at_large_sums() -> None:
    symbols = [f"S{index:03d}USDT" for index in range(100)]
    for total, window in ((0.2, 30), (0.0021, 7), (0.9, 30)):
        carries = {symbol: carry_value(symbol, _FIRST_CLOSE, total, window) for symbol in symbols}
        assert len(set(carries.values())) == len(symbols)
        draws = {
            symbol: int.from_bytes(
                hashlib.sha256(f"{symbol}|{_FIRST_CLOSE}".encode()).digest()[:8], "big"
            )
            for symbol in symbols
        }
        assert sorted(symbols, key=lambda symbol: -carries[symbol]) == sorted(
            symbols, key=lambda symbol: -draws[symbol]
        )


def test_an_untraded_day_restarts_the_carry_window() -> None:
    bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0], trades=[10, 0, 10, 10, 10])
    rows = _rows(bars)
    # A flat archive day pays no one's funding and is not checked by the build.
    assert [row.carry is None for row in rows] == [True, True, True, False, False]


def test_a_listing_month_first_day_counts_at_what_it_charged(tmp_path: Path) -> None:
    root, manifest = _universe_root(tmp_path)
    # AAAUSDT's first settlement is at 08:00 on Jan 1, the day it lists.
    _write_month(root, "funding", "AAAUSDT", "2026-01", range(1, 32), slots=range(1, 93))
    out = tmp_path / "panel.parquet"
    assert main(_panel_args(root, manifest, out)) == 0
    connection = duckdb.connect()
    try:
        first = connection.execute(
            "SELECT min(ts) FILTER (WHERE carry_3d IS NOT NULL), "
            "min(ts) FILTER (WHERE funding_3d IS NOT NULL) "
            "FROM read_parquet(?) WHERE symbol = 'AAAUSDT'",
            [str(out)],
        ).fetchone()
    finally:
        connection.close()
    # carry_3d takes Jan 1 at its two settlements; funding_3d waits for three
    # covered days.
    assert first == (_FIRST_CLOSE + 2 * DAY_MS, _FIRST_CLOSE + 3 * DAY_MS)
