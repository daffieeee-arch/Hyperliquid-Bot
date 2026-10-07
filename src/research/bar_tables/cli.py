"""Build a harness bar table from hist_etl Binance Parquet.

    python -m research.bar_tables trend --root "$HIST_ARCHIVES_ROOT" \
        --market um --symbol BTCUSDT --interval 1h \
        --start 2020-01-01 --end 2026-10-01 \
        --lookbacks 168,672,2016 --vol-window 168 --out bars.parquet

``--funding-means 3,21 --funding-baseline 0.0001`` adds a
``funding_tilt_<K>`` column per count: the baseline minus the mean of the
last ``K`` funding settlements known at the bar's close.

Bars with a close time in ``[start, end)`` are read; the first output bar
follows the warm-up (the longest lookback or the vol window, whichever is
longer). Nothing is written when the inputs fail a point-in-time check.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from research.bar_tables.trend import BarTableError, TrendRow, build_trend_rows
from research.hist_etl.models import INTERVAL_SECONDS, parquet_slug

_HOUR_MS = 3_600_000


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = _root(args.root)
        lookbacks = _lookbacks(args.lookbacks)
        funding_means, funding_baseline = _funding_tilt_options(
            args.funding_means, args.funding_baseline
        )
        start_ms = _date_ms(args.start, "--start")
        end_ms = _date_ms(args.end, "--end")
        if end_ms <= start_ms:
            raise BarTableError("--end must be after --start.")
        if args.interval not in INTERVAL_SECONDS:
            raise BarTableError(f"Unknown interval {args.interval}.")
        closes = _read_closes(root, args.market, args.symbol, args.interval, start_ms, end_ms)
        funding = _read_funding(root, args.market, args.symbol, start_ms, end_ms)
        rows = build_trend_rows(
            closes,
            funding,
            lookbacks=lookbacks,
            vol_window=args.vol_window,
            bar_ms=INTERVAL_SECONDS[args.interval] * 1000,
            max_funding_gap_ms=args.max_funding_gap_hours * _HOUR_MS,
            funding_means=funding_means,
            funding_baseline=funding_baseline,
        )
        write_trend_parquet(rows, lookbacks, Path(args.out), funding_means)
    except (BarTableError, duckdb.Error, OSError) as exc:
        print(f"bar_tables: {exc}", file=sys.stderr)
        return 2
    print(f"bar_tables\twrote\t{len(rows)}\trows\t{args.out}")
    return 0


def write_trend_parquet(
    rows: Sequence[TrendRow],
    lookbacks: Sequence[int],
    out: Path,
    funding_means: Sequence[int] = (),
) -> None:
    """Write the rows; ``available_ts`` equals ``ts``, the close the values were known at.

    The rows go through a CSV of shortest round-trip floats, which DuckDB reads
    back exactly. Binding each value as a query parameter instead costs DuckDB
    an import attempt per value, minutes for a few years of hourly bars.
    """

    names = ["ts", "available_ts", "close", *(f"ret_{lookback}" for lookback in lookbacks)]
    names += ["trend_score", "realized_vol", "funding_rate"]
    names += [f"funding_tilt_{count}" for count in funding_means]
    types = ", ".join(
        f"'{name}': '{'BIGINT' if name in {'ts', 'available_ts'} else 'DOUBLE'}'" for name in names
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    # Unique scratch names, so two builds to one output cannot share a file. They
    # are not hidden, so files a killed build leaves behind are easy to spot.
    descriptor, name = tempfile.mkstemp(dir=out.parent, prefix=f"{out.name}.", suffix=".csv")
    os.close(descriptor)
    staged = Path(name)
    # Not *.parquet, so a glob over the output folder never reads a half-written table.
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
                        repr(row.close),
                        *(repr(value) for value in row.returns),
                        repr(row.trend_score),
                        repr(row.realized_vol),
                        repr(row.funding_rate),
                        *(repr(value) for value in row.funding_tilts),
                    ]
                )
        connection = duckdb.connect()
        try:
            connection.execute(
                f"COPY (SELECT * FROM read_csv({_literal(staged)}, header = true, "
                f"columns = {{{types}}}) ORDER BY ts) TO {_literal(partial)} (FORMAT PARQUET)"
            )
        finally:
            connection.close()
        partial.replace(out)
    finally:
        staged.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def _literal(path: Path) -> str:
    """A SQL string literal; DuckDB binds the parameters of one COPY out of order."""

    return "'" + str(path).replace("'", "''") + "'"


def _read_closes(
    root: Path, market: str, symbol: str, interval: str, start_ms: int, end_ms: int
) -> list[tuple[int, float]]:
    files = _files(root, market, parquet_slug("klines", interval), symbol)
    rows = _query(
        "SELECT epoch_ms(ts), CAST(close AS DOUBLE) FROM read_parquet(?) "
        "WHERE ts >= make_timestamp(?) AND ts < make_timestamp(?) ORDER BY ts",
        [files, start_ms * 1000, end_ms * 1000],
    )
    return [(_int(ts), _float(close)) for ts, close in rows]


def _read_funding(
    root: Path, market: str, symbol: str, start_ms: int, end_ms: int
) -> list[tuple[int, float]]:
    files = _files(root, market, parquet_slug("fundingRate", None), symbol)
    rows = _query(
        "SELECT epoch_ms(calc_time), CAST(last_funding_rate AS DOUBLE) FROM read_parquet(?) "
        "WHERE calc_time >= make_timestamp(?) AND calc_time < make_timestamp(?) "
        "ORDER BY calc_time",
        [files, start_ms * 1000, end_ms * 1000],
    )
    return [(_int(ts), _float(rate)) for ts, rate in rows]


def _files(root: Path, market: str, slug: str, symbol: str) -> list[str]:
    directory = root / "parquet" / "hist_etl" / "binance" / market / slug
    files = sorted(str(path) for path in directory.glob(f"{symbol}-*.parquet"))
    if not files:
        raise BarTableError(f"No {slug} Parquet for {symbol} under {directory}.")
    return files


def _query(sql: str, parameters: list[object]) -> list[tuple[object, ...]]:
    connection = duckdb.connect()
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BarTableError(f"Expected an integer timestamp, got {value!r}.")
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BarTableError(f"Expected a number, got {value!r}.")
    return float(value)


def _root(value: str | None) -> Path:
    raw = value or os.environ.get("HIST_ARCHIVES_ROOT")
    if not raw:
        raise BarTableError("Pass --root or set HIST_ARCHIVES_ROOT.")
    return Path(raw)


def _lookbacks(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(token) for token in value.split(","))
    except ValueError as exc:
        raise BarTableError(f"--lookbacks must be comma-separated bar counts: {value}") from exc


def _funding_tilt_options(
    means: str | None, baseline: float | None
) -> tuple[tuple[int, ...], float]:
    if means is None and baseline is None:
        return (), 0.0
    if means is None or baseline is None:
        raise BarTableError("--funding-means and --funding-baseline go together.")
    try:
        counts = tuple(int(token) for token in means.split(","))
    except ValueError as exc:
        raise BarTableError(f"--funding-means must be comma-separated counts: {means}") from exc
    return counts, baseline


def _date_ms(value: str, flag: str) -> int:
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise BarTableError(f"{flag} must be a YYYY-MM-DD date: {value}") from exc
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research.bar_tables", description="Build point-in-time harness bar tables."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    trend = sub.add_parser("trend", help="Close, trend score, realized vol, and funding.")
    trend.add_argument("--root", help="Archive root. Defaults to HIST_ARCHIVES_ROOT.")
    trend.add_argument(
        "--market",
        default="um",
        choices=["um"],
        help="Binance USD-M perps; funding is required, so spot is not offered.",
    )
    trend.add_argument("--symbol", required=True)
    trend.add_argument("--interval", default="1h")
    trend.add_argument("--start", required=True, help="First bar close date, UTC.")
    trend.add_argument("--end", required=True, help="Exclusive end date, UTC.")
    trend.add_argument("--lookbacks", required=True, help="Comma-separated bar counts.")
    trend.add_argument("--vol-window", type=int, required=True, help="Bars in the vol window.")
    trend.add_argument(
        "--max-funding-gap-hours",
        type=int,
        default=9,
        help="Longest allowed time without a funding settlement (default 9).",
    )
    trend.add_argument(
        "--funding-means",
        help="Comma-separated settlement counts K; adds funding_tilt_<K> columns.",
    )
    trend.add_argument(
        "--funding-baseline",
        type=float,
        help="The rate each funding_tilt_<K> is measured from (baseline - mean).",
    )
    trend.add_argument("--out", required=True, help="Output Parquet path.")
    return parser
