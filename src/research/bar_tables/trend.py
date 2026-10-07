"""A point-in-time trend table: close, trend score, realized vol, and funding.

Every value at bar ``t`` uses closes at or before ``t`` only, so its
availability clock is the bar's own timestamp (the kline close time) and a
harness spec reading it needs ``latency_bars >= 1``. Warm-up bars without a
full lookback are dropped, never zero-filled. A missing bar fails closed,
because a return over ``L`` bars must span exactly ``L`` bars, and so does a
missing funding settlement, because funding must never read as a silent 0.

Optional funding tilts summarize the funding already settled at a bar's
close: ``baseline - mean`` of the last ``K`` settlements, positive when longs
paid less than the baseline (a contrarian long signal under the harness's
signed direction).
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
    # baseline - mean of the last K settlements at or before ``ts``, per K given.
    funding_tilts: tuple[float, ...] = ()


def build_trend_rows(
    closes: Sequence[tuple[int, float]],
    funding: Sequence[tuple[int, float]],
    *,
    lookbacks: Sequence[int],
    vol_window: int,
    bar_ms: int,
    max_funding_gap_ms: int,
    funding_means: Sequence[int] = (),
    funding_baseline: float = 0.0,
) -> list[TrendRow]:
    """Rows from ``(close_ts_ms, close)`` bars and ``(settle_ts_ms, rate)`` funding.

    - ``returns``: the trailing log return over each lookback, in bars.
    - ``trend_score``: the mean sign of those returns, in [-1, 1].
    - ``realized_vol``: the sample stdev of the last ``vol_window`` one-bar log
      returns, in per-bar units.
    - ``funding_rate``: the sum of the settlements in ``(previous close, close]``,
      the rate a long pays for holding over the bar (harness convention).
    - ``funding_tilts``: for each ``K`` in ``funding_means``,
      ``funding_baseline`` minus the mean of the last ``K`` settlements at or
      before the bar's close. Settlements before the first output bar count,
      so the funding read must reach back ``K`` settlements without a gap.
    """

    _check_parameters(lookbacks, vol_window, bar_ms, max_funding_gap_ms)
    _check_funding_means(funding_means, funding_baseline)
    warmup = max(max(lookbacks), vol_window)
    if len(closes) <= warmup:
        raise BarTableError(f"Need more than {warmup} bars for the warm-up; got {len(closes)}.")
    _check_bars(closes, bar_ms)
    # Only settlements inside the output bars' funding intervals are used or checked.
    settled = [entry for entry in funding if closes[warmup - 1][0] < entry[0] <= closes[-1][0]]
    _check_funding(settled, closes[warmup - 1][0], closes[-1][0], max_funding_gap_ms)
    prices = [close for _ts, close in closes]
    one_bar = [math.log(prices[index] / prices[index - 1]) for index in range(1, len(prices))]
    per_bar = _bucket_funding(settled, [ts for ts, _close in closes], start=warmup)
    tilts = _funding_tilts(
        funding,
        [ts for ts, _close in closes[warmup:]],
        funding_means,
        funding_baseline,
        max_funding_gap_ms,
    )
    rows: list[TrendRow] = []
    for index in range(warmup, len(closes)):
        returns = tuple(
            math.log(prices[index] / prices[index - lookback]) for lookback in lookbacks
        )
        # one_bar[k] is the return into bar k + 1, so these end at bar ``index``.
        vol = _sample_stdev(one_bar[index - vol_window : index])
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
                funding_tilts=tilts[index - warmup],
            )
        )
    return rows


def _check_funding_means(funding_means: Sequence[int], baseline: float) -> None:
    if any(count < 1 for count in funding_means):
        raise BarTableError("Funding mean counts must be positive settlement counts.")
    if len(set(funding_means)) != len(funding_means):
        raise BarTableError("Funding mean counts must not repeat.")
    if not math.isfinite(baseline):
        raise BarTableError("The funding baseline must be a finite rate.")


def _funding_tilts(
    funding: Sequence[tuple[int, float]],
    closes: Sequence[int],
    funding_means: Sequence[int],
    baseline: float,
    max_gap: int,
) -> list[tuple[float, ...]]:
    """``baseline - mean`` of the last ``K`` settlements at or before each close."""

    if not funding_means:
        return [() for _ in closes]
    known = [entry for entry in funding if entry[0] <= closes[-1]]
    for ts, rate in known:
        if not math.isfinite(rate):
            raise BarTableError(f"Funding rate at {ts} is not a finite number.")
    if any(later[0] <= earlier[0] for earlier, later in pairwise(known)):
        raise BarTableError("Funding settlements must be strictly increasing in time.")
    edges = [ts for ts, _rate in known] + [closes[-1]]
    for earlier, later in pairwise(edges):
        if later - earlier > max_gap:
            raise BarTableError(
                f"No funding settlement between {earlier} and {later}; "
                f"the gap exceeds {max_gap} ms."
            )
    rates = [rate for _ts, rate in known]
    tilts: list[tuple[float, ...]] = []
    count = 0
    for close in closes:
        while count < len(known) and known[count][0] <= close:
            count += 1
        if count < max(funding_means):
            raise BarTableError(
                f"Only {count} funding settlements at or before {close}; "
                f"a mean over {max(funding_means)} needs that many."
            )
        tilts.append(
            tuple(
                baseline - math.fsum(rates[count - size : count]) / size for size in funding_means
            )
        )
    return tilts


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
    settled: Sequence[tuple[int, float]], first_open: int, last_close: int, max_gap: int
) -> None:
    """The settlements in ``(first_open, last_close]`` leave no gap above ``max_gap``.

    ``first_open`` is the close of the last warm-up bar, where the first
    output bar's funding interval starts.
    """

    for ts, rate in settled:
        if not math.isfinite(rate):
            raise BarTableError(f"Funding rate at {ts} is not a finite number.")
    if any(later[0] <= earlier[0] for earlier, later in pairwise(settled)):
        raise BarTableError("Funding settlements must be strictly increasing in time.")
    edges = [first_open, *(ts for ts, _rate in settled), last_close]
    for earlier, later in pairwise(edges):
        if later - earlier > max_gap:
            raise BarTableError(
                f"No funding settlement between {earlier} and {later}; "
                f"the gap exceeds {max_gap} ms."
            )


def _bucket_funding(
    settled: Sequence[tuple[int, float]], closes: Sequence[int], *, start: int
) -> list[float]:
    """Sum each ordered settlement into the bar whose ``(previous close, close]`` holds it."""

    totals: list[list[float]] = [[] for _ in range(start, len(closes))]
    pointer = 0
    for ts, rate in settled:
        while closes[start + pointer] < ts:
            pointer += 1
        totals[pointer].append(rate)
    return [math.fsum(values) for values in totals]


def _sample_stdev(values: Sequence[float]) -> float:
    # Two passes, so a near-constant window cannot cancel to a negative variance.
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _sign(value: float) -> float:
    if value > 0.0:
        return 1.0
    if value < 0.0:
        return -1.0
    return 0.0
