"""Small offline archive fixtures for ingest and existing-output financial checks."""

from __future__ import annotations

import hashlib
import zipfile
from datetime import date
from pathlib import Path

import duckdb
import pytest

from research.hist_etl.binance_convert import audit_binance_month, materialize_binance_month
from research.hist_etl.catalog import refresh_catalog
from research.hist_etl.kraken import audit_kraken_tree, ingest_kraken, kraken_parquet_path
from research.hist_etl.models import BinanceSpec, KrakenSpec, SourceDigest

DAY = date(2025, 9, 1)
TODAY = date(2025, 10, 6)
KLINE_FIELDS = (
    (1, "open"),
    (2, "high"),
    (3, "low"),
    (4, "close"),
    (5, "volume"),
    (7, "quote_volume"),
    (9, "taker_buy_base_volume"),
    (10, "taker_buy_quote_volume"),
)
FIELDS = {
    "klines": KLINE_FIELDS,
    "aggTrades": ((1, "price"), (2, "quantity")),
    "fundingRate": ((2, "last_funding_rate"),),
    "metrics": (
        (2, "sum_open_interest"),
        (3, "sum_open_interest_value"),
        (4, "count_toptrader_long_short_ratio"),
        (5, "sum_toptrader_long_short_ratio"),
        (6, "count_long_short_ratio"),
        (7, "sum_taker_long_short_vol_ratio"),
    ),
}
CASES = [(dataset, index, column) for dataset, fields in FIELDS.items() for index, column in fields]


def _spec(dataset: str) -> BinanceSpec:
    return BinanceSpec(
        "sample",
        "um",
        dataset,
        "BTCUSDT",
        "1d" if dataset.endswith("Klines") or dataset == "klines" else None,
        DAY,
        DAY,
        DAY.isoformat(),
        "daily" if dataset == "metrics" else "monthly",
        True,
    )


def _csv(dataset: str) -> list[str]:
    if dataset == "aggTrades":
        return ["1", "100", "1", "1", "1", "1756684800000", "false"]
    if dataset == "fundingRate":
        return ["1756684800000", "", "0.0001"]
    if dataset == "metrics":
        return ["1756684800000", "BTCUSDT", "1", "100", "1", "1", "1", "1"]
    return [
        "1756684800000",
        "100",
        "100",
        "100",
        "100",
        "1",
        "1756771199999",
        "100",
        "1",
        "1",
        "100",
        "0",
    ]


def _source(root: Path, row: list[str], member: str = "BTCUSDT.csv") -> SourceDigest:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "sample.zip"
    # Metrics coverage needs a whole 5-minute day; the other fixtures need
    # one event/daily candle. All bodies stay small and deterministic.
    lines = [row]
    if len(row) == 8 and row[1] == "BTCUSDT":
        lines = [[str(int(row[0]) + index * 300_000), *row[1:]] for index in range(288)]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo(member, date_time=(2020, 1, 1, 0, 0, 0)),
            "\n".join(",".join(line) for line in lines) + "\n",
        )
    return SourceDigest(path.name, hashlib.sha256(path.read_bytes()).hexdigest(), path)


def _binance(root: Path, dataset: str, row: list[str]) -> Path | None:
    source = _source(root, row)
    out, _gaps = materialize_binance_month(
        spec=_spec(dataset),
        month=DAY,
        sources=(source,),
        root=root,
        today=TODAY,
        staging=root / "staging",
    )
    return out


def _edit(path: Path, expression: str) -> None:
    staged = path.with_suffix(".edited")
    connection = duckdb.connect()
    try:
        connection.execute(
            f"CREATE TABLE edited AS SELECT * REPLACE ({expression}) FROM read_parquet(?)",
            [str(path)],
        )
        connection.execute("COPY edited TO ? (FORMAT PARQUET)", [str(staged)])
    finally:
        connection.close()
    staged.replace(path)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("dataset,index,column", CASES)
