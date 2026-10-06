"""Ingest Kraken OHLCVT quarterly zips for selected pairs.

The support article defines headerless rows
``timestamp,open,high,low,close,volume,trades`` with Unix seconds.
Intervals with no trades are omitted, so a missing minute is not a gap.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import duckdb

from research.hist_etl.errors import HistEtlError
from research.hist_etl.models import KRAKEN_MINUTES_TO_SLUG, Gap, KrakenSpec, SourceDigest

_MEMBER = re.compile(r"(?i)(?:^|/)([A-Z0-9]+)_(\d+)\.csv$")
_COLUMNS = ("source", "ts_raw", "open", "high", "low", "close", "volume", "trades")


def kraken_parquet_path(root: Path, pair: str, interval: str, month: str) -> Path:
    return root / "parquet" / "kraken" / "ohlcvt" / pair / interval / f"{month}.parquet"


def ingest_kraken(
    spec: KrakenSpec,
    zips: tuple[Path, ...],
    *,
    root: Path,
    sources: tuple[SourceDigest, ...],
) -> tuple[Gap, ...]:
    if not zips:
        return (Gap("missing_kraken_zip", spec.id, f"no zip matched {spec.zip_glob}"),)
    members = _members(zips, spec)
    if not members:
        pairs = ", ".join(spec.pairs)
        return (
            Gap("missing_kraken_zip", spec.id, f"no CSV for {pairs} in the selected intervals"),
        )
    gaps: list[Gap] = []
    grouped: dict[tuple[str, str], list[tuple[Path, str]]] = defaultdict(list)
    for zip_path, member, pair, interval in members:
        grouped[(pair, interval)].append((zip_path, member))
    for pair in spec.pairs:
        found_intervals = {interval for found_pair, interval in grouped if found_pair == pair}
        missing = [interval for interval in spec.intervals if interval not in found_intervals]
        for interval in missing:
            gaps.append(Gap("missing_kraken_zip", spec.id, f"{pair} {interval} CSV is absent"))
    for (pair, interval), files in sorted(grouped.items()):
        gaps.extend(_materialize_series(spec, pair, interval, files, root, sources))
    return tuple(gaps)


def audit_kraken_tree(spec: KrakenSpec, root: Path) -> tuple[Gap, ...]:
    gaps: list[Gap] = []
    for pair in spec.pairs:
        for interval in spec.intervals:
            directory = root / "parquet" / "kraken" / "ohlcvt" / pair / interval
            files = sorted(directory.glob("*.parquet")) if directory.is_dir() else []
            if not files:
                gaps.append(Gap("missing_parquet", spec.id, f"{pair} {interval} parquet is absent"))
                continue
            for path in files:
                gaps.extend(_audit_file(spec.id, path))
    return tuple(gaps)


def _materialize_series(
    spec: KrakenSpec,
    pair: str,
    interval: str,
    files: list[tuple[Path, str]],
    root: Path,
    sources: tuple[SourceDigest, ...],
) -> tuple[Gap, ...]:
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone='UTC'")
        frames: list[Path] = []
        staging = root / "staging" / "hist_etl" / "kraken"
        staging.mkdir(parents=True, exist_ok=True)
        try:
            for index, (zip_path, member) in enumerate(files):
                target = staging / f"{pair}-{interval}-{index}.csv"
                _extract_member(zip_path, member, target)
                frames.append(target)
            _load(connection, frames)
            problems = _validate(connection)
            if problems:
                return tuple(Gap("kraken_schema", spec.id, problem) for problem in problems)
            connection.execute(
                """
                CREATE TABLE published AS
                SELECT * EXCLUDE (rn) FROM (
                    SELECT
                        CAST(to_timestamp(ts_i) AS TIMESTAMP) AS ts,
                        open_n AS open,
                        high_n AS high,
                        low_n AS low,
                        close_n AS close,
                        volume_n AS volume,
                        trades_n AS trades,
                        ? AS pair,
                        ? AS interval,
                        source AS source_name,
                        row_number() OVER (PARTITION BY ts_i ORDER BY source DESC) AS rn
                    FROM typed
                )
                WHERE rn = 1
                """,
                [pair, interval],
            )
            months = connection.execute(
                "SELECT DISTINCT strftime(ts, '%Y-%m') FROM published ORDER BY 1"
            ).fetchall()
            for (month,) in months:
                if not isinstance(month, str):
                    continue
                destination = kraken_parquet_path(root, pair, interval, month)
                if _month_is_current(destination, sources):
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                partial = destination.with_name(destination.name + ".partial")
                # COPY TO consumes parameters before the SELECT, so filter first.
                connection.execute(
                    """
                    CREATE OR REPLACE TABLE month_rows AS
                    SELECT * FROM published
                    WHERE strftime(ts, '%Y-%m') = ?
                    ORDER BY ts
                    """,
                    [month],
                )
                connection.execute(
                    "COPY month_rows TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                    [str(partial)],
                )
                os.replace(partial, destination)
                _write_sidecar(destination, sources)
        finally:
            for path in frames:
                if path.is_file():
                    path.unlink()
        return ()
    finally:
        connection.close()


def _audit_file(dataset_id: str, path: Path) -> tuple[Gap, ...]:
    connection = duckdb.connect()
    try:
        row = connection.execute(
            """
            SELECT
                count(*) FILTER (
                    WHERE high < low OR high < open OR high < close
                       OR low > open OR low > close
                       OR open <= 0 OR close <= 0 OR volume < 0 OR trades < 0
                ),
                count(*) - count(DISTINCT ts)
            FROM read_parquet(?)
            """,
            [str(path)],
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return (Gap("kraken_schema", dataset_id, f"{path.name} could not be read"),)
    gaps: list[Gap] = []
    if int(row[0]) > 0:
        gaps.append(
            Gap("kraken_schema", dataset_id, f"{path.name} has {int(row[0])} invalid OHLC rows")
        )
    if int(row[1]) > 0:
        gaps.append(Gap("kraken_conflict", dataset_id, f"{path.name} has duplicate timestamps"))
    return tuple(gaps)


def _members(zips: tuple[Path, ...], spec: KrakenSpec) -> list[tuple[Path, str, str, str]]:
    wanted_pairs = set(spec.pairs)
    wanted_intervals = set(spec.intervals)
    found: list[tuple[Path, str, str, str]] = []
    for zip_path in zips:
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                match = _MEMBER.search(info.filename)
                if match is None:
                    continue
                pair = match.group(1).upper()
                minutes = int(match.group(2))
                interval = KRAKEN_MINUTES_TO_SLUG.get(minutes)
                if pair not in wanted_pairs or interval is None or interval not in wanted_intervals:
                    continue
                found.append((zip_path, info.filename, pair, interval))
    return found


def _extract_member(zip_path: Path, member: str, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive, archive.open(member, "r") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        reader = csv.reader(text)
        with dest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            for row in reader:
                if not row or all(not cell.strip() for cell in row):
                    continue
                if not _is_int(row[0]) and row[0].strip().lower() == "timestamp":
                    continue
                if len(row) != 7:
                    raise HistEtlError(
                        f"{zip_path.name}:{member} has {len(row)} columns; expected 7",
                        exit_code=2,
                    )
                writer.writerow(
                    [f"{zip_path.name}:{Path(member).name}", *[cell.strip() for cell in row]]
                )


def _load(connection: duckdb.DuckDBPyConnection, paths: list[Path]) -> None:
    column_sql = ", ".join(f"'{name}': 'VARCHAR'" for name in _COLUMNS)
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


def _validate(connection: duckdb.DuckDBPyConnection) -> list[str]:
    connection.execute(
        """
        CREATE TABLE typed AS
        SELECT
            source,
            try_cast(ts_raw AS BIGINT) AS ts_i,
            try_cast(open AS DOUBLE) AS open_n,
            try_cast(high AS DOUBLE) AS high_n,
            try_cast(low AS DOUBLE) AS low_n,
            try_cast(close AS DOUBLE) AS close_n,
            try_cast(volume AS DOUBLE) AS volume_n,
            try_cast(trades AS BIGINT) AS trades_n
        FROM raw
        """
    )
    row = connection.execute(
        """
        SELECT
            count(*),
            count(*) FILTER (
                WHERE ts_i IS NULL OR open_n IS NULL OR high_n IS NULL OR low_n IS NULL
                   OR close_n IS NULL OR volume_n IS NULL OR trades_n IS NULL
                   OR ts_i < 1000000000 OR ts_i >= 100000000000
            ),
            count(*) FILTER (
                WHERE high_n < low_n OR high_n < open_n OR high_n < close_n
                   OR low_n > open_n OR low_n > close_n
                   OR open_n <= 0 OR close_n <= 0 OR volume_n < 0 OR trades_n < 0
            )
        FROM typed
        """
    ).fetchone()
    problems: list[str] = []
    if row is None or int(row[0]) == 0:
        problems.append("no Kraken rows")
        return problems
    if int(row[1]) > 0:
        problems.append(f"{int(row[1])} Kraken rows have a bad timestamp or number")
    if int(row[2]) > 0:
        problems.append(f"{int(row[2])} Kraken rows fail OHLC checks")
    conflict = connection.execute(
        """
        SELECT count(*) FROM (
            SELECT ts_i FROM typed
            GROUP BY ts_i
            HAVING count(*) > 1 AND (
                count(DISTINCT open_n) > 1 OR count(DISTINCT high_n) > 1
                OR count(DISTINCT low_n) > 1 OR count(DISTINCT close_n) > 1
                OR count(DISTINCT volume_n) > 1 OR count(DISTINCT trades_n) > 1
            )
        )
        """
    ).fetchone()
    if conflict is not None and int(conflict[0]) > 0:
        problems.append(f"{int(conflict[0])} Kraken timestamps disagree across archives")
    return problems


def _month_is_current(destination: Path, sources: tuple[SourceDigest, ...]) -> bool:
    sidecar = destination.with_name(destination.name + ".sources.json")
    if not destination.is_file() or not sidecar.is_file():
        return False
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return False
    expected = [{"name": source.name, "sha256": source.sha256} for source in sources]
    return payload.get("sources") == expected


def _write_sidecar(destination: Path, sources: tuple[SourceDigest, ...]) -> None:
    sidecar = destination.with_name(destination.name + ".sources.json")
    payload = {
        "sources": [{"name": source.name, "sha256": source.sha256} for source in sources],
        "sparse_intervals": True,
    }
    sidecar.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _is_int(value: str) -> bool:
    try:
        int(value.strip())
    except ValueError:
        return False
    return True


def manifest_present(zip_path: Path) -> bool:
    with zipfile.ZipFile(zip_path) as archive:
        return any(Path(info.filename).name == "MANIFEST.json" for info in archive.infolist())
