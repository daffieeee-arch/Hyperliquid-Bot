"""The Binance USD-M universe: every symbol with archives, delisted ones included.

``discover_universe`` lists the S3 bucket behind data.binance.vision, which
keeps the archives of delisted contracts. A study can therefore choose its
universe point in time (for example by trailing volume at each date) instead
of from the symbols that happen to trade today, which would be survivorship
bias. The result is committed as a dated JSON file, and ``expand_universe``
turns that file into one ``BinanceSpec`` per symbol, dataset, and run of
consecutive archive months.

An archive month is not a trading month. After a delisting, Binance Vision
can keep publishing kline months with a flat price, zero volume and zero
trades, and funding months at a constant default rate (SRMUSDT klines run to
2024-05 and funding to 2024-07). Whether a contract traded on a date is for
the reader of the bars to decide from volume and trade count.
"""

from __future__ import annotations

import http.client
import json
import math
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
    RETRYABLE_STATUS,
    RateLimiter,
    Sleeper,
    Transport,
    build_transport,
    open_with_retries,
)
from research.hist_etl.models import (
    BINANCE_VISION_LISTING,
    INTERVAL_SECONDS,
    SYMBOL_PATTERN,
    BinanceSpec,
)
from research.hist_etl.planning import latest_published_month, next_month, previous_month

FORMAT: Final = 1
SOURCE: Final = "binance-vision"
UNIVERSE_DATASETS: Final = frozenset({"klines", "fundingRate"})

_QUOTE = re.compile(r"[A-Z]{3,5}")
_MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
_S3_NS: Final = "{http://s3.amazonaws.com/doc/2006-03-01/}"
# A listing page holds at most 1000 entries, well under 1 MiB of XML.
_MAX_PAGE_BYTES: Final = 4 * 1024 * 1024
_NO_SUCH_BUCKET: Final = b"<Code>NoSuchBucket</Code>"
_TRANSIENT_ATTEMPTS: Final = 10
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
    by ``as_of``. ``still_published`` decides which runs stay open; that
    does not mean the contract still trades (see the module docstring).
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
        sleeper: Sleeper,
        base: str = BINANCE_VISION_LISTING,
    ) -> None:
        self._transport = transport
        self._limiter = limiter
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
        for attempt in range(1, _TRANSIENT_ATTEMPTS + 1):
            problem = self._attempt(url, prefix)
            if isinstance(problem, _Page):
                return problem
            transient, detail = problem
            if not transient or attempt == _TRANSIENT_ATTEMPTS:
                raise HistEtlError(f"bucket listing of {prefix} {detail}", exit_code=2)
            self._sleeper(delay)
            delay = min(delay * 2, 8.0)
        raise HistEtlError(f"bucket listing of {prefix} did not finish", exit_code=2)

    def _attempt(self, url: str, prefix: str) -> _Page | tuple[bool, str]:
        """A page, or whether the failure is worth another try and what it was.

        One failed page must not end a scan of some 1,800 listings. A dropped
        connection, a truncated or garbled body, and a 5xx or 429 are
        transient. So is NoSuchBucket: the regional endpoint now and then
        answers it for this bucket, which exists (about one listing in eight
        on 2026-10-08). Any other answer is final.
        """

        try:
            status, body = self._get(url, prefix)
        except _TooLarge:
            raise
        except (OSError, http.client.HTTPException, HistEtlError) as exc:
            return True, f"failed: {exc}"
        if status == 200:
            try:
                return _parse_page(body, prefix)
            except _Garbled as exc:
                return True, str(exc)
        if status in RETRYABLE_STATUS or (status == 404 and _NO_SUCH_BUCKET in body):
            return True, f"returned HTTP {status}"
        return False, f"returned HTTP {status}"

    def _get(self, url: str, prefix: str) -> tuple[int, bytes]:
        with open_with_retries(
            self._transport,
            "GET",
            url,
            None,
            limiter=self._limiter,
            # _page owns the retries, so one page costs at most
            # _TRANSIENT_ATTEMPTS requests.
            max_retries=1,
            sleeper=self._sleeper,
        ) as response:
            return response.status, _read_capped(response.iter_bytes(), prefix)


def _read_capped(chunks: Iterable[bytes], prefix: str) -> bytes:
    parts: list[bytes] = []
    size = 0
    for chunk in chunks:
        size += len(chunk)
        if size > _MAX_PAGE_BYTES:
            raise _TooLarge(f"bucket listing of {prefix} is too large", exit_code=2)
        parts.append(chunk)
    return b"".join(parts)


def _parse_page(body: bytes, prefix: str) -> _Page:
    # The standard parser does not fetch external entities, and the page is
    # size-capped above, so a hostile body can fail the run but not reach out.
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise _Garbled(f"is not XML: {exc}") from exc
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


class _TooLarge(HistEtlError):
    """Final: a listing page is never this large, so another try is pointless."""


