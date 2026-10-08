"""Read a hist_etl universe into a cross-sectional panel and write it as Parquet.

The universe's own manifest entry says which symbol months must exist. A
month file that is missing, or a day missing inside a run, fails the build:
a half-synced universe would otherwise drop symbols without a trace, and
the symbols most likely to be missing are the delisted ones, which is
survivorship bias by accident.
"""

from __future__ import annotations

import csv
import os
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Final

import duckdb

from research.bar_tables.panel import (
    DAY_MS,
    DailyBar,
    PanelRow,
    PanelSpec,
    Settlement,
    build_symbol_rows,
    rank_by_volume,
)
from research.bar_tables.trend import BarTableError
from research.hist_etl.binance_convert import binance_parquet_path
from research.hist_etl.errors import HistEtlError
from research.hist_etl.manifest import load_manifest
from research.hist_etl.models import BinanceSpec
from research.hist_etl.planning import next_month

_MISSING_SHOWN: Final = 10


@dataclass(frozen=True, slots=True)
class Run:
    """The days of one universe run that the panel's month files hold.

    The window spans whole months, the panel's months inside the run, so an
    edge is judged from the month file and not from where the panel range
    cuts it. A still-published run ends on the panel's last day instead,
    as its newest month may still be growing.

    Universe runs are whole months: in a run's listing month its first bar
    may come on any day (``late_start``), and in its delisting month its
    last bar may (``early_end``). Every other edge has to be there.
    """

    symbol: str
    first: date
    last: date
    late_start: bool
    early_end: bool


@dataclass(frozen=True, slots=True)
class UniverseFiles:
    """The month files a panel over ``[start, end)`` reads, per dataset."""

    klines: tuple[Path, ...]
    funding: tuple[Path, ...]
    kline_runs: tuple[Run, ...]
    funding_runs: tuple[Run, ...]


def universe_files(
    root: Path, manifest_path: Path, group: str, start: date, end: date
) -> UniverseFiles:
    """Every month file of ``group`` inside ``[start, end)``; any missing one fails."""

    try:
        manifest = load_manifest(manifest_path)
    except HistEtlError as exc:
        raise BarTableError(f"Manifest: {exc}") from exc
    if group not in manifest.binance_groups:
        raise BarTableError(f"{group} is not a binance_universe entry of {manifest_path}.")
    specs = [spec for spec in manifest.binance if spec.group == group]
    klines = [spec for spec in specs if spec.dataset == "klines"]
    funding = [spec for spec in specs if spec.dataset == "fundingRate"]
    if not klines or not funding:
        raise BarTableError(f"{group} must expand into both klines and fundingRate datasets.")
    if any(spec.interval != "1d" for spec in klines):
        raise BarTableError(f"{group} klines must be 1d for a daily panel.")
    kline_files, kline_runs, missing = _month_files(root, klines, start, end)
    funding_files, funding_runs, funding_missing = _month_files(root, funding, start, end)
    missing += funding_missing
    if missing:
        shown = ", ".join(str(path) for path in missing[:_MISSING_SHOWN])
        raise BarTableError(
            f"{len(missing)} month file(s) of {group} are missing or have no sidecar; "
            f"sync the universe first. First: {shown}"
        )
    return UniverseFiles(
        klines=kline_files,
        funding=funding_files,
        kline_runs=kline_runs,
        funding_runs=funding_runs,
    )


def _month_files(
    root: Path, specs: Sequence[BinanceSpec], start: date, end: date
) -> tuple[tuple[Path, ...], tuple[Run, ...], list[Path]]:
    files: list[Path] = []
    runs: list[Run] = []
    missing: list[Path] = []
    last_day = end - timedelta(days=1)
    for spec in specs:
        cut_first = max(spec.start, start)
        cut_last = last_day if spec.end is None else min(spec.end, last_day)
        if cut_first > cut_last:
            continue
        first = max(spec.start, date(cut_first.year, cut_first.month, 1))
        last = cut_last if spec.end is None else min(spec.end, _month_end(cut_last))
        runs.append(
            Run(
                symbol=spec.symbol,
                first=first,
                last=last,
                late_start=spec.open_start and _same_month(first, spec.start),
                early_end=spec.open_end and spec.end is not None and _same_month(last, spec.end),
            )
        )
        month = date(first.year, first.month, 1)
        if spec.dataset == "fundingRate" and month > date(spec.start.year, spec.start.month, 1):
            # A midnight settlement stamped just early sits in the month before.
            month = _month_before(month)
        while month <= last:
            path = binance_parquet_path(root, spec, f"{month.year:04d}-{month.month:02d}")
            # hist_etl writes the sidecar last; a file without one is not its output.
            if path.is_file() and path.with_name(path.name + ".sources.json").is_file():
                files.append(path)
            else:
                missing.append(path)
            month = next_month(month)
    return tuple(sorted(set(files))), tuple(runs), missing


