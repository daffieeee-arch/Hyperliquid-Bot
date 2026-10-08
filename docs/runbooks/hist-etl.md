# Historical archive ETL

PAPER / free public data only. This pipeline does not place orders, read
exchange keys, or attach to capture tmux sessions. Hyperliquid funding comes
from the public info endpoint, which needs no key.

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
parquet/hist_etl/binance/{spot|um}/{klines_1m|klines_1h|klines_1d|aggtrades|funding|mark_klines_1m|index_klines_1m|premium_klines_1m|metrics}/SYMBOL-YYYY-MM.parquet
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
USD-M 1h klines from 2020-01 (`bn-um-btcusdt-klines-1h-2020`, read by
[exp_tsmom_btc](../experiments/exp_tsmom_btc.md)), funding from 2020-01,
mark/index/premium 1m, and USD-M metrics are in the manifest with
`enabled = false`. Turn one on by id:

```bash
PYTHONPATH=src uv run --frozen python -m research.hist_etl plan \
  --dataset bn-um-btcusdt-funding-2020
```

Metrics are daily-only. Funding is monthly-only. Metrics samples are treated
as a 5-minute grid. Funding holes are gaps larger than the row's
`funding_interval_hours`.

## Binance USD-M universe

Cross-sectional studies need every contract that traded at each date, not the
ones that trade today: a universe drawn from today's listings leaves out the
contracts that died (LUNAUSDT, SRMUSDT, FTTUSDT) and is survivorship-biased.
Binance Vision keeps the archives of delisted contracts, and the S3 bucket
behind it lists them.

`universe` writes that list as a new, dated JSON file. It needs no archive
root and no manifest:

```bash
PYTHONPATH=src uv run --frozen python -m research.hist_etl universe \
  --today 2026-10-08 \
  --out config/hist_etl/universe/binance-um-usdt-1d-2026-10-08.json
```

- It lists `data/futures/um/monthly/klines/` and `.../fundingRate/` with the
  public ListObjects (v1) XML of
  `s3-ap-northeast-1.amazonaws.com/data.binance.vision`. There are no
  credentials and nothing is downloaded.
- It keeps symbols that end with `--quote` (default `USDT`). Other quotes
  and dated delivery contracts (`BTCUSDT_250926`) are skipped. A USDT name
  the manifest cannot hold (not 2 to 20 of `A-Z0-9`, such as a Chinese
  name) is recorded under `excluded` with the reason.
- Per symbol, it records the runs of consecutive months with a monthly
  `--interval` kline zip (default `1d`) and a monthly fundingRate zip.
- `latest_month` is the newest month the planner expects by `as_of`. A run
  that reaches it is still published. So is a run that ends the month
  before, while no series of its symbol has `latest_month` yet: Binance can
  still be uploading that month after the first Monday, and an open run that
  has ended shows up as a gap, while a closed one that still trades would
  lose its data silently. In the 2026-10-08 file, seven funding runs end in
  2026-08 while their klines reach 2026-09, so they are closed.
- It never replaces an existing file; a refresh is a new dated file.
- The regional endpoint now and then answers `NoSuchBucket` for this bucket
  (about one listing in eight on 2026-10-08). That answer, a dropped or
  garbled body, and a 5xx or 429 are retried up to ten times per page; any
  other answer, or the tenth failure, fails the run with exit 2.
- On 2026-10-08 it found 901 USDT names and scanned 896 in about 13
  minutes from a cloud container, at 8 requests per second. The default
  rate is `--requests-per-second 4`.
- That file holds those 896 symbols: 22,221 kline months and 21,063 funding
  months. Five Chinese names are excluded. BNTUSDT, BTCSTUSDT and LITUSDT
  have more than one run, and GAIBUSDT has funding but no 1d klines.

The manifest expands the committed file:

