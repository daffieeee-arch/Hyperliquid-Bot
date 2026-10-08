"""Binance USD-M universe discovery and expansion. No live HTTP."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import urllib.parse
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from pytest import CaptureFixture, MonkeyPatch

from research.hist_etl.cli import main
from research.hist_etl.errors import HistEtlError
from research.hist_etl.http import HttpBody, RateLimiter
from research.hist_etl.manifest import assert_known_ids, load_manifest, select_binance
from research.hist_etl.models import BINANCE_VISION_BASE, BINANCE_VISION_LISTING
from research.hist_etl.pipeline import run_plan, run_sync
from research.hist_etl.planning import latest_published_month, plan_binance
from research.hist_etl.universe import (
    BucketLister,
    MonthRun,
    Universe,
    UniverseSymbol,
    discover_universe,
    expand_universe,
    load_universe,
    render_universe,
)

AS_OF = date(2026, 10, 8)
KLINES = "data/futures/um/monthly/klines/"
FUNDING = "data/futures/um/monthly/fundingRate/"


class BytesResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.headers: Mapping[str, str] = {"content-length": str(len(body))}
        self.body = body

    def iter_bytes(self) -> Iterator[bytes]:
        if self.body:
            yield self.body


class FakeBucket:
    """ListObjects (v1) over in-memory keys: prefix, ``/`` delimiter, marker, small pages."""

    def __init__(self, keys: Iterable[str], page_size: int = 3) -> None:
        self.keys = sorted(keys)
        self.page_size = page_size
        self.calls: list[str] = []

    @contextmanager
    def open(
        self, method: str, url: str, headers: Mapping[str, str] | None = None
    ) -> Iterator[HttpBody]:
        del headers
        assert method == "GET"
        self.calls.append(url)
        base, _sep, query_text = url.partition("?")
        assert base == BINANCE_VISION_LISTING
        query = dict(urllib.parse.parse_qsl(query_text))
        assert query["delimiter"] == "/"
        prefix = query["prefix"]
        marker = query.get("marker", "")
        entries: dict[str, bool] = {}
        for key in self.keys:
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            name = prefix + rest.split("/", 1)[0] + "/" if "/" in rest else key
            if name > marker:
                entries[name] = "/" in rest
        ordered = sorted(entries)
        page = ordered[: self.page_size]
        truncated = len(ordered) > self.page_size
        yield BytesResponse(200, _listing_xml(prefix, page, entries, truncated))


def _listing_xml(prefix: str, page: list[str], kinds: dict[str, bool], truncated: bool) -> bytes:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">',
        "<Name>data.binance.vision</Name>",
        f"<Prefix>{escape(prefix)}</Prefix>",
    ]
    if truncated:
        parts.append(f"<NextMarker>{escape(page[-1])}</NextMarker>")
    parts.append(f"<IsTruncated>{'true' if truncated else 'false'}</IsTruncated>")
    for name in page:
        if kinds[name]:
            parts.append(f"<CommonPrefixes><Prefix>{escape(name)}</Prefix></CommonPrefixes>")
        else:
            parts.append(f"<Contents><Key>{escape(name)}</Key><Size>1</Size></Contents>")
    parts.append("</ListBucketResult>")
    return "".join(parts).encode()


def _months(first: str, last: str) -> list[str]:
    cursor = date.fromisoformat(f"{first}-01")
    end = date.fromisoformat(f"{last}-01")
    found: list[str] = []
    while cursor <= end:
        found.append(f"{cursor.year:04d}-{cursor.month:02d}")
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return found


def _kline_keys(symbol: str, months: list[str], interval: str = "1d") -> list[str]:
    keys: list[str] = []
    for month in months:
        name = f"{KLINES}{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
        keys.extend([name, name + ".CHECKSUM"])
    return keys


def _funding_keys(symbol: str, months: list[str]) -> list[str]:
    keys: list[str] = []
    for month in months:
        name = f"{FUNDING}{symbol}/{symbol}-fundingRate-{month}.zip"
        keys.extend([name, name + ".CHECKSUM"])
    return keys


def _bucket_keys() -> list[str]:
    return [
        *_kline_keys("BTCUSDT", _months("2026-06", "2026-09")),
        *_kline_keys("BTCUSDT", _months("2026-06", "2026-09"), interval="1h"),
        *_funding_keys("BTCUSDT", _months("2026-06", "2026-09")),
        *_kline_keys("LUNAUSDT", _months("2026-01", "2026-03")),
        *_funding_keys("LUNAUSDT", _months("2026-01", "2026-03")),
        *_kline_keys("RELUSDT", [*_months("2025-11", "2025-12"), *_months("2026-08", "2026-09")]),
        *_funding_keys("FUNDUSDT", _months("2026-07", "2026-09")),
        # Only another interval: no month of the listed one, no funding.
        *_kline_keys("ETHUSDT", _months("2026-09", "2026-09"), interval="1h"),
        *_kline_keys("BTCUSDC", _months("2026-09", "2026-09")),
        *_kline_keys("BTCUSDT_261225", _months("2026-09", "2026-09")),
        *_kline_keys("币安人生USDT", _months("2026-09", "2026-09")),
    ]


def _lister(bucket: FakeBucket) -> BucketLister:
    return BucketLister(
        bucket, limiter=RateLimiter(0, lambda _s: None), max_retries=2, sleeper=lambda _s: None
    )


def _discovered() -> Universe:
    return discover_universe(
        _lister(FakeBucket(_bucket_keys())), as_of=AS_OF, quote="USDT", interval="1d"
    )


def _m(text: str) -> date:
    return date.fromisoformat(f"{text}-01")


def test_lister_follows_markers_across_pages() -> None:
    bucket = FakeBucket(_kline_keys("BTCUSDT", _months("2026-01", "2026-05")), page_size=3)
    keys, prefixes = _lister(bucket).list(f"{KLINES}BTCUSDT/1d/")
    assert len(keys) == 10
    assert keys == tuple(sorted(keys))
    assert prefixes == ()
    assert len(bucket.calls) == 4
    assert "marker=" not in bucket.calls[0]
    assert "marker=data%2Ffutures" in bucket.calls[1]


class _Fixed:
    def __init__(self, status: int, body: bytes) -> None:
        self.response = BytesResponse(status, body)

    @contextmanager
    def open(
        self, method: str, url: str, headers: Mapping[str, str] | None = None
    ) -> Iterator[HttpBody]:
        del method, url, headers
        yield self.response


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (403, b"", "HTTP 403"),
        (503, b"", "HTTP 503"),
        (200, b"<html>", "not XML"),
        (200, b"<Other/>", "has root"),
        (200, _listing_xml("data/other/", [], {}, truncated=False), "another prefix"),
        (
            200,
            _listing_xml(KLINES, ["data/elsewhere/x.zip"], {"data/elsewhere/x.zip": False}, False),
            "returned data/elsewhere",
        ),
        (
            200,
            _listing_xml(KLINES, [f"{KLINES}A/"], {f"{KLINES}A/": True}, False).replace(
                b"<NextMarker>", b""
            )
            + b"",
            None,
        ),
    ],
)
def test_lister_fails_closed_on_bad_pages(status: int, body: bytes, message: str | None) -> None:
    lister = BucketLister(
        _Fixed(status, body),
        limiter=RateLimiter(0, lambda _s: None),
        max_retries=1,
        sleeper=lambda _s: None,
    )
    if message is None:
        assert lister.list(KLINES) == ((), (f"{KLINES}A/",))
        return
    with pytest.raises(HistEtlError, match=message) as caught:
        lister.list(KLINES)
    assert caught.value.exit_code == 2


class _Sequence:
    def __init__(self, responses: list[BytesResponse]) -> None:
        self.responses = responses
        self.calls = 0

    @contextmanager
    def open(
        self, method: str, url: str, headers: Mapping[str, str] | None = None
    ) -> Iterator[HttpBody]:
        del method, url, headers
        self.calls += 1
        yield self.responses.pop(0)


_NO_SUCH_BUCKET = b"<Error><Code>NoSuchBucket</Code></Error>"


def test_lister_retries_a_spurious_no_such_bucket() -> None:
    good = _listing_xml(KLINES, [f"{KLINES}A/"], {f"{KLINES}A/": True}, truncated=False)
    transport = _Sequence(
        [
            BytesResponse(404, _NO_SUCH_BUCKET),
            BytesResponse(404, _NO_SUCH_BUCKET),
            BytesResponse(200, good),
        ]
    )
    waits: list[float] = []
    lister = BucketLister(
        transport, limiter=RateLimiter(0, lambda _s: None), max_retries=1, sleeper=waits.append
    )
    assert lister.list(KLINES) == ((), (f"{KLINES}A/",))
    assert transport.calls == 3
    assert waits == [0.5, 1.0]


class _DroppedBody(BytesResponse):
    def iter_bytes(self) -> Iterator[bytes]:
        yield self.body[:10]
        raise ConnectionResetError("connection dropped")


def test_lister_retries_a_body_that_drops() -> None:
    good = _listing_xml(KLINES, [f"{KLINES}A/"], {f"{KLINES}A/": True}, truncated=False)
    transport = _Sequence([_DroppedBody(200, good), BytesResponse(200, good)])
    lister = BucketLister(
        transport, limiter=RateLimiter(0, lambda _s: None), max_retries=1, sleeper=lambda _s: None
    )
    assert lister.list(KLINES) == ((), (f"{KLINES}A/",))
    assert transport.calls == 2
    failing = _Sequence([_DroppedBody(200, good) for _ in range(10)])
    lister = BucketLister(
        failing, limiter=RateLimiter(0, lambda _s: None), max_retries=1, sleeper=lambda _s: None
    )
    with pytest.raises(HistEtlError, match="connection dropped") as caught:
        lister.list(KLINES)
    assert caught.value.exit_code == 2


class _IncompleteBody(BytesResponse):
    def iter_bytes(self) -> Iterator[bytes]:
        yield self.body[:10]
        raise http.client.IncompleteRead(self.body[:10], len(self.body) - 10)


def test_lister_retries_an_incomplete_chunked_body() -> None:
    good = _listing_xml(KLINES, [f"{KLINES}A/"], {f"{KLINES}A/": True}, truncated=False)
    transport = _Sequence(
        [
            _IncompleteBody(200, good),
            BytesResponse(503, b""),
            BytesResponse(200, good[:40]),
            BytesResponse(200, good),
        ]
    )
    lister = BucketLister(
        transport, limiter=RateLimiter(0, lambda _s: None), max_retries=1, sleeper=lambda _s: None
    )
    assert lister.list(KLINES) == ((), (f"{KLINES}A/",))
    assert transport.calls == 4


def test_lister_does_not_retry_another_404() -> None:
    transport = _Sequence([BytesResponse(404, b"<Error><Code>NoSuchKey</Code></Error>")])
    lister = BucketLister(
        transport, limiter=RateLimiter(0, lambda _s: None), max_retries=1, sleeper=lambda _s: None
    )
    with pytest.raises(HistEtlError, match="HTTP 404"):
        lister.list(KLINES)
    assert transport.calls == 1


def test_lister_gives_up_on_a_lasting_no_such_bucket() -> None:
    transport = _Sequence([BytesResponse(404, _NO_SUCH_BUCKET) for _ in range(10)])
    lister = BucketLister(
        transport, limiter=RateLimiter(0, lambda _s: None), max_retries=1, sleeper=lambda _s: None
    )
    with pytest.raises(HistEtlError, match="HTTP 404"):
        lister.list(KLINES)
    assert transport.calls == 10


def test_lister_refuses_a_marker_that_does_not_advance() -> None:
    page = _listing_xml(KLINES, [], {}, truncated=False).replace(
        b"<IsTruncated>false", b"<IsTruncated>true"
    )
    lister = BucketLister(
        _Fixed(200, page),
        limiter=RateLimiter(0, lambda _s: None),
        max_retries=1,
        sleeper=lambda _s: None,
    )
    with pytest.raises(HistEtlError, match="did not advance"):
        lister.list(KLINES)


def test_discovery_keeps_delisted_and_relisted_symbols() -> None:
    universe = _discovered()
    assert universe.latest_month == _m("2026-09")
    by_symbol = {item.symbol: item for item in universe.symbols}
    assert sorted(by_symbol) == ["BTCUSDT", "FUNDUSDT", "LUNAUSDT", "RELUSDT"]
    assert by_symbol["BTCUSDT"].klines == (MonthRun(_m("2026-06"), _m("2026-09")),)
    assert by_symbol["LUNAUSDT"].funding == (MonthRun(_m("2026-01"), _m("2026-03")),)
    assert by_symbol["RELUSDT"].klines == (
        MonthRun(_m("2025-11"), _m("2025-12")),
        MonthRun(_m("2026-08"), _m("2026-09")),
    )
    assert by_symbol["RELUSDT"].funding == ()
    assert by_symbol["FUNDUSDT"].klines == ()
    assert universe.excluded == (("币安人生USDT", "symbol is not 2 to 20 of A-Z and 0-9"),)


def test_latest_month_follows_the_first_monday_rule() -> None:
    # October 2026 starts on a Thursday; September is due Monday 2026-10-05.
    assert latest_published_month(date(2026, 10, 4)) == _m("2026-08")
    assert latest_published_month(date(2026, 10, 5)) == _m("2026-09")
    assert latest_published_month(date(2026, 1, 1)) == _m("2025-11")


def test_latest_month_keeps_runs_open_while_a_month_rolls_out() -> None:
    # 2026-10-04 is before the first Monday; September is listed for some
    # symbols only. A run that ends in August must stay open, not close.
    keys = [key for key in _bucket_keys() if "LUNAUSDT" in key or "2026-09" not in key]
    universe = discover_universe(
        _lister(FakeBucket(keys)), as_of=date(2026, 10, 4), quote="USDT", interval="1d"
    )
    assert universe.latest_month == _m("2026-08")
    specs = {
        spec.id: spec
        for spec in expand_universe("u", universe, datasets=("klines",), start=None, enabled=True)
    }
    assert specs["u-klines-btcusdt"].end is None
    assert specs["u-klines-lunausdt"].end == date(2026, 3, 31)


def test_a_run_one_month_short_stays_open_after_the_first_monday() -> None:
    # On 2026-10-08 September is due, but BTCUSDT's September zip is not up
    # yet: its run stays open. A run two months short is closed.
    keys = [
        key
        for key in _bucket_keys()
        if not ("BTCUSDT" in key and "2026-09" in key)
        and not ("RELUSDT" in key and ("2026-09" in key or "2026-08" in key))
    ]
    universe = discover_universe(
        _lister(FakeBucket(keys)), as_of=AS_OF, quote="USDT", interval="1d"
    )
    assert universe.latest_month == _m("2026-09")
    specs = {
        spec.id: spec
        for spec in expand_universe("u", universe, datasets=("klines",), start=None, enabled=True)
    }
    assert specs["u-klines-btcusdt"].end is None
    assert "u-klines-relusdt-r2" not in specs


def test_render_round_trips_one_line_per_symbol(tmp_path: Path) -> None:
    universe = _discovered()
    text = render_universe(universe)
    assert render_universe(universe) == text
    payload = json.loads(text)
    assert payload["latest_month"] == "2026-09"
    assert payload["symbols"]["LUNAUSDT"] == {
        "funding": [["2026-01", "2026-03"]],
        "klines": [["2026-01", "2026-03"]],
    }
    assert sum(1 for line in text.splitlines() if line.startswith('    "')) == 5
    path = tmp_path / "u.json"
    path.write_text(text, encoding="utf-8")
    assert load_universe(path) == universe
    empty = replace(universe, excluded=())
    path.write_text(render_universe(empty), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["excluded"] == {}
    assert load_universe(path) == empty


def _edited(tmp_path: Path, edit: dict[str, object]) -> Path:
    payload = json.loads(render_universe(_discovered()))
    payload.update(edit)
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "edit",
    [
        {"format": 2},
        {"market": "spot"},
        {"extra": 1},
        {"kline_interval": "2d"},
        {"latest_month": "2026-10"},
        {"as_of": "2026-13-01"},
        {"symbols": {}},
        {"symbols": {"btcusdt": {"klines": [["2026-01", "2026-02"]], "funding": []}}},
        {"symbols": {"BTCUSDC": {"klines": [["2026-01", "2026-02"]], "funding": []}}},
        {"symbols": {"BTCUSDT": {"klines": [["2026-03", "2026-02"]], "funding": []}}},
        {
            "symbols": {
                "BTCUSDT": {
                    "klines": [["2026-01", "2026-02"], ["2026-03", "2026-04"]],
                    "funding": [],
                }
            }
        },
        {
            "symbols": {
                "BTCUSDT": {
                    "klines": [["2026-01", "2026-04"], ["2026-03", "2026-05"]],
                    "funding": [],
                }
            }
        },
        {"symbols": {"BTCUSDT": {"klines": [], "funding": []}}},
        {"symbols": {"BTCUSDT": {"klines": [["2026-1", "2026-02"]], "funding": []}}},
        {"symbols": {"BTCUSDT": {"klines": []}}},
        {"excluded": {"BTCUSDT": "dup"}},
    ],
)
def test_load_universe_fails_closed(tmp_path: Path, edit: dict[str, object]) -> None:
    with pytest.raises(HistEtlError):
        load_universe(_edited(tmp_path, edit))


def test_expansion_ends_runs_and_marks_listing_edges() -> None:
    specs = {
        spec.id: spec
        for spec in expand_universe(
            "u", _discovered(), datasets=("klines", "fundingRate"), start=None, enabled=False
        )
    }
    assert sorted(specs) == [
        "u-funding-btcusdt",
        "u-funding-fundusdt",
        "u-funding-lunausdt",
        "u-klines-btcusdt",
        "u-klines-lunausdt",
        "u-klines-relusdt",
        "u-klines-relusdt-r2",
    ]
    listed = specs["u-klines-btcusdt"]
    assert (listed.start, listed.end, listed.end_token) == (date(2026, 6, 1), None, "today")
    assert listed.granularity == "monthly_with_daily_tail"
    assert (listed.open_start, listed.open_end) == (True, False)
    assert (listed.interval, listed.group, listed.enabled) == ("1d", "u", False)
    delisted = specs["u-klines-lunausdt"]
    assert (delisted.end, delisted.granularity) == (date(2026, 3, 31), "monthly")
    assert (delisted.open_start, delisted.open_end) == (True, True)
    first_run = specs["u-klines-relusdt"]
    assert (first_run.start, first_run.end) == (date(2025, 11, 1), date(2025, 12, 31))
    assert specs["u-klines-relusdt-r2"].end is None
    funding = specs["u-funding-lunausdt"]
    assert (funding.dataset, funding.interval, funding.granularity) == (
        "fundingRate",
        None,
        "monthly",
    )


def test_expansion_start_cuts_runs() -> None:
    specs = {
        spec.id: spec
        for spec in expand_universe(
            "u", _discovered(), datasets=("klines",), start=date(2026, 2, 1), enabled=True
        )
    }
    assert "u-klines-relusdt" not in specs
    assert "u-funding-btcusdt" not in specs
    cut = specs["u-klines-lunausdt"]
    assert (cut.start, cut.open_start, cut.open_end) == (date(2026, 2, 1), False, True)
    untouched = specs["u-klines-btcusdt"]
    assert (untouched.start, untouched.open_start) == (date(2026, 6, 1), True)


def test_universe_plan_reaches_only_listed_months() -> None:
    specs = expand_universe("u", _discovered(), datasets=("klines",), start=None, enabled=True)
    luna = tuple(spec for spec in specs if spec.symbol == "LUNAUSDT")
    names = [plan.filename for plan in plan_binance(luna, AS_OF, Path("/tmp/u"))]
    assert names == [
        "LUNAUSDT-1d-2026-01.zip",
        "LUNAUSDT-1d-2026-02.zip",
        "LUNAUSDT-1d-2026-03.zip",
    ]
    btc = tuple(spec for spec in specs if spec.symbol == "BTCUSDT")
    tail = [plan.filename for plan in plan_binance(btc, AS_OF, Path("/tmp/u"))]
    assert tail[-1] == "BTCUSDT-1d-2026-10-07.zip"


def _write_universe(directory: Path, universe: Universe) -> Path:
    path = directory / "universe" / "u.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_universe(universe), encoding="utf-8")
    return path


def _universe_manifest(directory: Path, body: str) -> Path:
    path = directory / "datasets.toml"
    path.write_text(
        "min_free_bytes = 1\nrequests_per_second = 0\nmax_retries = 2\ntimeout_seconds = 5\n"
        + body,
        encoding="utf-8",
    )
    return path


def test_manifest_expands_and_selects_by_group(tmp_path: Path) -> None:
    _write_universe(tmp_path, _discovered())
    manifest = load_manifest(
        _universe_manifest(
            tmp_path,
            '[[binance_universe]]\nid = "u"\nfile = "universe/u.json"\n'
            'datasets = ["klines"]\nenabled = false\n',
        )
    )
    assert manifest.binance_groups == ("u",)
    assert len(manifest.binance) == 4
    assert select_binance(manifest, None) == ()
    assert len(select_binance(manifest, ("u",))) == 4
    assert [spec.id for spec in select_binance(manifest, ("u-klines-lunausdt",))] == [
        "u-klines-lunausdt"
    ]
    assert_known_ids(manifest, ("u", "u-klines-btcusdt"))
    with pytest.raises(HistEtlError, match="unknown dataset id"):
        assert_known_ids(manifest, ("v",))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('file = "/etc/u.json"\n', "below the manifest directory"),
        ('file = "../u.json"\n', "below the manifest directory"),
        ('file = "universe/u.json"\ndatasets = ["aggTrades"]\n', "datasets must name"),
        ('file = "universe/u.json"\ndatasets = ["klines", "klines"]\n', "datasets must name"),
        ('file = "universe/u.json"\ndatasets = []\n', "datasets must name"),
        ('file = "universe/u.json"\nsymbols = ["BTCUSDT"]\n', "unknown keys"),
        ('file = "universe/missing.json"\n', "does not exist"),
        ('file = "universe/u.json"\nstart = "2026-01-05"\n', "first day of a month"),
    ],
)
def test_manifest_universe_entry_fails_closed(tmp_path: Path, body: str, message: str) -> None:
    _write_universe(tmp_path, _discovered())
    path = _universe_manifest(tmp_path, f'[[binance_universe]]\nid = "u"\n{body}')
    with pytest.raises(HistEtlError, match=message):
        load_manifest(path)


def test_manifest_universe_is_opt_in_by_default(tmp_path: Path) -> None:
    _write_universe(tmp_path, _discovered())
    manifest = load_manifest(
        _universe_manifest(tmp_path, '[[binance_universe]]\nid = "u"\nfile = "universe/u.json"\n')
    )
    assert manifest.binance
    assert select_binance(manifest, None) == ()


def test_manifest_group_id_must_be_unique(tmp_path: Path) -> None:
    _write_universe(tmp_path, _discovered())
    path = _universe_manifest(
        tmp_path,
        '[[binance_universe]]\nid = "u"\nfile = "universe/u.json"\n'
        '[[binance]]\nid = "u"\nmarket = "um"\ndataset = "fundingRate"\nsymbol = "BTCUSDT"\n'
        'start = "2026-01-01"\nend = "2026-01-31"\ngranularity = "monthly"\n',
    )
    with pytest.raises(HistEtlError, match="unique"):
        load_manifest(path)


def test_default_manifest_names_the_committed_universe() -> None:
    repo = Path(__file__).resolve().parents[2]
    manifest = load_manifest(repo / "config" / "hist_etl" / "datasets.toml")
    assert "bn-um-usdt-1d" in manifest.binance_groups
    grouped = [spec for spec in manifest.binance if spec.group == "bn-um-usdt-1d"]
    assert grouped
    assert not any(spec.enabled for spec in grouped)
    assert {spec.dataset for spec in grouped} == {"klines", "fundingRate"}
    symbols = {spec.symbol for spec in grouped}
    # Delisted contracts stay in the universe: that is its point.
    assert {"BTCUSDT", "LUNAUSDT", "SRMUSDT"} <= symbols


def _daily_kline(day: date) -> str:
    opened = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * 1000
    closed = opened + 86_400_000 - 1
    return f"{opened},1,1,1,1,1,{closed},1,1,1,1,0"


def _serve_kline_month(
    bucket: dict[str, bytes], symbol: str, month: str, days: Iterable[int]
) -> None:
    first = date.fromisoformat(f"{month}-01")
    text = "".join(_daily_kline(first.replace(day=day)) + "\n" for day in days)
    name = f"{symbol}-1d-{month}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name.replace(".zip", ".csv"), text)
    payload = buffer.getvalue()
    url = f"{BINANCE_VISION_BASE}data/futures/um/monthly/klines/{symbol}/1d/{name}"
    bucket[url] = payload
    bucket[url + ".CHECKSUM"] = f"{hashlib.sha256(payload).hexdigest()}  {name}\n".encode()


class _Archives:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.gets: list[str] = []
        self.requests: list[tuple[str, str]] = []

    @contextmanager
    def open(
        self, method: str, url: str, headers: Mapping[str, str] | None = None
    ) -> Iterator[HttpBody]:
        del headers
        self.requests.append((method, url))
        if method == "GET":
            self.gets.append(url)
        body = self.files.get(url)
        if body is None:
            yield BytesResponse(404, b"")
            return
        yield BytesResponse(200, b"" if method == "HEAD" else body)


def _sync_luna(
    tmp_path: Path, february_days: Iterable[int], start: str | None, months: int = 3
) -> list[str]:
    universe = Universe(
        market="um",
        quote="USDT",
        interval="1d",
        as_of=AS_OF,
        latest_month=_m("2026-09"),
        symbols=(UniverseSymbol("LUNAUSDT", (MonthRun(_m("2026-01"), _m("2026-03")),), ()),),
        excluded=(),
    )
    _write_universe(tmp_path, universe)
    start_line = "" if start is None else f'start = "{start}"\n'
    manifest = _universe_manifest(
        tmp_path,
        '[[binance_universe]]\nid = "u"\nfile = "universe/u.json"\n'
        f'datasets = ["klines"]\nenabled = false\n{start_line}',
    )
    files: dict[str, bytes] = {}
    # Listed on Jan 10, delisted after Mar 15.
    _serve_kline_month(files, "LUNAUSDT", "2026-01", range(10, 32))
    _serve_kline_month(files, "LUNAUSDT", "2026-02", february_days)
    _serve_kline_month(files, "LUNAUSDT", "2026-03", range(1, 16))
    root = tmp_path / "root"
    code = run_sync(
        root=root,
        manifest_path=manifest,
        today=AS_OF,
        dataset_ids=("u",),
        env={},
        dry_run=False,
        transport=_Archives(files),
    )
    report = json.loads((root / "logs" / "gap_report.json").read_text(encoding="utf-8"))
    assert code == (2 if report["gaps"] else 0)
    assert len(list((root / "parquet").rglob("LUNAUSDT-*.parquet"))) == months
    return [f"{gap['kind']} {gap['detail']}" for gap in report["gaps"]]


def test_listing_edges_are_not_gaps(tmp_path: Path) -> None:
    assert _sync_luna(tmp_path, range(1, 29), start=None) == []


def test_holes_inside_a_listed_run_are_still_gaps(tmp_path: Path) -> None:
    gaps = _sync_luna(tmp_path, [day for day in range(1, 29) if day != 14], start=None)
    assert gaps == ["kline_hole missing 86400s samples"]


def test_a_start_cut_inside_a_run_expects_full_coverage(tmp_path: Path) -> None:
    gaps = _sync_luna(tmp_path, range(3, 29), start="2026-02-01", months=2)
    assert len(gaps) == 1
    assert gaps[0].startswith("kline_hole coverage 2026-02-03 00:00:00..")


def test_a_start_cut_on_the_first_month_keeps_its_late_start(tmp_path: Path) -> None:
    assert _sync_luna(tmp_path, range(1, 29), start="2026-01-01") == []


def test_shared_archives_download_once(tmp_path: Path) -> None:
    universe = Universe(
        market="um",
        quote="USDT",
        interval="1d",
        as_of=AS_OF,
        latest_month=_m("2026-09"),
        symbols=(UniverseSymbol("BTCUSDT", (), (MonthRun(_m("2026-09"), _m("2026-09")),)),),
        excluded=(),
    )
    _write_universe(tmp_path, universe)
    manifest = _universe_manifest(
        tmp_path,
        '[[binance_universe]]\nid = "u"\nfile = "universe/u.json"\nenabled = false\n'
        '[[binance]]\nid = "legacy"\nmarket = "um"\ndataset = "fundingRate"\n'
        'symbol = "BTCUSDT"\nstart = "2026-09-01"\nend = "2026-09-30"\n'
        'granularity = "monthly"\n',
    )
    start = datetime(2026, 9, 1, tzinfo=UTC)
    rows = "".join(
        f"{int((start + timedelta(hours=8 * step)).timestamp()) * 1000},8,0.0001\n"
        for step in range(90)
    )
    name = "BTCUSDT-fundingRate-2026-09.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            name.replace(".zip", ".csv"),
            "calc_time,funding_interval_hours,last_funding_rate\n" + rows,
        )
    payload = buffer.getvalue()
    url = f"{BINANCE_VISION_BASE}data/futures/um/monthly/fundingRate/BTCUSDT/{name}"
    archives = _Archives(
        {
            url: payload,
            url + ".CHECKSUM": f"{hashlib.sha256(payload).hexdigest()}  {name}\n".encode(),
        }
    )
    root = tmp_path / "root"
    code = run_sync(
        root=root,
        manifest_path=manifest,
        today=AS_OF,
        dataset_ids=("u", "legacy"),
        env={},
        dry_run=False,
        transport=archives,
    )
    report = json.loads((root / "logs" / "gap_report.json").read_text(encoding="utf-8"))
    assert (code, report["gaps"]) == (0, [])
    assert archives.gets.count(url) == 1
    assert len(list((root / "parquet").rglob("BTCUSDT-2026-09.parquet"))) == 1

    probes = _Archives(archives.files)
    assert (
        run_plan(
            root=tmp_path / "plan",
            manifest_path=manifest,
            today=AS_OF,
            dataset_ids=("u", "legacy"),
            env={},
            transport=probes,
        )
        == 0
    )
    assert probes.requests == [("HEAD", url)]

    missing_root = tmp_path / "missing"
    empty = _Archives({})
    code = run_sync(
        root=missing_root,
        manifest_path=manifest,
        today=AS_OF,
        dataset_ids=("u", "legacy"),
        env={},
        dry_run=False,
        transport=empty,
    )
    report = json.loads((missing_root / "logs" / "gap_report.json").read_text(encoding="utf-8"))
    assert code == 2
    # The second dataset reuses the first one's failure instead of asking again.
    assert empty.requests
    assert len(empty.requests) == len(set(empty.requests))
    assert {gap["dataset_id"] for gap in report["gaps"] if gap["kind"] == "missing_archive"} >= {
        "u-funding-btcusdt",
        "legacy",
    }


def test_cli_rejects_a_bad_retry_setting(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    out = tmp_path / "u.json"
    args = ["universe", "--out", str(out), "--today", AS_OF.isoformat()]
    assert main(args, env={"HIST_ETL_MAX_RETRIES": "abc"}) == 1
    assert "invalid HIST_ETL retry or timeout setting" in capsys.readouterr().err
    assert not out.exists()


class _RacingBucket(FakeBucket):
    def __init__(self, keys: Iterable[str], out: Path) -> None:
        super().__init__(keys)
        self.out = out

    @contextmanager
    def open(
        self, method: str, url: str, headers: Mapping[str, str] | None = None
    ) -> Iterator[HttpBody]:
        if not self.out.exists():
            self.out.parent.mkdir(parents=True, exist_ok=True)
            self.out.write_text("someone else's file", encoding="utf-8")
        with super().open(method, url, headers) as response:
            yield response


def test_cli_keeps_a_file_that_appears_during_the_scan(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    out = tmp_path / "universe" / "u.json"
    bucket = _RacingBucket(_bucket_keys(), out)
    monkeypatch.setattr("research.hist_etl.universe.build_transport", lambda _timeout: bucket)
    monkeypatch.setattr("research.hist_etl.universe._sleep", lambda _seconds: None)
    args = ["universe", "--out", str(out), "--today", AS_OF.isoformat()]
    assert main(args, env={}) == 2
    assert "appeared during the scan" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "someone else's file"
    assert list(out.parent.iterdir()) == [out]


def test_cli_writes_a_new_universe_and_never_replaces_one(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    bucket = FakeBucket(_bucket_keys())
    monkeypatch.setattr("research.hist_etl.universe.build_transport", lambda _timeout: bucket)
    monkeypatch.setattr("research.hist_etl.universe._sleep", lambda _seconds: None)
    out = tmp_path / "universe" / "u.json"
    args = ["universe", "--out", str(out), "--today", AS_OF.isoformat()]
    assert main(args, env={}) == 0
    assert load_universe(out) == _discovered()
    printed = capsys.readouterr()
    assert "4 symbols\t3 still published\t1 closed\t1 excluded" in printed.out
    assert "scanned\t5/5" in printed.err
    calls = len(bucket.calls)
    assert main(args, env={}) == 2
    assert "refusing to replace" in capsys.readouterr().err
    assert len(bucket.calls) == calls