def build_panel(files: UniverseFiles, spec: PanelSpec, start: date, end: date) -> list[PanelRow]:
    """Ranked panel rows for every symbol with a bar closing in ``[start, end)``.

    The month files are read whole and checked whole, and rows are cut to the
    range only then. Features of the first rows can so use bars from earlier
    in the start's month, which are past data.
    """

    bars = _read_bars(files.klines)
    _check_kline_runs(bars, files.kline_runs)
    settlements = _read_settlements(files.funding)
    rows: list[PanelRow] = []
    for symbol in sorted(bars):
        rows.extend(build_symbol_rows(symbol, bars[symbol], settlements.get(symbol, []), spec))
    _check_funding_runs(rows, files.funding_runs)
    start_ms = _day_ms(start)
    end_ms = _day_ms(end)
    inside = [row for row in rows if start_ms <= row.ts < end_ms]
    if not inside:
        raise BarTableError("The universe has no daily bar inside the panel range.")
    return rank_by_volume(inside)


def _check_kline_runs(bars: dict[str, list[DailyBar]], runs: Sequence[Run]) -> None:
    """Every day of a run's window has its bar, edges included unless open."""

    for run in runs:
        low = _close_ms(run.first)
        high = _close_ms(run.last)
        inside = [bar.ts for bar in bars.get(run.symbol, []) if low <= bar.ts <= high]
        if not inside:
            # A still-published run's window can end inside its listing
            # month, before the listing.
            if run.late_start and _same_month(run.first, run.last):
                continue
            raise BarTableError(
                f"{run.symbol} has no daily bar from {run.first} to {run.last}; "
                "run hist_etl verify and sync."
            )
        first_allowed = _close_ms(_month_end(run.first)) if run.late_start else low
        if inside[0] > first_allowed:
            raise BarTableError(_missing_day(run.symbol, low - DAY_MS))
        last_allowed = _close_ms(date(run.last.year, run.last.month, 1)) if run.early_end else high
        if inside[-1] < last_allowed:
            raise BarTableError(_missing_day(run.symbol, inside[-1]))
        for earlier, later in pairwise(inside):
            if later - earlier != DAY_MS:
                raise BarTableError(_missing_day(run.symbol, earlier))


def _missing_day(symbol: str, previous_close: int) -> str:
    missing = datetime.fromtimestamp((previous_close + 1) / 1000, UTC).date()
    return f"{symbol} misses the daily bar of {missing} inside a run; run hist_etl verify and sync."


def _check_funding_runs(rows: Sequence[PanelRow], runs: Sequence[Run]) -> None:
    """Inside a funding run, a traded day without full funding is a hole.

    In a listing month, traded days before the run's first covered day may
    be partial, and in a delisting month, traded days after its last one,
    as funding starts and stops mid-day. A day that did not trade is not
    checked: delisted contracts carry default funding.
    """

    by_symbol: dict[str, list[PanelRow]] = defaultdict(list)
    for row in rows:
        by_symbol[row.symbol].append(row)
    for run in runs:
        inside = [
            row
            for row in by_symbol.get(run.symbol, [])
            if _close_ms(run.first) <= row.ts <= _close_ms(run.last)
        ]
        covered = [row.ts for row in inside if row.funding_covered]
        starts = covered[0] if covered else None
        stops = covered[-1] if covered else None
        listing_month_end = _close_ms(_month_end(run.first))
        delisting_month_start = _close_ms(date(run.last.year, run.last.month, 1))
        for row in inside:
            if not row.traded or row.funding_covered:
                continue
            if (
                run.late_start
                and row.ts <= listing_month_end
                and (starts is None or row.ts < starts)
            ):
                continue
            if (
                run.early_end
                and row.ts >= delisting_month_start
                and (stops is None or row.ts > stops)
            ):
                continue
            day = datetime.fromtimestamp(row.ts / 1000, UTC).date()
            raise BarTableError(
                f"{run.symbol} traded on {day} without full funding inside a funding run "
                f"({row.funding_settlements} settlement(s)); run hist_etl verify and sync."
            )


def _same_month(left: date, right: date) -> bool:
    return (left.year, left.month) == (right.year, right.month)


def _month_end(day: date) -> date:
    return next_month(date(day.year, day.month, 1)) - timedelta(days=1)


