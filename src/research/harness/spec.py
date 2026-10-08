"""Hypothesis pre-registration: load, validate, and sha256-lock a spec."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from research.harness.errors import LockError, SpecError
from research.harness.yaml_subset import JsonValue
from research.harness.yaml_subset import loads as load_yaml_subset

type Json = JsonValue

_HYPOTHESIS_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
_CONFIG_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,40}$")
_FEATURE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_VIEW_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_COLUMN_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_ROWS_CAP = 2_000_000
_TOP_KEYS = frozenset(
    {
        "hypothesis_id",
        "universe",
        "dataset_version",
        "h0",
        "h1",
        "alpha",
        "selection_method",
        "direction",
        "signal_feature",
        "costs",
        "split",
        "sample",
        "data",
        "features",
        "configs",
    }
)
_OPTIONAL_TOP_KEYS = frozenset({"sizing", "portfolio"})
_SIZING_METHODS = frozenset({"unit", "vol_target"})
_MAX_LEVERAGE_CAP = 100.0
_MAX_UNIVERSE_SIZE = 10_000
_BAR_BACKENDS = frozenset({"parquet", "duckdb"})
_BAR_ROLES = frozenset({"timestamp", "price", "feature", "availability", "funding"})
_PANEL_ROLES = _BAR_ROLES | {"symbol", "traded", "rank"}
_DTYPE_FOR_ROLE = {
    "timestamp": "int64",
    "availability": "int64",
    "rank": "int64",
    "price": "float64",
    "feature": "float64",
    "funding": "float64",
    "symbol": "string",
    "traded": "bool",
}


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    name: str
    dtype: str
    role: str


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    column: str
    available_at_column: str


@dataclass(frozen=True, slots=True)
class ConfigSpec:
    """One pre-registered config: a signal threshold for a bar series, or a
    quantile per leg for a panel portfolio; exactly one is set."""

    id: str
    threshold: float | None
    horizon_bars: int
    quantile: float | None = None


@dataclass(frozen=True, slots=True)
class CostSpec:
    fee_bps: float
    slippage_bps: float
    spread_bps: float
    latency_bars: int
    allow_zero_latency: bool
    # A funding-role column: the rate a long pays over each bar, settled at
    # the bar's timestamp. None means no funding is accrued.
    funding_column: str | None = None


@dataclass(frozen=True, slots=True)
class SizingSpec:
    """Position weight per trade. ``unit`` is one notional unit per trade.

    ``vol_target`` weights a trade by ``target_vol / vol`` at the decision bar,
    capped at ``max_leverage``. ``vol_feature`` is a declared feature, so its
    availability clock is audited like the signal's.
    """

    method: str
    vol_feature: str | None = None
    target_vol: float | None = None
    max_leverage: float | None = None


UNIT_SIZING = SizingSpec(method="unit")


@dataclass(frozen=True, slots=True)
class PortfolioSpec:
    """A cross-sectional portfolio over a panel (``data.backend: panel``).

    On each decision day the universe is the rows whose rank column is at
    most ``universe_size``; each config goes long the top ``quantile`` of it
    by the signal and, under ``direction: signed``, short the bottom
    ``quantile``. A leg needs ``min_names_per_leg`` names or the day is
    skipped.
    """

    universe_size: int
    min_names_per_leg: int


@dataclass(frozen=True, slots=True)
class SplitSpec:
    method: str
    train_bars: int
    test_bars: int
    holdout_bars: int


@dataclass(frozen=True, slots=True)
class SampleSpec:
    min_trades_validation: int
    min_trades_holdout: int
    min_folds: int


@dataclass(frozen=True, slots=True)
class DataSpec:
    backend: str
    parquet_path: str | None
    view: str | None
    timestamp_column: str
    price_column: str
    max_gap: int
    max_rows: int
    columns: tuple[ColumnSpec, ...]
    # Panel backend only: one row per symbol and timestamp.
    symbol_column: str | None = None
    traded_column: str | None = None
    rank_column: str | None = None


@dataclass(frozen=True, slots=True)
class HypothesisSpec:
    hypothesis_id: str
    universe: str
    dataset_version: str
    h0: str
    h1: str
    alpha: float
    selection_method: str
    direction: str
    signal_feature: str
    costs: CostSpec
    split: SplitSpec
    sample: SampleSpec
    data: DataSpec
    features: tuple[FeatureSpec, ...]
    configs: tuple[ConfigSpec, ...]
    sizing: SizingSpec = UNIT_SIZING
    portfolio: PortfolioSpec | None = None


def load_document(path: Path) -> dict[str, Json]:
    """Read a JSON or YAML-subset spec. The return value is the hash input."""

    if not path.is_file() or path.is_symlink():
        raise SpecError("Spec path must be a regular file.")
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        value = _decode_json(text)
    elif suffix in {".yaml", ".yml"}:
        value = load_yaml_subset(text)
    else:
        raise SpecError("Spec file must end in .json, .yaml, or .yml.")
    if not isinstance(value, dict):
        raise SpecError("Spec root must be a mapping.")
    return value


def canonical_bytes(document: dict[str, Json]) -> bytes:
    """Stable UTF-8 JSON used as the sha256 preimage."""

    try:
        encoded = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise SpecError("Spec is not canonical JSON.") from error
    return encoded.encode("ascii")


def spec_sha256(document: dict[str, Json]) -> str:
    """sha256 hex digest of the canonical spec. Computed before any backtest."""

    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def validate_spec(document: dict[str, Json]) -> HypothesisSpec:
    """Reject unknown keys and build the typed spec. Does not read market data."""

    _exact_with_optional(document, _TOP_KEYS, _OPTIONAL_TOP_KEYS, "spec")
    hypothesis_id = _identifier(
        _require_str(document["hypothesis_id"], "hypothesis_id", 81),
        _HYPOTHESIS_ID,
        "hypothesis_id",
    )
    universe = _require_str(document["universe"], "universe", 200)
    dataset_version = _require_str(document["dataset_version"], "dataset_version", 200)
    h0 = _require_str(document["h0"], "h0", 2000)
    h1 = _require_str(document["h1"], "h1", 2000)
    alpha = _require_number(document["alpha"], "alpha")
    if not 0.0 < alpha <= 0.2:
        raise SpecError("alpha must lie in (0, 0.2].")
    selection_method = _require_str(document["selection_method"], "selection_method", 16)
    if selection_method not in {"bonferroni", "holm", "bh"}:
        raise SpecError("selection_method must be bonferroni, holm, or bh.")
    direction = _require_str(document["direction"], "direction", 16)
    if direction not in {"signed", "long_only"}:
        raise SpecError("direction must be signed or long_only.")
    signal_feature = _identifier(
        _require_str(document["signal_feature"], "signal_feature", 41),
        _FEATURE_NAME,
        "signal_feature",
    )
    costs = _parse_costs(_require_mapping(document["costs"], "costs"))
    split = _parse_split(_require_mapping(document["split"], "split"))
    sample = _parse_sample(_require_mapping(document["sample"], "sample"))
    data = _parse_data(_require_mapping(document["data"], "data"))
    features = _parse_features(document["features"], data)
    panel = data.backend == "panel"
    if panel != ("portfolio" in document):
        raise SpecError("portfolio is required with data.backend panel and refused otherwise.")
    portfolio = (
        _parse_portfolio(_require_mapping(document["portfolio"], "portfolio")) if panel else None
    )
    configs = _parse_configs(document["configs"], panel=panel)
    if signal_feature not in {feature.name for feature in features}:
        raise SpecError("signal_feature must name a declared feature.")
    _require_funding_column(costs, data)
    sizing = (
        _parse_sizing(_require_mapping(document["sizing"], "sizing"), features)
        if "sizing" in document
        else UNIT_SIZING
    )
    if panel and sizing.method != "unit":
        raise SpecError("A panel portfolio sizes each period at unit weight; sizing must be unit.")
    decision_features = (signal_feature,) + (
        () if sizing.vol_feature is None else (sizing.vol_feature,)
    )
    _require_latency_floor(costs, features, data, decision_features)
    return HypothesisSpec(
        hypothesis_id=hypothesis_id,
        universe=universe,
        dataset_version=dataset_version,
        h0=h0,
        h1=h1,
        alpha=alpha,
        selection_method=selection_method,
        direction=direction,
        signal_feature=signal_feature,
        costs=costs,
        split=split,
        sample=sample,
        data=data,
        features=features,
        configs=configs,
        sizing=sizing,
        portfolio=portfolio,
    )


def lock_path_for(spec_path: Path) -> Path:
    """Sidecar lock path. The spec file itself is not rewritten."""

    return spec_path.with_name(spec_path.name + ".lock.json")


def write_lock(spec_path: Path, data_fingerprint: dict[str, Json]) -> tuple[Path, str]:
    """Validate the spec and write a hash lock that includes the data fingerprint."""

    document = load_document(spec_path)
    validate_spec(document)
    digest = spec_sha256(document)
    payload = {
        "spec_sha256": digest,
        "canonical_spec": document,
        "data_fingerprint": data_fingerprint,
    }
    destination = lock_path_for(spec_path)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination, digest


def verify_lock(spec_path: Path, document: dict[str, Json], digest: str) -> dict[str, Json]:
    """Fail closed unless the sidecar matches this exact canonical spec.

    Returns the locked data fingerprint. The caller compares it to the live input.
    """

    path = lock_path_for(spec_path)
    if not path.is_file() or path.is_symlink():
        raise LockError("Spec is not hash-locked. Run the lock command before run.")
    payload = _decode_json(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LockError("Lock file must be a JSON object.")
    _exact(payload, {"canonical_spec", "data_fingerprint", "spec_sha256"}, "lock")
    locked_hash = payload["spec_sha256"]
    if not isinstance(locked_hash, str) or locked_hash != digest:
        raise LockError(
            "Spec sha256 does not match the lock. Re-lock only as a new pre-registration."
        )
    canonical = payload["canonical_spec"]
    if not isinstance(canonical, dict):
        raise LockError("Lock canonical_spec must be an object.")
    if spec_sha256(canonical) != digest:
        raise LockError("Lock canonical_spec does not hash to spec_sha256.")
    fingerprint = payload["data_fingerprint"]
    if not isinstance(fingerprint, dict):
        raise LockError("Lock data_fingerprint must be an object.")
    return fingerprint


def _parse_costs(raw: dict[str, Json]) -> CostSpec:
    _exact_with_optional(
        raw,
        frozenset({"fee_bps", "slippage_bps", "spread_bps", "latency_bars"}),
        frozenset({"allow_zero_latency", "funding_column"}),
        "costs",
    )
    fee_bps = _non_negative(_require_number(raw["fee_bps"], "costs.fee_bps"), "costs.fee_bps")
    slippage_bps = _non_negative(
        _require_number(raw["slippage_bps"], "costs.slippage_bps"),
        "costs.slippage_bps",
    )
    spread_bps = _non_negative(
        _require_number(raw["spread_bps"], "costs.spread_bps"), "costs.spread_bps"
    )
    latency_bars = _require_int(raw["latency_bars"], "costs.latency_bars")
    if latency_bars < 0 or latency_bars > 100:
        raise SpecError("costs.latency_bars must lie in [0, 100].")
    allow_zero_latency = False
    if "allow_zero_latency" in raw:
        allow_zero_latency = _require_bool(raw["allow_zero_latency"], "costs.allow_zero_latency")
    funding_column = None
    if "funding_column" in raw:
        funding_column = _identifier(
            _require_str(raw["funding_column"], "costs.funding_column", 64),
            _COLUMN_NAME,
            "costs.funding_column",
        )
    return CostSpec(
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
        latency_bars=latency_bars,
        allow_zero_latency=allow_zero_latency,
        funding_column=funding_column,
    )


def _require_funding_column(costs: CostSpec, data: DataSpec) -> None:
    """A funding column is accrued only when named, and a named one must exist."""

    declared = [column.name for column in data.columns if column.role == "funding"]
    if costs.funding_column is None:
        if declared:
            raise SpecError(
                "A funding-role column is declared; name it in costs.funding_column "
                "so the cashflow is not silently ignored."
            )
        return
    if declared != [costs.funding_column]:
        raise SpecError("costs.funding_column must name the one declared funding-role column.")


def _parse_sizing(raw: dict[str, Json], features: tuple[FeatureSpec, ...]) -> SizingSpec:
    method = _require_str(raw.get("method"), "sizing.method", 16)
    if method not in _SIZING_METHODS:
        raise SpecError("sizing.method must be unit or vol_target.")
    if method == "unit":
        _exact(raw, {"method"}, "sizing")
        return UNIT_SIZING
    _exact(raw, {"method", "vol_feature", "target_vol", "max_leverage"}, "sizing")
    vol_feature = _identifier(
        _require_str(raw["vol_feature"], "sizing.vol_feature", 41),
        _FEATURE_NAME,
        "sizing.vol_feature",
    )
    if vol_feature not in {feature.name for feature in features}:
        raise SpecError("sizing.vol_feature must name a declared feature.")
    target_vol = _require_number(raw["target_vol"], "sizing.target_vol")
    if target_vol <= 0.0:
        raise SpecError("sizing.target_vol must be > 0.")
    max_leverage = _require_number(raw["max_leverage"], "sizing.max_leverage")
    if not 0.0 < max_leverage <= _MAX_LEVERAGE_CAP:
        raise SpecError(f"sizing.max_leverage must lie in (0, {_MAX_LEVERAGE_CAP:g}].")
    return SizingSpec(
        method=method,
        vol_feature=vol_feature,
        target_vol=target_vol,
        max_leverage=max_leverage,
    )


def _require_latency_floor(
    costs: CostSpec,
    features: tuple[FeatureSpec, ...],
    data: DataSpec,
    decision_features: tuple[str, ...],
) -> None:
    """Bar-timestamp clocks fill on a later bar unless zero latency is explicit.

    Every feature read at the decision bar counts: the signal, and the sizing
    volatility, which would otherwise size a fill with that bar's own close.
    """

    if costs.latency_bars >= 1 or costs.allow_zero_latency:
        return
    for feature in features:
        if feature.name in decision_features and (
            feature.available_at_column == data.timestamp_column
        ):
            raise SpecError(
                f"costs.latency_bars must be >= 1 when the {feature.name} clock is the bar "
                "timestamp. latency_bars 0 requires costs.allow_zero_latency: true."
            )


def _parse_split(raw: dict[str, Json]) -> SplitSpec:
    _exact(raw, {"method", "train_bars", "test_bars", "holdout_bars"}, "split")
    method = _require_str(raw["method"], "split.method", 16)
    if method not in {"expanding", "rolling"}:
        raise SpecError("split.method must be expanding or rolling.")
    train_bars = _positive_int(raw["train_bars"], "split.train_bars", 1_000_000)
    test_bars = _positive_int(raw["test_bars"], "split.test_bars", 1_000_000)
    holdout_bars = _positive_int(raw["holdout_bars"], "split.holdout_bars", 1_000_000)
    return SplitSpec(
        method=method,
        train_bars=train_bars,
        test_bars=test_bars,
        holdout_bars=holdout_bars,
    )


def _parse_sample(raw: dict[str, Json]) -> SampleSpec:
    _exact(
        raw,
        {"min_trades_validation", "min_trades_holdout", "min_folds"},
        "sample",
    )
    return SampleSpec(
        min_trades_validation=_positive_int(
            raw["min_trades_validation"], "sample.min_trades_validation", 1_000_000, minimum=2
        ),
        min_trades_holdout=_positive_int(
            raw["min_trades_holdout"], "sample.min_trades_holdout", 1_000_000, minimum=2
        ),
        min_folds=_positive_int(raw["min_folds"], "sample.min_folds", 10_000),
    )


def _parse_portfolio(raw: dict[str, Json]) -> PortfolioSpec:
    _exact(raw, {"universe_size", "min_names_per_leg"}, "portfolio")
    universe_size = _positive_int(
        raw["universe_size"], "portfolio.universe_size", _MAX_UNIVERSE_SIZE
    )
    min_names = _positive_int(
        raw["min_names_per_leg"], "portfolio.min_names_per_leg", universe_size
    )
    return PortfolioSpec(universe_size=universe_size, min_names_per_leg=min_names)


def _parse_data(raw: dict[str, Json]) -> DataSpec:
    backend = raw.get("backend")
    symbol_column = traded_column = rank_column = None
    if backend == "panel":
        _exact(
            raw,
            {
                "backend",
                "parquet_path",
                "timestamp_column",
                "symbol_column",
                "price_column",
                "traded_column",
                "rank_column",
                "max_gap",
                "max_rows",
                "columns",
            },
            "data",
        )
        parquet_path = _relative_path(_require_str(raw["parquet_path"], "data.parquet_path", 240))
        view = None
        symbol_column = _identifier(
            _require_str(raw["symbol_column"], "data.symbol_column", 64),
            _COLUMN_NAME,
            "data.symbol_column",
        )
        traded_column = _identifier(
            _require_str(raw["traded_column"], "data.traded_column", 64),
            _COLUMN_NAME,
            "data.traded_column",
        )
        rank_column = _identifier(
            _require_str(raw["rank_column"], "data.rank_column", 64),
            _COLUMN_NAME,
            "data.rank_column",
        )
    elif backend == "parquet":
        _exact(
            raw,
            {
                "backend",
                "parquet_path",
                "timestamp_column",
                "price_column",
                "max_gap",
                "max_rows",
                "columns",
            },
            "data",
        )
        parquet_path = _relative_path(_require_str(raw["parquet_path"], "data.parquet_path", 240))
        view = None
    elif backend == "duckdb":
        _exact(
            raw,
            {
                "backend",
                "view",
                "timestamp_column",
                "price_column",
                "max_gap",
                "max_rows",
                "columns",
            },
            "data",
        )
        view = _identifier(_require_str(raw["view"], "data.view", 64), _VIEW_NAME, "data.view")
        parquet_path = None
    else:
        raise SpecError("data.backend must be parquet, duckdb, or panel.")
    timestamp_column = _identifier(
        _require_str(raw["timestamp_column"], "data.timestamp_column", 64),
        _COLUMN_NAME,
        "data.timestamp_column",
    )
    price_column = _identifier(
        _require_str(raw["price_column"], "data.price_column", 64),
        _COLUMN_NAME,
        "data.price_column",
    )
    max_gap = _positive_int(raw["max_gap"], "data.max_gap", 10**18)
    max_rows = _positive_int(raw["max_rows"], "data.max_rows", _MAX_ROWS_CAP)
    columns = _parse_columns(raw["columns"], str(backend) == "panel")
    _require_role_column(columns, "timestamp", timestamp_column, "data.timestamp_column")
    _require_role_column(columns, "price", price_column, "data.price_column")
    if backend == "panel":
        _require_role_column(columns, "symbol", symbol_column, "data.symbol_column")
        _require_role_column(columns, "traded", traded_column, "data.traded_column")
        _require_role_column(columns, "rank", rank_column, "data.rank_column")
    return DataSpec(
        backend=str(backend),
        parquet_path=parquet_path,
        view=view,
        timestamp_column=timestamp_column,
        price_column=price_column,
        max_gap=max_gap,
        max_rows=max_rows,
        columns=columns,
        symbol_column=symbol_column,
        traded_column=traded_column,
        rank_column=rank_column,
    )


def _parse_columns(raw: Json, panel: bool) -> tuple[ColumnSpec, ...]:
    mapping = _require_mapping(raw, "data.columns")
    if not mapping:
        raise SpecError("data.columns must name at least one column.")
    roles = _PANEL_ROLES if panel else _BAR_ROLES
    columns: list[ColumnSpec] = []
    for name, value in mapping.items():
        column_name = _identifier(name, _COLUMN_NAME, "data.columns key")
        body = _require_mapping(value, f"data.columns.{column_name}")
        _exact(body, {"dtype", "role"}, f"data.columns.{column_name}")
        dtype = _require_str(body["dtype"], f"data.columns.{column_name}.dtype", 16)
        role = _require_str(body["role"], f"data.columns.{column_name}.role", 16)
        if role not in roles:
            raise SpecError(f"Column {column_name} role must be one of {', '.join(sorted(roles))}.")
        if dtype != _DTYPE_FOR_ROLE[role]:
            raise SpecError(f"data.columns.{column_name} must be {_DTYPE_FOR_ROLE[role]}.")
        columns.append(ColumnSpec(name=column_name, dtype=dtype, role=role))
    names = [column.name for column in columns]
    if len(names) != len(set(names)):
        raise SpecError("data.columns has a duplicate name.")
    return tuple(columns)


def _require_role_column(
    columns: tuple[ColumnSpec, ...], role: str, name: str | None, label: str
) -> None:
    hits = [column for column in columns if column.role == role]
    if len(hits) != 1 or hits[0].name != name:
        raise SpecError(f"{label} must be the unique {role}-role column.")


def _parse_features(raw: Json, data: DataSpec) -> tuple[FeatureSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise SpecError("features must be a non-empty list.")
    by_name = {column.name: column for column in data.columns}
    features: list[FeatureSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        body = _require_mapping(item, f"features[{index}]")
        _exact(body, {"name", "column", "available_at_column"}, f"features[{index}]")
        name = _identifier(
            _require_str(body["name"], f"features[{index}].name", 41), _FEATURE_NAME, "feature name"
        )
        column_name = _identifier(
            _require_str(body["column"], f"features[{index}].column", 64),
            _COLUMN_NAME,
            "feature column",
        )
        available = _identifier(
            _require_str(body["available_at_column"], f"features[{index}].available_at_column", 64),
            _COLUMN_NAME,
            "feature available_at_column",
        )
        if name in seen:
            raise SpecError(f"Duplicate feature name {name}.")
        seen.add(name)
        column = by_name.get(column_name)
        if column is None or column.role != "feature":
            raise SpecError(f"Feature {name} must use a feature-role column.")
        clock = by_name.get(available)
        if clock is None or clock.role not in {"availability", "timestamp"}:
            raise SpecError(
                f"Feature {name} available_at_column must be an availability or timestamp column."
            )
        features.append(FeatureSpec(name=name, column=column_name, available_at_column=available))
    return tuple(features)


def _parse_configs(raw: Json, *, panel: bool = False) -> tuple[ConfigSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise SpecError("configs must be a non-empty list.")
    knob = "quantile" if panel else "threshold"
    configs: list[ConfigSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        body = _require_mapping(item, f"configs[{index}]")
        _exact(body, {"id", knob, "horizon_bars"}, f"configs[{index}]")
        config_id = _identifier(
            _require_str(body["id"], f"configs[{index}].id", 41),
            _CONFIG_ID,
            "config id",
        )
        if config_id in seen:
            raise SpecError(f"Duplicate config id {config_id}.")
        seen.add(config_id)
        horizon_bars = _positive_int(body["horizon_bars"], f"configs[{index}].horizon_bars", 10_000)
        if panel:
            quantile = _require_number(body["quantile"], f"configs[{index}].quantile")
            # Two legs of the same quantile must not overlap.
            if not 0.0 < quantile <= 0.5:
                raise SpecError(f"configs[{index}].quantile must lie in (0, 0.5].")
            configs.append(
                ConfigSpec(
                    id=config_id, threshold=None, horizon_bars=horizon_bars, quantile=quantile
                )
            )
            continue
        threshold = _non_negative(
            _require_number(body["threshold"], f"configs[{index}].threshold"), "threshold"
        )
        configs.append(ConfigSpec(id=config_id, threshold=threshold, horizon_bars=horizon_bars))
    return tuple(configs)


def _relative_path(raw: str) -> str:
    if raw.startswith(("/", "\\", "~")) or ":" in raw:
        raise SpecError("parquet_path must be a relative path.")
    parts = Path(raw).parts
    if not parts or ".." in parts or raw.strip() != raw:
        raise SpecError("parquet_path must be a relative path without '..'.")
    return raw


def _decode_json(text: str) -> Json:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise SpecError("Spec JSON could not be parsed.") from error
    return _as_json(raw)


def _as_json(value: object) -> Json:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SpecError("Spec numbers must be finite.")
        return value
    if isinstance(value, list):
        return [_as_json(item) for item in value]
    if isinstance(value, dict):
        mapped: dict[str, Json] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SpecError("JSON keys must be strings.")
            mapped[key] = _as_json(item)
        return mapped
    raise SpecError("Spec contains an unsupported JSON value.")


def _require_mapping(value: Json, label: str) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise SpecError(f"{label} must be a mapping.")
    return value


def _require_str(value: Json, label: str, max_len: int) -> str:
    if not isinstance(value, str) or value.strip() == "" or value != value.strip():
        raise SpecError(f"{label} must be a non-empty string.")
    if len(value) > max_len or any(ord(char) < 32 for char in value):
        raise SpecError(f"{label} must be a single line up to {max_len} characters.")
    return value


def _require_bool(value: Json, label: str) -> bool:
    if not isinstance(value, bool):
        raise SpecError(f"{label} must be true or false.")
    return value


def _require_int(value: Json, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpecError(f"{label} must be an integer.")
    return value


def _require_number(value: Json, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SpecError(f"{label} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise SpecError(f"{label} must be finite.")
    return number


def _non_negative(value: float, label: str) -> float:
    if value < 0.0:
        raise SpecError(f"{label} must be >= 0.")
    return value


def _positive_int(value: Json, label: str, maximum: int, *, minimum: int = 1) -> int:
    number = _require_int(value, label)
    if number < minimum or number > maximum:
        raise SpecError(f"{label} must lie in [{minimum}, {maximum}].")
    return number


def _identifier(value: str, pattern: re.Pattern[str], label: str) -> str:
    if pattern.fullmatch(value) is None:
        raise SpecError(f"{label} has an illegal identifier {value!r}.")
    return value


def _exact_with_optional(
    data: dict[str, Json],
    required: frozenset[str],
    optional: frozenset[str],
    label: str,
) -> None:
    keys = set(data)
    if not required <= keys or keys - required - optional:
        missing = sorted(required - keys)
        extra = sorted(keys - required - optional)
        raise SpecError(f"{label} keys mismatch; missing={missing} extra={extra}.")


def _exact(data: dict[str, Json], required: frozenset[str] | set[str], label: str) -> None:
    keys = set(data)
    if keys != set(required):
        missing = sorted(set(required) - keys)
        extra = sorted(keys - set(required))
        raise SpecError(f"{label} keys mismatch; missing={missing} extra={extra}.")
