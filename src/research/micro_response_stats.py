"""Descriptive UTC-day clusters, never independent-event significance or selection."""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter
from datetime import date, timedelta
from typing import Any

from research.micro_response import COSTS, HORIZONS, POLICIES
from research.volume_repair_io import require

METRICS = ("gross_mid_bps", "charged_price_bps", "vs_long_bps", "vs_short_bps", "vs_momentum_bps")


class Summary:
    def __init__(self) -> None:
        self.cells: dict[tuple[str, str, int, str], dict[str, Any]] = {}

    def cell(self, group: str, cost: str, horizon: int, policy: str) -> dict[str, Any]:
        key = (group, cost, horizon, policy)
        if key not in self.cells:
            self.cells[key] = {"counts": Counter(), "days": {}, "captures": set()}
        return self.cells[key]

    def consume(self, row: dict[str, Any]) -> None:
        for group in (row["group"], "all_exposed"):
            for policy, _, _ in POLICIES:
                direction = row["directions"][policy]
                costs = COSTS if row["kind"] == "decision" else (row["cost"],)
                horizons = HORIZONS if row["kind"] != "label" else (row["horizon_seconds"],)
                for cost in costs:
                    for horizon in horizons:
                        cell = self.cell(group, cost, horizon, policy)
                        counts = cell["counts"]
                        if row["kind"] == "decision":
                            counts["scheduled"] += 1
                            counts[row["reasons"][policy]] += 1
                        elif direction:
                            counts[f"{row['kind']}_{row['status']}"] += 1
                            if row["kind"] == "label" and row["status"] == "observed":
                                self.sample(cell, row, direction)

    def sample(self, cell: dict[str, Any], row: dict[str, Any], side: int) -> None:
        # Actual receipt date anchors clustering; admission uses native time,
        # physical delays use monotonic time, and none is a wire-latency proof.
        day = str(date(1970, 1, 1) + timedelta(days=row["entry"]["utc_ns"] // 86_400_000_000_000))
        cell["captures"].add(row["capture"])
        values = cell["days"].setdefault(day, {metric: [0.0, 0] for metric in METRICS})
        long, short = row["charged_long_price_bps"], row["charged_short_price_bps"]
        selected = long if side == 1 else short
        metrics = {
            "gross_mid_bps": side * row["gross_mid_bps"],
            "charged_price_bps": selected,
            "vs_long_bps": selected - long,
            "vs_short_bps": selected - short,
        }
        momentum = row["momentum_direction"]
        if momentum:
            metrics["vs_momentum_bps"] = selected - (long if momentum == 1 else short)
        else:
            cell["counts"]["momentum_unavailable"] += 1
        cell["counts"]["funding_intersection"] += int(row["funding_event_within_padded_interval"])
        for metric, value in metrics.items():
            require(math.isfinite(value), "finite descriptive opportunity statistic")
            values[metric][0] += value
            values[metric][1] += 1

    def result(self) -> list[dict[str, Any]]:
        rows = []
        for (group, cost, horizon, policy), cell in sorted(self.cells.items()):
            count = cell["counts"]
            require(
                count["scheduled"]
                == sum(
                    count[k]
                    for k in ("stale_quote", "warmup", "calendar", "no_signal", "long", "short")
                ),
                "complete scheduled decision denominator",
            )
            signals = count["long"] + count["short"]
            require(
                signals
                == sum(
                    count[k]
                    for k in ("entry_observed", "entry_boundary_purged", "entry_unavailable_quote")
                ),
                "every signal has a recorded delayed-entry outcome",
            )
            require(
                count["entry_observed"]
                == sum(
                    count[k]
                    for k in ("label_observed", "label_boundary_purged", "label_unavailable_quote")
                ),
                "every observed entry has a recorded future-label outcome",
            )
            days = sorted(cell["days"])
            metrics = {}
            draws: dict[str, list[float]] = {metric: [] for metric in METRICS}
            rng = random.Random(20261113)
            vectors = {metric: [cell["days"][day][metric] for day in days] for metric in METRICS}
            valid = {
                metric: len(days) >= 5 and all(n for _, n in vector)
                for metric, vector in vectors.items()
            }
            if len(days) >= 5:
                for _ in range(2000):
                    indices = [rng.randrange(len(days)) for _ in days]
                    for metric, vector in vectors.items():
                        if valid[metric]:
                            draws[metric].append(
                                statistics.mean(vector[i][0] / vector[i][1] for i in indices)
                            )
            for metric, vector in vectors.items():
                total = sum(n for _, n in vector)
                daily = [value / n for value, n in vector if n]
                ordered = sorted(draws[metric])
                metrics[metric] = {
                    "observations": total,
                    "event_weighted_mean": sum(v for v, _ in vector) / total if total else None,
                    "equal_observed_utc_day_mean": statistics.mean(daily) if daily else None,
                    "day_cluster_interval": {
                        "available": valid[metric],
                        "reason": "descriptive_exposed_day_clusters"
                        if valid[metric]
                        else "fewer_than_five_days_or_missing_paired_day",
                        "lower_95": ordered[49] if ordered else None,
                        "upper_95": ordered[1949] if ordered else None,
                        "resamples": 2000,
                        "seed": 20261113,
                        "shared_indices_with_paired_controls": True,
                    },
                }
            rows.append(
                {
                    "group": group,
                    "cost": cost,
                    "horizon_seconds": horizon,
                    "primary_horizon": horizon == 300,
                    "policy": policy,
                    "counts": dict(count),
                    "observed_utc_days": days,
                    "observed_day_count": len(days),
                    "capture_count": len(cell["captures"]),
                    "captures": sorted(cell["captures"]),
                    "metrics_bps": metrics,
                    "utc_day_metric_sums_and_counts": cell["days"],
                    "day_sufficient_for_inference": False,
                    "interpretation": "conditional_price_opportunity_not_trade_or_account_return",
                }
            )
        return rows
