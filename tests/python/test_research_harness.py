"""Synthetic tests for the PAPER research harness.

The planted series is a fixture with a known sign, not a market result.
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import duckdb
import pytest

from research.harness.data import BarTable
from research.harness.errors import HarnessError, IntegrityError, SpecError
from research.harness.evaluate import (
    Trade,
    collect_trades,
    decide,
    position_weight,
    summarize,
    trade_series,
)
from research.harness.run import execute, lock_spec
from research.harness.spec import (
    UNIT_SIZING,
    CostSpec,
    Json,
    SizingSpec,
    SplitSpec,
    load_document,
    spec_sha256,
    validate_spec,
)
from research.harness.splits import walk_forward
from research.harness.stats import (
    benjamini_hochberg,
    bonferroni,
    holm,
    newey_west_mean_test,
    student_t_upper_tail,
)
from research.harness.yaml_subset import loads

_REPO = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO / "docs" / "research" / "examples" / "wp-template.spec.yaml"


def test_student_t_upper_tail_matches_published_quantiles() -> None:
    assert student_t_upper_tail(0.0, 10) == 0.5
    assert student_t_upper_tail(-1.0, 10) > 0.5
    # One-sided 5% critical value, df=10.
    assert math.isclose(student_t_upper_tail(1.8124611228, 10), 0.05, abs_tol=1e-5)
    # One-sided 5% critical value, df=30.
    assert math.isclose(student_t_upper_tail(1.697260887, 30), 0.05, abs_tol=1e-4)


def test_newey_west_lag_and_conservative_gate() -> None:
    t_stat, p_value, lag = newey_west_mean_test([0.01] * 100)
    assert t_stat is None
    assert p_value == 0.0
    assert lag == 4
    _, negative_p, _ = newey_west_mean_test([-0.01] * 100)
    assert negative_p == 1.0
    persistent = ([0.03] * 25 + [-0.01] * 25) * 4
    block = summarize(persistent)
    assert block.naive_p_value is not None
    assert block.hac_p_value is not None
    assert block.hac_p_value > block.naive_p_value
    assert block.p_value == max(block.naive_p_value, block.hac_p_value)


def test_multiple_testing_adjustments() -> None:
    p_values = [0.01, 0.04, 0.03]
    assert bonferroni(p_values) == pytest.approx([0.03, 0.12, 0.09])
    assert holm(p_values) == pytest.approx([0.03, 0.06, 0.06])
    assert benjamini_hochberg(p_values) == pytest.approx([0.03, 0.04, 0.04])


def test_expanding_and_rolling_folds_leave_the_holdout_untouched() -> None:
    expanding, holdout = walk_forward(
        30,
        _split(method="expanding", train_bars=10, test_bars=5, holdout_bars=8),
    )
    rolling, rolling_holdout = walk_forward(
        30,
        _split(method="rolling", train_bars=10, test_bars=5, holdout_bars=8),
    )
    assert holdout == (22, 30)
    assert rolling_holdout == holdout
    assert [fold.test_start for fold in expanding] == [10, 15]
    assert all(fold.train_start == 0 for fold in expanding)
    assert [fold.train_start for fold in rolling] == [0, 5]
    assert all(fold.test_end <= holdout[0] for fold in expanding)
    with pytest.raises(HarnessError, match="holdout_bars") as covered:
        walk_forward(10, _split(method="expanding", train_bars=4, test_bars=2, holdout_bars=10))
    assert covered.value.failure_kind == "split"
    with pytest.raises(HarnessError, match="holdout_bars"):
        walk_forward(10, _split(method="rolling", train_bars=4, test_bars=2, holdout_bars=11))


def test_validation_window_does_not_read_holdout_prices() -> None:
    prices = [100.0 + index for index in range(12)]
    prices[8:] = [0.0] * 4
    feature = [1.0] * 12
    trades = collect_trades(
        feature,
        threshold=0.0,
        horizon_bars=2,
        latency_bars=1,
        direction="signed",
        start=0,
        end=8,
    )
    assert trades
    assert all(trade.exit < 8 for trade in trades)
    returns = trade_series(trades, prices, funding=None, sizing=UNIT_SIZING, vol=None).gross
    assert all(math.isfinite(value) for value in returns)


def test_planted_signal_passes_h1_after_costs(tmp_path: Path) -> None:
    document = _run_rows(tmp_path, _regime_rows(420), configs=_two_configs())
    assert document["status"] == "completed"
    assert document["label"] == "passes_h1"
    assert document["promotion_decision"] == "paper_candidate"
    assert document["selected_config_id"] == "real"
    holdout = _mapping(document["holdout"])
    assert _mapping(holdout["gross"])["mean_return"] is not None
    net = _mapping(holdout["net"])
    assert _as_float(_mapping(net["1.0"])["mean_return"]) > 0.0
    assert _as_float(_mapping(net["2.0"])["mean_return"]) > 0.0
    testing = _mapping(document["multiple_testing"])
    configs = testing["configs"]
    assert isinstance(configs, list)
    assert configs
    first = _mapping(configs[0])
    adjusted = _mapping(first["adjusted_p"])
    assert {"bonferroni", "holm", "bh"} <= set(adjusted)
    markdown = (tmp_path / "out" / "result.md").read_text(encoding="utf-8")
    assert "Gross and net" in markdown
    assert "does not authorize LIVE" in markdown


def test_noise_series_is_no_edge(tmp_path: Path) -> None:
    # Flat prices: the feature has no return to harvest, and costs make net negative.
    document = _run_rows(
        tmp_path,
        _alternating_rows(420, bar_return=0.0),
        fee_bps=5.0,
        slippage_bps=0.0,
        spread_bps=0.0,
    )
    assert document["label"] == "no_edge"
    assert document["promotion_decision"] == "forbidden"
    assert document["selected_config_id"] is None
    assert document["holdout"] is None
    markdown = (tmp_path / "out" / "result.md").read_text(encoding="utf-8")
    assert "holdout sealed (not evaluated)" in markdown
    assert "net 1.0 mean" not in markdown


def test_cost_wipe_is_fragile(tmp_path: Path) -> None:
    document = _run_rows(
        tmp_path,
        _always_long_rows(420, bar_return=0.0001),
        fee_bps=5.0,
        slippage_bps=0.0,
        spread_bps=0.0,
    )
    assert document["label"] == "interesting_but_fragile"
    assert document["promotion_decision"] == "forbidden"
    assert document["selected_config_id"] is None
    assert document["holdout"] is None


def test_holdout_reversal_is_fragile(tmp_path: Path) -> None:
    document = _run_rows(tmp_path, _regime_rows(420, invert_from=336))
    assert document["label"] == "interesting_but_fragile"
    assert document["promotion_decision"] == "forbidden"
    assert document["label"] != "passes_h1"
    assert document["selected_config_id"] == "real"
    holdout = _mapping(document["holdout"])
    assert _mapping(holdout["gross"])["trade_count"] is not None


def test_lookahead_is_fail_closed(tmp_path: Path) -> None:
    document = _run_rows(tmp_path, _regime_rows(420, lookahead=True))
    assert document["status"] == "failed_closed"
    assert document["failure_kind"] == "lookahead"
    assert document["label"] is None
    assert document["promotion_decision"] == "forbidden"


def test_gap_duplicate_and_schema_fail_closed(tmp_path: Path) -> None:
    gap = _run_rows(tmp_path / "gap", _rows_with_timestamps([0, 1, 3]))
    duplicate = _run_rows(tmp_path / "dup", _rows_with_timestamps([0, 1, 1]))
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    _write_integer_close(schema_dir / "bars.parquet", _regime_rows(40))
    spec_path = schema_dir / "spec.json"
    spec_path.write_text(json.dumps(_spec_body(parquet=True)), encoding="utf-8")
    lock_spec(spec_path)
    schema = execute(spec_path, schema_dir / "out").document
    assert gap["failure_kind"] == "gap"
    assert duplicate["failure_kind"] == "duplicate"
    assert schema["failure_kind"] == "schema"
    assert gap["promotion_decision"] == "forbidden"


def test_short_series_is_not_enough_data(tmp_path: Path) -> None:
    document = _run_rows(
        tmp_path,
        _regime_rows(40),
        train_bars=20,
        test_bars=10,
        holdout_bars=15,
        min_folds=2,
    )
    assert document["status"] == "completed"
    assert document["label"] == "not_enough_data"
    assert document["promotion_decision"] == "forbidden"
    assert document["selected_config_id"] is None
    assert document["holdout"] is None


def test_holdout_covering_the_series_fails_closed(tmp_path: Path) -> None:
    document = _run_rows(
        tmp_path,
        _regime_rows(40),
        train_bars=10,
        test_bars=5,
        holdout_bars=40,
        min_folds=1,
    )
    assert document["status"] == "failed_closed"
    assert document["failure_kind"] == "split"
    assert document["label"] is None
    assert "holdout" not in document


def test_zero_latency_on_the_bar_clock_requires_an_explicit_flag(tmp_path: Path) -> None:
    _write_parquet(tmp_path / "bars.parquet", _regime_rows(80))
    spec = _spec_body(parquet=True)
    spec["features"] = [
        {
            "name": "taker_imbalance",
            "column": "taker_imbalance",
            "available_at_column": "ts",
        }
    ]
    costs = _mapping(spec["costs"])
    costs["latency_bars"] = 0
    split = _mapping(spec["split"])
    split["train_bars"] = 20
    split["test_bars"] = 10
    split["holdout_bars"] = 15
    sample = _mapping(spec["sample"])
    sample["min_trades_validation"] = 5
    sample["min_trades_holdout"] = 2
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(HarnessError, match="allow_zero_latency") as caught:
        validate_spec(load_document(spec_path))
    assert caught.value.failure_kind == "spec"
    costs["allow_zero_latency"] = True
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    lock_spec(spec_path)
    outcome = execute(spec_path, tmp_path / "out")
    assert outcome.document["status"] == "completed"
    reported = _mapping(outcome.document["costs"])
    assert reported["allow_zero_latency"] is True
    markdown = (tmp_path / "out" / "result.md").read_text(encoding="utf-8")
    assert "allow_zero_latency: true" in markdown


def test_separate_availability_clock_may_use_zero_latency_without_the_flag() -> None:
    body = _spec_body(parquet=True)
    _mapping(body["costs"])["latency_bars"] = 0
    document = json.loads(json.dumps(body))
    assert isinstance(document, dict)
    spec = validate_spec(document)
    assert spec.costs.latency_bars == 0
    assert spec.costs.allow_zero_latency is False


def test_data_fingerprint_mismatch_requires_relock(tmp_path: Path) -> None:
    spec_path = _write_spec(
        tmp_path,
        _regime_rows(120),
        train_bars=40,
        test_bars=20,
        holdout_bars=20,
        min_trades_validation=5,
        min_trades_holdout=2,
    )
    lock_spec(spec_path)
    lock_payload = json.loads((tmp_path / "spec.json.lock.json").read_text(encoding="utf-8"))
    assert isinstance(lock_payload, dict)
    locked = _mapping(lock_payload["data_fingerprint"])
    _write_parquet(tmp_path / "bars.parquet", _alternating_rows(120, bar_return=0.0))
    refused = execute(spec_path, tmp_path / "refused")
    assert refused.exit_code == 2
    assert refused.document["failure_kind"] == "lock"
    assert refused.document["label"] is None
    lock_spec(spec_path)
    rerun = execute(spec_path, tmp_path / "rerun")
    assert rerun.document["failure_kind"] is None
    fingerprint = _mapping(rerun.document["data_fingerprint"])
    assert fingerprint != locked
    inputs = fingerprint["inputs"]
    assert isinstance(inputs, list) and len(inputs) == 1
    entry = _mapping(inputs[0])
    assert entry["row_count"] == 120
    assert entry["timestamp_min"] == 0
    assert entry["timestamp_max"] == 119
    assert entry["checksum_algorithm"] == "sha256"
    checksum = entry["content_checksum"]
    assert isinstance(checksum, str) and len(checksum) == 64


def test_random_walk_zero_cost_false_positive_rate_stays_near_alpha(tmp_path: Path) -> None:
    """Zero-drift walk, zero costs, one config. Validation rejects stay near alpha.

    Ceiling is 6 of 40. Under a Binomial(40, 0.05) draw, P(X >= 7) is about 0.003,
    so a gate sized at alpha stays under the ceiling. Seeds are fixed.
    """

    survivals = 0
    passes = 0
    for seed in range(40):
        document = _run_rows(
            tmp_path / f"rw-{seed}",
            _random_walk_rows(480, seed),
            fee_bps=0.0,
            slippage_bps=0.0,
            spread_bps=0.0,
            train_bars=80,
            test_bars=40,
            holdout_bars=80,
            min_trades_validation=15,
            min_trades_holdout=8,
        )
        assert document["status"] == "completed"
        if document["selected_config_id"] is not None:
            survivals += 1
        else:
            assert document["holdout"] is None
        if document["label"] == "passes_h1":
            passes += 1
    assert survivals <= 6
    assert passes <= survivals


def test_lock_mismatch_refuses_to_run(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path, _regime_rows(40))
    lock_spec(spec_path)
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["universe"] = "rewritten-after-lock"
    spec_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = execute(spec_path, tmp_path / "out")
    assert outcome.exit_code == 2
    assert outcome.document["failure_kind"] == "lock"


def test_duckdb_view_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "research.duckdb"
    rows = _regime_rows(420)
    connection = duckdb.connect(str(database))
    connection.execute(
        "CREATE TABLE bars (ts BIGINT, close DOUBLE, taker_imbalance DOUBLE, "
        "imbalance_available_ts BIGINT)"
    )
    connection.executemany("INSERT INTO bars VALUES (?, ?, ?, ?)", rows)
    connection.execute("CREATE VIEW hist_bn_um_bars AS SELECT * FROM bars")
    connection.close()
    monkeypatch.setenv("RESEARCH_DUCKDB_PATH", str(database))
    spec = _spec_body(parquet=False)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    lock_spec(spec_path)
    outcome = execute(spec_path, tmp_path / "out")
    assert outcome.document["label"] == "passes_h1"
    assert outcome.document["promotion_decision"] == "paper_candidate"
    fingerprint = _mapping(outcome.document["data_fingerprint"])
    inputs = fingerprint["inputs"]
    assert isinstance(inputs, list) and len(inputs) == 1
    entry = _mapping(inputs[0])
    assert entry["locator"] == "hist_bn_um_bars"
    assert entry["checksum_algorithm"] == "duckdb-row-hash-sha256"
    duckdb_checksum = entry["content_checksum"]
    assert isinstance(duckdb_checksum, str) and len(duckdb_checksum) == 64
    assert entry["row_count"] == 420
    connection = duckdb.connect(str(database))
    connection.execute("INSERT INTO bars VALUES (1000, 101.0, 0.0, 1000)")
    connection.close()
    changed = execute(spec_path, tmp_path / "changed")
    assert changed.document["failure_kind"] == "lock"


def test_lock_refuses_unsafe_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec_path = _write_spec(tmp_path, _regime_rows(40))
    monkeypatch.setenv("TRADING_MODE", "SHADOW")
    with pytest.raises(HarnessError) as caught:
        lock_spec(spec_path)
    assert caught.value.failure_kind == "unsafe_mode"


def test_unsafe_mode_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec_path = _write_spec(tmp_path, _regime_rows(40))
    lock_spec(spec_path)
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    outcome = execute(spec_path, tmp_path / "out")
    assert outcome.exit_code == 2
    assert outcome.document["failure_kind"] == "unsafe_mode"
    assert outcome.document["promotion_decision"] == "forbidden"


def test_yaml_and_json_hash_match(tmp_path: Path) -> None:
    body = _spec_body(parquet=True)
    json_path = tmp_path / "spec.json"
    yaml_path = tmp_path / "spec.yaml"
    json_path.write_text(json.dumps(body), encoding="utf-8")
    yaml_path.write_text(_to_simple_yaml(body), encoding="utf-8")
    assert spec_sha256(load_document(json_path)) == spec_sha256(load_document(yaml_path))


def test_yaml_rejects_tabs_and_flow_collections() -> None:
    with pytest.raises(Exception, match="tabs"):
        loads("alpha:\t0.05\n")
    with pytest.raises(Exception, match="unsupported"):
        loads("costs: {fee_bps: 1}\n")


def test_template_spec_validates_and_cli_hashes_it() -> None:
    document = load_document(_EXAMPLE)
    spec = validate_spec(document)
    assert spec.data.backend == "duckdb"
    assert spec.data.view == "hist_bn_um_bars"
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env.pop("TRADING_MODE", None)
    completed = subprocess.run(
        [sys.executable, "-m", "research.harness", "hash", str(_EXAMPLE)],
        check=False,
        capture_output=True,
        text=True,
        cwd=_REPO,
        env=env,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == spec_sha256(document)


def test_harness_source_does_not_reference_order_entry() -> None:
    banned = ("place_order", "api_secret", "private_key", "hyperliquid_bot.execution")
    for path in (_REPO / "src" / "research").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text


def test_funding_is_charged_over_the_bars_a_trade_is_held() -> None:
    # Held bars are entry+1..exit; bars 0 and 4 carry a rate that must not count.
    prices = (100.0, 100.0, 110.0, 110.0, 100.0)
    funding = (9.0, 0.001, 0.002, 0.003, 9.0)
    trades = (
        Trade(decision=0, entry=1, exit=3, side=1),
        Trade(decision=0, entry=1, exit=3, side=-1),
    )
    series = trade_series(trades, prices, funding=funding, sizing=UNIT_SIZING, vol=None)
    # Each rate is charged on the notional at that bar's close: 110 / 100.
    paid = 0.002 * 1.1 + 0.003 * 1.1
    assert series.gross == pytest.approx((0.1, -0.1))
    assert series.funding == pytest.approx((-paid, paid))
    assert series.funding_paid == pytest.approx((paid, 0.0))
    assert series.funding_received == pytest.approx((0.0, paid))
    assert series.weights == (1.0, 1.0)
    costs = _costs(fee_bps=5.0)  # a 0.001 round trip at 1.0x
    assert series.net(costs, 1.0) == pytest.approx((0.1 - 0.001 - paid, -0.1 - 0.001 + paid))
    # Under stress, funding paid grows and funding received shrinks.
    assert series.net(costs, 2.0) == pytest.approx(
        (0.1 - 0.002 - 2.0 * paid, -0.1 - 0.002 + paid / 2.0)
    )
    no_funding = trade_series(trades, prices, funding=None, sizing=UNIT_SIZING, vol=None)
    assert no_funding.funding == (0.0, 0.0)


def test_funding_stress_applies_to_each_payment_not_the_trade_net() -> None:
    # A long pays 0.01 over four bars and receives 0.009 over four more. Its
    # realized funding is -0.001, but at 2.0x every payment is stressed:
    # -0.02 paid + 0.0045 received, not 2 * -0.001.
    prices = (100.0,) * 10
    funding = (0.0, 0.0, 0.0025, 0.0025, 0.0025, 0.0025, -0.00225, -0.00225, -0.00225, -0.00225)
    trade = Trade(decision=0, entry=1, exit=9, side=1)
    series = trade_series((trade,), prices, funding=funding, sizing=UNIT_SIZING, vol=None)
    assert series.funding == pytest.approx((-0.001,))
    zero_costs = _costs(fee_bps=0.0)
    assert series.net(zero_costs, 1.0) == pytest.approx((-0.001,))
    assert series.net(zero_costs, 2.0) == pytest.approx((-0.02 + 0.0045,))


def test_vol_target_weight_reads_the_decision_bar_and_is_capped() -> None:
    sizing = SizingSpec(method="vol_target", vol_feature="vol", target_vol=0.02, max_leverage=3.0)
    vol = (0.01, 0.04, 0.001, 0.0, -0.01)
    assert position_weight(sizing, vol, 0) == pytest.approx(2.0)
    assert position_weight(sizing, vol, 1) == pytest.approx(0.5)
    assert position_weight(sizing, vol, 2) == 3.0
    # A zero, negative or non-finite volatility never takes the leverage cap.
    for decision in (3, 4):
        with pytest.raises(IntegrityError, match="not positive"):
            position_weight(sizing, vol, decision)
    for bad in (math.nan, math.inf):
        with pytest.raises(IntegrityError, match="not positive"):
            position_weight(sizing, (bad,), 0)
    assert position_weight(UNIT_SIZING, None, 0) == 1.0
    # Price return, cost and funding all scale with the weight.
    prices = (100.0, 100.0, 101.0)
    series = trade_series(
        (Trade(decision=0, entry=1, exit=2, side=1),),
        prices,
        funding=(0.0, 0.0, 0.001),
        sizing=sizing,
        vol=vol,
    )
    assert series.weights == pytest.approx((2.0,))
    assert series.net(_costs(fee_bps=10.0), 1.0) == pytest.approx(
        (2.0 * (0.01 - 0.002 - 0.001 * 1.01),)
    )


def test_spec_validates_funding_and_sizing() -> None:
    document = _spec_body(parquet=True)
    assert validate_spec(_json(document)).sizing == UNIT_SIZING
    assert validate_spec(_json(document)).costs.funding_column is None
    funded = _funding_body()
    assert validate_spec(_json(funded)).costs.funding_column == "funding_rate"
    unnamed = _funding_body()
    del _mapping(unnamed["costs"])["funding_column"]
    with pytest.raises(SpecError, match=r"name it in costs\.funding_column"):
        validate_spec(_json(unnamed))
    wrong_role = _funding_body()
    _mapping(wrong_role["costs"])["funding_column"] = "taker_imbalance"
    with pytest.raises(SpecError, match="funding-role column"):
        validate_spec(_json(wrong_role))
    undeclared = _spec_body(parquet=True)
    _mapping(undeclared["costs"])["funding_column"] = "funding_rate"
    with pytest.raises(SpecError, match="funding-role column"):
        validate_spec(_json(undeclared))
    sized = _sized_body()
    parsed = validate_spec(_json(sized)).sizing
    assert (parsed.method, parsed.vol_feature, parsed.target_vol, parsed.max_leverage) == (
        "vol_target",
        "realized_vol",
        0.02,
        5.0,
    )
    for patch, message in (
        ({"method": "unit", "target_vol": 0.02}, "sizing keys mismatch"),
        ({"vol_feature": "taker_vol"}, "declared feature"),
        ({"target_vol": 0.0}, "target_vol"),
        ({"max_leverage": 101.0}, "max_leverage"),
        ({"method": "kelly"}, "unit or vol_target"),
    ):
        bad = _sized_body()
        _mapping(bad["sizing"]).update(patch)
        if patch.get("method") == "unit":
            for key in ("vol_feature", "max_leverage"):
                del _mapping(bad["sizing"])[key]
        with pytest.raises(SpecError, match=message):
            validate_spec(_json(bad))
    unit = _spec_body(parquet=True)
    unit["sizing"] = {"method": "unit"}
    assert validate_spec(_json(unit)).sizing == UNIT_SIZING
    unknown = _spec_body(parquet=True)
    unknown["leverage"] = 2
    with pytest.raises(SpecError, match="extra"):
        validate_spec(_json(unknown))


def test_latency_floor_covers_the_sizing_volatility() -> None:
    # The signal has its own clock, but the volatility is stamped at the bar
    # close: with zero latency it would size a fill with that bar's own close.
    body = _sized_body()
    _mapping(body["costs"])["latency_bars"] = 0
    with pytest.raises(SpecError, match="realized_vol clock is the bar timestamp"):
        validate_spec(_json(body))
    _mapping(body["costs"])["allow_zero_latency"] = True
    assert validate_spec(_json(body)).costs.latency_bars == 0


def test_decide_audits_the_whole_volatility_series_of_any_table() -> None:
    # A table built without load_bars still gets the whole-series check, even
    # though no trade is decided on the zero row.
    spec = validate_spec(_json(_sized_body()))
    rows = 20
    table = BarTable(
        timestamps=tuple(range(rows)),
        prices=tuple(100.0 for _ in range(rows)),
        features={
            "taker_imbalance": tuple(0.0 for _ in range(rows)),
            "realized_vol": tuple(0.0 if index == rows - 1 else 0.01 for index in range(rows)),
        },
        availability={"imbalance_available_ts": tuple(range(rows))},
    )
    with pytest.raises(IntegrityError, match="must be positive") as refused:
        decide(spec, table)
    assert refused.value.failure_kind == "sizing"
    nan_table = BarTable(
        timestamps=table.timestamps,
        prices=table.prices,
        features={
            "taker_imbalance": table.features["taker_imbalance"],
            "realized_vol": tuple(math.nan if index == rows - 1 else 0.01 for index in range(rows)),
        },
        availability=table.availability,
    )
    with pytest.raises(IntegrityError, match="must be positive"):
        decide(spec, nan_table)


def test_bar_table_without_the_declared_funding_is_refused() -> None:
    spec = validate_spec(_json(_funding_body()))
    table = BarTable(
        timestamps=(0, 1, 2),
        prices=(100.0, 100.0, 100.0),
        features={"taker_imbalance": (1.0, 1.0, 1.0)},
        availability={"imbalance_available_ts": (0, 1, 2)},
        funding=None,
    )
    with pytest.raises(HarnessError, match="funding") as refused:
        decide(spec, table)
    assert refused.value.failure_kind == "invariant"


def test_any_non_positive_volatility_fails_the_run_closed(tmp_path: Path) -> None:
    # Only the last row is zero, where no trade is decided: the audit still
    # refuses the series rather than depending on which configs trade.
    rows = _regime_rows(420)
    vol = [0.01] * (len(rows) - 1) + [0.0]
    document = _run_extended(
        tmp_path,
        rows,
        vol=vol,
        sizing={
            "method": "vol_target",
            "vol_feature": "realized_vol",
            "target_vol": 0.02,
            "max_leverage": 5.0,
        },
    )
    assert document["status"] == "failed_closed"
    assert document["failure_kind"] == "sizing"
    assert document["promotion_decision"] == "forbidden"


def test_spec_without_funding_or_sizing_reports_unit_weight(tmp_path: Path) -> None:
    document = _run_rows(tmp_path, _regime_rows(420), configs=_two_configs())
    assert document["harness_version"] == "3"
    assert document["label"] == "passes_h1"
    assert _mapping(document["costs"])["funding_column"] is None
    assert _mapping(document["sizing"])["method"] == "unit"
    configs = _mapping(document["multiple_testing"])["configs"]
    assert isinstance(configs, list)
    real = _mapping(configs[1])
    assert real["funding"] is None
    assert real["mean_weight"] == 1.0
    holdout = _mapping(document["holdout"])
    assert holdout["funding"] is None
    assert holdout["mean_weight"] == 1.0


def test_funding_paid_on_the_position_wipes_a_planted_edge(tmp_path: Path) -> None:
    # Funding has the sign of each regime, so longs and shorts both pay
    # 0.5% per bar held: more than the planted 0.4% per bar.
    rows = _regime_rows(420)
    funding = [0.005 * feature for _timestamp, _price, feature, _available in rows]
    document = _run_extended(tmp_path, rows, funding=funding, configs=_two_configs())
    assert document["status"] == "completed"
    assert document["label"] == "interesting_but_fragile"
    assert document["promotion_decision"] == "forbidden"
    assert document["selected_config_id"] is None
    assert _mapping(document["costs"])["funding_column"] == "funding_rate"
    configs = _mapping(document["multiple_testing"])["configs"]
    assert isinstance(configs, list)
    real = _mapping(configs[1])
    assert _as_float(_mapping(real["funding"])["mean_return"]) < 0.0
    assert _as_float(_mapping(real["gross"])["mean_return"]) > 0.0
    markdown = (tmp_path / "out" / "result.md").read_text(encoding="utf-8")
    assert "mean funding" in markdown


def test_vol_target_sizing_scales_each_trade(tmp_path: Path) -> None:
    rows = _regime_rows(420)
    unit = _run_extended(tmp_path / "unit", rows, configs=_two_configs())
    sized = _run_extended(
        tmp_path / "sized",
        rows,
        vol=[0.01] * len(rows),
        sizing={
            "method": "vol_target",
            "vol_feature": "realized_vol",
            "target_vol": 0.02,
            "max_leverage": 5.0,
        },
        configs=_two_configs(),
    )
    assert sized["label"] == unit["label"] == "passes_h1"
    unit_configs = _mapping(unit["multiple_testing"])["configs"]
    sized_configs = _mapping(sized["multiple_testing"])["configs"]
    assert isinstance(unit_configs, list) and isinstance(sized_configs, list)
    unit_real = _mapping(unit_configs[1])
    sized_real = _mapping(sized_configs[1])
    assert sized_real["mean_weight"] == pytest.approx(2.0)
    unit_net = _as_float(_mapping(_mapping(unit_real["net"])["1.0"])["mean_return"])
    sized_net = _as_float(_mapping(_mapping(sized_real["net"])["1.0"])["mean_return"])
    assert sized_net == pytest.approx(2.0 * unit_net)
    assert _mapping(sized["sizing"])["vol_feature"] == "realized_vol"
    assert _mapping(sized["holdout"])["mean_weight"] == pytest.approx(2.0)


def test_vol_feature_from_the_future_fails_closed(tmp_path: Path) -> None:
    rows = _regime_rows(420)
    document = _run_extended(
        tmp_path,
        rows,
        vol=[0.01] * len(rows),
        vol_available=[timestamp + 1 for timestamp, _price, _feature, _available in rows],
        sizing={
            "method": "vol_target",
            "vol_feature": "realized_vol",
            "target_vol": 0.02,
            "max_leverage": 5.0,
        },
    )
    assert document["status"] == "failed_closed"
    assert document["failure_kind"] == "lookahead"
    assert document["promotion_decision"] == "forbidden"


def _run_extended(
    tmp_path: Path,
    rows: list[tuple[int, float, float, int]],
    *,
    funding: list[float] | None = None,
    vol: list[float] | None = None,
    vol_available: list[int] | None = None,
    sizing: dict[str, object] | None = None,
    **overrides: object,
) -> dict[str, Json]:
    """Run the fixture with optional funding and volatility columns."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    extra: dict[str, tuple[str, Sequence[object]]] = {}
    spec = _spec_body(parquet=True)
    columns = _mapping(_mapping(spec["data"])["columns"])
    if funding is not None:
        extra["funding_rate"] = ("DOUBLE", funding)
        columns["funding_rate"] = {"dtype": "float64", "role": "funding"}
        _mapping(spec["costs"])["funding_column"] = "funding_rate"
    if vol is not None:
        clock = vol_available or [timestamp for timestamp, _p, _f, _a in rows]
        extra["realized_vol"] = ("DOUBLE", vol)
        extra["vol_available_ts"] = ("BIGINT", clock)
        columns["realized_vol"] = {"dtype": "float64", "role": "feature"}
        columns["vol_available_ts"] = {"dtype": "int64", "role": "availability"}
        features = spec["features"]
        assert isinstance(features, list)
        features.append(
            {
                "name": "realized_vol",
                "column": "realized_vol",
                "available_at_column": "vol_available_ts",
            }
        )
    if sizing is not None:
        spec["sizing"] = sizing
    _apply_overrides(spec, overrides)
    _write_parquet_with(tmp_path / "bars.parquet", rows, extra)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    lock_spec(path)
    return execute(path, tmp_path / "out").document


