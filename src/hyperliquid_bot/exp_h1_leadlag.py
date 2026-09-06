"""PAPER-only H1 Binance-impulse / Hyperliquid-lag scaffold (WP-Q2).

This module is a research entrypoint, not a capture tool and not an execution
path. It never signs orders, never selects LIVE/TESTNET, and never claims a
trading edge.

It consumes a WP-Q1 ``panel_hl_binance`` panel (or builds one via that module),
masks gap/incomplete buckets, and scores one predeclared signal at three fixed
Δ horizons under 1x / 1.5x / 2x Hyperliquid cost stress.

The Binance impulse instrument is explicit. Default is USD-M mark
(perp-to-perp vs Hyperliquid BTC-PERP). Spot last/BBO is opt-in and never
falls through to USD-M agg or mark. Missing required-family prices skip that
observation. Issue #52 fixed USD-M bookTicker WebSocket routing only; it did
not fix this Quant instrument identity.

Verdicts are ``noise`` or ``not_enough_data`` only. This module refuses to
assign ``edge``. Positive-looking after-cost metrics are still ``noise``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, assert_never, cast

import duckdb

from .panel_hl_binance import (
    DEFAULT_BUCKET_MS,
    NS_PER_MS,
    PANEL_PARQUET_NAME,
    PANEL_SUMMARY_NAME,
    PANEL_VERSION,
    PanelVerdictName,
    build_hl_binance_panel,
    require_bucket_ms,
)

TRADING_MODE: Final = "PAPER"
EXPERIMENT_ID: Final = "exp_h1_leadlag"
STRATEGY_MODEL_VERSION: Final = "h1-leadlag-scaffold-v2"
FEATURE_SET_PREFIX: Final = "bn_prior_bucket_return_sign_to_hl_forward_return"
COMMIT_SHA_UNSET: Final = "UNSET"
PROMOTION_DECISION: Final = "forbidden"
SUMMARY_JSON_NAME: Final = "h1-leadlag-summary.json"

DELTA_BUCKETS: Final[tuple[int, int, int]] = (1, 5, 30)
SIGNAL_LOOKBACK_BUCKETS: Final = 1
COST_MULTIPLIERS: Final[tuple[Decimal, Decimal, Decimal]] = (
    Decimal("1.0"),
    Decimal("1.5"),
    Decimal("2.0"),
)
HL_TAKER_FEE: Final = Decimal("0.00045")
FALLBACK_HALF_SPREAD: Final = Decimal("0.00005")
MIN_USABLE_BUCKETS: Final = 16
MIN_TRADES_PER_HORIZON: Final = 8

_NO_EDGE_NOTE: Final = (
    "PAPER research only. Descriptive H1 scaffold; never a trading result "
    "and never a promotion signal."
)
_IDENTITY_NOTE: Final = (
    "Binance impulse identity is explicit and fail-closed. Spot last/BBO, "
    "USD-M aggTrade, and USD-M mark are distinct series and are never mixed. "
    "Issue #52 was USD-M bookTicker transport routing only."
)
_SCAFFOLD_NOTE: Final = (
    "WP-Q2 H1 may only emit noise or not_enough_data. Positive after-cost "
    "metrics do not change the verdict."
)
_OOS_NOTE: Final = (
    "In-sample metrics are descriptive only. The OOS UTC-ns range decides "
    "not_enough_data versus noise. Promotion language is forbidden."
)

H1InputMode = Literal["panel_files", "reconstructable_build"]


class BinanceImpulseInstrument(StrEnum):
    """Explicit Binance impulse identity. Families are never mixed."""

    SPOT = "binance_spot"
    USDM_AGG = "binance_usdm_agg"
    USDM_MARK = "binance_usdm_mark"


DEFAULT_BINANCE_IMPULSE_INSTRUMENT: Final = BinanceImpulseInstrument.USDM_MARK
FEATURE_SET: Final = f"{FEATURE_SET_PREFIX}/{DEFAULT_BINANCE_IMPULSE_INSTRUMENT.value}"


class H1VerdictName(StrEnum):
    """Closed H1 verdict set. Reserved promotion tokens are not members."""

    NOISE = "noise"
    NOT_ENOUGH_DATA = "not_enough_data"


def feature_set_for(instrument: BinanceImpulseInstrument) -> str:
    """Registry feature-set token including the chosen Binance impulse identity."""

    return f"{FEATURE_SET_PREFIX}/{instrument.value}"


def require_binance_impulse_instrument(value: object) -> BinanceImpulseInstrument:
    """Accept a closed Binance impulse identity. Cross-family tokens are rejected."""

    if type(value) is BinanceImpulseInstrument:
        return value
    if type(value) is not str:
        raise TypeError("binance_impulse_instrument must be a BinanceImpulseInstrument or its value.")
    try:
        return BinanceImpulseInstrument(value)
    except ValueError as error:
        raise ValueError(
            "binance_impulse_instrument must be binance_spot, binance_usdm_agg, "
            "or binance_usdm_mark."
        ) from error


@dataclass(frozen=True, slots=True)
class H1SampleThresholds:
    """Explicit numeric gate before H1 may emit ``noise``."""

    min_usable_buckets: int
    min_trades_per_horizon: int

    def __post_init__(self) -> None:
        if type(self.min_usable_buckets) is not int or self.min_usable_buckets < 1:
            raise ValueError("min_usable_buckets must be an integer of at least 1.")
        if type(self.min_trades_per_horizon) is not int or self.min_trades_per_horizon < 1:
            raise ValueError("min_trades_per_horizon must be an integer of at least 1.")

    def to_json_dict(self) -> dict[str, int]:
        return {
            "min_usable_buckets": self.min_usable_buckets,
            "min_trades_per_horizon": self.min_trades_per_horizon,
        }


H1_SAMPLE_THRESHOLDS: Final = H1SampleThresholds(
    min_usable_buckets=MIN_USABLE_BUCKETS,
    min_trades_per_horizon=MIN_TRADES_PER_HORIZON,
)


@dataclass(frozen=True, slots=True)
class PanelBucket:
    """One WP-Q1 receipt-clock panel row used by H1."""

    bucket_utc_ns: int
    hl_last_trade_price: Decimal | None
    hl_last_mid_price: Decimal | None
    hl_last_bid_price: Decimal | None
    hl_last_ask_price: Decimal | None
    hl_bbo_mid_proxy: Decimal | None
    hl_incomplete: bool
    hl_gap_detected: bool
    bn_spot_last_price: Decimal | None
    bn_spot_bbo_mid_proxy: Decimal | None
    bn_usdm_last_agg_price: Decimal | None
    bn_usdm_mark_price: Decimal | None
    bn_incomplete: bool
    bn_gap_detected: bool
    overlap_ok: bool


@dataclass(frozen=True, slots=True)
class H1Trade:
    """One predeclared-signal observation. Not an exchange order."""

    signal_utc_ns: int
    horizon_utc_ns: int
    delta_buckets: int
    signal: Decimal
    signed_gross_return: Decimal
    base_cost: Decimal


@dataclass(frozen=True, slots=True)
class HorizonCostMetrics:
    """Descriptive after-cost figures for one (Δ, multiplier) cell."""

    delta_buckets: int
    delta_ms: int
    cost_multiplier: Decimal
    trade_count: int
    mean_after_cost_hl_return: Decimal | None
    hit_rate: Decimal | None
    sum_pnl_units: Decimal | None
    verdict: H1VerdictName
    reason: str

    def __post_init__(self) -> None:
        _reject_edge_verdict(self.verdict)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "delta_buckets": self.delta_buckets,
            "delta_ms": self.delta_ms,
            "cost_multiplier": _decimal_text(self.cost_multiplier),
            "trade_count": self.trade_count,
            "mean_after_cost_hl_return": _optional_decimal_text(self.mean_after_cost_hl_return),
            "hit_rate": _optional_decimal_text(self.hit_rate),
            "sum_pnl_units": _optional_decimal_text(self.sum_pnl_units),
            "verdict": self.verdict.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class H1RunResult:
    """Written H1 verdict. Registry-shaped and fail-closed."""

    trading_mode: Literal["PAPER"]
    experiment_id: str
    verdict: H1VerdictName
    reasons: tuple[str, ...]
    commit_sha: str
    container_image_digest: str | None
    source_environment: str
    feature_set: str
    strategy_model_version: str
    parameters: tuple[tuple[str, object], ...]
    random_seed: int | None
    cost_assumptions: tuple[tuple[str, object], ...]
    panel_parquet: str | None
    panel_summary: str | None
    panel_version: str
    panel_verdict: str | None
    bucket_ms: int
    bucket_ns: int
    oos_start_utc_ns: int
    oos_end_utc_ns: int
    usable_bucket_count: int
    thresholds: H1SampleThresholds
    oos_metrics: tuple[HorizonCostMetrics, ...]
    in_sample_metrics: tuple[HorizonCostMetrics, ...]
    artifacts: tuple[tuple[str, object], ...]
    promotion_decision: str
    notes: tuple[str, ...]
    input_mode: H1InputMode
    summary_json: str | None

    def __post_init__(self) -> None:
        if self.trading_mode != TRADING_MODE:
            raise ValueError("H1 lead-lag research is PAPER only.")
        if self.promotion_decision != PROMOTION_DECISION:
            raise ValueError("H1 promotion_decision must remain 'forbidden'.")
        _reject_edge_verdict(self.verdict)
        for cell in (*self.oos_metrics, *self.in_sample_metrics):
            _reject_edge_verdict(cell.verdict)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "trading_mode": self.trading_mode,
            "experiment_id": self.experiment_id,
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "commit_sha": self.commit_sha,
            "container_image_digest": self.container_image_digest,
            "dataset": {
                "panel_parquet": self.panel_parquet,
                "panel_summary": self.panel_summary,
                "panel_version": self.panel_version,
                "panel_verdict": self.panel_verdict,
                "bucket_ms": self.bucket_ms,
                "dataset_range": {
                    "oos_start_utc_ns": self.oos_start_utc_ns,
                    "oos_end_utc_ns": self.oos_end_utc_ns,
                },
            },
            "source_environment": self.source_environment,
            "feature_set": self.feature_set,
            "strategy_model_version": self.strategy_model_version,
            "parameters": dict(self.parameters),
            "random_seed": self.random_seed,
            "cost_assumptions": dict(self.cost_assumptions),
            "oos": {
                "start_utc_ns": self.oos_start_utc_ns,
                "end_utc_ns": self.oos_end_utc_ns,
            },
            "metrics": {
                "usable_bucket_count": self.usable_bucket_count,
                "thresholds": self.thresholds.to_json_dict(),
                "oos": _metrics_by_delta(self.oos_metrics),
                "in_sample": _metrics_by_delta(self.in_sample_metrics),
            },
            "artifacts": dict(self.artifacts),
            "promotion_decision": self.promotion_decision,
            "notes": list(self.notes),
            "input_mode": self.input_mode,
            "summary_json": self.summary_json,
        }


def assign_h1_verdict(name: object) -> H1VerdictName:
    """Map a verdict token. Reserved promotion tokens raise."""

    if isinstance(name, str) and name.strip().lower() == "edge":
        raise ValueError("H1 lead-lag must not assign verdict 'edge'.")
    if type(name) is H1VerdictName:
        _reject_edge_verdict(name)
        return name
    if type(name) is not str:
        raise TypeError("verdict must be an H1VerdictName or its value.")
    try:
        verdict = H1VerdictName(name)
    except ValueError as error:
        raise ValueError("H1 lead-lag verdict must be noise or not_enough_data.") from error
    _reject_edge_verdict(verdict)
    return verdict


def require_utc_ns_range(start: object, end: object) -> tuple[int, int]:
    """Accept a closed UTC-nanosecond holdout. Fractions are not supported."""

    if type(start) is not int or type(end) is not int:
        raise TypeError("OOS bounds must be built-in integers (UTC ns).")
    if start > end:
        raise ValueError("oos_start_utc_ns must be <= oos_end_utc_ns.")
    return start, end


def require_predeclared_deltas(deltas: object) -> tuple[int, ...]:
    """Accept at most three unique positive bucket lags."""

    if type(deltas) is not tuple or not deltas:
        raise TypeError("delta_buckets must be a non-empty tuple of ints.")
    if len(deltas) > 3:
        raise ValueError("H1 predeclares at most 3 Δ horizons.")
    checked: list[int] = []
    for value in deltas:
        if type(value) is not int or value < 1:
            raise ValueError("Each Δ must be a positive integer bucket count.")
        checked.append(value)
    if len(set(checked)) != len(checked):
        raise ValueError("Δ horizons must be unique.")
    return tuple(checked)


def evaluate_h1_leadlag(
    *,
    oos_start_utc_ns: int,
    oos_end_utc_ns: int,
    panel_parquet: Path | None = None,
    panel_summary: Path | None = None,
    artifact_root: Path | None = None,
    hl_run_id: str | None = None,
    bn_run_id: str | None = None,
    bucket_ms: int = DEFAULT_BUCKET_MS,
    output_dir: Path | None = None,
    commit_sha: str | None = None,
    source_environment: str = "DEV",
    thresholds: H1SampleThresholds | None = None,
    binance_impulse_instrument: BinanceImpulseInstrument | str = (
        DEFAULT_BINANCE_IMPULSE_INSTRUMENT
    ),
) -> H1RunResult:
    """Load or build a WP-Q1 panel and return a fail-closed H1 verdict."""

    oos_start, oos_end = require_utc_ns_range(oos_start_utc_ns, oos_end_utc_ns)
    resolved_instrument = require_binance_impulse_instrument(binance_impulse_instrument)
    resolved_feature_set = feature_set_for(resolved_instrument)
    resolved_thresholds = thresholds if thresholds is not None else H1_SAMPLE_THRESHOLDS
    if type(resolved_thresholds) is not H1SampleThresholds:
        raise TypeError("thresholds must be an H1SampleThresholds instance.")
    if type(source_environment) is not str or not source_environment.strip():
        raise ValueError("source_environment must be a non-empty string.")
    resolved_commit = COMMIT_SHA_UNSET if commit_sha is None else commit_sha
    if type(resolved_commit) is not str or not resolved_commit.strip():
        raise ValueError("commit_sha must be a non-empty string.")
    if output_dir is not None and not isinstance(output_dir, Path):
        raise TypeError("output_dir must be a pathlib.Path or None.")

    deltas = require_predeclared_deltas(DELTA_BUCKETS)
    input_mode, parquet_path, summary_path, panel_build_reasons = _resolve_panel_sources(
        panel_parquet=panel_parquet,
        panel_summary=panel_summary,
        artifact_root=artifact_root,
        hl_run_id=hl_run_id,
        bn_run_id=bn_run_id,
        bucket_ms=bucket_ms,
        output_dir=output_dir,
    )
    parameters = (
        ("delta_buckets", list(deltas)),
        ("signal_lookback_buckets", SIGNAL_LOOKBACK_BUCKETS),
        ("cost_multipliers", [_decimal_text(item) for item in COST_MULTIPLIERS]),
        ("binance_impulse_instrument", resolved_instrument.value),
    )
    cost_assumptions = (
        ("hl_taker_fee", _decimal_text(HL_TAKER_FEE)),
        ("fallback_half_spread", _decimal_text(FALLBACK_HALF_SPREAD)),
        ("round_trip", True),
        ("stress_multipliers", [_decimal_text(item) for item in COST_MULTIPLIERS]),
    )

    if panel_build_reasons:
        return _finish_insufficient(
            reasons=panel_build_reasons,
            input_mode=input_mode,
            parquet_path=parquet_path,
            summary_path=summary_path,
            panel_verdict=None,
            bucket_ms=require_bucket_ms(bucket_ms),
            oos_start=oos_start,
            oos_end=oos_end,
            usable_bucket_count=0,
            thresholds=resolved_thresholds,
            oos_metrics=(),
            in_sample_metrics=(),
            parameters=parameters,
            cost_assumptions=cost_assumptions,
            commit_sha=resolved_commit,
            source_environment=source_environment.strip(),
            output_dir=output_dir,
            feature_set=resolved_feature_set,
        )

    summary_payload, summary_reasons = _load_panel_summary(summary_path)
    panel_verdict = None if summary_payload is None else summary_payload.get("verdict")
    summary_bucket_ms = (
        DEFAULT_BUCKET_MS if summary_payload is None else summary_payload.get("bucket_ms")
    )
    try:
        resolved_bucket_ms = require_bucket_ms(
            DEFAULT_BUCKET_MS if summary_bucket_ms is None else summary_bucket_ms
        )
    except (TypeError, ValueError) as error:
        summary_reasons = (*summary_reasons, f"panel summary bucket_ms is invalid: {error}")
        resolved_bucket_ms = require_bucket_ms(bucket_ms)

    if summary_reasons:
        return _finish_insufficient(
            reasons=summary_reasons,
            input_mode=input_mode,
            parquet_path=parquet_path,
            summary_path=summary_path,
            panel_verdict=None if not isinstance(panel_verdict, str) else panel_verdict,
            bucket_ms=resolved_bucket_ms,
            oos_start=oos_start,
            oos_end=oos_end,
            usable_bucket_count=0,
            thresholds=resolved_thresholds,
            oos_metrics=(),
            in_sample_metrics=(),
            parameters=parameters,
            cost_assumptions=cost_assumptions,
            commit_sha=resolved_commit,
            source_environment=source_environment.strip(),
            output_dir=output_dir,
            feature_set=resolved_feature_set,
        )

    rows, load_reasons = _load_panel_buckets(parquet_path)
    if load_reasons:
        return _finish_insufficient(
            reasons=load_reasons,
            input_mode=input_mode,
            parquet_path=parquet_path,
            summary_path=summary_path,
            panel_verdict=None if not isinstance(panel_verdict, str) else panel_verdict,
            bucket_ms=resolved_bucket_ms,
            oos_start=oos_start,
            oos_end=oos_end,
            usable_bucket_count=0,
            thresholds=resolved_thresholds,
            oos_metrics=(),
            in_sample_metrics=(),
            parameters=parameters,
            cost_assumptions=cost_assumptions,
            commit_sha=resolved_commit,
            source_environment=source_environment.strip(),
            output_dir=output_dir,
            feature_set=resolved_feature_set,
        )

    bucket_ns = resolved_bucket_ms * NS_PER_MS
    by_ns = {row.bucket_utc_ns: row for row in rows}
    usable = tuple(row for row in rows if bucket_is_usable(row))
    reasons: list[str] = []
    if len(usable) < resolved_thresholds.min_usable_buckets:
        reasons.append(
            f"usable_bucket_count {len(usable)} < min_usable_buckets "
            f"{resolved_thresholds.min_usable_buckets} after gap/incomplete mask"
        )
    oos_usable = tuple(row for row in usable if oos_start <= row.bucket_utc_ns <= oos_end)
    if not oos_usable:
        reasons.append("OOS UTC-ns range does not overlap usable panel buckets")

    oos_cells: list[HorizonCostMetrics] = []
    in_sample_cells: list[HorizonCostMetrics] = []
    for delta in deltas:
        trades = _signal_trades(
            by_ns,
            bucket_ns=bucket_ns,
            delta_buckets=delta,
            instrument=resolved_instrument,
        )
        oos_trades = tuple(
            trade
            for trade in trades
            if oos_start <= trade.signal_utc_ns <= oos_end
            and oos_start <= trade.horizon_utc_ns <= oos_end
        )
        in_sample_trades = tuple(
            trade
            for trade in trades
            if trade.horizon_utc_ns < oos_start and trade.signal_utc_ns < oos_start
        )
        oos_cells.extend(
            _metrics_for_multipliers(
                oos_trades,
                delta_buckets=delta,
                bucket_ms=resolved_bucket_ms,
                thresholds=resolved_thresholds,
                slice_label="oos",
            )
        )
        in_sample_cells.extend(
            _metrics_for_multipliers(
                in_sample_trades,
                delta_buckets=delta,
                bucket_ms=resolved_bucket_ms,
                thresholds=resolved_thresholds,
                slice_label="in_sample",
            )
        )
        oos_count = len(oos_trades)
        if oos_count < resolved_thresholds.min_trades_per_horizon:
            reasons.append(
                f"oos trade_count {oos_count} < min_trades_per_horizon "
                f"{resolved_thresholds.min_trades_per_horizon} for Δ={delta}"
            )

    if reasons:
        return _finish_insufficient(
            reasons=tuple(dict.fromkeys(reasons)),
            input_mode=input_mode,
            parquet_path=parquet_path,
            summary_path=summary_path,
            panel_verdict=None if not isinstance(panel_verdict, str) else panel_verdict,
            bucket_ms=resolved_bucket_ms,
            oos_start=oos_start,
            oos_end=oos_end,
            usable_bucket_count=len(usable),
            thresholds=resolved_thresholds,
            oos_metrics=tuple(oos_cells),
            in_sample_metrics=tuple(in_sample_cells),
            parameters=parameters,
            cost_assumptions=cost_assumptions,
            commit_sha=resolved_commit,
            source_environment=source_environment.strip(),
            output_dir=output_dir,
            feature_set=resolved_feature_set,
        )

    return _finish_result(
        verdict=assign_h1_verdict(H1VerdictName.NOISE),
        reasons=("H1 scaffold completed the predeclared Δ and cost grid; verdict remains noise"),
        input_mode=input_mode,
        parquet_path=parquet_path,
        summary_path=summary_path,
        panel_verdict=None if not isinstance(panel_verdict, str) else panel_verdict,
        bucket_ms=resolved_bucket_ms,
        oos_start=oos_start,
        oos_end=oos_end,
        usable_bucket_count=len(usable),
        thresholds=resolved_thresholds,
        oos_metrics=tuple(oos_cells),
        in_sample_metrics=tuple(in_sample_cells),
        parameters=parameters,
        cost_assumptions=cost_assumptions,
        commit_sha=resolved_commit,
        source_environment=source_environment.strip(),
        output_dir=output_dir,
        feature_set=resolved_feature_set,
    )


def bucket_is_usable(bucket: PanelBucket) -> bool:
    """WP-Q1 overlap plus gap/incomplete mask."""

    return (
        bucket.overlap_ok
        and not bucket.hl_incomplete
        and not bucket.bn_incomplete
        and not bucket.hl_gap_detected
        and not bucket.bn_gap_detected
    )


def hl_response_price(bucket: PanelBucket) -> Decimal | None:
    """Primary HL price: mid, else BBO mid proxy, else last trade."""

    return _first_positive(
        bucket.hl_last_mid_price,
        bucket.hl_bbo_mid_proxy,
        bucket.hl_last_trade_price,
    )


def bn_impulse_price(
    bucket: PanelBucket,
    instrument: BinanceImpulseInstrument,
) -> Decimal | None:
    """Same-family Binance impulse only. Missing family price fails closed.

    ``binance_spot`` uses ``bn_spot_last_price``, then same-family
    ``bn_spot_bbo_mid_proxy``. USD-M modes use only their named column.
    Spot and USD-M are never mixed.
    """

    match instrument:
        case BinanceImpulseInstrument.SPOT:
            return _first_positive(
                bucket.bn_spot_last_price,
                bucket.bn_spot_bbo_mid_proxy,
            )
        case BinanceImpulseInstrument.USDM_AGG:
            return _first_positive(bucket.bn_usdm_last_agg_price)
        case BinanceImpulseInstrument.USDM_MARK:
            return _first_positive(bucket.bn_usdm_mark_price)
        case _:
            assert_never(instrument)


def hl_half_spread(bucket: PanelBucket) -> Decimal:
    """Half-spread proxy from panel BBO, else the documented fallback."""

    bid = bucket.hl_last_bid_price
    ask = bucket.hl_last_ask_price
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return FALLBACK_HALF_SPREAD
    mid = bucket.hl_bbo_mid_proxy
    if mid is None or mid <= 0:
        mid = (bid + ask) / Decimal(2)
    if mid <= 0:
        return FALLBACK_HALF_SPREAD
    return (ask - bid) / (Decimal(2) * mid)


def _resolve_panel_sources(
    *,
    panel_parquet: Path | None,
    panel_summary: Path | None,
    artifact_root: Path | None,
    hl_run_id: str | None,
    bn_run_id: str | None,
    bucket_ms: int,
    output_dir: Path | None,
) -> tuple[H1InputMode, Path | None, Path | None, tuple[str, ...]]:
    files = panel_parquet is not None or panel_summary is not None
    reconstructable = artifact_root is not None or hl_run_id is not None or bn_run_id is not None
    if files and reconstructable:
        raise ValueError(
            "Use either --panel-parquet/--panel-summary or "
            "--artifact-root/--hl-run-id/--bn-run-id, not both."
        )
    if reconstructable:
        if artifact_root is None or hl_run_id is None or bn_run_id is None:
            raise ValueError(
                "Reconstructable H1 build requires artifact_root, hl_run_id and bn_run_id."
            )
        if output_dir is None:
            raise ValueError("Reconstructable H1 build requires output_dir for the WP-Q1 panel.")
        if not isinstance(artifact_root, Path):
            raise TypeError("artifact_root must be a pathlib.Path.")
        panel = build_hl_binance_panel(
            artifact_root=artifact_root,
            hl_run_id=hl_run_id,
            bn_run_id=bn_run_id,
            bucket_ms=bucket_ms,
            output_dir=output_dir / "panel",
        )
        if panel.verdict is not PanelVerdictName.PANEL_READY or panel.panel_parquet is None:
            reasons = (
                "WP-Q1 panel summary is not panel_ready; H1 fails closed",
                *panel.sufficiency.reasons,
            )
            return (
                "reconstructable_build",
                None,
                None if panel.summary_json is None else Path(panel.summary_json),
                reasons,
            )
        return (
            "reconstructable_build",
            Path(panel.panel_parquet),
            None if panel.summary_json is None else Path(panel.summary_json),
            (),
        )
    if panel_parquet is None or panel_summary is None:
        raise ValueError("H1 requires panel_parquet and panel_summary, or reconstructable run ids.")
    if not isinstance(panel_parquet, Path) or not isinstance(panel_summary, Path):
        raise TypeError("panel_parquet and panel_summary must be pathlib.Path values.")
    missing: list[str] = []
    if not panel_parquet.is_file():
        missing.append(f"panel parquet is missing: {panel_parquet}")
    if not panel_summary.is_file():
        missing.append(f"panel summary is missing: {panel_summary}")
    return "panel_files", panel_parquet, panel_summary, tuple(missing)


def _load_panel_summary(path: Path | None) -> tuple[dict[str, object] | None, tuple[str, ...]]:
    if path is None:
        return None, ("panel summary path is missing",)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, (f"panel summary is not valid UTF-8 JSON: {error}",)
    if type(loaded) is not dict:
        return None, ("panel summary root is not an object",)
    verdict = loaded.get("verdict")
    if verdict != PanelVerdictName.PANEL_READY.value:
        return loaded, (
            f"WP-Q1 panel summary is not panel_ready; H1 fails closed (verdict={verdict!r})",
        )
    return loaded, ()


def _load_panel_buckets(path: Path | None) -> tuple[tuple[PanelBucket, ...], tuple[str, ...]]:
    if path is None or not path.is_file():
        return (), ("panel parquet is missing",)
    escaped = _sql_string(path.resolve().as_posix())
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            f"""
            SELECT
                bucket_utc_ns,
                hl_last_trade_price,
                hl_last_mid_price,
                hl_last_bid_price,
                hl_last_ask_price,
                hl_bbo_mid_proxy,
                hl_incomplete,
                hl_gap_detected,
                bn_spot_last_price,
                bn_spot_bbo_mid_proxy,
                bn_usdm_last_agg_price,
                bn_usdm_mark_price,
                bn_incomplete,
                bn_gap_detected,
                overlap_ok
            FROM read_parquet('{escaped}')
            ORDER BY bucket_utc_ns
            """
        ).fetchall()
    except (OSError, RuntimeError, ValueError, duckdb.Error) as error:
        return (), (f"panel parquet could not be read: {error}",)
    finally:
        connection.close()

    buckets: list[PanelBucket] = []
    for row in rows:
        if not row or row[0] is None:
            continue
        buckets.append(
            PanelBucket(
                bucket_utc_ns=int(cast(int, row[0])),
                hl_last_trade_price=_decimal_or_none(row[1]),
                hl_last_mid_price=_decimal_or_none(row[2]),
                hl_last_bid_price=_decimal_or_none(row[3]),
                hl_last_ask_price=_decimal_or_none(row[4]),
                hl_bbo_mid_proxy=_decimal_or_none(row[5]),
                hl_incomplete=bool(row[6]),
                hl_gap_detected=bool(row[7]),
                bn_spot_last_price=_decimal_or_none(row[8]),
                bn_spot_bbo_mid_proxy=_decimal_or_none(row[9]),
                bn_usdm_last_agg_price=_decimal_or_none(row[10]),
                bn_usdm_mark_price=_decimal_or_none(row[11]),
                bn_incomplete=bool(row[12]),
                bn_gap_detected=bool(row[13]),
                overlap_ok=bool(row[14]),
            )
        )
    if not buckets:
        return (), ("panel parquet has no bucket rows",)
    return tuple(buckets), ()


def _signal_trades(
    by_ns: Mapping[int, PanelBucket],
    *,
    bucket_ns: int,
    delta_buckets: int,
    instrument: BinanceImpulseInstrument,
) -> tuple[H1Trade, ...]:
    lookback_ns = SIGNAL_LOOKBACK_BUCKETS * bucket_ns
    horizon_ns = delta_buckets * bucket_ns
    trades: list[H1Trade] = []
    for stamp in sorted(by_ns):
        bucket = by_ns[stamp]
        prior = by_ns.get(stamp - lookback_ns)
        later = by_ns.get(stamp + horizon_ns)
        if prior is None or later is None:
            continue
        if not (bucket_is_usable(bucket) and bucket_is_usable(prior) and bucket_is_usable(later)):
            continue
        bn_now = bn_impulse_price(bucket, instrument)
        bn_prev = bn_impulse_price(prior, instrument)
        hl_now = hl_response_price(bucket)
        hl_later = hl_response_price(later)
        if bn_now is None or bn_prev is None or hl_now is None or hl_later is None:
            continue
        bn_return = (bn_now - bn_prev) / bn_prev
        if bn_return == 0:
            continue
        signal = Decimal(1) if bn_return > 0 else Decimal(-1)
        signed = signal * ((hl_later - hl_now) / hl_now)
        base_cost = (Decimal(2) * HL_TAKER_FEE) + hl_half_spread(bucket) + hl_half_spread(later)
        trades.append(
            H1Trade(
                signal_utc_ns=stamp,
                horizon_utc_ns=later.bucket_utc_ns,
                delta_buckets=delta_buckets,
                signal=signal,
                signed_gross_return=signed,
                base_cost=base_cost,
            )
        )
    return tuple(trades)


def _metrics_for_multipliers(
    trades: tuple[H1Trade, ...],
    *,
    delta_buckets: int,
    bucket_ms: int,
    thresholds: H1SampleThresholds,
    slice_label: str,
) -> tuple[HorizonCostMetrics, ...]:
    cells: list[HorizonCostMetrics] = []
    for multiplier in COST_MULTIPLIERS:
        cells.append(
            _cell_metrics(
                trades,
                delta_buckets=delta_buckets,
                bucket_ms=bucket_ms,
                multiplier=multiplier,
                thresholds=thresholds,
                slice_label=slice_label,
            )
        )
    return tuple(cells)


def _cell_metrics(
    trades: tuple[H1Trade, ...],
    *,
    delta_buckets: int,
    bucket_ms: int,
    multiplier: Decimal,
    thresholds: H1SampleThresholds,
    slice_label: str,
) -> HorizonCostMetrics:
    after_cost = tuple(trade.signed_gross_return - trade.base_cost * multiplier for trade in trades)
    count = len(after_cost)
    if count < thresholds.min_trades_per_horizon:
        verdict = assign_h1_verdict(H1VerdictName.NOT_ENOUGH_DATA)
        reason = (
            f"{slice_label} trade_count {count} < min_trades_per_horizon "
            f"{thresholds.min_trades_per_horizon} for Δ={delta_buckets}"
        )
        mean = None if count == 0 else sum(after_cost, Decimal(0)) / Decimal(count)
        hits = sum(1 for value in after_cost if value > 0)
        hit_rate = None if count == 0 else Decimal(hits) / Decimal(count)
        total = None if count == 0 else sum(after_cost, Decimal(0))
    else:
        verdict = assign_h1_verdict(H1VerdictName.NOISE)
        reason = (
            f"{slice_label} Δ={delta_buckets} x{multiplier} is a descriptive "
            "scaffold cell; verdict remains noise"
        )
        mean = sum(after_cost, Decimal(0)) / Decimal(count)
        hits = sum(1 for value in after_cost if value > 0)
        hit_rate = Decimal(hits) / Decimal(count)
        total = sum(after_cost, Decimal(0))
    return HorizonCostMetrics(
        delta_buckets=delta_buckets,
        delta_ms=delta_buckets * bucket_ms,
        cost_multiplier=multiplier,
        trade_count=count,
        mean_after_cost_hl_return=mean,
        hit_rate=hit_rate,
        sum_pnl_units=total,
        verdict=verdict,
        reason=reason,
    )


def _finish_insufficient(
    *,
    reasons: tuple[str, ...],
    input_mode: H1InputMode,
    parquet_path: Path | None,
    summary_path: Path | None,
    panel_verdict: str | None,
    bucket_ms: int,
    oos_start: int,
    oos_end: int,
    usable_bucket_count: int,
    thresholds: H1SampleThresholds,
    oos_metrics: tuple[HorizonCostMetrics, ...],
    in_sample_metrics: tuple[HorizonCostMetrics, ...],
    parameters: tuple[tuple[str, object], ...],
    cost_assumptions: tuple[tuple[str, object], ...],
    commit_sha: str,
    source_environment: str,
    output_dir: Path | None,
    feature_set: str,
) -> H1RunResult:
    return _finish_result(
        verdict=assign_h1_verdict(H1VerdictName.NOT_ENOUGH_DATA),
        reasons="; ".join(reasons) if reasons else "insufficient H1 sample",
        input_mode=input_mode,
        parquet_path=parquet_path,
        summary_path=summary_path,
        panel_verdict=panel_verdict,
        bucket_ms=bucket_ms,
        oos_start=oos_start,
        oos_end=oos_end,
        usable_bucket_count=usable_bucket_count,
        thresholds=thresholds,
        oos_metrics=oos_metrics,
        in_sample_metrics=in_sample_metrics,
        parameters=parameters,
        cost_assumptions=cost_assumptions,
        commit_sha=commit_sha,
        source_environment=source_environment,
        output_dir=output_dir,
        reason_tuple=reasons,
        feature_set=feature_set,
    )


def _finish_result(
    *,
    verdict: H1VerdictName,
    reasons: str,
    input_mode: H1InputMode,
    parquet_path: Path | None,
    summary_path: Path | None,
    panel_verdict: str | None,
    bucket_ms: int,
    oos_start: int,
    oos_end: int,
    usable_bucket_count: int,
    thresholds: H1SampleThresholds,
    oos_metrics: tuple[HorizonCostMetrics, ...],
    in_sample_metrics: tuple[HorizonCostMetrics, ...],
    parameters: tuple[tuple[str, object], ...],
    cost_assumptions: tuple[tuple[str, object], ...],
    commit_sha: str,
    source_environment: str,
    output_dir: Path | None,
    feature_set: str,
    reason_tuple: tuple[str, ...] | None = None,
) -> H1RunResult:
    summary_path_out: str | None = None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path_out = (output_dir / SUMMARY_JSON_NAME).resolve().as_posix()
    result = H1RunResult(
        trading_mode="PAPER",
        experiment_id=EXPERIMENT_ID,
        verdict=verdict,
        reasons=reason_tuple if reason_tuple is not None else (reasons,),
        commit_sha=commit_sha,
        container_image_digest=None,
        source_environment=source_environment,
        feature_set=feature_set,
        strategy_model_version=STRATEGY_MODEL_VERSION,
        parameters=parameters,
        random_seed=None,
        cost_assumptions=cost_assumptions,
        panel_parquet=None if parquet_path is None else parquet_path.resolve().as_posix(),
        panel_summary=None if summary_path is None else summary_path.resolve().as_posix(),
        panel_version=PANEL_VERSION,
        panel_verdict=panel_verdict,
        bucket_ms=bucket_ms,
        bucket_ns=bucket_ms * NS_PER_MS,
        oos_start_utc_ns=oos_start,
        oos_end_utc_ns=oos_end,
        usable_bucket_count=usable_bucket_count,
        thresholds=thresholds,
        oos_metrics=oos_metrics,
        in_sample_metrics=in_sample_metrics,
        artifacts=(
            ("panel_parquet", PANEL_PARQUET_NAME),
            ("panel_summary", PANEL_SUMMARY_NAME),
            ("h1_summary", SUMMARY_JSON_NAME),
        ),
        promotion_decision=PROMOTION_DECISION,
        notes=(_NO_EDGE_NOTE, _SCAFFOLD_NOTE, _OOS_NOTE, _IDENTITY_NOTE),
        input_mode=input_mode,
        summary_json=summary_path_out,
    )
    if output_dir is not None and summary_path_out is not None:
        _write_json(Path(summary_path_out), result.to_json_dict())
    return result


def _metrics_by_delta(
    cells: tuple[HorizonCostMetrics, ...],
) -> dict[str, dict[str, dict[str, object]]]:
    grouped: dict[str, dict[str, dict[str, object]]] = {}
    for cell in cells:
        delta_key = str(cell.delta_buckets)
        grouped.setdefault(delta_key, {})[_decimal_text(cell.cost_multiplier)] = cell.to_json_dict()
    return grouped


def _first_positive(*values: Decimal | None) -> Decimal | None:
    for value in values:
        if value is not None and value > 0:
            return value
    return None


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _reject_edge_verdict(verdict: H1VerdictName) -> None:
    if verdict is H1VerdictName.NOISE or verdict is H1VerdictName.NOT_ENOUGH_DATA:
        return
    raise ValueError("H1 lead-lag must not assign verdict 'edge'.")


def _write_json(path: Path, payload: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    loaded = json.loads(rendered)
    if isinstance(loaded, dict) and str(loaded.get("verdict", "")).lower() == "edge":
        raise ValueError("H1 lead-lag must not assign verdict 'edge'.")
    path.write_text(rendered + "\n", encoding="utf-8")
    return path.resolve()


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "PAPER-only H1 Binance-impulse / Hyperliquid-lag scaffold. "
            "Consumes a WP-Q1 panel. Verdicts are noise or not_enough_data only."
        )
    )
    parser.add_argument(
        "--panel-parquet",
        type=Path,
        help="WP-Q1 panel.parquet from panel_hl_binance.",
    )
    parser.add_argument(
        "--panel-summary",
        type=Path,
        help="WP-Q1 panel-summary.json from panel_hl_binance.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        help="Optional reconstructable artifact root used with both run ids.",
    )
    parser.add_argument(
        "--hl-run-id",
        help="DATA-1A run id used to build the WP-Q1 panel first.",
    )
    parser.add_argument(
        "--bn-run-id",
        help="DATA-1F run id used to build the WP-Q1 panel first.",
    )
    parser.add_argument(
        "--bucket-ms",
        type=int,
        default=DEFAULT_BUCKET_MS,
        help=(
            "Receipt-UTC bucket width used only when building a panel. "
            f"Default: {DEFAULT_BUCKET_MS} (1s)."
        ),
    )
    parser.add_argument(
        "--oos-start-utc-ns",
        type=int,
        required=True,
        help="Inclusive OOS holdout start as UTC nanoseconds. Required.",
    )
    parser.add_argument(
        "--oos-end-utc-ns",
        type=int,
        required=True,
        help="Inclusive OOS holdout end as UTC nanoseconds. Required.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional directory for h1-leadlag-summary.json (and a built panel).",
    )
    parser.add_argument(
        "--commit-sha",
        help="Optional git SHA recorded on the registry-shaped summary. Default: UNSET.",
    )
    parser.add_argument(
        "--source-environment",
        default="DEV",
        help="Registry source environment. Default: DEV.",
    )
    parser.add_argument(
        "--binance-impulse-instrument",
        choices=tuple(item.value for item in BinanceImpulseInstrument),
        default=DEFAULT_BINANCE_IMPULSE_INSTRUMENT.value,
        help=(
            "Explicit Binance impulse identity. Default binance_usdm_mark "
            "(perp-to-perp vs HL BTC-PERP). binance_spot is opt-in and never "
            "falls back to USD-M. Missing family price skips the observation."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    result = evaluate_h1_leadlag(
        panel_parquet=cast(Path | None, args.panel_parquet),
        panel_summary=cast(Path | None, args.panel_summary),
        artifact_root=cast(Path | None, args.artifact_root),
        hl_run_id=cast(str | None, args.hl_run_id),
        bn_run_id=cast(str | None, args.bn_run_id),
        bucket_ms=cast(int, args.bucket_ms),
        oos_start_utc_ns=cast(int, args.oos_start_utc_ns),
        oos_end_utc_ns=cast(int, args.oos_end_utc_ns),
        output_dir=cast(Path | None, args.output_dir),
        commit_sha=cast(str | None, args.commit_sha),
        source_environment=cast(str, args.source_environment),
        binance_impulse_instrument=cast(str, args.binance_impulse_instrument),
    )
    print(json.dumps(result.to_json_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
