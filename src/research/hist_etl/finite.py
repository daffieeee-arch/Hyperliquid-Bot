"""Finite-value checks for the ETL's required financial columns."""

from __future__ import annotations

from collections.abc import Sequence

import duckdb


def invalid_financial_rows(
    connection: duckdb.DuckDBPyConnection, table: str, columns: Sequence[str]
) -> int:
    """Count null/non-finite values; table and columns are internal schema constants.

    Optional non-financial fields (funding intervals, boolean flags) are not
    part of this check and retain their existing null conventions.
    """

    clauses = [f'"{name}" IS NULL OR NOT isfinite("{name}")' for name in columns]
    row = connection.execute(
        f'SELECT count(*) FROM "{table}" WHERE ' + " OR ".join(clauses)
    ).fetchone()
    assert row is not None
    return int(row[0])
