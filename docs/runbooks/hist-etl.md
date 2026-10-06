# Historical archive ETL

PAPER / free public data only. This pipeline does not place orders, read
exchange keys, or attach to capture tmux sessions.

Command:

```bash
cd "$HOME/Hyperliquid Project/Hyperliquid-Bot"
export HIST_ARCHIVES_ROOT="$HOME/Hyperliquid Project/hist-archives"
PYTHONPATH=src uv run --frozen python -m research.hist_etl plan
PYTHONPATH=src uv run --frozen python -m research.hist_etl sync
PYTHONPATH=src uv run --frozen python -m research.hist_etl verify
PYTHONPATH=src uv run --frozen python -m research.hist_etl catalog
```

`plan` and `sync --dry-run` print the file list and a size estimate. They do
not write. `sync` downloads what is missing, checks SHA256, writes monthly
Parquet, and refreshes `hist_*` views. `verify` re-checks archives already on
disk and exits non-zero when the gap report is not empty.

The archive root is `--root` or `HIST_ARCHIVES_ROOT`. There is no default host
path. The dataset list is `config/hist_etl/datasets.toml` (override with
`--manifest` or `HIST_ETL_MANIFEST`).

## What it refuses to do

- It does not delete, truncate, or replace an existing zip, checksum, or
  unrelated file. A checksum mismatch is reported and left in place.
- Download scratch is `*.partial` under the canonical directory. That scratch
  is discarded only when the checksum of a new download fails.
- It will not use a root inside `data-capture`.
- `sync` exits 3 when free space is below `min_free_bytes` (manifest) or
  `HIST_ETL_MIN_FREE_BYTES`. The default floor is 10 GiB.
- Kline and metrics holes, missing publishable archives, schema failures, and
  checksum failures exit 2 and write `logs/gap_report.json`.
- Kraken OHLCVT omits minutes with no trades. Those holes are not failures.
  Disagreeing duplicate timestamps are.

## Binance Vision