```toml
[[binance_universe]]
id = "bn-um-usdt-1d"
file = "universe/binance-um-usdt-1d-2026-10-08.json"  # below the manifest directory
datasets = ["klines", "fundingRate"]                   # default: both
# start = "2021-01-01"                                 # optional cut, first of a month
enabled = false                                        # default: false
```

- Each symbol, dataset, and run becomes one Binance dataset, with id
  `bn-um-usdt-1d-klines-btcusdt` or `bn-um-usdt-1d-funding-btcusdt`. A
  relisted symbol's later runs add `-r2`, `-r3`.
- A still-published run keeps `end = "today"`, with a daily kline tail.
  Any other run ends on the last day of its last month.
- **Listing edges**: a run's first month may start late and a closed run's
  last month may end early, because the contract was listed or delisted
  then. Only those outer bars may be missing. A hole between two bars is
  still a `kline_hole`. `start` must be the first of a month. A `start`
  after a run's first month removes that run's late-start allowance,
  because the contract already traded then.
- A break between runs is an archive fact, not proof of a relisting.
  LITUSDT funding stops after 2025-06 and resumes in 2025-12 while its
  klines continue. The universe does not say whether that is a hole, a
  relisting, or another asset under the same ticker. The
  [cross-sectional panel](../research/cross-sectional-panel.md) restarts
  its price windows after every untraded day, so no return spans the break.
- Two datasets that plan the same archive (a universe and a BTCUSDT
  dataset) download it once in a sync, and a failure is not retried for the
  second.
- `--dataset bn-um-usdt-1d` selects every dataset of the entry, even while
  it is disabled. A single id selects one.
- Views follow the usual names, one per symbol: `hist_bn_um_btcusdt_klines_1d`
  and `hist_bn_um_btcusdt_funding`. Universe funding months for BTCUSDT are
  the same files the BTCUSDT funding datasets write.

**An archive month is not a trading month.** After a delisting, Binance
Vision can keep publishing monthly klines with a flat price, zero volume and
zero trades, and funding at a constant default rate:

- SRMUSDT klines run to 2024-05 and its funding to 2024-07. Its 2024-05
  bars all close at 0.2870 with volume 0, and its 2024-07 funding is
  0.0001 at every settlement.
- On 2026-10-08, 864 symbols had 1d klines through 2026-09 but only 738 had
  funding through that month. The 147 symbols whose kline and funding runs
  end in different months include 1000XUSDT: it traded until late 2025 and
  has flat, zero-volume bars in 2026-09.

So "still published" is not "still listed". The reader of the bars decides
whether a contract traded on a date, from volume and trade count, and must
not charge or credit funding for a date it did not trade.

The file holds every symbol with archives. That includes index contracts
(`BTCDOMUSDT`, `DEFIUSDT`) and later non-crypto contracts. The study chooses
its universe point in time, for example the top N by trailing quote volume
on each date, and writes that rule down in its pre-registration. It does not
drop symbols by hand after seeing results.

A full sync is VPS work: about 43,000 monthly zips (each with a checksum),
plus the daily kline tails, all small. `plan` sends a HEAD for every archive it
would download, and `sync` two GETs, at `requests_per_second` (2 by default).
Raise it with `HIST_ETL_REQUESTS_PER_SECOND` for this one run. A contract
delisted after `latest_month` stops publishing daily files, which `plan`
reports as absent until a newer universe file replaces this one.

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

Look-ahead: Kraken `ts` is the candle open and Binance kline `ts` is
`close_time`. Joining those columns compares an open Kraken bar with a closed
Binance bar. Add the Kraken interval (`ts + INTERVAL 1 DAY` for `1d`, and the
matching interval for the other bars) when the join should use the Kraken
decision time.

Put zips under `kraken-ohlcvt/Kraken_OHLCVT*.zip`. An optional `url` in the
manifest downloads one https zip when that file is absent. Kraken does not
publish a SHA256 sidecar; the zip must open and contain the selected CSV.

## Hyperliquid funding

