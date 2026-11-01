from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import hyperliquid_bot.calendar_research as module
from hyperliquid_bot.calendar_research import (
    END,
    HOUR,
    ROWS,
    START,
    TEST_START,
    Funding,
    Stage,
    _sanitize,
    _series,
    calendar_key,
    calendar_pairs,
    digest,
    evaluate,
    funding_buckets,
    load_inputs,
    metrics,
    validation_passes,
)
from hyperliquid_bot.local_mode import UnsafeTradingModeError
from research.harness.data import BarTable
from research.harness.errors import IntegrityError
from research.harness.evaluate import Trade, TradeSeries
from research.harness.spec import HypothesisSpec, load_document, spec_sha256, validate_spec
from research.harness.splits import walk_forward


@pytest.fixture
def spec() -> HypothesisSpec:
    return validate_spec(
        {
            "hypothesis_id": "btc-month-turn-calendar-proxy-v1",
            "universe": "fixture Binance reference",
            "dataset_version": "fixture",
            "h0": "nonpositive calendar excess",
            "h1": "positive conditional calendar excess",
            "alpha": 0.025,
            "selection_method": "holm",
            "direction": "long_only",
            "signal_feature": "month_turn",
            "costs": {
                "fee_bps": 5.0,
                "slippage_bps": 1.0,
                "spread_bps": 0.5,
                "latency_bars": 1,
                "funding_column": "funding_rate",
            },
            "split": {
                "method": "expanding",
                "train_bars": 8784,
                "test_bars": 8760,
                "holdout_bars": 14592,
            },
            "sample": {
                "min_trades_validation": 36,
                "min_trades_holdout": 18,
                "min_folds": 4,
            },
            "data": {
                "backend": "duckdb",
                "view": "month_turn_btc_hourly",
                "timestamp_column": "ts",
                "price_column": "close",
                "max_gap": HOUR,
                "max_rows": ROWS,
                "columns": {
                    "ts": {"dtype": "int64", "role": "timestamp"},
                    "close": {"dtype": "float64", "role": "price"},
                    "month_turn": {"dtype": "float64", "role": "feature"},
                    "funding_rate": {"dtype": "float64", "role": "funding"},
                    "available_ts": {"dtype": "int64", "role": "availability"},
                },
            },
            "features": [
                {
                    "name": "month_turn",
                    "column": "month_turn",
                    "available_at_column": "available_ts",
                }
            ],
            "configs": [{"id": "calendar-long-96h", "threshold": 0.5, "horizon_bars": 96}],
        }
    )


def ms(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("2020-01-30T23:59:59.999", (2020, 2)),
        ("2020-02-28T23:59:59.999", (2020, 3)),
        ("2021-02-27T23:59:59.999", (2021, 3)),
        ("2020-12-30T23:59:59.999", (2021, 1)),
        ("2020-02-27T23:59:59.999", None),
        ("2020-01-31T00:59:59.999", None),
    ],
)
def test_calendar_decision_precedes_actual_last_day_fill(
    clock: str, expected: tuple[int, int] | None
) -> None:
    assert calendar_key(ms(clock), control=False) == expected


def test_midmonth_targets_the_following_month_not_the_entry_month() -> None:
    assert calendar_key(ms("2020-01-30T23:59:59.999"), control=False) == (2020, 2)
    assert calendar_key(ms("2020-02-14T23:59:59.999"), control=True) == (2020, 2)
    assert calendar_key(ms("2020-01-14T23:59:59.999"), control=True) == (2020, 1)
    with pytest.raises(IntegrityError, match="hourly close"):
        calendar_key(ms("2020-01-31T00:00:00"), control=False)


