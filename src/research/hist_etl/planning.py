"""Turn a manifest into the Binance Vision files a run should cover.

Publication lag matches the public-data README: daily files the next UTC day,
monthly files on the first Monday of the following month.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from research.hist_etl.models import (
    BINANCE_VISION_BASE,
    INTERVAL_SECONDS,
    METRICS_SAMPLE_SECONDS,
    SPOT_MICROSECOND_START,
    ArchivePlan,
    BinanceSpec,
)

_CANONICAL_PREFIX = Path("binance-vision")


def resolve_end(spec: BinanceSpec, today: date) -> date:
    end = today if spec.end is None else spec.end
    if spec.start > end:
        return spec.start
    return end


def plan_binance(
    specs: tuple[BinanceSpec, ...], today: date, root: Path
) -> tuple[ArchivePlan, ...]:
    planned: list[ArchivePlan] = []
    for spec in specs:
        planned.extend(_plan_spec(spec, today, root))
    return tuple(planned)


def spot_timestamp_unit(period_start: date) -> str:
    """Spot files on/after 2025-01-01 are microseconds; earlier files are milliseconds."""

    if period_start >= SPOT_MICROSECOND_START:
        return "us"
    return "ms"


def sources_for_month(plans: tuple[ArchivePlan, ...]) -> tuple[ArchivePlan, ...]:
    """Monthly archives supersede dailies for the same dataset month."""

    monthly = tuple(plan for plan in plans if plan.granularity == "monthly")
    if monthly:
        return monthly
    return tuple(
        sorted(
            (plan for plan in plans if plan.granularity == "daily"), key=lambda item: item.period
        )
    )


def group_by_month(
    plans: tuple[ArchivePlan, ...],
) -> dict[tuple[str, str], tuple[ArchivePlan, ...]]:
    grouped: dict[tuple[str, str], list[ArchivePlan]] = defaultdict(list)
    for plan in plans:
        grouped[(plan.dataset_id, plan.month)].append(plan)
    return {key: tuple(value) for key, value in grouped.items()}


def coverage_window(
    spec: BinanceSpec, month: date, today: date
) -> tuple[datetime, datetime] | None:
    """Inclusive first and last expected sample, or None when nothing is publishable."""

    end = resolve_end(spec, today)
    start_dt = datetime.combine(max(month, spec.start), datetime.min.time())
    month_end_exclusive = datetime.combine(_next_month(month), datetime.min.time())
    range_end_exclusive = datetime.combine(end + timedelta(days=1), datetime.min.time())
    end_exclusive = min(month_end_exclusive, range_end_exclusive)
    if start_dt >= end_exclusive:
        return None
    step = _step_seconds(spec)
    if step is None:
        return None
    last = end_exclusive - timedelta(seconds=step)
    if last < start_dt:
        return None
    return start_dt, last


def _step_seconds(spec: BinanceSpec) -> int | None:
    if spec.dataset == "metrics":
        return METRICS_SAMPLE_SECONDS
    if spec.interval is None:
        return None
    if spec.dataset not in {"klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines"}:
        return None
    return INTERVAL_SECONDS[spec.interval]


def _plan_spec(spec: BinanceSpec, today: date, root: Path) -> list[ArchivePlan]:
    end = resolve_end(spec, today)
    if spec.start > end:
        return []
    planned: list[ArchivePlan] = []
    for month in _months(spec.start, end):
        published = _monthly_published(month, today)
        use_monthly = spec.granularity in {"monthly", "monthly_with_daily_tail"} and published
        if use_monthly:
            period = f"{month.year:04d}-{month.month:02d}"
            planned.append(_archive(spec, "monthly", period, month, root))
            continue
        if spec.granularity == "monthly":
            continue
        for day in _days(max(spec.start, month), min(end, _month_end(month))):
            if not _daily_published(day, today):
                continue
            planned.append(_archive(spec, "daily", day.isoformat(), month, root))
    return planned


def _archive(
    spec: BinanceSpec, granularity: str, period: str, month: date, root: Path
) -> ArchivePlan:
    relative = _vision_relative(spec, granularity, period)
    filename = relative.rsplit("/", 1)[-1]
    url = f"{BINANCE_VISION_BASE}{relative}"
    canonical_relative = str(_CANONICAL_PREFIX / relative)
    return ArchivePlan(
        dataset_id=spec.id,
        market=spec.market,
        dataset=spec.dataset,
        symbol=spec.symbol,
        interval=spec.interval,
        granularity=granularity,
        period=period,
        month=f"{month.year:04d}-{month.month:02d}",
        filename=filename,
        url=url,
        checksum_url=f"{url}.CHECKSUM",
        canonical_relative=canonical_relative,
        canonical_path=root / canonical_relative,
    )


def _vision_relative(spec: BinanceSpec, granularity: str, period: str) -> str:
    trading = "data/spot" if spec.market == "spot" else "data/futures/um"
    period_kind = "monthly" if granularity == "monthly" else "daily"
    if spec.interval is not None:
        folder = f"{trading}/{period_kind}/{spec.dataset}/{spec.symbol}/{spec.interval}"
        filename = f"{spec.symbol}-{spec.interval}-{period}.zip"
    else:
        folder = f"{trading}/{period_kind}/{spec.dataset}/{spec.symbol}"
        filename = f"{spec.symbol}-{spec.dataset}-{period}.zip"
    return f"{folder}/{filename}"


def _months(start: date, end: date) -> list[date]:
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    months: list[date] = []
    while cursor <= last:
        months.append(cursor)
        cursor = _next_month(cursor)
    return months


def _days(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _next_month(month: date) -> date:
    if month.month == 12:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


def _month_end(month: date) -> date:
    return _next_month(month) - timedelta(days=1)


def _first_monday_on_or_after(day: date) -> date:
    return day + timedelta(days=(7 - day.weekday()) % 7)


def _monthly_published(month: date, today: date) -> bool:
    return today >= _first_monday_on_or_after(_next_month(month))


def _daily_published(day: date, today: date) -> bool:
    return day < today
