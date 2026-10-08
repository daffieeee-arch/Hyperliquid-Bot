"""Load the TOML dataset manifest. Paths are not baked into the file."""

from __future__ import annotations

import os
import re
import tomllib
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path

from research.hist_etl.errors import HistEtlError
from research.hist_etl.models import (
    BINANCE_DATASETS,
    DAILY_ONLY_DATASETS,
    INTERVAL_SECONDS,
    KLINE_DATASETS,
    KRAKEN_MINUTES_TO_SLUG,
    MONTHLY_ONLY_DATASETS,
    SYMBOL_PATTERN,
    BinanceSpec,
    HistManifest,
    HyperliquidFundingSpec,
    KrakenSpec,
)
from research.hist_etl.universe import UNIVERSE_DATASETS, expand_universe, load_universe

_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
_COIN = re.compile(r"[A-Z0-9]{1,20}")
_HYPERLIQUID_KEYS = frozenset(
    {
        "id",
        "dataset",
        "coin",
        "start",
        "end",
        "funding_interval_hours",
        "known_holes",
        "enabled",
    }
)
_GRANULARITY = frozenset({"monthly", "daily", "monthly_with_daily_tail"})
_UNIVERSE_KEYS = frozenset({"id", "file", "datasets", "start", "enabled"})


def default_manifest_path() -> Path:
    override = _env_manifest()
    if override is not None:
        return override
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "hist_etl" / "datasets.toml"
        if candidate.is_file():
            return candidate
    raise HistEtlError("manifest not found; pass --manifest or set HIST_ETL_MANIFEST")


def _env_manifest() -> Path | None:
    raw = os.environ.get("HIST_ETL_MANIFEST")
    if not raw:
        return None
    return Path(raw)


def load_manifest(path: Path) -> HistManifest:
    if not path.is_file():
        raise HistEtlError(f"manifest does not exist: {path}")
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HistEtlError("manifest root must be a table")
    min_free = _int_field(payload, "min_free_bytes", 10 * 1024 * 1024 * 1024)
    per_second = _float_field(payload, "requests_per_second", 2.0)
    retries = _int_field(payload, "max_retries", 5)
    timeout = _float_field(payload, "timeout_seconds", 60.0)
    if min_free < 0 or retries < 1 or per_second < 0 or timeout <= 0:
        raise HistEtlError("manifest has a non-positive limit")
    binance_raw = payload.get("binance", [])
    kraken_raw = payload.get("kraken", [])
    hyperliquid_raw = payload.get("hyperliquid", [])
    universe_raw = payload.get("binance_universe", [])
    if (
        not isinstance(binance_raw, list)
        or not isinstance(kraken_raw, list)
        or not isinstance(hyperliquid_raw, list)
        or not isinstance(universe_raw, list)
    ):
        raise HistEtlError(
            "binance, binance_universe, kraken and hyperliquid must be arrays of tables"
        )
    groups: list[str] = []
    expanded: list[BinanceSpec] = []
    for item in universe_raw:
        group, specs = _binance_universe(item, path.parent)
        groups.append(group)
        expanded.extend(specs)
    binance = tuple(_binance_spec(item) for item in binance_raw) + tuple(expanded)
    kraken = tuple(_kraken_spec(item) for item in kraken_raw)
    hyperliquid = tuple(_hyperliquid_spec(item) for item in hyperliquid_raw)
    ids = (
        [spec.id for spec in binance]
        + [spec.id for spec in kraken]
        + [spec.id for spec in hyperliquid]
        + groups
    )
    if len(ids) != len(set(ids)):
        raise HistEtlError("dataset ids must be unique")
    _require_disjoint_funding(hyperliquid)
    return HistManifest(
        min_free_bytes=min_free,
        requests_per_second=per_second,
        max_retries=retries,
        timeout_seconds=timeout,
        binance=binance,
        kraken=kraken,
        hyperliquid=hyperliquid,
        binance_groups=tuple(groups),
    )


