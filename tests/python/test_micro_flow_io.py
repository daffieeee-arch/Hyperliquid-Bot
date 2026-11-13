"""Synthetic native receipts and sealed Parquet admission; no market archives."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from research import micro_flow_io as io
from research.harness.errors import IntegrityError

MILLISECOND = 1_000_000
NOW_MS = 1_700_000_000_000


def best(price: str, size: str = "1") -> dict[str, str | int]:
    return {"px": price, "sz": size, "n": 2}


def book(*, coin: str = "BTC", time: int = NOW_MS) -> dict[str, Any]:
    return {"coin": coin, "time": time, "bbo": [best("100000", "3"), best("100002")]}


def trade(
    *, time: int = NOW_MS, tid: int = 7, coin: str = "BTC", side: str = "B"
) -> dict[str, Any]:
    return {"coin": coin, "time": time, "tid": tid, "side": side, "px": "100001", "sz": "0.1"}


def receipt(
    sequence: int,
    channel: str,
    data: Any,
    *,
    direction: str = "inbound",
    session: str = "a",
    utc_ms: int = NOW_MS,
    body: Any = None,
) -> tuple[Any, ...]:
    payload = json.dumps({"channel": channel, "data": data} if body is None else body).encode()
    return (
        1,
        "hyperliquid",
        "BTC-PERP",
        channel,
        session,
        sequence,
        utc_ms * MILLISECOND,
        sequence * MILLISECOND,
        direction,
        "TEXT",
        "utf-8",
        payload,
        hashlib.sha256(payload).hexdigest(),
    )


def windows(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[tuple[Any, ...]],
) -> list[tuple[str, list[Any], dict[str, Any]]]:
    def source(capture: dict[str, Any]) -> Iterator[tuple[Any, ...]]:
        yield from rows

    monkeypatch.setattr(io, "native_records", source)
    return list(io.capture_windows({"name": "synthetic", "group": "development"}))


def test_native_bbo_preserves_exact_prices_sizes_and_both_clocks() -> None:
    event = io.book_event("bbo", book(), 5_000, NOW_MS * MILLISECOND, "a")
    assert event is not None
    assert (event.bid, event.ask, event.bid_size, event.ask_size) == (
        Decimal("100000"),
        Decimal("100002"),
        Decimal("3"),
        Decimal("1"),
    )
    assert event.mono_ns == 5_000 and event.venue_ms == NOW_MS
    assert event.utc_ns == NOW_MS * MILLISECOND and event.session == "a"


def test_full_l2_snapshot_uses_best_levels_not_a_bbo_shaped_payload() -> None:
    payload = {
        "coin": "BTC",
        "time": NOW_MS,
        "levels": [
            [best("100000", "3"), best("99999", "9")],
            [best("100002", "1"), best("100003", "8")],
        ],
    }
    event = io.book_event("l2Book", payload, 5, NOW_MS * MILLISECOND, "a")
    assert event is not None
    assert (event.bid, event.ask, event.bid_size, event.ask_size) == (
        Decimal("100000"),
        Decimal("100002"),
        Decimal("3"),
        Decimal("1"),
    )


@pytest.mark.parametrize(
    ("age_ms", "accepted"), [(2000, True), (2001, False), (-1000, True), (-1001, False)]
)
def test_native_book_age_and_future_clock_bounds(age_ms: int, accepted: bool) -> None:
    event = io.book_event("bbo", book(time=NOW_MS - age_ms), 5, NOW_MS * MILLISECOND, "a")
    assert (event is not None) == accepted


@pytest.mark.parametrize("levels", [[None, best("100002")], [best("100000"), None], [None, None]])
def test_null_native_bbo_is_explicitly_unavailable(levels: list[Any]) -> None:
    payload = book()
    payload["bbo"] = levels
    assert io.book_event("bbo", payload, 5, NOW_MS * MILLISECOND, "a") is None


def test_empty_l2_side_is_unavailable_and_not_an_invented_zero_price() -> None:
    payload = {"coin": "BTC", "time": NOW_MS, "levels": [[], [best("100002")]]}
    assert io.book_event("l2Book", payload, 5, NOW_MS * MILLISECOND, "a") is None


@pytest.mark.parametrize("bad", ["0", "-1", "NaN", "Infinity"])
def test_invalid_best_price_refuses_instead_of_imputing(bad: str) -> None:
    payload = book()
    payload["bbo"][0]["px"] = bad
    with pytest.raises(IntegrityError):
        io.book_event("bbo", payload, 5, NOW_MS * MILLISECOND, "a")


def test_native_outer_product_does_not_turn_eth_or_sol_into_btc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    got = windows(
        monkeypatch,
        [receipt(1, "bbo", book(coin="ETH")), receipt(2, "trades", [trade(coin="SOL")])],
    )
    assert len(got) == 1 and got[0][1] == []
    meta = got[0][2]
    assert meta["quality_only"] and meta["conditional_windows"] == 0
    assert meta["counts"] == {"other_coin_frames": 1, "other_coin_trades": 1}


def test_trade_identity_includes_native_time_and_survives_reconnection_dedup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    got = windows(
        monkeypatch,
        [
            receipt(1, "bbo", book()),
            receipt(2, "trades", [trade()]),
            receipt(3, "bbo", book(), session="b"),
            receipt(4, "trades", [trade(), trade(time=NOW_MS + 1, side="A")], session="b"),
        ],
    )
    actual = [event for _, events, _ in got for event in events if event.kind == "trade"]
    assert len(actual) == 2
    assert [event.trade_side for event in actual] == [1, -1]
    assert [event.venue_ms for event in actual] == [NOW_MS, NOW_MS + 1]
    assert got[-1][2]["counts"]["duplicate_native_trades"] == 1
    assert got[-1][2]["conditional_windows"] == 2


@pytest.mark.parametrize(
    "marker", ["gap_detected", "market_data_stale_despite_heartbeat", "disconnected"]
)
def test_gap_blackout_excludes_inbound_until_explicit_recovery(
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    got = windows(
        monkeypatch,
        [
            receipt(1, "bbo", book()),
            receipt(2, "data_quality", {}, direction="local", body={"event": marker}),
            receipt(3, "trades", [trade(tid=99)]),
            receipt(4, "session", {}, direction="local", body={"event": "market_data_recovered"}),
            receipt(5, "trades", [trade(tid=100)]),
        ],
    )
    actual = [event for _, events, _ in got for event in events]
    assert [e.kind for e in actual] == ["book", "trade"]
    assert got[0][2]["end_reason"] == marker
    assert got[-1][2]["counts"]["gap_or_stale_markers"] == 1


def test_quality_only_capture_and_trailing_invalid_suffix_keep_every_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        receipt(1, "bbo", book(time=NOW_MS - 2001)),
        receipt(2, "data_quality", {}, direction="local", body={"event": "gap_detected"}),
    ]
    quality = windows(monkeypatch, rows)
    assert len(quality) == 1 and quality[0][1] == []
    assert quality[0][2]["counts"] == {
        "stale_or_unavailable_native_book": 1,
        "gap_or_stale_markers": 1,
    }
    suffix = windows(
        monkeypatch,
        [
            receipt(1, "bbo", book()),
            receipt(2, "bbo", book(time=NOW_MS - 2001)),
            receipt(3, "bbo", book(time=NOW_MS - 2001)),
        ],
    )
    assert len(suffix) == 2 and suffix[-1][1] == []
    assert suffix[-1][2]["counts"] == {"bbo": 1, "stale_or_unavailable_native_book": 2}


def test_outgoing_trade_shaped_messages_never_create_economic_trades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    got = windows(monkeypatch, [receipt(1, "trades", [trade()], direction="outbound")])
    assert len(got) == 1 and got[0][1] == [] and got[0][2]["counts"] == {}


def test_late_trade_is_an_explicit_arrival_filter_not_zero_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    got = windows(
        monkeypatch,
        [
            receipt(1, "bbo", book()),
            receipt(2, "trades", [trade(time=NOW_MS - 2001), trade(time=NOW_MS + 1001), trade()]),
        ],
    )
    assert [e.kind for e in got[0][1]] == ["book", "trade"]
    assert got[-1][2]["counts"]["stale_or_snapshot_trades"] == 2


@pytest.mark.parametrize("body", [[], 1, None])
def test_nonobject_native_json_refuses_before_market_decoding(
    monkeypatch: pytest.MonkeyPatch,
    body: Any,
) -> None:
    row = list(receipt(1, "bbo", book()))
    row[11] = json.dumps(body).encode()
    with pytest.raises(IntegrityError, match="JSON object"):
        windows(monkeypatch, [tuple(row)])


def native_part(
    tmp_path: Path,
    rows: list[tuple[Any, ...]],
    *,
    extra_column: bool = False,
) -> dict[str, Any]:
    path = tmp_path / "part.parquet"
    connection = duckdb.connect()
    types = (
        "INTEGER",
        "VARCHAR",
        "VARCHAR",
        "VARCHAR",
        "VARCHAR",
        "BIGINT",
        "BIGINT",
        "BIGINT",
        "VARCHAR",
        "VARCHAR",
        "VARCHAR",
        "BLOB",
        "VARCHAR",
    )
    declaration = ",".join(f"{name} {kind}" for name, kind in zip(io.COLUMNS, types, strict=True))
    try:
        connection.execute("CREATE TABLE receipts(" + declaration + ")")
        connection.executemany(
            "INSERT INTO receipts VALUES (" + ",".join("?" for _ in types) + ")", rows
        )
        if extra_column:
            connection.execute("ALTER TABLE receipts ADD COLUMN unexpected VARCHAR")
        connection.execute("COPY receipts TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()
    content = path.read_bytes()
    return {"path": str(path), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def test_sealed_native_parquet_roundtrips_original_payloads(tmp_path: Path) -> None:
    rows = [receipt(1, "bbo", book()), receipt(2, "trades", [trade()])]
    part = native_part(tmp_path, rows)
    assert list(io.native_records({"parts": [part], "metadata": []})) == rows


def test_physical_receipt_order_cannot_be_repaired_by_sorting_ordinal(tmp_path: Path) -> None:
    first = receipt(1, "bbo", book())
    second = list(receipt(2, "trades", [trade()]))
    # Equal clocks deliberately make time-regression checks unable to detect
    # reversal. Only validating ordinals in the stored physical order catches it.
    second[6:8] = first[6:8]
    part = native_part(tmp_path, [tuple(second), first])
    with pytest.raises(IntegrityError, match="complete receipt ordinals"):
        list(io.native_records({"parts": [part], "metadata": []}))


def test_unknown_parquet_schema_cannot_silently_change_native_contract(tmp_path: Path) -> None:
    part = native_part(tmp_path, [receipt(1, "bbo", book())], extra_column=True)
    with pytest.raises(IntegrityError, match="exact native receipt columns"):
        list(io.native_records({"parts": [part], "metadata": []}))


@pytest.mark.parametrize("payload", [b"not JSON", b'{"channel":"bbo","channel":"trades"}'])
def test_invalid_or_duplicate_key_json_refuses_before_economic_decode(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    row = list(receipt(1, "bbo", book()))
    row[11] = payload
    with pytest.raises((json.JSONDecodeError, IntegrityError)):
        windows(monkeypatch, [tuple(row)])


def test_unknown_native_trade_side_cannot_become_a_buy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(IntegrityError, match="B/A trade sign"):
        windows(monkeypatch, [receipt(1, "trades", [trade(side="unknown")])])


@pytest.mark.parametrize("fault", ["ordinal", "clock", "payload_hash", "product", "part_hash"])
def test_sealed_native_receipt_refuses_integrity_corruption(tmp_path: Path, fault: str) -> None:
    rows = [receipt(1, "bbo", book()), receipt(2, "trades", [trade()])]
    bad = list(rows[1])
    if fault == "ordinal":
        bad[5] = 3
    elif fault == "clock":
        bad[7] = 0
    elif fault == "payload_hash":
        bad[12] = "0" * 64
    elif fault == "product":
        bad[2] = "ETH-PERP"
    rows[1] = tuple(bad)
    part = native_part(tmp_path, rows)
    if fault == "part_hash":
        part["sha256"] = "0" * 64
    with pytest.raises(IntegrityError):
        list(io.native_records({"parts": [part], "metadata": []}))


def test_native_metadata_is_rechecked_after_parts(tmp_path: Path) -> None:
    part = native_part(tmp_path, [receipt(1, "bbo", book())])
    metadata = tmp_path / "claim.json"
    metadata.write_bytes(b"{}")
    with pytest.raises(IntegrityError, match="identity changed"):
        list(
            io.native_records(
                {
                    "parts": [part],
                    "metadata": [{"path": str(metadata), "bytes": 2, "sha256": "0" * 64}],
                }
            )
        )
