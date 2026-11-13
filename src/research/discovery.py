"""Bounded exploratory mechanisms with causal targets and corrected accounting.

This module is numerical only: it has no data acquisition, signing or trading
client. Historical candidates remain exposed-history leads, never validated edge.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from itertools import pairwise
from typing import Any, Literal

from research.harness.errors import HarnessError, IntegrityError
from research.repaired_accounting import Frame, ReplayResult, ReplaySpec, Target, simulate

HOUR = 3_600_000
MINUTE = 60_000
BTC = "BTCUSDT"
ALTS = ("ETHUSDT", "SOLUSDT")
GROSS = 0.5


def require(ok: bool, message: str) -> None:
    if not ok:
        raise IntegrityError("discovery", message)


@dataclass(frozen=True, slots=True)
class DiscoveryBar:
    symbol: str
    close_ms: int
    close: float
    open_interest: float | None = None
    oi_timestamp_ms: int | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    name: str
    family: Literal["residual", "oi_deleveraging"]
    lookback_hours: int
    hold_hours: int
    direction: int
    price_threshold: float = 0.0

    def __post_init__(self) -> None:
        require(type(self.name) is str and 0 < len(self.name) <= 80, "Config identity required.")
        require(type(self.hold_hours) is int and self.hold_hours in (4, 12), "Holding horizon.")
        require(type(self.direction) is int and self.direction in (-1, 1), "Direction.")
        require(type(self.lookback_hours) is int, "Lookback must be an exact integer.")
        require(
            type(self.price_threshold) in (int, float) and math.isfinite(self.price_threshold),
            "Finite price threshold required.",
        )
        require(
            (
                self.family == "residual"
                and self.lookback_hours in (24, 72)
                and self.price_threshold == 0.0
            )
            or (
                self.family == "oi_deleveraging"
                and self.lookback_hours == 1
                and self.direction == -1
                and self.price_threshold in (0.005, 0.01)
            ),
            "Unknown discovery family/configuration.",
        )


@dataclass(frozen=True, slots=True)
class Episode:
    decision_ms: int
    entry_ms: int
    exit_ms: int
    weights: dict[str, float]


def configs() -> tuple[DiscoveryConfig, ...]:
    result = [
        DiscoveryConfig(f"residual-{side}-l{lookback}-h{hold}", "residual", lookback, hold, sign)
        for side, sign in (("continuation", 1), ("reversion", -1))
        for lookback in (24, 72)
        for hold in (4, 12)
    ]
    result.extend(
        DiscoveryConfig(
            f"oi-recovery-p{int(threshold * 10000)}-h{hold}",
            "oi_deleveraging",
            1,
            hold,
            -1,
            threshold,
        )
        for threshold in (0.005, 0.01)
        for hold in (4, 12)
    )
    return tuple(result)


def _bars(
    bars: Mapping[str, Sequence[DiscoveryBar]],
) -> dict[str, dict[int, DiscoveryBar]]:
    require(set(bars) <= {BTC, *ALTS} and BTC in bars, "Declared no-ICP discovery universe.")
    result = {}
    for symbol, series in bars.items():
        require(bool(series), "Empty hourly series.")
        indexed = {}
        last = None
        for bar in series:
            require(bar.symbol == symbol, "Bar instrument mismatch.")
            require(
                type(bar.close_ms) is int and bar.close_ms >= 0 and bar.close_ms % HOUR == 0,
                "Whole completed-hour clock required.",
            )
            require(last is None or bar.close_ms == last + HOUR, "Contiguous hourly series.")
            require(
                type(bar.close) in (int, float) and math.isfinite(bar.close) and bar.close > 0,
                "Positive finite close required.",
            )
            if bar.open_interest is None:
                require(bar.oi_timestamp_ms is None, "OI value/timestamp availability differs.")
            else:
                require(
                    type(bar.open_interest) in (int, float)
                    and math.isfinite(bar.open_interest)
                    and bar.open_interest > 0,
                    "Positive finite OI required.",
                )
                require(
                    type(bar.oi_timestamp_ms) is int
                    and bar.oi_timestamp_ms >= 0
                    and bar.oi_timestamp_ms == bar.close_ms - 5 * MINUTE,
                    "OI must use the exact five-minute-lagged snapshot.",
                )
            indexed[bar.close_ms] = bar
            last = bar.close_ms
        result[symbol] = indexed
    return result


def _hedged_weight(symbol: str, direction: int, beta: float) -> dict[str, float]:
    alt = direction * GROSS / (1 + abs(beta))
    result = {symbol: alt, BTC: -beta * alt}
    gross = math.fsum(abs(value) for value in result.values())
    if gross > GROSS:
        factor = math.nextafter(GROSS / gross, 0.0)
        result = {key: value * factor for key, value in result.items()}
    require(math.fsum(abs(value) for value in result.values()) <= GROSS, "Gross exposure cap.")
    return {key: value for key, value in result.items() if value != 0}


def _residual(
    config: DiscoveryConfig,
    data: dict[str, dict[int, DiscoveryBar]],
    instant: int,
) -> dict[str, float] | None:
    require(all(symbol in data for symbol in ALTS), "Residual family requires ETH and SOL.")
    clock = tuple(instant - offset * HOUR for offset in range(168, -1, -1))
    if any(t not in data[symbol] for symbol in (BTC, *ALTS) for t in clock):
        return None
    btc = [data[BTC][t].close for t in clock]
    x = [b / a - 1 for a, b in pairwise(btc)]
    mean_x = math.fsum(x) / len(x)
    variance = math.fsum((value - mean_x) ** 2 for value in x)
    if variance <= 1e-18:
        return None
    candidates = []
    for symbol in ALTS:
        prices = [data[symbol][t].close for t in clock]
        y = [b / a - 1 for a, b in pairwise(prices)]
        mean_y = math.fsum(y) / len(y)
        beta = math.fsum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True)) / variance
        require(math.isfinite(beta), "Invalid trailing beta.")
        if abs(beta) > 5:
            continue
        earlier = instant - config.lookback_hours * HOUR
        residual = data[symbol][instant].close / data[symbol][earlier].close - 1
        residual -= beta * (data[BTC][instant].close / data[BTC][earlier].close - 1)
        if residual != 0:
            candidates.append((abs(residual), symbol, residual, beta))
    if not candidates:
        return None
    _, symbol, residual, beta = sorted(candidates, key=lambda row: (-row[0], row[1]))[0]
    direction = config.direction * (1 if residual > 0 else -1)
    return _hedged_weight(symbol, direction, beta)


def _oi(
    config: DiscoveryConfig,
    data: dict[str, dict[int, DiscoveryBar]],
    instant: int,
) -> dict[str, float] | None:
    before, current = data[BTC].get(instant - HOUR), data[BTC].get(instant)
    if (
        before is None
        or current is None
        or before.open_interest is None
        or current.open_interest is None
    ):
        return None
    change = current.close / before.close - 1
    price_shock = current.close >= before.close * (
        1 + config.price_threshold
    ) or current.close <= before.close * (1 - config.price_threshold)
    if not price_shock or current.open_interest > before.open_interest * 0.99:
        return {}
    return {BTC: -GROSS if change > 0 else GROSS}


def generate_episodes(
    config: DiscoveryConfig,
    bars: Mapping[str, Sequence[DiscoveryBar]],
    start_ms: int,
    end_ms: int,
    boundaries: Sequence[int] = (),
) -> tuple[tuple[Episode, ...], dict[str, int]]:
    require(
        type(start_ms) is int
        and type(end_ms) is int
        and start_ms >= 0
        and end_ms > start_ms
        and start_ms % HOUR == end_ms % HOUR == 0,
        "Study clock.",
    )
    require(
        all(type(t) is int and start_ms < t < end_ms and t % HOUR == 0 for t in boundaries),
        "Known diagnostic boundaries.",
    )
    data = _bars(bars)
    counts = {"decisions": 0, "unavailable": 0, "no_signal": 0, "boundary": 0, "busy": 0}
    episodes = []
    busy_until = start_ms
    for instant in range(start_ms, end_ms, HOUR):
        entry, exit_ms = instant + HOUR, instant + (1 + config.hold_hours) * HOUR
        if exit_ms > end_ms or any(instant < t < exit_ms for t in boundaries):
            counts["boundary"] += 1
            continue
        if instant < busy_until:
            counts["busy"] += 1
            continue
        counts["decisions"] += 1
        weights = (_residual if config.family == "residual" else _oi)(config, data, instant)
        if weights is None:
            counts["unavailable"] += 1
            continue
        if not weights:
            counts["no_signal"] += 1
            continue
        require(entry > instant and exit_ms > entry, "Strict causal entry and exit.")
        episodes.append(Episode(instant, entry, exit_ms, weights))
        busy_until = exit_ms
    return tuple(episodes), counts


def targets(episodes: Sequence[Episode]) -> tuple[Target, ...]:
    result: list[Target] = []
    last_exit = -1
    for episode in episodes:
        require(
            episode.entry_ms == episode.decision_ms + HOUR
            and episode.exit_ms > episode.entry_ms
            and episode.decision_ms >= last_exit,
            "Nonoverlapping delayed episodes.",
        )
        require(
            set(episode.weights) <= {BTC, *ALTS}
            and all(math.isfinite(w) for w in episode.weights.values())
            and math.fsum(abs(w) for w in episode.weights.values()) <= GROSS,
            "Episode exposure.",
        )
        result.extend(
            (
                Target(episode.decision_ms, episode.entry_ms, dict(episode.weights)),
                Target(episode.decision_ms, episode.exit_ms, {}),
            )
        )
        last_exit = episode.exit_ms
    return tuple(result)


def _summary(
    result: ReplayResult,
    frames: Sequence[Frame],
    episodes: Sequence[Episode],
    split_ms: int,
) -> dict[str, Any]:
    points = result.points
    require(any(point.timestamp_ms == split_ms for point in points), "Exact diagnostic split mark.")
    first, last = points[0].timestamp_ms, points[-1].timestamp_ms
    at = {point.timestamp_ms: point.equity for point in points}
    execution_changes: dict[int, list[float]] = {}
    for trade in result.trades:
        execution_changes.setdefault(trade.timestamp_ms, []).append(
            trade.quantity_delta * (trade.mark - trade.fill_price) - trade.fee
        )
    segments = {}
    for name, start, end in (("development", first, split_ms), ("diagnostic", split_ms, last)):
        own = [point for point in points if start <= point.timestamp_ms <= end]
        peak = own[0].equity
        dd = 0.0
        for point in own[1:]:
            # Reverse executions only: the preceding funding payment is already
            # present. Start each segment at its postboundary ledger endpoint.
            before = point.equity - math.fsum(execution_changes.get(point.timestamp_ms, ()))
            for equity in (before, point.equity):
                peak = max(peak, equity)
                dd = max(dd, 1 - equity / peak)
        n = sum(start <= episode.entry_ms < episode.exit_ms <= end for episode in episodes)
        segments[name] = {
            "start_ms": start,
            "end_ms": end,
            "net_return": at[end] / at[start] - 1,
            "marked_max_drawdown": dd,
            "complete_episodes": n,
            "low_sample": n < 10,
        }
    daily = []
    previous_equity = result.initial_equity
    day = 86_400_000
    for index, point in enumerate(points):
        if index + 1 == len(points) or (point.timestamp_ms % day == 0 and index > 0):
            daily.append(
                {"end_ms": point.timestamp_ms, "net_return": point.equity / previous_equity - 1}
            )
            previous_equity = point.equity
    contributions: dict[str, list[float]] = {}
    for trade in result.trades:
        contributions.setdefault(trade.symbol, []).append(
            -trade.quantity_delta * trade.fill_price - trade.fee
        )
    previous_positions: dict[str, float] = {}
    for frame, point in zip(frames, points, strict=True):
        for symbol, quantity in previous_positions.items():
            rate = frame.funding_rates.get(symbol)
            require(rate is not None, "Funding attribution coverage.")
            assert rate is not None
            contributions.setdefault(symbol, []).append(-quantity * frame.marks[symbol] * rate)
        previous_positions = point.positions
    pnl = {symbol: math.fsum(parts) for symbol, parts in contributions.items()}
    require(
        abs(math.fsum(pnl.values()) - (result.final_equity - result.initial_equity))
        <= 1e-7 * max(1.0, result.initial_equity),
        "Asset attribution reconciliation.",
    )
    total_absolute = math.fsum(abs(value) for value in pnl.values())
    net_pnl = result.final_equity - result.initial_equity
    gross_price_pnl = net_pnl + result.total_cost - result.total_funding
    turnover = math.fsum(
        abs(trade.quantity_delta * (trade.reference_price or trade.mark)) for trade in result.trades
    )
    return {
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "fee_cost": math.fsum(trade.fee for trade in result.trades),
        "slippage_cost": math.fsum(trade.slippage_cost for trade in result.trades),
        "total_funding": result.total_funding,
        "total_cost": result.total_cost,
        "same_quantity_gross_price_return": gross_price_pnl / result.initial_equity,
        "net_pnl_per_completed_episode": net_pnl / len(episodes) if episodes else None,
        "same_quantity_gross_price_pnl_per_completed_episode": gross_price_pnl / len(episodes)
        if episodes
        else None,
        # An approximation on actual quantities, retaining funding. This is not
        # an independent zero-cost simulation or an executable-quote estimate.
        "same_quantity_break_even_friction_bps": (net_pnl + result.total_cost) / turnover * 10_000
        if turnover
        else None,
        "turnover_notional": turnover,
        "fills": len(result.trades),
        "complete_episodes": len(episodes),
        "asset_net_pnl": pnl,
        "absolute_pnl_concentration": max((abs(value) for value in pnl.values()), default=0)
        / total_absolute
        if total_absolute
        else None,
        "segments": segments,
        "daily": daily,
        "ledger": [asdict(point) for point in points],
        "trades": [asdict(trade) for trade in result.trades],
    }


def evaluate_config(
    config: DiscoveryConfig,
    spec: ReplaySpec,
    frames: Sequence[Frame],
    bars: Mapping[str, Sequence[DiscoveryBar]],
    split_ms: int,
) -> dict[str, Any]:
    require(spec.start_ms < split_ms < spec.end_ms and split_ms % HOUR == 0, "Diagnostic split.")
    require(
        all(instrument.kind == "perp" for instrument in spec.instruments), "Perpetual discovery."
    )
    episodes, counts = generate_episodes(config, bars, spec.start_ms, spec.end_ms, (split_ms,))
    schedule = tuple(target for target in targets(episodes) if target.execution_ms < spec.end_ms)
    base = _summary(simulate(spec, frames, schedule), frames, episodes, split_ms)
    stress_spec = replace(
        spec,
        instruments=tuple(
            replace(
                instrument, fee_bps=2 * instrument.fee_bps, slippage_bps=2 * instrument.slippage_bps
            )
            for instrument in spec.instruments
        ),
    )
    stress = _summary(simulate(stress_spec, frames, schedule), frames, episodes, split_ms)
    segments = ("development", "diagnostic")
    empty = any(base["segments"][name]["complete_episodes"] == 0 for name in segments)
    positive = all(
        case["segments"][name]["net_return"] > 0 for case in (base, stress) for name in segments
    )
    verdict = "insufficient" if empty else "exploratory_candidate" if positive else "reject"
    return {
        "config": asdict(config),
        "verdict": verdict,
        "counts": counts,
        "episodes": [asdict(episode) for episode in episodes],
        "base": base,
        "stress": stress,
        "limitations": [
            "Both periods are exposed retrospective development/diagnostic history, not fresh OOS.",
            "Survivor universe, proxy fills and sampled risk are not Hyperliquid execution proof.",
            "OI uses a five-minute-lag historical clock model, not observed live receipts.",
            "Low sample counts are reported, not hidden or used to claim statistical confidence.",
            "No calibration, leverage optimization, trading promotion or validated edge.",
        ],
    }


def run_batch(
    spec: ReplaySpec,
    frames: Sequence[Frame],
    bars: Mapping[str, Sequence[DiscoveryBar]],
    split_ms: int,
    configurations: Sequence[DiscoveryConfig] | None = None,
) -> tuple[dict[str, Any], ...]:
    chosen = configs() if configurations is None else tuple(configurations)
    require(
        0 < len(chosen) <= 40 and len({item.name for item in chosen}) == len(chosen),
        "Batch budget/identities.",
    )
    output = []
    for config in chosen:
        try:
            output.append(evaluate_config(config, spec, frames, bars, split_ms))
        except (HarnessError, ValueError, KeyError, TypeError, ArithmeticError) as error:
            output.append(
                {
                    "config": asdict(config),
                    "verdict": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error)[:240],
                }
            )
    return tuple(output)


def benchmarks(spec: ReplaySpec, frames: Sequence[Frame], split_ms: int) -> dict[str, Any]:
    """Fixed non-selected baselines, with the same explicit execution/cost model."""
    require(
        any(instrument.symbol == BTC for instrument in spec.instruments),
        "BTC benchmark instrument.",
    )
    output = {}
    for name, schedule in (
        ("cash", ()),
        ("half_btc_buy_hold", (Target(spec.start_ms, spec.start_ms + HOUR, {BTC: GROSS}),)),
    ):
        cases = {}
        for cost_name, multiplier in (("base", 1), ("stress", 2)):
            scenario = replace(
                spec,
                instruments=tuple(
                    replace(
                        item,
                        fee_bps=multiplier * item.fee_bps,
                        slippage_bps=multiplier * item.slippage_bps,
                    )
                    for item in spec.instruments
                ),
            )
            cases[cost_name] = _summary(simulate(scenario, frames, schedule), frames, (), split_ms)
        output[name] = cases | {"may_remain_exposed_across_split": name != "cash"}
    return output
