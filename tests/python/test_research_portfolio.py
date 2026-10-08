"""Synthetic tests for the harness's panel portfolio mode. No market data."""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from pathlib import Path

import duckdb
import pytest

from research.harness.costs import round_trip_cost
from research.harness.data import PanelTable, load_panel
from research.harness.errors import HarnessError, IntegrityError, SpecError
from research.harness.evaluate import decide_source
from research.harness.portfolio import PanelSource, PeriodStats, portfolio_block
from research.harness.run import execute, lock_spec
from research.harness.spec import ConfigSpec, HypothesisSpec, Json, validate_spec

_SYMBOLS = tuple(f"S{index:02d}USDT" for index in range(12))


@pytest.fixture(autouse=True)
def _default_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_ENV", raising=False)
    monkeypatch.delenv("RESEARCH_IMAGE_DIGEST", raising=False)


# --- Fixtures -----------------------------------------------------------------


def _spec_body(*, funding: bool = False, direction: str = "signed") -> dict[str, object]:
    columns: dict[str, object] = {
        "ts": {"dtype": "int64", "role": "timestamp"},
        "symbol": {"dtype": "string", "role": "symbol"},
        "close": {"dtype": "float64", "role": "price"},
        "traded": {"dtype": "bool", "role": "traded"},
        "volume_rank": {"dtype": "int64", "role": "rank"},
        "signal": {"dtype": "float64", "role": "feature"},
        "available_ts": {"dtype": "int64", "role": "availability"},
    }
    costs: dict[str, object] = {
        "fee_bps": 2.0,
        "slippage_bps": 1.0,
        "spread_bps": 1.0,
        "latency_bars": 1,
    }
    if funding:
        columns["funding_rate"] = {"dtype": "float64", "role": "funding"}
        columns["funding_covered"] = {"dtype": "bool", "role": "covered"}
        costs["funding_column"] = "funding_rate"
    return {
        "hypothesis_id": "fixture-panel",
        "universe": "synthetic-panel",
        "dataset_version": "fixture",
        "h0": "Mean net return per period is less than or equal to zero after costs.",
        "h1": "Mean net return per period is positive after costs on the untouched holdout.",
        "alpha": 0.05,
        "selection_method": "holm",
        "direction": direction,
        "signal_feature": "signal",
        "costs": costs,
        "split": {"method": "expanding", "train_bars": 30, "test_bars": 20, "holdout_bars": 60},
        "sample": {"min_trades_validation": 10, "min_trades_holdout": 5, "min_folds": 2},
        "data": {
            "backend": "panel",
            "parquet_path": "panel.parquet",
            "timestamp_column": "ts",
            "symbol_column": "symbol",
            "price_column": "close",
            "traded_column": "traded",
            "rank_column": "volume_rank",
            "max_gap": 1,
            "max_rows": 100000,
            "columns": columns,
        },
        "features": [{"name": "signal", "column": "signal", "available_at_column": "available_ts"}],
        "portfolio": {"universe_size": 12, "min_names_per_leg": 2},
        "configs": [{"id": "q25-h4", "quantile": 0.25, "horizon_bars": 4}],
    }


def _spec(body: dict[str, object]) -> HypothesisSpec:
    decoded = json.loads(json.dumps(body))
    assert isinstance(decoded, dict)
    return validate_spec(decoded)


Row = tuple[int, str, float, bool, int | None, float | None, int, float | None]


def _planted_rows(days: int, *, drift: float = 0.01, funding: float | None = None) -> list[Row]:
    """Six winners rise by ``drift`` a day with signal +1; six losers fall with -1."""

    rows: list[Row] = []
    prices = {symbol: 100.0 for symbol in _SYMBOLS}
    for day in range(days):
        for index, symbol in enumerate(_SYMBOLS):
            winner = index < 6
            if day:
                prices[symbol] *= 1.0 + (drift if winner else -drift)
            rows.append(
                (
                    day,
                    symbol,
                    prices[symbol],
                    True,
                    index + 1,
                    1.0 if winner else -1.0,
                    day,
                    funding,
                )
            )
    return rows