def _binance_spec(item: object) -> BinanceSpec:
    table = _table(item, "binance")
    dataset_id = _ident(table, "id")
    market = _choice(table, "market", frozenset({"spot", "um"}))
    dataset = _choice(table, "dataset", BINANCE_DATASETS)
    symbol = _symbol(table, "symbol")
    interval = _optional_str(table, "interval")
    granularity = _choice(table, "granularity", _GRANULARITY)
    start = _date_field(table, "start")
    end, end_token = _end_field(table)
    enabled = _bool_field(table, "enabled", True)
    if dataset in KLINE_DATASETS:
        if interval is None or interval not in INTERVAL_SECONDS:
            raise HistEtlError(f"{dataset_id} requires a supported kline interval")
    elif interval is not None:
        raise HistEtlError(f"{dataset_id} does not take an interval")
    if dataset in DAILY_ONLY_DATASETS and granularity != "daily":
        raise HistEtlError(f"{dataset_id} is daily-only on Binance Vision")
    if dataset in MONTHLY_ONLY_DATASETS and granularity != "monthly":
        raise HistEtlError(f"{dataset_id} is monthly-only on Binance Vision")
    if end is not None and start > end:
        raise HistEtlError(f"{dataset_id} start is after end")
    return BinanceSpec(
        id=dataset_id,
        market=market,
        dataset=dataset,
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        end_token=end_token,
        granularity=granularity,
        enabled=enabled,
    )


def _binance_universe(item: object, manifest_dir: Path) -> tuple[str, tuple[BinanceSpec, ...]]:
    """A committed universe file, expanded into per-symbol USD-M datasets."""

    table = _table(item, "binance_universe")
    extra = sorted(set(table) - _UNIVERSE_KEYS)
    if extra:
        raise HistEtlError(f"binance_universe entry has unknown keys: {', '.join(extra)}")
    group = _ident(table, "id")
    relative = _required_str(table, "file")
    parts = Path(relative).parts
    if Path(relative).is_absolute() or ".." in parts:
        raise HistEtlError(f"{group} file must be a path below the manifest directory")
    datasets_raw = table.get("datasets", sorted(UNIVERSE_DATASETS))
    if (
        not isinstance(datasets_raw, list)
        or not datasets_raw
        or any(
            not isinstance(value, str) or value not in UNIVERSE_DATASETS for value in datasets_raw
        )
        or len(set(datasets_raw)) != len(datasets_raw)
    ):
        raise HistEtlError(f"{group} datasets must name klines and/or fundingRate once each")
    start = _date_field(table, "start") if "start" in table else None
    # Month files are shared with other datasets of the same series, and a
    # run's first month may start late. A cut on the first of a month keeps
    # both exact: every month is whole, and only a run's own first month may
    # start late.
    if start is not None and start.day != 1:
        raise HistEtlError(f"{group} start must be the first day of a month")
    universe = load_universe(manifest_dir / relative)
    specs = expand_universe(
        group,
        universe,
        datasets=tuple(str(value) for value in datasets_raw),
        start=start,
        # Opt-in: a universe is thousands of archives, never a routine sync.
        enabled=_bool_field(table, "enabled", False),
    )
    return group, specs


