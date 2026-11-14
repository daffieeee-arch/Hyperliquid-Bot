"""CLAIRVOYANT_NONTRADABLE price-grid reference, never a causal strategy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    Inexact,
    localcontext,
)
from fractions import Fraction
from typing import Any

from research.clairvoyant_opportunity_io import LABEL, NativeDay
from research.two_models_io import MINUTE
from research.volume_repair_io import require

GRIDS = (1, 5, 15, 60)
COSTS = {
    "zero": (Fraction(0), Fraction(0)),
    "base": (Fraction(45, 100000), Fraction(2, 10000)),
    "stress": (Fraction(9, 10000), Fraction(4, 10000)),
}
FIELDS = ("net", "gross", "fees", "friction", "reference_turnover", "fill_turnover")


def rational(value: Fraction) -> list[int]:
    return [value.numerator, value.denominator]


@dataclass(frozen=True)
class Direction:
    quantity: Fraction
    entry_fill: Fraction
    exit_fill: Fraction
    net: Fraction
    gross: Fraction
    fees: Fraction
    friction: Fraction
    reference_turnover: Fraction
    fill_turnover: Fraction

    def evidence(self) -> list[list[int]]:
        return [rational(getattr(self, name)) for name in self.__dataclass_fields__]


def direction(p0: Fraction, p1: Fraction, side: int, cost: str) -> Direction:
    require(
        p0 > 0 and p1 > 0 and type(side) is int and side in (-1, 1) and cost in COSTS,
        "positive native prices and fixed exact scenario",
    )
    fee, adverse = COSTS[cost]
    q = Fraction(side) / p0
    entry = p0 * (1 + side * adverse)
    exit_price = p1 * (1 - side * adverse)
    gross = q * (p1 - p0)
    friction = abs(q) * adverse * (p0 + p1)
    fees = abs(q) * fee * (entry + exit_price)
    net = q * (exit_price - entry) - fees
    require(net == gross - friction - fees, "exact signed two-leg cost identity")
    return Direction(
        q,
        entry,
        exit_price,
        net,
        gross,
        fees,
        friction,
        abs(q) * (p0 + p1),
        abs(q) * (entry + exit_price),
    )


def choose(long: Direction, short: Direction) -> int:
    if max(long.net, short.net) <= 0:
        return 0
    return 1 if long.net >= short.net else -1


class Totals:
    def __init__(self) -> None:
        self.point = Context(prec=50, rounding=ROUND_HALF_EVEN)
        self.down = Context(prec=50, rounding=ROUND_FLOOR)
        self.up = Context(prec=50, rounding=ROUND_CEILING)
        self.sums = {key: Decimal(0) for key in FIELDS}
        self.lower = self.upper = Decimal(0)
        self.counts = {"long": 0, "short": 0, "flat": 0}

    @staticmethod
    def convert(value: Fraction, context: Context) -> Decimal:
        return context.divide(Decimal(value.numerator), Decimal(value.denominator))

    def add(self, value: Direction | None, side: int) -> None:
        self.counts["long" if side == 1 else "short" if side == -1 else "flat"] += 1
        if value is None:
            return
        for key in FIELDS:
            self.sums[key] = self.point.add(
                self.sums[key], self.convert(getattr(value, key), self.point)
            )
        self.lower = self.down.add(self.lower, self.convert(value.net, self.down))
        self.upper = self.up.add(self.upper, self.convert(value.net, self.up))


def half(value: Decimal, weight: Decimal) -> Decimal:
    require(weight in (Decimal(".5"), Decimal(1)), "two fixed reference fractions")
    with localcontext(Context(prec=60, rounding=ROUND_HALF_EVEN)) as context:
        context.clear_flags()
        result = value * weight
        require(not context.flags[Inexact], "dyadic scale must remain exact")
        return result


def threshold(lower: Decimal, upper: Decimal) -> tuple[str, bool | None]:
    require(lower <= upper, "ordered numerical bounds")
    if lower >= Decimal(".2"):
        return "PASS_PRICE_REFERENCE_THRESHOLD", True
    if upper < Decimal(".2"):
        return "BELOW_PRICE_REFERENCE_THRESHOLD", False
    return "NUMERIC_THRESHOLD_UNCERTAIN", None


def score_day(day: NativeDay) -> dict[str, Any]:
    prices = tuple(Fraction(p) for p in day.prices)
    cases = []
    digests = {}
    for grid in GRIDS:
        for cost in COSTS:
            total = Totals()
            digest = hashlib.sha256()
            for index in range(0, 1440, grid):
                long = direction(prices[index], prices[index + grid], 1, cost)
                short = direction(prices[index], prices[index + grid], -1, cost)
                selected = choose(long, short)
                total.add(long if selected == 1 else short if selected == -1 else None, selected)
                record = [
                    day.start_ms + index * MINUTE,
                    day.start_ms + (index + grid) * MINUTE,
                    long.evidence(),
                    short.evidence(),
                    selected,
                ]
                digest.update((json.dumps(record, separators=(",", ":")) + "\n").encode())
            interval_hash = digest.hexdigest()
            digests[f"{grid}-{cost}"] = interval_hash
            for weight in (Decimal(".5"), Decimal(1)):
                sums = {name: str(half(value, weight)) for name, value in total.sums.items()}
                lower, upper = half(total.lower, weight), half(total.upper, weight)
                verdict, hit = threshold(lower, upper)
                cases.append(
                    {
                        "label": LABEL,
                        "grid_minutes": grid,
                        "cost": cost,
                        "reference_notional_fraction": str(weight),
                        "reference_equity": "1",
                        "normalized_price_pnl": sums,
                        "net_lower": str(lower),
                        "net_upper": str(upper),
                        "threshold_status": verdict,
                        "threshold_hit": hit,
                        "counts": dict(total.counts),
                        "completed_roundtrips": total.counts["long"] + total.counts["short"],
                        "opportunities": 1440 // grid,
                        "unit_weight_interval_digest": interval_hash,
                        "paper_default_asset_strategy_cap_relaxed": weight == 1,
                    }
                )
    return {
        "label": LABEL,
        "start_ms": day.start_ms,
        "native_source_hash": day.source_hash(),
        "native_close_texts": day.prices,
        "cases": cases,
        "interval_digests": digests,
        "interval_evidence_order": list(Direction.__dataclass_fields__),
        "exact_interval_arithmetic": True,
        "aggregate_precision_digits": 50,
        "half_scale_exact": True,
        "funding_modeled": False,
        "tradable": False,
    }


def statistics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    require(bool(cases), "all-day statistics need actual cases")
    point = Context(prec=50, rounding=ROUND_HALF_EVEN)
    down = Context(prec=50, rounding=ROUND_FLOOR)
    up = Context(prec=50, rounding=ROUND_CEILING)
    values = sorted(Decimal(r["normalized_price_pnl"]["net"]) for r in cases)
    lows = sorted(Decimal(r["net_lower"]) for r in cases)
    highs = sorted(Decimal(r["net_upper"]) for r in cases)
    n = len(values)

    def mean(data: list[Decimal], context: Context) -> Decimal:
        total = Decimal(0)
        for value in data:
            total = context.add(total, value)
        return context.divide(total, Decimal(n))

    def median(data: list[Decimal], context: Context) -> Decimal:
        return (
            data[n // 2]
            if n % 2
            else context.divide(context.add(data[n // 2 - 1], data[n // 2]), Decimal(2))
        )

    quantiles = {}
    for percentage in (5, 25, 75, 95):
        rank = (percentage * n + 99) // 100 - 1
        quantiles[str(percentage)] = {
            "estimate": str(values[rank]),
            "lower": str(lows[rank]),
            "upper": str(highs[rank]),
        }
    passed = sum(r["threshold_hit"] is True for r in cases)
    uncertain = sum(r["threshold_hit"] is None for r in cases)
    return {
        "label": LABEL,
        "calendar_days": n,
        "threshold_day_count_range": [passed, passed + uncertain],
        "threshold_uncertain_days": uncertain,
        "threshold_day_fraction_range": [
            str(point.divide(Decimal(k), Decimal(n))) for k in (passed, passed + uncertain)
        ],
        "mean": {
            "estimate": str(mean(values, point)),
            "lower": str(mean(lows, down)),
            "upper": str(mean(highs, up)),
        },
        "median": {
            "estimate": str(median(values, point)),
            "lower": str(median(lows, down)),
            "upper": str(median(highs, up)),
        },
        "nearest_rank_quantiles_percent": quantiles,
        "min": {"estimate": str(values[0]), "lower": str(lows[0]), "upper": str(highs[0])},
        "max": {"estimate": str(values[-1]), "lower": str(lows[-1]), "upper": str(highs[-1])},
        "interpretation": "ROUNDED_DESCRIPTIVE_PRICE_ONLY_NOT_ACCOUNT_FORECAST",
    }
