"""Persistent, volatility-sized BTC regime research on the corrected ledger.

Only schedule changes cause trades. Native execution references, immutable mark
margin and a complete held-minute collateral screen remain separate from alpha.
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
VOL_HOURS = 720
RISK_TARGET = 0.10
ENTRY_CAP = 0.5
ISOLATED_LEVERAGE = 2.0
MAINTENANCE_FRACTION = 0.0125


def require(ok: bool, message: str) -> None:
    if not ok:
        raise IntegrityError("regime_momentum", message)


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    name: str
    lookback_hours: int
    confirmed: bool

    def __post_init__(self) -> None:
        require(type(self.name) is str and 0 < len(self.name) <= 80, "Configuration name.")
        require(
            type(self.lookback_hours) is int and self.lookback_hours in (168, 720),
            "Lookback must be exactly 168 or 720 completed hours.",
        )
        require(type(self.confirmed) is bool, "Exact confirmation flag.")


@dataclass(frozen=True, slots=True)
class RegimeDecision:
    timestamp_ms: int
    score: float | None
    annualized_volatility: float | None
    confirmation_side: int | None
    previous_side: int
    desired_side: int
    entry_weight: float | None
    action: str


@dataclass(frozen=True, slots=True)
class RegimeSchedule:
    targets: tuple[Target, ...]
    decisions: tuple[RegimeDecision, ...]


def configs() -> tuple[RegimeConfig, ...]:
    return tuple(
        RegimeConfig(f"regime-h{hours}-{'confirmed' if confirmed else 'plain'}", hours, confirmed)
        for hours in (168, 720)
        for confirmed in (False, True)
    )


def _validate(spec: ReplaySpec, frames: Sequence[Frame]) -> dict[int, Frame]:
    require(
        spec.step_ms == HOUR
        and spec.start_ms % DAY == spec.end_ms % DAY == 0
        and spec.end_ms > spec.start_ms,
        "Whole UTC days and an hourly regular grid are required.",
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
        and all(t in indexed for t in range(spec.start_ms, spec.end_ms + 1, HOUR)),
        "Complete unique ordered hourly valuation frames are required.",
    )
    return indexed


def _execution(indexed: Mapping[int, Frame], instant: int) -> None:
    require(instant in indexed, "Scheduled native execution frame is required.")
    frame = indexed[instant]
    require(
        frame.execution_prices is not None and BTC in frame.execution_prices,
        "Native trade execution references are required; marks are not fills.",
    )
    assert frame.execution_prices is not None
    for value in (frame.execution_prices[BTC], frame.marks.get(BTC, 0.0)):
        require(math.isfinite(value) and value > 0, "Positive native execution price and mark.")


def features(
    config: RegimeConfig, hourly: Mapping[int, float], instant: int
) -> tuple[float | None, float | None, int | None]:
    """Read only the 721 completed hourly observations ending at the decision."""
    clock = tuple(instant - offset * HOUR for offset in range(VOL_HOURS, -1, -1))
    if any(t not in hourly for t in clock):
        return None, None, None
    prices = [hourly[t] for t in clock]
    require(all(math.isfinite(p) and p > 0 for p in prices), "Finite positive hourly closes.")
    returns = [math.log(b / a) for a, b in pairwise(prices)]
    variance = math.fsum(r * r for r in returns)
    if variance == 0:
        return None, None, None
    volatility = math.sqrt(365 * 24 * variance / VOL_HOURS)
    window = returns[-config.lookback_hours :]
    denominator = math.sqrt(math.fsum(r * r for r in window))
    score = math.fsum(window) / denominator if denominator else None
    average = math.fsum(prices[1:]) / VOL_HOURS
    side = 1 if prices[-1] > average else -1 if prices[-1] < average else 0
    return score, volatility, side


def build_schedule(
    config: RegimeConfig,
    spec: ReplaySpec,
    frames: Sequence[Frame],
    hourly: Mapping[int, float],
) -> RegimeSchedule:
    indexed = _validate(spec, frames)
    targets: list[Target] = []
    decisions: list[RegimeDecision] = []
    side = 0
    for instant in range(spec.start_ms, spec.end_ms, DAY):
        score, sigma, confirmation = features(config, hourly, instant)
        previous = side
        desired = 0
        weight = None
        if score is not None and sigma is not None and sigma > 0:
            direction = 1 if score > 0 else -1 if score < 0 else 0
            confirmation_ok = not config.confirmed or confirmation == direction
            if direction and confirmation_ok:
                if direction == previous and abs(score) >= 0.5:
                    desired = direction
                elif abs(score) >= 1:
                    desired = direction
            if desired and desired != previous:
                weight = min(ENTRY_CAP, RISK_TARGET / sigma)
        action = "hold" if desired else "flat"
        if previous != desired:
            if previous:
                _execution(indexed, instant + MINUTE)
                targets.append(Target(instant, instant + MINUTE, {}))
                action = "close"
            if desired:
                execution = instant + (2 if previous else 1) * MINUTE
                require(execution < spec.end_ms, "Entry must precede terminal close.")
                _execution(indexed, execution)
                assert weight is not None
                targets.append(Target(instant, execution, {BTC: desired * weight}))
                action = "reverse" if previous else "enter"
        decisions.append(
            RegimeDecision(instant, score, sigma, confirmation, previous, desired, weight, action)
        )
        side = desired
    return RegimeSchedule(tuple(targets), tuple(decisions))


def control_schedule(
    name: str,
    spec: ReplaySpec,
    frames: Sequence[Frame],
    hourly: Mapping[int, float],
    *,
    matched: RegimeSchedule | None = None,
) -> tuple[Target, ...]:
    """Directional controls share the execution/risk budget, never a maker fill."""
    indexed = _validate(spec, frames)
    require(
        name in ("cash", "persistent_long", "persistent_short", "matched_long", "matched_short"),
        "Known control name.",
    )
    if name == "cash":
        return ()
    side = -1 if name.endswith("short") else 1
    if name.startswith("matched"):
        require(matched is not None, "Matched controls need the original regime schedule.")
        assert matched is not None
        return tuple(
            replace(
                target, weights={BTC: side * abs(target.weights[BTC])} if target.weights else {}
            )
            for target in matched.targets
        )
    config = configs()[0]
    for instant in range(spec.start_ms, spec.end_ms, DAY):
        _, sigma, _ = features(config, hourly, instant)
        if sigma is not None and sigma > 0:
            _execution(indexed, instant + MINUTE)
            return (
                Target(
                    instant, instant + MINUTE, {BTC: side * min(ENTRY_CAP, RISK_TARGET / sigma)}
                ),
            )
    return ()


def _funding(
    frames: Sequence[Frame],
) -> tuple[dict[int, float], tuple[Frame, ...]]:
    cumulative = 0.0
    prefix = {}
    events = []
    for frame in frames:
        rate = frame.funding_rates.get(BTC)
        require(rate is not None and math.isfinite(rate), "Explicit complete funding coverage.")
        assert rate is not None
        if rate:
            mark = frame.marks.get(BTC)
            require(mark is not None and math.isfinite(mark) and mark > 0, "Funding mark price.")
            assert mark is not None
            cumulative += rate * mark
            events.append(frame)
        prefix[frame.timestamp_ms] = cumulative
    return prefix, tuple(events)


def _risk(
    row: dict[str, Any],
    frames: Sequence[Frame],
    funding_events: Sequence[Frame],
    extremes: Mapping[int, tuple[float, float]],
) -> list[dict[str, Any]]:
    """Audit every held minute, not merely the sparse account valuation grid.

    Negative funding in a minute is debited before that minute's worst mark;
    positive funding is credited only to later bars because its intrabar order
    is unknown. Exact sampled settlement marks are checked independently.
    """
    quantity = float(row["quantity"])
    margin = float(row["initial_margin"])
    fill = float(row["entry_fill_price"])
    fee = float(row["entry_fee"])
    entry, exit_ms = int(row["entry_ms"]), int(row["exit_ms"])
    risks: list[dict[str, Any]] = []

    def check(instant: int, mark: float, funding: float, kind: str) -> None:
        collateral = margin - fee + funding + quantity * (mark - fill)
        maintenance = MAINTENANCE_FRACTION * abs(quantity) * mark
        if collateral <= maintenance:
            risks.append(
                {
                    "timestamp_ms": instant,
                    "kind": kind,
                    "collateral": collateral,
                    "maintenance": maintenance,
                    "entry_ms": entry,
                }
            )

    check(entry, float(row["entry_mark"]), 0.0, "entry_possible_liquidation")
    running = 0.0
    for frame in frames:
        if entry < frame.timestamp_ms <= exit_ms:
            rate = frame.funding_rates[BTC]
            assert rate is not None
            mark = frame.marks.get(BTC)
            require(mark is not None, "Held sampled mark is required.")
            assert mark is not None
            running -= quantity * mark * rate
            check(frame.timestamp_ms, mark, running, "sampled_possible_liquidation")
    selected = [frame for frame in funding_events if entry < frame.timestamp_ms <= exit_ms]
    pointer = 0
    before = 0.0
    for instant in range(entry + MINUTE, exit_ms + 1, MINUTE):
        require(instant in extremes, "Complete held-minute mark extremes are required.")
        low, high = extremes[instant]
        require(
            math.isfinite(low) and math.isfinite(high) and 0 < low <= high,
            "Finite positive ordered minute mark extremes.",
        )
        payments = []
        while pointer < len(selected) and selected[pointer].timestamp_ms <= instant:
            event = selected[pointer]
            rate = event.funding_rates[BTC]
            assert rate is not None
            payments.append(-quantity * event.marks[BTC] * rate)
            pointer += 1
        check(
            instant,
            low if quantity > 0 else high,
            before + math.fsum(min(value, 0.0) for value in payments),
            "intraminute_possible_liquidation",
        )
        before += math.fsum(payments)
    require(pointer == len(selected), "Every held funding event is included in the minute screen.")
    if margin + float(row["net_pnl"]) <= 0:
        risks.append(
            {
                "timestamp_ms": exit_ms,
                "kind": "close_collateral_exhausted",
                "collateral": margin + float(row["net_pnl"]),
                "maintenance": 0.0,
                "entry_ms": entry,
            }
        )
    return risks


def _summary(
    spec: ReplaySpec,
    frames: Sequence[Frame],
    result: ReplayResult,
    extremes: Mapping[int, tuple[float, float]],
) -> dict[str, Any]:
    require(len(result.trades) % 2 == 0, "Complete fixed-quantity entry/exit pairs.")
    prefix, events = _funding(frames)
    clock = tuple(frame.timestamp_ms for frame in frames)
    points = {point.timestamp_ms: point for point in result.points}
    rows: list[dict[str, Any]] = []
    risk_events = []
    for index in range(0, len(result.trades), 2):
        entry, exit_trade = result.trades[index : index + 2]
        quantity = entry.quantity_delta
        require(
            exit_trade.quantity_delta == -quantity and exit_trade.timestamp_ms > entry.timestamp_ms,
            "Each episode holds fixed units until a separate complete close.",
        )
        margin = abs(quantity * entry.mark) / ISOLATED_LEVERAGE
        require(margin > 0, "Positive immutable entry-mark margin.")
        payment = -quantity * (prefix[exit_trade.timestamp_ms] - prefix[entry.timestamp_ms])
        pnl = quantity * (exit_trade.fill_price - entry.fill_price) - entry.fee - exit_trade.fee
        pnl += payment
        roi = pnl / margin
        row: dict[str, Any] = {
            "entry_ms": entry.timestamp_ms,
            "exit_ms": exit_trade.timestamp_ms,
            "side": 1 if quantity > 0 else -1,
            "quantity": quantity,
            "initial_margin": margin,
            "margin_basis": "absolute_quantity_times_entry_mark_divided_by_two",
            "isolated_leverage": ISOLATED_LEVERAGE,
            "entry_mark": entry.mark,
            "entry_fill_price": entry.fill_price,
            "exit_fill_price": exit_trade.fill_price,
            "entry_reference_price": entry.reference_price,
            "exit_reference_price": exit_trade.reference_price,
            "entry_fee": entry.fee,
            "exit_fee": exit_trade.fee,
            "fee_cost": entry.fee + exit_trade.fee,
            "slippage_cost": entry.slippage_cost + exit_trade.slippage_cost,
            "funding": payment,
            "net_pnl": pnl,
            "net_margin_roi": roi,
            "target_met": roi >= 0.20,
        }
        selected_frames = frames[
            bisect_right(clock, entry.timestamp_ms) : bisect_right(clock, exit_trade.timestamp_ms)
        ]
        risks = _risk(row, selected_frames, events, extremes)
        before = (
            points[entry.timestamp_ms].equity
            - quantity * (entry.mark - entry.fill_price)
            + entry.fee
        )
        row["entry_equity_before_execution"] = before
        if margin + entry.fee > before:
            risks.append(
                {
                    "timestamp_ms": entry.timestamp_ms,
                    "kind": "entry_margin_unavailable",
                    "required_margin_and_fee": margin + entry.fee,
                    "available_equity": before,
                    "entry_ms": entry.timestamp_ms,
                }
            )
        row["risk_invalid"] = bool(risks)
        rows.append(row)
        risk_events.extend(risks)
    net = result.final_equity - result.initial_equity
    require(
        abs(math.fsum(row["net_pnl"] for row in rows) - net)
        <= 1e-7 * max(1.0, result.initial_equity),
        "Closed episodes reconcile the complete fixed-quantity ledger.",
    )
    require(not result.points[-1].positions, "Terminal position is flat.")
    fees = math.fsum(trade.fee for trade in result.trades)
    friction = math.fsum(trade.slippage_cost for trade in result.trades)
    turnover = math.fsum(
        abs(trade.quantity_delta * (trade.reference_price or trade.mark)) for trade in result.trades
    )
    return {
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "fee_cost": fees,
        "slippage_cost": friction,
        "total_cost": result.total_cost,
        "total_funding": result.total_funding,
        "turnover_notional": turnover,
        "same_quantity_gross_pnl": net + fees + friction - result.total_funding,
        "complete_trades": len(rows),
        "long_net_pnl": math.fsum(row["net_pnl"] for row in rows if row["side"] > 0),
        "short_net_pnl": math.fsum(row["net_pnl"] for row in rows if row["side"] < 0),
        "target_attainment_count": sum(row["target_met"] for row in rows),
        "mean_net_margin_roi": math.fsum(row["net_margin_roi"] for row in rows) / len(rows)
        if rows
        else None,
        "risk_invalid": bool(risk_events),
        "maintenance_fraction_proxy": MAINTENANCE_FRACTION,
        "isolated_leverage": ISOLATED_LEVERAGE,
        "maximum_sampled_gross_exposure": max(
            (
                abs(point.positions.get(BTC, 0.0) * frame.marks.get(BTC, 0.0)) / point.equity
                for point, frame in zip(result.points, frames, strict=True)
            ),
            default=0.0,
        ),
        "risk_events": risk_events,
        "episodes": rows,
        "ledger": [asdict(point) for point in result.points],
        "trades": [asdict(trade) for trade in result.trades],
        "valuation_step_ms": spec.step_ms,
    }


def evaluate_case(
    spec: ReplaySpec,
    frames: Sequence[Frame],
    targets: Sequence[Target],
    minute_extremes: Mapping[int, tuple[float, float]],
) -> dict[str, Any]:
    indexed = _validate(spec, frames)
    for target in targets:
        require(target.replace_all, "Regime targets must completely replace the position.")
        _execution(indexed, target.execution_ms)
    if targets and any(targets[-1].weights.values()):
        # The generic ledger permits mark-only replays. This study explicitly
        # needs a native trade reference for its charged terminal close too.
        _execution(indexed, spec.end_ms)
    result = simulate(spec, frames, targets)
    return _summary(spec, frames, result, minute_extremes)


def evaluate(
    config: RegimeConfig,
    spec: ReplaySpec,
    frames: Sequence[Frame],
    hourly: Mapping[int, float],
    minute_extremes: Mapping[int, tuple[float, float]],
    *,
    stress_initial_equity: float | None = None,
) -> dict[str, Any]:
    schedule = build_schedule(config, spec, frames, hourly)
    instrument = spec.instruments[0]
    stress = replace(
        spec,
        initial_equity=spec.initial_equity
        if stress_initial_equity is None
        else stress_initial_equity,
        instruments=(
            replace(
                instrument,
                fee_bps=2 * instrument.fee_bps,
                slippage_bps=2 * instrument.slippage_bps,
            ),
        ),
    )
    return {
        "config": asdict(config),
        "schedule": [asdict(target) for target in schedule.targets],
        "decisions": [asdict(decision) for decision in schedule.decisions],
        "base": evaluate_case(spec, frames, schedule.targets, minute_extremes),
        "stress": evaluate_case(stress, frames, schedule.targets, minute_extremes),
    }
