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
| `volume_rank` | Rank by `qv_<V>d` among the day's complete rows; 1 is the largest |

## Point-in-time rules

- **No silent zeros.** A feature is empty unless its whole window is there.
  Warm-up rows at the start and after a listing are kept with empty features,
  not dropped and not zero-filled.
- **An archive day is not a trading day.** After a delisting, Binance Vision
  keeps publishing flat, zero-volume bars and default-rate funding. Price
  features need every day of their window `traded`. `qv_<V>d` counts an
  untraded day as zero volume.
- **Gaps restart windows.** A relisted symbol has a gap between its runs, and
  no window spans it.
- **Funding days.** A settlement belongs to the day whose `(ts - 1 day, ts]`
  holds its time plus a minute, so one stamped just before midnight counts
  for the day it opens. `funding_covered` is judged at the close, from
  settlements up to it: consecutive settlements, the previous day's last
  included, are at most the longer of their intervals apart (plus a
  minute), and the next one is due after the close by the last one's
  interval. A day that goes from 8h to 4h funding is covered.
  - **Limitation:** Binance's interval label is sometimes the hours since
    the previous settlement and sometimes the new setting (TRBUSDT, GASUSDT
    and LOOMUSDT in 2023-10). On a day that returns from 4h to 8h, a missing
    20:00 settlement and the return look alike at the close, so such a day
    can read as not covered. Its `funding_<K>d` stays empty for `K` days. A
    later settlement must not decide a feature. For the same reason, one
    settlement missing right where the interval shortens cannot be told
    from the switch itself, and is not caught.
  - A settlement from before the previous day is not used to judge a day's
    start, so the first day of a relisting is judged like a fresh listing,
    whatever `start` the panel reads from.
- **The universe comes from the rank, not from survival.** A row is complete
  when it traded and has every feature. `volume_rank` orders the complete
  rows of one day, ties going to the symbol that sorts first. A study takes
  its universe per day from that rank, for example `volume_rank <= 50`, and
  writes that rule into its pre-registration.

## What fails the build

Nothing is written when any of these fail:

- **A month file is missing.** Every month of every run of the universe entry
  inside `[start, end)` must exist, with its hist_etl sidecar. A half-synced
  universe would otherwise drop symbols silently, most likely the delisted
  ones, which is survivorship bias.
  - A still-published run is expected up to `end`, so `end` cannot pass the
    synced data.
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
  is a hole. The settlement after the window, when loaded, gives the
  interval at the end. A window that lies wholly in a listing (or
  delisting) month may hold no settlement at all. Untraded days are not checked, because delisted contracts
  carry default-rate funding.
- The funding file of the month before the first month is needed too, when
  the run has it, for a midnight settlement stamped just before the month
  opens.
- **Inputs are not usable.** This covers bars that do not close at the end
  of a UTC day or are out of order, a non-positive close, negative volume,
  and a non-finite funding rate.

## Limits

- Rows start at `start`. Features can use bars from earlier in `start`'s
  month, which are past data, but not from before it, so up to the longest
  window of days is warm-up. Pick `start` that far before the study's first
  decision.
- Size: 1.44M synthetic rows (600 symbols over 2,400 days, about twice the
  2026-10-08 universe) took 51 s and peaked at 1 GB, input included.
- Daily bars only (`1d` klines). Funding is summed per day, not per position
  hold. The harness's cross-sectional mode (S3) decides how a held day's
  funding is charged, and what an incomplete funding day means for a held
  position.
- Delisting is seen through trading activity. The panel does not know the
  delisting price a holder actually got, so a study must state how it exits a
  contract that stops trading while held.
