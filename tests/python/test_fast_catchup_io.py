from __future__ import annotations

import hashlib
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from research import fast_catchup_cli as cli
from research import fast_catchup_io as io
from research.btc_flow_io import PrivateOutput
from research.fast_catchup import SECOND
from research.harness.errors import IntegrityError
from research.micro_flow_io import COLUMNS
from research.two_models_io import record

OFFSET = 1_794_600_600_000_000_000


def native(
    at: int,
    *,
    ordinal: int,
    lane: str = "binance",
    kind: str = "flow",
    session: str | None = None,
    ident: int = 1,
    direction: str = "inbound",
    body: dict[str, Any] | None = None,
) -> tuple[Any, ...]:
    session = session or (kind if lane == "binance" else "hl")
    clock = (OFFSET + at) // 1_000_000
    if lane == "binance":
        channel = "usdm_agg_trade" if kind == "flow" else "usdm_book_ticker"
        data: dict[str, Any] = {"s": "BTCUSDT", "st": 1, "E": clock, "T": clock}
        if kind == "flow":
            data.update(
                e="aggTrade",
                a=ident,
                f=ident * 2,
                l=ident * 2 + 1,
                p="100000",
                q=".2",
                nq=".1",
                m=False,
            )
        else:
            data.update(e="bookTicker", u=ident, ps="BTCUSDT", b="99999", B="1", a="100001", A="1")
        default = {
            "stream": "btcusdt@aggTrade" if kind == "flow" else "btcusdt@bookTicker",
            "data": data,
        }
        product = "BTCUSDT-USDS-M-PERPETUAL"
    else:
        channel = "bbo" if kind == "book" else kind
        default = {
            "channel": channel,
            "data": {
                "coin": "BTC",
                "time": clock,
                "bbo": [{"px": "99999", "sz": "1", "n": 1}, {"px": "100001", "sz": "1", "n": 1}],
            },
        }
        product = "BTC-PERP"
    payload = json.dumps(default if body is None else body).encode()
    return (
        1,
        lane,
        product,
        channel,
        session,
        ordinal,
        OFFSET + at,
        at,
        direction,
        "text" if direction == "inbound" else "marker",
        "utf-8-json",
        payload,
        hashlib.sha256(payload).hexdigest(),
    )


def acknowledgement(at: int, ordinal: int) -> tuple[Any, ...]:
    return native(
        at,
        ordinal=ordinal,
        lane="hyperliquid",
        kind="subscriptionResponse",
        body={
            "channel": "subscriptionResponse",
            "data": {"method": "subscribe", "subscription": {"type": "bbo", "coin": "BTC"}},
        },
    )


def local(at: int, ordinal: int, linked: int, *, kind: str = "flow") -> tuple[Any, ...]:
    return native(
        at,
        ordinal=ordinal,
        kind=kind,
        direction="local",
        body={
            "raw_message_ordinal": linked,
            "source_channel": "usdm_agg_trade" if kind == "flow" else "usdm_book_ticker",
        },
    )


def rewrite(row: tuple[Any, ...], change: dict[str, Any]) -> tuple[Any, ...]:
    data = json.loads(row[-2])
    data["data"].update(change)
    payload = json.dumps(data).encode()
    return (*row[:-2], payload, hashlib.sha256(payload).hexdigest())


def tape() -> dict[str, list[tuple[Any, ...]]]:
    # Physically BN flow(2s) precedes book(1s); each WS session is ordered.
    return {
        "binance": [
            native(2 * SECOND, ordinal=1),
            native(SECOND, ordinal=2, kind="book"),
            local(3 * SECOND, 3, 1),
            local(4 * SECOND, 4, 2, kind="book"),
            native(402 * SECOND, ordinal=5, ident=2),
            native(401 * SECOND, ordinal=6, ident=2, kind="book"),
            local(403 * SECOND, 7, 5),
            local(404 * SECOND, 8, 6, kind="book"),
        ],
        "hyperliquid": [
            acknowledgement(0, 1),
            native(SECOND, ordinal=2, lane="hyperliquid", kind="book"),
            native(401 * SECOND, ordinal=3, lane="hyperliquid", kind="book"),
            native(405 * SECOND, ordinal=4, lane="hyperliquid", kind="pong"),
        ],
    }


