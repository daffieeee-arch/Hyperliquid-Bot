# Cross-sectional panel

PAPER research only. The panel is the input table for cross-sectional
studies: one row per symbol and day, built from a hist_etl
`binance_universe` entry, delisted contracts included. It places no orders and
reads no keys.

```bash
cd "$REPO_ROOT"
export HIST_ARCHIVES_ROOT="$HOME/Hyperliquid Project/hist-archives"
PYTHONPATH=src uv run --frozen python -m research.hist_etl verify --dataset bn-um-usdt-1d
PYTHONPATH=src uv run --frozen python -m research.bar_tables panel \
  --group bn-um-usdt-1d --start 2020-01-01 --end 2026-10-01 \
  --lookbacks 7,30,90 --vol-window 30 --volume-window 30 --funding-window 7 \
  --out "$STUDY_DIR/panel.parquet"
```

The universe has to be synced first (see the "Binance USD-M universe" section
of [hist-etl.md](../runbooks/hist-etl.md)). Do not commit the Parquet.

Two options narrow who takes a `volume_rank`; both are off by default:

- `--exclude-symbols PATH`: a committed JSON list of contracts that never
  rank (see [Excluded symbols](#excluded-symbols)).
- `--rank-requires-funding`: a row ranks only on a day with a funding rate.

The build prints how many symbols the list held and its sha256, and whether
the rank required funding, so the build log records which rule made the
table.

## Columns

Every value is known at the row's daily kline close, `ts` (epoch ms), and
`available_ts` equals `ts`. A study that trades on it needs at least one bar
of latency.

| Column | Meaning |
| --- | --- |
| `ts`, `available_ts` | Close time of the daily bar, UTC epoch ms |
| `symbol` | Binance USD-M symbol |
| `close`, `quote_volume`, `trades` | The day's close, USDT volume and trade count |
| `traded` | `trades > 0` and `quote_volume > 0` |
| `funding_rate` | Sum of the day's settlements, positive when longs pay; empty without any |
| `funding_settlements` | How many settlements that is |
| `funding_covered` | No settlement of the day is missing |
| `ret_<L>d` | Log return over `L` days |
| `vol_<W>d` | Sample stdev of the last `W` daily log returns; empty unless positive |
| `qv_<V>d` | Mean quote volume over `V` days, untraded days counting as 0 |
| `funding_<K>d` | Mean daily `funding_rate` over `K` covered days |
| `carry_<K>d` | Minus the mean daily `funding_rate` over `K` days with a rate, covered or not; see [Carry](#carry) |
| `volume_rank` | Rank by `qv_<V>d` among the day's rows that traded and have it; 1 is the largest |

## Point-in-time rules

- **No silent zeros.** A feature is empty unless its whole window is there.
  Warm-up rows after a listing are kept with empty features, not dropped and
  not zero-filled.
- **A row does not depend on `start`.** The build reads the longest
  window's worth of days before `start` (whole months) as warm-up, so a
  row's features are the same whatever `start` is, wherever the universe
  has that history.
- **An archive day is not a trading day.** After a delisting, Binance Vision
  keeps publishing flat, zero-volume bars and default-rate funding. Price
  features need every day of their window `traded`. `qv_<V>d` counts an
  untraded day as zero volume.
- **Gaps restart windows.** A relisted symbol has a gap between its runs, and
  no window spans it.
- **Funding days.** A settlement belongs to the day whose `(ts - 1 day, ts]`
  holds its time plus a minute, so one stamped just before midnight counts
  for the day it opens. `funding_covered` is judged at the close from the
  day's own settlements: consecutive ones are at most the longer of their
  interval labels apart (plus a minute), the first one's label reaches back
  to the open, and the last one's past the close. A hole on the day before
  does not change it. A day that goes from 8h to 4h funding is covered.
  - **Limitation:** Binance's interval label is sometimes the hours since
    the previous settlement and sometimes the new setting (TRBUSDT, GASUSDT
    and LOOMUSDT in 2023-10). On a day that returns from 4h to 8h, a missing
    20:00 settlement and the return look alike at the close, so such a day
    can read as not covered. Its `funding_<K>d` stays empty for `K` days. A
    later settlement must not decide a feature. For the same reason, one
    settlement missing right where the interval shortens cannot be told
    from the switch itself, and is not caught.
  - Only the day's own settlements count, so the first day of a relisting
    is judged like a fresh listing, whatever `start` the panel reads from.
- **The universe comes from the rank, not from survival.** `volume_rank`
  orders the rows of one day that traded and have `qv_<V>d`, ties going to
  the symbol that sorts first. It needs no other feature, so the universe
  never depends on another feature's data, funding included. A study takes
  its universe per day from that rank, for example `volume_rank <= 50`,
  requires the features it uses on top, and writes both rules into its
  pre-registration.

## Carry

`carry_<K>d` is how a study ranks by low funding: the harness longs the top
of its signal, so a signed spec on `carry_<K>d` longs the lowest funding and
shorts the highest. It uses the same `--funding-window` as `funding_<K>d`
and the same clock.

- **Rate days, not covered days.** Its window needs `K` consecutive days in
  one stretch that each have a `funding_rate`, covered or not. A day's
  recorded sum is the funding a holder was charged that day, known at its
  close, and it is what the harness charges a position held through it. A
  day that returns from 4h to 8h funding reads as not covered at its close
  and blanks `funding_<K>d` for `K` days, and Binance changes an interval
  mostly when funding runs at its cap or floor, so a carry ranking on
  `funding_<K>d` would lose names on exactly the tails it sorts on.
  - A day with fewer settlements than usual (a listing or delisting day)
    counts at what it charged. That is real funding, not a gap; a missing
    settlement on a traded day inside a funding run fails the build.
  - Whether a day counts never depends on a later settlement or on the
    build's validation, which reads the whole series: a feature must not
    know whether a contract delists later that month.
  - A dataset's first day (a run's first month, or a manifest `start`)
    counts only when covered: hist_etl keeps no settlement stamped before
    it, so its midnight settlement may be cut away.
  - The build's validation shares the panel's limitation: one settlement
    missing right where an interval changes is not caught, so such a day
    enters short by that settlement.
- **Steps of 1e-9 of the window's sum.** Binance prints rates with at most
  8 decimals, so the window's funding sum is a multiple of 1e-8 up to
  floating-point rounding. Counted in steps of 1e-9, equal sums are exact
  ties however their settlements split, and distinct sums stay 10 steps
  apart, for any window length. A finer step would leave a float64 too few
  bits for the draw below.
- **Ties broken by a draw.** A tie is ordered by a number drawn from the
  sha256 of the symbol and the row's `ts`, which moves the value by less
  than a tenth of a step. It reads no market data, is the same in every
  build, and changes from day to day, so no symbol is favoured. Without it
  the harness would break the tie by symbol, and the contracts that sort
  first (the "1000x" ones) would always take the long leg's ties.
- A zero is never written as `-0.0`.

## Excluded symbols

A study that leaves contracts out of its universe, such as contracts whose
underlying is not a crypto asset, commits a list before any result and
passes it with `--exclude-symbols`:

```json
{
  "format": 1,
  "universe": "binance-um-usdt-1d-2026-10-08.json",
  "rule": "One line: why these contracts are left out.",
  "symbols": {"BTCDOMUSDT": "index", "XAUUSDT": "commodity"}
}
```

- A listed contract keeps its rows and features, but `volume_rank` is
  empty on every row, so the next contract moves up. A filter applied after
  the rank would shrink the top 50 instead.
- The categories are `index`, `fiat_pegged_or_fx`, `commodity`,
  `etf_or_etp`, `equity`, `private_company` and `unverified`.
- The build fails, writing nothing, on an unknown or missing key, a format
  other than 1, an empty rule, an unknown category, a duplicate key, a
  `universe` that is not the file name of the group's universe entry, or a
  symbol the group does not hold.
- The list is fixed by asset class, which is known at a contract's
  listing, so it is point in time. The data fingerprint pins its effect.

`--rank-requires-funding` drops every traded day without a rate from the
rank: listing months before funding starts, trading after a funding archive
ends, gaps between funding runs, and the first or last days of a run that
the build allows to lack funding. It reads only the day's own settlements.
It does not stop a position decided on a ranked day from holding into such
a day; the harness still fails that run closed.

## What fails the build

Nothing is written when any of these fail:

- **A month file is missing.** Every month of every run of the universe entry
  inside `[start, end)` must exist, with its hist_etl sidecar. A half-synced
  universe would otherwise drop symbols silently, most likely the delisted
  ones, which is survivorship bias.
  - A still-published run is expected up to `end`, so `end` cannot pass the
    synced data, and for funding not the newest published month.
  - A run that the universe keeps open by its grace month, but which never got
    that month, blocks the build until a newer universe file closes it.
- **A daily bar is missing inside a run.** The month files are read and
  checked whole, so where `start` or `end` cuts a month does not hide a
  hole; rows are cut to `[start, end)` only afterwards. Universe runs are
  whole months: in a run's listing month its first bar may come on any day,
  and in its delisting month its last bar may. Every other day has to be
  there, through `end` for a still-published run. Run `hist_etl verify` and
  `sync`.
- **A settlement is missing on a traded day inside a funding run.** That
  is a funding hole, and it would silently drop the symbol from the rank.
  This check is validation, not a feature, so it reads the whole series:
  between two of a run's settlements farther apart than the longer of
  their intervals, each day on which a settlement was due is a hole. At the
  edges of a run's window, funding may start late only in its listing month
  and stop early only in its delisting month, like the bars; elsewhere,
  including the end of a still-published run, a late start or early stop
  is a hole, on every day a settlement was due. The funding file of the
  month after the window is read when it exists, so the settlement after
  the window gives the interval at its end. When that month is not
  published yet, only whole days without any settlement count at the end,
  because the last label may still be the old setting. A window that lies wholly in a listing (or
  delisting) month may hold no settlement at all. Untraded days are not checked, because delisted contracts
  carry default-rate funding.
- The funding file of the month before the first month is needed too, when
  the run has it, for a midnight settlement stamped just before the month
  opens.
- **Inputs are not usable.** This covers bars that do not close at the end
  of a UTC day or are out of order, a non-positive close, negative volume,
  and a non-finite funding rate.

## Limits

- The warm-up months before `start` are checked like the rest, so they
  must be synced too.
- Funding is published per month only, so `end` cannot pass the newest
  published funding month; pick the first day of a month.
- Size: 1.44M synthetic rows (600 symbols over 2,400 days, about twice the
  2026-10-08 universe) took 51 s and peaked at 1 GB, input included.
- Daily bars only (`1d` klines). Funding is summed per day, not per position
  hold. The harness's [panel portfolio mode](hypothesis-harness.md#panel-portfolios)
  charges a held day's funding on the notional at that day's close, and
  fails closed on a traded day without a rate that a position could hold.
- A ticker that Binance reuses for another asset while it keeps trading,
  with no untraded day between, is not detected: returns then span both
  assets. Price windows restart only after untraded days and gaps.
- Delisting is seen through trading activity. The panel does not know the
  delisting price a holder actually got, so a study must state how it exits a
  contract that stops trading while held.
