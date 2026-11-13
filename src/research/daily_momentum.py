"""Causal daily BTC momentum experiments with explicit margin reporting.

The corrected fixed-quantity ledger remains authoritative. Margin is a fixed
reporting denominator, not permission to enable leveraged venue execution.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from itertools import pairwise
from typing import Any

from research.harness.errors import IntegrityError
from research.repaired_accounting import Frame, ReplayResult, ReplaySpec, Target, simulate

BTC = "BTCUSDT"
MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
GROSS = 0.5
REFERENCE_LEVERAGE = 10.0
MAINTENANCE_FRACTION = 0.0125
BENCHMARK_NAMES = (
    "cash",
    "always_long_time",
    "always_short_time",
    "always_long_bracket",
    "always_short_bracket",
)


def require(ok: bool, message: str) -> None:
    if not ok:
        raise IntegrityError("daily_momentum", message)


@dataclass(frozen=True, slots=True)
class DailyConfig:
    name: str
    lookback_hours: int
    filtered: bool
    bracket: bool

    def __post_init__(self) -> None:
        require(type(self.name) is str and 0 < len(self.name) <= 80, "Configuration name.")
        require(
            type(self.lookback_hours) is int and self.lookback_hours in (24, 72),
            "Completed-hour lookback must be 24 or 72.",
        )
        require(type(self.filtered) is bool and type(self.bracket) is bool, "Exact policy flags.")


@dataclass(frozen=True, slots=True)
class DailyEpisode:
    day_ms: int
    entry_ms: int
    exit_ms: int
    side: int
    score: float | None
    reason: str
    trigger_ms: int | None = None
    trigger_estimated_roi: float | None = None


def configs() -> tuple[DailyConfig, ...]:
    return tuple(
        DailyConfig(
            f"daily-l{lookback}-{'filtered' if filtered else 'forced'}-"
            f"{'bracket' if bracket else 'time'}",
            lookback,
            filtered,
            bracket,
        )
        for lookback in (24, 72)
        for filtered in (False, True)
        for bracket in (False, True)
    )


def _validate(spec: ReplaySpec, frames: Sequence[Frame]) -> dict[int, Frame]:
    require(
        spec.step_ms == MINUTE
        and spec.start_ms % DAY == spec.end_ms % DAY == 0
        and spec.end_ms > spec.start_ms,
        "Whole UTC days on a minute valuation grid are required.",
    )
    require(
        len(spec.instruments) == 1
        and spec.instruments[0].symbol == BTC
        and spec.instruments[0].kind == "perp",
        "Only the declared BTC perpetual is supported.",
    )
    indexed = {frame.timestamp_ms: frame for frame in frames}
    require(
        len(indexed) == len(frames)
        and tuple(indexed) == tuple(sorted(indexed))
        and all(t in indexed for t in range(spec.start_ms, spec.end_ms + 1, MINUTE)),
        "Complete unique ordered minute frames are required.",
    )
    return indexed


def _reference(frame: Frame) -> float:
    require(
        frame.execution_prices is not None and BTC in frame.execution_prices,
        "A native trade-price execution reference is required; marks are not fills.",
    )
    assert frame.execution_prices is not None
    price = frame.execution_prices[BTC]
    require(math.isfinite(price) and price > 0, "Positive finite execution reference.")
    return price


def _funding_prefix(frames: Sequence[Frame]) -> dict[int, float]:
    cumulative = 0.0
    result = {}
    for frame in frames:
        rate = frame.funding_rates.get(BTC)
        require(rate is not None and math.isfinite(rate), "Explicit complete funding coverage.")
        assert rate is not None
        if rate:
            mark = frame.marks.get(BTC)
            require(mark is not None and math.isfinite(mark) and mark > 0, "Funding event mark.")
            assert mark is not None
            cumulative += mark * rate
        result[frame.timestamp_ms] = cumulative
    return result


def _score(hourly: Mapping[int, float], instant: int, lookback: int) -> float | None:
    clock = tuple(instant - offset * HOUR for offset in range(lookback, -1, -1))
    if any(t not in hourly for t in clock):
        return None
    prices = [hourly[t] for t in clock]
    require(all(math.isfinite(p) and p > 0 for p in prices), "Positive hourly prices.")
    returns = [math.log(b / a) for a, b in pairwise(prices)]
    denominator = math.sqrt(math.fsum(value * value for value in returns))
    if denominator == 0:
        return None
    return math.log(prices[-1] / prices[0]) / denominator


def _bracket(
    spec: ReplaySpec,
    indexed: Mapping[int, Frame],
    funding: Mapping[int, float],
    entry: int,
    planned_exit: int,
    side: int,
) -> tuple[int, str, int | None, float | None]:
    instrument = spec.instruments[0]
    fee, slip = instrument.fee_bps / 10_000, instrument.slippage_bps / 10_000
    reference = _reference(indexed[entry])
    quantity = side / reference
    entry_fill = reference * (1 + side * slip)
    entry_fee = abs(quantity * entry_fill) * fee
    margin = abs(quantity * entry_fill) / REFERENCE_LEVERAGE
    for instant in range(entry + MINUTE, planned_exit, MINUTE):
        exit_fill = _reference(indexed[instant]) * (1 - side * slip)
        exit_fee = abs(quantity * exit_fill) * fee
        payment = -quantity * (funding[instant] - funding[entry])
        pnl = quantity * (exit_fill - entry_fill) - entry_fee - exit_fee + payment
        roi = pnl / margin
        if roi >= 0.20 or roi <= -0.10:
            return instant + MINUTE, "take_profit" if roi >= 0.20 else "stop", instant, roi
    return planned_exit, "time", None, None


def _targets(episodes: Sequence[DailyEpisode], end: int) -> tuple[Target, ...]:
    result = []
    for episode in episodes:
        result.append(Target(episode.day_ms, episode.entry_ms, {BTC: episode.side * GROSS}))
        if episode.exit_ms < end:
            decision = episode.trigger_ms if episode.trigger_ms is not None else episode.day_ms
            result.append(Target(decision, episode.exit_ms, {}))
    return tuple(result)


def build_schedule(
    config: DailyConfig,
    spec: ReplaySpec,
    frames: Sequence[Frame],
    hourly: Mapping[int, float],
    *,
    eligible_days: frozenset[int] | None = None,
) -> tuple[tuple[Target, ...], tuple[DailyEpisode, ...], dict[str, int]]:
    indexed = _validate(spec, frames)
    funding = _funding_prefix(frames)
    episodes = []
    counts = dict(
        calendar_days=0,
        eligible_days=0,
        unavailable_data_days=0,
        traded_days=0,
        unavailable_days=0,
        zero_signal_days=0,
        filtered_days=0,
    )
    for day in range(spec.start_ms, spec.end_ms, DAY):
        counts["calendar_days"] += 1
        if eligible_days is not None and day // DAY not in eligible_days:
            counts["unavailable_data_days"] += 1
            continue
        counts["eligible_days"] += 1
        score = _score(hourly, day, config.lookback_hours)
        if score is None:
            counts["unavailable_days"] += 1
            continue
        if score == 0:
            counts["zero_signal_days"] += 1
            continue
        if config.filtered and abs(score) < 1:
            counts["filtered_days"] += 1
            continue
        side, entry, planned_exit = (1 if score > 0 else -1), day + MINUTE, day + DAY
        _reference(indexed[entry])
        _reference(indexed[planned_exit])
        exit_ms, reason, trigger, roi = (
            _bracket(spec, indexed, funding, entry, planned_exit, side)
            if config.bracket
            else (planned_exit, "time", None, None)
        )
        episodes.append(DailyEpisode(day, entry, exit_ms, side, score, reason, trigger, roi))
        counts["traded_days"] += 1
    return _targets(episodes, spec.end_ms), tuple(episodes), counts


def _risk(
    episode: DailyEpisode,
    quantity: float,
    entry_fill: float,
    entry_mark: float,
    entry_fee: float,
    margin: float,
    frames: Sequence[Frame],
    extremes: Mapping[int, tuple[float, float]],
) -> list[dict[str, float | int | str]]:
    """Conservative current-contract collateral screen, not liquidation accounting.

    Within each bar debit every funding payment before the worst mark, without
    crediting positive funding that might have arrived only after that mark.
    """
    funding = 0.0
    prior_minute_funding = 0.0
    bar_debits = 0.0
    risks: list[dict[str, float | int | str]] = []
    # The position starts after same-timestamp funding, but already bears the
    # execution fee and the difference between its fill and current mark.
    entry_collateral = margin - entry_fee + quantity * (entry_mark - entry_fill)
    entry_maintenance = MAINTENANCE_FRACTION * abs(quantity) * entry_mark
    if entry_collateral <= entry_maintenance:
        risks.append(
            {
                "timestamp_ms": episode.entry_ms,
                "kind": "entry_possible_liquidation",
                "collateral": entry_collateral,
                "maintenance": entry_maintenance,
            }
        )
    for frame in frames:
        instant = frame.timestamp_ms
        if not episode.entry_ms < instant <= episode.exit_ms:
            continue
        rate = frame.funding_rates[BTC]
        assert rate is not None
        payment = -quantity * frame.marks[BTC] * rate
        funding += payment
        bar_debits += min(payment, 0.0)
        if instant % MINUTE == 0:
            require(instant in extremes, "Complete held-minute mark extremes are required.")
            low, high = extremes[instant]
            require(
                math.isfinite(low) and math.isfinite(high) and 0 < low <= high,
                "Positive ordered mark extremes.",
            )
            worst = low if quantity > 0 else high
            collateral = (
                margin
                - entry_fee
                + prior_minute_funding
                + bar_debits
                + quantity * (worst - entry_fill)
            )
            maintenance = MAINTENANCE_FRACTION * abs(quantity) * worst
            if collateral <= maintenance:
                risks.append(
                    {
                        "timestamp_ms": instant,
                        "kind": "intraminute_possible_liquidation",
                        "collateral": collateral,
                        "maintenance": maintenance,
                    }
                )
            prior_minute_funding, bar_debits = funding, 0.0
        # Exact asynchronous settlement marks can differ from minute OHLC.
        actual = margin - entry_fee + funding + quantity * (frame.marks[BTC] - entry_fill)
        maintenance = MAINTENANCE_FRACTION * abs(quantity) * frame.marks[BTC]
        if actual <= maintenance:
            risks.append(
                {
                    "timestamp_ms": instant,
                    "kind": "sampled_possible_liquidation",
                    "collateral": actual,
                    "maintenance": maintenance,
                }
            )
    return risks


def _summary(
    result: ReplayResult,
    frames: Sequence[Frame],
    episodes: Sequence[DailyEpisode],
    extremes: Mapping[int, tuple[float, float]],
    calendar_days: int,
    unavailable_data_days: int = 0,
) -> dict[str, Any]:
    trades = {trade.timestamp_ms: trade for trade in result.trades}
    require(
        len(trades) == len(result.trades) == 2 * len(episodes), "One entry and exit per episode."
    )
    funding = _funding_prefix(frames)
    rows: list[dict[str, Any]] = []
    risk_events = []
    pnl_parts = []
    clock = tuple(frame.timestamp_ms for frame in frames)
    for episode in episodes:
        entry, exit_trade = trades[episode.entry_ms], trades[episode.exit_ms]
        quantity = entry.quantity_delta
        require(
            quantity * episode.side > 0 and exit_trade.quantity_delta == -quantity,
            "Fixed quantities and terminal flatness.",
        )
        margin = abs(quantity * entry.fill_price) / REFERENCE_LEVERAGE
        payment = -quantity * (funding[episode.exit_ms] - funding[episode.entry_ms])
        pnl = quantity * (exit_trade.fill_price - entry.fill_price) - entry.fee - exit_trade.fee
        pnl += payment
        roi = pnl / margin
        pnl_parts.append(pnl)
        risks = _risk(
            episode,
            quantity,
            entry.fill_price,
            entry.mark,
            entry.fee,
            margin,
            frames[bisect_right(clock, episode.entry_ms) : bisect_right(clock, episode.exit_ms)],
            extremes,
        )
        risk_events.extend(risks)
        rows.append(
            asdict(episode)
            | {
                "quantity": quantity,
                "initial_margin": margin,
                "entry_fill_notional": abs(quantity * entry.fill_price),
                "reference_leverage": REFERENCE_LEVERAGE,
                "net_pnl": pnl,
                "net_margin_roi": roi,
                "funding": payment,
                "fee_cost": entry.fee + exit_trade.fee,
                "slippage_cost": entry.slippage_cost + exit_trade.slippage_cost,
                "realized_target_success": roi >= 0.20,
                "liquidation_possible": bool(risks),
            }
        )
    net_pnl = result.final_equity - result.initial_equity
    require(
        abs(math.fsum(pnl_parts) - net_pnl) <= 1e-7 * max(result.initial_equity, 1),
        "Episode PnL reconciles the marked ledger.",
    )
    require(not result.points[-1].positions, "Month ends flat.")
    fee_cost = math.fsum(trade.fee for trade in result.trades)
    slippage_cost = math.fsum(trade.slippage_cost for trade in result.trades)
    turnover = math.fsum(
        abs(t.quantity_delta * (t.reference_price or t.mark)) for t in result.trades
    )
    return {
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "fee_cost": fee_cost,
        "slippage_cost": slippage_cost,
        "total_cost": result.total_cost,
        "total_funding": result.total_funding,
        "same_quantity_gross_price_return": (net_pnl + result.total_cost - result.total_funding)
        / result.initial_equity,
        "turnover_notional": turnover,
        "asset_net_pnl": {BTC: net_pnl},
        "complete_episodes": len(rows),
        "eligible_days": calendar_days - unavailable_data_days,
        "calendar_days": calendar_days,
        "unavailable_data_days": unavailable_data_days,
        "no_trade_days": calendar_days - len(rows),
        "target_success_count": sum(row["realized_target_success"] for row in rows),
        "net_pnl_per_episode": net_pnl / len(rows) if rows else None,
        "mean_net_margin_roi": math.fsum(row["net_margin_roi"] for row in rows) / len(rows)
        if rows
        else None,
        "liquidation_possible": bool(risk_events),
        "maintenance_fraction_proxy": MAINTENANCE_FRACTION,
        "risk_events": risk_events,
        "risk_disposition": "counterfactual_risk_invalid"
        if risk_events
        else "sampled_current_contract_screen_only",
        "episodes": rows,
        "ledger": [asdict(point) for point in result.points],
        "trades": [asdict(trade) for trade in result.trades],
    }


def evaluate(
    config: DailyConfig,
    spec: ReplaySpec,
    frames: Sequence[Frame],
    hourly: Mapping[int, float],
    extremes: Mapping[int, tuple[float, float]],
    *,
    stress_initial_equity: float | None = None,
    eligible_days: frozenset[int] | None = None,
) -> dict[str, Any]:
    schedule, episodes, counts = build_schedule(
        config, spec, frames, hourly, eligible_days=eligible_days
    )
    output: dict[str, Any] = {"config": asdict(config), "counts": counts}
    for name, multiplier in (("base", 1), ("stress", 2)):
        scenario = replace(
            spec,
            initial_equity=stress_initial_equity
            if name == "stress" and stress_initial_equity is not None
            else spec.initial_equity,
            instruments=tuple(
                replace(i, fee_bps=i.fee_bps * multiplier, slippage_bps=i.slippage_bps * multiplier)
                for i in spec.instruments
            ),
        )
        output[name] = _summary(
            simulate(scenario, frames, schedule),
            frames,
            episodes,
            extremes,
            counts["calendar_days"],
            counts["unavailable_data_days"],
        )
    output["disposition"] = (
        "counterfactual_risk_invalid"
        if any(output[name]["liquidation_possible"] for name in ("base", "stress"))
        else "evaluated_exposed_history"
    )
    output["limitations"] = [
        "Exposed Binance historical proxy; no fresh OOS or Hyperliquid execution proof.",
        "Net brackets observe minute closes and execute the following minute, not touched stops.",
        "Stress replays base timestamps; its realized margin ROI need not reach the base target.",
        "Current BTC maintenance is a proxy screen, not historical liquidation proof.",
        "Potential liquidation leaves counterfactual PnL visible and prevents candidate admission.",
    ]
    return output


def benchmarks(
    spec: ReplaySpec,
    frames: Sequence[Frame],
    extremes: Mapping[int, tuple[float, float]],
    *,
    initial_equities: Mapping[str, tuple[float, float]] | None = None,
    eligible_days: frozenset[int] | None = None,
) -> dict[str, Any]:
    indexed = _validate(spec, frames)
    funding = _funding_prefix(frames)
    output = {}
    for name, side, bracket in (
        ("cash", 0, False),
        ("always_long_time", 1, False),
        ("always_short_time", -1, False),
        ("always_long_bracket", 1, True),
        ("always_short_bracket", -1, True),
    ):
        episodes = []
        if side:
            for day in range(spec.start_ms, spec.end_ms, DAY):
                if eligible_days is not None and day // DAY not in eligible_days:
                    continue
                entry = day + MINUTE
                _reference(indexed[entry])
                exit_ms, reason, trigger, roi = (
                    _bracket(spec, indexed, funding, entry, day + DAY, side)
                    if bracket
                    else (day + DAY, "time", None, None)
                )
                _reference(indexed[exit_ms])
                episodes.append(DailyEpisode(day, entry, exit_ms, side, None, reason, trigger, roi))
        schedule = _targets(episodes, spec.end_ms)
        pair = (spec.initial_equity, spec.initial_equity)
        if initial_equities is not None:
            require(name in initial_equities, "Every benchmark needs both carry-forward equities.")
            pair = initial_equities[name]
        cases = {}
        for index, (cost, multiplier) in enumerate((("base", 1), ("stress", 2))):
            scenario = replace(
                spec,
                initial_equity=pair[index],
                instruments=tuple(
                    replace(
                        i, fee_bps=i.fee_bps * multiplier, slippage_bps=i.slippage_bps * multiplier
                    )
                    for i in spec.instruments
                ),
            )
            cases[cost] = _summary(
                simulate(scenario, frames, schedule),
                frames,
                episodes,
                extremes,
                (spec.end_ms - spec.start_ms) // DAY,
                sum(
                    eligible_days is not None and day // DAY not in eligible_days
                    for day in range(spec.start_ms, spec.end_ms, DAY)
                ),
            )
        output[name] = cases
    return output