Source: the public info endpoint `fundingHistory`
([docs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)),
a JSON POST to `https://api.hyperliquid.xyz/info` with no key or account. It
returns `{coin, fundingRate, premium, time}` rows, oldest first, at most 500
per response; `startTime` and `endTime` are inclusive milliseconds. The ETL
pages from the last returned time, one UTC month at a time, and reads complete
UTC days only (a day is fetched after it ends).

REST requests share 1200 weight per minute per IP. An info request weighs 20
and `fundingHistory` adds 1 per 20 rows, so a full page is 45. The manifest
`requests_per_second` is capped at 0.3 for this endpoint (810 weight a
minute, leaving room for anything else on the IP), and a rate of 0 does not
lift the cap. After a 429 the client waits a full minute before it retries
([rate limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)).
The first backfill of BTC (about 29,000 rows from 2023) takes roughly 60
requests, a few minutes; a daily sync after that takes one or two.

Layout:

```text
hyperliquid-api/funding/{dataset_id}/{COIN}-funding-YYYY-MM.json       # settled month, written once
hyperliquid-api/funding/{dataset_id}/{COIN}-funding-YYYY-MM.open.json  # current month, rewritten each sync
parquet/hist_etl/hyperliquid/funding/{COIN}/YYYY-MM.{dataset_id}.parquet
```

A month settles only once it has ended and its fetch has a print in every
settlement slot apart from `known_holes`. A month with a hole the manifest
does not list, or with a conflict, never settles: a lagging node can return
the same truncated answer twice. A settled raw file is canonical JSON of the
rows as published, and it is reused, not fetched again (move it aside to
refetch a month). Every other month, the current one or an ended month that
has not settled, is fetched again on every sync and written to `.open.json`,
so a truncated answer is never frozen. A real venue hole therefore costs one
or two requests per sync until it is added to `known_holes`. The Parquet
sidecar of such a month says `"provisional": true`, and that file is
replaced on each sync until the month settles. Settled month files follow
the same `.sources.json` rule as the other venues (`sync --rebuild` to
replace). `.open.json` files are not deleted.

Each raw file and Parquet sidecar records the window it covers (`start_ms`,
`end_ms`). A month file whose sidecar does not record it is rewritten from the
same raw file by the next sync; `verify` reports it as `hyperliquid_sidecar`
until then. When the dataset's `start` or `end` changes, a settled month whose
window no longer matches is not overwritten: `sync` and `verify` report
`hyperliquid_window_changed` and `plan` lists it as `window_changed`, until
the raw file is moved aside. The next `sync --dataset <id> --rebuild` then
fetches the month and replaces its Parquet file. `--today` can move the
cutoff back for a test, never past the real UTC date. A month on disk that
already reaches past an earlier `--today` (a later sync fetched it) is left
as it is, never shrunk: `sync` and `verify` report it as
`hyperliquid_ahead_of_today` and `plan` lists it as `ahead`. Test an earlier
cutoff with a scratch `--root`; without `--today`, check the system clock.
`verify` judges an ended month over all its slots, whatever its file covers.

Columns: `ts` (the settlement time, UTC; the rate is known and charged then),
`slot_start` (the start of the settlement slot the print belongs to),
`funding_time_ms`, `coin`, `funding_rate`, `funding_rate_text` (the published
decimal), `premium`, `premium_text`, `funding_interval_hours`, `dataset_id`,
`source_name`. `funding_rate` is the rate for one settlement: per hour from
2023-06-08, per 8 hours before.

Gaps are judged per settlement slot, not by timestamp spacing: settlement
times jitter by about a second, and a late settlement can land minutes into
its slot. A print belongs to the slot that starts at most 60 seconds after
it, so one stamped just before the hour still counts for that hour, and a
month covers the slots that start inside it. A slot with no print is a
`funding_hole` (exit 2, the month is still written, provisionally). Two prints
in one slot, or two different prints at one time, is a `funding_conflict`,
and that month is not written. `known_holes` in the
manifest lists slots the venue never published, so they are not reported on
every run. For BTC these are 2023-07-02 20:00, 2023-08-23 20:00, and
2024-08-15 13:00 UTC, checked against `fundingHistory` on 2026-10-07. Any
other missing slot is a gap.

