"""Synthetic tests for the PAPER research harness.

The planted series is a fixture with a known sign, not a market result.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import duckdb
import pytest

from research.harness.errors import HarnessError
from research.harness.evaluate import collect_gross_returns
from research.harness.run import execute, lock_spec
from research.harness.spec import Json, SplitSpec, load_document, spec_sha256, validate_spec
from research.harness.splits import walk_forward
from research.harness.stats import benjamini_hochberg, bonferroni, holm, student_t_upper_tail
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


def test_validation_window_does_not_read_holdout_prices() -> None:
    prices = [100.0 + index for index in range(12)]
    prices[8:] = [0.0] * 4
    feature = [1.0] * 12
    returns = collect_gross_returns(
        feature,
        prices,
        threshold=0.0,
        horizon_bars=2,
        latency_bars=1,
        direction="signed",
        start=0,
        end=8,
    )
    assert returns
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


def test_holdout_reversal_is_fragile(tmp_path: Path) -> None:
    document = _run_rows(tmp_path, _regime_rows(420, invert_from=336))
    assert document["label"] == "interesting_but_fragile"
    assert document["promotion_decision"] == "forbidden"
    assert document["label"] != "passes_h1"


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