def _kraken_spec(item: object) -> KrakenSpec:
    table = _table(item, "kraken")
    dataset_id = _ident(table, "id")
    pairs_raw = table.get("pairs", ["XBTUSD"])
    if not isinstance(pairs_raw, list) or not pairs_raw:
        raise HistEtlError(f"{dataset_id} pairs must be a non-empty list")
    pairs: list[str] = []
    for pair in pairs_raw:
        if not isinstance(pair, str) or not SYMBOL_PATTERN.fullmatch(pair):
            raise HistEtlError(f"{dataset_id} has an invalid pair")
        pairs.append(pair)
    intervals_raw = table.get("intervals", list(KRAKEN_MINUTES_TO_SLUG.values()))
    if not isinstance(intervals_raw, list) or not intervals_raw:
        raise HistEtlError(f"{dataset_id} intervals must be a non-empty list")
    allowed = set(KRAKEN_MINUTES_TO_SLUG.values())
    intervals: list[str] = []
    for interval in intervals_raw:
        if not isinstance(interval, str) or interval not in allowed:
            raise HistEtlError(f"{dataset_id} has an unsupported Kraken interval")
        intervals.append(interval)
    zip_glob = _required_str(table, "zip_glob")
    if zip_glob.startswith("/") or ".." in Path(zip_glob).parts:
        raise HistEtlError(f"{dataset_id} zip_glob must be a relative path")
    url = _optional_str(table, "url")
    if url is not None and not url.startswith("https://"):
        raise HistEtlError(f"{dataset_id} url must be https")
    return KrakenSpec(
        id=dataset_id,
        pairs=tuple(pairs),
        intervals=tuple(intervals),
        zip_glob=zip_glob,
        enabled=_bool_field(table, "enabled", True),
        url=url,
    )


def _hyperliquid_spec(item: object) -> HyperliquidFundingSpec:
    table = _table(item, "hyperliquid")
    extra = sorted(set(table) - _HYPERLIQUID_KEYS)
    if extra:
        raise HistEtlError(f"hyperliquid entry has unknown keys: {', '.join(extra)}")
    dataset_id = _ident(table, "id")
    _choice(table, "dataset", frozenset({"funding"}))
    coin = _required_str(table, "coin")
    if not _COIN.fullmatch(coin):
        raise HistEtlError(f"invalid coin {coin}")
    start = _date_field(table, "start")
    end, end_token = _end_field(table)
    if end is not None and start > end:
        raise HistEtlError(f"{dataset_id} start is after end")
    hours = _int_field(table, "funding_interval_hours", 1)
    if hours < 1 or 24 % hours != 0:
        raise HistEtlError(f"{dataset_id} funding_interval_hours must divide 24")
    holes_raw = table.get("known_holes", [])
    if not isinstance(holes_raw, list):
        raise HistEtlError(f"{dataset_id} known_holes must be a list")
    holes = tuple(_hole(dataset_id, raw, hours) for raw in holes_raw)
    return HyperliquidFundingSpec(
        id=dataset_id,
        coin=coin,
        start=start,
        end=end,
        end_token=end_token,
        funding_interval_hours=hours,
        known_holes=holes,
        enabled=_bool_field(table, "enabled", True),
    )


def _hole(dataset_id: str, raw: object, hours: int) -> datetime:
    """One acknowledged missing settlement slot, as a UTC ISO timestamp."""

    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise HistEtlError(f"{dataset_id} known_holes entries must be UTC times ending in Z")
    try:
        moment = datetime.fromisoformat(raw.removesuffix("Z")).replace(tzinfo=UTC)
    except ValueError as exc:
        raise HistEtlError(f"{dataset_id} has an invalid known_holes entry {raw}") from exc
    if moment.minute or moment.second or moment.microsecond or moment.hour % hours:
        raise HistEtlError(f"{dataset_id} known_holes entry {raw} is not a settlement slot")
    return moment


def _require_disjoint_funding(specs: tuple[HyperliquidFundingSpec, ...]) -> None:
    """Two datasets for one coin must not cover the same day, or rows would repeat."""

    by_coin: dict[str, list[HyperliquidFundingSpec]] = {}
    for spec in specs:
        by_coin.setdefault(spec.coin, []).append(spec)
    for coin, group in by_coin.items():
        ordered = sorted(group, key=lambda spec: spec.start)
        for earlier, later in pairwise(ordered):
            if earlier.end is None or earlier.end >= later.start:
                raise HistEtlError(f"hyperliquid {coin} datasets overlap: {earlier.id}, {later.id}")