The venue settled every 8 hours until 2023-06-07 and hourly from 2023-06-08,
so the manifest has two datasets for BTC. `hl-perp-btc-funding` (hourly) is
enabled; `hl-perp-btc-funding-8h` is opt-in:

```bash
PYTHONPATH=src uv run --frozen python -m research.hist_etl sync --dataset hl-perp-btc-funding-8h
```

Datasets for one coin may not cover the same day. Both write to the same coin
directory and to the one view `hist_hl_funding_{coin}`. The view reads only
the month files the manifest selects: the coin's datasets, and only a file
whose sidecar covers its month's window as the manifest defines it (a
settled file exactly; a provisional one from the start of that window, the
first of the month or the dataset's `start`, up to the day it was fetched).
No date is read, so `--today` does not change the views. Files of a renamed,
removed, or re-ranged dataset stay on disk but out of the view, so they
cannot charge a bar twice. When no file of a coin qualifies, or the coin's
datasets were removed from the manifest, its view is dropped, so a query
fails instead of reading stale rows; the next sync that selects a month file
creates it again. The exception is a name the operator also declares outside
the generated block. Like any such view, it is then not generated again until
`--replace-legacy-views`, and the drop also removes a view the operator ran
under that name; its statement stays in `catalog.sql`. Do not declare
`hist_hl_funding_*` names by hand. A month that is not selected is missing
from a view that still has other months, like a hole: `sync` and `verify`
report it.

For a harness `role: funding` column on hourly bars stamped at their close,
the settlement printed at the bar's close belongs to that bar:

```sql
SELECT bar.ts, bar.close, coalesce(funding.funding_rate, 0.0) AS funding_rate
FROM hourly_bars AS bar
LEFT JOIN hist_hl_funding_btc AS funding ON funding.slot_start = bar.ts
```

Bars stamped at their open (like Kraken OHLCVT) add the interval first.
Coarser bars sum the settlements inside each bar. `coalesce` charges no
funding to a bar without a settlement, so check the study range first; apart
from `known_holes`, this should return no rows:

```sql
SELECT bar.ts
FROM hourly_bars AS bar
LEFT JOIN hist_hl_funding_btc AS funding ON funding.slot_start = bar.ts
WHERE funding.slot_start IS NULL
```

## Catalog

`catalog` rewrites only the marked block in `catalog.sql`. Default view names
do not collide with the warehouse views Quant already uses:

- `hist_bn_{market}_{symbol}_{slug}`, for example `hist_bn_um_btcusdt_klines_1h`
- `hist_kr_ohlcvt_{pair}_{interval}`, for example `hist_kr_ohlcvt_xbtusd_1d`
- `hist_hl_funding_{coin}`, for example `hist_hl_funding_btc`

`hist_bn_um_klines_1h`, `hist_bn_spot_aggtrades`, `hist_bn_um_funding`, and
`hist_kr_xbtusd_1d` stay where they are. `sync --replace-legacy-views` or
`catalog --replace-legacy-views` writes `catalog.sql.bak.<UTC timestamp>`,
prints a unified diff, and then lets the pipeline also publish those short
names. SQL outside the block, including `live_*` views, is otherwise kept.
Placeholder `__HIST__` is the archive root. Views are not created for empty
datasets. Each view is an explicit file list, not a directory glob.

Before it applies a view, `catalog` reads live relations from `duckdb_views()`
and `duckdb_tables()`. A name that exists in `research.duckdb` and is not in
the previous hist_etl block is foreign. The command skips that `CREATE OR
REPLACE`, prints a warning, and exits 2. `--replace-legacy-views` is the
explicit opt-in that replaces it, after the catalog backup and diff.

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
