"""The Binance USD-M universe: every symbol with archives, delisted ones included.

``discover_universe`` lists the S3 bucket behind data.binance.vision, which
keeps the archives of delisted contracts. A study can therefore choose its
universe point in time (for example by trailing volume at each date) instead
of from the symbols that happen to trade today, which would be survivorship
bias. The result is committed as a dated JSON file, and ``expand_universe``
turns that file into one ``BinanceSpec`` per symbol, dataset, and run of
consecutive archive months.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Final

from research.hist_etl.errors import HistEtlError
from research.hist_etl.http import (
    RateLimiter,
    Sleeper,
    Transport,
    build_transport,
    open_with_retries,
)
from research.hist_etl.models import BINANCE_VISION_LISTING, INTERVAL_SECONDS, BinanceSpec
from research.hist_etl.planning import latest_published_month, next_month

FORMAT: Final = 1
SOURCE: Final = "binance-vision"
UNIVERSE_DATASETS: Final = frozenset({"klines", "fundingRate"})

_SYMBOL = re.compile(r"[A-Z0-9]{2,20}")
_QUOTE = re.compile(r"[A-Z]{3,5}")
_MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
_S3_NS: Final = "{http://s3.amazonaws.com/doc/2006-03-01/}"
# A listing page holds at most 1000 entries, well under 1 MiB of XML.
_MAX_PAGE_BYTES: Final = 4 * 1024 * 1024
_NO_SUCH_BUCKET: Final = b"<Code>NoSuchBucket</Code>"
_NO_SUCH_BUCKET_ATTEMPTS: Final = 10
_KLINES_ROOT: Final = "data/futures/um/monthly/klines/"
_FUNDING_ROOT: Final = "data/futures/um/monthly/fundingRate/"
_TOP_KEYS: Final = frozenset(
    {
        "format",
        "source",
        "market",
        "quote",
        "kline_interval",
        "as_of",
        "latest_month",
        "symbols",
        "excluded",
    }
)
_SYMBOL_KEYS: Final = frozenset({"klines", "funding"})


@dataclass(frozen=True, slots=True)
class MonthRun:
    """Consecutive archive months, each the first day of its month, inclusive."""

    first: date
    last: date


@dataclass(frozen=True, slots=True)
class UniverseSymbol:
    symbol: str
    klines: tuple[MonthRun, ...]
    funding: tuple[MonthRun, ...]


@dataclass(frozen=True, slots=True)
class Universe:
    """Which months of monthly archives exist per symbol, as listed on ``as_of``.

    ``latest_month`` is the newest month the planner expects to be published
    by ``as_of``. A symbol whose last run reaches it is treated as listed.
    ``excluded`` names symbols with the quote suffix that the manifest cannot
    hold (for example a non-ASCII name), with the reason.
    """

    market: str
    quote: str
    interval: str
    as_of: date
    latest_month: date
    symbols: tuple[UniverseSymbol, ...]
    excluded: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _Page:
    keys: tuple[str, ...]
    prefixes: tuple[str, ...]
    truncated: bool
    next_marker: str | None


class BucketLister:
    """Paginated ListObjects (v1) with a ``/`` delimiter. Read-only, no credentials."""

    def __init__(
        self,
        transport: Transport,
        *,
        limiter: RateLimiter,
        max_retries: int,
        sleeper: Sleeper,
        base: str = BINANCE_VISION_LISTING,
    ) -> None:
        self._transport = transport
        self._limiter = limiter
        self._max_retries = max_retries
        self._sleeper = sleeper
        self._base = base

    def list(self, prefix: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """All keys and common prefixes directly under ``prefix``."""

        keys: list[str] = []
        prefixes: list[str] = []
        marker = ""
        while True:
            page = self._page(prefix, marker)
            keys.extend(page.keys)
            prefixes.extend(page.prefixes)
            if not page.truncated:
                return tuple(keys), tuple(prefixes)
            following = page.next_marker or max((*page.keys, *page.prefixes), default="")
            if following <= marker:
                raise HistEtlError(f"bucket listing of {prefix} did not advance", exit_code=2)
            marker = following

    def _page(self, prefix: str, marker: str) -> _Page:
        query = {"delimiter": "/", "prefix": prefix}
        if marker:
            query["marker"] = marker
        url = f"{self._base}?{urllib.parse.urlencode(query, quote_via=urllib.parse.quote)}"
        delay = 0.5
        for attempt in range(1, _NO_SUCH_BUCKET_ATTEMPTS + 1):
            status, body = self._get(url, prefix)
            if status == 200:
                return _parse_page(body, prefix)
            # The regional endpoint now and then answers NoSuchBucket for this
            # bucket, which exists: about one listing in eight on 2026-10-08.
            # That answer is never true, so it is retried; any other is final.
            if status != 404 or _NO_SUCH_BUCKET not in body or attempt == _NO_SUCH_BUCKET_ATTEMPTS:
                break
            self._sleeper(delay)
            delay = min(delay * 2, 8.0)
        raise HistEtlError(f"bucket listing of {prefix} returned HTTP {status}", exit_code=2)

    def _get(self, url: str, prefix: str) -> tuple[int, bytes]:
        with open_with_retries(
            self._transport,
            "GET",
            url,
            None,
            limiter=self._limiter,
            max_retries=self._max_retries,
            sleeper=self._sleeper,
        ) as response:
            return response.status, _read_capped(response.iter_bytes(), prefix)


def _read_capped(chunks: Iterable[bytes], prefix: str) -> bytes:
    parts: list[bytes] = []
    size = 0
    for chunk in chunks:
        size += len(chunk)
        if size > _MAX_PAGE_BYTES:
            raise HistEtlError(f"bucket listing of {prefix} is too large", exit_code=2)
        parts.append(chunk)
    return b"".join(parts)


def _parse_page(body: bytes, prefix: str) -> _Page:
    # The standard parser does not fetch external entities, and the page is
    # size-capped above, so a hostile body can fail the run but not reach out.
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise HistEtlError(f"bucket listing of {prefix} is not XML: {exc}", exit_code=2) from exc
    if root.tag != f"{_S3_NS}ListBucketResult":
        raise HistEtlError(f"bucket listing of {prefix} has root {root.tag}", exit_code=2)
    if _text(root, "Prefix") != prefix:
        raise HistEtlError(f"bucket listing answered another prefix than {prefix}", exit_code=2)
    truncated = _text(root, "IsTruncated")
    if truncated not in {"true", "false"}:
        raise HistEtlError(f"bucket listing of {prefix} has no IsTruncated flag", exit_code=2)
    keys = tuple(_text(item, "Key") for item in root.iter(f"{_S3_NS}Contents"))
    prefixes = tuple(_text(item, "Prefix") for item in root.iter(f"{_S3_NS}CommonPrefixes"))
    for name in (*keys, *prefixes):
        if not name.startswith(prefix):
            raise HistEtlError(f"bucket listing of {prefix} returned {name}", exit_code=2)
    next_marker = root.find(f"{_S3_NS}NextMarker")
    return _Page(
        keys=keys,
        prefixes=prefixes,
        truncated=truncated == "true",
        next_marker=None if next_marker is None else next_marker.text or None,
    )


def _text(element: ElementTree.Element, tag: str) -> str:
    found = element.find(f"{_S3_NS}{tag}")
    return "" if found is None or found.text is None else found.text


def discover_universe(
    lister: BucketLister,
    *,
    as_of: date,
    quote: str,
    interval: str,
    progress: Callable[[int, int], None] | None = None,
) -> Universe:
    """List every ``quote`` perp and its monthly kline and funding archive months."""

    if not _QUOTE.fullmatch(quote):
        raise HistEtlError(f"unsupported quote {quote}")
    if interval not in INTERVAL_SECONDS:
        raise HistEtlError(f"unsupported kline interval {interval}")
    _keys, kline_dirs = lister.list(_KLINES_ROOT)
    _keys, funding_dirs = lister.list(_FUNDING_ROOT)
    names = sorted(
        {_child(item, _KLINES_ROOT) for item in kline_dirs}
        | {_child(item, _FUNDING_ROOT) for item in funding_dirs}
    )
    # Other quotes and dated delivery contracts (BTCUSDT_250926) end otherwise.
    candidates = [name for name in names if name.endswith(quote) and name != quote]
    excluded = tuple(
        (name, "symbol is not 2 to 20 of A-Z and 0-9")
        for name in candidates
        if not _SYMBOL.fullmatch(name)
    )
    valid = [name for name in candidates if _SYMBOL.fullmatch(name)]
    if not valid:
        raise HistEtlError(f"the bucket lists no {quote} perp", exit_code=2)
    symbols: list[UniverseSymbol] = []
    for index, symbol in enumerate(valid, start=1):
        kline_keys, _dirs = lister.list(f"{_KLINES_ROOT}{symbol}/{interval}/")
        funding_keys, _dirs = lister.list(f"{_FUNDING_ROOT}{symbol}/")
        klines = _months(kline_keys, f"{symbol}-{interval}-")
        funding = _months(funding_keys, f"{symbol}-fundingRate-")
        if klines or funding:
            symbols.append(UniverseSymbol(symbol, _runs(klines), _runs(funding)))
        if progress is not None:
            progress(index, len(valid))
    return Universe(
        market="um",
        quote=quote,
        interval=interval,
        as_of=as_of,
        latest_month=latest_published_month(as_of),
        symbols=tuple(symbols),
        excluded=excluded,
    )


def _child(prefix: str, parent: str) -> str:
    return prefix.removeprefix(parent).removesuffix("/")


def _months(keys: Iterable[str], stem: str) -> list[date]:
    """Months whose zip is listed. A missing CHECKSUM is left for sync to report."""

    months: set[date] = set()
    for key in keys:
        name = key.rsplit("/", 1)[-1]
        if not name.startswith(stem) or not name.endswith(".zip"):
            continue
        month = _parse_month(name.removeprefix(stem).removesuffix(".zip"))
        if month is not None:
            months.add(month)
    return sorted(months)


def _runs(months: list[date]) -> tuple[MonthRun, ...]:
    runs: list[MonthRun] = []
    for month in months:
        if runs and next_month(runs[-1].last) == month:
            runs[-1] = MonthRun(runs[-1].first, month)
        else:
            runs.append(MonthRun(month, month))
    return tuple(runs)


def render_universe(universe: Universe) -> str:
    """Deterministic JSON with one line per symbol, so a refresh diffs per symbol."""

    head = {
        "format": FORMAT,
        "source": SOURCE,
        "market": universe.market,
        "quote": universe.quote,
        "kline_interval": universe.interval,
        "as_of": universe.as_of.isoformat(),
        "latest_month": _month_text(universe.latest_month),
    }
    lines = ["{"]
    lines.extend(f"  {json.dumps(key)}: {json.dumps(value)}," for key, value in head.items())
    entries = [
        (
            item.symbol,
            {"funding": _runs_json(item.funding), "klines": _runs_json(item.klines)},
        )
        for item in universe.symbols
    ]
    lines.extend(_object_lines("symbols", entries, last=False))
    lines.extend(_object_lines("excluded", list(universe.excluded), last=True))
    lines.append("}")
    return "\n".join(lines) + "\n"


def _object_lines(name: str, entries: Sequence[tuple[str, object]], *, last: bool) -> list[str]:
    comma_after = "" if last else ","
    if not entries:
        return [f"  {json.dumps(name)}: {{}}{comma_after}"]
    lines = [f"  {json.dumps(name)}: {{"]
    for position, (key, value) in enumerate(entries):
        comma = "," if position < len(entries) - 1 else ""
        lines.append(f"    {json.dumps(key)}: {json.dumps(value, sort_keys=True)}{comma}")
    lines.append(f"  }}{comma_after}")
    return lines


def _runs_json(runs: tuple[MonthRun, ...]) -> list[list[str]]:
    return [[_month_text(run.first), _month_text(run.last)] for run in runs]


def _month_text(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def _parse_month(text: str) -> date | None:
    match = _MONTH.fullmatch(text)
    if match is None:
        return None
    return date(int(match.group(1)), int(match.group(2)), 1)


def load_universe(path: Path) -> Universe:
    """Read and validate a universe file. Anything unexpected fails closed."""

    if not path.is_file():
        raise HistEtlError(f"universe file does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistEtlError(f"universe file {path.name} is not JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _TOP_KEYS:
        raise HistEtlError(f"universe file {path.name} must have exactly {sorted(_TOP_KEYS)}")
    if payload["format"] != FORMAT or payload["source"] != SOURCE or payload["market"] != "um":
        raise HistEtlError(f"universe file {path.name} is not a format-1 Binance USD-M universe")
    quote = payload["quote"]
    interval = payload["kline_interval"]
    if not isinstance(quote, str) or not _QUOTE.fullmatch(quote):
        raise HistEtlError(f"universe file {path.name} has an invalid quote")
    if not isinstance(interval, str) or interval not in INTERVAL_SECONDS:
        raise HistEtlError(f"universe file {path.name} has an unsupported kline_interval")
    as_of = _load_date(payload["as_of"], path)
    latest = _load_month(payload["latest_month"], path)
    if latest != latest_published_month(as_of):
        raise HistEtlError(f"universe file {path.name} latest_month does not follow as_of")
    symbols_raw = payload["symbols"]
    excluded_raw = payload["excluded"]
    if not isinstance(symbols_raw, dict) or not symbols_raw:
        raise HistEtlError(f"universe file {path.name} lists no symbols")
    if not isinstance(excluded_raw, dict):
        raise HistEtlError(f"universe file {path.name} excluded must be an object")
    symbols = tuple(
        _load_symbol(name, value, quote, path) for name, value in sorted(symbols_raw.items())
    )
    excluded: list[tuple[str, str]] = []
    for name, reason in sorted(excluded_raw.items()):
        if not isinstance(reason, str) or not reason or name in symbols_raw:
            raise HistEtlError(f"universe file {path.name} has an invalid exclusion")
        excluded.append((name, reason))
    return Universe(
        market="um",
        quote=quote,
        interval=interval,
        as_of=as_of,
        latest_month=latest,
        symbols=symbols,
        excluded=tuple(excluded),
    )


def _load_symbol(name: str, value: object, quote: str, path: Path) -> UniverseSymbol:
    if not _SYMBOL.fullmatch(name) or not name.endswith(quote) or name == quote:
        raise HistEtlError(f"universe file {path.name} has an invalid symbol {name}")
    if not isinstance(value, dict) or set(value) != _SYMBOL_KEYS:
        raise HistEtlError(f"universe file {path.name} {name} must have klines and funding")
    klines = _load_runs(value["klines"], f"{name} klines", path)
    funding = _load_runs(value["funding"], f"{name} funding", path)
    if not klines and not funding:
        raise HistEtlError(f"universe file {path.name} {name} has no archive months")
    return UniverseSymbol(name, klines, funding)


def _load_runs(value: object, label: str, path: Path) -> tuple[MonthRun, ...]:
    if not isinstance(value, list):
        raise HistEtlError(f"universe file {path.name} {label} must be a list of runs")
    runs: list[MonthRun] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise HistEtlError(f"universe file {path.name} {label} run must be [first, last]")
        run = MonthRun(_load_month(item[0], path), _load_month(item[1], path))
        if run.first > run.last:
            raise HistEtlError(f"universe file {path.name} {label} run ends before it starts")
        # Adjacent runs would be one run; overlapping ones would plan a month twice.
        if runs and next_month(runs[-1].last) >= run.first:
            raise HistEtlError(f"universe file {path.name} {label} runs touch or overlap")
        runs.append(run)
    return tuple(runs)


def _load_date(value: object, path: Path) -> date:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise HistEtlError(f"universe file {path.name} as_of must be YYYY-MM-DD")


def _load_month(value: object, path: Path) -> date:
    month = _parse_month(value) if isinstance(value, str) else None
    if month is None:
        raise HistEtlError(f"universe file {path.name} has an invalid month {value!r}")
    return month


def expand_universe(
    group: str,
    universe: Universe,
    *,
    datasets: tuple[str, ...],
    start: date | None,
    enabled: bool,
) -> tuple[BinanceSpec, ...]:
    """One spec per symbol, dataset, and run of months, ids ``{group}-{kind}-{symbol}``.

    A run that reaches ``latest_month`` stays open (``end = "today"``, with a
    daily kline tail). Any other run ends with its last month and may end early
    inside it; every run may start late inside its first month, unless
    ``start`` cuts into it. Later runs of one symbol (a relisting) add
    ``-r2``, ``-r3`` by their position in the file.
    """

    specs: list[BinanceSpec] = []
    for item in universe.symbols:
        for dataset in datasets:
            kind = "klines" if dataset == "klines" else "funding"
            runs = item.klines if dataset == "klines" else item.funding
            for position, run in enumerate(runs, start=1):
                spec = _run_spec(
                    group, universe, item.symbol, dataset, kind, position, run, start, enabled
                )
                if spec is not None:
                    specs.append(spec)
    return tuple(specs)


def _run_spec(
    group: str,
    universe: Universe,
    symbol: str,
    dataset: str,
    kind: str,
    position: int,
    run: MonthRun,
    start: date | None,
    enabled: bool,
) -> BinanceSpec | None:
    listed = run.last >= universe.latest_month
    end = None if listed else next_month(run.last) - timedelta(days=1)
    first = run.first
    open_start = True
    if start is not None and start > first:
        if end is not None and start > end:
            return None
        first = start
        open_start = False
    suffix = "" if position == 1 else f"-r{position}"
    if dataset == "klines":
        granularity = "monthly_with_daily_tail" if listed else "monthly"
        interval: str | None = universe.interval
    else:
        granularity = "monthly"
        interval = None
    return BinanceSpec(
        id=f"{group}-{kind}-{symbol.lower()}{suffix}",
        market=universe.market,
        dataset=dataset,
        symbol=symbol,
        interval=interval,
        start=first,
        end=end,
        end_token="today" if end is None else end.isoformat(),
        granularity=granularity,
        enabled=enabled,
        group=group,
        open_start=open_start,
        open_end=not listed,
    )


def run_universe(
    *,
    out: Path,
    today: date,
    quote: str,
    interval: str,
    requests_per_second: float,
    env: Mapping[str, str],
    transport: Transport | None = None,
    sleeper: Sleeper | None = None,
) -> int:
    """Write a new universe file. An existing file is never replaced."""

    if out.exists():
        raise HistEtlError(f"refusing to replace {out}; universe files are dated", exit_code=2)
    retries = int(env.get("HIST_ETL_MAX_RETRIES") or 5)
    timeout = float(env.get("HIST_ETL_HTTP_TIMEOUT_SECONDS") or 60.0)
    if requests_per_second <= 0 or retries < 1 or timeout <= 0:
        raise HistEtlError("universe needs a positive rate, retry count, and timeout")
    wait = sleeper if sleeper is not None else _sleep
    lister = BucketLister(
        transport if transport is not None else build_transport(timeout),
        limiter=RateLimiter(requests_per_second, wait),
        max_retries=retries,
        sleeper=wait,
    )
    universe = discover_universe(
        lister, as_of=today, quote=quote, interval=interval, progress=_progress
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    partial.write_text(render_universe(universe), encoding="utf-8")
    os.replace(partial, out)
    listed = sum(
        1
        for item in universe.symbols
        if any(
            runs and runs[-1].last >= universe.latest_month for runs in (item.klines, item.funding)
        )
    )
    print(
        f"universe\t{len(universe.symbols)} symbols\t{listed} listed\t"
        f"{len(universe.symbols) - listed} not listed\t{len(universe.excluded)} excluded\t{out}"
    )
    return 0


def _progress(done: int, total: int) -> None:
    if done == total or done % 100 == 0:
        print(f"scanned\t{done}/{total}", file=sys.stderr, flush=True)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)
