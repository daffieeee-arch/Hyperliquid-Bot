"""Synthetic-only economic, point-in-time and harness integration regressions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import duckdb
import pytest

from research.btc_flow import (
    ENTRY_DELAY,
    LATENCY,
    MINUTE,
    SECOND,
    Flow,
    FlowSource,
    Quote,
    SpotTrade,
    aggregate_flow,
    quote_fill,
    settlement_safe,
)
from research.btc_flow_cli import (
    DAY,
    checked_paths,
    exact_part_path,
    load_source,
    receipt_freshness,
    run,
    study_spec,
    validate_manifest,
)
from research.harness.errors import IntegrityError
from research.harness.evaluate import TradeSeries, _concat_series, decide_source
from research.harness.spec import ConfigSpec, CostSpec, HypothesisSpec

SPEC = Path(__file__).resolve().parents[2] / "docs/experiments/exp_btc_spot_flow.spec.yaml"
D = Decimal
CONFIG = ConfigSpec("test-h1", 0.2, 1)


def spec() -> HypothesisSpec:
    return study_spec(SPEC)[1]


def quote(when: int, ordinal: int, mid: str = "1000", session: str = "hl") -> Quote:
    return Quote(when, session, ordinal, D(mid) - 1, D(mid) + 1, D(1), D(1))


def source(*, gaps: tuple[int, ...] = (), quotes: tuple[Quote, ...] | None = None) -> FlowSource:
    if quotes is None:
        quotes = tuple(
            quote(m * MINUTE + offset, m * 4 + i, str(1000 + m))
            for m in range(41)
            for i, offset in enumerate((0, LATENCY, 2 * LATENCY, 3 * LATENCY))
        )
    flow = {m: Flow(D(6), D(10), frozenset({"bn"}), 2) for m in range(40)}
    return FlowSource(spec(), 0, 40 * MINUTE, quotes, flow, gaps)


def test_aggressor_notional_aggregation_and_half_open_receipt_clock() -> None:
    flow = aggregate_flow(
        [
            SpotTrade(0, "bn", 1, D(10), D(3), False),
            SpotTrade(MINUTE - 1, "bn", 2, D(20), D(1), True),
            SpotTrade(MINUTE, "bn", 3, D(100), D(1), False),
        ]
    )
    assert flow[0].total_notional == 50 and flow[0].signed_notional == 10
    assert flow[0].imbalance == D("0.2") and flow[1].trades == 1
    with pytest.raises(IntegrityError, match="duplicate"):
        aggregate_flow([SpotTrade(1, "bn", 1, D(10), D(1), False)] * 2)


@pytest.mark.parametrize(
    "side,exit_mid,expected_fee", [(1, "1010", D("0.0009045")), (-1, "990", D("0.0008955"))]
)
def test_two_actual_quote_sides_and_both_notional_fees(
    side: int, exit_mid: str, expected_fee: Decimal
) -> None:
    fill = quote_fill(
        0,
        D(1000),
        quote(ENTRY_DELAY, 1),
        quote(ENTRY_DELAY + MINUTE + LATENCY, 2, exit_mid),
        side,
        4.5,
        0,
    )
    assert fill is not None and fill.quantity == D("0.1")
    assert fill.gross == pytest.approx(0.01)
    assert fill.execution_cost == pytest.approx(float(D("0.002") + expected_fee))
    series = TradeSeries((fill.gross,), (0.0,), (0.0,), (fill.weight,), (fill.execution_cost,))
    costs = spec().costs
    assert series.net(costs, 1)[0] == pytest.approx(0.01 - float(D("0.002") + expected_fee))
    assert series.net(costs, 2)[0] == pytest.approx(0.01 - 2 * fill.execution_cost)


def test_slippage_and_adverse_price_grid_never_improve_fill() -> None:
    entry, exit_quote = quote(ENTRY_DELAY, 1), quote(ENTRY_DELAY + MINUTE + LATENCY, 2, "1010")
    zero = quote_fill(0, D(1000), entry, exit_quote, 1, 0, 0)
    slipped = quote_fill(0, D(1000), entry, exit_quote, 1, 0, 1)
    assert zero is not None and slipped is not None
    assert slipped.execution_cost > zero.execution_cost


def test_partial_entry_capacity_is_lot_rounded_and_exit_is_not_fabricated() -> None:
    entry = replace(quote(ENTRY_DELAY, 1), ask_size=D("0.025678"))
    exit_quote = quote(ENTRY_DELAY + MINUTE + LATENCY, 2, "1010")
    fill = quote_fill(0, D(1000), entry, exit_quote, 1, 0, 0)
    assert (
        fill is not None and fill.quantity == D("0.02567") and fill.weight == pytest.approx(0.2567)
    )
    assert quote_fill(0, D(1000), replace(entry, ask_size=D(".001")), exit_quote, 1, 0, 0) is None
    with pytest.raises(IntegrityError, match="insufficient touch"):
        quote_fill(0, D(1000), entry, replace(exit_quote, bid_size=D(".001")), 1, 0, 0)


def test_future_quote_and_future_flow_do_not_change_decision() -> None:
    original = source()
    when = 5 * MINUTE
    expected = original.signal(when)
    assert expected is not None and expected[:2] == (1, D(".6"))
    quotes = tuple(
        replace(q, bid=D(2000), ask=D(2002)) if q.received_ns > when else q for q in original.quotes
    )
    altered = FlowSource(
        spec(),
        0,
        40 * MINUTE,
        quotes,
        {**original.flow, 5: Flow(D(-9), D(10), frozenset({"bn"}), 1)},
    )
    assert altered.signal(when) == expected


def test_actual_entry_after_scheduling_latency_and_nonoverlap() -> None:
    fills = source().fills(CONFIG, 5, 20)
    assert fills and fills[0].entry.received_ns == 5 * MINUTE + ENTRY_DELAY
    assert all(f.exit.received_ns >= f.entry.received_ns + MINUTE + LATENCY for f in fills)
    assert all(b.decision_ns >= a.exit.received_ns for a, b in pairwise(fills))
    assert all(f.exit.received_ns < 20 * MINUTE for f in fills)


@pytest.mark.parametrize("gap", [6 * MINUTE, 6 * MINUTE + SECOND])
def test_unknown_gap_during_entry_or_holding_fails_whole_window(gap: int) -> None:
    with pytest.raises(IntegrityError, match="unknown"):
        source(gaps=(gap,)).window(CONFIG, 5, 10)


def test_missing_exit_does_not_silently_drop_a_trade() -> None:
    original = source()
    # Quotes stop after the first entry; the research window remains unchanged.
    truncated = tuple(q for q in original.quotes if q.received_ns <= 6 * MINUTE + LATENCY)
    with pytest.raises(IntegrityError, match="unknown fill"):
        source(quotes=truncated).window(CONFIG, 5, 10)


def test_reconnect_rejects_future_exit_even_if_session_later_reappears() -> None:
    quotes = tuple(
        replace(q, session="reconnected") if q.received_ns == 7 * MINUTE else q
        for q in source().quotes
    )
    with pytest.raises(IntegrityError, match="unknown"):
        source(quotes=quotes).window(CONFIG, 5, 10)


def test_feature_gap_and_stale_quote_are_known_predecision_ineligibility() -> None:
    assert source(gaps=(4 * MINUTE + 1,)).signal(5 * MINUTE) is None
    quotes = tuple(
        q for q in source().quotes if not 4 * MINUTE + LATENCY < q.received_ns <= 5 * MINUTE
    )
    assert source(quotes=quotes).signal(5 * MINUTE) is None


def test_settlement_guard_includes_all_waits_and_full_minute_latency() -> None:
    assert settlement_safe(52 * MINUTE, 5)
    assert not settlement_safe(53 * MINUTE, 5)
    assert not settlement_safe(58 * MINUTE, 1)
    assert settlement_safe(60 * MINUTE, 1)


def test_fold_censoring_does_not_consume_future_quotes() -> None:
    fills = source().fills(CONFIG, 5, 8)
    assert fills and all(f.exit.received_ns < 8 * MINUTE for f in fills)
    early = source().window(CONFIG, 5, 8)
    quotes = tuple(
        replace(q, bid=D(100), ask=D(102)) if q.received_ns >= 8 * MINUTE else q
        for q in source().quotes
    )
    assert source(quotes=quotes).window(CONFIG, 5, 8) == early


def test_quote_vector_concat_and_original_flat_bps_behavior() -> None:
    flat = TradeSeries((0.01,), (0.0,), (0.0,), (1.0,))
    costs = CostSpec(4, 1, 1, 1, False)
    assert flat.net(costs, 1) == pytest.approx((0.0088,))
    a = TradeSeries((0.01,), (0.0,), (0.0,), (1.0,), (0.002,))
    b = TradeSeries((0.02,), (0.0,), (0.0,), (0.5,), (0.003,))
    pooled = _concat_series((a, b))
    assert pooled.execution_costs == (0.002, 0.003) and pooled.net(costs, 2) == pytest.approx(
        (0.006, 0.014)
    )
    with pytest.raises(IntegrityError, match="mix"):
        _concat_series((a, flat))
    with pytest.raises(IntegrityError, match="vector"):
        replace(a, execution_costs=()).net(costs, 1)


def test_existing_harness_calls_holdout_only_after_validation_selection() -> None:
    s = source()
    s.spec = replace(
        s.spec,
        split=replace(s.spec.split, train_bars=5, test_bars=5, holdout_bars=10),
        sample=replace(
            s.spec.sample, min_trades_validation=100, min_trades_holdout=30, min_folds=3
        ),
    )
    calls = []
    original = s.window

    def observed(config: ConfigSpec, start: int, end: int) -> TradeSeries:
        calls.append((start, end))
        assert end <= 30
        return original(config, start, end)

    s.window = observed  # type: ignore[method-assign]
    result = decide_source(s)
    assert result.label == "not_enough_data" and result.holdout_net is None and calls


def manifest(start: int, end: int, phase: str = "exploratory") -> dict[str, object]:
    return {
        "format": 1,
        "phase": phase,
        "start_ns": start,
        "end_ns": end,
        "preregistration_commit": None,
        "preregistration_merged_ns": None,
        "parts": [],
    }


def test_prospective_window_is_fresh_fixed_calendar_not_historic_holdout() -> None:
    m = manifest(3 * DAY, 6 * DAY, "prospective")
    m.update(preregistration_commit="a" * 40, preregistration_merged_ns=DAY + 1)
    assert validate_manifest(m) == (3 * DAY, 6 * DAY, "prospective")
    with pytest.raises(IntegrityError, match="fresh"):
        validate_manifest({**m, "start_ns": 2 * DAY, "end_ns": 5 * DAY})
    with pytest.raises(IntegrityError, match="not fresh"):
        validate_manifest({**m, "phase": "exploratory"})


def test_insufficient_metadata_span_never_reads_financial_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research.btc_flow_cli as cli

    path = tmp_path / "inputs.json"
    raw = json.dumps(manifest(0, DAY)).encode()
    path.write_bytes(raw)

    def forbidden(*_args: object) -> None:
        raise AssertionError("financial read forbidden")

    monkeypatch.setattr(cli, "checked_paths", forbidden)
    result = run(SPEC, path, hashlib.sha256(raw).hexdigest(), tmp_path / "out")
    assert result["status"] == "not_enough_data" and result["financial_frames_read"] is False


def test_nonpaper_fails_before_input_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(Exception, match="PAPER"):
        run(SPEC, tmp_path / "absent", "0" * 64, tmp_path / "out")


def test_original_raw_frame_parser_ignores_normalization_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "part.parquet"
    connection = duckdb.connect()
    rows = []
    for ordinal, when in enumerate((0, MINUTE, 2 * MINUTE)):
        payload = json.dumps(
            {
                "channel": "bbo",
                "data": {
                    "coin": "BTC",
                    "time": when // 1_000_000,
                    "bbo": [{"px": "999", "sz": "1"}, {"px": "1001", "sz": "1"}],
                },
            }
        ).encode()
        rows.append(
            (
                1,
                "hyperliquid",
                "BTC-PERP",
                "bbo",
                "hl",
                ordinal,
                when,
                when,
                "inbound",
                payload,
                hashlib.sha256(payload).hexdigest(),
            )
        )
    payload = json.dumps(
        {
            "stream": "btcusdt@trade",
            "data": {
                "e": "trade",
                "E": (MINUTE - 1) // 1000,
                "s": "BTCUSDT",
                "t": 1,
                "p": "1000",
                "q": "1",
                "T": (MINUTE - 1) // 1000,
                "m": False,
                "M": False,
            },
        }
    ).encode()
    rows.append(
        (
            1,
            "binance",
            "BTCUSDT-SPOT",
            "spot_trade",
            "bn",
            1,
            MINUTE - 1,
            MINUTE - 1,
            "inbound",
            payload,
            hashlib.sha256(payload).hexdigest(),
        )
    )
    rows.append(
        (
            1,
            "binance",
            "BTCUSDT-SPOT",
            "normalized_spot_trade",
            "bn",
            2,
            MINUTE,
            MINUTE,
            "local",
            b"{}",
            hashlib.sha256(b"{}").hexdigest(),
        )
    )
    other_coin = json.dumps(
        {"channel": "bbo", "data": {"coin": "ETH", "bbo": "not-decoded"}}
    ).encode()
    rows.append(
        (
            1,
            "hyperliquid",
            "BTC-PERP",
            "bbo",
            "hl",
            3,
            2 * MINUTE + 1,
            2 * MINUTE + 1,
            "inbound",
            other_coin,
            hashlib.sha256(other_coin).hexdigest(),
        )
    )
    connection.execute(
        "CREATE TABLE frames(schema_version INTEGER,venue VARCHAR,product VARCHAR,"
        "channel VARCHAR,session_id VARCHAR,message_ordinal BIGINT,received_utc_ns BIGINT,"
        "received_monotonic_ns BIGINT,direction VARCHAR,payload_bytes BLOB,payload_sha256 VARCHAR)"
    )
    connection.execute("BEGIN")
    connection.executemany("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.execute("COMMIT")
    connection.execute("COPY frames TO ? (FORMAT PARQUET)", [str(path)])
    connection.close()
    parsed = load_source(spec(), 0, 3 * MINUTE, [str(path)])
    assert len(parsed.quotes) == 3 and parsed.flow[0].trades == 1 and parsed.flow[0].imbalance == 1


def test_event_clock_freshness_is_not_a_strategy_availability_clock() -> None:
    receipt_freshness(10 * SECOND, 5 * SECOND)
    receipt_freshness(10 * SECOND, 11 * SECOND)
    for event in (5 * SECOND - 1, 11 * SECOND + 1):
        with pytest.raises(IntegrityError, match="stale or future"):
            receipt_freshness(10 * SECOND, event)


def test_mismatched_fold_vectors_cannot_cancel_each_others_length_errors() -> None:
    a = TradeSeries((0.01,), (0.0,), (0.0,), (1.0,), (0.001, 0.002))
    b = TradeSeries((0.02,), (0.0,), (0.0,), (1.0,), ())
    with pytest.raises(IntegrityError, match="within a fold"):
        _concat_series((a, b))


def test_complete_synthetic_economic_runner_is_not_a_noise_only_scaffold(tmp_path: Path) -> None:
    """Original raw frames -> fixed economic family -> real harness -> locked result."""
    part = tmp_path / "economic-frames.parquet"
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE frames(schema_version INTEGER,venue VARCHAR,product VARCHAR,"
        "channel VARCHAR,session_id VARCHAR,message_ordinal BIGINT,received_utc_ns BIGINT,"
        "received_monotonic_ns BIGINT,direction VARCHAR,payload_bytes BLOB,payload_sha256 VARCHAR)"
    )
    rows = []
    for minute in range(4320):
        mid = D(1000) + D(minute) / 10
        for index in range(4):
            when = minute * MINUTE + index * LATENCY
            payload = json.dumps(
                {
                    "channel": "bbo",
                    "data": {
                        "coin": "BTC",
                        "time": when // 1_000_000,
                        "bbo": [{"px": str(mid - 1), "sz": "1"}, {"px": str(mid + 1), "sz": "1"}],
                    },
                }
            ).encode()
            rows.append(
                (
                    1,
                    "hyperliquid",
                    "BTC-PERP",
                    "bbo",
                    "hl",
                    minute * 4 + index,
                    when,
                    when,
                    "inbound",
                    payload,
                    hashlib.sha256(payload).hexdigest(),
                )
            )
        when = minute * MINUTE + 30 * SECOND
        payload = json.dumps(
            {
                "stream": "btcusdt@trade",
                "data": {
                    "e": "trade",
                    "E": when // 1000,
                    "s": "BTCUSDT",
                    "t": minute,
                    "p": "1000",
                    "q": "1",
                    "T": when // 1000,
                    "m": False,
                    "M": False,
                },
            }
        ).encode()
        rows.append(
            (
                1,
                "binance",
                "BTCUSDT-SPOT",
                "spot_trade",
                "bn",
                minute,
                when,
                when,
                "inbound",
                payload,
                hashlib.sha256(payload).hexdigest(),
            )
        )
    connection.execute("BEGIN")
    connection.executemany("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.execute("COMMIT")
    connection.execute("COPY frames TO ? (FORMAT PARQUET)", [str(part)])
    connection.close()
    inputs = manifest(0, 3 * DAY)
    inputs["parts"] = [{"path": str(part), "sha256": hashlib.sha256(part.read_bytes()).hexdigest()}]
    raw = json.dumps(inputs).encode()
    path = tmp_path / "inputs.json"
    path.write_bytes(raw)
    result = run(SPEC, path, hashlib.sha256(raw).hexdigest(), tmp_path / "out")
    assert result["status"] == "completed" and result["exploratory_only"] is True
    assert result["promotion_decision"] == "forbidden"
    decision = result["decision"]
    assert isinstance(decision, dict) and decision["label"] in (
        "exploratory_no_edge",
        "exploratory_interesting_but_fragile",
    )
    assert decision["promotion_decision"] == "forbidden"
    assert all(score["validation_net"]["1.0"]["mean_return"] < 0 for score in decision["scores"])
    assert (tmp_path / "out/spec.yaml.lock.json").is_file()
    assert (
        json.loads((tmp_path / "out/result.json").read_bytes())["source_manifest_sha256"]
        == hashlib.sha256(raw).hexdigest()
    )


@pytest.mark.parametrize(
    "name", ["part*.parquet", "part?.parquet", "part[12].parquet", "part{1,2}.parquet"]
)
def test_closed_part_paths_never_expand_duckdb_globs(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"literal file exists but must not be expanded")
    with pytest.raises(IntegrityError, match="globs refused"):
        exact_part_path(str(path))
    with pytest.raises(IntegrityError, match="globs refused"):
        checked_paths(
            {
                "parts": [
                    {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                ]
            }
        )
    with pytest.raises(IntegrityError, match="globs refused"):
        load_source(spec(), 0, 2 * MINUTE, [str(path)])


@pytest.mark.parametrize(
    "event",
    [
        "sequence_error",
        "truncation_error",
        "schema_error",
        "snapshot_error",
        None,
        {"malformed": True},
        "unreviewed_event",
    ],
)
def test_quality_errors_are_never_silently_ignored(tmp_path: Path, event: object) -> None:
    part = tmp_path / "quality.parquet"
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE frames(schema_version INTEGER,venue VARCHAR,product VARCHAR,"
        "channel VARCHAR,session_id VARCHAR,message_ordinal BIGINT,received_utc_ns BIGINT,"
        "received_monotonic_ns BIGINT,direction VARCHAR,payload_bytes BLOB,payload_sha256 VARCHAR)"
    )
    frames = []
    for ordinal, when in ((0, 0), (2, MINUTE)):
        payload = json.dumps(
            {
                "channel": "bbo",
                "data": {
                    "coin": "BTC",
                    "time": when // 1_000_000,
                    "bbo": [
                        {"px": str(999 + ordinal), "sz": "1"},
                        {"px": str(1001 + ordinal), "sz": "1"},
                    ],
                },
            }
        ).encode()
        frames.append(
            (
                1,
                "hyperliquid",
                "BTC-PERP",
                "bbo",
                "hl",
                ordinal,
                when,
                when,
                "inbound",
                payload,
                hashlib.sha256(payload).hexdigest(),
            )
        )
    payload = json.dumps({"event": event}).encode()
    frames.append(
        (
            1,
            "hyperliquid",
            "BTC-PERP",
            "data_quality",
            "hl",
            1,
            10 * SECOND,
            10 * SECOND,
            "local",
            payload,
            hashlib.sha256(payload).hexdigest(),
        )
    )
    connection.executemany("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?)", frames)
    connection.execute("COPY frames TO ? (FORMAT PARQUET)", [str(part)])
    connection.close()
    if not isinstance(event, str) or event == "unreviewed_event":
        with pytest.raises(IntegrityError, match="quality marker"):
            load_source(spec(), 0, 2 * MINUTE, [str(part)])
    else:
        parsed = load_source(spec(), 0, 2 * MINUTE, [str(part)])
        assert parsed.hl_gaps == (10 * SECOND,)
        parsed.confirmed = False
        assert parsed.signal(MINUTE) is None  # Momentum benchmark respects the same HL gap.