def test_frozen_pairs_and_known_fold_boundaries_without_reading_prices(
    spec: HypothesisSpec,
) -> None:
    timestamps = tuple(range(START + HOUR - 1, END, HOUR))
    folds, (start, end) = walk_forward(ROWS, spec.split)
    assert len(folds) == 4
    for fold in folds:
        primary, control = calendar_pairs(
            timestamps, fold.test_start, fold.test_end, spec.configs[0]
        )
        assert len(primary) == len(control) == 11
        for tom, mid in zip(primary, control, strict=True):
            assert tom.entry == tom.decision + 1 and tom.exit == tom.entry + 96
            assert mid.entry == mid.decision + 1 and mid.exit == mid.entry + 96
            assert timestamps[tom.exit] < timestamps[fold.test_end]
            assert calendar_key(timestamps[tom.decision], control=False) == calendar_key(
                timestamps[mid.decision], control=True
            )
    assert start == 43848 and end == ROWS
    assert timestamps[start - 1] == ms("2024-12-31T23:59:59.999")
    assert folds[-1].test_end == start - 24
    primary, control = calendar_pairs(timestamps, start, end, spec.configs[0])
    assert len(primary) == len(control) == 19


def test_missing_required_control_is_failure_not_outcome_dependent_removal(
    spec: HypothesisSpec,
) -> None:
    # The primary fits, but the predeclared control would lie outside this
    # deliberately incomplete window. Refuse the study instead of losing a pair.
    timestamps = tuple(range(ms("2020-01-30T00:59:59.999"), ms("2020-02-10"), HOUR))
    with pytest.raises(IntegrityError, match="control outside"):
        calendar_pairs(timestamps, 0, len(timestamps), spec.configs[0])


def test_native_funding_one_millisecond_boundaries_and_no_clock_truncation() -> None:
    timestamps = (HOUR - 1, 2 * HOUR - 1, 3 * HOUR - 1)
    events = (
        Funding(HOUR - 1, 0.1),
        Funding(HOUR, 0.2),
        Funding(2 * HOUR - 1, 0.3),
        Funding(2 * HOUR, 0.4),
    )
    rates = funding_buckets(timestamps, events)
    assert rates == pytest.approx((0.1, 0.5, 0.4))
    table = BarTable(timestamps, (100.0, 100.0, 100.0), {}, {}, rates)
    series = _series(Stage(table, events), (Trade(0, 0, 1, 1),))
    assert series.funding_paid == pytest.approx((0.5,))
    assert series.funding_received == (0.0,)
    with pytest.raises(IntegrityError, match="outside"):
        funding_buckets(timestamps, (Funding(3 * HOUR, 0.1),))
    with pytest.raises(IntegrityError, match="finite"):
        funding_buckets(timestamps, (Funding(HOUR, math.nan),))


def series(values: tuple[float, ...]) -> TradeSeries:
    zero = (0.0,) * len(values)
    return TradeSeries(values, zero, zero, (1.0,) * len(values))


def test_positive_btc_beta_does_not_pass_negative_paired_excess(spec: HypothesisSpec) -> None:
    gross = tuple(0.05 + 0.005 * math.sin(i) for i in range(44))
    blocks = metrics([series(gross)], [series(tuple(x + 0.01 for x in gross))], spec)
    assert all(
        group["primary"].mean_return is not None
        and group["primary"].mean_return > 0
        and group["primary"].p_value is not None
        and group["primary"].p_value < 0.025
        for group in blocks.values()
    )
    assert not validation_passes(blocks, spec)


def test_gate_requires_complete_stress_blocks_and_sample_floors(spec: HypothesisSpec) -> None:
    gross = tuple(0.05 + 0.005 * math.sin(i) for i in range(44))
    blocks = metrics([series(gross)], [series((0.0,) * 44)], spec)
    assert validation_passes(blocks, spec)
    assert not validation_passes({}, spec)
    assert not validation_passes({"1.0": blocks["1.0"]}, spec)
    weak = {key: dict(group) for key, group in blocks.items()}
    weak["2.0"]["paired_excess"] = replace(weak["2.0"]["paired_excess"], trade_count=35)
    assert not validation_passes(weak, spec)