Layouts and checksums follow
[binance/binance-public-data](https://github.com/binance/binance-public-data/blob/master/README.md)
and `python/utility.py` `get_path`:

- Spot: `data/spot/{monthly|daily}/{klines|aggTrades}/...`
- USD-M: `data/futures/um/{monthly|daily}/{klines|aggTrades|fundingRate|markPriceKlines|indexPriceKlines|premiumIndexKlines|metrics}/...`
- Each zip has a sibling `.CHECKSUM` checked as `sha256sum -c` (SHA256).

Publication lag from that README: daily files the next UTC day, monthly files
on the first Monday of the next month. The planner does not request a file
before that, and a later 404 is a gap.

CSV quirks the loader accepts, and tests:

- Headerless numeric rows, which are the README samples.
- A header row. Issue
  [#267](https://github.com/binance/binance-public-data/issues/267) records
  that some kline files contain headers and most do not. A non-numeric first
  field is treated as a header and mapped by name.
- Spot timestamps are microseconds from 2025-01-01 and milliseconds before
  that (README note plus the spot example `1735689600000000`). A spot file on
  the wrong side of that cutoff fails closed.
- USD-M kline examples in the README are milliseconds. A file whose stamps are
  uniformly microseconds is still accepted.

Monthly zips supersede daily zips for that month. Rows are deduped on the
series key. Conflicting duplicates fail. Parquet is one file per month, under
a tree the legacy converter does not use:

```text
parquet/hist_etl/binance/{spot|um}/{klines_1m|klines_1h|aggtrades|funding|mark_klines_1m|index_klines_1m|premium_klines_1m|metrics}/SYMBOL-YYYY-MM.parquet
```

A month file that already exists without a `.sources.json` sidecar, or whose
sidecar lists different archives, is left in place. `sync --rebuild` replaces
it. Views list only `SYMBOL-YYYY-MM.parquet` files that have a sidecar, so a
legacy file in the same directory is not read.

Every Binance file has `ts` plus the native time column. `ts` is UTC and naive
in DuckDB (`SET TimeZone='UTC'`). For klines, mark, index, and premium, `ts`
is `close_time` (the bar is closed and usable for a decision). For aggTrades,
`ts` is `transact_time`. For funding, `ts` is `calc_time`. For metrics, `ts`
is `create_time`.

The core manifest is the warehouse already described in
[hist-archives-research-warehouse.md](hist-archives-research-warehouse.md)
(BTCUSDT spot and USD-M from 2025-09, with a daily tail). Older 1m klines,
funding from 2020-01, mark/index/premium 1m, and USD-M metrics are in the
manifest with `enabled = false`. Turn one on by id:

```bash
PYTHONPATH=src uv run --frozen python -m research.hist_etl plan \
  --dataset bn-um-btcusdt-funding-2020
```

Metrics are daily-only. Funding is monthly-only. Metrics samples are treated
as a 5-minute grid. Funding holes are gaps larger than the row's
`funding_interval_hours`.

## Kraken OHLCVT

[Downloadable historical OHLCVT](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data)
ships quarterly zips. Rows are `timestamp,open,high,low,close,volume,trades`
with no header and Unix seconds. Only selected pairs are read (default
`XBTUSD`). Other pairs in the zip are ignored. Output:

```text
parquet/hist_etl/kraken/ohlcvt/XBTUSD/{1m|5m|15m|30m|1h|4h|12h|1d}/YYYY-MM.parquet
```

`ts` is the OHLCVT candle-open timestamp. The bar's close is `ts` plus the
interval. Sparse minutes are kept as published.

Put zips under `kraken-ohlcvt/Kraken_OHLCVT*.zip`. An optional `url` in the
manifest downloads one https zip when that file is absent. Kraken does not
publish a SHA256 sidecar; the zip must open and contain the selected CSV.

## Catalog

`catalog` rewrites only the marked block in `catalog.sql`. Default view names
do not collide with the warehouse views Quant already uses:

- `hist_bn_{market}_{symbol}_{slug}`, for example `hist_bn_um_btcusdt_klines_1h`
- `hist_kr_ohlcvt_{pair}_{interval}`, for example `hist_kr_ohlcvt_xbtusd_1d`

`hist_bn_um_klines_1h`, `hist_bn_spot_aggtrades`, `hist_bn_um_funding`, and
`hist_kr_xbtusd_1d` stay where they are. `sync --replace-legacy-views` or
`catalog --replace-legacy-views` writes `catalog.sql.bak.<UTC timestamp>`,
prints a unified diff, and then lets the pipeline also publish those short
names. SQL outside the block, including `live_*` views, is otherwise kept.
Placeholder `__HIST__` is the archive root. Views are not created for empty
datasets. Each view is an explicit file list, not a directory glob.

`catalog.sql` is replaced only after DuckDB accepts the new block. If
`research.duckdb` is locked, the command retries and then exits with a message
that names the lock. Close the other DuckDB session and run `catalog` again.
A legacy `parquet/kraken/xbtusd_1d.parquet` is not deleted.

Existing zips are reused when their path contains the dataset, the interval,
and either `spot` or `um` / `futures-um`. A bare filename match is not reused,
so a zip outside that layout is downloaded into `binance-vision/data/...`
instead of replacing the original.

## Example timer

Do not install this from the repo. It is an operator example for a daily
incremental sync after the capture window, with a low priority and the disk
floor. It does not stop `hl-capture`, `bn-capture`, or any other tmux session.

```ini
# ~/.config/systemd/user/hist-etl.service
[Service]
Type=oneshot
WorkingDirectory=%h/Hyperliquid Project/Hyperliquid-Bot
Environment=HIST_ARCHIVES_ROOT=%h/Hyperliquid Project/hist-archives
Environment=HIST_ETL_MIN_FREE_BYTES=10737418240
ExecStart=/usr/bin/env bash -lc 'cd "$HOME/Hyperliquid Project/Hyperliquid-Bot" && PYTHONPATH=src uv run --frozen python -m research.hist_etl sync'
Nice=10
```

```ini
# ~/.config/systemd/user/hist-etl.timer
[Timer]
OnCalendar=*-*-* 04:30:00 UTC
Persistent=true
Unit=hist-etl.service
```

A cron line with the same command:

```cron
30 4 * * * cd "$HOME/Hyperliquid Project/Hyperliquid-Bot" && HIST_ARCHIVES_ROOT="$HOME/Hyperliquid Project/hist-archives" PYTHONPATH=src uv run --frozen python -m research.hist_etl sync
```

Exit 2 means the gap report is non-empty. Exit 3 means the disk floor refused
the run. Neither retries by deleting data.