def _month_before(day: date) -> date:
    first = date(day.year, day.month, 1)
    return date(first.year - 1, 12, 1) if first.month == 1 else date(first.year, first.month - 1, 1)


def _close_ms(day: date) -> int:
    """The close time of the daily bar that opens on ``day``."""

    return _day_ms(day) + DAY_MS - 1


def _read_bars(files: Sequence[Path]) -> dict[str, list[DailyBar]]:
    if not files:
        raise BarTableError("The universe has no kline month inside the panel range.")
    rows = _query(
        "SELECT symbol, epoch_ms(ts), CAST(close AS DOUBLE), CAST(quote_volume AS DOUBLE), "
        "CAST(trade_count AS BIGINT) FROM read_parquet(?) ORDER BY symbol, ts",
        [[str(path) for path in files]],
    )
    bars: dict[str, list[DailyBar]] = defaultdict(list)
    for symbol, ts, close, volume, trades in rows:
        bars[_str(symbol)].append(
            DailyBar(
                ts=_int(ts), close=_float(close), quote_volume=_float(volume), trades=_int(trades)
            )
        )
    return dict(bars)


def _read_settlements(files: Sequence[Path]) -> dict[str, list[Settlement]]:
    if not files:
        return {}
    rows = _query(
        "SELECT symbol, epoch_ms(calc_time), CAST(last_funding_rate AS DOUBLE), "
        "coalesce(funding_interval_hours, 8) FROM read_parquet(?) ORDER BY symbol, calc_time",
        [[str(path) for path in files]],
    )
    settlements: dict[str, list[Settlement]] = defaultdict(list)
    for symbol, ts, rate, hours in rows:
        settlements[_str(symbol)].append(
            Settlement(ts=_int(ts), rate=_float(rate), interval_hours=_int(hours))
        )
    return dict(settlements)


def write_panel_parquet(rows: Sequence[PanelRow], spec: PanelSpec, out: Path) -> None:
    """Write the rows, ordered by ``ts`` and symbol; ``available_ts`` equals ``ts``.

    Like the trend table, the rows go through a CSV of shortest round-trip
    floats that DuckDB reads back exactly. An empty field is NULL.
    """

    features = [f"ret_{lookback}d" for lookback in spec.lookbacks]
    features += [
        f"vol_{spec.vol_window}d",
        f"qv_{spec.volume_window}d",
        f"funding_{spec.funding_window}d",
    ]
    names = ["ts", "available_ts", "symbol", "close", "quote_volume", "trades", "traded"]
    names += ["funding_rate", "funding_settlements", "funding_covered", *features, "volume_rank"]
    integers = {"ts", "available_ts", "trades", "funding_settlements", "volume_rank"}
    kinds = {"symbol": "VARCHAR", "traded": "BOOLEAN", "funding_covered": "BOOLEAN"}
    types = ", ".join(
        f"'{name}': '{kinds.get(name, 'BIGINT' if name in integers else 'DOUBLE')}'"
        for name in names
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=out.parent, prefix=f"{out.name}.", suffix=".csv")
    os.close(descriptor)
    staged = Path(name)
    partial = staged.with_suffix(".partial")
    try:
        with staged.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(names)
            for row in rows:
                writer.writerow(
                    [
                        row.ts,
                        row.ts,
                        row.symbol,
                        repr(row.close),
                        repr(row.quote_volume),
                        row.trades,
                        "true" if row.traded else "false",
                        _cell(row.funding_rate),
                        row.funding_settlements,
                        "true" if row.funding_covered else "false",
                        *(_cell(value) for value in row.returns),
                        _cell(row.realized_vol),
                        _cell(row.mean_quote_volume),
                        _cell(row.mean_funding),
                        "" if row.volume_rank is None else row.volume_rank,
                    ]
                )
        connection = duckdb.connect()
        try:
            connection.execute(
                f"COPY (SELECT * FROM read_csv({_literal(staged)}, header = true, "
                f"nullstr = '', columns = {{{types}}}) ORDER BY ts, symbol) "
                f"TO {_literal(partial)} (FORMAT PARQUET)"
            )
        finally:
            connection.close()
        partial.replace(out)
    finally:
        staged.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def _cell(value: float | None) -> str:
    return "" if value is None else repr(value)


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _day_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def _query(sql: str, parameters: list[object]) -> list[tuple[object, ...]]:
    connection = duckdb.connect()
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def _str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise BarTableError(f"Expected a symbol, got {value!r}.")
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BarTableError(f"Expected an integer, got {value!r}.")
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BarTableError(f"Expected a number, got {value!r}.")
    return float(value)
