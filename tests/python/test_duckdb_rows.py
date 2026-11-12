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


def test_opt_in_binary_rows_match_all_frame_columns_types_and_insertion_order() -> None:
    columns = (
        "(schema_version INTEGER,venue VARCHAR,product VARCHAR,channel VARCHAR,"
        "session_id VARCHAR,message_ordinal BIGINT,received_utc_ns BIGINT,"
        "received_monotonic_ns BIGINT,direction VARCHAR,payload_bytes BLOB,"
        "payload_sha256 VARCHAR)"
    )
    payloads = [
        bytes(range(256)),
        b"",
        b"\\N",
        b'\\x41\x00\xff\r\n"\\',
        bytearray("binary \u20ac".encode()),
        None,
    ]
    rows: list[tuple[object, ...]] = [
        (
            1,
            "venue,\u20ac",
            "BTC-PERP",
            'channel"\r\nline',
            "session\\literal",
            len(payloads) - index,
            1_799_999_999_123_456_789 + index,
            9_007_199_254_740_993 + index,
            "",
            payload,
            "a" * 64,
        )
        for index, payload in enumerate(payloads)
    ]
    connection = duckdb.connect()
    connection.execute("CREATE TABLE ordinary " + columns)
    connection.execute("CREATE TABLE bulk " + columns)
    connection.executemany("INSERT INTO ordinary VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    insert_rows(connection, "bulk", iter(rows), allow_blobs=True)
    expected = connection.execute("SELECT rowid,* FROM ordinary ORDER BY rowid").fetchall()
    actual = connection.execute("SELECT rowid,* FROM bulk ORDER BY rowid").fetchall()
    assert actual == expected
    assert (
        connection.execute("DESCRIBE ordinary").fetchall()
        == connection.execute("DESCRIBE bulk").fetchall()
    )
    connection.close()


def test_opt_in_binary_rows_keep_null_marker_refusal_before_loading_any_rows() -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE blobs (payload BLOB,label VARCHAR)")
    with pytest.raises(ValueError, match="NULL marker"):
        insert_rows(connection, "blobs", [(b"valid", "ok"), (b"\\N", "\\N")], allow_blobs=True)
    assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone() == (0,)
    connection.close()


def test_opt_in_binary_rows_accept_no_rows() -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE blobs (payload BLOB)")
    insert_rows(connection, "blobs", [], allow_blobs=True)
    assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone() == (0,)
    connection.close()
