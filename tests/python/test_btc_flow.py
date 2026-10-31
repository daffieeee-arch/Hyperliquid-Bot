"""Synthetic-only economic, point-in-time and harness integration regressions."""

from __future__ import annotations

import hashlib
import json
import resource
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
    CaptureWindow,
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
    FUNDING_MODEL,
    StagedFlowSource,
    checked_paths,
    exact_part_path,
    funding_timeline,
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


def approved_parts(*paths: Path) -> dict[str, str]:
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def synthetic_contexts(*paths: Path) -> dict[tuple[str, str], CaptureWindow]:
    return {
        (str(path), venue): CaptureWindow("s" * 64, venue, "synthetic-0000", 0, 0, 8 * DAY)
        for path in paths
        for venue in ("hyperliquid", "binance")
    }


def spec() -> HypothesisSpec:
    return study_spec(SPEC)[1]


def quote(when: int, ordinal: int, mid: str = "1000", session: str = "hl") -> Quote:
    return Quote(when, session, ordinal, D(mid) - 1, D(mid) + 1, D(1), D(1))


def source(
    *,
    gaps: tuple[int, ...] = (),
    quotes: tuple[Quote, ...] | None = None,
    funding_events_ms: tuple[int, ...] | None = None,
) -> FlowSource:
    if quotes is None:
        quotes = tuple(
            quote(m * MINUTE + offset, m * 4 + i, str(1000 + m))
            for m in range(41)
            for i, offset in enumerate((0, LATENCY, 2 * LATENCY, 3 * LATENCY))
        )
    flow = {m: Flow(D(6), D(10), frozenset({"bn"}), 2) for m in range(40)}
    return FlowSource(
        spec(), 0, 40 * MINUTE, quotes, flow, gaps, funding_events_ms=funding_events_ms
    )


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
        "format": 3,
        "phase": phase,
        "start_ns": start,
        "end_ns": end,
        "preregistration_commit": None,
        "preregistration_merged_ns": None,
        "parts": [],
        "funding": None,
        "capture": [],
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


@pytest.mark.parametrize("other", ["ETH", "SOL", " ", "BTC ", "btc", "UNKNOWN", None])
def test_original_raw_frame_parser_ignores_normalization_duplicates(
    tmp_path: Path, other: object
) -> None:
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
        {"channel": "bbo", "data": {"coin": other, "bbo": "not-decoded"}}
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
    if other in ("ETH", "SOL"):
        parsed = load_source(spec(), 0, 3 * MINUTE, approved_parts(path), synthetic_contexts(path))
        assert (
            len(parsed.quotes) == 3 and parsed.flow[0].trades == 1 and parsed.flow[0].imbalance == 1
        )
    else:
        with pytest.raises(IntegrityError, match="coin identity"):
            load_source(spec(), 0, 3 * MINUTE, approved_parts(path), synthetic_contexts(path))


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
            when = 3 * DAY + minute * MINUTE + index * LATENCY
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
        when = 3 * DAY + minute * MINUTE + 30 * SECOND
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
    inputs = manifest(3 * DAY, 6 * DAY)
    inputs["funding"] = funding_fixture(tmp_path, 3 * DAY)[0]
    inputs["capture"], inputs["parts"] = native_publications(part, tmp_path / "published", 3 * DAY)
    raw = json.dumps(inputs).encode()
    path = tmp_path / "inputs.json"
    path.write_bytes(raw)
    result = run(SPEC, path, hashlib.sha256(raw).hexdigest(), tmp_path / "out")
    assert result["status"] == "completed" and result["exploratory_only"] is True
    assert result["promotion_decision"] == "forbidden"
    decision = result["decision"]
    assert isinstance(decision, dict) and decision["label"] in (
        "model_conditional_exploratory_no_edge",
        "model_conditional_exploratory_interesting_but_fragile",
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
                    {
                        "path": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "publication_sha256": "s" * 64,
                    }
                ]
            }
        )
    with pytest.raises(IntegrityError, match="globs refused"):
        load_source(spec(), 0, 2 * MINUTE, approved_parts(path), synthetic_contexts(path))


