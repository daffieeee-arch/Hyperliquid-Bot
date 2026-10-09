"""Build harness bar tables from hist_etl Binance Parquet.

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

    python -m research.bar_tables panel --root "$HIST_ARCHIVES_ROOT" \
        --group bn-um-usdt-1d --start 2020-01-01 --end 2026-10-01 \
        --lookbacks 7,30,90 --vol-window 30 --volume-window 30 \
        --funding-window 7 --out panel.parquet

writes the daily cross-sectional panel of a hist_etl universe: one row per
symbol and day with a bar closing in ``[start, end)``, warm-up rows kept
with empty features (see ``research.bar_tables.panel``).
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

from research.bar_tables.panel import PanelSpec, RankRule
from research.bar_tables.panel_io import (
    Exclusions,
    build_panel,
    load_exclusions,
    load_panel_manifest,
    universe_files,
    write_panel_parquet,
)
from research.bar_tables.trend import (
    BarTableError,
    TrendRow,
    build_trend_rows,
    check_funding_means,
)
from research.hist_etl.errors import HistEtlError
from research.hist_etl.manifest import default_manifest_path
from research.hist_etl.models import INTERVAL_SECONDS, parquet_slug

_HOUR_MS = 3_600_000
_PANEL_FIRST_DAY = date(2000, 1, 1)
_PANEL_LAST_DAY = date(2100, 1, 1)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "panel":
        return _panel(args)
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
        max_gap_ms = args.max_funding_gap_hours * _HOUR_MS
        intervals_ms: list[int] = []
        if funding_means:
            # A tilt's mean may reach back before --start, so read that far back.
            reach_ms = max(funding_means) * max_gap_ms
            funding, intervals_ms = _read_funding_with_intervals(
                root, args.market, args.symbol, start_ms - reach_ms, end_ms
            )
        else:
            funding = _read_funding(root, args.market, args.symbol, start_ms, end_ms)
        rows = build_trend_rows(
            closes,
            funding,
            lookbacks=lookbacks,
            vol_window=args.vol_window,
            bar_ms=INTERVAL_SECONDS[args.interval] * 1000,
            max_funding_gap_ms=max_gap_ms,
            funding_means=funding_means,
            funding_baseline=funding_baseline,
            funding_intervals_ms=intervals_ms,
        )
        write_trend_parquet(rows, lookbacks, Path(args.out), funding_means)
    except (BarTableError, duckdb.Error, OSError) as exc:
        print(f"bar_tables: {exc}", file=sys.stderr)
        return 2
    print(f"bar_tables\twrote\t{len(rows)}\trows\t{args.out}")
    return 0


def _panel(args: argparse.Namespace) -> int:
    try:
        root = _root(args.root)
        spec = PanelSpec(
            lookbacks=_counts(args.lookbacks, "--lookbacks", "day counts"),
            vol_window=args.vol_window,
            volume_window=args.volume_window,
            funding_window=args.funding_window,
        )
        start = _date(args.start, "--start")
        end = _date(args.end, "--end")
        if end <= start:
            raise BarTableError("--end must be after --start.")
        # Binance archives start in 2017; the bounds keep warm-up and month
        # arithmetic far from the ends of the calendar.
        if start < _PANEL_FIRST_DAY or end > _PANEL_LAST_DAY:
            raise BarTableError(
                f"Panel dates must lie from {_PANEL_FIRST_DAY} to {_PANEL_LAST_DAY}."
            )
        manifest_path = Path(args.manifest) if args.manifest else default_manifest_path()
        manifest = load_panel_manifest(manifest_path, args.group)
        exclusions = (
            load_exclusions(Path(args.exclude_symbols), manifest, manifest_path, args.group)
            if args.exclude_symbols is not None
            else None
        )
        rule = RankRule(
            excluded=exclusions.symbols if exclusions else frozenset(),
            require_funding=args.rank_requires_funding,
        )
        files = universe_files(root, manifest, args.group, start, end, spec)
        rows = build_panel(files, spec, start, end, rule)
        write_panel_parquet(rows, spec, Path(args.out))
    except (BarTableError, HistEtlError, duckdb.Error, OSError) as exc:
        print(f"bar_tables: {exc}", file=sys.stderr)
        return 2
    symbols = len({row.symbol for row in rows})
    ranked = len({row.symbol for row in rows if row.volume_rank is not None})
    print(
        f"bar_tables\twrote\t{len(rows)}\trows\t{symbols}\tsymbols\t"
        f"{ranked}\tever ranked\t{args.out}{_rule_note(exclusions, rule)}"
    )
    return 0


def _rule_note(exclusions: Exclusions | None, rule: RankRule) -> str:
    """The rank rule a build used, after the output path; empty for the default rule."""

    note = ""
    if exclusions is not None:
        note += f"\t{len(exclusions.symbols)}\texcluded\tsha256:{exclusions.sha256}"
    if rule.require_funding:
        note += "\trank requires funding"
    return note


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
    if any(len(row.funding_tilts) != len(funding_means) for row in rows):
        raise BarTableError("Each row needs one funding tilt per funding mean count.")
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


def _read_funding_with_intervals(
    root: Path, market: str, symbol: str, start_ms: int, end_ms: int
) -> tuple[list[tuple[int, float]], list[int]]:
    """Settlements with each one's interval in ms; a missing interval is 8h, as in hist_etl."""

    files = _files(root, market, parquet_slug("fundingRate", None), symbol)
    rows = _query(
        "SELECT epoch_ms(calc_time), CAST(last_funding_rate AS DOUBLE), "
        "coalesce(funding_interval_hours, 8) FROM read_parquet(?) "
        "WHERE calc_time >= make_timestamp(?) AND calc_time < make_timestamp(?) "
        "ORDER BY calc_time",
        [files, start_ms * 1000, end_ms * 1000],
    )
    funding = [(_int(ts), _float(rate)) for ts, rate, _hours in rows]
    return funding, [_interval_ms(hours) for _ts, _rate, hours in rows]