def test_failed_validation_never_loads_exposed_test_or_its_benchmark(spec: HypothesisSpec) -> None:
    calls: list[tuple[int, int]] = []

    def load(start: int, end: int) -> Stage:
        calls.append((start, end))
        assert (start, end) == (START, TEST_START), "test financial data opened prematurely"
        clocks = tuple(range(start + HOUR - 1, end, HOUR))
        table = BarTable(clocks, (100.0,) * len(clocks), {}, {}, (0.0,) * len(clocks))
        return Stage(table, ())

    outcome = evaluate(spec, load)
    assert calls == [(START, TEST_START)]
    assert outcome["label"] == "historical_no_edge"
    assert outcome["historical_test"] == {"state": "sealed", "financial_read": False}
    assert outcome["promotion_decision"] == "forbidden"
    assert outcome["hyperliquid_execution_qualified"] is False


@pytest.mark.parametrize("fail_test", [False, True])
def test_validation_is_frozen_before_test_and_survives_a_later_failure(
    spec: HypothesisSpec, tmp_path: Path, fail_test: bool
) -> None:
    calls: list[tuple[int, int]] = []
    artifact = tmp_path / "validation.json"

    def freeze(evidence: dict[str, object]) -> None:
        assert not artifact.exists()
        artifact.write_text(json.dumps(evidence))

    def load(start: int, end: int) -> Stage:
        calls.append((start, end))
        if start == TEST_START:
            assert artifact.is_file(), "validation must be durable before test access"
            frozen = json.loads(artifact.read_text())
            assert frozen["admitted_test"] is True
            assert frozen["historical_test_financial_read"] is False
            if fail_test:
                raise RuntimeError("synthetic late data failure")
        clocks = tuple(range(start + HOUR - 1, end, HOUR))
        prices = [100.0] * len(clocks)
        windows = (
            [(fold.test_start, fold.test_end) for fold in walk_forward(ROWS, spec.split)[0]]
            if start == START
            else [(0, len(clocks))]
        )
        for first, last in windows:
            primary, _ = calendar_pairs(clocks, first, last, spec.configs[0])
            for index, trade in enumerate(primary):
                prices[trade.exit] = 110.0 + math.sin(index)
        return Stage(BarTable(clocks, tuple(prices), {}, {}, (0.0,) * len(clocks)), ())

    if fail_test:
        with pytest.raises(RuntimeError, match="late data failure"):
            evaluate(spec, load, freeze_validation=freeze)
        assert artifact.is_file()
        assert json.loads(artifact.read_text())["admitted_test"] is True
        return
    outcome = evaluate(spec, load, freeze_validation=freeze)
    assert calls == [(START, TEST_START), (TEST_START, END)]
    assert outcome["label"] == "historical_interesting_but_unconfirmed"
    assert outcome["future_oos_assigned"] is False
    assert outcome["hyperliquid_execution_qualified"] is False
    assert outcome["promotion_decision"] == "forbidden"
    text = json.dumps(_sanitize(outcome))
    assert "paper_candidate" not in text
    assert "untouched" not in text.lower()
    assert '"label": "passes_h1"' not in text


def test_nested_promotion_and_untouched_claims_are_never_published() -> None:
    outcome = _sanitize(
        {
            "promotion_decision": "paper_candidate",
            "nested": [{"promotion_decision": "paper_candidate"}],
            "reason": "Untouched holdout mean net stayed positive under all stresses.",
        }
    )
    text = json.dumps(outcome)
    assert "paper_candidate" not in text
    assert "untouched" not in text.lower()
    assert "exposed historical test" in text