def _write_parquet_with(
    path: Path,
    rows: Sequence[tuple[int, float, float, int]],
    extra: dict[str, tuple[str, Sequence[object]]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = ["ts", "close", "taker_imbalance", "imbalance_available_ts", *extra]
    types = ["BIGINT", "DOUBLE", "DOUBLE", "BIGINT", *(kind for kind, _ in extra.values())]
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE bars ("
        + ", ".join(f"{name} {kind}" for name, kind in zip(names, types, strict=True))
        + ")"
    )
    values = [
        (*row, *(column[index] for _kind, column in extra.values()))
        for index, row in enumerate(rows)
    ]
    placeholders = ", ".join("?" for _ in names)
    connection.executemany(f"INSERT INTO bars VALUES ({placeholders})", values)
    destination = str(path).replace("'", "''")
    connection.execute(f"COPY bars TO '{destination}' (FORMAT PARQUET)")
    connection.close()


def _funding_body() -> dict[str, object]:
    body = _spec_body(parquet=True)
    _mapping(_mapping(body["data"])["columns"])["funding_rate"] = {
        "dtype": "float64",
        "role": "funding",
    }
    _mapping(body["costs"])["funding_column"] = "funding_rate"
    return body


def _sized_body() -> dict[str, object]:
    body = _spec_body(parquet=True)
    columns = _mapping(_mapping(body["data"])["columns"])
    columns["realized_vol"] = {"dtype": "float64", "role": "feature"}
    features = body["features"]
    assert isinstance(features, list)
    features.append({"name": "realized_vol", "column": "realized_vol", "available_at_column": "ts"})
    body["sizing"] = {
        "method": "vol_target",
        "vol_feature": "realized_vol",
        "target_vol": 0.02,
        "max_leverage": 5.0,
    }
    return body


def _costs(*, fee_bps: float) -> CostSpec:
    return CostSpec(
        fee_bps=fee_bps,
        slippage_bps=0.0,
        spread_bps=0.0,
        latency_bars=1,
        allow_zero_latency=False,
    )


def _json(body: dict[str, object]) -> dict[str, Json]:
    decoded = json.loads(json.dumps(body))
    assert isinstance(decoded, dict)
    return decoded


def _run_rows(
    tmp_path: Path,
    rows: list[tuple[int, float, float, int]],
    **overrides: object,
) -> dict[str, Json]:
    spec_path = _write_spec(tmp_path, rows, **overrides)
    lock_spec(spec_path)
    outcome = execute(spec_path, tmp_path / "out")
    return outcome.document


def _write_spec(
    tmp_path: Path,
    rows: list[tuple[int, float, float, int]],
    **overrides: object,
) -> Path:
    _write_parquet(tmp_path / "bars.parquet", rows)
    spec = _spec_body(parquet=True)
    _apply_overrides(spec, overrides)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _apply_overrides(spec: dict[str, object], overrides: dict[str, object]) -> None:
    costs = _mapping(spec["costs"])
    split = _mapping(spec["split"])
    sample = _mapping(spec["sample"])
    for key in ("fee_bps", "slippage_bps", "spread_bps"):
        if key in overrides:
            costs[key] = overrides.pop(key)
    for key in ("train_bars", "test_bars", "holdout_bars"):
        if key in overrides:
            split[key] = overrides.pop(key)
    for key in ("min_trades_validation", "min_trades_holdout", "min_folds"):
        if key in overrides:
            sample[key] = overrides.pop(key)
    if "configs" in overrides:
        spec["configs"] = overrides.pop("configs")
    if overrides:
        raise AssertionError(sorted(overrides))


def _spec_body(*, parquet: bool) -> dict[str, object]:
    data: dict[str, object]
    if parquet:
        data = {
            "backend": "parquet",
            "parquet_path": "bars.parquet",
            "timestamp_column": "ts",
            "price_column": "close",
            "max_gap": 1,
            "max_rows": 10000,
            "columns": _columns(),
        }
    else:
        data = {
            "backend": "duckdb",
            "view": "hist_bn_um_bars",
            "timestamp_column": "ts",
            "price_column": "close",
            "max_gap": 1,
            "max_rows": 10000,
            "columns": _columns(),
        }
    return {
        "hypothesis_id": "fixture-signal",
        "universe": "synthetic",
        "dataset_version": "fixture",
        "h0": "Mean net return per trade is less than or equal to zero after costs.",
        "h1": "Mean net return per trade is positive after costs on the untouched holdout.",
        "alpha": 0.05,
        "selection_method": "bonferroni",
        "direction": "signed",
        "signal_feature": "taker_imbalance",
        "costs": {"fee_bps": 2.0, "slippage_bps": 1.0, "spread_bps": 1.0, "latency_bars": 1},
        "split": {"method": "expanding", "train_bars": 112, "test_bars": 28, "holdout_bars": 84},
        "sample": {"min_trades_validation": 20, "min_trades_holdout": 10, "min_folds": 2},
        "data": data,
        "features": [
            {
                "name": "taker_imbalance",
                "column": "taker_imbalance",
                "available_at_column": "imbalance_available_ts",
            }
        ],
        "configs": [{"id": "real", "threshold": 0.0, "horizon_bars": 4}],
    }


def _columns() -> dict[str, object]:
    return {
        "ts": {"dtype": "int64", "role": "timestamp"},
        "close": {"dtype": "float64", "role": "price"},
        "taker_imbalance": {"dtype": "float64", "role": "feature"},
        "imbalance_available_ts": {"dtype": "int64", "role": "availability"},
    }


def _two_configs() -> list[dict[str, object]]:
    return [
        {"id": "dead", "threshold": 10.0, "horizon_bars": 4},
        {"id": "real", "threshold": 0.0, "horizon_bars": 4},
    ]


def _regime_rows(
    n_rows: int,
    *,
    invert_from: int | None = None,
    lookahead: bool = False,
) -> list[tuple[int, float, float, int]]:
    price = 100.0
    rows: list[tuple[int, float, float, int]] = []
    for timestamp in range(n_rows):
        regime = 1.0 if (timestamp // 28) % 2 == 0 else -1.0
        sign = -regime if invert_from is not None and timestamp >= invert_from else regime
        if timestamp > 0:
            price *= 1.0 + 0.004 * sign
        available = timestamp + 1 if lookahead else timestamp
        rows.append((timestamp, price, regime, available))
    return rows


def _random_walk_rows(n_rows: int, seed: int) -> list[tuple[int, float, float, int]]:
    rng = random.Random(seed)
    price = 100.0
    rows: list[tuple[int, float, float, int]] = []
    for timestamp in range(n_rows):
        feature = 1.0 if rng.randrange(2) == 0 else -1.0
        if timestamp > 0:
            shock = 0.01 if rng.randrange(2) == 0 else -0.01
            price *= 1.0 + shock
        rows.append((timestamp, price, feature, timestamp))
    return rows


def _alternating_rows(n_rows: int, *, bar_return: float) -> list[tuple[int, float, float, int]]:
    price = 100.0
    rows: list[tuple[int, float, float, int]] = []
    for timestamp in range(n_rows):
        feature = 1.0 if timestamp % 2 == 0 else -1.0
        if timestamp > 0:
            price *= 1.0 + bar_return
        rows.append((timestamp, price, feature, timestamp))
    return rows


def _always_long_rows(n_rows: int, *, bar_return: float) -> list[tuple[int, float, float, int]]:
    price = 100.0
    rows: list[tuple[int, float, float, int]] = []
    for timestamp in range(n_rows):
        if timestamp > 0:
            price *= 1.0 + bar_return
        rows.append((timestamp, price, 1.0, timestamp))
    return rows


def _rows_with_timestamps(timestamps: list[int]) -> list[tuple[int, float, float, int]]:
    return [
        (timestamp, 100.0 + index, 1.0, timestamp) for index, timestamp in enumerate(timestamps)
    ]


def _write_integer_close(path: Path, rows: Sequence[tuple[int, float, float, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE bars (ts BIGINT, close BIGINT, taker_imbalance DOUBLE, "
        "imbalance_available_ts BIGINT)"
    )
    integer_rows = [
        (timestamp, int(price), feature, available) for timestamp, price, feature, available in rows
    ]
    connection.executemany("INSERT INTO bars VALUES (?, ?, ?, ?)", integer_rows)
    destination = str(path).replace("'", "''")
    connection.execute(f"COPY bars TO '{destination}' (FORMAT PARQUET)")
    connection.close()


def _write_parquet(path: Path, rows: Sequence[tuple[int, float, float, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE bars (ts BIGINT, close DOUBLE, taker_imbalance DOUBLE, "
        "imbalance_available_ts BIGINT)"
    )
    connection.executemany("INSERT INTO bars VALUES (?, ?, ?, ?)", list(rows))
    destination = str(path).replace("'", "''")
    connection.execute(f"COPY bars TO '{destination}' (FORMAT PARQUET)")
    connection.close()


def _split(*, method: str, train_bars: int, test_bars: int, holdout_bars: int) -> SplitSpec:
    return SplitSpec(
        method=method,
        train_bars=train_bars,
        test_bars=test_bars,
        holdout_bars=holdout_bars,
    )


def _to_simple_yaml(body: dict[str, object]) -> str:
    """Emit the spec shape the subset parser accepts. Test helper, not a general emitter."""

    lines: list[str] = []

    def emit(key: str, value: object, indent: int) -> None:
        pad = " " * indent
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            for child_key, child in value.items():
                emit(str(child_key), child, indent + 2)
            return
        if isinstance(value, list):
            lines.append(f"{pad}{key}:")
            for item in value:
                if not isinstance(item, dict):
                    raise AssertionError(item)
                first = True
                for child_key, child in item.items():
                    if first:
                        lines.append(f"{pad}  - {child_key}: {_scalar(child)}")
                        first = False
                    else:
                        lines.append(f"{pad}    {child_key}: {_scalar(child)}")
            return
        lines.append(f"{pad}{key}: {_scalar(value)}")

    for top_key, top_value in body.items():
        emit(top_key, top_value, 0)
    return "\n".join(lines) + "\n"


def _scalar(value: object) -> str:
    if isinstance(value, str):
        if any(char in value for char in " #:") or value in {"", "true", "false", "null"}:
            return json.dumps(value)
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return (
            format(value, ".1f")
            if value in {0.0, 2.0, 1.0, 5.0, 4.0} or value.is_integer()
            else repr(value)
        )
    raise AssertionError(type(value))


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _as_float(value: object) -> float:
    assert isinstance(value, int | float) and not isinstance(value, bool)
    return float(value)
