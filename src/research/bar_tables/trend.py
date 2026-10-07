"""A point-in-time trend table: close, trend score, realized vol, and funding.

Every value at bar ``t`` uses closes at or before ``t`` only, so its
availability clock is the bar's own timestamp (the kline close time) and a
harness spec reading it needs ``latency_bars >= 1``. Warm-up bars without a
full lookback are dropped, never zero-filled. A missing bar fails closed,
because a return over ``L`` bars must span exactly ``L`` bars, and so does a
missing funding settlement, because funding must never read as a silent 0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise


class BarTableError(Exception):
    """The inputs cannot give a point-in-time table; nothing is written."""


@dataclass(frozen=True, slots=True)
class TrendRow:
    """One bar. ``returns`` follows the lookback order given to the builder."""

    ts: int
    close: float
    returns: tuple[float, ...]
    trend_score: float
    realized_vol: float
    funding_rate: float


def build_trend_rows(
    closes: Sequence[tuple[int, float]],
    funding: Sequence[tuple[int, float]],
    *,
    lookbacks: Sequence[int],
    vol_window: int,
    bar_ms: int,
    max_funding_gap_ms: int,
) -> list[TrendRow]:
    """Rows from ``(close_ts_ms, close)`` bars and ``(settle_ts_ms, rate)`` funding.

    - ``returns``: the trailing log return over each lookback, in bars.
    - ``trend_score``: the mean sign of those returns, in [-1, 1].
    - ``realized_vol``: the sample stdev of the last ``vol_window`` one-bar log
      returns, in per-bar units.
    - ``funding_rate``: the sum of the settlements in ``(previous close, close]``,
      the rate a long pays for holding over the bar (harness convention).
    """

    _check_parameters(lookbacks, vol_window, bar_ms, max_funding_gap_ms)
    warmup = max(max(lookbacks), vol_window)
    if len(closes) <= warmup:
        raise BarTableError(f"Need more than {warmup} bars for the warm-up; got {len(closes)}.")
    _check_bars(closes, bar_ms)
    _check_funding(funding, closes[warmup - 1][0], closes[-1][0], max_funding_gap_ms)
    prices = [close for _ts, close in closes]
    one_bar = [math.log(prices[index] / prices[index - 1]) for index in range(1, len(prices))]
    squares = [value * value for value in one_bar]
    per_bar = _bucket_funding(funding, [ts for ts, _close in closes], start=warmup)
    rows: list[TrendRow] = []
    for index in range(warmup, len(closes)):
        returns = tuple(
            math.log(prices[index] / prices[index - lookback]) for lookback in lookbacks
        )
        # one_bar[k] is the return into bar k + 1, so these end at bar ``index``.
        vol = _sample_stdev(
            one_bar[index - vol_window : index], squares[index - vol_window : index]
        )
        if not vol > 0.0:
            raise BarTableError(f"Realized vol is not positive at {closes[index][0]}.")
        rows.append(
            TrendRow(
                ts=closes[index][0],
                close=prices[index],
                returns=returns,
                trend_score=math.fsum(_sign(value) for value in returns) / len(returns),
                realized_vol=vol,
                funding_rate=per_bar[index - warmup],
            )
        )
    return rows


def _check_parameters(
    lookbacks: Sequence[int], vol_window: int, bar_ms: int, max_funding_gap_ms: int
) -> None:
    if not lookbacks or any(lookback < 1 for lookback in lookbacks):
        raise BarTableError("Lookbacks must be one or more positive bar counts.")
    if len(set(lookbacks)) != len(lookbacks):
        raise BarTableError("Lookbacks must not repeat.")
    if vol_window < 2:
        raise BarTableError("The vol window needs at least two returns.")
    if bar_ms < 1 or max_funding_gap_ms < 1:
        raise BarTableError("The bar length and the funding gap must be positive.")


def _check_bars(closes: Sequence[tuple[int, float]], bar_ms: int) -> None:
    for index, (ts, close) in enumerate(closes):
        if not (math.isfinite(close) and close > 0.0):
            raise BarTableError(f"Close at {ts} is not a positive number.")
        if index and ts - closes[index - 1][0] != bar_ms:
            raise BarTableError(
                f"Bars are not contiguous: {closes[index - 1][0]} is followed by {ts}, "
                f"not by {closes[index - 1][0] + bar_ms}."
            )


def _check_funding(
    funding: Sequence[tuple[int, float]], first_open: int, last_close: int, max_gap: int
) -> None:
    """Settlements must cover ``(first_open, last_close]`` with no gap above ``max_gap``.

    ``first_open`` is the close of the last warm-up bar, where the first
    output bar's funding interval starts.
    """

    covered = [ts for ts, _rate in funding if first_open < ts <= last_close]
    for ts, rate in funding:
        if not math.isfinite(rate):
            raise BarTableError(f"Funding rate at {ts} is not a finite number.")
    if any(later <= earlier for earlier, later in pairwise(funding)):
        raise BarTableError("Funding settlements must be strictly increasing in time.")
    edges = [first_open, *covered, last_close]
    for earlier, later in pairwise(edges):
        if later - earlier > max_gap:
            raise BarTableError(
                f"No funding settlement between {earlier} and {later}; "
                f"the gap exceeds {max_gap} ms."
            )


def _bucket_funding(
    funding: Sequence[tuple[int, float]], closes: Sequence[int], *, start: int
) -> list[float]:
    """Sum each settlement into the bar whose ``(previous close, close]`` holds it."""

    totals: list[list[float]] = [[] for _ in range(start, len(closes))]
    pointer = 0
    for ts, rate in funding:
        if ts <= closes[start - 1] or ts > closes[-1]:
            continue
        while closes[start + pointer] < ts:
            pointer += 1
        totals[pointer].append(rate)
    return [math.fsum(values) for values in totals]


def _sample_stdev(values: Sequence[float], squares: Sequence[float]) -> float:
    # Exact sums of the values and their squares keep this one pass per window.
    count = len(values)
    total = math.fsum(values)
    variance = (math.fsum(squares) - total * total / count) / (count - 1)
    return math.sqrt(variance) if variance > 0.0 else 0.0


def _sign(value: float) -> float:
    if value > 0.0:
        return 1.0
    if value < 0.0:
        return -1.0
    return 0.0