def _noise_rows(days: int, seed: int) -> list[Row]:
    rng = random.Random(seed)
    rows: list[Row] = []
    prices = {symbol: 100.0 for symbol in _SYMBOLS}
    for day in range(days):
        for index, symbol in enumerate(_SYMBOLS):
            if day:
                prices[symbol] *= 1.0 + rng.gauss(0.0, 0.01)
            rows.append(
                (day, symbol, prices[symbol], True, index + 1, rng.gauss(0.0, 1.0), day, None)
            )
    return rows


def _write_panel(path: Path, rows: Sequence[Row], *, funding: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE panel (ts BIGINT, symbol VARCHAR, close DOUBLE, traded BOOLEAN, "
        "volume_rank BIGINT, signal DOUBLE, available_ts BIGINT, funding_rate DOUBLE, "
        "funding_covered BOOLEAN)"
    )
    connection.executemany(
        "INSERT INTO panel VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(*row, row[7] is not None) for row in rows],
    )
    kept = "*" if funding else "* EXCLUDE (funding_rate, funding_covered)"
    destination = str(path).replace("'", "''")
    connection.execute(f"COPY (SELECT {kept} FROM panel) TO '{destination}' (FORMAT PARQUET)")
    connection.close()


def _run_panel(
    tmp_path: Path, rows: Sequence[Row], body: dict[str, object] | None = None
) -> dict[str, Json]:
    spec = body or _spec_body()
    funding = "funding_column" in _mapping(spec["costs"])
    _write_panel(tmp_path / "panel.parquet", rows, funding=funding)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    lock_spec(path)
    return execute(path, tmp_path / "out").document


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _first(value: object) -> dict[str, object]:
    assert isinstance(value, list) and value
    return _mapping(value[0])


def _number(value: object) -> float:
    assert isinstance(value, int | float) and not isinstance(value, bool)
    return float(value)


def _table(
    *,
    symbols: Sequence[str],
    prices: Sequence[Sequence[float | None]],
    traded: Sequence[Sequence[bool | None]] | None = None,
    ranks: Sequence[Sequence[int | None]] | None = None,
    signals: Sequence[Sequence[float | None]],
    funding: Sequence[Sequence[float | None]] | None = None,
    covered: Sequence[Sequence[bool | None]] | None = None,
) -> PanelTable:
    """A hand-built panel; a price of None means no row that day.

    With funding, a day is covered exactly when it has a rate, unless
    ``covered`` says otherwise.
    """

    width = len(prices[0])
    present = [[price is not None for price in line] for line in prices]
    return PanelTable(
        timestamps=tuple(range(width)),
        symbols=tuple(symbols),
        prices=tuple(tuple(line) for line in prices),
        traded=tuple(
            tuple(line)
            for line in (
                traded if traded is not None else [[flag or None for flag in p] for p in present]
            )
        ),
        ranks=tuple(
            tuple(line)
            for line in (
                ranks
                if ranks is not None
                else [
                    [index + 1 if flag else None for flag in line]
                    for index, line in enumerate(present)
                ]
            )
        ),
        signals=tuple(tuple(line) for line in signals),
        funding=None if funding is None else tuple(tuple(line) for line in funding),
        covered=(
            None
            if funding is None
            else tuple(
                tuple(line)
                for line in (
                    covered
                    if covered is not None
                    else [[None if rate is None else True for rate in line] for line in funding]
                )
            )
        ),
    )


def _config(quantile: float = 0.5, horizon: int = 2) -> ConfigSpec:
    return ConfigSpec(id="c", threshold=None, horizon_bars=horizon, quantile=quantile)


def _four_symbol_spec(**overrides: object) -> HypothesisSpec:
    body = _spec_body(funding=bool(overrides.pop("funding", False)))
    body["portfolio"] = {"universe_size": 4, "min_names_per_leg": 1}
    # The direct window tests pass their own config; the spec's must validate.
    body["configs"] = [{"id": "q50-h2", "quantile": 0.5, "horizon_bars": 2}]
    for key, value in overrides.items():
        body[key] = value
    return _spec(body)


# --- End to end ---------------------------------------------------------------


def test_planted_cross_section_passes_h1(tmp_path: Path) -> None:
    document = _run_panel(tmp_path, _planted_rows(300))
    assert document["status"] == "completed", document.get("reasons")
    assert document["label"] == "passes_h1"
    assert document["promotion_decision"] == "paper_candidate"
    assert document["selected_config_id"] == "q25-h4"
    holdout = _mapping(document["holdout"])
    assert _number(_mapping(_mapping(holdout["net"])["2.0"])["mean_return"]) > 0.0
    block = _mapping(document["portfolio"])
    assert block["signed"] is True and block["symbol_count"] == 12
    config = _first(block["configs"])
    validation = _mapping(config["validation"])
    # 12 eligible names at quantile 0.25 give 3 per leg, every period.
    assert (validation["mean_long_names"], validation["mean_short_names"]) == (3.0, 3.0)
    assert validation["forced_exits"] == 0 and validation["skipped_decisions"] == 0
    assert "holdout" in config
    benchmark = _mapping(document["benchmark"])
    assert _mapping(benchmark["validation"])["status"] == "not_applicable"
    markdown = (tmp_path / "out" / "result.md").read_text(encoding="utf-8")
    assert "## Portfolio" in markdown and "| q25-h4 | 0.25 | validation |" in markdown
    assert validation["uncovered_funding_days"] == 0
    assert "- validation: not applicable" in markdown
    scored = _first(_mapping(document["multiple_testing"])["configs"])
    assert scored["quantile"] == 0.25 and scored["threshold"] is None


def test_noise_panel_does_not_pass(tmp_path: Path) -> None:
    document = _run_panel(tmp_path, _noise_rows(300, seed=7))
    assert document["status"] == "completed", document.get("reasons")
    assert document["label"] != "passes_h1"
    assert document["promotion_decision"] == "forbidden"
    if document["selected_config_id"] is None:
        assert document["holdout"] is None
        assert "holdout" not in _first(_mapping(document["portfolio"])["configs"])


def test_long_only_holds_the_long_leg_with_all_the_capital(tmp_path: Path) -> None:
    document = _run_panel(tmp_path, _planted_rows(300), _spec_body(direction="long_only"))
    assert document["status"] == "completed", document.get("reasons")
    block = _mapping(document["portfolio"])
    assert block["signed"] is False
    validation = _mapping(_first(block["configs"])["validation"])
    assert (validation["mean_long_names"], validation["mean_short_names"]) == (3.0, 0.0)
    scored = _first(_mapping(document["multiple_testing"])["configs"])
    assert scored["mean_weight"] == 1.0


def test_lookahead_in_the_panel_fails_closed(tmp_path: Path) -> None:
    rows = [(*row[:6], row[0] + 1, row[7]) for row in _planted_rows(300)]
    document = _run_panel(tmp_path, rows)
    assert document["status"] == "failed_closed"
    assert document["failure_kind"] == "lookahead"


def test_every_declared_feature_clock_is_audited(tmp_path: Path) -> None:
    # A second feature, not the signal, stamped after its bar.
    body = _spec_body()
    columns = _mapping(_mapping(body["data"])["columns"])
    columns["other"] = {"dtype": "float64", "role": "feature"}
    columns["other_ts"] = {"dtype": "int64", "role": "availability"}
    features = body["features"]
    assert isinstance(features, list)
    features.append({"name": "other", "column": "other", "available_at_column": "other_ts"})
    rows = _planted_rows(300)
    _write_panel(tmp_path / "panel.parquet", rows, funding=False)
    connection = duckdb.connect()
    path = str(tmp_path / "panel.parquet").replace("'", "''")
    connection.execute(
        f"COPY (SELECT *, 0.0::DOUBLE AS other, ts + 1 AS other_ts FROM read_parquet('{path}')) "
        f"TO '{path}.tmp' (FORMAT PARQUET)"
    )
    connection.close()
    Path(f"{tmp_path / 'panel.parquet'}.tmp").replace(tmp_path / "panel.parquet")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(body), encoding="utf-8")
    lock_spec(spec_path)
    document = execute(spec_path, tmp_path / "out").document
    assert document["failure_kind"] == "lookahead"