def _interval_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BarTableError(f"Expected a positive whole funding_interval_hours, got {value!r}.")
    return value * _HOUR_MS


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
    return _counts(value, "--lookbacks", "bar counts")


def _counts(value: str, flag: str, what: str) -> tuple[int, ...]:
    try:
        return tuple(int(token) for token in value.split(","))
    except ValueError as exc:
        raise BarTableError(f"{flag} must be comma-separated {what}: {value}") from exc


def _funding_tilt_options(
    means: str | None, baseline: float | None
) -> tuple[tuple[int, ...], float]:
    if means is None and baseline is None:
        return (), 0.0
    if means is None or baseline is None:
        raise BarTableError("--funding-means and --funding-baseline go together.")
    counts = _counts(means, "--funding-means", "settlement counts")
    # Checked before the counts size any read.
    check_funding_means(counts, baseline)
    return counts, baseline


def _date(value: str, flag: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise BarTableError(f"{flag} must be a YYYY-MM-DD date: {value}") from exc


def _date_ms(value: str, flag: str) -> int:
    day = _date(value, flag)
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
    panel = sub.add_parser(
        "panel",
        help="Daily cross-sectional panel of a hist_etl binance_universe entry.",
    )
    panel.add_argument("--root", help="Archive root. Defaults to HIST_ARCHIVES_ROOT.")
    panel.add_argument(
        "--manifest", help="hist_etl manifest. Defaults to config/hist_etl/datasets.toml."
    )
    panel.add_argument("--group", required=True, help="binance_universe id, e.g. bn-um-usdt-1d.")
    panel.add_argument("--start", required=True, help="First daily bar's open date, UTC.")
    panel.add_argument("--end", required=True, help="Exclusive end date, UTC.")
    panel.add_argument("--lookbacks", required=True, help="Comma-separated return lookbacks, days.")
    panel.add_argument("--vol-window", type=int, required=True, help="Realized vol window, days.")
    panel.add_argument(
        "--volume-window", type=int, required=True, help="Trailing quote volume window, days."
    )
    panel.add_argument(
        "--funding-window", type=int, required=True, help="Trailing funding mean window, days."
    )
    panel.add_argument(
        "--exclude-symbols",
        help="JSON list of symbols that never rank, checked against the group's universe file.",
    )
    panel.add_argument(
        "--rank-requires-funding",
        action="store_true",
        help="Rank a row only on a day with a funding rate.",
    )
    panel.add_argument("--out", required=True, help="Output Parquet path.")
    return parser
