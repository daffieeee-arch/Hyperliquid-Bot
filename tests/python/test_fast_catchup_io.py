from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from research import fast_catchup_cli as cli
from research import fast_catchup_io as io
from research.btc_flow_io import PrivateOutput
from research.fast_catchup import SECOND, Event, Intent, Level, Replay
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
    row = native(
        at,
        ordinal=ordinal,
        kind=kind,
        direction="local",
        body={
            "raw_message_ordinal": linked,
            "source_channel": "usdm_agg_trade" if kind == "flow" else "usdm_book_ticker",
        },
    )

    return (*row[:3], "normalized_usdm_context", *row[4:])


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


@pytest.mark.parametrize(
    "bad", ["unknown", "missing", "repeated", "session", "product", "channel", "profile", "boolean"]
)
def test_required_local_normalizer_requires_exact_existing_raw_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bad: str,
) -> None:
    rows = tape()
    marker = list(rows["binance"][2])
    body = json.loads(marker[-2])
    if bad == "unknown":
        body["raw_message_ordinal"] = 999999
    elif bad == "missing":
        del body["raw_message_ordinal"]
    elif bad == "repeated":
        marker = list(local(4 * SECOND, 4, 1))
        body = json.loads(marker[-2])
    elif bad == "session":
        marker[4] = "other"
    elif bad == "product":
        marker[2] = "BTCUSDT-SPOT"
    elif bad == "channel":
        marker[3] = "normalized_spot_bbo"
    elif bad == "profile":
        body["source_channel"] = "usdm_book_ticker"
    else:
        body["raw_message_ordinal"] = True
    marker[-2] = json.dumps(body).encode()
    marker[-1] = hashlib.sha256(marker[-2]).hexdigest()
    rows["binance"][3 if bad == "repeated" else 2] = tuple(marker)
    monkeypatch.setattr(io, "records", lambda capture, lane: iter(rows[lane]))
    index = {"captures": {lane: {"metadata": []} for lane in rows}, "clock_offset_ns": OFFSET}
    with PrivateOutput(tmp_path / "bad-link") as output:
        with pytest.raises(IntegrityError, match="LOCAL"):
            io.project(index, output)


@pytest.mark.parametrize("disposition", ["duplicate", "excluded"])
def test_legitimate_duplicate_and_excluded_raw_normalizers_are_explicit_not_extra_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    disposition: str,
) -> None:
    rows = tape()
    initial = rows["binance"][0]
    if disposition == "duplicate":
        extra = (*initial[:5], 3, *initial[6:])
        witness = local(3 * SECOND + 1, 5, 3)
    else:
        excluded = list(native(2 * SECOND, ordinal=3, kind="book", session="spot"))
        excluded[2:4] = ["BTCUSDT-SPOT", "spot_book_ticker"]
        extra = tuple(excluded)
        marker = list(local(3 * SECOND + 1, 5, 3))
        marker[2:5] = ["BTCUSDT-SPOT", "normalized_spot_bbo", "spot"]
        body = {"raw_message_ordinal": 3, "source_channel": "spot_book_ticker"}
        marker[-2] = json.dumps(body).encode()
        marker[-1] = hashlib.sha256(marker[-2]).hexdigest()
        witness = tuple(marker)
    rows["binance"] = [
        initial,
        rows["binance"][1],
        extra,
        local(3 * SECOND, 4, 1),
        witness,
        local(4 * SECOND, 6, 2, kind="book"),
        native(402 * SECOND, ordinal=7, ident=2),
        native(401 * SECOND, ordinal=8, ident=2, kind="book"),
        local(403 * SECOND, 9, 7),
        local(404 * SECOND, 10, 8, kind="book"),
    ]
    monkeypatch.setattr(io, "records", lambda capture, lane: iter(rows[lane]))
    index = {"captures": {lane: {"metadata": []} for lane in rows}, "clock_offset_ns": OFFSET}
    with PrivateOutput(tmp_path / "classified-links") as output:
        projection = io.project(index, output)
        assert projection.source_counts["binance:linked_" + disposition] == 1
        assert projection.source_counts["binance:unwitnessed_tail_events"] == 0
        assert len([e for e in io.merged(output, projection, "WITNESS") if e.kind == "flow"]) == 2


