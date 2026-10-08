"""Read-only DuckDB and parquet access with fail-closed series checks.

Paths come from the spec (relative parquet) or from RESEARCH_DUCKDB_PATH /
HIST_ARCHIVES_ROOT. This module does not embed a machine-specific warehouse path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

import duckdb

from research.harness.errors import IntegrityError
from research.harness.spec import DataSpec, FeatureSpec, HypothesisSpec, Json

_INT_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
    }
)
_FLOAT_TYPES = frozenset({"FLOAT", "DOUBLE", "REAL"})
_BOOL_TYPES = frozenset({"BOOLEAN"})
_STRING_TYPES = frozenset({"VARCHAR"})
_TYPES_FOR_DTYPE = {
    "int64": _INT_TYPES,
    "float64": _FLOAT_TYPES,
    "bool": _BOOL_TYPES,
    "string": _STRING_TYPES,
}
# Bounds the dense symbol-by-day series a panel is held in.
_PANEL_MAX_SYMBOLS = 5_000


@dataclass(frozen=True, slots=True)
class BarTable:
    """One observation per row, already ordered and audited."""

    timestamps: tuple[int, ...]
    prices: tuple[float, ...]
    features: dict[str, tuple[float, ...]]
    availability: dict[str, tuple[int, ...]]
    # The funding rate a long pays over each bar, when costs.funding_column is set.
    funding: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class PanelTable:
    """One symbol per column group, one timestamp per index, already audited.

    ``timestamps`` is the date axis: every distinct timestamp, in order,
    gap-checked like a bar series. Each per-symbol series is indexed by that
    axis and is ``None`` where the symbol has no row. A row's price is
    positive and its traded flag is set; its signal, rank and funding may be
    ``None``, which means not known at that close, never zero.
    """

    timestamps: tuple[int, ...]
    symbols: tuple[str, ...]
    prices: tuple[tuple[float | None, ...], ...]
    traded: tuple[tuple[bool | None, ...], ...]
    ranks: tuple[tuple[int | None, ...], ...]
    signals: tuple[tuple[float | None, ...], ...]
    funding: tuple[tuple[float | None, ...], ...] | None
    # With funding: whether the row's funding day is whole; None without funding.
    covered: tuple[tuple[bool | None, ...], ...] | None = None


def fingerprint_inputs(spec: HypothesisSpec, spec_dir: Path) -> dict[str, Json]:
    """Row count, timestamp bounds, and a content checksum for the declared input.

    Parquet checksums are the sha256 of the file bytes. A DuckDB view checksum
    is the sha256 of four row-hash aggregates over the declared columns, so an
    unrelated catalog table does not change it.
    """

    data = spec.data
    connection, relation_sql, parameters = _open_source(data, spec_dir)
    try:
        count, timestamp_min, timestamp_max, logical_hash = _fingerprint_query(
            connection, data, relation_sql, parameters
        )
    except IntegrityError:
        raise
    except duckdb.Error as error:
        raise IntegrityError("schema", f"DuckDB read failed: {_short(str(error))}") from error
    finally:
        connection.close()
    if data.backend in {"parquet", "panel"}:
        if data.parquet_path is None:
            raise IntegrityError("data_config", "parquet backend is missing parquet_path.")
        checksum = _sha256_file(spec_dir / data.parquet_path)
        algorithm = "sha256"
        locator = data.parquet_path
    else:
        if data.view is None:
            raise IntegrityError("data_config", "duckdb backend is missing a view name.")
        checksum = logical_hash
        algorithm = "duckdb-row-hash-sha256"
        locator = data.view
    inputs: list[Json] = [
        {
            "backend": data.backend,
            "locator": locator,
            "row_count": count,
            "timestamp_min": timestamp_min,
            "timestamp_max": timestamp_max,
            "checksum_algorithm": algorithm,
            "content_checksum": checksum,
        }
    ]
    encoded = _canonical_inputs(inputs)
    return {
        "fingerprint_sha256": hashlib.sha256(encoded).hexdigest(),
        "inputs": inputs,
    }


def load_bars(spec: HypothesisSpec, spec_dir: Path) -> BarTable:
    """Load declared columns and refuse gaps, duplicates, schema drift, and look-ahead."""

    if spec.data.backend == "panel":
        raise IntegrityError("data_config", "A panel spec is loaded with load_panel.")
    connection, relation_sql, parameters = _open_source(spec.data, spec_dir)
    try:
        _assert_relation(connection, spec.data, relation_sql, parameters)
        rows = _fetch_rows(connection, spec.data, relation_sql, parameters)
    except IntegrityError:
        raise
    except duckdb.Error as error:
        raise IntegrityError("schema", f"DuckDB read failed: {_short(str(error))}") from error
    finally:
        connection.close()
    return _table_from_rows(spec, rows)


def _open_source(
    data: DataSpec,
    spec_dir: Path,
) -> tuple[duckdb.DuckDBPyConnection, str, list[object]]:
    if data.backend in {"parquet", "panel"}:
        if data.parquet_path is None:
            raise IntegrityError("data_config", "parquet backend is missing parquet_path.")
        path = spec_dir / data.parquet_path
        if path.is_symlink() or not path.is_file():
            raise IntegrityError(
                "data_config", "parquet_path must be a regular file next to the spec."
            )
        connection = duckdb.connect(":memory:")
        return connection, "read_parquet(?)", [str(path)]
    if data.backend != "duckdb" or data.view is None:
        raise IntegrityError("data_config", "duckdb backend is missing a view name.")
    database = _duckdb_path()
    if database.is_symlink() or not database.is_file():
        raise IntegrityError("data_config", "DuckDB catalog must be a regular file.")
    connection = duckdb.connect(str(database), read_only=True)
    return connection, f'"{data.view}"', []


def _fingerprint_query(
    connection: duckdb.DuckDBPyConnection,
    data: DataSpec,
    relation_sql: str,
    parameters: list[object],
) -> tuple[int, int | None, int | None, str]:
    quoted = ", ".join(f'"{column.name}"' for column in data.columns)
    timestamp = f'"{data.timestamp_column}"'
    hash_exprs = ", ".join(f"printf('%016x', bit_xor(hash({quoted}, {seed})))" for seed in range(4))
    sql = (
        f"SELECT count(*)::BIGINT, min({timestamp}), max({timestamp}), {hash_exprs} "
        f"FROM {relation_sql}"
    )
    fetched = connection.execute(sql, parameters).fetchone()
    if fetched is None or len(fetched) != 7:
        raise IntegrityError("schema", "Fingerprint query returned no row.")
    count_raw, min_raw, max_raw = fetched[0], fetched[1], fetched[2]
    if isinstance(count_raw, bool) or not isinstance(count_raw, int):
        raise IntegrityError("schema", "Fingerprint row count was not an integer.")
    parts = [_optional_hash(raw, count_raw) for raw in fetched[3:]]
    return (
        count_raw,
        _optional_int(min_raw, "timestamp_min"),
        _optional_int(max_raw, "timestamp_max"),
        _combine_row_hashes(count_raw, parts),
    )


def _optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise IntegrityError("schema", f"Fingerprint {label} was not an integer.")
    return value


def _optional_hash(value: object, row_count: int) -> str | None:
    if value is None:
        if row_count == 0:
            return None
        raise IntegrityError("schema", "Fingerprint content hash was null.")
    if not isinstance(value, str) or len(value) != 16:
        raise IntegrityError("schema", "Fingerprint content hash was not 16 hex characters.")
    if any(char not in "0123456789abcdef" for char in value):
        raise IntegrityError("schema", "Fingerprint content hash was not hexadecimal.")
    return value


def _combine_row_hashes(row_count: int, parts: list[str | None]) -> str:
    if row_count == 0 or any(part is None for part in parts):
        return hashlib.sha256(b"").hexdigest()
    payload = "|".join(part for part in parts if part is not None)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _canonical_inputs(inputs: list[Json]) -> bytes:
    return json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def _duckdb_path() -> Path:
    configured = os.environ.get("RESEARCH_DUCKDB_PATH")
    if configured:
        return Path(configured)
    root = os.environ.get("HIST_ARCHIVES_ROOT")
    if root:
        return Path(root) / "research.duckdb"
    raise IntegrityError(
        "data_config",
        "Set RESEARCH_DUCKDB_PATH or HIST_ARCHIVES_ROOT. No warehouse path is built in.",
    )


def _assert_relation(
    connection: duckdb.DuckDBPyConnection,
    data: DataSpec,
    relation_sql: str,
    parameters: list[object],
) -> None:
    if data.backend == "duckdb" and data.view is not None:
        found = connection.execute(
            "SELECT table_type FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = ?",
            [data.view],
        ).fetchall()
        if not found:
            raise IntegrityError("schema", f"Catalog has no table or view named {data.view}.")
    probe = connection.execute(f"SELECT * FROM {relation_sql} LIMIT 0", parameters)
    description = probe.description
    if description is None:
        raise IntegrityError("schema", "DuckDB returned no column description.")
    actual = {_column_name(entry): _type_token(entry) for entry in description}
    for column in data.columns:
        if column.name not in actual:
            raise IntegrityError("schema", f"Column {column.name} is missing.")
        token = actual[column.name]
        if token not in _TYPES_FOR_DTYPE[column.dtype]:
            raise IntegrityError(
                "schema", f"Column {column.name} is {token}, expected {column.dtype}."
            )


def _fetch_rows(
    connection: duckdb.DuckDBPyConnection,
    data: DataSpec,
    relation_sql: str,
    parameters: list[object],
) -> list[tuple[object, ...]]:
    quoted = ", ".join(f'"{column.name}"' for column in data.columns)
    order = f'"{data.timestamp_column}"'
    if data.symbol_column is not None:
        order += f', "{data.symbol_column}"'
    limit = data.max_rows + 1
    sql = f"SELECT {quoted} FROM {relation_sql} ORDER BY {order} LIMIT ?"
    fetched = connection.execute(sql, [*parameters, limit]).fetchall()
    if len(fetched) > data.max_rows:
        raise IntegrityError(
            "too_many_rows",
            f"Source exceeds max_rows {data.max_rows}. Aggregate to bars before running.",
        )
    rows: list[tuple[object, ...]] = []
    for fetched_row in fetched:
        rows.append(tuple(fetched_row))
    return rows


def _table_from_rows(spec: HypothesisSpec, rows: list[tuple[object, ...]]) -> BarTable:
    data = spec.data
    index_by_name = {column.name: index for index, column in enumerate(data.columns)}
    timestamps: list[int] = []
    prices: list[float] = []
    feature_values: dict[str, list[float]] = {
        column.name: [] for column in data.columns if column.role == "feature"
    }
    availability_values: dict[str, list[int]] = {
        column.name: [] for column in data.columns if column.role == "availability"
    }
    funding_column = spec.costs.funding_column
    funding_values: list[float] = []
    for row_index, row in enumerate(rows):
        if len(row) != len(data.columns):
            raise IntegrityError("schema", f"Row {row_index} does not match the declared width.")
        if any(value is None for value in row):
            raise IntegrityError(
                "schema", f"Row {row_index} contains a null. Nulls are not filled with zero."
            )
        timestamps.append(
            _as_int(row[index_by_name[data.timestamp_column]], data.timestamp_column, row_index)
        )
        prices.append(
            _as_float(row[index_by_name[data.price_column]], data.price_column, row_index)
        )
        for name, feature_bucket in feature_values.items():
            feature_bucket.append(_as_float(row[index_by_name[name]], name, row_index))
        for name, clock_bucket in availability_values.items():
            clock_bucket.append(_as_int(row[index_by_name[name]], name, row_index))
        if funding_column is not None:
            funding_values.append(
                _as_float(row[index_by_name[funding_column]], funding_column, row_index)
            )
    _audit_clock(timestamps, data.max_gap)
    _audit_prices(prices)
    availability = {name: tuple(values) for name, values in availability_values.items()}
    table = BarTable(
        timestamps=tuple(timestamps),
        prices=tuple(prices),
        features={name: tuple(values) for name, values in feature_values.items()},
        availability=availability,
        funding=None if funding_column is None else tuple(funding_values),
    )
    _audit_lookahead(spec.features, table)
    return table


def load_panel(spec: HypothesisSpec, spec_dir: Path) -> PanelTable:
    """Load a panel: one row per symbol and timestamp, audited like a bar series.

    The date axis must be gap-free and duplicate-free; a (timestamp, symbol)
    pair may appear once. Price and the traded flag are required on every
    row; the signal, rank and funding columns may be null where the panel
    does not know them, and a null is kept as None rather than read as 0.
    """

    data = spec.data
    if data.backend != "panel" or data.symbol_column is None:
        raise IntegrityError("data_config", "load_panel needs data.backend panel.")
    connection, relation_sql, parameters = _open_source(data, spec_dir)
    try:
        _assert_relation(connection, data, relation_sql, parameters)
        rows = _fetch_rows(connection, data, relation_sql, parameters)
    except IntegrityError:
        raise
    except duckdb.Error as error:
        raise IntegrityError("schema", f"DuckDB read failed: {_short(str(error))}") from error
    finally:
        connection.close()
    return _panel_from_rows(spec, rows)


def _panel_from_rows(spec: HypothesisSpec, rows: list[tuple[object, ...]]) -> PanelTable:
    data = spec.data
    index_by_name = {column.name: index for index, column in enumerate(data.columns)}
    signal_feature = next(
        feature for feature in spec.features if feature.name == spec.signal_feature
    )
    clocks = {feature.available_at_column: feature.name for feature in spec.features}
    required = [
        name
        for name in (
            data.timestamp_column,
            data.symbol_column,
            data.price_column,
            data.traded_column,
            *clocks,
        )
        if name is not None
    ]
    timestamp_at = index_by_name[data.timestamp_column]
    symbol_at = index_by_name[str(data.symbol_column)]
    price_at = index_by_name[data.price_column]
    traded_at = index_by_name[str(data.traded_column)]
    rank_at = index_by_name[str(data.rank_column)]
    signal_at = index_by_name[signal_feature.column]
    clock_at = {index_by_name[column]: name for column, name in clocks.items()}
    with_funding = spec.costs.funding_column is not None
    funding_at = index_by_name[str(spec.costs.funding_column)] if with_funding else -1
    covered_at = index_by_name[str(data.funding_covered_column)] if with_funding else -1
    names = (
        data.timestamp_column,
        str(data.symbol_column),
        data.price_column,
        str(data.traded_column),
        str(data.rank_column),
        signal_feature.column,
        str(spec.costs.funding_column),
        str(data.funding_covered_column),
    )
    # First pass: the axes and every row-level check; second pass: the series.
    keys: list[tuple[int, str]] = []
    for row_index, row in enumerate(rows):
        if len(row) != len(data.columns):
            raise IntegrityError("schema", f"Row {row_index} does not match the declared width.")
        if any(row[index_by_name[name]] is None for name in required):
            raise IntegrityError(
                "schema",
                f"Row {row_index} has a null timestamp, symbol, price, traded flag or clock.",
            )
        timestamp = _as_int(row[timestamp_at], names[0], row_index)
        for index, feature_name in clock_at.items():
            clock = _as_int(row[index], feature_name, row_index)
            if clock > timestamp:
                raise IntegrityError(
                    "lookahead",
                    f"Feature {feature_name} row {row_index} is available at {clock}, "
                    f"after bar {timestamp}.",
                )
        keys.append((timestamp, _as_str(row[symbol_at], names[1], row_index)))
    timestamps = sorted({timestamp for timestamp, _symbol in keys})
    _audit_clock(timestamps, data.max_gap)
    symbols = sorted({symbol for _timestamp, symbol in keys})
    if len(symbols) > _PANEL_MAX_SYMBOLS:
        raise IntegrityError("too_many_rows", f"Panel has more than {_PANEL_MAX_SYMBOLS} symbols.")
    date_index = {timestamp: index for index, timestamp in enumerate(timestamps)}
    symbol_index = {symbol: index for index, symbol in enumerate(symbols)}
    width = len(timestamps)
    prices: list[list[float | None]] = [[None] * width for _ in symbols]
    traded: list[list[bool | None]] = [[None] * width for _ in symbols]
    ranks: list[list[int | None]] = [[None] * width for _ in symbols]
    signals: list[list[float | None]] = [[None] * width for _ in symbols]
    funding: list[list[float | None]] = [[None] * width for _ in symbols] if with_funding else []
    covered: list[list[bool | None]] = [[None] * width for _ in symbols] if with_funding else []
    for row_index, (row, (timestamp, symbol)) in enumerate(zip(rows, keys, strict=True)):
        column = date_index[timestamp]
        line = symbol_index[symbol]
        if prices[line][column] is not None:
            raise IntegrityError("duplicate", f"{symbol} appears twice at {timestamp}.")
        price = _as_float(row[price_at], names[2], row_index)
        if not math.isfinite(price) or price <= 0.0:
            raise IntegrityError("price", f"Price at row {row_index} must be finite and positive.")
        prices[line][column] = price
        traded[line][column] = _as_bool(row[traded_at], names[3], row_index)
        rank_raw = row[rank_at]
        ranks[line][column] = None if rank_raw is None else _as_int(rank_raw, names[4], row_index)
        signal_raw = row[signal_at]
        signals[line][column] = (
            None if signal_raw is None else _as_float(signal_raw, names[5], row_index)
        )
        if with_funding:
            funding_raw = row[funding_at]
            funding[line][column] = (
                None if funding_raw is None else _as_float(funding_raw, names[6], row_index)
            )
            covered_raw = row[covered_at]
            covered[line][column] = (
                None if covered_raw is None else _as_bool(covered_raw, names[7], row_index)
            )
    return PanelTable(
        timestamps=tuple(timestamps),
        symbols=tuple(symbols),
        prices=tuple(tuple(line) for line in prices),
        traded=tuple(tuple(line) for line in traded),
        ranks=tuple(tuple(line) for line in ranks),
        signals=tuple(tuple(line) for line in signals),
        funding=tuple(tuple(line) for line in funding) if with_funding else None,
        covered=tuple(tuple(line) for line in covered) if with_funding else None,
    )


def _as_str(value: object, column: str, row_index: int) -> str:
    if not isinstance(value, str) or not value:
        raise IntegrityError("schema", f"Column {column} row {row_index} is not a symbol.")
    return value


def _as_bool(value: object, column: str, row_index: int) -> bool:
    if not isinstance(value, bool):
        raise IntegrityError("schema", f"Column {column} row {row_index} is not a boolean.")
    return value


def _audit_clock(timestamps: list[int], max_gap: int) -> None:
    for index in range(1, len(timestamps)):
        delta = timestamps[index] - timestamps[index - 1]
        if delta == 0:
            raise IntegrityError("duplicate", f"Duplicate timestamp at row {index}.")
        if delta < 0:
            raise IntegrityError("order", f"Timestamp moves backwards at row {index}.")
        if delta > max_gap:
            raise IntegrityError(
                "gap",
                f"Gap {delta} exceeds max_gap {max_gap} at row {index}. Rows are not zero-filled.",
            )


def _audit_prices(prices: list[float]) -> None:
    for index, price in enumerate(prices):
        if not math.isfinite(price) or price <= 0.0:
            raise IntegrityError("price", f"Price at row {index} must be finite and positive.")


def _audit_lookahead(features: tuple[FeatureSpec, ...], table: BarTable) -> None:
    """A feature value is usable at bar t only when its availability clock is <= t."""

    for feature in features:
        if feature.available_at_column in table.availability:
            clock = table.availability[feature.available_at_column]
        else:
            clock = table.timestamps
        for index, (bar_ts, available_ts) in enumerate(zip(table.timestamps, clock, strict=True)):
            if available_ts > bar_ts:
                raise IntegrityError(
                    "lookahead",
                    (
                        f"Feature {feature.name} row {index} is available at "
                        f"{available_ts}, after bar {bar_ts}."
                    ),
                )


def _as_int(value: object, column: str, row_index: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IntegrityError("schema", f"Column {column} row {row_index} is not an integer.")
    return value


def _as_float(value: object, column: str, row_index: int) -> float:
    if isinstance(value, bool) or not isinstance(value, float):
        raise IntegrityError("schema", f"Column {column} row {row_index} is not a float.")
    if not math.isfinite(value):
        raise IntegrityError("schema", f"Column {column} row {row_index} is not finite.")
    return value


def _column_name(entry: object) -> str:
    if not isinstance(entry, tuple) or not entry:
        raise IntegrityError("schema", "DuckDB description was not a column tuple.")
    return str(entry[0])


def _type_token(entry: object) -> str:
    if not isinstance(entry, tuple) or len(entry) < 2:
        raise IntegrityError("schema", "DuckDB description was not a column tuple.")
    return str(entry[1]).upper().split("(", 1)[0].strip()


def _short(message: str) -> str:
    if len(message) <= 300:
        return message
    return message[:300]
