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
from typing import Final

# hist_etl's own continuity rule: settlements may drift up to a minute late.
SETTLEMENT_SLACK_MS: Final = 60_000


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
    funding_intervals_ms: Sequence[int] = (),
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
      ``funding_intervals_ms`` gives each settlement's interval; the
      settlements a mean reads must share one, with none missing.
    """

    _check_parameters(lookbacks, vol_window, bar_ms, max_funding_gap_ms)
    check_funding_means(funding_means, funding_baseline)
    if funding_means and len(funding_intervals_ms) != len(funding):
        raise BarTableError("Funding tilts need one settlement interval per settlement.")
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
        funding_intervals_ms,
        [ts for ts, _close in closes[warmup:]],
        funding_means,
        funding_baseline,
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


def check_funding_means(funding_means: Sequence[int], baseline: float) -> None:
    if any(count < 1 for count in funding_means):
        raise BarTableError("Funding mean counts must be positive settlement counts.")
    if len(set(funding_means)) != len(funding_means):
        raise BarTableError("Funding mean counts must not repeat.")
    if not math.isfinite(baseline):
        raise BarTableError("The funding baseline must be a finite rate.")


def _funding_tilts(
    funding: Sequence[tuple[int, float]],
    intervals_ms: Sequence[int],
    closes: Sequence[int],
    funding_means: Sequence[int],
    baseline: float,
) -> list[tuple[float, ...]]:
    """``baseline - mean`` of the last ``K`` settlements at or before each close.

    Only the settlements some mean reads are checked: from the oldest one the
    first output bar needs through the last close. They must share one
    interval, since one baseline cannot fit 8h and 4h rates alike, and each
    must follow the last within that interval (plus hist_etl's minute of
    slack), so a missing settlement never stretches a mean.
    """

    if not funding_means:
        return [() for _ in closes]
    # Sorted, so the count at a close holds even if older rows arrive out of order.
    known = sorted(
        (
            (ts, rate, interval)
            for (ts, rate), interval in zip(funding, intervals_ms, strict=True)
            if ts <= closes[-1]
        ),
        key=lambda entry: entry[0],
    )
    longest = max(funding_means)
    first = sum(1 for ts, _rate, _interval in known if ts <= closes[0])
    if first < longest:
        raise BarTableError(
            f"Only {first} funding settlements at or before {closes[0]}; "
            f"a mean over {longest} needs that many."
        )
    needed = known[first - longest :]
    intervals = sorted({interval for _ts, _rate, interval in needed})
    if len(intervals) != 1 or intervals[0] < 1:
        raise BarTableError(f"Funding tilts need one settlement interval; found {intervals} ms.")
    interval = intervals[0]
    settled = [(ts, rate) for ts, rate, _interval in needed]
    # None missing (finite, strictly increasing, no gap above the interval) ...
    _check_funding(settled, settled[0][0], closes[-1], interval + SETTLEMENT_SLACK_MS)
    # ... and none extra: settlements closer than the interval mean the label
    # is wrong, e.g. 4h prints marked 8h, and K of them would span half the time.
    for earlier, later in pairwise(ts for ts, _rate in settled):
        if later - earlier < interval - SETTLEMENT_SLACK_MS:
            raise BarTableError(
                f"Funding settlements at {earlier} and {later} are closer than "
                f"their {interval} ms interval."
            )
    rates = [rate for _ts, rate, _interval in known]
    # The means change only when a settlement arrives, so compute each once.
    by_count: dict[int, tuple[float, ...]] = {}
    tilts: list[tuple[float, ...]] = []
    count = first
    for close in closes:
        while count < len(known) and known[count][0] <= close:
            count += 1
        if count not in by_count:
            by_count[count] = tuple(
                baseline - math.fsum(rates[count - size : count]) / size for size in funding_means
            )
        tilts.append(by_count[count])
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
