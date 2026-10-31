"""The preregistration helper rejects malformed metadata before publishing gate counts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import duckdb
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docs/experiments/exp_xs_usdm_shape.py"
FIRST = 1577923199999
DAY = 86_400_000
DAYS = 2435


@pytest.fixture(scope="module")
def shape() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s4_shape_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def panel(tmp_path: Path, **changes: str) -> Path:
    fields = {
        "ts": f"({FIRST} + i * {DAY})::BIGINT",
        "symbol": "'BTCUSDT'::VARCHAR",
        "traded": "true",
        "volume_rank": "1::BIGINT",
        "funding_covered": "true",
        "funding_rate": "0.1::DOUBLE",
        "ret_14d": "0.2::DOUBLE",
        "carry_7d": "0.3::DOUBLE",
        "funding_7d": "0.4::DOUBLE",
    }
    tail = changes.pop("tail", "")
    fields.update(changes)
    select = ", ".join(f"{value} AS {name}" for name, value in fields.items())
    path = tmp_path / "panel.parquet"
    connection = duckdb.connect(config={"threads": 1})
    try:
        connection.execute(
            f"COPY (SELECT {select} FROM range({DAYS}) AS t(i) {tail}) TO ? (FORMAT PARQUET)",
            [str(path)],
        )
    finally:
        connection.close()
    return path


def test_valid_complete_axis_preserves_exact_metadata(shape: ModuleType, tmp_path: Path) -> None:
    days, symbols = shape.load(panel(tmp_path))
    assert days == [FIRST + i * DAY for i in range(DAYS)]
    assert symbols == {
        "BTCUSDT": {i: (True, 1, True, False, False, False, False) for i in range(DAYS)}
    }


@pytest.mark.parametrize(
    "field,expression",
    [
        ("ts", f"({FIRST} + i * {DAY})::DOUBLE"),
        ("ts", "true"),
        ("ts", "NULL::BIGINT"),
        ("symbol", "NULL::VARCHAR"),
        ("symbol", "23"),
        ("symbol", "''::VARCHAR"),
        ("symbol", "' BTCUSDT'::VARCHAR"),
        ("symbol", "repeat('X', 129)"),
        ("traded", "'false'::VARCHAR"),
        ("traded", "NULL::BOOLEAN"),
        ("funding_covered", "1"),
        ("funding_covered", "NULL::BOOLEAN"),
        ("volume_rank", "1.9::DOUBLE"),
        ("volume_rank", "true"),
        ("volume_rank", "0::BIGINT"),
        ("volume_rank", "-1::BIGINT"),
    ],
)
def test_malformed_metadata_never_becomes_counts(
    shape: ModuleType, tmp_path: Path, field: str, expression: str
) -> None:
    with pytest.raises(ValueError, match="S4 shape"):
        shape.load(panel(tmp_path, **{field: expression}))


def test_duplicate_day_symbol_cannot_overwrite_metadata(shape: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        shape.load(panel(tmp_path, ts=f"({FIRST} + (i // 2) * {DAY})::BIGINT"))


@pytest.mark.parametrize("tail", ["WHERE i > 0", "WHERE i < 2434", "WHERE i != 100", "WHERE false"])
def test_incomplete_axis_fails_before_fixed_window_counts(
    shape: ModuleType, tmp_path: Path, tail: str
) -> None:
    with pytest.raises(ValueError, match="complete daily timestamp axis"):
        shape.load(panel(tmp_path, tail=tail))


def test_shifted_axis_fails(shape: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="complete daily timestamp axis"):
        shape.load(panel(tmp_path, ts=f"({FIRST} + i * {DAY} + 1)::BIGINT"))


def test_null_rank_is_valid(shape: ModuleType, tmp_path: Path) -> None:
    _, symbols = shape.load(panel(tmp_path, volume_rank="NULL::BIGINT"))
    assert all(cell[1] is None for cell in symbols["BTCUSDT"].values())


def test_row_limit_rejects_instead_of_truncating(
    shape: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shape, "MAX_ROWS", DAYS - 1)
    with pytest.raises(ValueError, match="row bound"):
        shape.load(panel(tmp_path))


def test_financial_values_do_not_change_metadata(shape: ModuleType, tmp_path: Path) -> None:
    first = shape.load(panel(tmp_path))
    second = shape.load(
        panel(
            tmp_path, funding_rate="-123.0", ret_14d="999.0", carry_7d="888.0", funding_7d="777.0"
        )
    )
    assert second == first


@pytest.mark.parametrize(
    "traded,no_rate,uncovered,halt_unfunded,traded_unfunded",
    [
        (False, False, 1, 0, 0),
        (False, True, 0, 1, 0),
        (True, False, 1, 0, 0),
        (True, True, 0, 0, 1),
    ],
)
def test_reach_counts_present_uncovered_rates_on_halts_as_harness_does(
    shape: ModuleType,
    traded: bool,
    no_rate: bool,
    uncovered: int,
    halt_unfunded: int,
    traded_unfunded: int,
) -> None:
    days = [FIRST + i * DAY for i in range(DAYS)]
    rows = {
        371: (True, 1, True, False, False, False, False),
        372: (True, None, True, False, False, False, False),
        373: (traded, None, False, no_rate, False, False, False),
    }
    result = shape.reach({"BTCUSDT": rows}, days)
    assert result["held_days"] == 1
    assert result["uncovered_days"] == uncovered
    assert result["halt_days_without_rate"] == halt_unfunded
    assert result["traded_days_without_rate"] == traded_unfunded
