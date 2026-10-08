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
from research.bar_tables.trend import SETTLEMENT_SLACK_MS, BarTableError
from research.hist_etl.binance_convert import binance_parquet_path
from research.hist_etl.errors import HistEtlError
from research.hist_etl.manifest import load_manifest
from research.hist_etl.models import BinanceSpec
from research.hist_etl.planning import next_month

_MISSING_SHOWN: Final = 10


@dataclass(frozen=True, slots=True)
class Run:
    """One universe run clipped to the panel, days inclusive.

    ``must_start`` and ``must_end`` say whether the panel needs that edge
    day: a run may start late only in its own listing month and end early
    only in its delisting month; a run cut by the panel range, or still
    published, has to reach the cut.
    """

    symbol: str
    first: date
    last: date
    must_start: bool
    must_end: bool


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
        first = max(spec.start, start)
        last = last_day if spec.end is None else min(spec.end, last_day)
        if first > last:
            continue
        runs.append(
            Run(
                symbol=spec.symbol,
                first=first,
                last=last,
                must_start=not spec.open_start or first > spec.start,
                must_end=not spec.open_end or spec.end is None or last < spec.end,
            )
        )
        month = date(first.year, first.month, 1)
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
    """Ranked panel rows for every symbol with a bar closing in ``[start, end)``."""

    start_ms = _day_ms(start)
    end_ms = _day_ms(end)
    bars = _read_bars(files.klines, start_ms, end_ms)
    _check_kline_runs(bars, files.kline_runs)
    # Only the range's own month files are read; a settlement stamped up to a
    # minute early still belongs to the first day.
    settlements = _read_settlements(files.funding, start_ms - SETTLEMENT_SLACK_MS, end_ms)
    rows: list[PanelRow] = []
    for symbol in sorted(bars):
        rows.extend(build_symbol_rows(symbol, bars[symbol], settlements.get(symbol, []), spec))
    if not rows:
        raise BarTableError("The universe has no daily bar inside the panel range.")
    _check_funding_runs(rows, files.funding_runs)
    return rank_by_volume(rows)


def _check_kline_runs(bars: dict[str, list[DailyBar]], runs: Sequence[Run]) -> None:
    """A run's days inside the panel are all there, edges included unless open."""

    for run in runs:
        low = _day_ms(run.first)
        high = _day_ms(run.last + timedelta(days=1))
        inside = [bar.ts for bar in bars.get(run.symbol, []) if low <= bar.ts < high]
        if not inside:
            if run.must_start or run.must_end:
                raise BarTableError(
                    f"{run.symbol} has no daily bar from {run.first} to {run.last}; "
                    "run hist_etl verify and sync."
                )
            continue
        if run.must_start and inside[0] != low + DAY_MS - 1:
            raise BarTableError(_missing_day(run.symbol, low - 1))
        if run.must_end and inside[-1] != high - 1:
            raise BarTableError(_missing_day(run.symbol, inside[-1]))
        for earlier, later in pairwise(inside):
            if later - earlier != DAY_MS:
                raise BarTableError(_missing_day(run.symbol, earlier))


def _missing_day(symbol: str, previous_close: int) -> str:
    missing = datetime.fromtimestamp((previous_close + 1) / 1000, UTC).date()
    return f"{symbol} misses the daily bar of {missing} inside a run; run hist_etl verify and sync."


def _check_funding_runs(rows: Sequence[PanelRow], runs: Sequence[Run]) -> None:
    """Inside a funding run, a traded day without full funding is a hole.

    A listing or delisting day may be partial, like the bars. A day that did
    not trade is not checked: delisted contracts carry default funding.
    """

    by_symbol: dict[str, list[PanelRow]] = defaultdict(list)
    for row in rows:
        by_symbol[row.symbol].append(row)
    for run in runs:
        first_close = _day_ms(run.first) + DAY_MS - 1
        last_close = _day_ms(run.last) + DAY_MS - 1
        for row in by_symbol.get(run.symbol, []):
            if not first_close <= row.ts <= last_close or not row.traded or row.funding_covered:
                continue
            if (row.ts == first_close and not run.must_start) or (
                row.ts == last_close and not run.must_end
            ):
                continue
            day = datetime.fromtimestamp(row.ts / 1000, UTC).date()
            raise BarTableError(
                f"{run.symbol} traded on {day} without full funding inside a funding run "
                f"({row.funding_settlements} settlement(s)); run hist_etl verify and sync."
            )


def _read_bars(files: Sequence[Path], start_ms: int, end_ms: int) -> dict[str, list[DailyBar]]:
    if not files:
        raise BarTableError("The universe has no kline month inside the panel range.")
    rows = _query(
        "SELECT symbol, epoch_ms(ts), CAST(close AS DOUBLE), CAST(quote_volume AS DOUBLE), "
        "CAST(trade_count AS BIGINT) FROM read_parquet(?) "
        "WHERE ts >= make_timestamp(?) AND ts < make_timestamp(?) ORDER BY symbol, ts",
        [[str(path) for path in files], start_ms * 1000, end_ms * 1000],
    )
    bars: dict[str, list[DailyBar]] = defaultdict(list)
    for symbol, ts, close, volume, trades in rows:
        bars[_str(symbol)].append(
            DailyBar(
                ts=_int(ts), close=_float(close), quote_volume=_float(volume), trades=_int(trades)
            )
        )
    return dict(bars)


def _read_settlements(
    files: Sequence[Path], start_ms: int, end_ms: int
) -> dict[str, list[Settlement]]:
    if not files:
        return {}
    rows = _query(
        "SELECT symbol, epoch_ms(calc_time), CAST(last_funding_rate AS DOUBLE), "
        "coalesce(funding_interval_hours, 8) FROM read_parquet(?) "
        "WHERE calc_time >= make_timestamp(?) AND calc_time < make_timestamp(?) "
        "ORDER BY symbol, calc_time",
        [[str(path) for path in files], start_ms * 1000, end_ms * 1000],
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
