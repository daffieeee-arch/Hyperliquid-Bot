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
among the rows that traded and have that volume, and needs no other
feature. A study takes its universe on each day from that rank (for
example the top 50) and requires the features it uses on top; the rank is
point in time and never looks at which symbols survive. A ``RankRule`` can
narrow who ranks: a committed list of symbols that never rank, and a day
without funding.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Final

from research.bar_tables.trend import SETTLEMENT_SLACK_MS, BarTableError

DAY_MS: Final = 86_400_000
# Ten years: longer than any archive, and short enough for date arithmetic.
MAX_WINDOW_DAYS: Final = 3_650
_HOUR_MS: Final = 3_600_000
# carry_<K>d counts its window's funding sum in steps of 1e-9. Binance prints
# rates with at most 8 decimals, so equal sums are exact ties and distinct
# ones stay 10 steps apart, whatever the window length; a coarser step
# leaves a float64 more bits for the tie-break draw.
_CARRY_STEPS_PER_UNIT: Final = 1_000_000_000


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One daily kline. ``ts`` is its close time in epoch ms."""

    ts: int
    close: float
    quote_volume: float
    trades: int

    @property
    def traded(self) -> bool:
        """Trades and quote volume: a delisted contract's flat bars have neither."""

        return self.trades > 0 and self.quote_volume > 0.0


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
        if not self.lookbacks or any(not 1 <= window <= MAX_WINDOW_DAYS for window in windows):
            raise BarTableError(
                f"Panel windows must be 1 to {MAX_WINDOW_DAYS} days, with a lookback."
            )
        if self.vol_window < 2:
            raise BarTableError("The vol window needs at least two daily returns.")
        if len(set(self.lookbacks)) != len(self.lookbacks):
            raise BarTableError("Panel lookbacks must be distinct.")

    @property
    def warmup_days(self) -> int:
        """Days of history before a row that its longest window can read."""

        return max(max(self.lookbacks), self.vol_window, self.volume_window, self.funding_window)


@dataclass(frozen=True, slots=True)
class PanelRow:
    """One symbol on one day; every value is known at ``ts``.

    - ``funding_rate``: the sum of the day's settlements, the rate a long
      pays for holding from the previous close to ``ts``; ``None`` without
      any. A settlement belongs to the day whose ``(ts - 1 day, ts]`` holds
      its time plus a minute, so one stamped just before midnight still
      counts for the day it opens.
    - ``funding_settlements``: how many settlements that is.
    - ``funding_covered``: no settlement of the day is missing, judged at the
      close from each settlement's interval label: the day's consecutive
      settlements are at most the longer label apart, the first one's label
      reaches back to the open, and the last one's reaches past the close.
    - ``returns``: the log return over each lookback, in the spec's order.
    - ``realized_vol``: the sample stdev of the window's daily log returns;
      ``None`` unless positive.
    - ``mean_quote_volume``: the mean over the volume window, untraded days
      counting as their (zero) volume.
    - ``mean_funding``: the mean daily ``funding_rate`` over the funding
      window, every day of it covered.
    - ``carry``: minus the mean daily ``funding_rate`` over the funding
      window, every day of it with a rate, covered or not, and ordered as
      ``carry_value`` says. A study that ranks by low funding sorts on it.
    """

    ts: int
    symbol: str
    close: float
    quote_volume: float
    trades: int
    traded: bool
    funding_rate: float | None
    funding_settlements: int
    funding_covered: bool
    returns: tuple[float | None, ...]
    realized_vol: float | None
    mean_quote_volume: float | None
    mean_funding: float | None
    volume_rank: int | None = None
    carry: float | None = None

    @property
    def rankable(self) -> bool:
        """Traded, with a trailing volume: what the volume universe needs."""

        return self.traded and self.mean_quote_volume is not None

    @property
    def complete(self) -> bool:
        """Rankable with every feature present, a study's usual row filter."""

        return (
            self.rankable
            and all(value is not None for value in self.returns)
            and self.realized_vol is not None
            and self.mean_funding is not None
        )