def fixture_index(tmp_path: Path) -> tuple[dict[str, Any], Path, str]:
    captures = {}
    for lane, rows in tape().items():
        path = tmp_path / (lane + ".parquet")
        connection = duckdb.connect()
        try:
            types = [
                "BIGINT",
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
            ]
            connection.execute(
                "CREATE TABLE receipt ("
                + ",".join(f"{name} {kind}" for name, kind in zip(COLUMNS, types, strict=True))
                + ")"
            )
            connection.executemany(
                "INSERT INTO receipt VALUES (" + ",".join("?" for _ in types) + ")", rows
            )
            connection.execute("COPY receipt TO ? (FORMAT PARQUET)", [str(path)])
        finally:
            connection.close()
        metadata = []
        for n in range(12):
            meta = tmp_path / f"{lane}-metadata-{n}.json"
            meta.write_text("{}")
            metadata.append(record(meta))
        captures[lane] = {"parts": [record(path)], "metadata": metadata}
    receipt = tmp_path / "acceptance.json"
    receipt.write_text(
        json.dumps(
            {
                "status": "PASS_SYNTHETIC_FIXTURE_ONLY",
                "campaign": io.CAMPAIGN,
                "captures": captures,
                "native_required_market_fields_valid": True,
                "financial_labels_or_model_scoring": False,
            }
        )
    )
    begin = (OFFSET // 1_000_000) // 3_600_000 * 3_600_000 - 3_600_000
    finish = begin + 4 * 3_600_000 + 999
    native_funding = tmp_path / "funding.json"
    native_funding.write_text(
        json.dumps(
            [
                {"coin": "BTC", "time": t + 20, "fundingRate": ".00001"}
                for t in range(begin, finish, 3_600_000)
            ]
        )
    )
    index = {
        "schema": "fast-catchup-source-index-v1",
        "study_id": io.STUDY,
        "campaign": io.CAMPAIGN,
        "plan_sha256": io.PLAN_SHA,
        "data_status": "exposed_retrospective_no_fresh_oos",
        "source_acceptance": record(receipt),
        "source_acceptance_passed": True,
        "captures": captures,
        "clock_offset_ns": OFFSET,
        "precision": {"sz_decimals": 5, "mode": "explicit_epoch_assumption"},
        "funding": {"source": record(native_funding), "start_ms": begin, "end_ms": finish},
    }
    path, sha = write_index(tmp_path, index)
    return index, path, sha


def write_index(tmp_path: Path, index: dict[str, Any]) -> tuple[Path, str]:
    path = tmp_path / "index.json"
    raw = json.dumps(index, sort_keys=True).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_native_flow_uses_full_q_and_exact_m_not_nq_or_local_derived_row() -> None:
    decoder = io.Decoder()
    raw = native(SECOND, ordinal=1)
    event = decoder.decode(raw)
    assert event is not None and event.signed_notional == 20000
    assert decoder.decode(local(2 * SECOND, 2, 1)) is None
    sell = decoder.decode(rewrite(native(3 * SECOND, ordinal=3, ident=2), {"m": True}))
    assert sell is not None and sell.signed_notional == -20000
    historical = json.loads(native(4 * SECOND, ordinal=4, ident=3)[-2])
    del historical["data"]["nq"]
    assert decoder.decode(native(4 * SECOND, ordinal=4, body=historical)) is not None


@pytest.mark.parametrize(
    "change",
    [
        {"m": "false"},
        {"q": "NaN"},
        {"q": "0"},
        {"st": 2},
        {"s": "ETHUSDT"},
        {"T": "123"},
        {"nq": ".3"},
    ],
)
def test_native_wrong_fields_fail_closed(change: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        io.Decoder().decode(rewrite(native(SECOND, ordinal=1), change))


def test_equal_native_id_deduplicates_without_refresh_and_conflict_or_regression_fails() -> None:
    decoder = io.Decoder()
    first = native(SECOND, ordinal=1, ident=5)
    assert decoder.decode(first) is not None
    assert decoder.decode((*first[:5], 2, *first[6:])) is None
    with pytest.raises(IntegrityError, match="conflicting"):
        decoder.decode(rewrite(first, {"q": ".3"}))
    with pytest.raises(IntegrityError, match="regression"):
        decoder.decode(native(2 * SECOND, ordinal=3, ident=4))


def test_hl_requires_ack_and_preserves_observed_full_depth() -> None:
    decoder = io.Decoder()
    with pytest.raises(IntegrityError, match="acknowledgement"):
        decoder.decode(native(SECOND, ordinal=1, lane="hyperliquid", kind="book"))
    decoder.decode(acknowledgement(0, 1))
    event = decoder.decode(native(SECOND, ordinal=2, lane="hyperliquid", kind="book"))
    assert event is not None and len(event.asks) == 1 and event.asks[0].size == 1


def test_sealed_parquet_retains_physical_order_and_payload_hash(tmp_path: Path) -> None:
    index, _, _ = fixture_index(tmp_path)
    got = list(io.records(index["captures"]["binance"], "binance"))
    assert [r[5] for r in got] == list(range(1, 9))
    assert got[0][7] > got[1][7]  # Never globally repaired.
    part = index["captures"]["binance"]["parts"][0]
    part["sha256"] = "0" * 64
    with pytest.raises(IntegrityError, match="identity"):
        list(io.records(index["captures"]["binance"], "binance"))


def test_raw_merge_and_online_witness_release_keep_original_clocks_and_unwitnessed_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rows = tape()
    monkeypatch.setattr(io, "records", lambda capture, lane: iter(rows[lane]))
    index = {"captures": {lane: {"metadata": []} for lane in rows}, "clock_offset_ns": OFFSET}
    with PrivateOutput(tmp_path / "projection") as output:
        projection = io.project(index, output)
        io.verify_projection(projection)
        raw = list(io.merged(output, projection, "RAW"))
        witness = list(io.merged(output, projection, "WITNESS"))
        assert all(a.available_ns <= b.available_ns for a, b in pairwise(raw))
        early_flow = next(e for e in witness if e.kind == "flow")
        assert early_flow.raw_ns == 2 * SECOND and early_flow.available_ns == 3 * SECOND
        assert early_flow.witness == "binance:3"
        early_external_book = next(e for e in witness if e.kind == "bn_book")
        assert (
            early_external_book.raw_ns == SECOND and early_external_book.available_ns == 4 * SECOND
        )
        hl = [e for e in witness if e.kind == "hl_book"]
        assert hl[0].raw_ns == SECOND and hl[0].available_ns == 401 * SECOND
        assert hl[-1].available_ns == 405 * SECOND
        assert not any(e.kind == "noop" and e.raw_ns == 405 * SECOND for e in witness)
        assert projection.source_counts["hyperliquid:unwitnessed_tail_events"] == 1
        assert len([e for e in raw if e.kind == "flow"]) == 2


def test_projection_linkage_wrong_profile_and_resource_limit_fail_without_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rows = tape()
    rows["binance"][2] = local(3 * SECOND, 3, 1, kind="book")
    monkeypatch.setattr(io, "records", lambda capture, lane: iter(rows[lane]))
    index = {"captures": {"binance": {"metadata": []}}, "clock_offset_ns": OFFSET}
    with PrivateOutput(tmp_path / "bad-profile") as output:
        with pytest.raises(IntegrityError, match="matching online"):
            io.project(index, output)
    rows = tape()
    monkeypatch.setattr(io, "MAX_ROWS", 2)
    with PrivateOutput(tmp_path / "too-many") as output:
        with pytest.raises(IntegrityError, match="PHYSICAL"):
            io.project(index, output)


def test_input_sha_flags_and_complete_selection_are_not_interchangeable(tmp_path: Path) -> None:
    index, path, sha = fixture_index(tmp_path)
    assert io.load(path, sha) == index
    with pytest.raises(IntegrityError, match="exact qualified"):
        io.load(path, "0" * 64)
    index["captures"]["hyperliquid"]["metadata"].reverse()
    path, sha = write_index(tmp_path, index)
    with pytest.raises(IntegrityError, match="exact parts"):
        io.load(path, sha)
    index["campaign"] = "pilot"
    path, sha = write_index(tmp_path, index)
    with pytest.raises(IntegrityError, match="selection"):
        io.load(path, sha)


def test_funding_requires_native_complete_padded_hour_census(tmp_path: Path) -> None:
    index, _, _ = fixture_index(tmp_path)
    assert len(io.funding(index, 2 * SECOND, 401 * SECOND)) == 5
    path = Path(index["funding"]["source"]["path"])
    rows = json.loads(path.read_bytes())
    path.write_text(json.dumps(rows[1:]))
    index["funding"]["source"] = record(path)
    with pytest.raises(IntegrityError, match="every full"):
        io.funding(index, 2 * SECOND, 401 * SECOND)


def test_unsafe_mode_refuses_before_identity_input_or_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli, "identity", lambda commit: pytest.fail("mode guard must precede source I/O")
    )
    with pytest.raises(UnsafeTradingModeError):
        cli.execute(
            mode="LIVE",
            source_index=tmp_path / "absent",
            expected_sha="0" * 64,
            source_commit="0" * 40,
            destination=tmp_path / "never-created",
        )
    assert not (tmp_path / "never-created").exists()


def test_complete_synthetic_native_cli_writes_exact_four_cases_and_create_only_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    index, path, sha = fixture_index(tmp_path)
    monkeypatch.setattr(cli, "identity", lambda commit: {"synthetic_fixture": "not-real-source"})
    result = cli.execute(
        mode=None,
        source_index=path,
        expected_sha=sha,
        source_commit="0" * 40,
        destination=tmp_path / "result",
    )
    assert result["status"] == "COMPLETE_ALL_FOUR_CONDITIONAL_CHAINS"
    assert [case["case"] for case in result["cases"]] == [
        "RAW-base-C100",
        "RAW-stress-C100",
        "WITNESS-base-C100",
        "WITNESS-stress-C100",
    ]
    assert all(case["status"] == "COMPLETE_CONDITIONAL_RESEARCH" for case in result["cases"])
    assert all(case["initial_equity"] == 10000 for case in result["cases"])
    assert result["capacity_500_2500"] == "NOT_RUN_UNREGISTERED"
    assert (tmp_path / "result/result.json").is_file()
    with pytest.raises(FileExistsError):
        cli.execute(
            mode="PAPER",
            source_index=path,
            expected_sha=sha,
            source_commit="0" * 40,
            destination=tmp_path / "result",
        )
    assert io.load(path, sha) == index


def test_protocol_initial_pin_is_unchanged_and_amendment_is_separate() -> None:
    assert hashlib.sha256(io.PLAN.read_bytes()).hexdigest() == io.PLAN_SHA
    assert "20,000,000 physical" in io.AMENDMENT.read_text()
