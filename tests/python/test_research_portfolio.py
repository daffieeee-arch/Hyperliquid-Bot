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


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


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
    # A split that fits the hand-built panels of a few days.
    body["split"] = {"method": "expanding", "train_bars": 1, "test_bars": 1, "holdout_bars": 1}
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
    assert benchmark["method"] is None
    assert _mapping(benchmark["validation"])["status"] == "not_applicable"
    markdown = (tmp_path / "out" / "result.md").read_text(encoding="utf-8")
    assert "## Portfolio" in markdown and "rank at most 12, at least 2 names per leg" in markdown
    periods = validation["periods"]
    assert validation["unwound_periods"] == 0
    assert f"| q25-h4 | 0.25 | validation | {periods} | 0 | 3.0 | 3.0 | 0 | 0 | 0 | 0 |" in markdown
    assert "Buy-and-hold: one unit long" not in markdown
    assert validation["uncovered_funding_days"] == 0
    assert validation["unfunded_halt_days"] == 0
    assert "- validation: not applicable" in markdown
    limitations = " ".join(str(item) for item in _sequence(document["limitations"]))
    assert "A panel portfolio scores one trade" in limitations
    assert "The buy-and-hold benchmark is one unit long" not in limitations
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
    decision = decide_source(source)
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
    assert source.stats[("c", 0, 12)] == PeriodStats(periods=2, long_names=2, short_names=2)


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


def test_an_untraded_exit_day_marks_the_position_at_its_last_traded_close() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 5, [100.0, 100.0, 110.0, 150.0, 150.0]],
        traded=[[True] * 5, [True, True, True, False, False]],
        signals=[[-1.0] * 5, [1.0] * 5],
        funding=[[0.0] * 5, [0.0, 0.0, 0.001, 0.001, 0.001]],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 3), 0, 5)
    # Long B is marked at 110, its last traded close, not at the flat
    # untraded bars at 150; their recorded funding is still paid through the
    # exit day, each day on that day's notional per 100 of entry.
    assert series.gross == pytest.approx((0.05,))
    assert series.funding_paid == pytest.approx((0.5 * 0.001 * (1.1 + 1.5 + 1.5),))
    assert source.stats[("c", 0, 5)].forced_exits == 1


def test_a_halt_that_resumes_before_the_exit_is_held_through_and_pays_funding() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 5, [100.0, 100.0, 110.0, 50.0, 90.0]],
        traded=[[True] * 5, [True, True, True, False, True]],
        signals=[[-1.0] * 5, [1.0] * 5],
        funding=[[0.0] * 5, [0.0, 0.0, 0.001, 0.001, 0.001]],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 3), 0, 5)
    # Long B sits through the halt on day 3 and exits at day 4's close of 90;
    # it pays the halt day's funding too, on that day's notional.
    assert series.gross == pytest.approx((0.5 * -0.10,))
    assert series.funding_paid == pytest.approx((0.5 * 0.001 * (1.1 + 0.5 + 0.9),))
    assert source.stats[("c", 0, 5)].forced_exits == 0


def test_a_symbol_whose_rows_break_before_the_fill_is_not_opened() -> None:
    spec = _four_symbol_spec(
        costs={"fee_bps": 2.0, "slippage_bps": 1.0, "spread_bps": 1.0, "latency_bars": 2}
    )
    panel = _table(
        symbols=["A", "B", "C", "D"],
        prices=[
            [100.0] * 6,
            [100.0, None, 300.0, 310.0, 320.0, 330.0],
            [100.0] * 6,
            [100.0] * 6,
        ],
        signals=[[-1.0] * 6, [2.0] * 6, [1.0] * 6, [-2.0] * 6],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 2), 0, 6)
    # Decided on day 0, filled on day 2: B's row is missing on day 1, so the
    # 300 it trades at on day 2 is another listing. The long leg is C alone
    # and B's later rise is never booked.
    assert series.gross == pytest.approx((0.0,))
    assert series.weights == pytest.approx((0.75,))
    assert source.stats[("c", 0, 6)].long_names == 1


def test_a_halt_day_without_a_rate_pays_nothing_and_counts_as_unfunded() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 5, [100.0, 100.0, 110.0, 50.0, 90.0]],
        traded=[[True] * 5, [True, True, True, False, True]],
        signals=[[-1.0] * 5, [1.0] * 5],
        funding=[[0.0] * 5, [0.0, 0.0, 0.001, None, 0.001]],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 3), 0, 5)
    assert series.funding_paid == pytest.approx((0.5 * 0.001 * (1.1 + 0.9),))
    stats = source.stats[("c", 0, 5)]
    assert (stats.unfunded_halt_days, stats.uncovered_funding_days) == (1, 0)


