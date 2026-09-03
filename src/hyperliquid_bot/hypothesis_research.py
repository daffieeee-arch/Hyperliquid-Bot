"""PAPER-only retained Hyperliquid BTC-PERP series sanity and baseline slot.

This module is a research entrypoint, not a capture tool and not an execution path.
It never signs orders, never selects LIVE/TESTNET, and never claims a trading edge.

Input contract
--------------
Point the runner at a **retained DATA-1A capture directory** written by
``ParquetResearchWriter`` / ``python -m hyperliquid_bot.hyperliquid_raw_research``
when Trading keeps a multi-day Hyperliquid BTC-PERP run **outside git**.

Expected layout (UTC nanosecond receipt clocks on every row):

- ``<parquet-dir>/part-*.parquet`` — completed ZSTD parts; hidden ``.*.partial``
  files are ignored, matching DATA-1A readers.
- Schema version ``RAW_RESEARCH_SCHEMA_VERSION`` (currently ``1``) on every row.
- ``venue='hyperliquid'``, ``product='BTC-PERP'``.
- Market channels: ``trades``, ``bbo``, ``l2Book``, ``activeAssetCtx``.
- Local markers: ``session``, ``subscription`` / ``subscriptionResponse``,
  ``data_quality`` (including ``gap_detected``).

The entrypoint rebuilds the existing DATA-1A DuckDB catalog via
``create_research_catalog`` and reads the Hyperliquid views already defined in
``parquet_research``:

- ``raw_records``, ``trades``, ``bbo``, ``l2``, ``derivative_context``
- ``sessions``, ``subscription_events``, ``data_quality_events``

``trades.price``, ``bbo`` bid/ask and ``derivative_context.mid_price`` remain
text. Receipt time is ``received_utc_ns`` (UTC ns). Venue event times are not
used as the series clock.

Committed fixtures under ``tests/fixtures/hyperliquid/`` and D01's 27-event
PAPER routing proof are **not** a research series. A 1-600s DATA-1A smoke is
also not hypothesis-usable.

Fail-closed candidate thresholds
--------------------------------
A *candidate* baseline slot runs only when every constant in
``CANDIDATE_SUFFICIENCY_THRESHOLDS`` is met. Otherwise the verdict is
``not_enough_data`` and **no strategy fit is attempted**.

The CLI cannot lower these thresholds. Tests may construct a clearly labelled
synthetic ``SufficiencyThresholds`` instance only to exercise the happy-path
scaffold; that path is not market evidence.

Baseline slots
--------------
- ``momentum`` — fixed 1-hour mid/trade lookback placeholder, wired only after
  sufficiency passes. The scaffold always returns ``noise``. It never emits
  ``edge`` and never prints profitability.
- ``basis`` — reserved for a later Hyperliquid mark vs Binance DATA-1F
  comparison. Fails closed without a Binance Parquet directory; even when one
  is present, no basis fit is implemented.

Verdicts are ``not_enough_data``, ``noise``, or reserved ``edge``. This module
never assigns ``edge``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, cast

import duckdb

from .parquet_research import RESEARCH_VIEW_NAMES, create_research_catalog
from .raw_research import RAW_RESEARCH_SCHEMA_VERSION

RESEARCH_VENUE: Final = "hyperliquid"
RESEARCH_PRODUCT: Final = "BTC-PERP"
TRADING_MODE: Final = "PAPER"
NS_PER_HOUR: Final = 3_600_000_000_000
MOMENTUM_LOOKBACK_HOURS: Final = 1
MOMENTUM_LOOKBACK_NS: Final = MOMENTUM_LOOKBACK_HOURS * NS_PER_HOUR

# Multi-day candidate gate. Smoke fixtures and 1-600s captures cannot pass.
CANDIDATE_MIN_SPAN_HOURS: Final = 72.0
CANDIDATE_MIN_TRADE_COUNT: Final = 10_000
CANDIDATE_MIN_BBO_COUNT: Final = 5_000
CANDIDATE_MIN_MID_COUNT: Final = 500
CANDIDATE_MAX_GAP_FRACTION: Final = 0.05

REQUIRED_CATALOG_VIEWS: Final = (
    "raw_records",
    "trades",
    "bbo",
    "derivative_context",
    "sessions",
    "data_quality_events",
)

_NO_EDGE_NOTE: Final = (
    "PAPER research only. This entrypoint never claims a trading edge and "
    "does not promote strategies."
)
_FIXTURE_NOTE: Final = (
    "Committed Hyperliquid fixtures, D01 routing events and DATA-1A smokes "
    "are not hypothesis-usable."
)
_BLOCKED_NOTE: Final = (
    "Blocked on a retained multi-day Hyperliquid BTC-PERP DATA-1A series "
    "from Trading (ZSTD Parquet parts, schema version "
    f"{RAW_RESEARCH_SCHEMA_VERSION}, UTC ns receipt clocks)."
)


class HypothesisVerdictName(StrEnum):
    """Closed verdict set. ``edge`` is reserved and never assigned here."""

    EDGE = "edge"
    NOISE = "noise"
    NOT_ENOUGH_DATA = "not_enough_data"


class BaselineSlot(StrEnum):
    """Boring baseline placeholders. Neither is a validated strategy."""

    MOMENTUM = "momentum"
    BASIS = "basis"


@dataclass(frozen=True, slots=True)
class SufficiencyThresholds:
    """Explicit numeric gate before any baseline slot may run."""

    min_span_hours: float
    min_trade_count: int
    min_bbo_count: int
    min_mid_count: int
    max_gap_fraction: float

    def __post_init__(self) -> None:
        if type(self.min_span_hours) not in (int, float) or not self.min_span_hours > 0:
            raise ValueError("min_span_hours must be a positive number.")
        for field_name in ("min_trade_count", "min_bbo_count", "min_mid_count"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer.")
        if type(self.max_gap_fraction) not in (int, float) or not (
            0.0 <= float(self.max_gap_fraction) <= 1.0
        ):
            raise ValueError("max_gap_fraction must be between 0 and 1 inclusive.")

    def to_json_dict(self) -> dict[str, float | int]:
        return {
            "min_span_hours": float(self.min_span_hours),
            "min_trade_count": self.min_trade_count,
            "min_bbo_count": self.min_bbo_count,
            "min_mid_count": self.min_mid_count,
            "max_gap_fraction": float(self.max_gap_fraction),
        }


CANDIDATE_SUFFICIENCY_THRESHOLDS: Final = SufficiencyThresholds(
    min_span_hours=CANDIDATE_MIN_SPAN_HOURS,
    min_trade_count=CANDIDATE_MIN_TRADE_COUNT,
    min_bbo_count=CANDIDATE_MIN_BBO_COUNT,
    min_mid_count=CANDIDATE_MIN_MID_COUNT,
    max_gap_fraction=CANDIDATE_MAX_GAP_FRACTION,
)


@dataclass(frozen=True, slots=True)
class SeriesSanityReport:
    """Coverage, gap and schema facts. Not a performance report."""

    trading_mode: Literal["PAPER"]
    venue: str
    product: str
    schema_versions: tuple[int, ...]
    raw_record_count: int
    inbound_channel_counts: tuple[tuple[str, int], ...]
    span_start_utc_ns: int | None
    span_end_utc_ns: int | None
    span_duration_hours: float
    trade_count: int
    bbo_count: int
    mid_count: int
    gap_detected_count: int
    session_markers: tuple[tuple[str, int], ...]
    span_hour_count: int
    incomplete_hour_count: int
    gap_fraction: float
    parquet_file_count: int

    def to_json_dict(self) -> dict[str, object]:
        return {
            "trading_mode": self.trading_mode,
            "venue": self.venue,
            "product": self.product,
            "schema_versions": list(self.schema_versions),
            "raw_record_count": self.raw_record_count,
            "inbound_channel_counts": dict(self.inbound_channel_counts),
            "span_start_utc_ns": self.span_start_utc_ns,
            "span_end_utc_ns": self.span_end_utc_ns,
            "span_duration_hours": self.span_duration_hours,
            "trade_count": self.trade_count,
            "bbo_count": self.bbo_count,
            "mid_count": self.mid_count,
            "gap_detected_count": self.gap_detected_count,
            "session_markers": dict(self.session_markers),
            "span_hour_count": self.span_hour_count,
            "incomplete_hour_count": self.incomplete_hour_count,
            "gap_fraction": self.gap_fraction,
            "parquet_file_count": self.parquet_file_count,
        }


@dataclass(frozen=True, slots=True)
class SufficiencyDecision:
    """Whether the retained series may enter a baseline slot."""

    enough_data: bool
    reasons: tuple[str, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {"enough_data": self.enough_data, "reasons": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class BaselineSlotResult:
    """Outcome of one boring baseline placeholder."""

    slot: BaselineSlot
    ran: bool
    verdict: HypothesisVerdictName
    reason: str
    diagnostics: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        if self.verdict is HypothesisVerdictName.EDGE:
            raise ValueError("Baseline slots in this module must not assign verdict 'edge'.")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "slot": self.slot.value,
            "ran": self.ran,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class HypothesisRunResult:
    """Written verdict for a retained-series evaluation."""

    trading_mode: Literal["PAPER"]
    verdict: HypothesisVerdictName
    sufficiency: SufficiencyDecision
    sanity: SeriesSanityReport
    thresholds: SufficiencyThresholds
    baseline: BaselineSlotResult
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.trading_mode != TRADING_MODE:
            raise ValueError("Hypothesis research is PAPER only.")
        if self.verdict is HypothesisVerdictName.EDGE:
            raise ValueError("This entrypoint must not assign verdict 'edge'.")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "trading_mode": self.trading_mode,
            "verdict": self.verdict.value,
            "sufficiency": self.sufficiency.to_json_dict(),
            "sanity": self.sanity.to_json_dict(),
            "thresholds": self.thresholds.to_json_dict(),
            "baseline": self.baseline.to_json_dict(),
            "notes": list(self.notes),
        }


def evaluate_retained_series(
    *,
    parquet_dir: Path,
    database_path: Path,
    baseline: BaselineSlot = BaselineSlot.MOMENTUM,
    thresholds: SufficiencyThresholds | None = None,
    binance_parquet_dir: Path | None = None,
) -> HypothesisRunResult:
    """Load a retained DATA-1A directory and return a fail-closed verdict."""

    if not isinstance(parquet_dir, Path) or not isinstance(database_path, Path):
        raise TypeError("parquet_dir and database_path must be pathlib.Path values.")
    if type(baseline) is not BaselineSlot:
        raise TypeError("baseline must be a BaselineSlot.")
    if binance_parquet_dir is not None and not isinstance(binance_parquet_dir, Path):
        raise TypeError("binance_parquet_dir must be a pathlib.Path or None.")
    resolved_thresholds = thresholds if thresholds is not None else CANDIDATE_SUFFICIENCY_THRESHOLDS
    if type(resolved_thresholds) is not SufficiencyThresholds:
        raise TypeError("thresholds must be a SufficiencyThresholds instance.")

    create_research_catalog(parquet_dir, database_path)
    parquet_file_count = len(tuple(parquet_dir.resolve().glob("*.parquet")))
    sanity = compute_sanity_report(database_path, parquet_file_count=parquet_file_count)
    sufficiency = decide_sufficiency(
        sanity,
        resolved_thresholds,
        baseline=baseline,
        binance_parquet_dir=binance_parquet_dir,
    )
    if not sufficiency.enough_data:
        baseline_result = BaselineSlotResult(
            slot=baseline,
            ran=False,
            verdict=HypothesisVerdictName.NOT_ENOUGH_DATA,
            reason="baseline slot skipped: " + "; ".join(sufficiency.reasons),
            diagnostics=(),
        )
        return HypothesisRunResult(
            trading_mode="PAPER",
            verdict=HypothesisVerdictName.NOT_ENOUGH_DATA,
            sufficiency=sufficiency,
            sanity=sanity,
            thresholds=resolved_thresholds,
            baseline=baseline_result,
            notes=(_NO_EDGE_NOTE, _FIXTURE_NOTE, _BLOCKED_NOTE),
        )

    if baseline is BaselineSlot.MOMENTUM:
        baseline_result = _evaluate_momentum_slot(database_path)
    else:
        baseline_result = _evaluate_basis_slot(binance_parquet_dir)

    return HypothesisRunResult(
        trading_mode="PAPER",
        verdict=baseline_result.verdict,
        sufficiency=sufficiency,
        sanity=sanity,
        thresholds=resolved_thresholds,
        baseline=baseline_result,
        notes=(_NO_EDGE_NOTE, _FIXTURE_NOTE, _BLOCKED_NOTE),
    )


def compute_sanity_report(
    database_path: Path,
    *,
    parquet_file_count: int,
) -> SeriesSanityReport:
    """Query DATA-1A views for span, counts, gaps and session markers."""

    if not isinstance(database_path, Path):
        raise TypeError("database_path must be a pathlib.Path.")
    if type(parquet_file_count) is not int or parquet_file_count < 0:
        raise ValueError("parquet_file_count must be a non-negative integer.")

    connection = duckdb.connect(str(database_path.resolve()), read_only=True)
    try:
        _require_catalog_views(connection)
        schema_versions = tuple(
            int(version)
            for (version,) in connection.execute(
                """
                SELECT DISTINCT schema_version
                FROM raw_records
                WHERE venue = ? AND product = ?
                ORDER BY schema_version
                """,
                [RESEARCH_VENUE, RESEARCH_PRODUCT],
            ).fetchall()
        )
        span_row = connection.execute(
            """
            SELECT min(received_utc_ns), max(received_utc_ns), count(*)
            FROM raw_records
            WHERE venue = ? AND product = ?
            """,
            [RESEARCH_VENUE, RESEARCH_PRODUCT],
        ).fetchone()
        if span_row is None:
            raise RuntimeError("DuckDB did not return Hyperliquid BTC-PERP span aggregates.")
        start_raw, end_raw, raw_count_raw = span_row
        start_ns = int(start_raw) if start_raw is not None else None
        end_ns = int(end_raw) if end_raw is not None else None
        raw_record_count = int(raw_count_raw)
        inbound_channel_counts = tuple(
            (str(channel), int(count))
            for channel, count in connection.execute(
                """
                SELECT channel, count(*)
                FROM raw_records
                WHERE venue = ? AND product = ? AND direction = 'inbound'
                GROUP BY channel
                ORDER BY channel
                """,
                [RESEARCH_VENUE, RESEARCH_PRODUCT],
            ).fetchall()
        )
        trade_count = _required_count(connection, "SELECT count(*) FROM trades")
        bbo_count = _required_count(connection, "SELECT count(*) FROM bbo")
        mid_count = _required_count(
            connection,
            """
            SELECT count(*)
            FROM derivative_context
            WHERE mid_price IS NOT NULL AND mid_price <> ''
            """,
        )
        gap_detected_count = _required_count(
            connection,
            "SELECT count(*) FROM data_quality_events WHERE event = 'gap_detected'",
        )
        session_markers = tuple(
            (str(event), int(count))
            for event, count in connection.execute(
                "SELECT event, count(*) FROM sessions GROUP BY event ORDER BY event"
            ).fetchall()
        )
        trade_hours = {
            int(hour)
            for (hour,) in connection.execute(
                "SELECT DISTINCT received_utc_ns // ? FROM trades",
                [NS_PER_HOUR],
            ).fetchall()
            if hour is not None
        }
        bbo_hours = {
            int(hour)
            for (hour,) in connection.execute(
                "SELECT DISTINCT received_utc_ns // ? FROM bbo",
                [NS_PER_HOUR],
            ).fetchall()
            if hour is not None
        }
    finally:
        connection.close()

    span_duration_hours = (
        (end_ns - start_ns) / NS_PER_HOUR if start_ns is not None and end_ns is not None else 0.0
    )
    span_hour_count, incomplete_hour_count, gap_fraction = _hour_coverage(
        start_ns,
        end_ns,
        trade_hours,
        bbo_hours,
    )
    return SeriesSanityReport(
        trading_mode="PAPER",
        venue=RESEARCH_VENUE,
        product=RESEARCH_PRODUCT,
        schema_versions=schema_versions,
        raw_record_count=raw_record_count,
        inbound_channel_counts=inbound_channel_counts,
        span_start_utc_ns=start_ns,
        span_end_utc_ns=end_ns,
        span_duration_hours=span_duration_hours,
        trade_count=trade_count,
        bbo_count=bbo_count,
        mid_count=mid_count,
        gap_detected_count=gap_detected_count,
        session_markers=session_markers,
        span_hour_count=span_hour_count,
        incomplete_hour_count=incomplete_hour_count,
        gap_fraction=gap_fraction,
        parquet_file_count=parquet_file_count,
    )


def decide_sufficiency(
    sanity: SeriesSanityReport,
    thresholds: SufficiencyThresholds,
    *,
    baseline: BaselineSlot,
    binance_parquet_dir: Path | None,
) -> SufficiencyDecision:
    """Return fail-closed reasons. An empty reason tuple is the only pass."""

    if type(sanity) is not SeriesSanityReport:
        raise TypeError("sanity must be a SeriesSanityReport.")
    if type(thresholds) is not SufficiencyThresholds:
        raise TypeError("thresholds must be a SufficiencyThresholds instance.")
    if type(baseline) is not BaselineSlot:
        raise TypeError("baseline must be a BaselineSlot.")

    reasons: list[str] = []
    if sanity.schema_versions != (RAW_RESEARCH_SCHEMA_VERSION,):
        reasons.append(
            "schema_versions "
            f"{list(sanity.schema_versions)} are not exactly "
            f"[{RAW_RESEARCH_SCHEMA_VERSION}]"
        )
    if sanity.raw_record_count <= 0:
        reasons.append("no hyperliquid BTC-PERP raw records")
    if sanity.span_duration_hours < float(thresholds.min_span_hours):
        reasons.append(
            "span_duration_hours "
            f"{sanity.span_duration_hours:.6g} < min_span_hours "
            f"{float(thresholds.min_span_hours):.6g}"
        )
    if sanity.trade_count < thresholds.min_trade_count:
        reasons.append(
            f"trade_count {sanity.trade_count} < min_trade_count {thresholds.min_trade_count}"
        )
    if sanity.bbo_count < thresholds.min_bbo_count:
        reasons.append(f"bbo_count {sanity.bbo_count} < min_bbo_count {thresholds.min_bbo_count}")
    if sanity.mid_count < thresholds.min_mid_count:
        reasons.append(f"mid_count {sanity.mid_count} < min_mid_count {thresholds.min_mid_count}")
    if sanity.gap_fraction > float(thresholds.max_gap_fraction):
        reasons.append(
            f"gap_fraction {sanity.gap_fraction:.6g} > max_gap_fraction "
            f"{float(thresholds.max_gap_fraction):.6g} "
            f"({sanity.incomplete_hour_count}/{sanity.span_hour_count} incomplete UTC hours)"
        )
    if baseline is BaselineSlot.BASIS and not _binance_series_present(binance_parquet_dir):
        reasons.append(
            "basis slot requires a retained Binance DATA-1F Parquet directory; none supplied"
        )
    return SufficiencyDecision(enough_data=not reasons, reasons=tuple(reasons))


def _evaluate_momentum_slot(database_path: Path) -> BaselineSlotResult:
    """Fixed-lookback mid/trade return placeholder. Always verdict ``noise``."""

    connection = duckdb.connect(str(database_path.resolve()), read_only=True)
    try:
        mid_points = _decimal_points(
            connection.execute(
                """
                SELECT received_utc_ns, mid_price
                FROM derivative_context
                WHERE mid_price IS NOT NULL AND mid_price <> ''
                ORDER BY received_utc_ns, message_ordinal
                """
            ).fetchall()
        )
        trade_points = _decimal_points(
            connection.execute(
                """
                SELECT received_utc_ns, price
                FROM trades
                WHERE price IS NOT NULL AND price <> ''
                ORDER BY received_utc_ns, message_ordinal, event_index
                """
            ).fetchall()
        )
    finally:
        connection.close()

    mid_returns = _lookback_returns(mid_points, MOMENTUM_LOOKBACK_NS)
    trade_returns = _lookback_returns(trade_points, MOMENTUM_LOOKBACK_NS)
    diagnostics = (
        ("lookback_hours", MOMENTUM_LOOKBACK_HOURS),
        ("mid_observations", len(mid_points)),
        ("trade_observations", len(trade_points)),
        ("mid_return_pairs", len(mid_returns)),
        ("trade_return_pairs", len(trade_returns)),
        ("mid_mean_abs_return", _mean_abs_text(mid_returns)),
        ("trade_mean_abs_return", _mean_abs_text(trade_returns)),
    )
    return BaselineSlotResult(
        slot=BaselineSlot.MOMENTUM,
        ran=True,
        verdict=HypothesisVerdictName.NOISE,
        reason=(
            "momentum slot is a fixed-lookback scaffold; it does not fit "
            "parameters and does not claim edge or noise-adjusted expectancy"
        ),
        diagnostics=diagnostics,
    )


def _evaluate_basis_slot(binance_parquet_dir: Path | None) -> BaselineSlotResult:
    """Reserved HL mark vs Binance stub. No comparison is implemented."""

    parquet_files = 0
    if binance_parquet_dir is not None:
        parquet_files = len(tuple(binance_parquet_dir.resolve().glob("*.parquet")))
    return BaselineSlotResult(
        slot=BaselineSlot.BASIS,
        ran=False,
        verdict=HypothesisVerdictName.NOISE,
        reason=(
            "basis slot is reserved for a later Hyperliquid mark vs Binance "
            "comparison; no basis fit is implemented"
        ),
        diagnostics=(("binance_parquet_files", parquet_files),),
    )


def _decimal_points(rows: Sequence[tuple[object, object]]) -> tuple[tuple[int, Decimal], ...]:
    points: list[tuple[int, Decimal]] = []
    for received_utc_ns, raw_price in rows:
        if type(received_utc_ns) is not int or raw_price is None:
            continue
        try:
            price = Decimal(str(raw_price))
        except (InvalidOperation, ValueError):
            continue
        if price <= 0:
            continue
        points.append((received_utc_ns, price))
    return tuple(points)


def _lookback_returns(
    points: tuple[tuple[int, Decimal], ...],
    lookback_ns: int,
) -> tuple[Decimal, ...]:
    returns: list[Decimal] = []
    forward = 0
    for index, (stamp, price) in enumerate(points):
        target = stamp + lookback_ns
        if forward <= index:
            forward = index + 1
        while forward < len(points) and points[forward][0] < target:
            forward += 1
        if forward >= len(points):
            break
        later_price = points[forward][1]
        returns.append((later_price - price) / price)
    return tuple(returns)


def _mean_abs_text(values: tuple[Decimal, ...]) -> str | None:
    if not values:
        return None
    total = sum((abs(value) for value in values), Decimal(0))
    return str(total / Decimal(len(values)))


def _hour_coverage(
    start_ns: int | None,
    end_ns: int | None,
    trade_hours: set[int],
    bbo_hours: set[int],
) -> tuple[int, int, float]:
    if start_ns is None or end_ns is None:
        return 0, 0, 1.0
    first_hour = start_ns // NS_PER_HOUR
    last_hour = end_ns // NS_PER_HOUR
    span_hour_count = last_hour - first_hour + 1
    incomplete_hour_count = sum(
        1
        for hour in range(first_hour, last_hour + 1)
        if hour not in trade_hours or hour not in bbo_hours
    )
    gap_fraction = incomplete_hour_count / span_hour_count if span_hour_count else 1.0
    return span_hour_count, incomplete_hour_count, gap_fraction


def _binance_series_present(binance_parquet_dir: Path | None) -> bool:
    if binance_parquet_dir is None:
        return False
    resolved = binance_parquet_dir.resolve()
    return resolved.is_dir() and any(resolved.glob("*.parquet"))


def _require_catalog_views(connection: duckdb.DuckDBPyConnection) -> None:
    views = {
        str(name)
        for (name,) in connection.execute(
            "SELECT view_name FROM duckdb_views() WHERE database_name <> 'system'"
        ).fetchall()
    }
    missing = [name for name in REQUIRED_CATALOG_VIEWS if name not in views]
    if missing:
        raise ValueError(f"DuckDB catalog is missing required DATA-1A views: {missing}")
    unexpected_missing = [name for name in RESEARCH_VIEW_NAMES if name not in views]
    if unexpected_missing:
        raise ValueError(f"DuckDB catalog is missing catalog views: {unexpected_missing}")


def _required_count(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise RuntimeError("DuckDB did not return the requested count.")
    return int(row[0])


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "PAPER-only Hyperliquid BTC-PERP retained-series sanity and "
            "fail-closed baseline slot. Never claims edge."
        )
    )
    parser.add_argument(
        "--parquet-dir",
        required=True,
        type=Path,
        help="Directory of completed DATA-1A ZSTD Parquet parts (part-*.parquet).",
    )
    parser.add_argument(
        "--database",
        required=True,
        type=Path,
        help="DuckDB catalog path. Views are rebuilt from --parquet-dir.",
    )
    parser.add_argument(
        "--baseline",
        default=BaselineSlot.MOMENTUM.value,
        choices=(BaselineSlot.MOMENTUM.value, BaselineSlot.BASIS.value),
        help="Boring baseline slot. Default: momentum.",
    )
    parser.add_argument(
        "--binance-parquet-dir",
        type=Path,
        default=None,
        help="Optional retained DATA-1F Binance Parquet directory for the basis stub.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    result = evaluate_retained_series(
        parquet_dir=cast(Path, args.parquet_dir),
        database_path=cast(Path, args.database),
        baseline=BaselineSlot(cast(str, args.baseline)),
        binance_parquet_dir=cast(Path | None, args.binance_parquet_dir),
    )
    print(json.dumps(result.to_json_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