def test_real_raw_tail_without_any_normalizer_is_unavailable_not_unknown_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rows = tape()
    rows["binance"].append(native(406 * SECOND, ordinal=9, ident=3))
    monkeypatch.setattr(io, "records", lambda capture, lane: iter(rows[lane]))
    index = {"captures": {lane: {"metadata": []} for lane in rows}, "clock_offset_ns": OFFSET}
    with PrivateOutput(tmp_path / "unwitnessed-tail") as output:
        projection = io.project(index, output)
        assert projection.source_counts["binance:unwitnessed_tail_events"] == 1
        assert len([e for e in io.merged(output, projection, "WITNESS") if e.kind == "flow"]) == 2


def test_cli_input_error_retains_current_owned_case_audit_and_aborts_without_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, path, sha = fixture_index(tmp_path)
    monkeypatch.setattr(cli, "identity", lambda commit: {"synthetic_fixture": "only"})

    class Started(Replay):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.pending = Intent(
                "entry",
                1,
                self.start,
                self.start + self.cost.latency,
                self.start + self.cost.latency + SECOND,
                Decimal(".00099"),
                Decimal("101000"),
                Decimal("101000"),
                Decimal("99000"),
                Decimal("101000"),
                "fixture",
                "prior",
            )

    attempts = 0

    def broken(output: PrivateOutput, projection: io.Projection, availability: str) -> Any:
        nonlocal attempts
        attempts += 1
        at = projection.start + SECOND
        for when in (at, at + 1):
            yield Event(
                "hyperliquid",
                "hl",
                when,
                when,
                OFFSET + when,
                (OFFSET + when) // 1_000_000,
                when,
                str(when),
                "hl_book",
                (Level(Decimal("99999"), Decimal(1)),),
                (Level(Decimal("100001"), Decimal(1)),),
            )
        raise OSError("synthetic projected input failure")

    monkeypatch.setattr(cli, "Replay", Started)
    monkeypatch.setattr(cli, "merged", broken)
    with pytest.raises(IntegrityError, match="synthetic projected"):
        cli.execute(
            mode="PAPER",
            source_index=path,
            expected_sha=sha,
            source_commit="0" * 40,
            destination=tmp_path / "failed-owned",
        )
    failure = json.loads((tmp_path / "failed-owned/failure.json").read_bytes())
    assert attempts == 1 and len(failure["cases"]) == 1
    case = failure["cases"][0]
    assert case["status"] == "FAILED_NULLACCOUNT" and case["fatal_run_error"]
    assert case["final_equity"] is None and case["net_return"] is None
    assert Decimal(case["remaining_quantity"]) != 0
    assert Decimal(case["last_observed_state"]["fees_usdc"]) > 0
    audit = case["audit"]["RAW-base-C100.jsonl"]
    raw = Path(audit["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == audit["sha256"]
    assert json.loads(raw.splitlines()[-1])["kind"] == "failure"


@pytest.mark.parametrize("fail_indices", [{0}, {0, 1, 2, 3}])
def test_top_level_completion_is_derived_from_every_case_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fail_indices: set[int],
) -> None:
    _, path, sha = fixture_index(tmp_path)
    monkeypatch.setattr(cli, "identity", lambda commit: {"synthetic_fixture": "only"})
    attempts = 0

    class ConditionalFailure(Replay):
        def run(self, events: Any) -> dict[str, Any]:
            nonlocal attempts
            summary = super().run(events)
            if attempts in fail_indices:
                summary.update(
                    status="FAILED_NULLACCOUNT",
                    failure="synthetic conditional failure",
                    final_equity=None,
                    net_return=None,
                )
            attempts += 1
            return summary

    monkeypatch.setattr(cli, "Replay", ConditionalFailure)
    result = cli.execute(
        mode="PAPER",
        source_index=path,
        expected_sha=sha,
        source_commit="0" * 40,
        destination=tmp_path / "failed-status",
    )
    assert result["status"] == "FAILED_ONE_OR_MORE_CONDITIONAL_CHAINS"
    assert result["attempted_case_count"] == 4 and len(result["cases"]) == 4
    assert len(result["failed_cases"]) == len(fail_indices)


@pytest.mark.parametrize(
    "status,expected",
    [("FAILED_ONE_OR_MORE_CONDITIONAL_CHAINS", 1), ("COMPLETE_ALL_FOUR_CONDITIONAL_CHAINS", 0)],
)
def test_command_exit_status_does_not_make_failed_chains_successful(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected: int,
) -> None:
    monkeypatch.setattr(cli, "execute", lambda **kwargs: {"status": status})
    assert (
        cli.main(
            [
                "--source-index",
                "/synthetic",
                "--expected-index-sha256",
                "0" * 64,
                "--source-commit",
                "0" * 40,
                "--output",
                "/synthetic-output",
            ]
        )
        == expected
    )