def test_a_relisting_inside_the_horizon_exits_before_the_gap() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 5, [100.0, 100.0, 90.0, None, 300.0]],
        signals=[[-1.0] * 5, [1.0] * 5],
        funding=[[0.0] * 5, [0.0, 0.0, 0.001, None, 0.001]],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 3), 0, 5)
    # B's row is missing on day 3: the contract ended, and the 300 of day 4 is
    # another listing. Long B exits at 90, its last traded close before the
    # gap, pays funding to that day only, and the period counts a forced exit.
    assert series.gross == pytest.approx((0.5 * -0.10,))
    assert series.funding_paid == pytest.approx((0.5 * 0.001 * 0.9,))
    assert source.stats[("c", 0, 5)].forced_exits == 1


def test_a_panel_rank_must_be_positive_and_unique_on_its_day(tmp_path: Path) -> None:
    rows = _planted_rows(300)
    zero = [
        row if not (row[0] == 10 and row[1] == _SYMBOLS[0]) else (*row[:4], 0, *row[5:])
        for row in rows
    ]
    assert _run_panel(tmp_path / "zero", zero)["failure_kind"] == "schema"
    twin = [
        row if not (row[0] == 10 and row[1] == _SYMBOLS[0]) else (*row[:4], 2, *row[5:])
        for row in rows
    ]
    assert _run_panel(tmp_path / "twin", twin)["failure_kind"] == "duplicate"


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
    # B could not fill: A keeps its decision-time quarter of the capital and
    # B's quarter sits idle, so the period deploys three quarters.
    assert series.gross == pytest.approx((0.25 * 0.10,))
    assert series.weights == pytest.approx((0.75,))
    assert source.stats[("c", 0, 3)] == PeriodStats(periods=1, long_names=1, short_names=2)


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
    # Day 0's short leg cannot fill on day 1, so its long fills (A and B, half
    # the capital) are unwound at the fill close for no return; the next
    # decision is day 1, whose period fills on day 2.
    assert series.gross == pytest.approx((0.0, 0.0))
    assert series.weights == pytest.approx((0.5, 1.0))
    stats = source.stats[("c", 0, 4)]
    assert (stats.periods, stats.skipped_decisions, stats.unwound_periods) == (2, 0, 1)
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
    # D's non-fill leaves the short leg at one name under a floor of two: the
    # three fills are unwound, paying the round trip on three quarters.
    series = PanelSource(floored, thin).window(_config(0.5, 1), 0, 3)
    assert series.gross == pytest.approx((0.0,)) and series.weights == pytest.approx((0.75,))


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


def test_a_traded_day_without_funding_in_reach_of_the_universe_fails_closed() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 4, [100.0] * 4],
        signals=[[1.0] * 4, [-1.0] * 4],
        funding=[[0.0, 0.0, None, 0.0], [0.0] * 4],
    )
    # Refused when the window is scored, whichever config would hold A then:
    # the hole is on day 2, inside the grid's reach; this config's is not.
    with pytest.raises(IntegrityError, match="a traded day a position could hold"):
        PanelSource(spec, panel).window(_config(0.5, 1), 0, 4)


def test_a_row_off_the_date_grid_fails_closed(tmp_path: Path) -> None:
    # Days 0, 2, 4, ... with one row of one symbol stamped at 3: every other
    # symbol would have no row on that day, which would end its contract.
    body = _spec_body()
    _mapping(body["data"])["max_gap"] = 2
    rows = [(row[0] * 2, *row[1:]) for row in _planted_rows(300)]
    rows = [row if not (row[0] == 2 and row[1] == _SYMBOLS[0]) else (3, *row[1:]) for row in rows]
    assert _run_panel(tmp_path, rows, body)["failure_kind"] == "gap"