def test_binance_ingest_refuses_nonfinite_financial_fields(
    tmp_path: Path, dataset: str, index: int, column: str, token: str
) -> None:
    row = _csv(dataset)
    row[index] = token
    assert _binance(tmp_path, dataset, row) is None, column
    assert not list((tmp_path / "parquet").rglob("*.parquet"))


@pytest.mark.parametrize("dataset,index,column", CASES)
def test_binance_audits_each_existing_financial_field(
    tmp_path: Path, dataset: str, index: int, column: str
) -> None:
    del index
    path = _binance(tmp_path, dataset, _csv(dataset))
    assert path is not None
    assert audit_binance_month(spec=_spec(dataset), month=DAY, root=tmp_path, today=TODAY) == ()
    _edit(path, f"CAST('Infinity' AS DOUBLE) AS {column}")
    before = path.read_bytes()
    gaps = audit_binance_month(spec=_spec(dataset), month=DAY, root=tmp_path, today=TODAY)
    assert gaps and gaps[0].kind == "schema"
    source = _source(tmp_path, _csv(dataset))
    _out, gaps = materialize_binance_month(
        spec=_spec(dataset),
        month=DAY,
        sources=(source,),
        root=tmp_path,
        today=TODAY,
        staging=tmp_path / "staging",
    )
    assert gaps and gaps[0].kind == "schema"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "dataset",
    [
        "klines",
        "markPriceKlines",
        "indexPriceKlines",
        "premiumIndexKlines",
        "aggTrades",
        "fundingRate",
        "metrics",
    ],
)
def test_binance_valid_outputs_and_optional_nulls_remain_readable(
    tmp_path: Path, dataset: str
) -> None:
    path = _binance(tmp_path, dataset, _csv(dataset))
    assert path is not None
    assert audit_binance_month(spec=_spec(dataset), month=DAY, root=tmp_path, today=TODAY) == ()
    refresh_catalog(tmp_path, hyperliquid_files={})
    connection = duckdb.connect(str(tmp_path / "research.duckdb"), read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM duckdb_views() WHERE view_name LIKE 'hist_bn_%'"
        ).fetchone() == (1,)
        if dataset == "fundingRate":
            assert connection.execute(
                "SELECT funding_interval_hours FROM read_parquet(?)", [str(path)]
            ).fetchone() == (None,)
        if dataset == "aggTrades":
            assert connection.execute(
                "SELECT is_best_match FROM read_parquet(?)", [str(path)]
            ).fetchone() == (None,)
    finally:
        connection.close()


def _kraken_spec() -> KrakenSpec:
    return KrakenSpec("kraken", ("XBTUSD",), ("1d",), "*.zip", True, None)


@pytest.mark.parametrize(
    "column,index", [("open", 1), ("high", 2), ("low", 3), ("close", 4), ("volume", 5)]
)
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_kraken_ingest_and_existing_output_refuse_nonfinite(
    tmp_path: Path, column: str, index: int, token: str
) -> None:
    row = ["1600000000", "100", "100", "100", "100", "1", "1"]
    bad = row.copy()
    bad[index] = token
    source = _source(tmp_path / "bad", bad, "XBTUSD_1440.csv")
    gaps = ingest_kraken(_kraken_spec(), (source.path,), root=tmp_path / "bad", sources=(source,))
    assert gaps and gaps[0].kind == "kraken_schema"
    source = _source(tmp_path / "good", row, "XBTUSD_1440.csv")
    root = tmp_path / "good"
    assert ingest_kraken(_kraken_spec(), (source.path,), root=root, sources=(source,)) == ()
    assert audit_kraken_tree(_kraken_spec(), root) == ()
    path = kraken_parquet_path(root, "XBTUSD", "1d", "2020-09")
    _edit(path, f"CAST('{token}' AS DOUBLE) AS {column}")
    before = path.read_bytes()
    assert audit_kraken_tree(_kraken_spec(), root)[0].kind == "kraken_schema"
    assert (
        ingest_kraken(_kraken_spec(), (source.path,), root=root, sources=(source,))[0].kind
        == "kraken_schema"
    )
    assert path.read_bytes() == before