def build_symbol_rows(
    symbol: str,
    bars: Sequence[DailyBar],
    settlements: Sequence[Settlement],
    spec: PanelSpec,
    cut_closes: frozenset[int] = frozenset(),
) -> list[PanelRow]:
    """Rows for one symbol, without ``volume_rank``.

    ``bars`` may have gaps (between runs of a relisted symbol); a window that
    spans one gives ``None``. ``settlements`` may reach back before the first
    bar; only those inside a bar's day are used.

    ``carry`` needs every day of its window traded and with a rate, covered
    or not: the day's recorded sum is the funding a holder was charged that
    day (and what the harness charges), known at its close. Like the price
    features it reads no archive day that did not trade, whose funding no
    one paid and the build does not check. ``cut_closes`` are days whose
    settlements the data range may have cut (a funding dataset's first day:
    hist_etl keeps no settlement stamped before it); they count only when
    covered. ``mean_funding`` keeps needing covered days.
    """

    _check_bars(symbol, bars)
    _check_settlements(symbol, settlements)
    funding = _daily_funding(bars, settlements)
    traded = [bar.traded for bar in bars]
    # An untraded day counts as zero volume, whatever the bar says.
    volume = [bar.quote_volume if flag else 0.0 for bar, flag in zip(bars, traded, strict=True)]
    # run_start[i]: index of the first bar of the consecutive-day stretch holding i.
    run_start = _run_starts(bars)
    traded_streak = _streaks(traded, run_start)
    covered_streak = _streaks([day.covered for day in funding], run_start)
    carry_streak = _streaks(
        [
            flag and day.rate is not None and (day.covered or bar.ts not in cut_closes)
            for day, flag, bar in zip(funding, traded, bars, strict=True)
        ],
        run_start,
    )
    # one_day[i]: the log return into bar i, for bars inside one stretch.
    one_day = [
        math.log(bar.close / bars[index - 1].close) if run_start[index] != index else 0.0
        for index, bar in enumerate(bars)
    ]
    rows: list[PanelRow] = []
    for index, bar in enumerate(bars):
        span = index - run_start[index] + 1
        # Summed when either funding feature needs it; a covered window has
        # every rate, so the sum serves both.
        funding_sum = (
            math.fsum(
                _known(day.rate) for day in funding[index - spec.funding_window + 1 : index + 1]
            )
            if max(carry_streak[index], covered_streak[index]) >= spec.funding_window
            else None
        )
        rows.append(
            PanelRow(
                ts=bar.ts,
                symbol=symbol,
                close=bar.close,
                quote_volume=bar.quote_volume,
                trades=bar.trades,
                traded=traded[index],
                funding_rate=funding[index].rate,
                funding_settlements=funding[index].count,
                funding_covered=funding[index].covered,
                returns=tuple(
                    _log_return(bars, index, lookback) if traded_streak[index] > lookback else None
                    for lookback in spec.lookbacks
                ),
                realized_vol=(
                    _sample_stdev(one_day[index - spec.vol_window + 1 : index + 1])
                    if traded_streak[index] > spec.vol_window
                    else None
                ),
                mean_quote_volume=(
                    math.fsum(volume[index - spec.volume_window + 1 : index + 1])
                    / spec.volume_window
                    if span >= spec.volume_window
                    else None
                ),
                mean_funding=(
                    _known(funding_sum) / spec.funding_window
                    if covered_streak[index] >= spec.funding_window
                    else None
                ),
                carry=(
                    carry_value(symbol, bar.ts, _known(funding_sum), spec.funding_window)
                    if carry_streak[index] >= spec.funding_window
                    else None
                ),
            )
        )
    return rows


def carry_value(symbol: str, ts: int, funding_sum: float, window: int) -> float:
    """Minus the window's mean daily funding, ties broken by a draw.

    Sorting by it puts the lowest funding first. The window's sum is counted
    in steps of 1e-9, so sums that differ only by floating-point rounding
    (one settlement of 0.0003 against three of 0.0001) are an exact tie,
    and distinct 8-decimal sums never merge. A tie is ordered by a draw from
    the symbol and the day alone: it moves a value by less than a tenth of
    a step, so it never reorders distinct sums, reads no market data, and
    favours no symbol across days, where a tie-break by symbol would always
    favour the same end of the alphabet.
    """

    steps = round(funding_sum * _CARRY_STEPS_PER_UNIT)
    digest = hashlib.sha256(f"{symbol}|{ts}".encode()).digest()
    draw = int.from_bytes(digest[:8], "big") / 2**64
    # draw / 10 - steps is never -0.0: it is at least 0 when steps is 0.
    return (draw / 10 - steps) / (window * _CARRY_STEPS_PER_UNIT)


@dataclass(frozen=True, slots=True)
class RankRule:
    """Which rows may take a volume rank, on top of trading with a trailing volume.

    - ``excluded``: symbols that never rank, such as a study's committed list
      of contracts whose underlying is not a crypto asset. Their rows and
      features stay; the next symbol moves up. The list is fixed before
      any result, so it is point in time.
    - ``require_funding``: a row ranks only with a funding rate that day,
      known at its close, so a study that charges funding never takes a
      name into its universe on a day the name has none.
    """

    excluded: frozenset[str] = frozenset()
    require_funding: bool = False

    def admits(self, row: PanelRow) -> bool:
        if not row.rankable or row.symbol in self.excluded:
            return False
        return row.funding_rate is not None or not self.require_funding