def test_the_funding_audit_starts_after_the_fill_day() -> None:
    # A is in the universe on day 0 only; at latency 1 a position fills on
    # day 1 and is charged from day 2. Day 1 may lack a rate; day 2 may not.
    spec = _four_symbol_spec(funding=True)
    ranks = [[1, 7, 7, 7, 7, 7], [2] * 6]
    pre_fill = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 6, [100.0] * 6],
        ranks=ranks,
        signals=[[1.0] * 6, [-1.0] * 6],
        funding=[[0.0, None, 0.0, 0.0, 0.0, 0.0], [0.0] * 6],
    )
    PanelSource(spec, pre_fill).window(_config(0.5, 2), 0, 6)
    first_held = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 6, [100.0] * 6],
        ranks=ranks,
        signals=[[1.0] * 6, [-1.0] * 6],
        funding=[[0.0, 0.0, None, 0.0, 0.0, 0.0], [0.0] * 6],
    )
    with pytest.raises(IntegrityError, match="a traded day a position could hold"):
        PanelSource(spec, first_held).window(_config(0.5, 2), 0, 6)


def test_a_relisting_with_late_funding_after_the_gap_is_tolerated() -> None:
    # A is in the universe on day 1, has no row on day 3 and comes back on
    # day 4 without a rate: no position can hold across the gap, so the
    # audit does not reach the new listing's first day.
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0, 100.0, 100.0, None, 100.0, 100.0, 100.0], [100.0] * 7],
        ranks=[[1, 1, 1, None, 7, 7, 7], [2] * 7],
        signals=[[1.0] * 7, [-1.0] * 7],
        funding=[[0.0, 0.0, 0.0, None, None, 0.0, 0.0], [0.0] * 7],
    )
    PanelSource(spec, panel).window(_config(0.5, 2), 0, 7)


def test_a_config_beyond_the_grid_still_fails_closed_on_a_held_day_without_a_rate() -> None:
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 6, [100.0] * 6],
        ranks=[[1, 7, 7, 7, 7, 7], [2] * 6],
        signals=[[1.0] * 6, [-1.0] * 6],
        funding=[[0.0, 0.0, 0.0, 0.0, None, 0.0], [0.0] * 6],
    )
    # A is in the universe on day 0 only; the grid's q50-h2 reaches day 3
    # from it, so the audit passes, while a horizon of 4 holds day 4.
    source = PanelSource(spec, panel)
    with pytest.raises(IntegrityError, match="no funding on a held day"):
        source.window(_config(0.5, 4), 0, 6)


def test_the_funding_audit_spans_the_grid_legs_only() -> None:
    # Six names, quantile 0.34 under a floor of one: legs of two each. C
    # ranks in the middle of the signal on every day, so no leg ever opens
    # it; its missing rate on day 2 is harmless. A, in the long leg, needs
    # one.
    spec = _four_symbol_spec(
        funding=True,
        portfolio={"universe_size": 6, "min_names_per_leg": 1},
        configs=[{"id": "q34-h2", "quantile": 0.34, "horizon_bars": 2}],
    )
    symbols = ["A", "B", "C", "D", "E", "F"]
    signals = [[3.0] * 5, [2.0] * 5, [1.0] * 5, [-1.0] * 5, [-2.0] * 5, [-3.0] * 5]
    middle = _table(
        symbols=symbols,
        prices=[[100.0] * 5] * 6,
        signals=signals,
        funding=[[0.0] * 5, [0.0] * 5, [0.0, 0.0, None, 0.0, 0.0], [0.0] * 5, [0.0] * 5, [0.0] * 5],
    )
    PanelSource(spec, middle).window(_config(0.34, 2), 0, 5)
    top = _table(
        symbols=symbols,
        prices=[[100.0] * 5] * 6,
        signals=signals,
        funding=[[0.0, 0.0, None, 0.0, 0.0], [0.0] * 5, [0.0] * 5, [0.0] * 5, [0.0] * 5, [0.0] * 5],
    )
    with pytest.raises(IntegrityError, match="a traded day a position could hold"):
        PanelSource(spec, top).window(_config(0.34, 2), 0, 5)


