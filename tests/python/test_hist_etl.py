"""Fixture tests for the historical archive ETL. No live HTTP."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
from pytest import MonkeyPatch, raises

from research.hist_etl.checksums import parse_checksum
from research.hist_etl.cli import main
from research.hist_etl.errors import HistEtlError
from research.hist_etl.http import HttpBody, build_transport
from research.hist_etl.manifest import load_manifest
from research.hist_etl.models import BINANCE_VISION_BASE, ArchivePlan
from research.hist_etl.pipeline import run_sync
from research.hist_etl.planning import plan_binance, sources_for_month

_VIEW_PATTERN = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+VIEW\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s+"
    r"SELECT\s+\*\s+FROM\s+read_parquet\(\s*'([^']+)'",
    re.IGNORECASE | re.DOTALL,
)
TODAY = date(2026, 10, 6)
REPO_ROOT = Path(__file__).resolve().parents[2]


class BytesResponse:
    def __init__(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.status = status
        self.headers: Mapping[str, str] = {key.lower(): value for key, value in headers.items()}
        self.body = body

    def iter_bytes(self) -> Iterator[bytes]:
        if self.body:
            yield self.body


class MapTransport:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[BytesResponse | Exception]] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def add(self, method: str, url: str, response: BytesResponse | Exception) -> None:
        self.routes.setdefault((method, url), []).append(response)

    @contextmanager
    def open(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[HttpBody]:
        received = dict(headers or {})
        self.calls.append((method, url, received))
        queue = self.routes.get((method, url))
        if queue is None and method == "HEAD":
            origin = self.routes.get(("GET", url))
            if origin:
                item = origin[0]
                if isinstance(item, Exception):
                    raise item
                length = str(len(item.body))
                yield BytesResponse(item.status, {**item.headers, "content-length": length}, b"")
                return
        if not queue:
            yield BytesResponse(404, {}, b"")
            return
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        range_header = received.get("Range")
        if range_header and item.status == 200 and item.body:
            start = int(range_header.removeprefix("bytes=").split("-", 1)[0])
            sliced = item.body[start:]
            yield BytesResponse(
                206,
                {
                    "content-length": str(len(sliced)),
                    "content-range": f"bytes {start}-/{len(item.body)}",
                },
                sliced,
            )
            return
        yield item


def test_checksum_accepts_sha256sum_and_bare_hash() -> None:
    digest = "a" * 64
    assert parse_checksum(f"{digest}  BTCUSDT-1m-2025-09.zip\n", "BTCUSDT-1m-2025-09.zip") == digest
    assert parse_checksum(digest, "BTCUSDT-1m-2025-09.zip") == digest
    with raises(HistEtlError):
        parse_checksum(f"{digest}  OTHER.zip\n", "BTCUSDT-1m-2025-09.zip")


def test_monthly_supersedes_daily_in_the_plan() -> None:
    monthly = _plan_item("monthly", "2025-09")
    daily = _plan_item("daily", "2025-09-01")
    chosen = sources_for_month((daily, monthly))
    assert [item.granularity for item in chosen] == ["monthly"]


def test_default_manifest_publication_and_opt_in_bundle() -> None:
    manifest = load_manifest(REPO_ROOT / "config" / "hist_etl" / "datasets.toml")
    root = Path("/tmp/hist-etl-plan")
    core = plan_binance(
        tuple(spec for spec in manifest.binance if spec.id == "bn-spot-btcusdt-klines-1m"),
        TODAY,
        root,
    )
    names = {item.filename for item in core}
    assert "BTCUSDT-1m-2026-09.zip" in names
    assert "BTCUSDT-1m-2026-10-05.zip" in names
    assert "BTCUSDT-1m-2026-10.zip" not in names
    assert "BTCUSDT-1m-2017-08.zip" not in names
    september = next(item for item in core if item.filename == "BTCUSDT-1m-2026-09.zip")
    assert september.url == (
        f"{BINANCE_VISION_BASE}data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip"
    )
    assert september.checksum_url.endswith(".zip.CHECKSUM")
    backfill = plan_binance(
        tuple(spec for spec in manifest.binance if spec.id == "bn-spot-btcusdt-klines-1m-backfill"),
        TODAY,
        root,
    )
    assert any(item.filename == "BTCUSDT-1m-2017-08.zip" for item in backfill)
    funding = plan_binance(
        tuple(spec for spec in manifest.binance if spec.id == "bn-um-btcusdt-funding-2020"),
        TODAY,
        root,
    )
    funding_url = next(item for item in funding if item.period == "2020-01").url
    assert funding_url.endswith(
        "/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2020-01.zip"
    )
    metrics = plan_binance(
        tuple(spec for spec in manifest.binance if spec.id == "bn-um-btcusdt-metrics"),
        date(2020, 1, 3),
        root,
    )
    assert [item.filename for item in metrics] == [
        "BTCUSDT-metrics-2020-01-01.zip",
        "BTCUSDT-metrics-2020-01-02.zip",
    ]
    mark = plan_binance(
        tuple(spec for spec in manifest.binance if spec.id == "bn-um-btcusdt-mark-1m"),
        date(2020, 2, 4),
        root,
    )
    assert any(
        item.url.endswith("/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2020-01.zip") for item in mark
    )
    enabled = {spec.id for spec in manifest.binance if spec.enabled}
    assert "bn-um-btcusdt-funding-2020" not in enabled
    assert "bn-um-btcusdt-metrics" not in enabled


def test_sync_header_microseconds_and_headerless_milliseconds(tmp_path: Path) -> None:
    us_root = tmp_path / "us"
    us_day = datetime(2025, 9, 1, tzinfo=UTC)
    _sync_klines(
        us_root,
        market="spot",
        unit="us",
        day=us_day,
        header=True,
        end="2025-09-01",
    )
    us_stamp = _one_open_time(us_root, "spot", "klines_1h")
    assert us_stamp == datetime(2025, 9, 1, 0, 0)

    ms_root = tmp_path / "ms"
    ms_day = datetime(2024, 12, 1, tzinfo=UTC)
    _sync_klines(
        ms_root,
        market="spot",
        unit="ms",
        day=ms_day,
        header=False,
        end="2024-12-01",
    )
    assert _one_open_time(ms_root, "spot", "klines_1h") == datetime(2024, 12, 1, 0, 0)


def test_spot_timestamp_unit_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "bad-unit"
    code = _sync_klines(
        root,
        market="spot",
        unit="ms",
        day=datetime(2025, 9, 1, tzinfo=UTC),
        header=False,
        end="2025-09-01",
        full_day=False,
    )
    assert code == 2
    assert not list((root / "parquet").rglob("*.parquet"))
    report = json.loads((root / "logs" / "gap_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert any(gap["kind"] == "schema" for gap in report["gaps"])


def test_um_microseconds_are_accepted(tmp_path: Path) -> None:
    root = tmp_path / "um-us"
    code = _sync_klines(
        root,
        market="um",
        unit="us",
        day=datetime(2025, 9, 1, tzinfo=UTC),
        header=False,
        end="2025-09-01",
    )
    assert code == 0
    assert _one_open_time(root, "um", "klines_1h") == datetime(2025, 9, 1, 0, 0)


def test_kline_hole_and_monthly_values_win(tmp_path: Path) -> None:
    root = tmp_path / "holes"
    day = datetime(2025, 9, 1, tzinfo=UTC)
    lines = [_kline(day + timedelta(hours=hour), "us") for hour in range(24) if hour != 3]
    transport = MapTransport()
    _serve_month(
        transport, "spot", "klines", "1h", "2025-09", "\n".join(lines) + "\n", header=False
    )
    daily = _kline(day, "us", price="999")
    _serve_day(transport, "spot", "klines", "1h", "2025-09-01", daily + "\n")
    code = _run(root, transport, market="spot", dataset="klines", interval="1h", end="2025-09-01")
    assert code == 2
    report = json.loads((root / "logs" / "gap_report.json").read_text(encoding="utf-8"))
    assert any(gap["kind"] == "kline_hole" for gap in report["gaps"])
    closes = _column(root, "spot", "klines_1h", "close")
    assert closes
    assert 999.0 not in closes


def test_duplicate_rows_collapse_and_conflicts_fail(tmp_path: Path) -> None:
    root = tmp_path / "dedupe"
    day = datetime(2025, 9, 1, tzinfo=UTC)
    lines = [_kline(day + timedelta(hours=hour), "us") for hour in range(24)]
    lines.append(_kline(day, "us"))
    transport = MapTransport()
    _serve_month(
        transport, "spot", "klines", "1h", "2025-09", "\n".join(lines) + "\n", header=False
    )
    assert (
        _run(root, transport, market="spot", dataset="klines", interval="1h", end="2025-09-01") == 0
    )
    assert len(_column(root, "spot", "klines_1h", "close")) == 24

    conflict = tmp_path / "conflict"
    bad = [_kline(day + timedelta(hours=hour), "us") for hour in range(24)]
    bad.append(_kline(day, "us", price="50"))
    transport = MapTransport()
    _serve_month(transport, "spot", "klines", "1h", "2025-09", "\n".join(bad) + "\n", header=False)
    assert (
        _run(conflict, transport, market="spot", dataset="klines", interval="1h", end="2025-09-01")
        == 2
    )
    assert not list((conflict / "parquet").rglob("*.parquet"))


def test_skip_present_resume_and_second_sync_is_idle(tmp_path: Path) -> None:
    root = tmp_path / "resume"
    day = datetime(2025, 9, 1, tzinfo=UTC)
    body = "\n".join(_kline(day + timedelta(hours=hour), "us") for hour in range(24)) + "\n"
    transport = MapTransport()
    zip_bytes, _checksum = _serve_month(
        transport, "spot", "klines", "1h", "2025-09", body, header=False
    )
    canonical = (
        root
        / "binance-vision"
        / "data"
        / "spot"
        / "monthly"
        / "klines"
        / "BTCUSDT"
        / "1h"
        / "BTCUSDT-1h-2025-09.zip.partial"
    )
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(zip_bytes[:10])
    assert (
        _run(root, transport, market="spot", dataset="klines", interval="1h", end="2025-09-01") == 0
    )
    assert canonical.with_name("BTCUSDT-1h-2025-09.zip").read_bytes() == zip_bytes
    parquet = next((root / "parquet").rglob("*.parquet"))
    stamp = parquet.stat().st_mtime_ns
    calls = len(transport.calls)
    assert (
        _run(root, transport, market="spot", dataset="klines", interval="1h", end="2025-09-01") == 0
    )
    assert len(transport.calls) == calls
    assert parquet.stat().st_mtime_ns == stamp


def test_checksum_mismatch_keeps_the_zip(tmp_path: Path) -> None:
    root = tmp_path / "mismatch"
    zip_path = (
        root
        / "binance-vision"
        / "data"
        / "spot"
        / "monthly"
        / "klines"
        / "BTCUSDT"
        / "1h"
        / "BTCUSDT-1h-2025-09.zip"
    )
    payload = _zip_bytes("BTCUSDT-1h-2025-09.csv", "1,1,1,1,1,1,2,1,1,1,1,0\n")
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(payload)
    (zip_path.parent / (zip_path.name + ".CHECKSUM")).write_text(
        f"{'b' * 64}  {zip_path.name}\n", encoding="utf-8"
    )
    keeper = root / "binance-vision" / "keep-me.zip"
    keeper.write_bytes(b"keep")
    code = _run(
        root, MapTransport(), market="spot", dataset="klines", interval="1h", end="2025-09-01"
    )
    assert code == 2
    assert zip_path.read_bytes() == payload
    assert keeper.read_bytes() == b"keep"


def test_disk_guard_refuses_before_download(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("research.hist_etl.disk.free_bytes", lambda _path: 0)
    root = tmp_path / "disk"
    manifest = _manifest(tmp_path, market="spot", dataset="klines", interval="1h", end="2025-09-01")
    with raises(HistEtlError) as caught:
        run_sync(
            root=root,
            manifest_path=manifest,
            today=TODAY,
            dataset_ids=None,
            env={},
            dry_run=False,
            transport=MapTransport(),
        )
    assert caught.value.exit_code == 3
    assert not root.exists()


def test_dry_run_prints_sizes_without_writing(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    root = tmp_path / "plan"
    transport = MapTransport()
    zip_bytes, _checksum = _serve_month(
        transport, "spot", "klines", "1h", "2025-09", "1\n", header=False
    )
    monkeypatch.setattr("research.hist_etl.pipeline.build_transport", lambda _timeout: transport)
    manifest = _manifest(tmp_path, market="spot", dataset="klines", interval="1h", end="2025-09-01")
    code = main(
        ["plan", "--root", str(root), "--manifest", str(manifest), "--today", TODAY.isoformat()]
    )
    assert code == 0
    assert not root.exists()
    assert any(
        method == "HEAD" and call_url.endswith("BTCUSDT-1h-2025-09.zip")
        for method, call_url, _headers in transport.calls
    )
    assert len(zip_bytes) > 0


def test_retries_then_downloads(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("research.hist_etl.pipeline.default_sleeper", lambda _seconds: None)
    root = tmp_path / "retry"
    day = datetime(2025, 9, 1, tzinfo=UTC)
    body = "\n".join(_kline(day + timedelta(hours=hour), "us") for hour in range(24)) + "\n"
    transport = MapTransport()
    zip_bytes = _zip_bytes("BTCUSDT-1h-2025-09.csv", body)
    digest = hashlib.sha256(zip_bytes).hexdigest()
    url = f"{BINANCE_VISION_BASE}data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2025-09.zip"
    transport.add("GET", url, BytesResponse(500, {}, b""))
    transport.add(
        "GET", url, BytesResponse(200, {"content-length": str(len(zip_bytes))}, zip_bytes)
    )
    checksum = f"{digest}  BTCUSDT-1h-2025-09.zip\n".encode()
    transport.add(
        "GET",
        url + ".CHECKSUM",
        BytesResponse(200, {"content-length": str(len(checksum))}, checksum),
    )
    assert (
        _run(root, transport, market="spot", dataset="klines", interval="1h", end="2025-09-01") == 0
    )


def test_refuses_data_capture_root(tmp_path: Path) -> None:
    root = tmp_path / "data-capture" / "hist"
    with raises(HistEtlError) as caught:
        _run(root, MapTransport(), market="spot", dataset="klines", interval="1h", end="2025-09-01")
    assert caught.value.exit_code == 2


def test_ambiguous_archives_are_not_deleted(tmp_path: Path) -> None:
    root = tmp_path / "amb"
    first = root / "binance-vision" / "data" / "spot" / "monthly" / "klines" / "BTCUSDT" / "1h"
    second = root / "binance-vision" / "spot" / "klines" / "BTCUSDT" / "1h"
    for directory in (first, second):
        directory.mkdir(parents=True)
        (directory / "BTCUSDT-1h-2025-09.zip").write_bytes(b"zip")
    with raises(HistEtlError):
        _run(root, MapTransport(), market="spot", dataset="klines", interval="1h", end="2025-09-01")
    assert (first / "BTCUSDT-1h-2025-09.zip").read_bytes() == b"zip"
    assert (second / "BTCUSDT-1h-2025-09.zip").read_bytes() == b"zip"


def test_funding_metrics_and_aggtrades(tmp_path: Path) -> None:
    funding_root = tmp_path / "funding"
    start = datetime(2025, 9, 1, tzinfo=UTC)
    rows = []
    for hour in (0, 8, 16):
        stamp = int((start + timedelta(hours=hour)).timestamp() * 1000)
        rows.append(f"{stamp},8,0.0001")
    transport = MapTransport()
    _serve_named(
        transport,
        f"{BINANCE_VISION_BASE}data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2025-09.zip",
        "BTCUSDT-fundingRate-2025-09.csv",
        "\n".join(rows) + "\n",
    )
    assert (
        _run(
            funding_root,
            transport,
            market="um",
            dataset="fundingRate",
            interval=None,
            end="2025-09-01",
            granularity="monthly",
        )
        == 0
    )

    hole_root = tmp_path / "funding-hole"
    sparse = [rows[0], rows[2]]
    transport = MapTransport()
    _serve_named(
        transport,
        f"{BINANCE_VISION_BASE}data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2025-09.zip",
        "BTCUSDT-fundingRate-2025-09.csv",
        "\n".join(sparse) + "\n",
    )
    assert (
        _run(
            hole_root,
            transport,
            market="um",
            dataset="fundingRate",
            interval=None,
            end="2025-09-01",
            granularity="monthly",
        )
        == 2
    )

    metrics_root = tmp_path / "metrics"
    metric_rows = [
        "create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio"
    ]
    cursor = datetime(2020, 1, 1, tzinfo=UTC)
    for index in range(288):
        created = (cursor + timedelta(minutes=5 * index)).strftime("%Y-%m-%d %H:%M:%S")
        metric_rows.append(f"{created},BTCUSDT,1,2,0.5,0.5,0.5,0.5")
    transport = MapTransport()
    _serve_named(
        transport,
        f"{BINANCE_VISION_BASE}data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2020-01-01.zip",
        "BTCUSDT-metrics-2020-01-01.csv",
        "\n".join(metric_rows) + "\n",
    )
    assert (
        _run(
            metrics_root,
            transport,
            market="um",
            dataset="metrics",
            interval=None,
            end="2020-01-01",
            granularity="daily",
            today=date(2020, 1, 2),
        )
        == 0
    )


def test_kraken_pair_filter_sparse_ok_and_conflict(tmp_path: Path) -> None:
    root = tmp_path / "kraken"
    zip_path = root / "kraken-ohlcvt" / "Kraken_OHLCVT_2026Q2.zip"
    zip_path.parent.mkdir(parents=True)
    _write_kraken_zip(
        zip_path,
        {
            "XBTUSD_1440.csv": (
                "1600000000,100,110,90,105,1.5,2\n1600086400,105,112,100,108,1.2,1\n"
            ),
            "ETHUSD_1440.csv": "1600000000,10,11,9,10,1,1\n",
            "MANIFEST.json": "{}\n",
        },
    )
    manifest = _kraken_manifest(tmp_path)
    code = run_sync(
        root=root,
        manifest_path=manifest,
        today=TODAY,
        dataset_ids=None,
        env={},
        dry_run=False,
        transport=MapTransport(),
    )
    assert code == 0
    parquet = root / "parquet" / "kraken" / "ohlcvt" / "XBTUSD" / "1d"
    assert list(parquet.glob("*.parquet"))
    assert not (root / "parquet" / "kraken" / "ohlcvt" / "ETHUSD").exists()
    catalog = (root / "catalog.sql").read_text(encoding="utf-8")
    assert "hist_kr_xbtusd_1d" in catalog
    connection = duckdb.connect(str(root / "research.duckdb"))
    try:
        count = connection.execute("SELECT count(*) FROM hist_kr_xbtusd_1d").fetchone()
    finally:
        connection.close()
    assert count == (2,)

    conflict = tmp_path / "kraken-conflict"
    first = conflict / "kraken-ohlcvt" / "Kraken_OHLCVT_2026Q1.zip"
    second = conflict / "kraken-ohlcvt" / "Kraken_OHLCVT_2026Q2.zip"
    first.parent.mkdir(parents=True)
    _write_kraken_zip(first, {"XBTUSD_1440.csv": "1600000000,100,110,90,105,1,1\n"})
    _write_kraken_zip(second, {"XBTUSD_1440.csv": "1600000000,100,110,90,50,1,1\n"})
    code = run_sync(
        root=conflict,
        manifest_path=_kraken_manifest(tmp_path, name="kraken-conflict.toml"),
        today=TODAY,
        dataset_ids=None,
        env={},
        dry_run=False,
        transport=MapTransport(),
    )
    assert code == 2
    assert not list((conflict / "parquet").rglob("*.parquet"))


def test_catalog_is_idempotent_and_preserves_live_views(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    day = datetime(2025, 9, 1, tzinfo=UTC)
    body = "\n".join(_kline(day + timedelta(hours=hour), "us") for hour in range(24)) + "\n"
    transport = MapTransport()
    _serve_month(transport, "um", "klines", "1h", "2025-09", body, header=False)
    assert (
        _run(root, transport, market="um", dataset="klines", interval="1h", end="2025-09-01") == 0
    )
    catalog = root / "catalog.sql"
    original = catalog.read_text(encoding="utf-8")
    catalog.write_text(
        "CREATE OR REPLACE VIEW live_bn_parts AS\n"
        "SELECT * FROM read_parquet('__DC__/data-1f/example/raw/*.parquet');\n\n"
        "CREATE OR REPLACE VIEW hist_bn_um_klines_1h AS\n"
        "SELECT * FROM read_parquet('__HIST__/old/*.parquet');\n\n" + original,
        encoding="utf-8",
    )
    again = main(
        [
            "catalog",
            "--root",
            str(root),
            "--manifest",
            str(
                _manifest(tmp_path, market="um", dataset="klines", interval="1h", end="2025-09-01")
            ),
        ]
    )
    assert again == 0
    text = catalog.read_text(encoding="utf-8")
    assert text.count("-- BEGIN research.hist_etl") == 1
    assert "live_bn_parts" in text
    assert "old/*.parquet" not in text
    names = _VIEW_PATTERN.findall(text.replace("__HIST__", str(root)))
    assert "hist_bn_um_klines_1h" in {name for name, _path in names}
    third = catalog.read_text(encoding="utf-8")
    main(
        [
            "catalog",
            "--root",
            str(root),
            "--manifest",
            str(
                _manifest(
                    tmp_path,
                    market="um",
                    dataset="klines",
                    interval="1h",
                    end="2025-09-01",
                    filename="again.toml",
                )
            ),
        ]
    )
    assert catalog.read_text(encoding="utf-8") == third


def test_source_has_no_hardcoded_vps_path() -> None:
    root = REPO_ROOT / "src" / "research"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "/home/chupa" not in text
        assert "Hyperliquid Project" not in text


def _sync_klines(
    root: Path,
    *,
    market: str,
    unit: str,
    day: datetime,
    header: bool,
    end: str,
    full_day: bool = True,
) -> int:
    hours = range(24) if full_day else range(1)
    lines = [_kline(day + timedelta(hours=hour), unit) for hour in hours]
    text = "\n".join(lines) + "\n"
    if header:
        text = (
            "open_time,open,high,low,close,volume,close_time,quote asset volume,"
            "number of trades,taker buy base asset volume,taker buy quote asset volume,ignore\n"
            + text
        )
    transport = MapTransport()
    _serve_month(transport, market, "klines", "1h", day.strftime("%Y-%m"), text, header=False)
    return _run(root, transport, market=market, dataset="klines", interval="1h", end=end)


def _run(
    root: Path,
    transport: MapTransport,
    *,
    market: str,
    dataset: str,
    interval: str | None,
    end: str,
    granularity: str = "monthly_with_daily_tail",
    today: date = TODAY,
) -> int:
    manifest = _manifest(
        root,
        market=market,
        dataset=dataset,
        interval=interval,
        end=end,
        granularity=granularity,
    )
    return run_sync(
        root=root,
        manifest_path=manifest,
        today=today,
        dataset_ids=None,
        env={},
        dry_run=False,
        transport=transport,
    )


def _manifest(
    root: Path,
    *,
    market: str,
    dataset: str,
    interval: str | None,
    end: str,
    granularity: str = "monthly_with_daily_tail",
    filename: str = "datasets.toml",
) -> Path:
    interval_line = f'interval = "{interval}"\n' if interval else ""
    text = (
        "min_free_bytes = 1\n"
        "requests_per_second = 0\n"
        "max_retries = 3\n"
        "timeout_seconds = 5\n"
        "[[binance]]\n"
        'id = "sample"\n'
        f'market = "{market}"\n'
        f'dataset = "{dataset}"\n'
        'symbol = "BTCUSDT"\n'
        f"{interval_line}"
        f'start = "{end}"\n'
        f'end = "{end}"\n'
        f'granularity = "{granularity}"\n'
    )
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _kraken_manifest(root: Path, name: str = "kraken.toml") -> Path:
    path = root / name
    path.write_text(
        "min_free_bytes = 1\n"
        "requests_per_second = 0\n"
        "max_retries = 2\n"
        "timeout_seconds = 5\n"
        "[[kraken]]\n"
        'id = "kraken-ohlcvt-xbtusd"\n'
        'pairs = ["XBTUSD"]\n'
        'intervals = ["1d"]\n'
        'zip_glob = "kraken-ohlcvt/Kraken_OHLCVT*.zip"\n',
        encoding="utf-8",
    )
    return path


def _serve_month(
    transport: MapTransport,
    market: str,
    dataset: str,
    interval: str,
    period: str,
    csv_text: str,
    *,
    header: bool,
) -> tuple[bytes, str]:
    del header
    trading = "data/spot" if market == "spot" else "data/futures/um"
    filename = f"BTCUSDT-{interval}-{period}.zip"
    url = f"{BINANCE_VISION_BASE}{trading}/monthly/{dataset}/BTCUSDT/{interval}/{filename}"
    return _serve_named(transport, url, filename.replace(".zip", ".csv"), csv_text)


def _serve_day(
    transport: MapTransport,
    market: str,
    dataset: str,
    interval: str,
    period: str,
    csv_text: str,
) -> None:
    trading = "data/spot" if market == "spot" else "data/futures/um"
    filename = f"BTCUSDT-{interval}-{period}.zip"
    url = f"{BINANCE_VISION_BASE}{trading}/daily/{dataset}/BTCUSDT/{interval}/{filename}"
    _serve_named(transport, url, filename.replace(".zip", ".csv"), csv_text)


def _serve_named(
    transport: MapTransport, url: str, csv_name: str, csv_text: str
) -> tuple[bytes, str]:
    payload = _zip_bytes(csv_name, csv_text)
    digest = hashlib.sha256(payload).hexdigest()
    checksum = f"{digest}  {Path(url).name}\n".encode()
    transport.add("GET", url, BytesResponse(200, {"content-length": str(len(payload))}, payload))
    transport.add(
        "GET",
        url + ".CHECKSUM",
        BytesResponse(200, {"content-length": str(len(checksum))}, checksum),
    )
    transport.add("HEAD", url, BytesResponse(200, {"content-length": str(len(payload))}, b""))
    return payload, digest


def _zip_bytes(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def _write_kraken_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)


def _kline(moment: datetime, unit: str, price: str = "100", interval_s: int = 3600) -> str:
    seconds = int(moment.timestamp())
    if unit == "us":
        opened = seconds * 1_000_000
        closed = opened + interval_s * 1_000_000 - 1
    else:
        opened = seconds * 1_000
        closed = opened + interval_s * 1_000 - 1
    return f"{opened},{price},{price},{price},{price},1,{closed},1,1,1,1,0"


def _one_open_time(root: Path, market: str, slug: str) -> datetime:
    path = next((root / "parquet" / "binance" / market / slug).glob("*.parquet"))
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone='UTC'")
        row = connection.execute(
            "SELECT min(open_time) FROM read_parquet(?)", [str(path)]
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    stamp = row[0]
    assert isinstance(stamp, datetime)
    return stamp


def _column(root: Path, market: str, slug: str, column: str) -> list[float]:
    path = next((root / "parquet" / "binance" / market / slug).glob("*.parquet"))
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            f"SELECT {column} FROM read_parquet(?) ORDER BY 1",
            [str(path)],
        ).fetchall()
    finally:
        connection.close()
    return [float(row[0]) for row in rows]


def _plan_item(granularity: str, period: str) -> ArchivePlan:
    return ArchivePlan(
        dataset_id="sample",
        market="spot",
        dataset="klines",
        symbol="BTCUSDT",
        interval="1h",
        granularity=granularity,
        period=period,
        month=period[:7],
        filename=f"BTCUSDT-1h-{period}.zip",
        url="https://data.binance.vision/example.zip",
        checksum_url="https://data.binance.vision/example.zip.CHECKSUM",
        canonical_relative="binance-vision/example.zip",
        canonical_path=Path("example.zip"),
    )


def test_build_transport_is_real() -> None:
    assert build_transport(1.0).__class__.__name__ == "UrllibTransport"