def test_duplicate_symbol_day_and_date_gap_fail_closed(tmp_path: Path) -> None:
    rows = _planted_rows(300)
    assert _run_panel(tmp_path / "dup", [*rows, rows[0]])["failure_kind"] == "duplicate"
    gapped = [row for row in rows if row[0] != 150]
    assert _run_panel(tmp_path / "gap", gapped)["failure_kind"] == "gap"


def test_null_price_or_traded_flag_fails_closed(tmp_path: Path) -> None:
    rows = _planted_rows(300)
    _write_panel(tmp_path / "panel.parquet", rows, funding=False)
    connection = duckdb.connect()
    path = str(tmp_path / "panel.parquet").replace("'", "''")
    connection.execute(
        f"COPY (SELECT * REPLACE (CASE WHEN ts = 10 THEN NULL ELSE traded END AS traded) "
        f"FROM read_parquet('{path}')) TO '{path}.tmp' (FORMAT PARQUET)"
    )
    connection.close()
    Path(f"{tmp_path / 'panel.parquet'}.tmp").replace(tmp_path / "panel.parquet")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_spec_body()), encoding="utf-8")
    lock_spec(spec_path)
    document = execute(spec_path, tmp_path / "out").document
    assert document["status"] == "failed_closed"
    assert document["failure_kind"] == "schema"


