"""Declarative dataset types for the historical archive ETL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# Binance Vision: spot timestamps are microseconds from this date onward.
# USD-M examples in the public-data README remain milliseconds.
SPOT_MICROSECOND_START = date(2025, 1, 1)
MICROSECOND_THRESHOLD = 100_000_000_000_000
MILLISECOND_THRESHOLD = 100_000_000_000

# A Binance symbol the manifest, paths, and view names can hold.
SYMBOL_PATTERN = re.compile(r"[A-Z0-9]{2,20}")

KLINE_DATASETS = frozenset({"klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines"})
DAILY_ONLY_DATASETS = frozenset({"metrics"})
MONTHLY_ONLY_DATASETS = frozenset({"fundingRate"})
BINANCE_DATASETS = KLINE_DATASETS | DAILY_ONLY_DATASETS | MONTHLY_ONLY_DATASETS | {"aggTrades"}

# USD-M metrics rows are 5-minute samples (create_time steps of 300s).
METRICS_SAMPLE_SECONDS = 300

INTERVAL_SECONDS: dict[str, int] = {
    "1s": 1,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "6h": 21_600,
    "8h": 28_800,
    "12h": 43_200,
    "1d": 86_400,
    "3d": 259_200,
    "1w": 604_800,
}

KRAKEN_MINUTES_TO_SLUG: dict[int, str] = {
    1: "1m",
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h",
    240: "4h",
    720: "12h",
    1440: "1d",
}

USER_AGENT = "hyperliquid-bot-hist-etl/1"
BINANCE_VISION_BASE = "https://data.binance.vision/"
# The public S3 bucket behind data.binance.vision. Its ListObjects (v1) XML is
# the only index of which symbols and months exist, delisted ones included.
BINANCE_VISION_LISTING = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
# Public info endpoint. fundingHistory needs no key and no account.
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
# REST requests share 1200 weight per minute per IP. An info request weighs 20,
# and fundingHistory adds 1 per 20 rows returned, so a full 500-row page is 45:
# at most ~26 pages a minute. 0.3 per second (810 weight a minute) leaves room
# for anything else on the same IP.
# https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits
HYPERLIQUID_MAX_REQUESTS_PER_SECOND = 0.3


def parquet_slug(dataset: str, interval: str | None) -> str:
    if dataset == "aggTrades":
        return "aggtrades"
    if dataset == "fundingRate":
        return "funding"
    if dataset == "metrics":
        return "metrics"
    if dataset == "klines":
        if interval is None:
            raise ValueError("klines require an interval")
        return f"klines_{interval}"
    prefixes = {
        "markPriceKlines": "mark_klines",
        "indexPriceKlines": "index_klines",
        "premiumIndexKlines": "premium_klines",
    }
    prefix = prefixes.get(dataset)
    if prefix is None or interval is None:
        raise ValueError(f"unsupported dataset {dataset}")
    return f"{prefix}_{interval}"


@dataclass(frozen=True, slots=True)
class BinanceSpec:
    """One Binance Vision series over an inclusive UTC date range."""

    id: str
    market: str
    dataset: str
    symbol: str
    interval: str | None
    start: date
    end: date | None
    end_token: str
    granularity: str
    enabled: bool
    # The universe entry that expanded into this series; --dataset selects it.
    group: str | None = None
    # Listing edges: the first (or last) month may start late (or end early),
    # because the contract was listed (or delisted) that month. Only those
    # outer bars may be absent; a hole between two bars is still a gap.
    open_start: bool = False
    open_end: bool = False


@dataclass(frozen=True, slots=True)
class KrakenSpec:
    """Kraken OHLCVT quarterly zip ingest for selected pairs."""

    id: str
    pairs: tuple[str, ...]
    intervals: tuple[str, ...]
    zip_glob: str
    enabled: bool
    url: str | None


@dataclass(frozen=True, slots=True)
class HyperliquidFundingSpec:
    """Hyperliquid perp funding settlements over an inclusive UTC date range.

    ``funding_interval_hours`` is the venue's settlement cadence over that
    range; every slot of that length should hold one print. ``known_holes``
    are settlement slots the venue never published, acknowledged by an
    operator so they are not reported again.
    """

    id: str
    coin: str
    start: date
    end: date | None
    end_token: str
    funding_interval_hours: int
    known_holes: tuple[datetime, ...]
    enabled: bool


@dataclass(frozen=True, slots=True)
class HistManifest:
    min_free_bytes: int
    requests_per_second: float
    max_retries: int
    timeout_seconds: float
    binance: tuple[BinanceSpec, ...]
    kraken: tuple[KrakenSpec, ...]
    hyperliquid: tuple[HyperliquidFundingSpec, ...] = ()
    # Ids of binance_universe entries; each selects the specs it expanded into.
    binance_groups: tuple[str, ...] = ()
    # (id, file) of each binance_universe entry, the file as the manifest writes it.
    binance_universe_files: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ArchivePlan:
    """One zip the Binance planner expects to exist for a dataset."""

    dataset_id: str
    market: str
    dataset: str
    symbol: str
    interval: str | None
    granularity: str
    period: str
    month: str
    filename: str
    url: str
    checksum_url: str
    canonical_relative: str
    canonical_path: Path
    local_path: Path | None = None
    action: str = "download"
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class Gap:
    kind: str
    dataset_id: str
    detail: str
    samples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "dataset_id": self.dataset_id,
            "detail": self.detail,
            "samples": list(self.samples),
        }


@dataclass(frozen=True, slots=True)
class SourceDigest:
    name: str
    sha256: str
    path: Path