@pytest.mark.parametrize("change", ["venue", "month", "funding", "duplicate"])
def test_unqualified_or_incomplete_manifest_refuses_before_source_reads(
    tmp_path: Path, change: str
) -> None:
    parts = [
        {"path": f"/nonexistent/BTCUSDT_{year}-{month:02}.parquet", "sha256": "0" * 64}
        for year in range(2020, 2027)
        for month in range(1, 13)
        if (year, month) <= (2026, 8)
    ]
    data: dict[str, object] = {
        "format": 1,
        "venue": "binance-usdm",
        "symbol": "BTCUSDT",
        "start_ms": START,
        "end_ms": END,
        "bars": parts,
        "funding": [dict(p, path=p["path"].replace("nonexistent", "funding")) for p in parts],
        "qualification": [],
    }
    if change == "venue":
        data["venue"] = "hyperliquid"
    elif change == "month":
        data["bars"] = parts[:-1]
    elif change == "funding":
        data["funding"] = []
    else:
        data["bars"] = [*parts[:-1], parts[0]]
    path = tmp_path / "inputs.json"
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    with pytest.raises(IntegrityError):
        load_inputs(path, digest(raw))


def test_wrong_manifest_hash_refuses_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(b"not-json")
    with pytest.raises(IntegrityError, match="manifest hash"):
        load_inputs(path, "0" * 64)


@pytest.mark.parametrize("mode", ["LIVE", "SHADOW", "TESTNET"])
def test_research_api_refuses_unapproved_mode_before_loading(
    monkeypatch: pytest.MonkeyPatch, spec: HypothesisSpec, mode: str
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)

    def reject_read(start: int, end: int) -> Stage:
        raise AssertionError("must refuse before data access")

    with pytest.raises(UnsafeTradingModeError):
        evaluate(spec, reject_read)


def test_cli_retains_noncanonical_go_bytes_matching_validation_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import resource
    import subprocess
    from collections.abc import Callable

    spec_path = Path(module.__file__).resolve().parents[2] / module.SPEC_PATH
    spec_bytes = spec_path.read_bytes()
    source = "1" * 40
    inputs = tmp_path / "inputs.json"
    inputs.write_bytes(b"{}\n")
    output = tmp_path / "new-result"
    go = {
        "explicit_go": True,
        "data_acceptance_sha256": "2" * 64,
        "review_sha256": "3" * 64,
        "output": str(output),
        "spec_sha256": spec_sha256(load_document(spec_path)),
        "inputs_sha256": digest(inputs.read_bytes()),
        "source_commit": source,
        "study": module.STUDY,
    }
    go_bytes = b" \n" + json.dumps(go, indent=1).encode() + b"\n\n"
    go_path = tmp_path / "go.json"
    go_path.write_bytes(go_bytes)

    def git_read(argv: list[str], **kwargs: object) -> str | bytes:
        if argv[-1] == "HEAD":
            return source + "\n"
        if "status" in argv:
            return b""
        assert "show" in argv
        return spec_bytes

    def fixture_evaluate(
        spec: HypothesisSpec,
        loader: Callable[[int, int], Stage],
        *,
        freeze_validation: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        assert freeze_validation is not None
        freeze_validation({"admitted_test": False})
        return {"status": "completed", "label": "historical_no_edge"}

    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setattr(subprocess, "check_output", git_read)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(resource, "setrlimit", lambda *a: None)
    monkeypatch.setattr(module, "load_inputs", lambda *a: module.Inputs((), (), ()))
    monkeypatch.setattr(module, "clock_census", lambda *a: {"hours": ROWS})
    monkeypatch.setattr(module, "evaluate", fixture_evaluate)
    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar_research",
            "--inputs",
            str(inputs),
            "--inputs-sha256",
            digest(inputs.read_bytes()),
            "--source-commit",
            source,
            "--review-go",
            str(go_path),
            "--review-go-sha256",
            digest(go_bytes),
            "--output",
            str(output),
        ],
    )
    assert module.main() == 0
    assert (output / "operator-go.json").read_bytes() == go_bytes
    frozen = json.loads((output / "validation.json").read_bytes())
    assert digest((output / "operator-go.json").read_bytes()) == frozen["operator_go_sha256"]
