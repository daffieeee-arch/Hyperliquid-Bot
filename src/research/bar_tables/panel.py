"""A point-in-time daily cross-sectional panel: one row per symbol and day.

Each row holds what was known at that day's kline close: the close, volume,
trade count, the funding settled over the day, and trailing features. A
feature is ``None`` unless its whole window is there, so a listing, a
delisting, a relisting gap, or a day without trades never turns into a
silent 0 or a return across missing days.

An archive day is not a trading day: after a delisting Binance Vision keeps
publishing flat, zero-volume bars and default-rate funding. ``traded``
marks a day with trades and quote volume, and the price features need
every day of their window traded.

``volume_rank`` orders the symbols of one day by trailing quote volume
among the rows that traded and have every feature. A study takes its
universe on each day from that rank (for example the top 50), which is
point in time: it never looks at which symbols survive.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Final

from research.bar_tables.trend import BarTableError

DAY_MS: Final = 86_400_000


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One daily kline. ``ts`` is its close time in epoch ms."""

    ts: int
    close: float
    quote_volume: float
    trades: int


@dataclass(frozen=True, slots=True)
class Settlement:
    ts: int
    rate: float
    interval_hours: int


@dataclass(frozen=True, slots=True)
class PanelSpec:
    """Window lengths in days."""

    lookbacks: tuple[int, ...]
    vol_window: int
    volume_window: int
    funding_window: int

    def __post_init__(self) -> None:
        windows = (*self.lookbacks, self.vol_window, self.volume_window, self.funding_window)
        if not self.lookbacks or any(window < 1 for window in windows):
            raise BarTableError("Panel windows must be positive day counts, with a lookback.")
        if self.vol_window < 2:
            raise BarTableError("The vol window needs at least two daily returns.")
        if len(set(self.lookbacks)) != len(self.lookbacks):
            raise BarTableError("Panel lookbacks must be distinct.")


@dataclass(frozen=True, slots=True)
class PanelRow:
    """One symbol on one day; every value is known at ``ts``.

    - ``funding_rate``: the sum of the settlements in ``(ts - 1 day, ts]``,
      the rate a long pays for holding over the day; ``None`` without any.
    - ``funding_hours``: the sum of those settlements' intervals. A day is
      fully covered when it is 24.
    - ``returns``: the log return over each lookback, in the spec's order.
    - ``realized_vol``: the sample stdev of the window's daily log returns;
      ``None`` unless positive.
    - ``mean_quote_volume``: the mean over the volume window, untraded days
      counting as their (zero) volume.
    - ``mean_funding``: the mean daily ``funding_rate`` over the funding
      window, every day of it fully covered.
    """

    ts: int
    symbol: str
    close: float
    quote_volume: float
    trades: int
    traded: bool
    funding_rate: float | None
    funding_hours: int
    returns: tuple[float | None, ...]
    realized_vol: float | None
    mean_quote_volume: float | None
    mean_funding: float | None
    volume_rank: int | None = None

    @property
    def complete(self) -> bool:
        return (
            self.traded
            and all(value is not None for value in self.returns)
            and self.realized_vol is not None
            and self.mean_quote_volume is not None
            and self.mean_funding is not None
        )


def build_symbol_rows(
    symbol: str,
    bars: Sequence[DailyBar],
    settlements: Sequence[Settlement],
    spec: PanelSpec,
) -> list[PanelRow]:
    """Rows for one symbol, without ``volume_rank``.

    ``bars`` may have gaps (between runs of a relisted symbol); a window that
    spans one gives ``None``. ``settlements`` may reach back before the first
    bar; only those inside a bar's day are used.
    """

    _check_bars(symbol, bars)
    _check_settlements(symbol, settlements)
    funding = _daily_funding(bars, settlements)
    traded = [bar.trades > 0 and bar.quote_volume > 0.0 for bar in bars]
    # run_start[i]: index of the first bar of the consecutive-day stretch holding i.
    run_start = _run_starts(bars)
    traded_streak = _streaks(traded, run_start)
    covered = [hours == 24 for _rate, hours in funding]
    covered_streak = _streaks(covered, run_start)
    rows: list[PanelRow] = []
    for index, bar in enumerate(bars):
        span = index - run_start[index] + 1
        rows.append(
            PanelRow(
                ts=bar.ts,
                symbol=symbol,
                close=bar.close,
                quote_volume=bar.quote_volume,
                trades=bar.trades,
                traded=traded[index],
                funding_rate=funding[index][0],
                funding_hours=funding[index][1],
                returns=tuple(
                    _log_return(bars, index, lookback) if traded_streak[index] > lookback else None
                    for lookback in spec.lookbacks
                ),
                realized_vol=(
                    _realized_vol(bars, index, spec.vol_window)
                    if traded_streak[index] > spec.vol_window
                    else None
                ),
                mean_quote_volume=(
                    math.fsum(
                        bar.quote_volume for bar in bars[index - spec.volume_window + 1 : index + 1]
                    )
                    / spec.volume_window
                    if span >= spec.volume_window
                    else None
                ),
                mean_funding=(
                    math.fsum(
                        _known(rate)
                        for rate, _hours in funding[index - spec.funding_window + 1 : index + 1]
                    )
                    / spec.funding_window
                    if covered_streak[index] >= spec.funding_window
                    else None
                ),
            )
        )
    return rows