class _Garbled(ValueError):
    """A 200 body that does not parse, such as one cut short."""


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
        if not SYMBOL_PATTERN.fullmatch(name)
    )
    valid = [name for name in candidates if SYMBOL_PATTERN.fullmatch(name)]
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
    symbols_raw = payload["symbols"]
    excluded_raw = payload["excluded"]
    if not isinstance(symbols_raw, dict) or not symbols_raw:
        raise HistEtlError(f"universe file {path.name} lists no symbols")
    if not isinstance(excluded_raw, dict):
        raise HistEtlError(f"universe file {path.name} excluded must be an object")
    symbols = tuple(
        _load_symbol(name, value, quote, path) for name, value in sorted(symbols_raw.items())
    )
    # Stored, not recomputed: a later change to the planner's publication rule
    # must not invalidate a committed file.
    if latest >= date(as_of.year, as_of.month, 1):
        raise HistEtlError(f"universe file {path.name} latest_month is not before as_of")
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
    if not SYMBOL_PATTERN.fullmatch(name) or not name.endswith(quote) or name == quote:
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


def still_published(run: MonthRun, universe: Universe) -> bool:
    """Whether Binance Vision may still add months to this run (see ``Universe``).

    A run that ends the month before ``latest_month`` also counts: Binance
    can still be uploading the newest month after the first Monday, and a
    run wrongly kept open shows up as a gap where a run wrongly closed would
    cut a trading contract's data off silently.
    """

    return run.last >= previous_month(universe.latest_month)


def expand_universe(
    group: str,
    universe: Universe,
    *,
    datasets: tuple[str, ...],
    start: date | None,
    enabled: bool,
) -> tuple[BinanceSpec, ...]:
    """One spec per symbol, dataset, and run of months, ids ``{group}-{kind}-{symbol}``.

    A run that is ``still_published`` stays open (``end = "today"``, with a
    daily kline tail). Any other run ends with its last month and may end early
    inside it; every run may start late inside its first month, unless
    ``start`` cuts into it. Later runs of one symbol (a relisting) add
    ``-r2``, ``-r3`` by their position in the file.
    """

    specs: list[BinanceSpec] = []
    for item in universe.symbols:
        for dataset in datasets:
            runs = item.klines if dataset == "klines" else item.funding
            for position, run in enumerate(runs, start=1):
                spec = _run_spec(
                    group=group,
                    universe=universe,
                    symbol=item.symbol,
                    dataset=dataset,
                    position=position,
                    run=run,
                    start=start,
                    enabled=enabled,
                )
                if spec is not None:
                    specs.append(spec)
    return tuple(specs)


def _run_spec(
    *,
    group: str,
    universe: Universe,
    symbol: str,
    dataset: str,
    position: int,
    run: MonthRun,
    start: date | None,
    enabled: bool,
) -> BinanceSpec | None:
    published = still_published(run, universe)
    end = None if published else next_month(run.last) - timedelta(days=1)
    first = run.first
    open_start = True
    if start is not None and start > first:
        if end is not None and start > end:
            return None
        first = start
        open_start = False
    suffix = "" if position == 1 else f"-r{position}"
    kind = "klines" if dataset == "klines" else "funding"
    if dataset == "klines":
        granularity = "monthly_with_daily_tail" if published else "monthly"
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
        open_end=not published,
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
    try:
        timeout = float(env.get("HIST_ETL_HTTP_TIMEOUT_SECONDS") or 60.0)
    except ValueError as exc:
        raise HistEtlError(f"invalid HIST_ETL_HTTP_TIMEOUT_SECONDS: {exc}") from exc
    # NaN or inf would switch the throttle or the timeout off.
    if not all(math.isfinite(value) and value > 0 for value in (requests_per_second, timeout)):
        raise HistEtlError("universe needs a finite, positive rate and timeout")
    wait = sleeper if sleeper is not None else _sleep
    lister = BucketLister(
        transport if transport is not None else build_transport(timeout),
        limiter=RateLimiter(requests_per_second, wait),
        sleeper=wait,
    )
    universe = discover_universe(
        lister, as_of=today, quote=quote, interval=interval, progress=_progress
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    text = render_universe(universe)
    try:
        # Exclusive create: a file that appeared during the scan is kept.
        with out.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise HistEtlError(
            f"refusing to replace {out}; it appeared during the scan", exit_code=2
        ) from exc
    published = sum(
        1
        for item in universe.symbols
        if any(runs and still_published(runs[-1], universe) for runs in (item.klines, item.funding))
    )
    print(
        f"universe\t{len(universe.symbols)} symbols\t{published} still published\t"
        f"{len(universe.symbols) - published} closed\t{len(universe.excluded)} excluded\t{out}"
    )
    return 0


def _progress(done: int, total: int) -> None:
    if done == total or done % 100 == 0:
        print(f"scanned\t{done}/{total}", file=sys.stderr, flush=True)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)