def test_the_funding_audit_takes_the_longest_horizon_that_fits_the_window() -> None:
    # Grid horizons 1 and 3 at latency 1, window [0, 10). A is in the
    # universe on day 5 only in the first panel: horizon 3 fits (exit 9), so
    # days 7 to 9 need a rate. In the second it is in on day 6 only: horizon
    # 3 would exit on day 10, outside, so horizon 1 holds day 8 alone.
    spec = _four_symbol_spec(
        funding=True,
        configs=[
            {"id": "q50-h1", "quantile": 0.5, "horizon_bars": 1},
            {"id": "q50-h3", "quantile": 0.5, "horizon_bars": 3},
        ],
    )
    prices = [[100.0] * 10, [100.0] * 10]
    signals = [[1.0] * 10, [-1.0] * 10]
    rates: list[list[float | None]] = [[0.0] * 10, [0.0] * 10]
    rates[0][9] = None
    early = _table(
        symbols=["A", "B"],
        prices=prices,
        ranks=[[7, 7, 7, 7, 7, 1, 7, 7, 7, 7], [2] * 10],
        signals=signals,
        funding=rates,
    )
    with pytest.raises(IntegrityError, match="a traded day a position could hold"):
        PanelSource(spec, early).window(_config(0.5, 3), 0, 10)
    late = _table(
        symbols=["A", "B"],
        prices=prices,
        ranks=[[7, 7, 7, 7, 7, 7, 1, 7, 7, 7], [2] * 10],
        signals=signals,
        funding=rates,
    )
    PanelSource(spec, late).window(_config(0.5, 1), 0, 10)


def test_a_two_config_grid_runs_end_to_end_with_overfitting_diagnostics(tmp_path: Path) -> None:
    body = _spec_body(funding=True)
    body["configs"] = [
        {"id": "q25-h4", "quantile": 0.25, "horizon_bars": 4},
        {"id": "q25-h2", "quantile": 0.25, "horizon_bars": 2},
    ]
    document = _run_panel(tmp_path, _planted_rows(300, funding=0.0001), body)
    assert document["label"] == "passes_h1"
    configs = _sequence(_mapping(document["portfolio"])["configs"])
    assert [_mapping(config)["id"] for config in configs] == ["q25-h4", "q25-h2"]
    pbo = _mapping(_mapping(document["overfitting"])["pbo"])
    assert pbo["value"] is not None


def test_a_traded_day_without_funding_beyond_the_universe_reach_is_tolerated() -> None:
    # The spec's grid is q50-h2 at latency 1: a position opened from day d
    # holds through day d + 3. A is in the universe only on days 0 and 1, so
    # its rate on day 5 is out of reach, and its early days are before any.
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 6, [100.0] * 6],
        ranks=[[1, 1, 7, 7, 7, 7], [2] * 6],
        signals=[[1.0] * 6, [-1.0] * 6],
        funding=[[0.0, 0.0, 0.0, 0.0, 0.0, None], [0.0] * 6],
    )
    source = PanelSource(spec, panel)
    assert source.window(_config(0.5, 2), 0, 6).gross == pytest.approx((0.0,))
    listing = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 6, [100.0] * 6],
        ranks=[[None, None, 1, 1, 1, 1], [2] * 6],
        signals=[[1.0] * 6, [-1.0] * 6],
        funding=[[None, None, 0.0, 0.0, 0.0, 0.0], [0.0] * 6],
    )
    PanelSource(spec, listing).window(_config(0.5, 2), 0, 6)


def test_the_funding_audit_covers_the_scored_windows_only(tmp_path: Path) -> None:
    body = _spec_body(funding=True)
    rows = _planted_rows(300, funding=0.0)
    # Day 5 lies in the warm-up before the first fold (train_bars 30): never
    # decided on, never held, so the run completes.
    warm_up = [
        row if not (row[0] == 5 and row[1] == _SYMBOLS[0]) else (*row[:7], None) for row in rows
    ]
    assert _run_panel(tmp_path / "warm", warm_up, body)["label"] is not None
    # Day 40 lies inside the first fold's test window, within reach.
    held = [
        row if not (row[0] == 40 and row[1] == _SYMBOLS[0]) else (*row[:7], None) for row in rows
    ]
    assert _run_panel(tmp_path / "held", held, body)["failure_kind"] == "funding"


def test_a_period_skipped_at_the_fill_is_followed_by_a_decision_on_the_fill_day() -> None:
    spec = _four_symbol_spec(
        costs={"fee_bps": 2.0, "slippage_bps": 1.0, "spread_bps": 1.0, "latency_bars": 2}
    )
    panel = _table(
        symbols=["A", "B", "C", "D"],
        prices=[[100.0] * 6] * 4,
        traded=[[True] * 6, [True] * 6, [True, True, False, True, True, True]] * 1
        + [[True, True, False, True, True, True]],
        signals=[[2.0] * 6, [1.0] * 6, [-1.0] * 6, [-2.0] * 6],
    )
    source = PanelSource(spec, panel)
    series = source.window(_config(0.5, 2), 0, 6)
    # Day 0's short leg cannot fill on day 2, which is known on day 2 only:
    # the long fills are unwound there and the next decision is day 2,
    # whose exit (day 6) falls outside the window, so no period is held. A
    # decision on day 1 would have been placed with day 2's knowledge.
    assert series.gross == pytest.approx((0.0,)) and series.weights == pytest.approx((0.5,))
    stats = source.stats[("c", 0, 6)]
    assert (stats.periods, stats.skipped_decisions, stats.unwound_periods) == (1, 0, 1)


