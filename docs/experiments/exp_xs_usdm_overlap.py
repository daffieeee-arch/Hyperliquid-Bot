"""How the S4 carry and momentum legs look on validation days: overlap, liquidity and ties.

PAPER research only. A descriptive diagnostic for exp_xsmom_usdm and
exp_xscarry_usdm: it enters no decision rule. It reads the panel's signal
columns and ``volume_rank`` on the validation days of the pre-registered
split and never a holdout day, a close, or a forward return, so it can run
whatever the harness labels.

    PYTHONPATH=src uv run --frozen python docs/experiments/exp_xs_usdm_overlap.py \\
        "$STUDY_DIR/panel.parquet" > "$STUDY_DIR/overlap.json"

On each validation day each family's universe is the names that traded,
rank at most 50 and have its signal (``ret_14d`` or ``carry_7d``), ordered
the way the harness orders a leg (signal descending, ties by symbol). It
reports:

- ``overlap``: on the names with both signals, the Spearman rank correlation
  of the two signals and how many of momentum's top and bottom 20% are also
  in carry's top or bottom 20%;
- ``leg_volume_rank``: per family, quantile and leg, the mean
  ``volume_rank`` of the leg's names against the mean of the family's
  universe (25.5 for a full 50). The flat 10 bp a side assumes legs spread
  evenly over ranks 1 to 50; legs that sit mostly in ranks 31 to 50 make the
  2.0x stress the realistic cost;
- ``carry_ties``: per carry quantile and leg, how often the leg's boundary
  falls inside a block of tied ``carry_7d`` values (the same 7-day funding
  sum in the panel's steps of 1e-9, told apart only by its hashed draw),
  how many leg slots such blocks fill, and how many of those slots, and of
  all universe names, start with a digit (the "1000x" contracts, which an
  alphabetical tie-break would favour).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import duckdb

AXIS_DAYS = 2435
VALIDATION = (371, 1827)  # train_bars, then 16 folds of 91 days
UNIVERSE_SIZE = 50
MIN_NAMES = 10
QUANTILES = {"momentum": ((1, 5), (3, 10)), "carry": ((1, 10), (1, 5))}
# The panel (#145) writes carry_7d as (draw / 10 - steps) / (7 * 1e9), with
# steps the 7-day funding sum counted in steps of 1e-9 and draw in [0, 1)
# from the sha256 of symbol and day. Values with equal steps differ by less
# than a tenth of STEP, values with different steps by more than nine
# tenths, so half a step separates them.
FUNDING_WINDOW = 7
STEP = 1.0 / (FUNDING_WINDOW * 1e9)
TIE = STEP / 2

Name = tuple[str, float, int]
ByDay = dict[int, dict[str, list[Name]]]


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for position in range(start, end + 1):
            ranks[order[position]] = (start + end) / 2.0 + 1.0
        start = end + 1
    return ranks


def _spearman(left: list[float], right: list[float]) -> float | None:
    a, b = _average_ranks(left), _average_ranks(right)
    mean_a, mean_b = math.fsum(a) / len(a), math.fsum(b) / len(b)
    cov = math.fsum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    var_a = math.fsum((x - mean_a) ** 2 for x in a)
    var_b = math.fsum((y - mean_b) ** 2 for y in b)
    if var_a == 0.0 or var_b == 0.0:
        return None
    return cov / math.sqrt(var_a * var_b)


def _ordered(names: list[Name]) -> list[Name]:
    return sorted(names, key=lambda item: (-item[1], item[0]))


def _size(count: int, quantile: tuple[int, int]) -> int:
    return count * quantile[0] // quantile[1]


def _legs(names: list[Name], quantile: tuple[int, int]) -> tuple[set[str], set[str]]:
    ordered = _ordered(names)
    size = _size(len(ordered), quantile)
    return {item[0] for item in ordered[:size]}, {item[0] for item in ordered[-size:]}


def _digit(symbol: str) -> bool:
    return symbol[:1].isdigit()


def main(panel: Path) -> dict[str, object]:
    connection = duckdb.connect()
    try:
        days = [
            int(row[0])
            for row in connection.execute(
                "SELECT DISTINCT ts FROM read_parquet(?) ORDER BY ts", [str(panel)]
            ).fetchall()
        ]
        if len(days) != AXIS_DAYS:
            raise SystemExit(f"expected {AXIS_DAYS} days on the axis, found {len(days)}")
        first, end = days[VALIDATION[0]], days[VALIDATION[1]]
        rows = connection.execute(
            "SELECT ts, symbol, ret_14d, carry_7d, volume_rank FROM read_parquet(?) "
            "WHERE ts >= ? AND ts < ? AND traded AND volume_rank <= ? ORDER BY ts, symbol",
            [str(panel), first, end, UNIVERSE_SIZE],
        ).fetchall()
    finally:
        connection.close()
    by_day: ByDay = {}
    for ts, symbol, momentum, carry, rank in rows:
        day = by_day.setdefault(int(ts), {"momentum": [], "carry": []})
        if momentum is not None:
            day["momentum"].append((str(symbol), float(momentum), int(rank)))
        if carry is not None:
            day["carry"].append((str(symbol), float(carry), int(rank)))
    return {
        "validation_days": VALIDATION[1] - VALIDATION[0],
        "overlap": _overlap(days, by_day),
        "leg_volume_rank": _leg_volume_rank(days, by_day),
        "carry_ties": _carry_ties(days, by_day),
    }


def _overlap(days: list[int], by_day: ByDay) -> dict[str, object]:
    correlations: list[float] = []
    overlaps = {
        "mom_long_in_carry_long": 0,
        "mom_long_in_carry_short": 0,
        "mom_short_in_carry_long": 0,
        "mom_short_in_carry_short": 0,
    }
    slots = 0
    for day in days[VALIDATION[0] : VALIDATION[1]]:
        signals = by_day.get(day, {"momentum": [], "carry": []})
        carry = {symbol: (value, rank) for symbol, value, rank in signals["carry"]}
        both = [(s, m, carry[s][0], r) for s, m, r in signals["momentum"] if s in carry]
        if len(both) < MIN_NAMES:
            continue
        rho = _spearman([m for _, m, _, _ in both], [c for _, _, c, _ in both])
        if rho is None:
            continue
        correlations.append(rho)
        mom_long, mom_short = _legs([(s, m, r) for s, m, _, r in both], (1, 5))
        carry_long, carry_short = _legs([(s, c, r) for s, _, c, r in both], (1, 5))
        slots += len(mom_long)
        overlaps["mom_long_in_carry_long"] += len(mom_long & carry_long)
        overlaps["mom_long_in_carry_short"] += len(mom_long & carry_short)
        overlaps["mom_short_in_carry_long"] += len(mom_short & carry_long)
        overlaps["mom_short_in_carry_short"] += len(mom_short & carry_short)
    ordered = sorted(correlations)
    return {
        "days_used": len(ordered),
        "spearman_mean": None if not ordered else math.fsum(ordered) / len(ordered),
        "spearman_median": None if not ordered else ordered[len(ordered) // 2],
        "share_days_negative": None if not ordered else sum(r < 0 for r in ordered) / len(ordered),
        "leg_share": {
            key: (None if slots == 0 else value / slots) for key, value in overlaps.items()
        },
    }


def _leg_volume_rank(days: list[int], by_day: ByDay) -> dict[str, object]:
    out: dict[str, object] = {}
    for family, quantiles in QUANTILES.items():
        universe_ranks: list[int] = []
        legs: dict[str, dict[str, list[int]]] = {
            f"q{100 * n // d}": {"long": [], "short": []} for n, d in quantiles
        }
        for day in days[VALIDATION[0] : VALIDATION[1]]:
            names = by_day.get(day, {}).get(family, [])
            if len(names) < MIN_NAMES:
                continue
            universe_ranks.extend(rank for _, _, rank in names)
            ordered = _ordered(names)
            for n, d in quantiles:
                size = _size(len(ordered), (n, d))
                if size == 0:
                    continue
                legs[f"q{100 * n // d}"]["long"].extend(r for _, _, r in ordered[:size])
                legs[f"q{100 * n // d}"]["short"].extend(r for _, _, r in ordered[-size:])
        out[family] = {
            "universe_mean_rank": None
            if not universe_ranks
            else math.fsum(universe_ranks) / len(universe_ranks),
            **{
                label: {
                    side: None if not ranks else math.fsum(ranks) / len(ranks)
                    for side, ranks in sides.items()
                }
                | {
                    f"{side}_share_rank_31_50": None
                    if not ranks
                    else sum(rank > 30 for rank in ranks) / len(ranks)
                    for side, ranks in sides.items()
                }
                for label, sides in legs.items()
            },
        }
    return out


def _carry_ties(days: list[int], by_day: ByDay) -> dict[str, object]:
    out: dict[str, object] = {}
    universe_names = 0
    universe_digit = 0
    for day in days[VALIDATION[0] : VALIDATION[1]]:
        names = by_day.get(day, {}).get("carry", [])
        universe_names += len(names)
        universe_digit += sum(_digit(symbol) for symbol, _, _ in names)
    for n, d in QUANTILES["carry"]:
        counts = {
            side: {"boundaries": 0, "inside_tie": 0, "slots": 0, "tie_slots": 0, "tie_digit": 0}
            for side in ("long", "short")
        }
        for day in days[VALIDATION[0] : VALIDATION[1]]:
            ordered = _ordered(by_day.get(day, {}).get("carry", []))
            size = _size(len(ordered), (n, d))
            if size < 4 or 2 * size > len(ordered):
                continue
            for side, sequence in (("long", ordered), ("short", ordered[::-1])):
                leg, outside = sequence[:size], sequence[size]
                edge = leg[-1][1]
                block = counts[side]
                block["boundaries"] += 1
                block["slots"] += size
                if abs(outside[1] - edge) < TIE:
                    block["inside_tie"] += 1
                    tied = [symbol for symbol, value, _ in leg if abs(value - edge) < TIE]
                    block["tie_slots"] += len(tied)
                    block["tie_digit"] += sum(_digit(symbol) for symbol in tied)
        out[f"q{100 * n // d}"] = counts
    out["universe_digit_share"] = None if not universe_names else universe_digit / universe_names
    return out


if __name__ == "__main__":
    json.dump(main(Path(sys.argv[1])), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
