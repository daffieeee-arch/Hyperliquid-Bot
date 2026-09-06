"""PAPER-only gap-aware Hyperliquid DATA-1A + Binance DATA-1F research panel.

This module is a research entrypoint, not a capture tool and not an execution
path. It never signs orders, never selects LIVE/TESTNET, and never claims a
trading result. Verdicts are ``panel_ready`` or ``not_enough_data`` only.

Input contract
--------------
Preferred reconstructable layouts (Trading retains these **outside git**):

- HL: ``<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/``
  from ``reconstructable_paths.data1a_run_paths``
- BN: ``<artifact-root>/data-1f/binance/BTCUSDT/<run_id>/``
  from ``reconstructable_paths.data1f_run_paths``

Each run directory must contain ``raw/part-*.parquet``, ``research.duckdb``
(rebuilt here from ``raw/``), and ``capture-claim.json`` with
``retained: true`` and the matching path contract, venue and product.

Ad-hoc ``--hl-parquet-dir`` / ``--hl-database`` and
``--bn-parquet-dir`` / ``--bn-database`` remain available for disposable
synthetic tests. Do not mix reconstructable and ad-hoc flags.

The entrypoint rebuilds both catalogs via ``create_research_catalog`` and
reads the existing views:

- HL: ``trades``, ``bbo``, ``derivative_context``, ``data_quality_events``
- BN: ``binance_spot_trades``, ``binance_spot_bbo``, ``binance_usdm_context``,
  ``data_quality_events``

Prices, mids, marks and funding stay text. The series clock is receipt UTC
(``received_utc_ns``). Venue event times are not used as the join clock.

Binance column families are **not interchangeable**. Spot last/BBO are
spot-market prices. USD-M ``aggTrade`` is the futures tape. USD-M mark is a
calculated fair-value / liquidation series. Consumers (including H1) must
select one family explicitly and must never blend Spot with USD-M. Issue #52
fixed USD-M bookTicker WebSocket ``/public`` vs ``/market`` routing only; it
did not fix Quant instrument identity.

Bucket join
-----------
Default bucket is **1000 ms** (1 second), configurable via ``--bucket-ms``
in ``[1, 3_600_000]``. Bucket id is ``received_utc_ns // bucket_ns``.
``bucket_utc_ns`` is the inclusive bucket start (``bucket_id * bucket_ns``).

Panel columns (Parquet, ZSTD):

- ``bucket_utc_ns``
- HL last trade / mid / bid / ask / BBO mid proxy (text), trade/BBO/mid
  counts, ``hl_incomplete``, ``hl_gap_detected`` (+ count)
- BN last spot trade / spot bid / ask / BBO mid proxy (text), USDM last
  aggregate-trade price, mark, index and funding (text), spot/USDM counts,
  ``bn_incomplete``, ``bn_gap_detected`` (+ count)
- ``bn_spot_complete`` / ``bn_usdm_complete`` — per-family presence flags
  (additive; do not replace ``bn_incomplete``)
- ``overlap_ok`` — both sides have at least one market observation in the
  bucket

A Hyperliquid bucket is complete when it has at least one trade, BBO, or
non-empty mid. A Binance bucket is complete (``bn_incomplete`` is false)
when it has at least one spot trade, spot BBO, USDM aggregate trade, or
USDM mark. That storage gate is **not** an invitation to mix families:
``bn_spot_complete`` is true only when a spot trade or spot BBO is present;
``bn_usdm_complete`` is true only when a USD-M agg or mark is present.

Fail-closed gates
-----------------
A panel is written only when every constant in
``PANEL_SUFFICIENCY_THRESHOLDS`` is met **and** reconstructable claims
(when used) are valid retained runs. Otherwise the verdict is
``not_enough_data`` and **no panel Parquet is written**.

- Missing/invalid ``capture-claim.json`` or ``retained`` is not true
- Schema versions other than ``RAW_RESEARCH_SCHEMA_VERSION``
- No market observations on either side
- No overlapping receipt-UTC span
- Overlap bucket count below ``min_overlap_buckets`` (1)
- Either-side incomplete-bucket fraction above ``max_gap_fraction`` (0.05)
  over the closed overlap window

The CLI cannot lower these thresholds. This module never assigns a verdict
other than ``panel_ready`` or ``not_enough_data``. It does not run H1/H2
lead-lag experiments (WP-Q2).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, cast

import duckdb

from .parquet_research import RESEARCH_VIEW_NAMES, create_research_catalog
from .raw_research import RAW_RESEARCH_SCHEMA_VERSION
from .reconstructable_paths import (
    DATA1A_PATH_CONTRACT_ID,
    DATA1F_PATH_CONTRACT_ID,
    DATA1F_PRODUCT,
    DATA1F_VENUE,
    Data1ARunPaths,
    Data1FRunPaths,
    data1a_run_paths,
    data1f_run_paths,
)

HL_VENUE: Final = "hyperliquid"
HL_PRODUCT: Final = "BTC-PERP"
TRADING_MODE: Final = "PAPER"
NS_PER_MS: Final = 1_000_000
DEFAULT_BUCKET_MS: Final = 1000
MIN_BUCKET_MS: Final = 1
MAX_BUCKET_MS: Final = 3_600_000
PANEL_MAX_GAP_FRACTION: Final = 0.05
PANEL_MIN_OVERLAP_BUCKETS: Final = 1
PANEL_PARQUET_NAME: Final = "panel.parquet"
PANEL_SUMMARY_NAME: Final = "panel-summary.json"
PANEL_VERSION: Final = "panel_hl_binance/wp-q1.1"

BINANCE_SPOT_FAMILY_COLUMNS: Final = (
    "bn_spot_last_price",
    "bn_spot_bid_price",
    "bn_spot_ask_price",
    "bn_spot_bbo_mid_proxy",
    "bn_spot_trade_count",
    "bn_spot_bbo_count",
    "bn_spot_complete",
)
BINANCE_USDM_FAMILY_COLUMNS: Final = (
    "bn_usdm_last_agg_price",
    "bn_usdm_mark_price",
    "bn_usdm_index_price",
    "bn_usdm_funding_rate",
    "bn_usdm_agg_count",
    "bn_usdm_mark_count",
    "bn_usdm_complete",
)
BINANCE_IDENTITY_WARNING: Final = (
    "Consumers must not blend Binance Spot and USD-M columns. Spot last/BBO "
    "are spot-market prices; USD-M aggTrade is the futures tape; USD-M mark "
    "is a calculated fair-value / liquidation series. They are not "
    "interchangeable. Issue #52 fixed USD-M bookTicker WebSocket routing "
    "only; it did not fix Quant instrument identity."
)

REQUIRED_HL_VIEWS: Final = (
    "raw_records",
    "trades",
    "bbo",
    "derivative_context",
    "data_quality_events",
)
REQUIRED_BN_VIEWS: Final = (
    "raw_records",
    "binance_spot_trades",
    "binance_spot_bbo",
    "binance_usdm_context",
    "data_quality_events",
)

_PRICE_COLUMN_NAMES: Final = (
    "hl_last_trade_price",
    "hl_last_mid_price",
    "hl_last_bid_price",
    "hl_last_ask_price",
    "hl_bbo_mid_proxy",
    "bn_spot_last_price",
    "bn_spot_bid_price",
    "bn_spot_ask_price",
    "bn_spot_bbo_mid_proxy",
    "bn_usdm_last_agg_price",
    "bn_usdm_mark_price",
    "bn_usdm_index_price",
    "bn_usdm_funding_rate",
)

_PANEL_COLUMNS: Final = (
    ("bucket_utc_ns", "BIGINT NOT NULL"),
    ("hl_last_trade_price", "VARCHAR"),
    ("hl_last_mid_price", "VARCHAR"),
    ("hl_last_bid_price", "VARCHAR"),
    ("hl_last_ask_price", "VARCHAR"),
    ("hl_bbo_mid_proxy", "VARCHAR"),
    ("hl_trade_count", "BIGINT NOT NULL"),
    ("hl_bbo_count", "BIGINT NOT NULL"),
    ("hl_mid_count", "BIGINT NOT NULL"),
    ("hl_gap_detected_count", "BIGINT NOT NULL"),
    ("hl_incomplete", "BOOLEAN NOT NULL"),
    ("hl_gap_detected", "BOOLEAN NOT NULL"),
    ("bn_spot_last_price", "VARCHAR"),
    ("bn_spot_bid_price", "VARCHAR"),
    ("bn_spot_ask_price", "VARCHAR"),
    ("bn_spot_bbo_mid_proxy", "VARCHAR"),
    ("bn_spot_trade_count", "BIGINT NOT NULL"),
    ("bn_spot_bbo_count", "BIGINT NOT NULL"),
    ("bn_usdm_last_agg_price", "VARCHAR"),
    ("bn_usdm_mark_price", "VARCHAR"),
    ("bn_usdm_index_price", "VARCHAR"),
    ("bn_usdm_funding_rate", "VARCHAR"),
    ("bn_usdm_agg_count", "BIGINT NOT NULL"),
    ("bn_usdm_mark_count", "BIGINT NOT NULL"),
    ("bn_gap_detected_count", "BIGINT NOT NULL"),
    ("bn_incomplete", "BOOLEAN NOT NULL"),
    ("bn_gap_detected", "BOOLEAN NOT NULL"),
    ("overlap_ok", "BOOLEAN NOT NULL"),
    ("bn_spot_complete", "BOOLEAN NOT NULL"),
    ("bn_usdm_complete", "BOOLEAN NOT NULL"),
)
_HL_INCOMPLETE_INDEX: Final = 10
_BN_INCOMPLETE_INDEX: Final = 25
_OVERLAP_OK_INDEX: Final = 27

_NO_CLAIM_NOTE: Final = (
    "PAPER research only. Descriptive HL/BN receipt-clock panel; not a "
    "strategy result and not a promotion signal."
)
_BLOCKED_NOTE: Final = (
    "Blocked on retained overlapping DATA-1A and DATA-1F series at "
    "<artifact-root>/data-1a/hyperliquid/BTC-PERP/<hl_run_id>/ and "
    "<artifact-root>/data-1f/binance/BTCUSDT/<bn_run_id>/ "
    "(ZSTD Parquet under raw/, schema version "
    f"{RAW_RESEARCH_SCHEMA_VERSION}, UTC ns receipt clocks, "
    "capture-claim.json retained)."
)
_WPQ2_NOTE: Final = "WP-Q1 only. H1 lead-lag / H2 basis experiments are out of scope."

_FORBIDDEN_SUMMARY_TOKENS: Final = ("edge",)

PanelInputMode = Literal["reconstructable", "ad_hoc"]


class PanelVerdictName(StrEnum):
    """Closed verdict set. No reserved promotion token is assigned here."""

    PANEL_READY = "panel_ready"
    NOT_ENOUGH_DATA = "not_enough_data"


@dataclass(frozen=True, slots=True)
class PanelSufficiencyThresholds:
    """Explicit numeric gate before a panel Parquet may be written."""

    max_gap_fraction: float
    min_overlap_buckets: int

    def __post_init__(self) -> None:
        if type(self.max_gap_fraction) not in (int, float) or not (
            0.0 <= float(self.max_gap_fraction) <= 1.0
        ):
            raise ValueError("max_gap_fraction must be between 0 and 1 inclusive.")
        if type(self.min_overlap_buckets) is not int or self.min_overlap_buckets < 1:
            raise ValueError("min_overlap_buckets must be an integer of at least 1.")

    def to_json_dict(self) -> dict[str, float | int]:
        return {
            "max_gap_fraction": float(self.max_gap_fraction),
            "min_overlap_buckets": self.min_overlap_buckets,
        }


PANEL_SUFFICIENCY_THRESHOLDS: Final = PanelSufficiencyThresholds(
    max_gap_fraction=PANEL_MAX_GAP_FRACTION,
    min_overlap_buckets=PANEL_MIN_OVERLAP_BUCKETS,
)


@dataclass(frozen=True, slots=True)
class SideSpanReport:
    """Receipt-clock span and counts for one venue over its market views."""

    venue: str
    product: str
    schema_versions: tuple[int, ...]
    span_start_utc_ns: int | None
    span_end_utc_ns: int | None
    parquet_file_count: int
    gap_detected_count: int

    def to_json_dict(self) -> dict[str, object]:
        return {
            "venue": self.venue,
            "product": self.product,
            "schema_versions": list(self.schema_versions),
            "span_start_utc_ns": self.span_start_utc_ns,
            "span_end_utc_ns": self.span_end_utc_ns,
            "parquet_file_count": self.parquet_file_count,
            "gap_detected_count": self.gap_detected_count,
        }


@dataclass(frozen=True, slots=True)
class PanelSufficiencyDecision:
    """Whether both retained series may be written as a join panel."""

    enough_data: bool
    reasons: tuple[str, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {"enough_data": self.enough_data, "reasons": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class PanelRunResult:
    """Written verdict for a DATA-1A / DATA-1F panel attempt."""

    trading_mode: Literal["PAPER"]
    verdict: PanelVerdictName
    sufficiency: PanelSufficiencyDecision
    thresholds: PanelSufficiencyThresholds
    bucket_ms: int
    bucket_ns: int
    overlap_start_utc_ns: int | None
    overlap_end_utc_ns: int | None
    overlap_bucket_count: int
    panel_row_count: int
    overlap_ok_row_count: int
    hl: SideSpanReport
    bn: SideSpanReport
    hl_incomplete_bucket_count: int
    bn_incomplete_bucket_count: int
    hl_gap_fraction: float
    bn_gap_fraction: float
    notes: tuple[str, ...]
    input_mode: PanelInputMode
    hl_path_contract: str | None
    bn_path_contract: str | None
    hl_run_id: str | None
    bn_run_id: str | None
    panel_parquet: str | None
    summary_json: str | None

    def __post_init__(self) -> None:
        if self.trading_mode != TRADING_MODE:
            raise ValueError("HL/BN panel research is PAPER only.")
        if self.verdict not in (
            PanelVerdictName.PANEL_READY,
            PanelVerdictName.NOT_ENOUGH_DATA,
        ):
            raise ValueError("Panel verdict must be panel_ready or not_enough_data.")
        rendered = json.dumps(self.to_json_dict(), sort_keys=True)
        _reject_forbidden_summary_tokens(rendered)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "trading_mode": self.trading_mode,
            "verdict": self.verdict.value,
            "panel_version": PANEL_VERSION,
            "binance_column_families": {
                "spot": list(BINANCE_SPOT_FAMILY_COLUMNS),
                "usdm": list(BINANCE_USDM_FAMILY_COLUMNS),
            },
            "binance_identity_warning": BINANCE_IDENTITY_WARNING,
            "sufficiency": self.sufficiency.to_json_dict(),
            "thresholds": self.thresholds.to_json_dict(),
            "bucket_ms": self.bucket_ms,
            "bucket_ns": self.bucket_ns,
            "overlap_start_utc_ns": self.overlap_start_utc_ns,
            "overlap_end_utc_ns": self.overlap_end_utc_ns,
            "overlap_bucket_count": self.overlap_bucket_count,
            "panel_row_count": self.panel_row_count,
            "overlap_ok_row_count": self.overlap_ok_row_count,
            "hl": self.hl.to_json_dict(),
            "bn": self.bn.to_json_dict(),
            "hl_incomplete_bucket_count": self.hl_incomplete_bucket_count,
            "bn_incomplete_bucket_count": self.bn_incomplete_bucket_count,
            "hl_gap_fraction": self.hl_gap_fraction,
            "bn_gap_fraction": self.bn_gap_fraction,
            "notes": list(self.notes),
            "input_mode": self.input_mode,
            "hl_path_contract": self.hl_path_contract,
            "bn_path_contract": self.bn_path_contract,
            "hl_run_id": self.hl_run_id,
            "bn_run_id": self.bn_run_id,
            "panel_parquet": self.panel_parquet,
            "summary_json": self.summary_json,
        }


def require_bucket_ms(bucket_ms: object) -> int:
    """Accept a closed millisecond bucket width in the documented bound."""

    if type(bucket_ms) is not int:
        raise TypeError("bucket_ms must be a built-in integer.")
    if not MIN_BUCKET_MS <= bucket_ms <= MAX_BUCKET_MS:
        raise ValueError(
            f"bucket_ms must be between {MIN_BUCKET_MS} and {MAX_BUCKET_MS} inclusive."
        )
    return bucket_ms


def resolve_panel_inputs(
    *,
    artifact_root: Path | None = None,
    hl_run_id: str | None = None,
    bn_run_id: str | None = None,
    hl_parquet_dir: Path | None = None,
    hl_database_path: Path | None = None,
    bn_parquet_dir: Path | None = None,
    bn_database_path: Path | None = None,
) -> tuple[
    Path,
    Path,
    Path,
    Path,
    Data1ARunPaths | None,
    Data1FRunPaths | None,
]:
    """Resolve reconstructable or ad-hoc pair paths. Do not create directories."""

    reconstructable = artifact_root is not None or hl_run_id is not None or bn_run_id is not None
    ad_hoc = any(
        path is not None
        for path in (hl_parquet_dir, hl_database_path, bn_parquet_dir, bn_database_path)
    )
    if reconstructable and ad_hoc:
        raise ValueError(
            "Use either --artifact-root/--hl-run-id/--bn-run-id or the four "
            "ad-hoc parquet/database flags, not both."
        )
    if reconstructable:
        if artifact_root is None or hl_run_id is None or bn_run_id is None:
            raise ValueError(
                "Reconstructable panel requires artifact_root, hl_run_id and bn_run_id."
            )
        hl_paths = data1a_run_paths(artifact_root, hl_run_id)
        bn_paths = data1f_run_paths(artifact_root, bn_run_id)
        return (
            hl_paths.raw_dir,
            hl_paths.database_path,
            bn_paths.raw_dir,
            bn_paths.database_path,
            hl_paths,
            bn_paths,
        )
    if (
        hl_parquet_dir is None
        or hl_database_path is None
        or bn_parquet_dir is None
        or bn_database_path is None
    ):
        raise ValueError(
            "Ad-hoc panel requires hl_parquet_dir, hl_database_path, "
            "bn_parquet_dir and bn_database_path; preferred reconstructable "
            "mode uses artifact_root, hl_run_id and bn_run_id."
        )
    for path in (hl_parquet_dir, hl_database_path, bn_parquet_dir, bn_database_path):
        if not isinstance(path, Path):
            raise TypeError("Ad-hoc parquet and database paths must be pathlib.Path values.")
    return hl_parquet_dir, hl_database_path, bn_parquet_dir, bn_database_path, None, None


def build_hl_binance_panel(
    *,
    artifact_root: Path | None = None,
    hl_run_id: str | None = None,
    bn_run_id: str | None = None,
    hl_parquet_dir: Path | None = None,
    hl_database_path: Path | None = None,
    bn_parquet_dir: Path | None = None,
    bn_database_path: Path | None = None,
    bucket_ms: int = DEFAULT_BUCKET_MS,
    output_dir: Path,
    thresholds: PanelSufficiencyThresholds | None = None,
) -> PanelRunResult:
    """Join retained DATA-1A and DATA-1F series on receipt-UTC buckets."""

    if not isinstance(output_dir, Path):
        raise TypeError("output_dir must be a pathlib.Path.")
    resolved_bucket_ms = require_bucket_ms(bucket_ms)
    bucket_ns = resolved_bucket_ms * NS_PER_MS
    resolved_thresholds = thresholds if thresholds is not None else PANEL_SUFFICIENCY_THRESHOLDS
    if type(resolved_thresholds) is not PanelSufficiencyThresholds:
        raise TypeError("thresholds must be a PanelSufficiencyThresholds instance.")

    hl_raw, hl_db, bn_raw, bn_db, hl_paths, bn_paths = resolve_panel_inputs(
        artifact_root=artifact_root,
        hl_run_id=hl_run_id,
        bn_run_id=bn_run_id,
        hl_parquet_dir=hl_parquet_dir,
        hl_database_path=hl_database_path,
        bn_parquet_dir=bn_parquet_dir,
        bn_database_path=bn_database_path,
    )
    input_mode: PanelInputMode = "reconstructable" if hl_paths is not None else "ad_hoc"
    claim_reasons = _reconstructable_claim_reasons(hl_paths, bn_paths)
    empty_hl = _empty_side(HL_VENUE, HL_PRODUCT)
    empty_bn = _empty_side(DATA1F_VENUE, DATA1F_PRODUCT)

    catalog_reasons: list[str] = []
    if not _build_catalog(hl_raw, hl_db, catalog_reasons, "hyperliquid"):
        return _insufficient_result(
            reasons=tuple(claim_reasons) + tuple(catalog_reasons),
            thresholds=resolved_thresholds,
            bucket_ms=resolved_bucket_ms,
            bucket_ns=bucket_ns,
            hl=empty_hl,
            bn=empty_bn,
            input_mode=input_mode,
            hl_paths=hl_paths,
            bn_paths=bn_paths,
            output_dir=output_dir,
        )
    if not _build_catalog(bn_raw, bn_db, catalog_reasons, "binance"):
        return _insufficient_result(
            reasons=tuple(claim_reasons) + tuple(catalog_reasons),
            thresholds=resolved_thresholds,
            bucket_ms=resolved_bucket_ms,
            bucket_ns=bucket_ns,
            hl=empty_hl,
            bn=empty_bn,
            input_mode=input_mode,
            hl_paths=hl_paths,
            bn_paths=bn_paths,
            output_dir=output_dir,
        )

    hl_parquet_count = len(tuple(hl_raw.resolve().glob("*.parquet")))
    bn_parquet_count = len(tuple(bn_raw.resolve().glob("*.parquet")))
    connection = duckdb.connect(":memory:")
    try:
        _attach_catalog(connection, "hl", hl_db)
        _attach_catalog(connection, "bn", bn_db)
        _require_attached_views(connection, "hl", REQUIRED_HL_VIEWS)
        _require_attached_views(connection, "bn", REQUIRED_BN_VIEWS)
        unexpected_missing = [
            name
            for name in RESEARCH_VIEW_NAMES
            if name not in _attached_views(connection, "hl") | _attached_views(connection, "bn")
        ]
        if unexpected_missing:
            raise ValueError(f"DuckDB catalog is missing catalog views: {unexpected_missing}")
        hl_report = _side_span_report(
            connection,
            alias="hl",
            venue=HL_VENUE,
            product=HL_PRODUCT,
            parquet_file_count=hl_parquet_count,
            span_sql="""
                SELECT min(received_utc_ns), max(received_utc_ns)
                FROM (
                    SELECT received_utc_ns FROM hl.trades
                    UNION ALL
                    SELECT received_utc_ns FROM hl.bbo
                    UNION ALL
                    SELECT received_utc_ns
                    FROM hl.derivative_context
                    WHERE mid_price IS NOT NULL AND mid_price <> ''
                )
            """,
            schema_sql="""
                SELECT DISTINCT schema_version
                FROM hl.raw_records
                WHERE venue = ? AND product = ?
                ORDER BY schema_version
            """,
            schema_params=(HL_VENUE, HL_PRODUCT),
            gap_sql="SELECT count(*) FROM hl.data_quality_events WHERE event = 'gap_detected'",
        )
        bn_report = _side_span_report(
            connection,
            alias="bn",
            venue=DATA1F_VENUE,
            product=DATA1F_PRODUCT,
            parquet_file_count=bn_parquet_count,
            span_sql="""
                SELECT min(received_utc_ns), max(received_utc_ns)
                FROM (
                    SELECT received_utc_ns FROM bn.binance_spot_trades
                    UNION ALL
                    SELECT received_utc_ns FROM bn.binance_spot_bbo
                    UNION ALL
                    SELECT received_utc_ns
                    FROM bn.binance_usdm_context
                    WHERE context_type IN ('aggregate_trade', 'mark_price')
                )
            """,
            schema_sql="""
                SELECT DISTINCT schema_version
                FROM bn.raw_records
                WHERE venue = ?
                ORDER BY schema_version
            """,
            schema_params=(DATA1F_VENUE,),
            gap_sql="SELECT count(*) FROM bn.data_quality_events WHERE event = 'gap_detected'",
        )
        overlap_start, overlap_end = _overlap_span(hl_report, bn_report)
        early_reasons = list(claim_reasons)
        early_reasons.extend(_schema_reasons(hl_report, bn_report))
        if hl_report.span_start_utc_ns is None or hl_report.span_end_utc_ns is None:
            early_reasons.append("no hyperliquid BTC-PERP market observations")
        if bn_report.span_start_utc_ns is None or bn_report.span_end_utc_ns is None:
            early_reasons.append("no binance BTCUSDT market observations")
        if overlap_start is None or overlap_end is None:
            early_reasons.append("no overlapping UTC receipt-clock span")
            return _insufficient_result(
                reasons=tuple(dict.fromkeys(early_reasons)),
                thresholds=resolved_thresholds,
                bucket_ms=resolved_bucket_ms,
                bucket_ns=bucket_ns,
                hl=hl_report,
                bn=bn_report,
                input_mode=input_mode,
                hl_paths=hl_paths,
                bn_paths=bn_paths,
                output_dir=output_dir,
            )

        rows = _build_panel_rows(
            connection,
            overlap_start=overlap_start,
            overlap_end=overlap_end,
            bucket_ns=bucket_ns,
        )
        joined_overlap_start = overlap_start
        joined_overlap_end = overlap_end
    finally:
        connection.close()

    overlap_bucket_count = len(rows)
    hl_incomplete = sum(1 for row in rows if bool(row[_HL_INCOMPLETE_INDEX]))
    bn_incomplete = sum(1 for row in rows if bool(row[_BN_INCOMPLETE_INDEX]))
    overlap_ok_count = sum(1 for row in rows if bool(row[_OVERLAP_OK_INDEX]))
    hl_gap_fraction = _gap_fraction(hl_incomplete, overlap_bucket_count)
    bn_gap_fraction = _gap_fraction(bn_incomplete, overlap_bucket_count)
    reasons = list(dict.fromkeys(early_reasons))
    if overlap_bucket_count < resolved_thresholds.min_overlap_buckets:
        reasons.append(
            f"overlap_bucket_count {overlap_bucket_count} < min_overlap_buckets "
            f"{resolved_thresholds.min_overlap_buckets}"
        )
    if hl_gap_fraction > float(resolved_thresholds.max_gap_fraction):
        reasons.append(
            f"hl_gap_fraction {hl_gap_fraction:.6g} > max_gap_fraction "
            f"{float(resolved_thresholds.max_gap_fraction):.6g} "
            f"({hl_incomplete}/{overlap_bucket_count} incomplete overlap buckets)"
        )
    if bn_gap_fraction > float(resolved_thresholds.max_gap_fraction):
        reasons.append(
            f"bn_gap_fraction {bn_gap_fraction:.6g} > max_gap_fraction "
            f"{float(resolved_thresholds.max_gap_fraction):.6g} "
            f"({bn_incomplete}/{overlap_bucket_count} incomplete overlap buckets)"
        )
    if reasons:
        return _finish_result(
            verdict=PanelVerdictName.NOT_ENOUGH_DATA,
            sufficiency=PanelSufficiencyDecision(enough_data=False, reasons=tuple(reasons)),
            thresholds=resolved_thresholds,
            bucket_ms=resolved_bucket_ms,
            bucket_ns=bucket_ns,
            overlap_start_utc_ns=joined_overlap_start,
            overlap_end_utc_ns=joined_overlap_end,
            overlap_bucket_count=overlap_bucket_count,
            panel_row_count=0,
            overlap_ok_row_count=overlap_ok_count,
            hl=hl_report,
            bn=bn_report,
            hl_incomplete_bucket_count=hl_incomplete,
            bn_incomplete_bucket_count=bn_incomplete,
            hl_gap_fraction=hl_gap_fraction,
            bn_gap_fraction=bn_gap_fraction,
            input_mode=input_mode,
            hl_paths=hl_paths,
            bn_paths=bn_paths,
            output_dir=output_dir,
            panel_rows=None,
        )

    return _finish_result(
        verdict=PanelVerdictName.PANEL_READY,
        sufficiency=PanelSufficiencyDecision(enough_data=True, reasons=()),
        thresholds=resolved_thresholds,
        bucket_ms=resolved_bucket_ms,
        bucket_ns=bucket_ns,
        overlap_start_utc_ns=joined_overlap_start,
        overlap_end_utc_ns=joined_overlap_end,
        overlap_bucket_count=overlap_bucket_count,
        panel_row_count=overlap_bucket_count,
        overlap_ok_row_count=overlap_ok_count,
        hl=hl_report,
        bn=bn_report,
        hl_incomplete_bucket_count=hl_incomplete,
        bn_incomplete_bucket_count=bn_incomplete,
        hl_gap_fraction=hl_gap_fraction,
        bn_gap_fraction=bn_gap_fraction,
        input_mode=input_mode,
        hl_paths=hl_paths,
        bn_paths=bn_paths,
        output_dir=output_dir,
        panel_rows=rows,
    )


def _build_panel_rows(
    connection: duckdb.DuckDBPyConnection,
    *,
    overlap_start: int,
    overlap_end: int,
    bucket_ns: int,
) -> tuple[tuple[object, ...], ...]:
    first_bucket = overlap_start // bucket_ns
    last_bucket = overlap_end // bucket_ns
    hl_trades = _bucket_map(
        connection,
        """
        WITH ranked AS (
            SELECT
                received_utc_ns // ? AS bucket_id,
                price,
                count(*) OVER (PARTITION BY received_utc_ns // ?) AS trade_count,
                row_number() OVER (
                    PARTITION BY received_utc_ns // ?
                    ORDER BY received_utc_ns DESC, message_ordinal DESC, event_index DESC
                ) AS rk
            FROM hl.trades
            WHERE received_utc_ns BETWEEN ? AND ?
        )
        SELECT bucket_id, price, trade_count FROM ranked WHERE rk = 1
        """,
        [bucket_ns, bucket_ns, bucket_ns, overlap_start, overlap_end],
    )
    hl_bbo = _bucket_map(
        connection,
        """
        WITH ranked AS (
            SELECT
                received_utc_ns // ? AS bucket_id,
                bid_price,
                ask_price,
                count(*) OVER (PARTITION BY received_utc_ns // ?) AS bbo_count,
                row_number() OVER (
                    PARTITION BY received_utc_ns // ?
                    ORDER BY received_utc_ns DESC, message_ordinal DESC
                ) AS rk
            FROM hl.bbo
            WHERE received_utc_ns BETWEEN ? AND ?
        )
        SELECT bucket_id, bid_price, ask_price, bbo_count FROM ranked WHERE rk = 1
        """,
        [bucket_ns, bucket_ns, bucket_ns, overlap_start, overlap_end],
    )
    hl_mid = _bucket_map(
        connection,
        """
        WITH ranked AS (
            SELECT
                received_utc_ns // ? AS bucket_id,
                mid_price,
                count(*) OVER (PARTITION BY received_utc_ns // ?) AS mid_count,
                row_number() OVER (
                    PARTITION BY received_utc_ns // ?
                    ORDER BY received_utc_ns DESC, message_ordinal DESC
                ) AS rk
            FROM hl.derivative_context
            WHERE received_utc_ns BETWEEN ? AND ?
              AND mid_price IS NOT NULL AND mid_price <> ''
        )
        SELECT bucket_id, mid_price, mid_count FROM ranked WHERE rk = 1
        """,
        [bucket_ns, bucket_ns, bucket_ns, overlap_start, overlap_end],
    )
    hl_gaps = _bucket_count_map(
        connection,
        """
        SELECT received_utc_ns // ? AS bucket_id, count(*)
        FROM hl.data_quality_events
        WHERE event = 'gap_detected' AND received_utc_ns BETWEEN ? AND ?
        GROUP BY 1
        """,
        [bucket_ns, overlap_start, overlap_end],
    )
    bn_trades = _bucket_map(
        connection,
        """
        WITH ranked AS (
            SELECT
                received_utc_ns // ? AS bucket_id,
                price,
                count(*) OVER (PARTITION BY received_utc_ns // ?) AS trade_count,
                row_number() OVER (
                    PARTITION BY received_utc_ns // ?
                    ORDER BY received_utc_ns DESC, raw_message_ordinal DESC
                ) AS rk
            FROM bn.binance_spot_trades
            WHERE received_utc_ns BETWEEN ? AND ?
        )
        SELECT bucket_id, price, trade_count FROM ranked WHERE rk = 1
        """,
        [bucket_ns, bucket_ns, bucket_ns, overlap_start, overlap_end],
    )
    bn_bbo = _bucket_map(
        connection,
        """
        WITH ranked AS (
            SELECT
                received_utc_ns // ? AS bucket_id,
                bid_price,
                ask_price,
                count(*) OVER (PARTITION BY received_utc_ns // ?) AS bbo_count,
                row_number() OVER (
                    PARTITION BY received_utc_ns // ?
                    ORDER BY received_utc_ns DESC, raw_message_ordinal DESC
                ) AS rk
            FROM bn.binance_spot_bbo
            WHERE received_utc_ns BETWEEN ? AND ?
        )
        SELECT bucket_id, bid_price, ask_price, bbo_count FROM ranked WHERE rk = 1
        """,
        [bucket_ns, bucket_ns, bucket_ns, overlap_start, overlap_end],
    )
    bn_agg = _bucket_map(
        connection,
        """
        WITH ranked AS (
            SELECT
                received_utc_ns // ? AS bucket_id,
                price,
                count(*) OVER (PARTITION BY received_utc_ns // ?) AS agg_count,
                row_number() OVER (
                    PARTITION BY received_utc_ns // ?
                    ORDER BY received_utc_ns DESC, raw_message_ordinal DESC
                ) AS rk
            FROM bn.binance_usdm_context
            WHERE received_utc_ns BETWEEN ? AND ?
              AND context_type = 'aggregate_trade'
        )
        SELECT bucket_id, price, agg_count FROM ranked WHERE rk = 1
        """,
        [bucket_ns, bucket_ns, bucket_ns, overlap_start, overlap_end],
    )
    bn_mark = _bucket_map(
        connection,
        """
        WITH ranked AS (
            SELECT
                received_utc_ns // ? AS bucket_id,
                mark_price,
                index_price,
                funding_rate,
                count(*) OVER (PARTITION BY received_utc_ns // ?) AS mark_count,
                row_number() OVER (
                    PARTITION BY received_utc_ns // ?
                    ORDER BY received_utc_ns DESC, raw_message_ordinal DESC
                ) AS rk
            FROM bn.binance_usdm_context
            WHERE received_utc_ns BETWEEN ? AND ?
              AND context_type = 'mark_price'
        )
        SELECT bucket_id, mark_price, index_price, funding_rate, mark_count
        FROM ranked WHERE rk = 1
        """,
        [bucket_ns, bucket_ns, bucket_ns, overlap_start, overlap_end],
    )
    bn_gaps = _bucket_count_map(
        connection,
        """
        SELECT received_utc_ns // ? AS bucket_id, count(*)
        FROM bn.data_quality_events
        WHERE event = 'gap_detected' AND received_utc_ns BETWEEN ? AND ?
        GROUP BY 1
        """,
        [bucket_ns, overlap_start, overlap_end],
    )

    rows: list[tuple[object, ...]] = []
    for bucket_id in range(first_bucket, last_bucket + 1):
        trade = hl_trades.get(bucket_id)
        bbo = hl_bbo.get(bucket_id)
        mid = hl_mid.get(bucket_id)
        spot_trade = bn_trades.get(bucket_id)
        spot_bbo = bn_bbo.get(bucket_id)
        usdm_agg = bn_agg.get(bucket_id)
        usdm_mark = bn_mark.get(bucket_id)
        hl_trade_price = _text_or_none(None if trade is None else trade[0])
        hl_bid = _text_or_none(None if bbo is None else bbo[0])
        hl_ask = _text_or_none(None if bbo is None else bbo[1])
        hl_mid_price = _text_or_none(None if mid is None else mid[0])
        hl_trade_count = 0 if trade is None else int(cast(int, trade[1]))
        hl_bbo_count = 0 if bbo is None else int(cast(int, bbo[2]))
        hl_mid_count = 0 if mid is None else int(cast(int, mid[1]))
        hl_gap_count = hl_gaps.get(bucket_id, 0)
        hl_incomplete = hl_trade_count == 0 and hl_bbo_count == 0 and hl_mid_count == 0
        bn_spot_price = _text_or_none(None if spot_trade is None else spot_trade[0])
        bn_bid = _text_or_none(None if spot_bbo is None else spot_bbo[0])
        bn_ask = _text_or_none(None if spot_bbo is None else spot_bbo[1])
        bn_spot_trade_count = 0 if spot_trade is None else int(cast(int, spot_trade[1]))
        bn_spot_bbo_count = 0 if spot_bbo is None else int(cast(int, spot_bbo[2]))
        bn_agg_price = _text_or_none(None if usdm_agg is None else usdm_agg[0])
        bn_mark_price = _text_or_none(None if usdm_mark is None else usdm_mark[0])
        bn_index_price = _text_or_none(None if usdm_mark is None else usdm_mark[1])
        bn_funding = _text_or_none(None if usdm_mark is None else usdm_mark[2])
        bn_agg_count = 0 if usdm_agg is None else int(cast(int, usdm_agg[1]))
        bn_mark_count = 0 if usdm_mark is None else int(cast(int, usdm_mark[3]))
        bn_gap_count = bn_gaps.get(bucket_id, 0)
        bn_spot_complete = bn_spot_trade_count > 0 or bn_spot_bbo_count > 0
        bn_usdm_complete = bn_agg_count > 0 or bn_mark_count > 0
        bn_incomplete = not bn_spot_complete and not bn_usdm_complete
        rows.append(
            (
                bucket_id * bucket_ns,
                hl_trade_price,
                hl_mid_price,
                hl_bid,
                hl_ask,
                _mid_proxy(hl_bid, hl_ask),
                hl_trade_count,
                hl_bbo_count,
                hl_mid_count,
                hl_gap_count,
                hl_incomplete,
                hl_gap_count > 0,
                bn_spot_price,
                bn_bid,
                bn_ask,
                _mid_proxy(bn_bid, bn_ask),
                bn_spot_trade_count,
                bn_spot_bbo_count,
                bn_agg_price,
                bn_mark_price,
                bn_index_price,
                bn_funding,
                bn_agg_count,
                bn_mark_count,
                bn_gap_count,
                bn_incomplete,
                bn_gap_count > 0,
                (not hl_incomplete) and (not bn_incomplete),
                bn_spot_complete,
                bn_usdm_complete,
            )
        )
    return tuple(rows)


def _write_panel_parquet(output_dir: Path, rows: Sequence[tuple[object, ...]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    panel_path = output_dir / PANEL_PARQUET_NAME
    column_sql = ", ".join(f"{name} {sql_type}" for name, sql_type in _PANEL_COLUMNS)
    placeholders = ", ".join("?" for _ in _PANEL_COLUMNS)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(f"CREATE TABLE panel ({column_sql})")
        connection.executemany(f"INSERT INTO panel VALUES ({placeholders})", list(rows))
        escaped = _sql_string(panel_path.resolve().as_posix())
        connection.execute(f"COPY panel TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        connection.close()
    return panel_path.resolve()


def _insufficient_result(
    *,
    reasons: tuple[str, ...],
    thresholds: PanelSufficiencyThresholds,
    bucket_ms: int,
    bucket_ns: int,
    hl: SideSpanReport,
    bn: SideSpanReport,
    input_mode: PanelInputMode,
    hl_paths: Data1ARunPaths | None,
    bn_paths: Data1FRunPaths | None,
    output_dir: Path,
    overlap_start_utc_ns: int | None = None,
    overlap_end_utc_ns: int | None = None,
    overlap_bucket_count: int = 0,
    overlap_ok_row_count: int = 0,
    hl_incomplete_bucket_count: int = 0,
    bn_incomplete_bucket_count: int = 0,
    hl_gap_fraction: float = 1.0,
    bn_gap_fraction: float = 1.0,
) -> PanelRunResult:
    return _finish_result(
        verdict=PanelVerdictName.NOT_ENOUGH_DATA,
        sufficiency=PanelSufficiencyDecision(enough_data=False, reasons=reasons),
        thresholds=thresholds,
        bucket_ms=bucket_ms,
        bucket_ns=bucket_ns,
        overlap_start_utc_ns=overlap_start_utc_ns,
        overlap_end_utc_ns=overlap_end_utc_ns,
        overlap_bucket_count=overlap_bucket_count,
        panel_row_count=0,
        overlap_ok_row_count=overlap_ok_row_count,
        hl=hl,
        bn=bn,
        hl_incomplete_bucket_count=hl_incomplete_bucket_count,
        bn_incomplete_bucket_count=bn_incomplete_bucket_count,
        hl_gap_fraction=hl_gap_fraction,
        bn_gap_fraction=bn_gap_fraction,
        input_mode=input_mode,
        hl_paths=hl_paths,
        bn_paths=bn_paths,
        output_dir=output_dir,
        panel_rows=None,
    )


def _finish_result(
    *,
    verdict: PanelVerdictName,
    sufficiency: PanelSufficiencyDecision,
    thresholds: PanelSufficiencyThresholds,
    bucket_ms: int,
    bucket_ns: int,
    overlap_start_utc_ns: int | None,
    overlap_end_utc_ns: int | None,
    overlap_bucket_count: int,
    panel_row_count: int,
    overlap_ok_row_count: int,
    hl: SideSpanReport,
    bn: SideSpanReport,
    hl_incomplete_bucket_count: int,
    bn_incomplete_bucket_count: int,
    hl_gap_fraction: float,
    bn_gap_fraction: float,
    input_mode: PanelInputMode,
    hl_paths: Data1ARunPaths | None,
    bn_paths: Data1FRunPaths | None,
    output_dir: Path,
    panel_rows: Sequence[tuple[object, ...]] | None,
) -> PanelRunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    panel_path: Path | None = None
    if verdict is PanelVerdictName.PANEL_READY:
        if panel_rows is None:
            raise RuntimeError("panel_ready requires assembled panel rows.")
        panel_path = _write_panel_parquet(output_dir, panel_rows)
    summary_path = (output_dir / PANEL_SUMMARY_NAME).resolve()
    result = _result(
        verdict=verdict,
        sufficiency=sufficiency,
        thresholds=thresholds,
        bucket_ms=bucket_ms,
        bucket_ns=bucket_ns,
        overlap_start_utc_ns=overlap_start_utc_ns,
        overlap_end_utc_ns=overlap_end_utc_ns,
        overlap_bucket_count=overlap_bucket_count,
        panel_row_count=panel_row_count,
        overlap_ok_row_count=overlap_ok_row_count,
        hl=hl,
        bn=bn,
        hl_incomplete_bucket_count=hl_incomplete_bucket_count,
        bn_incomplete_bucket_count=bn_incomplete_bucket_count,
        hl_gap_fraction=hl_gap_fraction,
        bn_gap_fraction=bn_gap_fraction,
        input_mode=input_mode,
        hl_paths=hl_paths,
        bn_paths=bn_paths,
        panel_parquet=None if panel_path is None else panel_path.as_posix(),
        summary_json=summary_path.as_posix(),
    )
    _write_json(summary_path, result.to_json_dict())
    return result


def _result(
    *,
    verdict: PanelVerdictName,
    sufficiency: PanelSufficiencyDecision,
    thresholds: PanelSufficiencyThresholds,
    bucket_ms: int,
    bucket_ns: int,
    overlap_start_utc_ns: int | None,
    overlap_end_utc_ns: int | None,
    overlap_bucket_count: int,
    panel_row_count: int,
    overlap_ok_row_count: int,
    hl: SideSpanReport,
    bn: SideSpanReport,
    hl_incomplete_bucket_count: int,
    bn_incomplete_bucket_count: int,
    hl_gap_fraction: float,
    bn_gap_fraction: float,
    input_mode: PanelInputMode,
    hl_paths: Data1ARunPaths | None,
    bn_paths: Data1FRunPaths | None,
    panel_parquet: str | None,
    summary_json: str | None,
) -> PanelRunResult:
    return PanelRunResult(
        trading_mode="PAPER",
        verdict=verdict,
        sufficiency=sufficiency,
        thresholds=thresholds,
        bucket_ms=bucket_ms,
        bucket_ns=bucket_ns,
        overlap_start_utc_ns=overlap_start_utc_ns,
        overlap_end_utc_ns=overlap_end_utc_ns,
        overlap_bucket_count=overlap_bucket_count,
        panel_row_count=panel_row_count,
        overlap_ok_row_count=overlap_ok_row_count,
        hl=hl,
        bn=bn,
        hl_incomplete_bucket_count=hl_incomplete_bucket_count,
        bn_incomplete_bucket_count=bn_incomplete_bucket_count,
        hl_gap_fraction=hl_gap_fraction,
        bn_gap_fraction=bn_gap_fraction,
        notes=(_NO_CLAIM_NOTE, _BLOCKED_NOTE, _WPQ2_NOTE),
        input_mode=input_mode,
        hl_path_contract=None if hl_paths is None else hl_paths.contract_id,
        bn_path_contract=None if bn_paths is None else bn_paths.contract_id,
        hl_run_id=None if hl_paths is None else hl_paths.run_id,
        bn_run_id=None if bn_paths is None else bn_paths.run_id,
        panel_parquet=panel_parquet,
        summary_json=summary_json,
    )


def _build_catalog(
    parquet_dir: Path,
    database_path: Path,
    reasons: list[str],
    label: str,
) -> bool:
    try:
        create_research_catalog(parquet_dir, database_path)
    except (OSError, ValueError) as error:
        reasons.append(f"{label} catalog could not be built: {error}")
        return False
    return True


def _attach_catalog(connection: duckdb.DuckDBPyConnection, alias: str, database_path: Path) -> None:
    escaped = _sql_string(database_path.resolve().as_posix())
    connection.execute(f"ATTACH '{escaped}' AS {alias} (READ_ONLY)")


def _attached_views(connection: duckdb.DuckDBPyConnection, alias: str) -> set[str]:
    return {
        str(name)
        for (name,) in connection.execute(
            """
            SELECT view_name
            FROM duckdb_views()
            WHERE database_name = ? AND schema_name = 'main'
            """,
            [alias],
        ).fetchall()
    }


def _require_attached_views(
    connection: duckdb.DuckDBPyConnection,
    alias: str,
    required: Sequence[str],
) -> None:
    views = _attached_views(connection, alias)
    missing = [name for name in required if name not in views]
    if missing:
        raise ValueError(f"DuckDB catalog {alias} is missing required views: {missing}")


def _side_span_report(
    connection: duckdb.DuckDBPyConnection,
    *,
    alias: str,
    venue: str,
    product: str,
    parquet_file_count: int,
    span_sql: str,
    schema_sql: str,
    schema_params: tuple[object, ...],
    gap_sql: str,
) -> SideSpanReport:
    del alias
    span_row = connection.execute(span_sql).fetchone()
    if span_row is None:
        raise RuntimeError("DuckDB did not return market-view span aggregates.")
    start_raw, end_raw = span_row
    schema_versions = tuple(
        int(version)
        for (version,) in connection.execute(schema_sql, list(schema_params)).fetchall()
    )
    return SideSpanReport(
        venue=venue,
        product=product,
        schema_versions=schema_versions,
        span_start_utc_ns=None if start_raw is None else int(start_raw),
        span_end_utc_ns=None if end_raw is None else int(end_raw),
        parquet_file_count=parquet_file_count,
        gap_detected_count=_required_count(connection, gap_sql),
    )


def _overlap_span(
    hl: SideSpanReport,
    bn: SideSpanReport,
) -> tuple[int | None, int | None]:
    if (
        hl.span_start_utc_ns is None
        or hl.span_end_utc_ns is None
        or bn.span_start_utc_ns is None
        or bn.span_end_utc_ns is None
    ):
        return None, None
    start = max(hl.span_start_utc_ns, bn.span_start_utc_ns)
    end = min(hl.span_end_utc_ns, bn.span_end_utc_ns)
    if start > end:
        return None, None
    return start, end


def _schema_reasons(hl: SideSpanReport, bn: SideSpanReport) -> list[str]:
    reasons: list[str] = []
    expected = (RAW_RESEARCH_SCHEMA_VERSION,)
    if hl.schema_versions != expected:
        reasons.append(
            "hyperliquid schema_versions "
            f"{list(hl.schema_versions)} are not exactly [{RAW_RESEARCH_SCHEMA_VERSION}]"
        )
    if bn.schema_versions != expected:
        reasons.append(
            "binance schema_versions "
            f"{list(bn.schema_versions)} are not exactly [{RAW_RESEARCH_SCHEMA_VERSION}]"
        )
    return reasons


def _reconstructable_claim_reasons(
    hl_paths: Data1ARunPaths | None,
    bn_paths: Data1FRunPaths | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if hl_paths is not None:
        reasons.extend(
            _claim_reasons(
                hl_paths.capture_claim_path,
                path_contract=DATA1A_PATH_CONTRACT_ID,
                venue=HL_VENUE,
                product=HL_PRODUCT,
                label="hyperliquid",
            )
        )
    if bn_paths is not None:
        reasons.extend(
            _claim_reasons(
                bn_paths.capture_claim_path,
                path_contract=DATA1F_PATH_CONTRACT_ID,
                venue=DATA1F_VENUE,
                product=DATA1F_PRODUCT,
                label="binance",
            )
        )
    return tuple(reasons)


def _claim_reasons(
    claim_path: Path,
    *,
    path_contract: str,
    venue: str,
    product: str,
    label: str,
) -> tuple[str, ...]:
    if not claim_path.is_file():
        return (f"{label} reconstructable run is missing capture-claim.json",)
    try:
        loaded = json.loads(claim_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return (f"{label} capture-claim.json is not valid UTF-8 JSON",)
    if type(loaded) is not dict:
        return (f"{label} capture-claim.json root is not an object",)
    reasons: list[str] = []
    if loaded.get("path_contract") != path_contract:
        reasons.append(f"{label} capture-claim.json path_contract is not {path_contract}")
    if loaded.get("retained") is not True:
        reasons.append(f"{label} capture-claim.json does not mark the run as retained")
    if loaded.get("venue") != venue or loaded.get("product") != product:
        reasons.append(f"{label} capture-claim.json is not {venue} {product}")
    return tuple(reasons)


def _bucket_map(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    params: Sequence[object],
) -> dict[int, tuple[object, ...]]:
    mapped: dict[int, tuple[object, ...]] = {}
    for row in connection.execute(sql, list(params)).fetchall():
        if not row or row[0] is None:
            continue
        mapped[int(row[0])] = tuple(row[1:])
    return mapped


def _bucket_count_map(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    params: Sequence[object],
) -> dict[int, int]:
    return {
        bucket_id: int(cast(int, values[0]))
        for bucket_id, values in _bucket_map(connection, sql, params).items()
        if values
    }


def _required_count(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise RuntimeError("DuckDB did not return the requested count.")
    return int(row[0])


def _gap_fraction(incomplete_count: int, overlap_bucket_count: int) -> float:
    if overlap_bucket_count <= 0:
        return 1.0
    return incomplete_count / overlap_bucket_count


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return None if text == "" else text


def _mid_proxy(bid: str | None, ask: str | None) -> str | None:
    if bid is None or ask is None:
        return None
    try:
        mid = (Decimal(bid) + Decimal(ask)) / Decimal(2)
    except (InvalidOperation, ValueError):
        return None
    if mid <= 0:
        return None
    return format(mid, "f")


def _empty_side(venue: str, product: str) -> SideSpanReport:
    return SideSpanReport(
        venue=venue,
        product=product,
        schema_versions=(),
        span_start_utc_ns=None,
        span_end_utc_ns=None,
        parquet_file_count=0,
        gap_detected_count=0,
    )


def _write_json(path: Path, payload: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    _reject_forbidden_summary_tokens(rendered)
    path.write_text(rendered + "\n", encoding="utf-8")
    return path.resolve()


def _reject_forbidden_summary_tokens(rendered: str) -> None:
    lowered = rendered.lower()
    for token in _FORBIDDEN_SUMMARY_TOKENS:
        if token in lowered:
            raise ValueError("Panel summary must not contain the token 'edge'.")


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "PAPER-only gap-aware Hyperliquid DATA-1A + Binance DATA-1F "
            "receipt-clock panel. Writes panel.parquet only when overlap, "
            "claims and gap fractions are sufficient. Never a strategy result."
        )
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        help="Preferred reconstructable artifact root used with both run ids.",
    )
    parser.add_argument(
        "--hl-run-id",
        help="DATA-1A run id under <artifact-root>/data-1a/hyperliquid/BTC-PERP/.",
    )
    parser.add_argument(
        "--bn-run-id",
        help="DATA-1F run id under <artifact-root>/data-1f/binance/BTCUSDT/.",
    )
    parser.add_argument(
        "--hl-parquet-dir",
        type=Path,
        help="Ad-hoc directory of completed DATA-1A ZSTD Parquet parts.",
    )
    parser.add_argument(
        "--hl-database",
        type=Path,
        help="Ad-hoc Hyperliquid DuckDB catalog path. Views are rebuilt from --hl-parquet-dir.",
    )
    parser.add_argument(
        "--bn-parquet-dir",
        type=Path,
        help="Ad-hoc directory of completed DATA-1F ZSTD Parquet parts.",
    )
    parser.add_argument(
        "--bn-database",
        type=Path,
        help="Ad-hoc Binance DuckDB catalog path. Views are rebuilt from --bn-parquet-dir.",
    )
    parser.add_argument(
        "--bucket-ms",
        type=int,
        default=DEFAULT_BUCKET_MS,
        help=f"Receipt-UTC bucket width in milliseconds. Default: {DEFAULT_BUCKET_MS} (1s).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for panel.parquet (on success) and panel-summary.json.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    result = build_hl_binance_panel(
        artifact_root=cast(Path | None, args.artifact_root),
        hl_run_id=cast(str | None, args.hl_run_id),
        bn_run_id=cast(str | None, args.bn_run_id),
        hl_parquet_dir=cast(Path | None, args.hl_parquet_dir),
        hl_database_path=cast(Path | None, args.hl_database),
        bn_parquet_dir=cast(Path | None, args.bn_parquet_dir),
        bn_database_path=cast(Path | None, args.bn_database),
        bucket_ms=cast(int, args.bucket_ms),
        output_dir=cast(Path, args.output_dir),
    )
    print(json.dumps(result.to_json_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
