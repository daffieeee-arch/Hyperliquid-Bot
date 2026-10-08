"""Unpack Binance Vision zips into one Parquet file per month.

CSV quirks, from the public-data README and issue #267:
headerless numeric rows are the documented sample; some archives add a header.
Spot timestamps switch from milliseconds to microseconds on 2025-01-01.
USD-M examples stay milliseconds, but a microsecond stamp is still accepted
when every row in the file agrees.

``ts`` is the decision time: kline ``close_time``, and the native event time
for aggTrades (``transact_time``), funding (``calc_time``), and metrics
(``create_time``). Those native columns stay in the file.
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from research.hist_etl.errors import HistEtlError
from research.hist_etl.files import atomic_write_json, decide_output, warn
from research.hist_etl.models import (
    INTERVAL_SECONDS,
    MICROSECOND_THRESHOLD,
    MILLISECOND_THRESHOLD,
    BinanceSpec,
    Gap,
    SourceDigest,
    parquet_slug,
)
from research.hist_etl.planning import coverage_window, spot_timestamp_unit

_IDENT = re.compile(r"[a-z_]+")
_KLINE_FIELDS = (
    "open_raw",
    "open_px",
    "high_px",
    "low_px",
    "close_px",
    "volume",
    "close_raw",
    "quote_volume",
    "trade_count",
    "taker_base",
    "taker_quote",
)
_AGG_FIELDS = (
    "agg_id",
    "price",
    "quantity",
    "first_id",
    "last_id",
    "time_raw",
    "is_buyer_maker",
    "is_best_match",
)
_FUNDING_FIELDS = ("calc_raw", "interval_hours", "rate")
_METRICS_FIELDS = (
    "create_raw",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


def binance_parquet_path(root: Path, spec: BinanceSpec, month: str) -> Path:
    slug = parquet_slug(spec.dataset, spec.interval)
    return (
        root
        / "parquet"
        / "hist_etl"
        / "binance"
        / spec.market
        / slug
        / f"{spec.symbol}-{month}.parquet"
    )


def materialize_binance_month(
    *,
    spec: BinanceSpec,
    month: date,
    sources: Sequence[SourceDigest],
    root: Path,
    today: date,
    staging: Path,
    rebuild: bool = False,
) -> tuple[Path | None, tuple[Gap, ...]]:
    """Write one month of Parquet, or return gaps and leave prior output alone."""

    destination = binance_parquet_path(root, spec, f"{month.year:04d}-{month.month:02d}")
    if not sources:
        return None, (
            Gap(
                "missing_archive",
                spec.id,
                f"no local archive for {spec.symbol} {spec.dataset} {month:%Y-%m}",
            ),
        )
    action = decide_output(destination, sources, rebuild=rebuild)
    if action == "audit":
        gaps = _audit_existing(destination, spec, month, today)
        return destination, gaps
    if action != "write":
        return destination, (_refuse_overwrite(destination, spec.id, action),)
    staging.mkdir(parents=True, exist_ok=True)
    csv_paths: list[Path] = []
    try:
        for index, source in enumerate(sources):
            target = staging / f"{index:04d}.csv"
            extract_canonical(source.path, target, spec)
            csv_paths.append(target)
        gaps = _write_parquet(csv_paths, destination, spec, month, today, sources)
    except HistEtlError as exc:
        return None, (Gap("schema", spec.id, str(exc)),)
    finally:
        for path in csv_paths:
            if path.is_file():
                path.unlink()
    if any(gap.kind == "schema" for gap in gaps):
        return None, gaps
    return destination, gaps


def audit_binance_month(
    *,
    spec: BinanceSpec,
    month: date,
    root: Path,
    today: date,
) -> tuple[Gap, ...]:
    """Grid-check an existing month file. Does not rebuild or delete it."""

    destination = binance_parquet_path(root, spec, f"{month.year:04d}-{month.month:02d}")
    if not destination.is_file():
        return (Gap("missing_parquet", spec.id, destination.name),)
    return _audit_existing(destination, spec, month, today)


def extract_canonical(zip_path: Path, dest: Path, spec: BinanceSpec) -> int:
    """Stream the zip's CSV into a headerless canonical file. Returns the row count."""

    with zipfile.ZipFile(zip_path) as archive:
        member = _csv_member(archive, zip_path)
        with archive.open(member, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.reader(text)
            first = _first_row(reader)
            if first is None:
                raise HistEtlError(f"{zip_path.name} has no CSV rows", exit_code=2)
            header, pending = _classify_first(first, spec, zip_path.name)
            written = 0
            with dest.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                if pending is not None:
                    writer.writerow(_project(pending, header, spec, zip_path.name))
                    written += 1
                for row in reader:
                    if not row or all(not cell.strip() for cell in row):
                        continue
                    writer.writerow(_project(row, header, spec, zip_path.name))
                    written += 1
    if written == 0:
        raise HistEtlError(f"{zip_path.name} has no data rows", exit_code=2)
    return written


def _write_parquet(
    csv_paths: Sequence[Path],
    destination: Path,
    spec: BinanceSpec,
    month: date,
    today: date,
    sources: Sequence[SourceDigest],
) -> tuple[Gap, ...]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone='UTC'")
        _load_raw(connection, csv_paths, _raw_columns(spec))
        problems, unit = _validate(connection, spec, month)
        if problems:
            return tuple(Gap("schema", spec.id, problem) for problem in problems)
        _build_final(connection, spec, month, today, unit)
        connection.execute("CREATE VIEW published AS SELECT * EXCLUDE (rn) FROM final WHERE rn = 1")
        row = connection.execute("SELECT count(*) FROM published").fetchone()
        count = 0 if row is None else int(row[0])
        if count == 0:
            return (
                Gap("schema", spec.id, f"no rows inside the requested range for {month:%Y-%m}"),
            )
        copied = False
        try:
            connection.execute(
                "COPY (SELECT * FROM published ORDER BY 1) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(partial)],
            )
            os.replace(partial, destination)
            copied = True
        finally:
            if partial.exists() and not copied:
                partial.unlink()
        _write_sidecar(destination, sources, count, unit)
        holes = _grid_gaps(connection, spec, month, today)
        return holes
    finally:
        connection.close()


def _audit_existing(path: Path, spec: BinanceSpec, month: date, today: date) -> tuple[Gap, ...]:
    if spec.dataset == "aggTrades":
        return ()
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone='UTC'")
        connection.execute(
            "CREATE TABLE published AS SELECT * FROM read_parquet(?)",
            [str(path)],
        )
        return _grid_gaps(connection, spec, month, today)
    finally:
        connection.close()


def _load_raw(
    connection: duckdb.DuckDBPyConnection, paths: Sequence[Path], columns: tuple[str, ...]
) -> None:
    column_sql = ", ".join(f"'{name}': 'VARCHAR'" for name in columns)
    connection.execute(
        f"""
        CREATE TABLE raw AS
        SELECT * FROM read_csv(
            ?,
            header = false,
            auto_detect = false,
            delim = ',',
            quote = '"',
            escape = '"',
            columns = {{{column_sql}}},
            strict_mode = true,
            null_padding = false
        )
        """,
        [[str(path) for path in paths]],
    )


def _raw_columns(spec: BinanceSpec) -> tuple[str, ...]:
    if spec.dataset in {"klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines"}:
        return ("source", *_KLINE_FIELDS)
    if spec.dataset == "aggTrades":
        return ("source", *_AGG_FIELDS)
    if spec.dataset == "fundingRate":
        return ("source", *_FUNDING_FIELDS)
    if spec.dataset == "metrics":
        return ("source", *_METRICS_FIELDS)
    raise HistEtlError(f"unsupported dataset {spec.dataset}", exit_code=2)


def _validate(
    connection: duckdb.DuckDBPyConnection, spec: BinanceSpec, month: date
) -> tuple[list[str], str]:
    if spec.dataset == "metrics":
        return _validate_metrics(connection), "clock"
    if spec.dataset == "fundingRate":
        return _validate_funding(connection, spec, month)
    if spec.dataset == "aggTrades":
        return _validate_agg(connection, spec, month)
    return _validate_klines(connection, spec, month)


def _validate_klines(
    connection: duckdb.DuckDBPyConnection, spec: BinanceSpec, month: date
) -> tuple[list[str], str]:
    connection.execute(
        """
        CREATE TABLE typed AS
        SELECT
            source,
            try_cast(open_raw AS BIGINT) AS open_i,
            try_cast(close_raw AS BIGINT) AS close_i,
            try_cast(open_px AS DOUBLE) AS open_px,
            try_cast(high_px AS DOUBLE) AS high_px,
            try_cast(low_px AS DOUBLE) AS low_px,
            try_cast(close_px AS DOUBLE) AS close_px,
            try_cast(volume AS DOUBLE) AS volume_n,
            try_cast(quote_volume AS DOUBLE) AS quote_n,
            try_cast(trade_count AS BIGINT) AS trades,
            try_cast(taker_base AS DOUBLE) AS taker_base_n,
            try_cast(taker_quote AS DOUBLE) AS taker_quote_n
        FROM raw
        """
    )
    row = connection.execute(
        """
        SELECT
            count(*) AS n,
            count(*) FILTER (WHERE open_i IS NULL OR close_i IS NULL OR open_px IS NULL
                OR high_px IS NULL OR low_px IS NULL OR close_px IS NULL
                OR volume_n IS NULL OR quote_n IS NULL OR trades IS NULL
                OR taker_base_n IS NULL OR taker_quote_n IS NULL) AS bad_cast,
            count(*) FILTER (WHERE isnan(open_px) OR isnan(high_px) OR isnan(low_px)
                OR isnan(close_px)) AS nans,
            count(*) FILTER (WHERE high_px < low_px OR high_px < open_px OR high_px < close_px
                OR low_px > open_px OR low_px > close_px) AS ohlc,
            count(*) FILTER (WHERE volume_n < 0 OR quote_n < 0 OR trades < 0
                OR taker_base_n < 0 OR taker_quote_n < 0) AS negative_size,
            count(*) FILTER (WHERE close_i < open_i) AS time_order
        FROM typed
        """
    ).fetchone()
    problems = _count_problems(
        row,
        (
            "unparseable kline field",
            "non-finite price",
            "ohlc inconsistency",
            "negative size",
            "close before open",
        ),
    )
    if spec.dataset != "premiumIndexKlines":
        positive = connection.execute(
            """
            SELECT count(*) FROM typed
            WHERE open_px <= 0 OR high_px <= 0 OR low_px <= 0 OR close_px <= 0
            """
        ).fetchone()
        if positive is not None and int(positive[0]) > 0:
            problems.append(f"{int(positive[0])} non-positive prices")
    unit, unit_problems = _timestamp_unit(connection, spec, month, "open_i", "close_i")
    problems.extend(unit_problems)
    conflict = connection.execute(
        """
        SELECT count(*) FROM (
            SELECT open_i FROM typed
            GROUP BY open_i
            HAVING count(*) > 1 AND (
                count(DISTINCT open_px) > 1 OR count(DISTINCT high_px) > 1
                OR count(DISTINCT low_px) > 1 OR count(DISTINCT close_px) > 1
            )
        )
        """
    ).fetchone()
    if conflict is not None and int(conflict[0]) > 0:
        problems.append(f"{int(conflict[0])} conflicting duplicate open times")
    return problems, unit


def _validate_agg(
    connection: duckdb.DuckDBPyConnection, spec: BinanceSpec, month: date
) -> tuple[list[str], str]:
    connection.execute(
        """
        CREATE TABLE typed AS
        SELECT
            source,
            try_cast(agg_id AS BIGINT) AS agg_id_n,
            try_cast(price AS DOUBLE) AS price_n,
            try_cast(quantity AS DOUBLE) AS quantity_n,
            try_cast(first_id AS BIGINT) AS first_n,
            try_cast(last_id AS BIGINT) AS last_n,
            try_cast(time_raw AS BIGINT) AS time_i,
            lower(is_buyer_maker) AS buyer,
            lower(is_best_match) AS best
        FROM raw
        """
    )
    best_check = (
        "OR best NOT IN ('true', 'false')"
        if spec.market == "spot"
        else "OR best NOT IN ('', 'true', 'false')"
    )
    row = connection.execute(
        f"""
        SELECT
            count(*) AS n,
            count(*) FILTER (WHERE agg_id_n IS NULL OR price_n IS NULL OR quantity_n IS NULL
                OR first_n IS NULL OR last_n IS NULL OR time_i IS NULL) AS bad_cast,
            count(*) FILTER (WHERE price_n <= 0 OR quantity_n <= 0 OR first_n > last_n) AS trade,
            count(*) FILTER (WHERE buyer NOT IN ('true', 'false') {best_check}) AS flags
        FROM typed
        """
    ).fetchone()
    problems = _count_problems(
        row, ("unparseable aggTrade field", "invalid trade", "invalid boolean")
    )
    unit, unit_problems = _timestamp_unit(connection, spec, month, "time_i", "time_i")
    problems.extend(unit_problems)
    conflict = connection.execute(
        """
        SELECT count(*) FROM (
            SELECT agg_id_n FROM typed
            GROUP BY agg_id_n
            HAVING count(*) > 1 AND (
                count(DISTINCT price_n) > 1 OR count(DISTINCT quantity_n) > 1
            )
        )
        """
    ).fetchone()
    if conflict is not None and int(conflict[0]) > 0:
        problems.append(f"{int(conflict[0])} conflicting duplicate aggregate ids")
    return problems, unit


def _validate_funding(
    connection: duckdb.DuckDBPyConnection, spec: BinanceSpec, month: date
) -> tuple[list[str], str]:
    connection.execute(
        """
        CREATE TABLE typed AS
        SELECT
            source,
            try_cast(calc_raw AS BIGINT) AS time_i,
            try_cast(NULLIF(interval_hours, '') AS INTEGER) AS interval_n,
            try_cast(rate AS DOUBLE) AS rate_n
        FROM raw
        """
    )
    row = connection.execute(
        """
        SELECT
            count(*) AS n,
            count(*) FILTER (WHERE time_i IS NULL OR rate_n IS NULL) AS bad_cast,
            count(*) FILTER (WHERE isnan(rate_n)) AS nans,
            count(*) FILTER (WHERE interval_n IS NOT NULL AND interval_n <= 0) AS bad_interval
        FROM typed
        """
    ).fetchone()
    problems = _count_problems(
        row,
        ("unparseable funding field", "non-finite funding rate", "invalid funding interval"),
    )
    unit, unit_problems = _timestamp_unit(connection, spec, month, "time_i", "time_i")
    problems.extend(unit_problems)
    return problems, unit


def _validate_metrics(connection: duckdb.DuckDBPyConnection) -> list[str]:
    connection.execute(
        """
        CREATE TABLE typed AS
        SELECT
            source,
            try_cast(strptime(create_raw, '%Y-%m-%d %H:%M:%S') AS TIMESTAMP) AS create_time,
            try_cast(sum_open_interest AS DOUBLE) AS oi,
            try_cast(sum_open_interest_value AS DOUBLE) AS oi_value,
            try_cast(count_toptrader_long_short_ratio AS DOUBLE) AS count_top,
            try_cast(sum_toptrader_long_short_ratio AS DOUBLE) AS sum_top,
            try_cast(count_long_short_ratio AS DOUBLE) AS count_ls,
            try_cast(sum_taker_long_short_vol_ratio AS DOUBLE) AS taker
        FROM raw
        """
    )
    row = connection.execute(
        """
        SELECT
            count(*) AS n,
            count(*) FILTER (WHERE create_time IS NULL OR oi IS NULL OR oi_value IS NULL
                OR count_top IS NULL OR sum_top IS NULL OR count_ls IS NULL
                OR taker IS NULL) AS bad_cast,
            count(*) FILTER (WHERE oi < 0 OR oi_value < 0) AS negative_oi
        FROM typed
        """
    ).fetchone()
    return _count_problems(row, ("unparseable metrics field", "negative open interest"))


def _timestamp_unit(
    connection: duckdb.DuckDBPyConnection,
    spec: BinanceSpec,
    month: date,
    left: str,
    right: str,
) -> tuple[str, list[str]]:
    if not _IDENT.fullmatch(left) or not _IDENT.fullmatch(right):
        raise HistEtlError("unsafe timestamp column", exit_code=2)
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (
                WHERE {left} >= {MICROSECOND_THRESHOLD} AND {right} >= {MICROSECOND_THRESHOLD}
            ) AS us_n,
            count(*) FILTER (
                WHERE {left} >= {MILLISECOND_THRESHOLD} AND {left} < {MICROSECOND_THRESHOLD}
                  AND {right} >= {MILLISECOND_THRESHOLD} AND {right} < {MICROSECOND_THRESHOLD}
            ) AS ms_n,
            count(*) AS n
        FROM typed
        """
    ).fetchone()
    us_n = 0 if row is None else int(row[0])
    ms_n = 0 if row is None else int(row[1])
    total = 0 if row is None else int(row[2])
    problems: list[str] = []
    if us_n + ms_n != total:
        problems.append("timestamp is not uniformly milliseconds or microseconds")
        return "bad", problems
    if us_n and ms_n:
        problems.append("mixed millisecond and microsecond timestamps")
        return "bad", problems
    unit = "us" if us_n else "ms"
    if spec.market == "spot":
        expected = spot_timestamp_unit(month)
        if unit != expected:
            problems.append(
                f"spot {month:%Y-%m} timestamps are {unit}; Binance Vision requires {expected} "
                "from 2025-01-01 and milliseconds before that"
            )
    return unit, problems


def _build_final(
    connection: duckdb.DuckDBPyConnection,
    spec: BinanceSpec,
    month: date,
    today: date,
    unit: str,
) -> None:
    start, end_exclusive = _range_bounds(spec, month, today)
    divisor = 1_000_000.0 if unit == "us" else 1_000.0
    if spec.dataset in {"klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines"}:
        connection.execute(
            f"""
            CREATE TABLE final AS
            SELECT *, row_number() OVER (PARTITION BY open_time ORDER BY source_name) AS rn
            FROM (
                SELECT
                    CAST(to_timestamp(open_i / {divisor}) AS TIMESTAMP) AS open_time,
                    open_px AS open,
                    high_px AS high,
                    low_px AS low,
                    close_px AS close,
                    volume_n AS volume,
                    CAST(to_timestamp(close_i / {divisor}) AS TIMESTAMP) AS close_time,
                    CAST(to_timestamp(close_i / {divisor}) AS TIMESTAMP) AS ts,
                    quote_n AS quote_volume,
                    trades AS trade_count,
                    taker_base_n AS taker_buy_base_volume,
                    taker_quote_n AS taker_buy_quote_volume,
                    ? AS symbol,
                    ? AS market,
                    ? AS interval,
                    source AS source_name,
                    ? AS timestamp_unit
                FROM typed
            )
            WHERE open_time >= ? AND open_time < ?
            """,
            [spec.symbol, spec.market, spec.interval or "", unit, start, end_exclusive],
        )
        return
    if spec.dataset == "aggTrades":
        connection.execute(
            f"""
            CREATE TABLE final AS
            SELECT *, row_number() OVER (PARTITION BY agg_trade_id ORDER BY source_name) AS rn
            FROM (
                SELECT
                    agg_id_n AS agg_trade_id,
                    price_n AS price,
                    quantity_n AS quantity,
                    first_n AS first_trade_id,
                    last_n AS last_trade_id,
                    CAST(to_timestamp(time_i / {divisor}) AS TIMESTAMP) AS transact_time,
                    CAST(to_timestamp(time_i / {divisor}) AS TIMESTAMP) AS ts,
                    buyer = 'true' AS is_buyer_maker,
                    CASE WHEN best = '' THEN NULL ELSE best = 'true' END AS is_best_match,
                    ? AS symbol,
                    ? AS market,
                    source AS source_name,
                    ? AS timestamp_unit
                FROM typed
            )
            WHERE transact_time >= ? AND transact_time < ?
            """,
            [spec.symbol, spec.market, unit, start, end_exclusive],
        )
        return
    if spec.dataset == "fundingRate":
        connection.execute(
            f"""
            CREATE TABLE final AS
            SELECT *, row_number() OVER (PARTITION BY calc_time ORDER BY source_name) AS rn
            FROM (
                SELECT
                    CAST(to_timestamp(time_i / {divisor}) AS TIMESTAMP) AS calc_time,
                    CAST(to_timestamp(time_i / {divisor}) AS TIMESTAMP) AS ts,
                    interval_n AS funding_interval_hours,
                    rate_n AS last_funding_rate,
                    ? AS symbol,
                    ? AS market,
                    source AS source_name,
                    ? AS timestamp_unit
                FROM typed
            )
            WHERE calc_time >= ? AND calc_time < ?
            """,
            [spec.symbol, spec.market, unit, start, end_exclusive],
        )
        return
    connection.execute(
        """
        CREATE TABLE final AS
        SELECT *, row_number() OVER (PARTITION BY create_time ORDER BY source_name) AS rn
        FROM (
            SELECT
                create_time,
                create_time AS ts,
                oi AS sum_open_interest,
                oi_value AS sum_open_interest_value,
                count_top AS count_toptrader_long_short_ratio,
                sum_top AS sum_toptrader_long_short_ratio,
                count_ls AS count_long_short_ratio,
                taker AS sum_taker_long_short_vol_ratio,
                ? AS symbol,
                ? AS market,
                source AS source_name
            FROM typed
        )
        WHERE create_time >= ? AND create_time < ?
        """,
        [spec.symbol, spec.market, start, end_exclusive],
    )


def _grid_gaps(
    connection: duckdb.DuckDBPyConnection,
    spec: BinanceSpec,
    month: date,
    today: date,
) -> tuple[Gap, ...]:
    if spec.dataset == "aggTrades":
        return ()
    if spec.dataset == "fundingRate":
        return _funding_holes(connection, spec.id)
    window = coverage_window(spec, month, today)
    if window is None:
        return ()
    expected_first, expected_last = window
    column = "create_time" if spec.dataset == "metrics" else "open_time"
    step = 300 if spec.dataset == "metrics" else _kline_step(spec)
    late_start = spec.open_start and month == date(spec.start.year, spec.start.month, 1)
    early_end = (
        spec.open_end and spec.end is not None and month == date(spec.end.year, spec.end.month, 1)
    )
    return _regular_holes(
        connection,
        column,
        step,
        expected_first,
        expected_last,
        spec,
        late_start=late_start,
        early_end=early_end,
    )


def _kline_step(spec: BinanceSpec) -> int:
    if spec.interval is None:
        raise HistEtlError(f"{spec.id} is missing an interval", exit_code=2)
    return INTERVAL_SECONDS[spec.interval]


def _regular_holes(
    connection: duckdb.DuckDBPyConnection,
    column: str,
    step_seconds: int,
    expected_first: datetime,
    expected_last: datetime,
    spec: BinanceSpec,
    *,
    late_start: bool = False,
    early_end: bool = False,
) -> tuple[Gap, ...]:
    """Holes between bars, and edges that miss the expected range.

    ``late_start`` and ``early_end`` accept a first bar after, or a last bar
    before, the expected edge: the month a contract was listed or delisted.
    """

    if not _IDENT.fullmatch(column):
        raise HistEtlError("unsafe column", exit_code=2)
    step_us = step_seconds * 1_000_000
    holes = connection.execute(
        f"""
        SELECT strftime(ts, '%Y-%m-%dT%H:%M:%SZ')
        FROM (
            SELECT {column} AS ts, lead({column}) OVER (ORDER BY {column}) AS nxt
            FROM published
        )
        WHERE nxt IS NOT NULL AND date_diff('microsecond', ts, nxt) <> ?
        LIMIT 20
        """,
        [step_us],
    ).fetchall()
    samples = [str(item[0]) for item in holes]
    edge = connection.execute(
        f"SELECT min({column}), max({column}), count(*) FROM published"
    ).fetchone()
    details: list[str] = []
    if edge is None or edge[2] == 0:
        details.append("parquet has no rows")
    else:
        first = _naive(edge[0])
        last = _naive(edge[1])
        first_ok = first == expected_first or (late_start and first > expected_first)
        last_ok = last == expected_last or (early_end and last < expected_last)
        if not first_ok or not last_ok:
            details.append(
                f"coverage {first}..{last} does not match expected "
                f"{expected_first}..{expected_last}"
            )
            samples.append(f"expected {expected_first.isoformat()}Z..{expected_last.isoformat()}Z")
    if not samples and not details:
        return ()
    kind = "metrics_hole" if spec.dataset == "metrics" else "kline_hole"
    detail = "; ".join(details) if details else f"missing {step_seconds}s samples"
    return (Gap(kind, spec.id, detail, tuple(samples)),)


def _funding_holes(connection: duckdb.DuckDBPyConnection, dataset_id: str) -> tuple[Gap, ...]:
    rows = connection.execute(
        """
        SELECT strftime(calc_time, '%Y-%m-%dT%H:%M:%SZ')
        FROM (
            SELECT
                calc_time,
                funding_interval_hours,
                lead(calc_time) OVER (ORDER BY calc_time) AS nxt,
                lead(funding_interval_hours) OVER (ORDER BY calc_time) AS nxt_hours
            FROM published
        )
        WHERE nxt IS NOT NULL
          AND date_diff('microsecond', calc_time, nxt) >
              CAST(greatest(coalesce(funding_interval_hours, 8), coalesce(nxt_hours, 8)) AS BIGINT)
              * 3600000000 + 60000000
        LIMIT 20
        """
    ).fetchall()
    if not rows:
        return ()
    samples = tuple(str(item[0]) for item in rows)
    return (
        Gap(
            "funding_hole",
            dataset_id,
            "funding settlements are farther apart than the stated interval",
            samples,
        ),
    )


def _range_bounds(spec: BinanceSpec, month: date, today: date) -> tuple[datetime, datetime]:
    end = today if spec.end is None else spec.end
    start = datetime.combine(max(month, spec.start), datetime.min.time())
    if month.month == 12:
        next_month = date(month.year + 1, 1, 1)
    else:
        next_month = date(month.year, month.month + 1, 1)
    month_end = datetime.combine(next_month, datetime.min.time())
    range_end = datetime.combine(end + timedelta(days=1), datetime.min.time())
    return start, min(month_end, range_end)


def _as_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise HistEtlError(f"unexpected count {value!r}", exit_code=2)
    return int(value)


def _count_problems(row: tuple[object, ...] | None, labels: tuple[str, ...]) -> list[str]:
    if row is None:
        return ["validation query returned no row"]
    total = _as_count(row[0])
    problems: list[str] = []
    if total == 0:
        problems.append("no data rows")
    for label, value in zip(labels, row[1:], strict=True):
        count = _as_count(value)
        if count:
            problems.append(f"{count} rows with {label}")
    return problems


def _refuse_overwrite(destination: Path, dataset_id: str, action: str) -> Gap:
    reason = (
        "no .sources.json sidecar" if action == "untracked" else "sources differ from .sources.json"
    )
    detail = f"refusing to overwrite {destination.name}: {reason}; pass --rebuild to replace it"
    warn(detail)
    return Gap("refused_overwrite", dataset_id, detail)


def _write_sidecar(
    destination: Path, sources: Sequence[SourceDigest], rows: int, unit: str
) -> None:
    sidecar = destination.with_name(destination.name + ".sources.json")
    payload = {
        "sources": [{"name": source.name, "sha256": source.sha256} for source in sources],
        "rows": rows,
        "timestamp_unit": unit,
    }
    atomic_write_json(sidecar, payload)


def _naive(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise HistEtlError(f"expected a timestamp, got {value!r}", exit_code=2)
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _csv_member(archive: zipfile.ZipFile, zip_path: Path) -> str:
    members = [
        info.filename
        for info in archive.infolist()
        if not info.is_dir()
        and info.filename.lower().endswith(".csv")
        and not info.filename.startswith("__MACOSX")
    ]
    if not members:
        raise HistEtlError(f"{zip_path.name} contains no CSV", exit_code=2)
    stem = zip_path.stem
    named = [name for name in members if Path(name).name == f"{stem}.csv"]
    if len(named) == 1:
        return named[0]
    if len(members) == 1:
        return members[0]
    raise HistEtlError(f"{zip_path.name} contains multiple CSV members", exit_code=2)


def _first_row(reader: Iterator[list[str]]) -> list[str] | None:
    for row in reader:
        if row and any(cell.strip() for cell in row):
            return row
    return None


def _classify_first(
    row: list[str], spec: BinanceSpec, filename: str
) -> tuple[Mapping[str, int] | None, list[str] | None]:
    token = row[0].strip()
    if spec.dataset == "metrics":
        if token.lower().replace(" ", "_") == "create_time":
            return _header_index(row, _METRICS_ALIASES, filename), None
        return None, row
    if _is_int(token):
        return None, row
    aliases = _aliases_for(spec)
    return _header_index(row, aliases, filename), None


def _project(
    row: list[str],
    header: Mapping[str, int] | None,
    spec: BinanceSpec,
    filename: str,
) -> list[str]:
    if spec.dataset in {"klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines"}:
        values = _take(row, header, _KLINE_FIELDS, filename, positional_width=12)
        return [filename, *values]
    if spec.dataset == "aggTrades":
        width = 8 if spec.market == "spot" else 7
        values = _take(row, header, _AGG_FIELDS, filename, positional_width=width)
        if spec.market != "spot" and header is None:
            values = [*values, ""]
        return [filename, *values]
    if spec.dataset == "fundingRate":
        values = _take_funding(row, header, filename)
        return [filename, *values]
    if spec.dataset == "metrics":
        return [filename, *_project_metrics(row, header, spec, filename)]
    raise HistEtlError(f"unsupported dataset {spec.dataset}", exit_code=2)


def _take(
    row: list[str],
    header: Mapping[str, int] | None,
    fields: tuple[str, ...],
    filename: str,
    *,
    positional_width: int,
) -> list[str]:
    if header is None:
        if len(row) != positional_width:
            raise HistEtlError(
                f"{filename} has {len(row)} columns; expected {positional_width}",
                exit_code=2,
            )
        width = len(fields)
        return [cell.strip() for cell in row[:width]]
    values: list[str] = []
    for field in fields:
        index = header.get(field)
        if index is None:
            if field == "is_best_match":
                values.append("")
                continue
            raise HistEtlError(f"{filename} is missing {field}", exit_code=2)
        if index >= len(row):
            raise HistEtlError(f"{filename} is missing a value for {field}", exit_code=2)
        values.append(row[index].strip())
    return values


def _project_metrics(
    row: list[str],
    header: Mapping[str, int] | None,
    spec: BinanceSpec,
    filename: str,
) -> list[str]:
    if header is None:
        if len(row) != 8:
            raise HistEtlError(f"{filename} metrics row has {len(row)} columns", exit_code=2)
        symbol = row[1].strip()
        values = [cell.strip() for cell in (row[0], row[2], row[3], row[4], row[5], row[6], row[7])]
    else:
        symbol_index = header.get("file_symbol")
        if symbol_index is None:
            raise HistEtlError(f"{filename} metrics header is missing symbol", exit_code=2)
        symbol = row[symbol_index].strip()
        values = _take(row, header, _METRICS_FIELDS, filename, positional_width=8)
    if symbol != spec.symbol:
        raise HistEtlError(
            f"{filename} metrics symbol {symbol} does not match {spec.symbol}",
            exit_code=2,
        )
    values[0] = _normalize_metrics_time(values[0], filename)
    return values


def _take_funding(row: list[str], header: Mapping[str, int] | None, filename: str) -> list[str]:
    if header is None:
        if len(row) == 3:
            return [row[0].strip(), row[1].strip(), row[2].strip()]
        if len(row) == 2:
            return [row[0].strip(), "", row[1].strip()]
        raise HistEtlError(f"{filename} funding row has {len(row)} columns", exit_code=2)
    calc = header.get("calc_raw")
    rate = header.get("rate")
    if calc is None or rate is None:
        raise HistEtlError(f"{filename} funding header is missing calc_time or rate", exit_code=2)
    interval = header.get("interval_hours")
    interval_value = "" if interval is None else row[interval].strip()
    return [row[calc].strip(), interval_value, row[rate].strip()]


def _header_index(row: list[str], aliases: Mapping[str, str], filename: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, cell in enumerate(row):
        key = aliases.get(_norm_header(cell))
        if key is None:
            raise HistEtlError(f"{filename} has unexpected column {cell}", exit_code=2)
        if key == "ignore":
            continue
        if key in found:
            raise HistEtlError(f"{filename} repeats column {cell}", exit_code=2)
        found[key] = index
    return found


def _aliases_for(spec: BinanceSpec) -> Mapping[str, str]:
    if spec.dataset in {"klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines"}:
        return _KLINE_ALIASES
    if spec.dataset == "aggTrades":
        return _AGG_ALIASES
    if spec.dataset == "fundingRate":
        return _FUNDING_ALIASES
    return _METRICS_ALIASES


def _normalize_metrics_time(token: str, filename: str) -> str:
    if _is_int(token):
        raw = int(token)
        if raw >= MICROSECOND_THRESHOLD:
            seconds = raw / 1_000_000
        elif raw >= MILLISECOND_THRESHOLD:
            seconds = raw / 1_000
        else:
            raise HistEtlError(f"{filename} metrics timestamp {token} is not ms or us", exit_code=2)
        return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    try:
        parsed = datetime.strptime(token, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise HistEtlError(
            f"{filename} metrics time {token} is not YYYY-MM-DD HH:MM:SS", exit_code=2
        ) from exc
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _norm_header(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _is_int(value: str) -> bool:
    try:
        int(value.strip())
    except ValueError:
        return False
    return True


_KLINE_ALIASES: dict[str, str] = {
    "open_time": "open_raw",
    "opentime": "open_raw",
    "open": "open_px",
    "high": "high_px",
    "low": "low_px",
    "close": "close_px",
    "volume": "volume",
    "close_time": "close_raw",
    "closetime": "close_raw",
    "quote_asset_volume": "quote_volume",
    "quote_volume": "quote_volume",
    "number_of_trades": "trade_count",
    "count": "trade_count",
    "trades": "trade_count",
    "trade_count": "trade_count",
    "taker_buy_base_asset_volume": "taker_base",
    "taker_buy_base_volume": "taker_base",
    "taker_buy_volume": "taker_base",
    "taker_buy_quote_asset_volume": "taker_quote",
    "taker_buy_quote_volume": "taker_quote",
    "ignore": "ignore",
}

_AGG_ALIASES: dict[str, str] = {
    "aggregate_tradeid": "agg_id",
    "aggregate_trade_id": "agg_id",
    "agg_trade_id": "agg_id",
    "agg_tradeid": "agg_id",
    "price": "price",
    "quantity": "quantity",
    "qty": "quantity",
    "first_tradeid": "first_id",
    "first_trade_id": "first_id",
    "last_tradeid": "last_id",
    "last_trade_id": "last_id",
    "timestamp": "time_raw",
    "transact_time": "time_raw",
    "was_the_buyer_the_maker": "is_buyer_maker",
    "is_buyer_maker": "is_buyer_maker",
    "was_the_trade_the_best_price_match": "is_best_match",
    "is_best_match": "is_best_match",
}

_FUNDING_ALIASES: dict[str, str] = {
    "calc_time": "calc_raw",
    "funding_interval_hours": "interval_hours",
    "last_funding_rate": "rate",
    "funding_rate": "rate",
}

_METRICS_ALIASES: dict[str, str] = {
    "create_time": "create_raw",
    "symbol": "file_symbol",
    "sum_open_interest": "sum_open_interest",
    "sum_open_interest_value": "sum_open_interest_value",
    "count_toptrader_long_short_ratio": "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio": "sum_toptrader_long_short_ratio",
    "count_long_short_ratio": "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio": "sum_taker_long_short_vol_ratio",
}