def rank_by_volume(rows: Sequence[PanelRow], rule: RankRule | None = None) -> list[PanelRow]:
    """Set ``volume_rank`` per day among the rows ``rule`` admits: 1 is the largest volume.

    By default the rank needs only trading and trailing volume, so the
    universe never depends on a feature's data, funding included; a study
    requires the features it uses on top. A ``RankRule`` narrows it.

    Ties go to the symbol that sorts first, so the rank is deterministic.
    Rows come back ordered by ``ts`` and then symbol.
    """

    admitted = rule or RankRule()
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
            (row for row in day if admitted.admits(row)),
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
    for bar in bars:
        if bar.ts % DAY_MS != DAY_MS - 1:
            raise BarTableError(f"{symbol} bar at {bar.ts} does not close at the end of a UTC day.")
        if not (math.isfinite(bar.close) and bar.close > 0.0):
            raise BarTableError(f"{symbol} close at {bar.ts} is not a positive number.")
        if not math.isfinite(bar.quote_volume):
            raise BarTableError(f"{symbol} quote volume at {bar.ts} is not a number.")
        if bar.quote_volume < 0.0 or bar.trades < 0:
            raise BarTableError(f"{symbol} volume or trade count at {bar.ts} is negative.")


def _check_settlements(symbol: str, settlements: Sequence[Settlement]) -> None:
    for earlier, later in pairwise(settlements):
        if later.ts <= earlier.ts:
            raise BarTableError(f"{symbol} funding is not in strictly increasing time order.")
    for settlement in settlements:
        if not math.isfinite(settlement.rate) or not 1 <= settlement.interval_hours <= 24:
            raise BarTableError(f"{symbol} funding at {settlement.ts} is not usable.")


@dataclass(frozen=True, slots=True)
class _FundingDay:
    rate: float | None
    count: int
    covered: bool


def _daily_funding(
    bars: Sequence[DailyBar], settlements: Sequence[Settlement]
) -> list[_FundingDay]:
    result: list[_FundingDay] = []
    cursor = 0
    for bar in bars:
        opens = bar.ts - DAY_MS
        while cursor < len(settlements) and _day_time(settlements[cursor]) <= opens:
            cursor += 1
        probe = cursor
        while probe < len(settlements) and _day_time(settlements[probe]) <= bar.ts:
            probe += 1
        day = settlements[cursor:probe]
        rate = math.fsum(item.rate for item in day) if day else None
        result.append(_FundingDay(rate, len(day), _covered(day, opens, bar.ts)))
    return result


def _day_time(settlement: Settlement) -> int:
    return settlement.ts + SETTLEMENT_SLACK_MS


def settlement_day_close(moment: int) -> int:
    """The close of the day a settlement stamped at ``moment`` belongs to."""

    shifted = moment + SETTLEMENT_SLACK_MS
    return shifted - shifted % DAY_MS + DAY_MS - 1


def _covered(day: Sequence[Settlement], opens: int, closes: int) -> bool:
    """No settlement due inside the day is missing, judged at the close.

    The day's consecutive settlements are at most the longer of their labels
    apart, the first one's label reaches back to the open, and the last
    one's past the close. Only the day's own settlements count, so neither a
    hole on the day before nor how far back the panel reads changes it.
    Binance's interval label is the hours since the previous settlement or
    the new setting, so a day that returns from 4h to 8h funding can read
    as short a settlement: the close cannot tell yet, and a later settlement
    must not decide it. ``funding_hole_closes`` judges holes with the whole
    series instead.
    """

    if not day:
        return False
    first, last = day[0], day[-1]
    if first.ts - first.interval_hours * _HOUR_MS + SETTLEMENT_SLACK_MS > opens:
        return False
    if last.ts + last.interval_hours * _HOUR_MS + SETTLEMENT_SLACK_MS <= closes:
        return False
    return all(_close_enough(earlier, later) for earlier, later in pairwise(day))


def _close_enough(earlier: Settlement, later: Settlement) -> bool:
    longer = max(earlier.interval_hours, later.interval_hours)
    return later.ts - earlier.ts <= longer * _HOUR_MS + SETTLEMENT_SLACK_MS


def funding_hole_closes(settlements: Sequence[Settlement]) -> set[int]:
    """Close times of the days in which a settlement was due but is missing.

    Validation, not a feature: it reads the whole series, later settlements
    included. Between two settlements farther apart than the longer of their
    intervals, settlements were due every shorter interval after the first
    expected one; each due time marks the day it would have opened.
    """

    closes: set[int] = set()
    for earlier, later in pairwise(settlements):
        if _close_enough(earlier, later):
            continue
        step = min(earlier.interval_hours, later.interval_hours) * _HOUR_MS
        due = earlier.ts + max(earlier.interval_hours, later.interval_hours) * _HOUR_MS
        while due + SETTLEMENT_SLACK_MS < later.ts:
            closes.add(settlement_day_close(due))
            due += step
    return closes


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
        elif run_start[index] != index:
            streaks.append(streaks[-1] + 1)
        else:
            streaks.append(1)
    return streaks


def _log_return(bars: Sequence[DailyBar], index: int, lookback: int) -> float:
    return math.log(bars[index].close / bars[index - lookback].close)


def _sample_stdev(returns: Sequence[float]) -> float | None:
    mean = math.fsum(returns) / len(returns)
    stdev = math.sqrt(math.fsum((value - mean) ** 2 for value in returns) / (len(returns) - 1))
    return stdev if stdev > 0.0 else None


def _known(value: float | None) -> float:
    if value is None:
        raise BarTableError("A value the window checks guarantee is missing.")
    return value