def test_a_validation_period_never_reads_a_holdout_close(tmp_path: Path) -> None:
    rows = _planted_rows(300)
    _write_panel(tmp_path / "panel.parquet", rows, funding=False)
    spec = _spec(_spec_body())
    panel = load_panel(spec, tmp_path)
    source = PanelSource(spec, panel)
    decision = decide_source(spec, source)
    before = [
        source.window(config, fold.test_start, fold.test_end)
        for config in spec.configs
        for fold in decision.folds
    ]
    # Rewrite every holdout close; validation must not notice.
    changed = [
        (row[0], row[1], row[2] * 3.0 if row[0] >= decision.holdout_start else row[2], *row[3:])
        for row in rows
    ]
    _write_panel(tmp_path / "panel.parquet", changed, funding=False)
    rewritten = PanelSource(spec, load_panel(spec, tmp_path))
    after = [
        rewritten.window(config, fold.test_start, fold.test_end)
        for config in spec.configs
        for fold in decision.folds
    ]
    assert before == after


# --- Period mechanics ---------------------------------------------------------


def test_a_period_is_leg_weighted_and_pays_one_round_trip() -> None:
    # Four symbols; A and B rise, C and D fall. Quantile 0.5: long A,B; short C,D.
    spec = _four_symbol_spec()
    panel = _table(
        symbols=["A", "B", "C", "D"],
        prices=[
            [100.0, 100.0, 110.0, 121.0],
            [100.0, 100.0, 120.0, 120.0],
            [100.0, 100.0, 90.0, 90.0],
            [100.0, 100.0, 80.0, 72.0],
        ],
        signals=[[2.0] * 4, [1.0] * 4, [-1.0] * 4, [-2.0] * 4],
    )
    series = PanelSource(spec, panel).window(_config(0.5, 2), 0, 4)
    # Decided on day 0, filled on day 1, exited on day 3.
    assert len(series.gross) == 1
    long_leg = 0.5 * ((0.21 + 0.20) / 2)
    short_leg = 0.5 * ((-0.10 + -0.28) / 2)
    assert series.gross[0] == pytest.approx(long_leg - short_leg)
    assert series.weights == (1.0,)
    assert series.net(spec.costs, 1.0)[0] == pytest.approx(
        series.gross[0] - round_trip_cost(spec.costs, 1.0)
    )
    assert series.funding_paid == (0.0,) and series.funding_received == (0.0,)


