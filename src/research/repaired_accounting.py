"""Deterministic fixed-quantity accounting for bounded offline research replays.

Funding maps certify complete interval coverage, including explicit zeroes. A
result measures sampled mark-to-market equity. Collateral, liquidation and
between-sample moves require separate evidence before economic admission.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from research.harness.errors import IntegrityError


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    kind: Literal["spot", "perp"]
    fee_bps: float
    slippage_bps: float


@dataclass(frozen=True, slots=True)
class Frame:
    timestamp_ms: int
    marks: dict[str, float]
    funding_rates: dict[str, float | None]
    execution_prices: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class Target:
    decision_ms: int
    execution_ms: int
    weights: dict[str, float]
    replace_all: bool = True


@dataclass(frozen=True, slots=True)
class ReplaySpec:
    start_ms: int
    end_ms: int
    step_ms: int
    initial_equity: float
    instruments: tuple[Instrument, ...]
    event_times_ms: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class LedgerPoint:
    timestamp_ms: int
    equity: float
    cash: float
    positions: dict[str, float]
    drawdown: float


@dataclass(frozen=True, slots=True)
class LedgerTrade:
    timestamp_ms: int
    symbol: str
    quantity_delta: float
    mark: float
    fill_price: float
    fee: float
    slippage_cost: float
    reference_price: float | None = None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    points: tuple[LedgerPoint, ...]
    trades: tuple[LedgerTrade, ...]
    initial_equity: float
    final_equity: float
    net_return: float
    max_drawdown: float
    total_funding: float
    total_cost: float


@dataclass(frozen=True, slots=True)
class ScaleSelection:
    scale: float
    result: ReplayResult
    evaluations: tuple[tuple[float, float], ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError("research_accounting", message)


def _number(value: float, name: str) -> None:
    _require(type(value) in (int, float), f"Invalid {name}.")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    _require(finite, f"Invalid {name}.")


def _clock(value: int, name: str) -> None:
    _require(type(value) is int and value >= 0, f"Invalid {name}.")


def _validate(
    spec: ReplaySpec, frames: Sequence[Frame], targets: Sequence[Target], scale: float
) -> dict[str, Instrument]:
    _clock(spec.start_ms, "start clock")
    _clock(spec.end_ms, "end clock")
    _clock(spec.step_ms, "step")
    _require(spec.step_ms > 0 and spec.end_ms > spec.start_ms, "Invalid replay interval.")
    span = spec.end_ms - spec.start_ms
    _require(span % spec.step_ms == 0, "The replay interval must contain whole steps.")
    _number(spec.initial_equity, "initial equity")
    _require(spec.initial_equity > 0, "Initial equity must be positive.")
    _number(scale, "scale")
    _require(scale >= 0, "Scale must be nonnegative.")
    instruments: dict[str, Instrument] = {}
    for instrument in spec.instruments:
        _require(
            isinstance(instrument.symbol, str)
            and bool(instrument.symbol.strip())
            and instrument.symbol not in instruments,
            "Instrument symbols must be nonempty and unique.",
        )
        _require(instrument.kind in ("spot", "perp"), "Unsupported instrument kind.")
        for name, value in (
            ("fee", instrument.fee_bps),
            ("slippage", instrument.slippage_bps),
        ):
            _number(value, name)
            _require(0 <= value < 10_000, f"Invalid {name} basis points.")
        instruments[instrument.symbol] = instrument
    _require(bool(instruments), "At least one instrument is required.")
    _require(len(frames) >= span // spec.step_ms + 1, "Incomplete replay clock grid.")
    regular = tuple(range(spec.start_ms, spec.end_ms + 1, spec.step_ms))
    clock = regular if spec.event_times_ms is None else spec.event_times_ms
    clock_set = set(clock)
    for instant in clock:
        _clock(instant, "event clock")
    _require(
        clock == tuple(sorted(clock_set))
        and set(regular) <= clock_set
        and clock[0] == spec.start_ms
        and clock[-1] == spec.end_ms,
        "The event clock must retain every regular valuation and both boundaries.",
    )
    _require(len(frames) == len(clock), "Incomplete replay clock grid.")
    for index, frame in enumerate(frames):
        _clock(frame.timestamp_ms, "frame clock")
        _require(
            frame.timestamp_ms == clock[index],
            "Frames must follow the complete ordered replay clock grid.",
        )
        for symbol, price in frame.marks.items():
            _require(symbol in instruments, "Unknown mark instrument.")
            _number(price, "mark price")
            _require(price > 0, "Mark prices must be positive.")
        if frame.execution_prices is not None:
            for symbol, price in frame.execution_prices.items():
                _require(symbol in instruments, "Unknown execution instrument.")
                _number(price, "execution reference price")
                _require(price > 0, "Execution reference prices must be positive.")
        for symbol, rate in frame.funding_rates.items():
            _require(
                symbol in instruments and instruments[symbol].kind == "perp",
                "Funding needs a declared perpetual instrument.",
            )
            if rate is not None:
                _number(rate, "funding rate")
    previous_execution = spec.start_ms - 1
    for target in targets:
        _require(type(target.replace_all) is bool, "Invalid target replacement flag.")
        _clock(target.decision_ms, "decision clock")
        _clock(target.execution_ms, "execution clock")
        _require(
            spec.start_ms <= target.decision_ms <= target.execution_ms < spec.end_ms,
            "A decision and execution must lie inside the replay window in causal order.",
        )
        _require(
            target.execution_ms > previous_execution and target.execution_ms in clock_set,
            "Executions must be unique, ordered and on the frame grid.",
        )
        previous_execution = target.execution_ms
        for symbol, weight in target.weights.items():
            _require(symbol in instruments, "Unknown target instrument.")
            _number(weight, "target weight")
            _require(
                instruments[symbol].kind == "perp" or weight >= 0,
                "Spot shorting requires a separate borrowing model.",
            )
    return instruments


def _equity(cash: float, positions: dict[str, float], marks: dict[str, float]) -> float:
    _require(all(symbol in marks for symbol in positions), "Missing mark for an open position.")
    equity = cash + math.fsum(quantity * marks[symbol] for symbol, quantity in positions.items())
    _number(equity, "marked equity")
    if equity <= 0:
        raise IntegrityError("research_insolvency", "Replay equity is insolvent.")
    return equity


def _rebalance(
    frame: Frame,
    positions: dict[str, float],
    cash: float,
    weights: dict[str, float],
    instruments: dict[str, Instrument],
    scale: float,
    replace_all: bool,
) -> tuple[float, dict[str, float], list[LedgerTrade]]:
    """Size all target units from the same equity before executing any trade."""
    equity = _equity(cash, positions, frame.marks)
    references = frame.marks if frame.execution_prices is None else frame.execution_prices
    desired: dict[str, float] = {} if replace_all else dict(positions)
    for symbol, weight in weights.items():
        if weight == 0 or scale == 0:
            desired.pop(symbol, None)
            continue
        _require(symbol in frame.marks, "Missing mark at target execution.")
        _require(symbol in references, "Missing execution reference price.")
        quantity = equity * scale * weight / references[symbol]
        _number(quantity, "target quantity")
        desired[symbol] = quantity
    trades: list[LedgerTrade] = []
    for symbol in sorted(set(positions) | set(desired)):
        delta = desired.get(symbol, 0.0) - positions.get(symbol, 0.0)
        if delta == 0:
            continue
        instrument = instruments[symbol]
        mark = frame.marks[symbol]
        _require(symbol in references, "Missing execution reference price.")
        reference = references[symbol]
        direction = 1 if delta > 0 else -1
        fill = reference * (1 + direction * instrument.slippage_bps / 10_000)
        fee = abs(delta * fill) * instrument.fee_bps / 10_000
        slippage = abs(delta) * abs(fill - reference)
        _number(fill, "fill price")
        _number(fee, "trade fee")
        cash -= delta * fill + fee
        _number(cash, "cash balance")
        trades.append(
            LedgerTrade(frame.timestamp_ms, symbol, delta, mark, fill, fee, slippage, reference)
        )
    return cash, desired, trades


def simulate(
    spec: ReplaySpec,
    frames: Sequence[Frame],
    targets: Sequence[Target],
    scale: float = 1.0,
) -> ReplayResult:
    """Replay fixed units between explicit executions and close at the final mark.

    Each perpetual funding value certifies the interval ending at its frame.
    Multiple settlements or changing marks inside that interval need finer
    frames, so each payment uses its own contemporaneous mark and held size.
    """
    instruments = _validate(spec, frames, targets, scale)
    schedule = {target.execution_ms: target for target in targets}
    cash = float(spec.initial_equity)
    positions: dict[str, float] = {}
    points: list[LedgerPoint] = []
    trades: list[LedgerTrade] = []
    total_funding = 0.0
    peak = float(spec.initial_equity)
    drawdown = 0.0
    for frame in frames:
        # Payment belongs to the units held before executions at this timestamp.
        for symbol, quantity in positions.items():
            if instruments[symbol].kind == "perp":
                rate = frame.funding_rates.get(symbol)
                _require(rate is not None, "Unknown funding coverage for a held perpetual.")
                assert rate is not None
                _require(symbol in frame.marks, "Missing funding settlement mark.")
                payment = -quantity * frame.marks[symbol] * rate
                _number(payment, "funding cash flow")
                cash += payment
                total_funding += payment
        before = _equity(cash, positions, frame.marks)
        peak = max(peak, before)
        drawdown = max(drawdown, 1 - before / peak)
        target = schedule.get(frame.timestamp_ms)
        if frame.timestamp_ms == spec.end_ms or target is not None:
            weights = target.weights if target is not None else {}
            replace_all = target.replace_all if target is not None else True
            cash, positions, executions = _rebalance(
                frame, positions, cash, weights, instruments, scale, replace_all
            )
            trades.extend(executions)
        equity = _equity(cash, positions, frame.marks)
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        points.append(
            LedgerPoint(frame.timestamp_ms, equity, cash, dict(positions), 1 - equity / peak)
        )
    final_equity = points[-1].equity
    return ReplayResult(
        tuple(points),
        tuple(trades),
        spec.initial_equity,
        final_equity,
        final_equity / spec.initial_equity - 1,
        drawdown,
        total_funding,
        math.fsum(trade.fee + trade.slippage_cost for trade in trades),
    )


def select_scale(
    spec: ReplaySpec,
    frames: Sequence[Frame],
    targets: Sequence[Target],
    candidates: Sequence[float],
    max_drawdown: float,
) -> ScaleSelection:
    """Choose the largest predeclared scale that passes a complete historical replay.

    This calibrates a development-period scenario. A passing historical loss
    bound cannot guarantee a future bound or replace collateral/liquidation tests.
    """
    _number(max_drawdown, "drawdown limit")
    _require(0 <= max_drawdown < 1, "Drawdown limit must be in [0, 1).")
    _require(0 < len(candidates) <= 32, "Between one and 32 scale candidates are required.")
    seen: set[float] = set()
    for candidate in candidates:
        _number(candidate, "scale candidate")
        _require(candidate >= 0 and candidate not in seen, "Invalid or duplicate scale candidate.")
        seen.add(candidate)
    evaluations: list[tuple[float, float]] = []
    selected: tuple[float, ReplayResult] | None = None
    for candidate in sorted(seen):
        try:
            result = simulate(spec, frames, targets, candidate)
        except IntegrityError as error:
            if error.failure_kind != "research_insolvency":
                raise
            evaluations.append((candidate, 1.0))
            continue
        evaluations.append((candidate, result.max_drawdown))
        if result.max_drawdown <= max_drawdown:
            selected = candidate, result
    _require(selected is not None, "No declared scale satisfies the historical drawdown limit.")
    assert selected is not None
    return ScaleSelection(selected[0], selected[1], tuple(evaluations))
