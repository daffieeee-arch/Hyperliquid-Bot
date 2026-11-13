"""Four frozen hourly persistent-trend hypotheses; offline scheduling only."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from research import regime_momentum as original
from research.repaired_accounting import Frame, ReplaySpec, Target

HOUR = original.HOUR
MINUTE = original.MINUTE
BTC = original.BTC


@dataclass(frozen=True, slots=True)
class HourlyTrendConfig:
    name: str
    lookback_hours: int
    confirmed: bool

    def __post_init__(self) -> None:
        original.require(type(self.name) is str and 0 < len(self.name) <= 80, "Configuration name.")
        original.require(
            type(self.lookback_hours) is int and self.lookback_hours in (24, 72),
            "Exactly 24 or 72 completed hours.",
        )
        original.require(type(self.confirmed) is bool, "Exact confirmation flag.")


def configs() -> tuple[HourlyTrendConfig, ...]:
    return tuple(
        HourlyTrendConfig(
            f"hourly-h{hours}-{'confirmed' if confirmed else 'plain'}", hours, confirmed
        )
        for hours in (24, 72)
        for confirmed in (False, True)
    )


def features(
    config: HourlyTrendConfig, hourly: Mapping[int, float], instant: int
) -> tuple[float | None, float | None, int | None]:
    # The unchanged helper owns all 721-close validation, sigma and slow confirmation.
    _, sigma, confirmation = original.features(original.configs()[0], hourly, instant)
    if sigma is None:
        return None, None, None
    prices = [hourly[instant - offset * HOUR] for offset in range(config.lookback_hours, -1, -1)]
    returns = [math.log(b / a) for a, b in pairwise(prices)]
    denominator = math.sqrt(math.fsum(value * value for value in returns))
    return math.fsum(returns) / denominator if denominator else None, sigma, confirmation


def build_schedule(
    config: HourlyTrendConfig,
    spec: ReplaySpec,
    frames: Sequence[Frame],
    hourly: Mapping[int, float],
) -> original.RegimeSchedule:
    indexed = original._validate(spec, frames)
    targets: list[Target] = []
    decisions: list[original.RegimeDecision] = []
    side = 0
    for instant in range(spec.start_ms, spec.end_ms, HOUR):
        score, sigma, confirmation = features(config, hourly, instant)
        previous, desired, weight = side, 0, None
        if score is not None and sigma is not None and sigma > 0:
            direction = 1 if score > 0 else -1 if score < 0 else 0
            if direction and (not config.confirmed or confirmation == direction):
                if direction == previous and abs(score) >= 0.5:
                    desired = direction
                elif abs(score) >= 1:
                    desired = direction
            if desired and desired != previous:
                weight = min(original.ENTRY_CAP, original.RISK_TARGET / sigma)
        action = "hold" if desired else "flat"
        if previous != desired:
            if previous:
                original._execution(indexed, instant + MINUTE)
                targets.append(Target(instant, instant + MINUTE, {}))
                action = "close"
            if desired:
                execution = instant + (2 if previous else 1) * MINUTE
                original.require(execution < spec.end_ms, "Entry precedes terminal close.")
                original._execution(indexed, execution)
                assert weight is not None
                targets.append(Target(instant, execution, {BTC: desired * weight}))
                action = "reverse" if previous else "enter"
        decisions.append(
            original.RegimeDecision(
                instant, score, sigma, confirmation, previous, desired, weight, action
            )
        )
        side = desired
    return original.RegimeSchedule(tuple(targets), tuple(decisions))