def test_periods_do_not_overlap_and_the_exit_stays_inside_the_window() -> None:
    spec = _four_symbol_spec()
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0 + day for day in range(12)], [100.0 - day for day in range(12)]],
        signals=[[1.0] * 12, [-1.0] * 12],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 3), 0, 12)
    # Decisions at 0, 4, 8 (fill +1, exit +3); the one at 8 would exit at 12.
    assert len(series.gross) == 2
    assert source.stats[("c", 0, 12)] == PeriodStats(2, 0, 2, 2, 0)


def test_a_symbol_that_stops_trading_exits_at_its_last_traded_close() -> None:
    spec = _four_symbol_spec()
    # B is delisted after day 2: no row on day 3 and after.
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0, 100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 90.0, None, None]],
        signals=[[1.0] * 5, [-1.0, -1.0, -1.0, None, None]],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 3), 0, 5)
    # Short B filled at 100 on day 1 and closed at 90 on day 2: +10% on half the capital.
    assert series.gross == pytest.approx((0.05,))
    assert source.stats[("c", 0, 5)].forced_exits == 1


def test_an_untraded_day_also_forces_the_exit() -> None:
    spec = _four_symbol_spec()
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 5, [100.0, 100.0, 110.0, 150.0, 150.0]],
        traded=[[True] * 5, [True, True, True, False, False]],
        signals=[[-1.0] * 5, [1.0] * 5],
    )
    series = PanelSource(spec, panel).window(_config(0.5, 3), 0, 5)
    # Long B exits at 110 on day 2, before the flat untraded bars at 150.
    assert series.gross == pytest.approx((0.05,))


def test_a_symbol_not_trading_on_the_fill_day_is_not_opened() -> None:
    spec = _four_symbol_spec()
    panel = _table(
        symbols=["A", "B", "C", "D"],
        prices=[[100.0, 100.0, 110.0], [100.0, 100.0, 150.0], [100.0] * 3, [100.0] * 3],
        traded=[[True] * 3, [True, False, True], [True] * 3, [True] * 3],
        signals=[[2.0] * 3, [1.0] * 3, [-1.0] * 3, [-2.0] * 3],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 1), 0, 3)
    # B could not fill, so the long leg is A alone at half the capital.
    assert series.gross == pytest.approx((0.05,))
    assert source.stats[("c", 0, 3)] == PeriodStats(1, 0, 1, 2, 0)


