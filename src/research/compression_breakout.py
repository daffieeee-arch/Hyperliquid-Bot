"""One frozen compression-breakout policy and explicit ENTRY-MARK/10 adapter.

A schedule uses quantity-per-equity linearity. The unchanged fixed-unit ledger
remains authoritative for capital, charged fills and native funding ownership.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any

from research.daily_momentum import DailyEpisode, _risk
from research.repaired_accounting import Frame, ReplaySpec, Target, simulate
from research.two_models_io import BTC, DAY, HOUR, MINUTE
from research.volume_repair_io import require

NAME = "btc-compression-half-prior24-nextminute-mark10"
REFERENCE_LEVERAGE = 10


@dataclass(frozen=True, slots=True)
class HourBox:
    low: float
    high: float
    close: float

    def __post_init__(self) -> None:
        require(
            all(math.isfinite(v) and v > 0 for v in (self.low, self.high, self.close))
            and self.low <= self.close <= self.high,
            "positive finite H1 OHLC bounds",
        )

    @property
    def normalized_range(self) -> float:
        return (self.high - self.low) / self.close


@dataclass(frozen=True, slots=True)
class Episode:
    decision_ms: int
    entry_ms: int
    exit_ms: int
    side: int
    coefficient: float
    decision_mark: float
    box_ms: int
    reason: str
    trigger_ms: int | None
    trigger_estimated_roi: float | None


@dataclass(frozen=True)
class Schedule:
    targets: tuple[Target, ...]
    episodes: tuple[Episode, ...]
    observations: tuple[dict[str, Any], ...]
    counts: dict[str, int]


def reference(frame: Frame) -> float:
    require(
        frame.execution_prices is not None and BTC in frame.execution_prices,
        "native TRADE minute execution reference; MARK is not a fill",
    )
    assert frame.execution_prices is not None
    value = frame.execution_prices[BTC]
    require(math.isfinite(value) and value > 0, "positive finite TRADE reference")
    return value


def indexed_frames(spec: ReplaySpec, frames: Sequence[Frame]) -> dict[int, Frame]:
    require(
        spec.step_ms == MINUTE and spec.start_ms % HOUR == spec.end_ms % HOUR == 0,
        "whole-hour segment and minute valuation grid",
    )
    require(
        len(spec.instruments) == 1
        and spec.instruments[0].symbol == BTC
        and spec.instruments[0].kind == "perp",
        "one BTC perpetual",
    )
    indexed = {f.timestamp_ms: f for f in frames}
    require(
        len(indexed) == len(frames)
        and tuple(indexed) == tuple(sorted(indexed))
        and set(range(spec.start_ms, spec.end_ms + 1, MINUTE)) <= indexed.keys(),
        "complete unique ordered minute source frames",
    )
    for frame in frames:
        rate = frame.funding_rates.get(BTC)
        require(rate is not None and math.isfinite(rate), "explicit finite funding coverage")
        if frame.timestamp_ms > spec.start_ms:
            require(
                BTC in frame.marks and math.isfinite(frame.marks[BTC]) and frame.marks[BTC] > 0,
                "complete qualified separate MARK",
            )
            if frame.timestamp_ms % MINUTE == 0:
                reference(frame)
    return indexed


def setup(hourly: Mapping[int, HourBox], instant: int) -> tuple[bool, float, float]:
    clock = tuple(instant - i * HOUR for i in range(24, -1, -1))
    require(all(t in hourly for t in clock), "25 complete H1 bars; no partial warmup")
    background = statistics.median(hourly[t].normalized_range for t in clock[:-1])
    value = hourly[instant].normalized_range
    return background > 0 and 0 < value <= background / 2, value, background


def funding_prefix(frames: Sequence[Frame]) -> dict[int, float]:
    result, total = {}, 0.0
    for frame in frames:
        rate = frame.funding_rates[BTC]
        assert rate is not None
        if rate:
            total += frame.marks[BTC] * rate
        result[frame.timestamp_ms] = total
    return result


def _exit(
    spec: ReplaySpec,
    indexed: Mapping[int, Frame],
    funding: Mapping[int, float],
    entry: int,
    coefficient: float,
) -> tuple[int, str, int | None, float | None]:
    side = 1 if coefficient > 0 else -1
    instrument = spec.instruments[0]
    fee, slip = instrument.fee_bps / 10000, instrument.slippage_bps / 10000
    fill = reference(indexed[entry]) * (1 + side * slip)
    entry_fee = abs(coefficient * fill) * fee
    margin = abs(coefficient) * indexed[entry].marks[BTC] / REFERENCE_LEVERAGE
    deadline = min(entry + DAY, spec.end_ms)
    reason = "segment_end" if deadline == spec.end_ms else "time"
    # A deadline wins a coincident delayed bracket execution.
    for t in range(entry + MINUTE, deadline, MINUTE):
        exit_fill = reference(indexed[t]) * (1 - side * slip)
        payment = -coefficient * (funding[t] - funding[entry])
        pnl = coefficient * (exit_fill - fill) - entry_fee - abs(coefficient * exit_fill) * fee
        roi = (pnl + payment) / margin
        if roi >= 0.20 or roi <= -0.10:
            if t + MINUTE == deadline:
                return deadline, reason, t, roi
            return t + MINUTE, "target" if roi >= 0.20 else "stop", t, roi
    return deadline, reason, None, None


def materialize(
    episodes: Sequence[Episode], indexed: Mapping[int, Frame], end: int
) -> tuple[Target, ...]:
    result = []
    for episode in episodes:
        # This transport cancels the ledger's reference denominator; coefficient
        # was locked from D's MARK. No future-price information sizes the units.
        weight = episode.coefficient * reference(indexed[episode.entry_ms])
        result.append(Target(episode.decision_ms, episode.entry_ms, {BTC: weight}))
        if episode.exit_ms < end:
            decision = episode.trigger_ms if episode.trigger_ms is not None else episode.entry_ms
            result.append(Target(decision, episode.exit_ms, {}))
    require(
        all(a.execution_ms < b.execution_ms for a, b in pairwise(result)),
        "unique causal execution order",
    )
    return tuple(result)


def build_schedule(
    spec: ReplaySpec, frames: Sequence[Frame], hourly: Mapping[int, HourBox]
) -> Schedule:
    indexed = indexed_frames(spec, frames)
    funding = funding_prefix(frames)
    observations: list[dict[str, Any]] = []
    episodes: list[Episode] = []
    counts: Counter[str] = Counter(
        {
            name: 0
            for name in (
                "minute_clocks",
                "hour_clocks",
                "zero_range_boxes",
                "zero_background",
                "compression_boxes",
                "expired_boxes",
                "armed_minute_clocks",
                "within_or_equal_box_minutes",
                "signals",
                "busy",
                "terminal_excluded",
                "entry",
                "exit_target",
                "exit_stop",
                "exit_time",
                "exit_segment_end",
                "target_triggers",
                "stop_triggers",
            )
        }
    )
    active: tuple[int, HourBox] | None = None
    available = spec.start_ms
    for t in range(spec.start_ms, spec.end_ms, MINUTE):
        counts["minute_clocks"] += 1
        if t % HOUR == 0:
            if active is not None:
                counts["expired_boxes"] += 1
            eligible, value, median = setup(hourly, t)
            counts["hour_clocks"] += 1
            counts["zero_range_boxes"] += value == 0
            counts["zero_background"] += median == 0
            counts["compression_boxes"] += eligible
            observations.append(
                {
                    "kind": "setup",
                    "timestamp_ms": t,
                    "range": value,
                    "prior24_median": median,
                    "native_open_ms": t - HOUR,
                    "native_close_ms": t - 1,
                    "box_low": hourly[t].low,
                    "box_high": hourly[t].high,
                    "box_close": hourly[t].close,
                    "eligible": eligible,
                }
            )
            active = (t, hourly[t]) if eligible else None
            continue
        if active is None:
            continue
        counts["armed_minute_clocks"] += 1
        box_t, box = active
        price = reference(indexed[t])
        side = 1 if price > box.high else -1 if price < box.low else 0
        if not side:
            counts["within_or_equal_box_minutes"] += 1
            continue
        active = None  # First breakout consumes the box even if this chain is busy.
        counts["signals"] += 1
        disposition = (
            "busy"
            if t < available
            else "terminal_excluded"
            if t + MINUTE >= spec.end_ms
            else "entry"
        )
        counts[disposition] += 1
        observations.append(
            {
                "kind": "signal",
                "timestamp_ms": t,
                "box_ms": box_t,
                "side": side,
                "trade_close": price,
                "disposition": disposition,
            }
        )
        if disposition != "entry":
            continue
        mark = indexed[t].marks[BTC]
        coefficient = side * 0.25 / mark
        entry = t + MINUTE
        exit_t, reason, trigger, roi = _exit(spec, indexed, funding, entry, coefficient)
        episodes.append(
            Episode(t, entry, exit_t, side, coefficient, mark, box_t, reason, trigger, roi)
        )
        counts["exit_" + reason] += 1
        if roi is not None:
            counts["target_triggers" if roi >= 0.20 else "stop_triggers"] += 1
        available = exit_t
    if active is not None:
        counts["expired_boxes"] += 1
    return Schedule(
        materialize(episodes, indexed, spec.end_ms),
        tuple(episodes),
        tuple(observations),
        dict(counts),
    )


def control_schedule(
    schedule: Schedule, indexed: Mapping[int, Frame], end: int, side: int
) -> Schedule:
    require(side in (-1, 0, 1), "fixed conditional control direction")
    episodes = (
        tuple(
            Episode(
                e.decision_ms,
                e.entry_ms,
                e.exit_ms,
                side,
                side * abs(e.coefficient),
                e.decision_mark,
                e.box_ms,
                "matched_" + e.reason,
                e.trigger_ms,
                None,
            )
            for e in schedule.episodes
        )
        if side
        else ()
    )
    return Schedule(
        materialize(episodes, indexed, end),
        episodes,
        schedule.observations,
        {"matched_candidate_entries": len(schedule.episodes), "entries": len(episodes)},
    )


def evaluate_case(
    spec: ReplaySpec,
    frames: Sequence[Frame],
    schedule: Schedule,
    extremes: Mapping[int, tuple[float, float]],
) -> dict[str, Any]:
    indexed_frames(spec, frames)
    result = simulate(spec, frames, schedule.targets)
    trades = {t.timestamp_ms: t for t in result.trades}
    require(
        len(trades) == len(result.trades) == 2 * len(schedule.episodes),
        "two executions per episode",
    )
    funding = funding_prefix(frames)
    clock = tuple(f.timestamp_ms for f in frames)
    points = {p.timestamp_ms: p for p in result.points}
    rows, risks = [], []
    for episode in schedule.episodes:
        entry, exit_trade = trades[episode.entry_ms], trades[episode.exit_ms]
        decision_equity = points[episode.decision_ms].equity
        q = entry.quantity_delta
        committed = episode.coefficient * decision_equity
        require(
            math.isclose(q, committed, rel_tol=1e-12, abs_tol=1e-12)
            and math.isclose(exit_trade.quantity_delta, -q, rel_tol=1e-12),
            "causal quantity commitment and fixed-unit close reconcile",
        )
        margin = abs(q) * entry.mark / REFERENCE_LEVERAGE
        payment = -q * (funding[episode.exit_ms] - funding[episode.entry_ms])
        pnl = q * (exit_trade.fill_price - entry.fill_price) - entry.fee - exit_trade.fee + payment
        selected = frames[
            bisect_right(clock, episode.entry_ms) : bisect_right(clock, episode.exit_ms)
        ]
        # Existing risk arithmetic takes an explicit supplied margin. Its old
        # ENTRY-FILL/10 summary is never called or relabeled here.
        risk_episode = DailyEpisode(
            episode.decision_ms,
            episode.entry_ms,
            episode.exit_ms,
            episode.side,
            None,
            episode.reason,
        )
        local = _risk(
            risk_episode, q, entry.fill_price, entry.mark, entry.fee, margin, selected, extremes
        )
        if abs(q) * entry.mark > 0.5 * decision_equity:
            local.append(
                {
                    "timestamp_ms": episode.entry_ms,
                    "kind": "entry_asset_cap_overrun",
                    "actual_marked_notional": abs(q) * entry.mark,
                    "decision_equity": decision_equity,
                }
            )
        if margin + entry.fee > decision_equity:
            local.append(
                {
                    "timestamp_ms": episode.entry_ms,
                    "kind": "entry_margin_cash_overrun",
                    "initial_margin_plus_fee": margin + entry.fee,
                    "available_equity": decision_equity,
                }
            )
        if margin + pnl <= 0:
            local.append(
                {
                    "timestamp_ms": episode.exit_ms,
                    "kind": "exit_collateral_exhausted",
                    "collateral_after_charged_close": margin + pnl,
                }
            )
        risks.extend(local)
        minute_bounds = [
            extremes[t] for t in range(episode.entry_ms + MINUTE, episode.exit_ms + 1, MINUTE)
        ]
        adverse = (
            min([episode.side * entry.mark, *((lo if q > 0 else -hi) for lo, hi in minute_bounds)])
            if minute_bounds
            else entry.mark * episode.side
        )
        favorable = (
            max([episode.side * entry.mark, *((hi if q > 0 else -lo) for lo, hi in minute_bounds)])
            if minute_bounds
            else entry.mark * episode.side
        )
        assert entry.reference_price is not None and exit_trade.reference_price is not None
        rows.append(
            asdict(episode)
            | {
                "day_ms": episode.decision_ms // DAY * DAY,
                "quantity": q,
                "decision_equity": decision_equity,
                "committed_quantity": committed,
                "actual_entry_mark": entry.mark,
                "initial_margin": margin,
                "margin_denominator": "abs(quantity)*ACTUAL_ENTRY_MARK/10",
                "reference_leverage_offline_only": REFERENCE_LEVERAGE,
                "entry_fill_notional": abs(q * entry.fill_price),
                "net_pnl": pnl,
                "net_margin_roi": pnl / margin,
                "target_met": pnl / margin >= 0.20,
                "funding": payment,
                "fee_cost": entry.fee + exit_trade.fee,
                "slippage_cost": entry.slippage_cost + exit_trade.slippage_cost,
                "gross_price_pnl": q * (exit_trade.reference_price - entry.reference_price),
                "holding_minutes": (episode.exit_ms - episode.entry_ms) // MINUTE,
                "marked_adverse_price_pnl_per_margin": abs(q)
                * (adverse - episode.side * entry.fill_price)
                / margin,
                "marked_favorable_price_pnl_per_margin": abs(q)
                * (favorable - episode.side * entry.fill_price)
                / margin,
                "risk_invalid": bool(local),
                "risk_events": local,
            }
        )
    require(
        math.isclose(
            math.fsum(r["net_pnl"] for r in rows),
            result.final_equity - result.initial_equity,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ),
        "episode PnL reconciles authoritative ledger",
    )
    require(not result.points[-1].positions, "paid flat terminal")
    return {
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "total_funding": result.total_funding,
        "counts": schedule.counts,
        "fee_cost": math.fsum(t.fee for t in result.trades),
        "slippage_cost": math.fsum(t.slippage_cost for t in result.trades),
        "risk_events": risks,
        "episodes": rows,
        "ledger": [asdict(p) for p in result.points],
        "trades": [asdict(t) for t in result.trades],
    }
