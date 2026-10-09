"""The bulk row loader builds the same table as executemany."""

from __future__ import annotations

import duckdb
import pytest

from duckdb_rows import insert_rows

_DDL = "CREATE TABLE t (i BIGINT, x DOUBLE, s VARCHAR, b BOOLEAN, y DOUBLE)"

_EDGE_ROWS: list[tuple[object, ...]] = [
    (1, float("nan"), "a,b", True, None),
    (2, float("inf"), 'say "hi"', False, -0.0),
    (3, float("-inf"), "it's\nnew", None, 5e-324),
    (9223372036854775807, 1.7976931348623157e308, "", True, 0.1 + 0.2),
    (-4, 100, "S1", False, 1),
    (5, 1.5, "carriage\rreturn", True, 2.5),
    (6, 2.5, "crlf\r\nline", False, 3.5),
]


def _table(load: str, rows: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    connection = duckdb.connect()
    connection.execute(_DDL)
    if load == "executemany":
        connection.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?)", rows)
    else:
        insert_rows(connection, "t", rows)
    fetched = connection.execute("SELECT * FROM t ORDER BY i").fetchall()
    connection.close()
    return fetched


def test_insert_rows_matches_executemany_on_edge_values() -> None:
    expected = _table("executemany", _EDGE_ROWS)
    actual = _table("insert_rows", _EDGE_ROWS)
    # repr compares NaN, the sign of zero and exact floats, which == cannot.
    assert repr(actual) == repr(expected)


def test_insert_rows_accepts_no_rows() -> None:
    assert _table("insert_rows", []) == []


def test_insert_rows_refuses_ambiguous_or_binary_cells() -> None:
    with pytest.raises(ValueError, match="NULL marker"):
        _table("insert_rows", [(1, 1.0, "\\N", True, 1.0)])
    connection = duckdb.connect()
    connection.execute("CREATE TABLE blobs (payload BLOB)")
    with pytest.raises(TypeError, match="BLOB"):
        insert_rows(connection, "blobs", [(b"\x00",)])
    connection.close()