def test_a_period_is_skipped_when_a_leg_cannot_fill_or_is_too_small() -> None:
    spec = _four_symbol_spec()
    panel = _table(
        symbols=["A", "B", "C", "D"],
        prices=[[100.0] * 4] * 4,
        traded=[[True] * 4, [True] * 4, [True, False, True, True], [True, False, True, True]],
        signals=[[2.0] * 4, [1.0] * 4, [-1.0] * 4, [-2.0] * 4],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 1), 0, 4)
    # Day 0's short leg cannot fill on day 1; day 1's period fills on day 2.
    assert len(series.gross) == 1
    assert source.stats[("c", 0, 4)].skipped_decisions == 1
    # Three eligible names at quantile 0.5 give one per leg, under a floor of two.
    small = _four_symbol_spec(portfolio={"universe_size": 4, "min_names_per_leg": 2})
    three = _table(
        symbols=["A", "B", "C"],
        prices=[[100.0] * 4] * 3,
        signals=[[2.0] * 4, [1.0] * 4, [-1.0] * 4],
    )
    assert len(PanelSource(small, three).window(_config(0.5, 1), 0, 4).gross) == 0
    # A leg that fills below the floor skips the period too.
    floored = _four_symbol_spec(portfolio={"universe_size": 4, "min_names_per_leg": 2})
    thin = _table(
        symbols=["A", "B", "C", "D"],
        prices=[[100.0] * 3] * 4,
        traded=[[True] * 3, [True] * 3, [True] * 3, [True, False, True]],
        signals=[[2.0] * 3, [1.0] * 3, [-1.0] * 3, [-2.0] * 3],
    )
    assert len(PanelSource(floored, thin).window(_config(0.5, 1), 0, 3).gross) == 0


def test_the_universe_is_read_at_the_decision_day() -> None:
    spec = _four_symbol_spec(portfolio={"universe_size": 2, "min_names_per_leg": 1})
    # C ranks 3 on day 0 (out) and 1 later; A ranks 1 on day 0 and 3 later.
    panel = _table(
        symbols=["A", "B", "C"],
        prices=[[100.0, 100.0, 110.0], [100.0, 100.0, 90.0], [100.0, 100.0, 200.0]],
        ranks=[[1, 3, 3], [2, 2, 2], [3, 1, 1]],
        signals=[[1.0] * 3, [-1.0] * 3, [5.0] * 3],
    )
    series = PanelSource(spec, panel).window(_config(0.5, 1), 0, 3)
    # Long A, short B; C's rank at the decision keeps it out, whatever it did after.
    assert series.gross == pytest.approx((0.5 * 0.10 + 0.5 * 0.10,))


def test_a_null_signal_at_the_decision_makes_a_symbol_ineligible() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B", "C"],
        prices=[[100.0] * 3] * 3,
        signals=[[None, 1.0, 1.0], [None, 1.0, 1.0], [-1.0] * 3],
        funding=[[0.0] * 3, [None, 0.0, 0.0], [0.0] * 3],
    )
    source = PanelSource(spec, panel)
    source.window(_config(0.5, 1), 0, 3)
    # Only C is eligible on day 0: one name, quantile 0.5 gives 0 per leg, skipped.
    assert source.stats[("c", 0, 3)].skipped_decisions == 1


def test_funding_unknown_at_the_decision_does_not_shape_the_universe() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 3, [100.0] * 3],
        signals=[[1.0] * 3, [-1.0] * 3],
        funding=[[None, 0.0, 0.0], [None, 0.0, 0.0]],
    )
    source = PanelSource(spec, panel)
    assert len(source.window(_config(0.5, 1), 0, 3).gross) == 1


def test_the_leg_size_is_the_exact_floor_of_universe_times_quantile() -> None:
    spec = _four_symbol_spec(portfolio={"universe_size": 100, "min_names_per_leg": 1})
    symbols = [f"S{index:03d}" for index in range(100)]
    panel = _table(
        symbols=symbols,
        prices=[[100.0, 100.0, 100.0]] * 100,
        ranks=[[index + 1] * 3 for index in range(100)],
        signals=[[float(100 - index)] * 3 for index in range(100)],
    )
    source = PanelSource(spec, panel)
    # 100 * 0.29 is 28.999999999999996 in binary; the leg still has 29 names.
    source.window(_config(0.29, 1), 0, 3)
    assert source.stats[("c", 0, 3)].long_names == 29