@pytest.mark.parametrize(
    "event",
    [
        "gap",
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
            load_source(spec(), 0, 2 * MINUTE, approved_parts(part), synthetic_contexts(part))
    else:
        parsed = load_source(spec(), 0, 2 * MINUTE, approved_parts(part), synthetic_contexts(part))
        assert parsed.hl_gaps == (10 * SECOND,)
        parsed.confirmed = False
        assert parsed.signal(MINUTE) is None  # Momentum benchmark respects the same HL gap.


def sealed_oos_fixture(path: Path, start: int = 0) -> None:
    """Valid validation quotes, opaque malformed OOS that must remain sealed."""
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE frames(schema_version INTEGER,venue VARCHAR,product VARCHAR,"
        "channel VARCHAR,session_id VARCHAR,message_ordinal BIGINT,received_utc_ns BIGINT,"
        "received_monotonic_ns BIGINT,direction VARCHAR,payload_bytes BLOB,payload_sha256 VARCHAR)"
    )
    for ordinal, when in enumerate((start, start + MINUTE, start + 2 * DAY)):
        payload = (
            b"financial OOS must not be decoded"
            if when == start + 2 * DAY
            else json.dumps(
                {
                    "channel": "bbo",
                    "data": {
                        "coin": "BTC",
                        "time": when // 1_000_000,
                        "bbo": [{"px": "999", "sz": "1"}, {"px": "1001", "sz": "1"}],
                    },
                }
            ).encode()
        )
        connection.execute(
            "INSERT INTO frames VALUES (1,'hyperliquid','BTC-PERP','bbo','hl',?,?,?,'inbound',?,?)",
            [ordinal, when, when, payload, hashlib.sha256(payload).hexdigest()],
        )
    connection.execute("COPY frames TO ? (FORMAT PARQUET)", [str(path)])
    connection.close()


def test_actual_reader_never_decodes_oos_after_failed_validation_including_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research.btc_flow_cli as cli

    part = tmp_path / "sealed.parquet"
    sealed_oos_fixture(part)
    decoded: list[bytes] = []
    original = cli._json

    def observed(raw: bytes) -> dict[str, object]:
        decoded.append(raw)
        return original(raw)

    monkeypatch.setattr(cli, "_json", observed)
    staged = StagedFlowSource(spec(), 0, 3 * DAY, approved_parts(part), synthetic_contexts(part))
    result = decide_source(staged)
    assert result.label == "not_enough_data" and result.selected_config_id is None
    for fold in result.folds:
        staged.benchmark_window(spec().configs[0], fold.test_start, fold.test_end)
    assert len(decoded) == 2 and not staged.holdout_decoding_started
    # The same actual parser would reject this OOS; failure cannot contaminate
    # the previous validation result because it is first decoded at this request.
    with pytest.raises(json.JSONDecodeError):
        staged.window(spec().configs[0], 2880, 4320)
    assert len(decoded) == 3 and staged.holdout_decoding_started


def test_actual_reader_opens_oos_only_after_harness_validation_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    part = tmp_path / "sealed.parquet"
    sealed_oos_fixture(part)
    staged = StagedFlowSource(spec(), 0, 3 * DAY, approved_parts(part), synthetic_contexts(part))
    calls: list[tuple[int, int]] = []

    def passing_validation(config: ConfigSpec, start: int, end: int) -> TradeSeries:
        assert not staged.holdout_decoding_started
        assert end <= 2880
        calls.append((start, end))
        gross = tuple(0.01 + (i % 2) * 0.001 for i in range(40))
        return TradeSeries(gross, (0.0,) * 40, (0.0,) * 40, (1.0,) * 40, (0.0,) * 40)

    monkeypatch.setattr(staged._sources[(False, True)], "window", passing_validation)
    with pytest.raises(json.JSONDecodeError):
        decide_source(staged)
    assert len(calls) == 12 and staged.holdout_decoding_started


def funding_fixture(tmp_path: Path, start: int = 0) -> tuple[dict[str, str], Path, Path]:
    from research.hist_etl.hyperliquid import FundingRow, FundingWindow, render_raw

    hour_ms = 3_600_000
    window = FundingWindow(
        "native-fixture",
        "BTC",
        "1970-01",
        start // 1_000_000 - hour_ms,
        start // 1_000_000 + 73 * hour_ms,
        True,
    )
    native = tmp_path / "BTC-funding-native.json"
    native.write_text(
        render_raw(
            window,
            tuple(
                FundingRow(
                    start // 1_000_000 + i * hour_ms, "opaque-unused-rate", "opaque-unused-premium"
                )
                for i in range(-1, 73)
            ),
        )
    )
    proof = tmp_path / "funding-proof.json"
    proof.write_text(
        json.dumps(
            {
                "format": 1,
                "model": FUNDING_MODEL,
                "endpoint": "https://api.hyperliquid.xyz/info",
                "coin": "BTC",
                "snapshot_cutoff_ns": start + 3 * DAY + 61 * MINUTE,
                "frozen_utc_ns": start + 3 * DAY + 62 * MINUTE,
                "known_holes": [],
                "sources": [
                    {"path": str(native), "sha256": hashlib.sha256(native.read_bytes()).hexdigest()}
                ],
            }
        )
    )
    return (
        {"path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()},
        proof,
        native,
    )


def rebind_funding(proof: Path, native: Path) -> dict[str, str]:
    document = json.loads(proof.read_bytes())
    document["sources"][0]["sha256"] = hashlib.sha256(native.read_bytes()).hexdigest()
    proof.write_text(json.dumps(document))
    return {"path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}


def test_native_funding_timeline_qualifies_times_without_numeric_rate_or_oracle_inputs(
    tmp_path: Path,
) -> None:
    reference, proof, native = funding_fixture(tmp_path)
    events, hashes = funding_timeline(reference, 0, 3 * DAY)
    assert events == tuple(i * 3_600_000 for i in range(-1, 73))
    assert hashes == {
        str(proof): reference["sha256"],
        str(native): hashlib.sha256(native.read_bytes()).hexdigest(),
    }


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "last_padding",
        "duplicate",
        "unknown_model",
        "known_hole",
        "stale_cutoff",
        "future_freeze",
        "wrong_coin",
        "raw_mutation",
        "proof_mutation",
    ],
)
def test_funding_timeline_rejects_unqualified_native_metadata(tmp_path: Path, failure: str) -> None:
    reference, proof, native = funding_fixture(tmp_path)
    data, control = json.loads(native.read_bytes()), json.loads(proof.read_bytes())
    if failure == "missing":
        del data["rows"][20]
    elif failure == "last_padding":
        data["rows"].pop()
    elif failure == "duplicate":
        data["rows"].insert(20, dict(data["rows"][20], time=data["rows"][20]["time"] - 1))
    elif failure == "unknown_model":
        control["model"] = "actual-onchain-settlement"
    elif failure == "known_hole":
        control["known_holes"] = [0]
    elif failure == "stale_cutoff":
        control["snapshot_cutoff_ns"] = 3 * DAY
    elif failure == "future_freeze":
        control["frozen_utc_ns"] = 10**30
    elif failure == "wrong_coin":
        data["rows"][20]["coin"] = "ETH"
    elif failure == "proof_mutation":
        control["coin"] = "ETH"
    native.write_text(json.dumps(data))
    proof.write_text(json.dumps(control))
    if failure == "raw_mutation":
        reference = {"path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}
    elif failure != "proof_mutation":
        reference = rebind_funding(proof, native)
    with pytest.raises(IntegrityError):
        funding_timeline(reference, 0, 3 * DAY)


@pytest.mark.parametrize("phase", ["exploratory", "prospective"])
def test_unqualified_funding_stops_before_any_financial_part_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    import research.btc_flow_cli as cli

    inputs = manifest(3 * DAY, 6 * DAY, phase)
    if phase == "prospective":
        inputs.update(preregistration_commit="a" * 40, preregistration_merged_ns=DAY + 1)
    path = tmp_path / "inputs.json"
    raw = json.dumps(inputs).encode()
    path.write_bytes(raw)

    def forbidden(*_args: object) -> None:
        raise AssertionError("no financial input read before funding qualification")

    monkeypatch.setattr(cli, "checked_paths", forbidden)
    monkeypatch.setattr(cli, "load_source", forbidden)
    result = run(SPEC, path, hashlib.sha256(raw).hexdigest(), tmp_path / "out")
    assert result["status"] == "funding_unqualified"
    assert result["financial_frames_read"] is False and result["promotion_decision"] == "forbidden"
    assert "decision" not in result


@pytest.mark.parametrize(
    "boundary,offset,blocked",
    [
        ("entry", -1, False),
        ("entry", 0, True),
        ("entry", 1, True),
        ("exit", -1, True),
        ("exit", 0, True),
        ("exit", 1, False),
    ],
)
def test_exact_api_event_clock_closed_holding_interval_boundaries(
    boundary: str, offset: int, blocked: bool
) -> None:
    first = source().fills(CONFIG, 0, 40)[0]
    when = first.entry.received_ns if boundary == "entry" else first.exit.received_ns
    selected = source(funding_events_ms=(when // 1_000_000 + offset,))
    if blocked:
        with pytest.raises(IntegrityError, match="funding API-clock event"):
            selected.window(CONFIG, 0, 40)
    else:
        assert selected.window(CONFIG, 0, 40).gross


@pytest.mark.parametrize("confirmed", [False, True])
def test_late_api_event_inside_holding_fails_whole_window_including_benchmark(
    confirmed: bool,
) -> None:
    original = source()
    selected = FlowSource(
        original.spec,
        original.start_ns,
        original.end_ns,
        original.quotes,
        original.flow,
        confirmed=confirmed,
        funding_events_ms=((2 * MINUTE + 500_000_000) // 1_000_000,),
    )
    for config in original.spec.configs:
        with pytest.raises(IntegrityError, match="funding API-clock event"):
            selected.window(config, 0, 40)


@pytest.mark.parametrize("boundary", ["entry", "exit"])
def test_submillisecond_holding_boundaries_are_conservative(boundary: str) -> None:
    original = source()
    first = original.fills(CONFIG, 0, 40)[0]
    shifted = source(
        quotes=tuple(
            replace(q, received_ns=q.received_ns + 123) if q in (first.entry, first.exit) else q
            for q in original.quotes
        )
    )
    actual = shifted.fills(CONFIG, 0, 40)[0]
    event = (
        actual.entry.received_ns // 1_000_000
        if boundary == "entry"
        else (actual.exit.received_ns + 999_999) // 1_000_000
    )
    selected = source(quotes=shifted.quotes, funding_events_ms=(event,))
    with pytest.raises(IntegrityError, match="funding API-clock event"):
        selected.window(CONFIG, 0, 40)


def test_fixed_engineering_part_admission_accepts_native_small_parts_without_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import research.btc_flow_cli as cli

    data = b"opaque closed file bytes"
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(cli, "_read", lambda _path, _limit: data)
    inputs = manifest(0, 3 * DAY)
    inputs["parts"] = [
        {"path": f"/fixture/{i}.parquet", "sha256": digest, "publication_sha256": "s" * 64}
        for i in range(10_001)
    ]
    assert len(checked_paths(inputs)[0]) == 10_001
    inputs["parts"] = [{}] * 70_001
    with pytest.raises(IntegrityError, match="raw part count"):
        checked_paths(inputs)


@pytest.mark.parametrize("inherited", [(8 << 30, 8 << 30), (-1, -1), (4 << 30, 6 << 30)])
def test_cli_address_space_cap_never_relaxes_stricter_inherited_limit(
    monkeypatch: pytest.MonkeyPatch, inherited: tuple[int, int]
) -> None:
    import research.btc_flow_cli as cli

    applied = []
    monkeypatch.setattr(resource, "getrlimit", lambda _kind: inherited)
    monkeypatch.setattr(resource, "setrlimit", lambda kind, values: applied.append((kind, values)))
    cli.bound_address_space()
    expected = 4 << 30 if inherited[0] == 4 << 30 else 8 << 30
    assert applied == [(resource.RLIMIT_AS, (expected, expected))]


@pytest.mark.parametrize("phase", ["exploratory", "prospective"])
def test_full_runner_keeps_malformed_oos_sealed_after_failed_validation(
    tmp_path: Path, phase: str
) -> None:
    from research.harness.spec import spec_sha256, verify_lock

    part = tmp_path / "sealed.parquet"
    sealed_oos_fixture(part, 3 * DAY)
    inputs = manifest(3 * DAY, 6 * DAY, phase)
    if phase == "prospective":
        inputs.update(preregistration_commit="a" * 40, preregistration_merged_ns=DAY + 1)
    inputs["funding"] = funding_fixture(tmp_path, 3 * DAY)[0]
    inputs["capture"], inputs["parts"] = native_publications(part, tmp_path / "published", 3 * DAY)
    raw = json.dumps(inputs).encode()
    path = tmp_path / "inputs.json"
    path.write_bytes(raw)
    result = run(SPEC, path, hashlib.sha256(raw).hexdigest(), tmp_path / "out")
    assert result["status"] == "completed"
    assert result["promotion_decision"] == "forbidden"
    decision = result["decision"]
    assert isinstance(decision, dict)
    assert decision["label"] == (
        "model_conditional_"
        + ("exploratory_" if phase == "exploratory" else "")
        + "not_enough_data"
    )
    assert decision["selected_config_id"] is None and decision["promotion_decision"] == "forbidden"
    assert "momentum_without_flow_holdout_benchmark" not in result
    document, _ = study_spec(SPEC)
    locked = verify_lock(tmp_path / "out/spec.yaml", document, spec_sha256(document))
    assert locked["capture_source_sha256"] == result["capture_source_sha256"]


def test_complete_native_timeline_with_late_event_still_invalidates_exposure(
    tmp_path: Path,
) -> None:
    _, proof, native = funding_fixture(tmp_path)
    data = json.loads(native.read_bytes())
    data["rows"][1]["time"] = (2 * MINUTE + 500_000_000) // 1_000_000
    native.write_text(json.dumps(data))
    events, _ = funding_timeline(rebind_funding(proof, native), 0, 3 * DAY)
    with pytest.raises(IntegrityError, match="funding API-clock event"):
        source(funding_events_ms=events).window(CONFIG, 0, 40)


def test_quoted_costs_preserve_per_trade_weight_shape_per_fold() -> None:
    costs = spec().costs
    valid = TradeSeries((0.1,), (0.0,), (0.0,), (1.0,), (0.01,))
    short = replace(valid, weights=())
    long = replace(valid, weights=(1.0, 1.0))
    for malformed in (short, long):
        with pytest.raises(IntegrityError, match="vector"):
            malformed.net(costs, 1.0)
    with pytest.raises(IntegrityError, match="fold"):
        _concat_series((short, long))
    assert valid.net(costs, 1.0) == pytest.approx((0.09,))


@pytest.mark.parametrize("attack", ["symlink", "inplace_aba"])
def test_actual_reader_uses_approved_immutable_bytes_during_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    from contextlib import contextmanager

    import research.btc_flow_cli as cli
    from research.btc_flow_io import sealed_parquet

    approved = tmp_path / "approved.parquet"
    altered = tmp_path / "altered.parquet"
    sealed_oos_fixture(approved)
    c = duckdb.connect()
    c.execute("CREATE TABLE changed AS SELECT * FROM read_parquet(?)", [str(approved)])
    c.execute(
        "UPDATE changed SET payload_bytes=encode(replace("
        "replace(decode(payload_bytes), '999', '1999'), '1001', '2001'))"
    )
    c.execute("UPDATE changed SET payload_sha256=sha256(payload_bytes)")
    c.execute("COPY changed TO ? (FORMAT PARQUET)", [str(altered)])
    c.close()
    hashes = approved_parts(approved)
    original_bytes = approved.read_bytes()

    @contextmanager
    def swapped(path: Path, expected: str):  # type: ignore[no-untyped-def]
        with sealed_parquet(path, expected) as bound:
            backup = path.with_suffix(".retained")
            if attack == "symlink":
                path.rename(backup)
                path.symlink_to(altered)
            else:
                path.write_bytes(altered.read_bytes())
            try:
                yield bound
            finally:
                if attack == "symlink":
                    path.unlink()
                    backup.rename(path)
                else:
                    path.write_bytes(original_bytes)

    monkeypatch.setattr(cli, "sealed_parquet", swapped)
    parsed = load_source(spec(), 0, 2 * MINUTE, hashes, synthetic_contexts(approved))
    assert parsed.quotes[0].mid == D(1000)
    assert approved_parts(approved) == hashes


def test_actual_memfd_sql_seals_allowlist_and_successive_descriptor_reuse(tmp_path: Path) -> None:
    import os

    from research.btc_flow_io import sealed_parquet

    numbers = []
    for value in (17, 29):
        path = tmp_path / f"part-{value}.parquet"
        other = tmp_path / f"other-{value}.parquet"
        connection = duckdb.connect()
        connection.execute("CREATE TABLE data AS SELECT ? AS fixture", [value])
        connection.execute("COPY data TO ? (FORMAT PARQUET)", [str(path)])
        connection.execute("COPY (SELECT 99 AS fixture) TO ? (FORMAT PARQUET)", [str(other)])
        connection.close()
        with sealed_parquet(path, approved_parts(path)[str(path)]) as (sql, bound):
            fd = int(bound.rsplit("/", 1)[1])
            numbers.append(fd)
            with pytest.raises(OSError):
                os.write(fd, b"not mutable")
            with pytest.raises(OSError):
                os.ftruncate(fd, 0)
            assert sql.execute("SELECT fixture FROM read_parquet(?)", [bound]).fetchone() == (
                value,
            )
            with pytest.raises(duckdb.PermissionException):
                sql.execute("SELECT * FROM read_parquet(?)", [str(other)])
    assert numbers[0] == numbers[1]  # Fresh connections must not alias reused FD paths.


@pytest.mark.parametrize("leaf", ["spec.yaml", "spec.yaml.lock.json", "result.json"])
@pytest.mark.parametrize("race", [False, True])
def test_anchored_output_refuses_directory_rebind_without_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leaf: str, race: bool
) -> None:
    from research.btc_flow_io import PrivateOutput

    output, retained, target = tmp_path / "out", tmp_path / "retained", tmp_path / "other"
    target.mkdir()
    with pytest.raises(OSError):
        with PrivateOutput(output) as writer:

            def rebind() -> None:
                output.rename(retained)
                output.symlink_to(target, target_is_directory=True)

            if race:
                original = writer.assert_bound

                def checked_then_swapped() -> None:
                    original()
                    rebind()

                monkeypatch.setattr(writer, "assert_bound", checked_then_swapped)
            else:
                rebind()
            writer.json(leaf, {"synthetic": True})
    assert list(target.iterdir()) == []
    assert not (target / leaf).exists()


def test_wrong_approved_parquet_digest_is_rejected_before_sql(tmp_path: Path) -> None:
    part = tmp_path / "part.parquet"
    sealed_oos_fixture(part)
    with pytest.raises(IntegrityError, match="approved Parquet bytes"):
        load_source(spec(), 0, 2 * MINUTE, {str(part): "0" * 64}, synthetic_contexts(part))


def native_publications(
    part: Path, root: Path, start: int
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Synthetic records through actual native planning/claim/health/publication APIs."""
    from unittest.mock import patch

    from hyperliquid_bot.binance_public_research import data1f_capture_claim, data1f_capture_health
    from hyperliquid_bot.collector_runtime import RuntimePlan, capture_window_plan
    from hyperliquid_bot.collector_storage import exclusive_json, publish_closed_chunk
    from hyperliquid_bot.hyperliquid_raw_research import data1a_capture_claim, data1a_capture_health
    from hyperliquid_bot.reconstructable_paths import data1a_run_paths, data1f_run_paths

    root.mkdir(mode=0o700)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE original AS SELECT * FROM read_parquet(?)", [str(part)])
    groups = connection.execute(
        "SELECT DISTINCT venue,((received_utc_ns-?)//?) AS chunk "
        "FROM original ORDER BY venue,chunk",
        [start, 6 * 60 * MINUTE],
    ).fetchall()
    refs, parts = [], []
    campaign = RuntimePlan(
        "synthetic", ("hyperliquid", "binance"), 21600, 12, False, "a" * 40, "sha256:" + "b" * 64
    )
    for venue, chunk in groups:
        lane = "hyperliquid" if venue == "hyperliquid" else "binance"
        run = f"synthetic-{chunk:04d}"
        before = start + chunk * 6 * 60 * MINUTE - SECOND
        clock = {
            "schema": "collector-host-clock-v1",
            "sampled_utc_ns": before,
            "synchronized": True,
            "system_offset_seconds": 0.001,
            "root_delay_seconds": 0.01,
            "root_dispersion_seconds": 0.002,
        }
        plan = capture_window_plan(
            campaign,
            lane,
            chunk,
            clock,
            utc_before_ns=before,
            monotonic_ns=before,
            utc_after_ns=before + 1000,
        )
        artifact_root = root / "original" / lane
        with patch(
            "hyperliquid_bot.capture_live_status.process_code_version", return_value="a" * 40
        ):
            if venue == "hyperliquid":
                hl = data1a_run_paths(artifact_root, run)
                source, raw = hl.run_dir, hl.raw_dir / "part.parquet"
                claim = data1a_capture_claim(run_id=run, duration_seconds=21600, paths=hl)
                health = data1a_capture_health(
                    run_id=run,
                    duration_seconds=21600,
                    status="COMPLETED",
                    report={"elapsed_seconds": 21600},
                )
            else:
                bn = data1f_run_paths(artifact_root, run)
                source, raw = bn.run_dir, bn.raw_dir / "part.parquet"
                claim = data1f_capture_claim(run_id=run, duration_seconds=21600, paths=bn)
                health = data1f_capture_health(
                    run_id=run,
                    duration_seconds=21600,
                    status="COMPLETED",
                    report={"elapsed_seconds": 21600},
                )
        source.mkdir(parents=True, mode=0o700)
        raw.parent.mkdir(mode=0o700)
        connection.execute(
            "CREATE OR REPLACE TEMP TABLE selected AS SELECT * "
            "REPLACE (session_id||? AS session_id) FROM original "
            "WHERE venue=? AND ((received_utc_ns-?)//?)=?",
            [f"-{chunk}", venue, start, 6 * 60 * MINUTE, chunk],
        )
        connection.execute("COPY selected TO ? (FORMAT PARQUET)", [str(raw)])
        plan_sha = exclusive_json(source / "collector-plan.json", plan)
        provenance = {
            "schema": "public-collector-run-provenance-v1",
            "campaign": "synthetic",
            "run_id": run,
            "lane": lane,
            "source_commit": "a" * 40,
            "image_digest": "sha256:" + "b" * 64,
            "chunk_seconds": 21600,
            "window_plan_sha256": plan_sha,
        }
        exclusive_json(source / "collector-runtime.json", provenance)
        exclusive_json(source / "capture-claim.json", claim)
        exclusive_json(source / "capture-health.json", health)
        destination = root / f"{lane}-{chunk}"
        receipt = publish_closed_chunk(source, destination, provenance=provenance, catalogue=None)
        manifest_sha = str(receipt["manifest_sha256"])
        published_raw = destination / "raw" / "part.parquet"
        refs.append(
            {"path": str(destination / "publication-manifest.json"), "sha256": manifest_sha}
        )
        parts.append(
            {
                "path": str(published_raw),
                "sha256": hashlib.sha256(published_raw.read_bytes()).hexdigest(),
                "publication_sha256": manifest_sha,
            }
        )
    connection.close()
    return refs, parts


def capture_fixture(tmp_path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    part = tmp_path / "original.parquet"
    sealed_oos_fixture(part, 3 * DAY)
    return native_publications(part, tmp_path / "published", 3 * DAY)


def rebind_publication(
    refs: list[dict[str, str]], parts: list[dict[str, str]], index: int = 0
) -> None:
    path = Path(refs[index]["path"])
    document = json.loads(path.read_bytes())
    for name, record in document["files"].items():
        if name in (
            "collector-plan.json",
            "capture-claim.json",
            "collector-runtime.json",
            "capture-health.json",
        ):
            content = (path.parent / name).read_bytes()
            record.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    path.write_text(json.dumps(document))
    previous = refs[index]["sha256"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    refs[index]["sha256"] = digest
    for part in parts:
        if part["publication_sha256"] == previous:
            part["publication_sha256"] = digest
    (path.parent / "COMPLETE.json").write_text(
        json.dumps({"manifest_sha256": digest, "closed_chunk_verified": True})
    )


def test_native_capture_publications_qualify_exact_parts_without_reading_payloads(
    tmp_path: Path,
) -> None:
    from research.btc_flow_capture import qualify_capture
    from research.btc_flow_cli import _json

    refs, parts = capture_fixture(tmp_path)
    contexts, hashes = qualify_capture(refs, parts, _json)
    assert set(contexts) == {(p["path"], "hyperliquid") for p in parts}
    assert all(context.earliest_end_ns > context.prelaunch_utc_ns for context in contexts.values())
    assert len(hashes) == 6 * len(refs)  # manifest, seal, plan, claim, runtime, health


@pytest.mark.parametrize(
    "failure",
    [
        "missing_plan",
        "rewritten_native_path",
        "wrong_product",
        "wrong_source",
        "not_completed",
        "wrong_seal",
        "bad_bracket",
        "bad_end",
        "raw_hash",
        "wrong_publication",
    ],
)
def test_capture_context_rejects_invalid_bindings_before_financial_decode(
    tmp_path: Path, failure: str
) -> None:
    from research.btc_flow_capture import qualify_capture
    from research.btc_flow_cli import _json

    refs, parts = capture_fixture(tmp_path)
    root = Path(refs[0]["path"]).parent
    if failure in ("rewritten_native_path", "wrong_product", "wrong_source"):
        path = root / "capture-claim.json"
        control = json.loads(path.read_bytes())
        control[
            {
                "rewritten_native_path": "raw_dir",
                "wrong_product": "product",
                "wrong_source": "code_version",
            }[failure]
        ] = {
            "rewritten_native_path": str(root / "raw"),
            "wrong_product": "BTCUSDT-SPOT",
            "wrong_source": "c" * 40,
        }[failure]
        path.write_text(json.dumps(control))
        rebind_publication(refs, parts)
    elif failure == "missing_plan":
        (root / "collector-plan.json").unlink()
    elif failure == "not_completed":
        path = root / "capture-health.json"
        control = json.loads(path.read_bytes())
        control["status"] = "OPERATOR_STOP"
        path.write_text(json.dumps(control))
        rebind_publication(refs, parts)
    elif failure == "wrong_seal":
        (root / "COMPLETE.json").write_text("{}")
    elif failure in ("bad_bracket", "bad_end"):
        path = root / "collector-plan.json"
        control = json.loads(path.read_bytes())
        control[
            "prelaunch_utc_after_ns" if failure == "bad_bracket" else "earliest_planned_end_utc_ns"
        ] += 2 * SECOND
        path.write_text(json.dumps(control))
        rebind_publication(refs, parts)
    elif failure == "raw_hash":
        parts[0]["sha256"] = "f" * 64
    else:
        parts[0]["publication_sha256"] = refs[1]["sha256"]
    with pytest.raises((IntegrityError, OSError)):
        qualify_capture(refs, parts, _json)


def test_capture_requires_every_published_raw_part(tmp_path: Path) -> None:
    from research.btc_flow_capture import qualify_capture
    from research.btc_flow_cli import _json

    refs, parts = capture_fixture(tmp_path)
    path = Path(refs[0]["path"])
    pub = json.loads(path.read_bytes())
    # Metadata-only census: every raw file is mandatory even before financial
    # duplicate/event checks, so an omitted losing part cannot be hidden.
    pub["files"]["raw/another.parquet"] = dict(pub["files"]["raw/part.parquet"])
    path.write_text(json.dumps(pub))
    rebind_publication(refs, parts)
    with pytest.raises(IntegrityError, match="every published raw part"):
        qualify_capture(refs, parts, _json)


@pytest.mark.parametrize("confirmed", [False, True])
def test_predeclared_drifting_capture_end_filters_entries_before_future_unknown_exit(
    confirmed: bool,
) -> None:
    original = source()
    known_end = 10 * MINUTE + 30 * SECOND  # Deliberately not HH:00.
    context = CaptureWindow("s" * 64, "synthetic", "synthetic-0000", 0, 0, known_end)
    selected = FlowSource(
        original.spec,
        original.start_ns,
        original.end_ns,
        original.quotes,
        original.flow,
        confirmed=confirmed,
        hl_windows={"hl": context},
        bn_windows={"bn": context},
        bn_first_trade={"bn": 0},
    )
    for config in original.spec.configs:
        fills = selected.fills(config, 0, 40)
        assert fills
        assert all(
            f.decision_ns + ENTRY_DELAY + 2 * 5 * SECOND + LATENCY + config.horizon_bars * MINUTE
            < known_end - MINUTE
            for f in fills
        )
        assert all(f.exit.received_ns < known_end - MINUTE for f in fills)


@pytest.mark.parametrize("confirmed", [False, True])
def test_spot_warmup_requires_whole_previous_minute_after_first_actual_trade(
    confirmed: bool,
) -> None:
    original = source()
    selected = FlowSource(
        original.spec,
        original.start_ns,
        original.end_ns,
        original.quotes,
        original.flow,
        confirmed=confirmed,
        bn_first_trade={"bn": 30 * SECOND},
    )
    assert selected.signal(MINUTE) is None
    assert selected.signal(2 * MINUTE) is not None


def test_capture_unqualified_stops_before_any_financial_part_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research.btc_flow_cli as cli

    inputs = manifest(0, 3 * DAY)
    inputs["funding"] = funding_fixture(tmp_path)[0]
    raw = json.dumps(inputs).encode()
    path = tmp_path / "inputs.json"
    path.write_bytes(raw)

    def forbidden(*_args: object) -> None:
        raise AssertionError("capture qualification precedes financial input access")

    monkeypatch.setattr(cli, "checked_paths", forbidden)
    monkeypatch.setattr(cli, "load_source", forbidden)
    result = run(SPEC, path, hashlib.sha256(raw).hexdigest(), tmp_path / "out")
    assert result["status"] == "capture_unqualified" and result["financial_frames_read"] is False
    assert result["promotion_decision"] == "forbidden" and "decision" not in result


@pytest.mark.parametrize("bad_clock", [False, True])
def test_cross_part_clock_ranges_use_source_ordinals_not_path_order(
    tmp_path: Path, bad_clock: bool
) -> None:
    original = tmp_path / "original.parquet"
    sealed_oos_fixture(original)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE source AS SELECT * FROM read_parquet(?)", [str(original)])
    if bad_clock:
        connection.execute(
            "UPDATE source SET received_monotonic_ns=?-received_utc_ns WHERE received_utc_ns<=?",
            [MINUTE, MINUTE],
        )
    first, last = tmp_path / "z-first.parquet", tmp_path / "a-last.parquet"
    for path, when in ((first, 0), (last, MINUTE)):
        connection.execute(
            "CREATE OR REPLACE TEMP TABLE selected AS SELECT * FROM source WHERE received_utc_ns=?",
            [when],
        )
        connection.execute("COPY selected TO ? (FORMAT PARQUET)", [str(path)])
    connection.close()
    if bad_clock:
        with pytest.raises(IntegrityError, match="cross-part receipt clock"):
            load_source(
                spec(), 0, 2 * MINUTE, approved_parts(first, last), synthetic_contexts(first, last)
            )
    else:
        parsed = load_source(
            spec(), 0, 2 * MINUTE, approved_parts(first, last), synthetic_contexts(first, last)
        )
        assert parsed.quote_times == (0, MINUTE)


def test_private_output_requires_leaf_mount_equal_verified_local_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research.btc_flow_io as io

    original = io._mount
    calls = 0

    def changed_mount(fd: int) -> str:
        nonlocal calls
        calls += 1
        value = original(fd)
        return value if calls == 1 else value + "-inserted"

    monkeypatch.setattr(io, "_mount", changed_mount)
    with pytest.raises(IntegrityError, match="leaf mount"):
        io.PrivateOutput(tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []
