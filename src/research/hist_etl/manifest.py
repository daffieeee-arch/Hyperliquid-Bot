"""Load the TOML dataset manifest. Paths are not baked into the file."""

from __future__ import annotations

import os
import re
import tomllib
from datetime import date
from pathlib import Path

from research.hist_etl.errors import HistEtlError
from research.hist_etl.models import (
    BINANCE_DATASETS,
    DAILY_ONLY_DATASETS,
    INTERVAL_SECONDS,
    KLINE_DATASETS,
    KRAKEN_MINUTES_TO_SLUG,
    MONTHLY_ONLY_DATASETS,
    BinanceSpec,
    HistManifest,
    KrakenSpec,
)

_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
_SYMBOL = re.compile(r"[A-Z0-9]{2,20}")
_GRANULARITY = frozenset({"monthly", "daily", "monthly_with_daily_tail"})


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
    if not isinstance(binance_raw, list) or not isinstance(kraken_raw, list):
        raise HistEtlError("binance and kraken must be arrays of tables")
    binance = tuple(_binance_spec(item) for item in binance_raw)
    kraken = tuple(_kraken_spec(item) for item in kraken_raw)
    ids = [spec.id for spec in binance] + [spec.id for spec in kraken]
    if len(ids) != len(set(ids)):
        raise HistEtlError("dataset ids must be unique")
    return HistManifest(
        min_free_bytes=min_free,
        requests_per_second=per_second,
        max_retries=retries,
        timeout_seconds=timeout,
        binance=binance,
        kraken=kraken,
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


def _kraken_spec(item: object) -> KrakenSpec:
    table = _table(item, "kraken")
    dataset_id = _ident(table, "id")
    pairs_raw = table.get("pairs", ["XBTUSD"])
    if not isinstance(pairs_raw, list) or not pairs_raw:
        raise HistEtlError(f"{dataset_id} pairs must be a non-empty list")
    pairs: list[str] = []
    for pair in pairs_raw:
        if not isinstance(pair, str) or not _SYMBOL.fullmatch(pair):
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


def select_binance(
    manifest: HistManifest, dataset_ids: tuple[str, ...] | None
) -> tuple[BinanceSpec, ...]:
    return tuple(_select(manifest.binance, dataset_ids))


def select_kraken(
    manifest: HistManifest, dataset_ids: tuple[str, ...] | None
) -> tuple[KrakenSpec, ...]:
    return tuple(_select(manifest.kraken, dataset_ids))


def _select[T: BinanceSpec | KrakenSpec](
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
    known = {spec.id for spec in manifest.binance} | {spec.id for spec in manifest.kraken}
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
    if not _SYMBOL.fullmatch(value):
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
