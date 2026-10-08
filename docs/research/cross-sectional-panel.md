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
| `funding_rate` | Sum of the settlements in `(ts - 1 day, ts]`, positive when longs pay; empty without any |
| `funding_hours` | Sum of those settlements' intervals; 24 means the day is fully covered |
| `ret_<L>d` | Log return over `L` days |
| `vol_<W>d` | Sample stdev of the last `W` daily log returns; empty unless positive |
| `qv_<V>d` | Mean quote volume over `V` days, untraded days counting as 0 |
| `funding_<K>d` | Mean daily `funding_rate` over `K` fully covered days |
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
- **A daily bar is missing inside a run.** Run `hist_etl verify` and `sync`.
- **Inputs are not usable.** This covers bars off the daily grid or out of
  order, a non-positive close, negative volume, a non-finite funding rate, or
  a symbol twice on one day.

## Limits

- Daily bars only (`1d` klines). Funding is summed per day, not per position
  hold. The harness's cross-sectional mode (S3) decides how a held day's
  funding is charged, and what an incomplete funding day means for a held
  position.
- Delisting is seen through trading activity. The panel does not know the
  delisting price a holder actually got, so a study must state how it exits a
  contract that stops trading while held.