def test_funding_is_paid_by_the_long_leg_and_received_by_the_short_leg() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 100.0, 100.0]],
        signals=[[1.0] * 4, [-1.0] * 4],
        funding=[[0.001] * 4, [0.002] * 4],
    )
    series = PanelSource(spec, panel).window(_config(0.5, 2), 0, 4)
    # Held days 2 and 3: long A pays 2 x 0.001, short B receives 2 x 0.002, each on half.
    assert series.funding_paid == pytest.approx((0.5 * 0.002,))
    assert series.funding_received == pytest.approx((0.5 * 0.004,))
    base = series.net(spec.costs, 1.0)[0]
    stressed = series.net(spec.costs, 2.0)[0]
    assert stressed < base


def test_a_partly_covered_held_day_is_charged_and_counted() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 4, [100.0] * 4],
        signals=[[1.0] * 4, [-1.0] * 4],
        funding=[[0.0001] * 4, [0.0002] * 4],
        covered=[[True, True, False, True], [True] * 4],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 2), 0, 4)
    # Day 2 is charged its recorded rate, and the position-day is counted.
    assert series.funding_paid == pytest.approx((0.5 * 0.0002,))
    assert source.stats[("c", 0, 4)].uncovered_funding_days == 1


def test_a_negative_rate_is_received_by_the_long_leg_and_paid_by_the_short() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 4, [100.0] * 4],
        signals=[[1.0] * 4, [-1.0] * 4],
        funding=[[-0.001] * 4, [-0.002] * 4],
    )
    series = PanelSource(spec, panel).window(_config(0.5, 2), 0, 4)
    assert series.funding_received == pytest.approx((0.5 * 0.002,))
    assert series.funding_paid == pytest.approx((0.5 * 0.004,))
    assert series.net(spec.costs, 2.0)[0] < series.net(spec.costs, 1.0)[0]


def test_a_third_quantile_does_not_round_a_leg_up() -> None:
    spec = _four_symbol_spec(portfolio={"universe_size": 4, "min_names_per_leg": 1})
    panel = _table(
        symbols=["A", "B", "C"],
        prices=[[100.0] * 3] * 3,
        signals=[[1.0] * 3, [0.0] * 3, [-1.0] * 3],
    )
    source = PanelSource(spec, panel)
    # 3 * 0.3333333333 is 0.9999999999: no name, so the day is skipped.
    assert len(source.window(_config(0.3333333333, 1), 0, 3).gross) == 0
    assert source.stats[("c", 0, 3)].skipped_decisions == 1


def test_an_unscored_window_is_an_invariant_error() -> None:
    spec = _four_symbol_spec()
    panel = _table(symbols=["A"], prices=[[100.0] * 3], signals=[[1.0] * 3])
    with pytest.raises(HarnessError, match="was not scored"):
        PanelSource(spec, panel).window_stats("c", [(0, 3)])


def test_a_held_day_without_funding_fails_closed() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 4, [100.0] * 4],
        signals=[[1.0] * 4, [-1.0] * 4],
        funding=[[0.0, 0.0, None, 0.0], [0.0] * 4],
    )
    with pytest.raises(IntegrityError, match="no funding on a held day"):
        PanelSource(spec, panel).window(_config(0.5, 2), 0, 4)


def test_the_panel_must_match_the_spec_on_funding() -> None:
    panel = _table(symbols=["A"], prices=[[100.0] * 3], signals=[[1.0] * 3])
    with pytest.raises(HarnessError, match="funding"):
        PanelSource(_four_symbol_spec(funding=True), panel)


def test_the_portfolio_block_sums_validation_folds_and_names_the_holdout() -> None:
    spec = _four_symbol_spec()
    panel = _table(
        symbols=["A", "B"],
        prices=[
            [100.0 * 1.01**day for day in range(200)],
            [100.0 * 0.99**day for day in range(200)],
        ],
        signals=[[1.0] * 200, [-1.0] * 200],
    )
    source = PanelSource(spec, panel)
    decision = decide_source(spec, source)
    block = portfolio_block(source, decision)
    config = _first(block["configs"])
    validation = _mapping(config["validation"])
    assert validation["periods"] == sum(
        source.stats[("q50-h2", fold.test_start, fold.test_end)].periods for fold in decision.folds
    )
    assert ("holdout" in config) == (decision.holdout_config_id == "q50-h2")