def select_binance(
    manifest: HistManifest, dataset_ids: tuple[str, ...] | None
) -> tuple[BinanceSpec, ...]:
    """Enabled specs, or the ones named by id or by their universe group."""

    if dataset_ids:
        return tuple(
            spec
            for spec in manifest.binance
            if spec.id in dataset_ids or (spec.group is not None and spec.group in dataset_ids)
        )
    return tuple(spec for spec in manifest.binance if spec.enabled)


def select_kraken(
    manifest: HistManifest, dataset_ids: tuple[str, ...] | None
) -> tuple[KrakenSpec, ...]:
    return tuple(_select(manifest.kraken, dataset_ids))


def select_hyperliquid(
    manifest: HistManifest, dataset_ids: tuple[str, ...] | None
) -> tuple[HyperliquidFundingSpec, ...]:
    return tuple(_select(manifest.hyperliquid, dataset_ids))


def _select[T: KrakenSpec | HyperliquidFundingSpec](
    specs: tuple[T, ...], dataset_ids: tuple[str, ...] | None
) -> list[T]:
    if dataset_ids:
        known = {spec.id for spec in specs}
        missing = [dataset_id for dataset_id in dataset_ids if dataset_id not in known]
        # Missing is checked by the caller across both venues.
        chosen = [spec for spec in specs if spec.id in dataset_ids]
        if missing and not chosen:
            return []
        return chosen
    return [spec for spec in specs if spec.enabled]


def assert_known_ids(manifest: HistManifest, dataset_ids: tuple[str, ...] | None) -> None:
    if not dataset_ids:
        return
    known = (
        {spec.id for spec in manifest.binance}
        | set(manifest.binance_groups)
        | {spec.id for spec in manifest.kraken}
        | {spec.id for spec in manifest.hyperliquid}
    )
    missing = [dataset_id for dataset_id in dataset_ids if dataset_id not in known]
    if missing:
        raise HistEtlError(f"unknown dataset id: {', '.join(missing)}")


def _table(item: object, label: str) -> dict[str, object]:
    if not isinstance(item, dict):
        raise HistEtlError(f"{label} entry must be a table")
    return {str(key): value for key, value in item.items()}


def _required_str(table: dict[str, object], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise HistEtlError(f"manifest field {key} must be a string")
    return value


def _optional_str(table: dict[str, object], key: str) -> str | None:
    if key not in table or table[key] is None:
        return None
    value = table[key]
    if not isinstance(value, str) or not value:
        raise HistEtlError(f"manifest field {key} must be a string")
    return value


def _ident(table: dict[str, object], key: str) -> str:
    value = _required_str(table, key)
    if not _ID.fullmatch(value):
        raise HistEtlError(f"invalid dataset id {value}")
    return value


def _symbol(table: dict[str, object], key: str) -> str:
    value = _required_str(table, key)
    if not SYMBOL_PATTERN.fullmatch(value):
        raise HistEtlError(f"invalid symbol {value}")
    return value


def _choice(table: dict[str, object], key: str, allowed: frozenset[str]) -> str:
    value = _required_str(table, key)
    if value not in allowed:
        raise HistEtlError(f"unsupported {key} {value}")
    return value


def _bool_field(table: dict[str, object], key: str, default: bool) -> bool:
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, bool):
        raise HistEtlError(f"manifest field {key} must be a boolean")
    return value


def _int_field(table: dict[str, object], key: str, default: int) -> int:
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise HistEtlError(f"manifest field {key} must be an integer")
    return value


def _float_field(table: dict[str, object], key: str, default: float) -> float:
    if key not in table:
        return default
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HistEtlError(f"manifest field {key} must be a number")
    return float(value)


def _date_field(table: dict[str, object], key: str) -> date:
    raw = _required_str(table, key)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HistEtlError(f"manifest field {key} must be YYYY-MM-DD") from exc


def _end_field(table: dict[str, object]) -> tuple[date | None, str]:
    raw = _required_str(table, "end")
    if raw == "today":
        return None, "today"
    try:
        return date.fromisoformat(raw), raw
    except ValueError as exc:
        raise HistEtlError("manifest field end must be YYYY-MM-DD or today") from exc
