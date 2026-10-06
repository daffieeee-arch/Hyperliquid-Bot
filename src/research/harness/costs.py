"""Flat bps cost model. Latency is applied by shifting the fill, not as bps."""

from __future__ import annotations

from typing import Final

from research.harness.spec import CostSpec

STRESS_MULTIPLIERS: Final[tuple[float, ...]] = (1.0, 1.5, 2.0)


def stress_key(multiplier: float) -> str:
    return f"{multiplier:.1f}"


def round_trip_cost(costs: CostSpec, stress: float) -> float:
    """Entry plus exit, in return units, after the stress multiplier.

    ``spread_bps`` is the half-spread per side, paid on entry and again on
    exit, not the full quoted spread. Each side pays fee + slippage + that
    half-spread. Stress scales the whole schedule. It does not scale latency.
    """

    per_side_bps = costs.fee_bps + costs.slippage_bps + costs.spread_bps
    return 2.0 * per_side_bps * stress / 10_000.0