# --- Spec rules ----------------------------------------------------------------


def test_the_panel_template_validates() -> None:
    from research.harness.spec import load_document

    template = (
        Path(__file__).resolve().parents[2] / "docs/research/examples/panel-template.spec.yaml"
    )
    spec = validate_spec(load_document(template))
    assert spec.portfolio is not None and spec.data.backend == "panel"
    assert all(config.quantile == 0.2 for config in spec.configs)


def test_spec_rules_for_the_panel_backend() -> None:
    body = _spec_body()
    assert _spec(body).portfolio is not None
    without = _spec_body()
    del without["portfolio"]
    with pytest.raises(SpecError, match="portfolio is required"):
        _spec(without)
    threshold = _spec_body()
    threshold["configs"] = [{"id": "t", "threshold": 0.1, "horizon_bars": 4}]
    with pytest.raises(SpecError, match="keys mismatch"):
        _spec(threshold)
    for quantile in (0.0, 0.51, 1.0):
        wide = _spec_body()
        wide["configs"] = [{"id": "q", "quantile": quantile, "horizon_bars": 4}]
        with pytest.raises(SpecError, match="quantile"):
            _spec(wide)
    sized = _spec_body()
    _mapping(_mapping(sized["data"])["columns"])["vol"] = {"dtype": "float64", "role": "feature"}
    _mapping(sized)["features"] = [
        *sized["features"],  # type: ignore[misc]
        {"name": "vol", "column": "vol", "available_at_column": "available_ts"},
    ]
    sized["sizing"] = {
        "method": "vol_target",
        "vol_feature": "vol",
        "target_vol": 0.01,
        "max_leverage": 2.0,
    }
    with pytest.raises(SpecError, match="sizing must be unit"):
        _spec(sized)
    leg = _spec_body()
    leg["portfolio"] = {"universe_size": 4, "min_names_per_leg": 5}
    with pytest.raises(SpecError, match="min_names_per_leg"):
        _spec(leg)
    unreachable = _spec_body()
    unreachable["portfolio"] = {"universe_size": 50, "min_names_per_leg": 5}
    unreachable["configs"] = [{"id": "q", "quantile": 0.05, "horizon_bars": 4}]
    with pytest.raises(SpecError, match="fills at most 2 names"):
        _spec(unreachable)
    uncovered = _spec_body(funding=True)
    del _mapping(_mapping(uncovered["data"])["columns"])["funding_covered"]
    with pytest.raises(SpecError, match="covered-role"):
        _spec(uncovered)
    stray = _spec_body()
    _mapping(_mapping(stray["data"])["columns"])["funding_covered"] = {
        "dtype": "bool",
        "role": "covered",
    }
    with pytest.raises(SpecError, match="covered-role"):
        _spec(stray)
    immediate = _spec_body()
    _mapping(immediate["costs"])["latency_bars"] = 0
    with pytest.raises(SpecError, match="latency_bars must be >= 1 on a panel"):
        _spec(immediate)
    _mapping(immediate["costs"])["allow_zero_latency"] = True
    assert _spec(immediate).costs.latency_bars == 0
    wrong_type = _spec_body()
    _mapping(_mapping(wrong_type["data"])["columns"])["traded"] = {
        "dtype": "int64",
        "role": "traded",
    }
    with pytest.raises(SpecError, match="must be bool"):
        _spec(wrong_type)


def test_panel_roles_and_portfolio_are_refused_on_a_bar_backend() -> None:
    body = _spec_body()
    data = _mapping(body["data"])
    data["backend"] = "parquet"
    for key in ("symbol_column", "traded_column", "rank_column"):
        del data[key]
    with pytest.raises(SpecError, match="role must be one of"):
        _spec(body)
