"""Shape-only statistics of the S4 panel, for the placeholders of the S4 drafts.

PAPER research only. It reads no value of any price, volume, return, funding
or signal column: only ``ts``, ``symbol``, ``traded``, ``volume_rank`` and
``funding_covered``, and whether ``funding_rate``, ``ret_14d``,
``funding_7d`` and ``carry_7d`` are present (``IS NULL``). Every number it
prints is a count of rows, days or symbols.

    PYTHONPATH=src uv run --frozen python shape_only.py "$STUDY_DIR/panel.parquet" \
        config/research/binance-um-usdt-non-crypto-2026-10-08.json > shape.json

The split is the drafts': 371 days before the first fold, 16 folds of 91
days, a 608-day holdout, on a 2,435-day axis (2020-01-01 to 2026-08-31, the
panel built with ``--end 2026-09-01``).
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import duckdb

AXIS_DAYS = 2435
MAX_ROWS = 2_000_000  # Both existing specs' data.max_rows, never a truncation allowance.
MAX_SYMBOL_LENGTH = 128
FIRST_TS = 1577923199999
LAST_TS = 1788220799999
DAY_MS = 86_400_000
TRAIN, TEST, FOLDS, HOLDOUT = 371, 91, 16, 608
UNIVERSE = 50
LATENCY, SHORTEST, LONGEST = 1, 7, 14
HORIZONS = (7, 14)
# Fewest eligible names that still give a leg at the floor: momentum floor 5
# (q 0.2 needs 25, q 0.3 needs 17); carry floor 4 (q 0.1 needs 40, q 0.2 20).
NEEDED = {"momentum_q20": 25, "momentum_q30": 17, "carry_q10": 40, "carry_q20": 20}
# Row fields, in order: traded, rank, covered, no rate, no ret_14d, no carry_7d, no funding_7d.
Cell = tuple[bool, int | None, bool, bool, bool, bool, bool]


def windows() -> list[tuple[str, int, int]]:
    folds = [(f"fold{k:02d}", TRAIN + TEST * k, TRAIN + TEST * (k + 1)) for k in range(FOLDS)]
    return [*folds, ("holdout", AXIS_DAYS - HOLDOUT, AXIS_DAYS)]


def load(panel: Path) -> tuple[list[int], dict[str, dict[int, Cell]]]:
    connection = duckdb.connect()
    try:
        projection = (
            "SELECT ts, symbol, traded, volume_rank, funding_covered, funding_rate IS NULL, "
            "ret_14d IS NULL, carry_7d IS NULL, funding_7d IS NULL FROM read_parquet(?)"
        )
        schema = connection.execute("DESCRIBE " + projection, [str(panel)]).fetchall()
        if tuple(column[1] for column in schema) != (
            "BIGINT",
            "VARCHAR",
            "BOOLEAN",
            "BIGINT",
            "BOOLEAN",
            "BOOLEAN",
            "BOOLEAN",
            "BOOLEAN",
            "BOOLEAN",
        ):
            raise ValueError("S4 shape refuses incompatible projected metadata types.")
        rows = connection.execute(
            projection + f" ORDER BY ts, symbol LIMIT {MAX_ROWS + 1}", [str(panel)]
        ).fetchall()
    finally:
        connection.close()
    if len(rows) > MAX_ROWS:
        raise ValueError("S4 shape exceeds the preregistered row bound; no counts are published.")
    # These counts gate a fixed preregistration. Coercion or duplicate collapse
    # could turn malformed metadata into plausible, but incorrect, gate counts.
    seen: set[tuple[int, str]] = set()
    for row in rows:
        ts, symbol, traded, rank, covered, *presence = row
        if (
            type(ts) is not int
            or not isinstance(symbol, str)
            or not symbol
            or symbol != symbol.strip()
            or len(symbol) > MAX_SYMBOL_LENGTH
        ):
            raise ValueError("S4 shape requires integer timestamps and nonempty string symbols.")
        if type(traded) is not bool or type(covered) is not bool:
            raise ValueError("S4 shape requires boolean traded and funding_covered metadata.")
        if rank is not None and (type(rank) is not int or rank <= 0):
            raise ValueError("S4 shape requires positive integer ranks or NULL.")
        if any(type(value) is not bool for value in presence):
            raise ValueError("S4 shape requires boolean presence metadata.")
        key = (ts, symbol)
        if key in seen:
            raise ValueError("S4 shape refuses duplicate timestamp/symbol rows.")
        seen.add(key)
    days = sorted({row[0] for row in rows})
    if not days or not axis(days)["as_expected"]:
        raise ValueError("S4 shape requires the preregistered complete daily timestamp axis.")
    index = {ts: position for position, ts in enumerate(days)}
    by_symbol: dict[str, dict[int, Cell]] = defaultdict(dict)
    for ts, symbol, traded, rank, covered, no_rate, no_mom, no_carry, no_mean in rows:
        by_symbol[symbol][index[ts]] = (
            traded,
            rank,
            covered,
            no_rate,
            no_mom,
            no_carry,
            no_mean,
        )
    return days, by_symbol


def axis(days: list[int]) -> dict[str, object]:
    steps = {later - earlier for earlier, later in pairwise(days)}
    return {
        "days": len(days),
        "timestamp_min": days[0],
        "timestamp_max": days[-1],
        "steps_ms": sorted(steps),
        "as_expected": len(days) == AXIS_DAYS
        and days[0] == FIRST_TS
        and days[-1] == LAST_TS
        and steps == {DAY_MS},
    }


def depth(by_symbol: dict[str, dict[int, Cell]]) -> dict[str, object]:
    """Eligible names per decision day, per window: counts only."""

    per_day: dict[str, list[int]] = {
        key: [0] * AXIS_DAYS for key in ("ranked", "momentum", "carry", "ranked_any")
    }
    for rows in by_symbol.values():
        for day, (traded, rank, _covered, _no_rate, no_mom, no_carry, _m) in rows.items():
            if rank is not None:
                per_day["ranked_any"][day] += 1
            if not traded or rank is None or rank > UNIVERSE:
                continue
            per_day["ranked"][day] += 1
            per_day["momentum"][day] += not no_mom
            per_day["carry"][day] += not no_carry
    out: dict[str, object] = {}
    for name, start, end in windows():
        block: dict[str, object] = {}
        for key, counts in per_day.items():
            values = counts[start:end]
            block[key] = {
                "min": min(values),
                "median": statistics.median(values),
                "max": max(values),
            }
        block["days_below"] = {
            label: sum(
                1
                for day in range(start, end)
                if per_day["momentum" if label.startswith("momentum") else "carry"][day] < needed
            )
            for label, needed in NEEDED.items()
        }
        out[name] = block
    return out


def reach(by_symbol: dict[str, dict[int, Cell]], days: list[int]) -> dict[str, object]:
    """What a position opened on a ranked day could meet, whatever the signal.

    For every symbol ranked at most 50 on a scored decision day ``d``: whether
    it can fill at ``d + 1``, and over the days ``d + 2`` through ``d + 15``
    (cut at the window's end and at the first day without a row) how many are
    halts, traded days without a funding rate (the harness fails closed on
    these), halt days without a rate, and uncovered days. Per horizon, how
    many such positions would exit forced: a day without a row before the
    exit day, or no trade on it.

    Every config's legs are drawn from these (symbol, decision day) pairs, so
    each count bounds the same count of any config, leg or window: an
    absolute bound, not a rate. A leg is picked by its signal and can fail
    to fill, or exit forced, more often than the ranked names on average.
    """

    keys = (
        "ranked_decisions",
        "cannot_fill",
        "contract_ended",
        "held_days",
        "halt_days",
        "halt_days_without_rate",
        "traded_days_without_rate",
        "uncovered_days",
    )
    totals: dict[str, int] = dict.fromkeys(keys, 0)
    by_horizon = {f"h{h}": {"positions": 0, "forced_exits": 0} for h in HORIZONS}
    holes: dict[str, list[int]] = defaultdict(list)
    for symbol, rows in by_symbol.items():
        for _name, start, end in windows():
            for day in range(start, end):
                row = rows.get(day)
                if row is None or not row[0] or row[1] is None or row[1] > UNIVERSE:
                    continue
                if day + LATENCY + SHORTEST >= end:
                    continue  # no period of the grid fits: never decided
                totals["ranked_decisions"] += 1
                entry = rows.get(day + LATENCY)
                if entry is None or not entry[0]:
                    totals["cannot_fill"] += 1
                    continue
                for horizon in HORIZONS:
                    exit_day = day + LATENCY + horizon
                    if exit_day >= end:
                        continue
                    block = by_horizon[f"h{horizon}"]
                    block["positions"] += 1
                    span = range(day + LATENCY + 1, exit_day + 1)
                    if any(rows.get(held) is None for held in span) or not rows[exit_day][0]:
                        block["forced_exits"] += 1
                for held in range(day + LATENCY + 1, min(day + LATENCY + LONGEST, end - 1) + 1):
                    cell = rows.get(held)
                    if cell is None:
                        totals["contract_ended"] += 1
                        break
                    traded, _rank, covered, no_rate, _m, _c, _f = cell
                    totals["held_days"] += 1
                    if not traded:
                        totals["halt_days"] += 1
                        totals["halt_days_without_rate"] += no_rate
                    elif no_rate:
                        totals["traded_days_without_rate"] += 1
                        holes[symbol].append(held)
                    elif not covered:
                        totals["uncovered_days"] += 1
    return {
        **totals,
        "by_horizon": by_horizon,
        "traded_without_rate_by_symbol": {
            symbol: {
                "days": len(set(held)),
                "first_ts": days[min(held)],
                "last_ts": days[max(held)],
            }
            for symbol, held in sorted(holes.items())
        },
    }


def unfunded_trading(by_symbol: dict[str, dict[int, Cell]], days: list[int]) -> dict[str, object]:
    """Traded rows without a funding rate, by symbol, and whether any is ranked."""

    out: dict[str, object] = {}
    for symbol, rows in sorted(by_symbol.items()):
        hits = [day for day, cell in rows.items() if cell[0] and cell[3]]
        if hits:
            out[symbol] = {
                "days": len(hits),
                "first_ts": days[min(hits)],
                "last_ts": days[max(hits)],
                "ranked_days": sum(1 for day in hits if rows[day][1] is not None),
            }
    return out


def ranked_by_year(by_symbol: dict[str, dict[int, Cell]], days: list[int]) -> dict[str, float]:
    """Median count of ranked (traded, crypto, funded) contracts a day, per calendar year."""

    counts = [0] * len(days)
    for rows in by_symbol.values():
        for day, cell in rows.items():
            counts[day] += cell[1] is not None
    years: dict[str, list[int]] = defaultdict(list)
    for day, ts in enumerate(days):
        years[datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y")].append(counts[day])
    return {year: statistics.median(values) for year, values in sorted(years.items())}


def uncovered_ranked(by_symbol: dict[str, dict[int, Cell]]) -> dict[str, object]:
    """Rows ranked at most 50 whose funding day is not whole, per window."""

    out: dict[str, object] = {}
    for name, start, end in windows():
        ranked = uncovered = 0
        for rows in by_symbol.values():
            for day in range(start, end):
                cell = rows.get(day)
                if cell is None or not cell[0] or cell[1] is None or cell[1] > UNIVERSE:
                    continue
                ranked += 1
                uncovered += not cell[2]
        out[name] = {"ranked_rows": ranked, "uncovered": uncovered}
    return out


def excluded(by_symbol: dict[str, dict[int, Cell]], exclusions: Path) -> dict[str, object]:
    listed = set(json.loads(exclusions.read_text(encoding="utf-8"))["symbols"])
    present = sorted(listed & set(by_symbol))
    ranked = sorted(
        s for s in present if any(cell[1] is not None for cell in by_symbol[s].values())
    )
    return {"listed": len(listed), "with_rows": len(present), "ever_ranked": ranked}


def carry_presence(by_symbol: dict[str, dict[int, Cell]]) -> dict[str, object]:
    """Per window, ranked rows (traded, rank at most 50) by presence of the funding means.

    ``without_carry``: rows with a rate that day but no ``carry_7d``, so a
    ranked name is left out of carry's universe (a day of the last seven
    that did not trade or has no rate, a funding dataset's first day that is
    not covered, or fewer than seven days since a listing or a gap);
    ``covered_mean_missing``: rows with a ``carry_7d`` but no ``funding_7d``,
    the ranked rows with an uncovered day in their carry window, which
    ``carry_7d`` takes at its recorded sum and the covered-day mean would
    have left out. Counts, and rows per symbol.
    """

    out: dict[str, object] = {}
    for name, start, end in windows():
        ranked = 0
        without: dict[str, int] = defaultdict(int)
        covered_missing: dict[str, int] = defaultdict(int)
        for symbol, rows in by_symbol.items():
            for day in range(start, end):
                cell = rows.get(day)
                if cell is None or not cell[0] or cell[1] is None or cell[1] > UNIVERSE:
                    continue
                ranked += 1
                if not cell[3] and cell[5]:
                    without[symbol] += 1
                if not cell[5] and cell[6]:
                    covered_missing[symbol] += 1
        out[name] = {
            "ranked_rows": ranked,
            "without_carry": sum(without.values()),
            "without_carry_by_symbol": dict(sorted(without.items())),
            "covered_mean_missing": sum(covered_missing.values()),
            "covered_mean_missing_by_symbol": dict(sorted(covered_missing.items())),
        }
    return out


def main(panel: Path, exclusions: Path) -> dict[str, object]:
    days, by_symbol = load(panel)
    return {
        "axis": axis(days),
        "symbols": len(by_symbol),
        "symbols_ever_ranked": sum(
            1 for rows in by_symbol.values() if any(cell[1] is not None for cell in rows.values())
        ),
        "excluded": excluded(by_symbol, exclusions),
        "depth": depth(by_symbol),
        "traded_without_rate": unfunded_trading(by_symbol, days),
        "reach": reach(by_symbol, days),
        "ranked_any_median_by_year": ranked_by_year(by_symbol, days),
        "uncovered_ranked": uncovered_ranked(by_symbol),
        "carry_presence": carry_presence(by_symbol),
    }


if __name__ == "__main__":
    json.dump(main(Path(sys.argv[1]), Path(sys.argv[2])), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