def rank_by_volume(rows: Sequence[PanelRow]) -> list[PanelRow]:
    """Set ``volume_rank`` per day among complete rows: 1 is the largest volume.

    Ties go to the symbol that sorts first, so the rank is deterministic.
    Rows come back ordered by ``ts`` and then symbol.
    """

    by_day: dict[int, list[PanelRow]] = defaultdict(list)
    for row in rows:
        by_day[row.ts].append(row)
    ranked: list[PanelRow] = []
    for ts in sorted(by_day):
        day = by_day[ts]
        symbols = [row.symbol for row in day]
        if len(set(symbols)) != len(symbols):
            raise BarTableError(f"A symbol appears twice on the day closing at {ts}.")
        eligible = sorted(
            (row for row in day if row.complete),
            key=lambda row: (-_known(row.mean_quote_volume), row.symbol),
        )
        ranks = {row.symbol: position for position, row in enumerate(eligible, start=1)}
        ranked.extend(
            replace(row, volume_rank=ranks.get(row.symbol))
            for row in sorted(day, key=lambda row: row.symbol)
        )
    return ranked


def _check_bars(symbol: str, bars: Sequence[DailyBar]) -> None:
    for earlier, later in pairwise(bars):
        if later.ts <= earlier.ts:
            raise BarTableError(f"{symbol} bars are not in strictly increasing time order.")
        if (later.ts - earlier.ts) % DAY_MS:
            raise BarTableError(f"{symbol} bar at {later.ts} is off the daily grid.")
    for bar in bars:
        if not (math.isfinite(bar.close) and bar.close > 0.0):
            raise BarTableError(f"{symbol} close at {bar.ts} is not a positive number.")
        if not (math.isfinite(bar.quote_volume) and bar.quote_volume >= 0.0) or bar.trades < 0:
            raise BarTableError(f"{symbol} volume or trade count at {bar.ts} is negative.")


def _check_settlements(symbol: str, settlements: Sequence[Settlement]) -> None:
    for earlier, later in pairwise(settlements):
        if later.ts <= earlier.ts:
            raise BarTableError(f"{symbol} funding is not in strictly increasing time order.")
    for settlement in settlements:
        if not math.isfinite(settlement.rate) or settlement.interval_hours < 1:
            raise BarTableError(f"{symbol} funding at {settlement.ts} is not usable.")


def _daily_funding(
    bars: Sequence[DailyBar], settlements: Sequence[Settlement]
) -> list[tuple[float | None, int]]:
    """Per bar, the summed rate and interval hours of settlements in ``(ts - 1 day, ts]``."""

    result: list[tuple[float | None, int]] = []
    cursor = 0
    for bar in bars:
        while cursor < len(settlements) and settlements[cursor].ts <= bar.ts - DAY_MS:
            cursor += 1
        day: list[Settlement] = []
        probe = cursor
        while probe < len(settlements) and settlements[probe].ts <= bar.ts:
            day.append(settlements[probe])
            probe += 1
        rate = math.fsum(item.rate for item in day) if day else None
        result.append((rate, sum(item.interval_hours for item in day)))
    return result


def _run_starts(bars: Sequence[DailyBar]) -> list[int]:
    starts: list[int] = []
    for index, bar in enumerate(bars):
        if index and bar.ts - bars[index - 1].ts == DAY_MS:
            starts.append(starts[-1])
        else:
            starts.append(index)
    return starts


def _streaks(flags: Sequence[bool], run_start: Sequence[int]) -> list[int]:
    """Consecutive days ending at each index, inside one stretch, with the flag set."""

    streaks: list[int] = []
    for index, flag in enumerate(flags):
        if not flag:
            streaks.append(0)
        elif index and run_start[index] != index:
            streaks.append(streaks[-1] + 1)
        else:
            streaks.append(1)
    return streaks


def _log_return(bars: Sequence[DailyBar], index: int, lookback: int) -> float:
    return math.log(bars[index].close / bars[index - lookback].close)


def _realized_vol(bars: Sequence[DailyBar], index: int, window: int) -> float | None:
    returns = [
        math.log(bars[position].close / bars[position - 1].close)
        for position in range(index - window + 1, index + 1)
    ]
    mean = math.fsum(returns) / window
    stdev = math.sqrt(math.fsum((value - mean) ** 2 for value in returns) / (window - 1))
    return stdev if stdev > 0.0 else None


def _known(value: float | None) -> float:
    if value is None:
        raise BarTableError("A value the window checks guarantee is missing.")
    return value
