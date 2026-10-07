"""Fixture tests for the Hyperliquid funding history ETL. No live HTTP."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from pytest import CaptureFixture

from research.hist_etl.errors import HistEtlError
from research.hist_etl.http import RateLimiter
from research.hist_etl.hyperliquid import (
    FundingWindow,
    UrllibJsonPoster,
    build_poster,
    fetch_funding,
    funding_windows,
    hyperliquid_parquet_path,
    hyperliquid_rate,
    raw_funding_path,
)
from research.hist_etl.manifest import assert_known_ids, load_manifest
from research.hist_etl.models import HYPERLIQUID_INFO_URL, HyperliquidFundingSpec
from research.hist_etl.pipeline import run_plan, run_sync, run_verify

REPO_ROOT = Path(__file__).resolve().parents[2]
HOUR_MS = 3_600_000


class FakeFundingPoster:
    """Serves hourly prints like fundingHistory: inclusive bounds, a page cap."""

    def __init__(
        self,
        times: list[int],
        *,
        page_size: int = 5,
        rates: dict[int, str] | None = None,
    ) -> None:
        self.times = sorted(times)
        self.page_size = page_size
        self.rates = rates or {}
        self.requests: list[dict[str, object]] = []
        self.queued: list[tuple[int, bytes] | Exception] = []

    def post(self, url: str, payload: bytes) -> tuple[int, bytes]:
        assert url == HYPERLIQUID_INFO_URL
        body = json.loads(payload)
        assert isinstance(body, dict)
        self.requests.append(body)
        if self.queued:
            item = self.queued.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        start = body["startTime"]
        end = body["endTime"]
        assert isinstance(start, int) and isinstance(end, int)
        page = [moment for moment in self.times if start <= moment <= end][: self.page_size]
        rows = [
            {
                "coin": body["coin"],
                "fundingRate": self.rates.get(moment, "0.0000125"),
                "premium": "-0.0002",
                "time": moment,
            }
            for moment in page
        ]
        return 200, json.dumps(rows).encode("utf-8")


def test_manifest_parses_funding_and_refuses_bad_entries(tmp_path: Path) -> None:
    manifest = load_manifest(REPO_ROOT / "config" / "hist_etl" / "datasets.toml")
    by_id = {spec.id: spec for spec in manifest.hyperliquid}
    hourly = by_id["hl-perp-btc-funding"]
    assert hourly.enabled and hourly.funding_interval_hours == 1
    assert hourly.known_holes[0] == datetime(2023, 7, 2, 20, tzinfo=UTC)
    assert not by_id["hl-perp-btc-funding-8h"].enabled
    assert_known_ids(manifest, ("hl-perp-btc-funding-8h",))
    for body, message in (
        ("funding_interval_hours = 5\n", "divide 24"),
        ('known_holes = ["2026-09-01T20:30:00Z"]\n', "not a settlement slot"),
        ('known_holes = ["2026-09-01T20:00:00"]\n', "ending in Z"),
        ('cadence = "1h"\n', "unknown keys"),
    ):
        path = _manifest(tmp_path, start="2026-09-01", extra=body)
        with pytest.raises(HistEtlError, match=message):
            load_manifest(path)
    overlapping = _manifest(tmp_path, start="2026-09-01").read_text(encoding="utf-8") + (
        "[[hyperliquid]]\n"
        'id = "hl-later"\n'
        'dataset = "funding"\n'
        'coin = "BTC"\n'
        'start = "2026-09-15"\n'
        'end = "today"\n'
    )
    (tmp_path / "overlap.toml").write_text(overlapping, encoding="utf-8")
    with pytest.raises(HistEtlError, match="overlap"):
        load_manifest(tmp_path / "overlap.toml")


def test_windows_cover_complete_utc_days_only() -> None:
    spec = _spec(start=date(2026, 9, 15), end=None)
    windows = funding_windows(spec, date(2026, 10, 6))
    assert [(window.month, window.complete) for window in windows] == [
        ("2026-09", True),
        ("2026-10", False),
    ]
    assert windows[0].start_ms == _ms(datetime(2026, 9, 15, tzinfo=UTC))
    assert windows[1].end_ms == _ms(datetime(2026, 10, 6, tzinfo=UTC))
    bounded = funding_windows(
        _spec(start=date(2026, 9, 15), end=date(2026, 9, 20)), date(2026, 10, 6)
    )
    assert [(window.month, window.complete) for window in bounded] == [("2026-09", True)]
    assert bounded[0].end_ms == _ms(datetime(2026, 9, 21, tzinfo=UTC))
    assert funding_windows(spec, date(2026, 9, 15)) == ()


def test_fetch_pages_through_the_window_and_fails_closed() -> None:
    window = _window(datetime(2026, 9, 1, tzinfo=UTC), hours=12)
    times = [*_hourly(datetime(2026, 9, 1, tzinfo=UTC), 12), window.end_ms + 5]
    poster = FakeFundingPoster(times, page_size=5)
    rows = fetch_funding(poster, window, limiter=_limiter(), max_retries=3, sleeper=_no_sleep)
    assert [row["time"] for row in rows] == times[:12]
    assert [request["startTime"] for request in poster.requests[:3]] == [
        window.start_ms,
        times[4] + 1,
        times[9] + 1,
    ]
    assert all(request["endTime"] == window.end_ms - 1 for request in poster.requests)
    assert poster.requests[0]["type"] == "fundingHistory"
    retried = FakeFundingPoster(times[:3], page_size=5)
    retried.queued = [(429, b""), ConnectionResetError("dropped")]
    assert (
        len(fetch_funding(retried, window, limiter=_limiter(), max_retries=3, sleeper=_no_sleep))
        == 3
    )
    for queued, message in (
        ((404, b"{}"), "HTTP 404"),
        ((200, b"not json"), "invalid JSON"),
        ((200, json.dumps([_row(times[1]), _row(times[0])]).encode()), "out of order"),
        ((200, json.dumps([_row(window.end_ms)]).encode()), "outside"),
        ((200, json.dumps([{**_row(times[0]), "fundingRate": "nan"}]).encode()), "non-finite"),
        ((200, json.dumps([{**_row(times[0]), "coin": "ETH"}]).encode()), "coin"),
    ):
        broken = FakeFundingPoster(times, page_size=5)
        broken.queued = [queued]
        with pytest.raises(HistEtlError, match=message):
            fetch_funding(broken, window, limiter=_limiter(), max_retries=1, sleeper=_no_sleep)


def test_sync_writes_months_reuses_settled_ones_and_grows_the_open_one(tmp_path: Path) -> None:
    start = datetime(2026, 9, 29, tzinfo=UTC)
    poster = FakeFundingPoster(_hourly(start, 24 * 4), page_size=50)
    manifest = _manifest(tmp_path, start="2026-09-29")
    assert _sync(tmp_path, manifest, poster, date(2026, 10, 2)) == 0
    september = _window_for(tmp_path, manifest, "2026-09", date(2026, 10, 2))
    october = _window_for(tmp_path, manifest, "2026-10", date(2026, 10, 2))
    assert raw_funding_path(tmp_path, september).name == "BTC-funding-2026-09.json"
    assert raw_funding_path(tmp_path, october).name == "BTC-funding-2026-10.open.json"
    settled = hyperliquid_parquet_path(tmp_path, september)
    assert _sidecar(settled)["provisional"] is False
    assert _sidecar(hyperliquid_parquet_path(tmp_path, october))["provisional"] is True
    assert _view_count(tmp_path) == 72
    columns = _columns(settled)
    assert {"ts", "slot_start", "funding_rate", "funding_rate_text", "premium"} <= columns
    settled_mtime = settled.stat().st_mtime_ns
    raw_bytes = raw_funding_path(tmp_path, september).read_bytes()

    poster.requests.clear()
    assert _sync(tmp_path, manifest, poster, date(2026, 10, 2)) == 0
    # The settled month is read from disk; only the open month is fetched.
    starts = [request["startTime"] for request in poster.requests]
    assert starts[0] == october.start_ms
    assert all(isinstance(start, int) and start >= october.start_ms for start in starts)
    assert settled.stat().st_mtime_ns == settled_mtime

    assert _sync(tmp_path, manifest, poster, date(2026, 10, 3)) == 0
    assert _view_count(tmp_path) == 96
    assert raw_funding_path(tmp_path, september).read_bytes() == raw_bytes
    assert (
        run_verify(
            root=tmp_path, manifest_path=manifest, today=date(2026, 10, 3), dataset_ids=None, env={}
        )
        == 0
    )


def test_holes_are_gaps_unless_acknowledged_and_late_prints_are_not(tmp_path: Path) -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    times = _hourly(start, 48)
    missing = times.pop(20)
    # A settlement that lands 14 minutes into its slot is late, not missing.
    times[30] = times[30] - (times[30] % HOUR_MS) + 14 * 60_000
    manifest = _manifest(tmp_path / "plain", start="2026-09-01", end="2026-09-02")
    poster = FakeFundingPoster(times, page_size=100)
    assert _sync(tmp_path / "plain", manifest, poster, date(2026, 10, 1)) == 2
    report = json.loads((tmp_path / "plain" / "logs" / "gap_report.json").read_text())
    holes = [gap for gap in report["gaps"] if gap["kind"] == "funding_hole"]
    slot = datetime.fromtimestamp((missing - missing % HOUR_MS) / 1000, UTC)
    assert [gap["samples"] for gap in holes] == [[slot.strftime("%Y-%m-%dT%H:%M:%SZ")]]
    assert len(report["gaps"]) == 1
    # The month is still written: a hole is reported, not hidden.
    assert _view_count(tmp_path / "plain") == 47
    acknowledged = _manifest(
        tmp_path / "known",
        start="2026-09-01",
        end="2026-09-02",
        extra=f'known_holes = ["{slot.strftime("%Y-%m-%dT%H:%M:%SZ")}"]\n',
    )
    assert _sync(tmp_path / "known", acknowledged, poster, date(2026, 10, 1)) == 0


def test_two_prints_in_one_slot_refuse_the_month(tmp_path: Path) -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    times = [*_hourly(start, 24), _ms(start) + 5 * HOUR_MS + 600_000]
    manifest = _manifest(tmp_path, start="2026-09-01", end="2026-09-01")
    assert (
        _sync(tmp_path, manifest, FakeFundingPoster(times, page_size=100), date(2026, 10, 1)) == 2
    )
    report = json.loads((tmp_path / "logs" / "gap_report.json").read_text())
    assert [gap["kind"] for gap in report["gaps"]] == ["funding_conflict"]
    window = _window_for(tmp_path, manifest, "2026-09", date(2026, 10, 1))
    assert not hyperliquid_parquet_path(tmp_path, window).exists()


def test_verify_sees_an_edited_raw_file(tmp_path: Path) -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    manifest = _manifest(tmp_path, start="2026-09-01", end="2026-09-01")
    poster = FakeFundingPoster(_hourly(start, 24), page_size=100)
    assert _sync(tmp_path, manifest, poster, date(2026, 10, 1)) == 0
    today = date(2026, 10, 1)
    assert (
        run_verify(root=tmp_path, manifest_path=manifest, today=today, dataset_ids=None, env={})
        == 0
    )
    raw = raw_funding_path(tmp_path, _window_for(tmp_path, manifest, "2026-09", today))
    raw.write_text(raw.read_text(encoding="utf-8").replace("0.0000125", "0.0000126", 1))
    assert (
        run_verify(root=tmp_path, manifest_path=manifest, today=today, dataset_ids=None, env={})
        == 2
    )
    report = json.loads((tmp_path / "logs" / "gap_report.json").read_text())
    assert [gap["kind"] for gap in report["gaps"]] == ["hyperliquid_sidecar"]


def test_plan_lists_fetch_open_and_present_months(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path, start="2026-09-29")
    run_plan(
        root=tmp_path, manifest_path=manifest, today=date(2026, 10, 2), dataset_ids=None, env={}
    )
    lines = [line for line in capsys.readouterr().out.splitlines() if "hl-test" in line]
    assert [line.split("\t")[1:4] for line in lines] == [
        ["download", "hl-test", "BTC-funding-2026-09.json"],
        ["open", "hl-test", "BTC-funding-2026-10.open.json"],
    ]
    start = datetime(2026, 9, 29, tzinfo=UTC)
    _sync(
        tmp_path, manifest, FakeFundingPoster(_hourly(start, 72), page_size=100), date(2026, 10, 2)
    )
    capsys.readouterr()
    run_plan(
        root=tmp_path, manifest_path=manifest, today=date(2026, 10, 2), dataset_ids=None, env={}
    )
    lines = [line for line in capsys.readouterr().out.splitlines() if "hl-test" in line]
    assert lines[0].split("\t")[1] == "present"


def test_request_rate_stays_inside_the_venue_weight_budget() -> None:
    assert hyperliquid_rate(2.0) == 0.4
    assert hyperliquid_rate(0.2) == 0.2
    assert hyperliquid_rate(0.0) == 0.0
    assert isinstance(build_poster(5.0), UrllibJsonPoster)


def _sync(root: Path, manifest: Path, poster: FakeFundingPoster, today: date) -> int:
    return run_sync(
        root=root,
        manifest_path=manifest,
        today=today,
        dataset_ids=None,
        env={},
        dry_run=False,
        poster=poster,
    )


def _manifest(root: Path, *, start: str, end: str = "today", extra: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "datasets.toml"
    path.write_text(
        "min_free_bytes = 1\n"
        "requests_per_second = 0\n"
        "max_retries = 3\n"
        "timeout_seconds = 5\n"
        "[[hyperliquid]]\n"
        'id = "hl-test"\n'
        'dataset = "funding"\n'
        'coin = "BTC"\n'
        f'start = "{start}"\n'
        f'end = "{end}"\n'
        f"{extra}",
        encoding="utf-8",
    )
    return path


def _spec(*, start: date, end: date | None) -> HyperliquidFundingSpec:
    return HyperliquidFundingSpec(
        id="hl-test",
        coin="BTC",
        start=start,
        end=end,
        end_token="today" if end is None else end.isoformat(),
        funding_interval_hours=1,
        known_holes=(),
        enabled=True,
    )


def _window(start: datetime, *, hours: int) -> FundingWindow:
    return FundingWindow(
        dataset_id="hl-test",
        coin="BTC",
        month=start.strftime("%Y-%m"),
        start_ms=_ms(start),
        end_ms=_ms(start + timedelta(hours=hours)),
        complete=True,
    )


def _window_for(root: Path, manifest: Path, month: str, today: date) -> FundingWindow:
    spec = load_manifest(manifest).hyperliquid[0]
    return next(window for window in funding_windows(spec, today) if window.month == month)


def _hourly(start: datetime, count: int) -> list[int]:
    """Hourly settlements with the venue's sub-second jitter."""

    base = _ms(start)
    return [base + index * HOUR_MS + (index * 37) % 900 for index in range(count)]


def _row(moment: int) -> dict[str, object]:
    return {"coin": "BTC", "fundingRate": "0.0000125", "premium": "-0.0002", "time": moment}


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _limiter() -> RateLimiter:
    return RateLimiter(0.0, _no_sleep)


def _no_sleep(seconds: float) -> None:
    del seconds


def _sidecar(parquet: Path) -> dict[str, object]:
    payload = json.loads(parquet.with_name(parquet.name + ".sources.json").read_text())
    assert isinstance(payload, dict)
    return payload


def _view_count(root: Path) -> int:
    connection = duckdb.connect(str(root / "research.duckdb"), read_only=True)
    try:
        row = connection.execute("SELECT count(*) FROM hist_hl_funding_btc").fetchone()
    finally:
        connection.close()
    assert row is not None
    return int(row[0])


def _columns(parquet: Path) -> set[str]:
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            "SELECT name FROM parquet_schema(?) WHERE name <> 'duckdb_schema'", [str(parquet)]
        ).fetchall()
    finally:
        connection.close()
    return {str(row[0]) for row in rows}
