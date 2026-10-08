"""Load test rows into a DuckDB table with one bulk copy.

``executemany`` binds every row on its own and takes seconds for a few
thousand rows, which made row-building fixtures the bulk of the research
harness tests' run time. Writing the rows to a CSV file and copying it in
takes milliseconds and yields the same table: floats round-trip through
their shortest ``repr`` (NaN, infinities and -0.0 included), ``None`` is
NULL and an empty string stays an empty string.
"""

from __future__ import annotations

import csv
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

import duckdb

# The NULL marker of the CSV file; a string cell with this exact value is refused.
_NULL = "\\N"


def insert_rows(
    connection: duckdb.DuckDBPyConnection, table: str, rows: Iterable[Sequence[object]]
) -> None:
    """Append ``rows`` to the existing ``table``, cast to its declared column types."""

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "rows.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            for row in rows:
                writer.writerow([_cell(value) for value in row])
        source = str(path).replace("'", "''")
        connection.execute(
            f"COPY {table} FROM '{source}' (FORMAT csv, HEADER false, DELIM ',', "
            f"QUOTE '\"', ESCAPE '\"', NULLSTR '{_NULL}', AUTO_DETECT false)"
        )


def _cell(value: object) -> object:
    if value is None:
        return _NULL
    if isinstance(value, bytes | bytearray):
        raise TypeError("insert_rows does not load BLOB values; use executemany for those.")
    if value == _NULL:
        raise ValueError(f"A string cell equal to the NULL marker {_NULL!r} is ambiguous.")
    return value