def test_a_hole_in_the_holdout_fails_the_run_without_a_selected_config(tmp_path: Path) -> None:
    body = _spec_body(funding=True)
    rows = [(*row[:7], 0.0) for row in _noise_rows(300, seed=7)]
    assert _run_panel(tmp_path / "whole", rows, body)["label"] == "no_edge"
    # Holdout_bars 60 of 300 days: day 270 is in the holdout, which noise
    # never reaches through selection; the audit of the split still does.
    holed = [
        row if not (row[0] == 270 and row[1] == _SYMBOLS[0]) else (*row[:7], None) for row in rows
    ]
    assert _run_panel(tmp_path / "holed", holed, body)["failure_kind"] == "funding"


def test_the_funding_audit_skips_a_symbol_that_cannot_fill() -> None:
    # A is in the universe on day 0 but does not trade on the fill day 1, so
    # no position holds it; its missing rate on day 2 is harmless.
    spec = _four_symbol_spec(funding=True)
    panel = _table(
        symbols=["A", "B"],
        prices=[[100.0] * 5, [100.0] * 5],
        traded=[[True, False, True, True, True], [True] * 5],
        ranks=[[1, 7, 7, 7, 7], [2] * 5],
        signals=[[1.0] * 5, [-1.0] * 5],
        funding=[[0.0, 0.0, None, 0.0, 0.0], [0.0] * 5],
    )
    PanelSource(spec, panel).window(_config(0.5, 2), 0, 5)


def test_a_signed_quantile_above_one_half_cannot_be_scored() -> None:
    spec = _four_symbol_spec()
    panel = _table(
        symbols=["A", "B", "C", "D"],
        prices=[[100.0] * 4] * 4,
        signals=[[2.0] * 4, [1.0] * 4, [-1.0] * 4, [-2.0] * 4],
    )
    with pytest.raises(HarnessError, match="overlap"):
        PanelSource(spec, panel).window(_config(0.75, 2), 0, 4)


def test_the_panel_must_match_the_spec_on_funding() -> None:
    panel = _table(symbols=["A"], prices=[[100.0] * 3], signals=[[1.0] * 3])
    with pytest.raises(HarnessError, match="funding"):
        PanelSource(_four_symbol_spec(funding=True), panel)


def test_the_portfolio_block_sums_validation_folds_and_names_the_holdout() -> None:
    spec = _four_symbol_spec(
        split={"method": "expanding", "train_bars": 30, "test_bars": 20, "holdout_bars": 60}
    )
    panel = _table(
        symbols=["A", "B"],
        prices=[
            [100.0 * 1.01**day for day in range(200)],
            [100.0 * 0.99**day for day in range(200)],
        ],
        signals=[[1.0] * 200, [-1.0] * 200],
    )
    source = PanelSource(spec, panel)
    decision = decide_source(source)
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
    # One leg may take the whole universe; two legs must not overlap.
    long_only = _spec_body(direction="long_only")
    long_only["configs"] = [{"id": "q", "quantile": 1.0, "horizon_bars": 4}]
    assert _spec(long_only).configs[0].quantile == 1.0
    long_only["configs"] = [{"id": "q", "quantile": 1.01, "horizon_bars": 4}]
    with pytest.raises(SpecError, match="quantile"):
        _spec(long_only)
    with pytest.raises(SpecError, match="exactly one of threshold and quantile"):
        ConfigSpec(id="both", threshold=0.1, horizon_bars=4, quantile=0.2)
    with pytest.raises(SpecError, match="exactly one of threshold and quantile"):
        ConfigSpec(id="neither", threshold=None, horizon_bars=4)
    listed = _spec_body()
    _mapping(listed["data"])["backend"] = ["panel"]
    with pytest.raises(SpecError, match=r"data\.backend must be"):
        _spec(listed)
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
