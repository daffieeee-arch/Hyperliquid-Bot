# Hypothesis harness

PAPER research only. The harness evaluates a hash-locked hypothesis. It does
not place orders, and `promotion_decision: paper_candidate` does not authorize
LIVE, SHADOW, or TESTNET.

```bash
cd "$REPO_ROOT"
PYTHONPATH=src uv run --frozen python -m research.harness lock path/to/spec.yaml
PYTHONPATH=src uv run --frozen python -m research.harness run path/to/spec.yaml \
  --output-dir path/to/out
```

`lock` writes `spec.yaml.lock.json` with the canonical sha256 and a data
fingerprint. The fingerprint records, per input, the row count, the minimum
and maximum timestamp, and a content checksum. Parquet checksums are the
sha256 of the file bytes. A DuckDB view checksum is a sha256 of row-hash
aggregates over the declared columns, so an unrelated catalog table does not
change it. `run` refuses the
job if the spec or the fingerprint changed after that lock. Re-lock only when
you intend a new pre-registration. The artifact repeats both digests.

DuckDB catalogs are not hard-coded. For `data.backend: duckdb` set
`RESEARCH_DUCKDB_PATH` to the catalog file, or set `HIST_ARCHIVES_ROOT` to the
warehouse directory that contains `research.duckdb`. Parquet specs use a
relative `parquet_path` next to the spec. Do not commit Parquet, ZIP, CSV, or
DuckDB files.

## What you pre-register

See [examples/wp-template.spec.yaml](examples/wp-template.spec.yaml). Required
fields:

- `hypothesis_id`, `universe`, `dataset_version`, `h0`, `h1`, `alpha`
- `selection_method`: `bonferroni` (always reported), or `holm` / `bh`
- one signal feature, known at a declared availability clock
- one or more configs (`threshold`, `horizon_bars`; for a panel `quantile`
  instead of `threshold`) — this is the whole grid
- costs in bps (`fee_bps`, `slippage_bps`, `spread_bps`) and `latency_bars`.
  `spread_bps` is the half-spread per side, not the full quoted spread
- optional `costs.funding_column` for a perp: see [Funding](#funding)
- optional `sizing`: `unit` (the default) or `vol_target`, see [Sizing](#sizing)
- optional `portfolio` with `data.backend: panel`, see [Panel portfolios](#panel-portfolios)
- walk-forward `expanding` or `rolling`, plus a final `holdout_bars` suffix
- sample floors for folds and for validation / holdout trade counts

The harness does not search parameters and does not fit anything on the train
window. Train length only delays the first test fold. Configs are scored on
the walk-forward **test** folds. The selected config, if any, is scored once
on the untouched holdout. If validation selects no config, the holdout is not
read: `result.json` has `holdout: null` and `result.md` says
`holdout sealed (not evaluated)`. Holdout prices are not readable from a
validation trade. A `holdout_bars` suffix that covers the whole series fails
closed instead of treating every row as holdout.

Labels:

| Label | Meaning |
| --- | --- |
| `not_enough_data` | Not enough folds or trades to decide |
| `no_edge` | Enough data, and no config survived costs and multiple testing |
| `interesting_but_fragile` | A gross, unadjusted, or in-sample result died on costs, stress, multiplicity, or the holdout |
| `passes_h1` | The validation-selected config kept a positive mean net on the holdout at 1.0×, 1.5×, and 2.0× cost. The p-value is the larger of the iid t and a Newey-West HAC t, and it is ≤ alpha |

`promotion_decision` is `forbidden` unless the label is `passes_h1`. The pass
value is `paper_candidate`: eligible for a later PAPER review, not a trading
authorization.

Gross and net means are both in `result.json` and `result.md` when the holdout
was evaluated. Gross is the price return. Net subtracts the round trip
`2 * (fee_bps + slippage_bps + spread_bps) / 10000`, multiplied by the stress,
and adds the funding cashflow when a funding column is declared. With a
position weight `w` (1 unless `sizing` says otherwise), a trade's net return
is `w * (price_return - round_trip * stress - paid * stress + received / stress)`:
each funding payment the trade made is multiplied by the stress and each one
it received is divided by it.
`spread_bps` is the half-spread per side, so the round trip charges it on
entry and again on exit. Latency is not a bps add-on. A fill uses the close of
`decision_bar + latency_bars`, and the exit is `horizon_bars` later. When the
signal's availability clock is the bar timestamp column, `latency_bars` must
be at least 1. `latency_bars: 0` is accepted only with
`costs.allow_zero_latency: true`, and that flag is printed in the report.

Each metric block reports the iid one-sided t and a Newey-West HAC t
(Bartlett kernel, lag `floor(4 * (n / 100) ** (2 / 9))`). Bonferroni, Holm,
and BH, and the holdout check, use the larger of the two p-values.

## Funding

A perp position pays or receives funding while it is held. Declare one column
with `role: funding` and name it in `costs.funding_column`. A funding-role
column that is not named there is refused, so a funding series is never
silently ignored. Without one, no funding is accrued.

The value at bar `t` is the funding rate a long pays for holding over that
bar, settled at the bar's timestamp (positive: longs pay, shorts receive). If
the venue settles more often than once per bar, the ETL sums the rates inside
the bar; if less often, bars without a settlement carry 0. A trade filled at
the close of bar `entry` and exited at the close of bar `exit` holds bars
`entry + 1` through `exit`: it pays each of those rates, on the notional at
that bar's close (`price[t] / price[entry]` per unit of entry notional), and
nothing for the entry bar. A short receives the same amount. The realized
funding is exact in a backtest, but it is a market cashflow that need not
repeat, so the stresses treat it adversely, payment by payment: at 1.5x and
2.0x, every bar's funding paid is multiplied by the stress and every bar's
funding received is divided by it, so payments inside one trade do not net
each other out first. The stress sees the per-bar values, so with bars coarser
than the venue's settlement interval the payments inside one bar are netted
before it; use bars no coarser than the settlement interval when the stress
matters. An edge that rests on received funding must survive that haircut
too. Each config and the
holdout report a separate `funding` block (the realized per-trade funding
cashflow, at 1.0x) next to gross and net.

Funding is realized, so it needs no availability clock. A signal that uses a
funding *forecast* is a feature like any other, with its own availability
clock.

## Sizing

`sizing` is optional. `method: unit` (the default) trades one notional unit.
`method: vol_target` weights each trade:

```yaml
sizing:
  method: vol_target
  vol_feature: realized_vol   # a declared feature, so its clock is audited
  target_vol: 0.02
  max_leverage: 3.0
```

The weight is `min(max_leverage, target_vol / vol)`, where `vol` is the
`vol_feature` value at the decision bar, like the signal. The volatility must
be positive on every row: a zero or negative value anywhere fails the run
closed (`failure_kind: sizing`), not only where a trade sizes, so trim warm-up
rows in the ETL instead of zero-filling them. With `latency_bars: 0`, a
volatility stamped with the bar timestamp needs `allow_zero_latency: true`,
as the signal does. `target_vol` is in the units of `vol_feature`: if that is
a per-bar return stdev, so is the target. Gross, costs, and funding all scale
with the weight, and each config and the holdout report `mean_weight`.
Volatility scaling changes what the t-test measures (risk-scaled returns per
trade), which is the point of pre-registering it.

## Panel portfolios

A spec with `data.backend: panel` scores a cross-sectional portfolio over the
[cross-sectional panel](cross-sectional-panel.md) instead of one bar series.
See [examples/panel-template.spec.yaml](examples/panel-template.spec.yaml).
The panel has one row per symbol and day; the harness splits, gates and
labels exactly as for a bar series, with one trade per rebalance period.

```yaml
data:
  backend: panel
  parquet_path: panel.parquet
  timestamp_column: ts
  symbol_column: symbol        # role: symbol, dtype string
  price_column: close
  traded_column: traded        # role: traded, dtype bool
  rank_column: volume_rank     # role: rank, dtype int64
  # with costs.funding_column: one role: covered column (dtype bool)
  max_gap: 86400000            # between consecutive days of the date axis
  max_rows: 2000000
  columns: ...
portfolio:
  universe_size: 50            # rank at most 50 on the decision day
  min_names_per_leg: 5
configs:
  - id: q20-h7
    quantile: 0.2              # per leg, in (0, 0.5]
    horizon_bars: 7
```

- **Period**: decided on one day of the date axis, filled `latency_bars`
  days later at that day's close, exited `horizon_bars` days after the fill.
  The next decision is the exit day, so periods do not overlap, like a bar
  series' trades. A validation period never reads a holdout close. The
  rank and the traded flag are the decision day's own close, so
  `latency_bars` must be at least 1 unless `allow_zero_latency` is set.
- **Universe and legs**: on the decision day the universe is every symbol
  that traded, has a rank at most `universe_size` and a known signal;
  funding plays no part in it. Sorted by the signal (ties by symbol), the
  top `quantile` of the universe is the long leg and, under
  `direction: signed`, the bottom `quantile` the short leg; `long_only`
  holds the long leg alone. It is one ranking, so the legs are disjoint:
  ties go to the symbol that sorts first, which puts the alphabetically
  first of tied names in the long leg's top and the alphabetically last in
  the short leg's bottom. Both legs have `floor(universe × quantile)`
  names, with the quantile as written in the spec (100 names at 0.29 give
  29), at least `min_names_per_leg` at the decision and again at the fill,
  or the day is skipped. A config whose quantile cannot fill a leg from a
  full universe is refused at validation.
- **Return and costs**: equal weight within a leg; each leg holds half the
  capital under `signed`, the long leg all of it under `long_only`. The
  period's gross return is the capital-weighted sum of its positions'
  returns and its weight the gross exposure (1.0), so the round trip is
  charged once on the capital per period, as each position pays entry and
  exit on its notional. `sizing` must be `unit`.
- **Funding**: the long leg pays each held day's rate on the notional at
  that day's close and the short leg receives it, position by position, so
  the stress treats each payment adversely as for a bar trade. With
  funding, the panel declares one `role: covered` column (the panel's
  `funding_covered`).
  - A held **traded** day with **no rate** fails the run closed
    (`failure_kind: funding`). The panel builder fails on a funding hole
    inside a funding run, so this happens only on a traded day outside one
    (a listing month before funding starts, or trading after a funding
    archive ends); a study's panel range and universe must not hold a
    position across such a day. A held **halt** day with no rate, which
    the builder does not check, is charged nothing and counted as
    uncovered.
  - A held day whose rate is there but **not covered** (at most one
    settlement missing, or an interval switch the panel cannot tell apart,
    see the panel's limitation) is charged its recorded sum, and the
    report counts such position-days per config as
    `uncovered_funding_days`. The error is bounded by one settlement per
    counted day. A pre-registration should say how many it tolerates.
- **Halts and delistings while held**: a position is held to the period's
  exit day whatever happens in between, so no exit uses knowledge of a later
  day. It exits at that day's close when the symbol trades then; otherwise
  at its last traded close at or before the exit day (no row, or `traded`
  false), the one price a holder of a halted or delisted contract has, and
  the period counts a forced exit. That is not the price a holder got at
  the delisting; a study must say what it assumes. A day without a row
  ends the contract: the panel builder fails on a day missing inside a run
  and keeps a gap only between the runs of a relisted symbol, whose rows
  after the gap are another listing, so the hold stops at the last traded
  close before the gap and never marks at the relisted price. A day with a
  row that did not trade is a halt, held through. Funding is charged on
  every held day up to that last traded day, so a halt that resumes pays
  its days and a delisting pays nothing after its last trade (not on the
  flat archive days that follow it). `forced_exits` is the trace of both.
  The one case the data cannot tell apart is a relisting whose archive
  follows the old contract's without a missing day; it reads as a halt. A
  symbol that does not trade on the fill day, or whose rows break between
  the decision and the fill, is not opened, and its leg is spread over the
  names that filled; a period with a leg below the floor at the fill is
  skipped.
- **Report**: `result.json` has a `portfolio` block with the universe rule,
  `symbol_count`, and per config the validation (and, when scored, holdout)
  period count, skipped decisions, mean names per leg, forced exits and
  uncovered funding days;
  `result.md` has a Portfolio section. `bar_count`, `timestamp_min` and
  `timestamp_max` describe the date axis (one bar is one day of the panel,
  not one row); the fingerprint's `row_count` is the rows read. Each
  config reports its `quantile`
  and a null `threshold` (a bar series reports the reverse). The
  buy-and-hold benchmark does not apply: both windows report
  `not_applicable`.
- **Nulls and ranks**: price, the traded flag, the symbol and every
  feature's clock must be present on every row; a rank is at least 1 and
  unique on its day. The signal, rank and funding may be null
  where the panel does not know them (warm-up, untraded days); a null is
  never read as zero. Every declared feature's availability clock is
  audited on every row.

## Overfitting diagnostics

`result.json` has an `overfitting` block, summarized in `result.md`. Both
values are diagnostics: they never change the label or the promotion
decision. Both count only the pre-registered grid as trials, so exploration
done outside the harness is not deflated. Neither is computed when
validation itself is `not_enough_data` (fewer than `sample.min_folds` folds,
or no config with `sample.min_trades_validation` trades); `note` then gives
the validation reason. A holdout short of trades leaves them in place, since
both describe validation.

- **Deflated Sharpe ratio** (Bailey and López de Prado, 2014). It tests the
  config validation selected, or, when nothing was selected, the config
  with the highest validation mean net per trade among those with at least
  `sample.min_trades_validation` trades (`selected` says which). Its
  validation net Sharpe per trade at 1.0x is set against the highest Sharpe
  that as many pure-noise trials would show. Every pre-registered config is
  a trial, as in the multiple-testing family, and under the null each
  trial's Sharpe has the sampling variance `1 / (T - 1)` of the tested
  config's `T` trades. `dsr` is the probability that the tested Sharpe beats
  that noise maximum, corrected for the skewness and kurtosis of its trade
  returns. Near 1 is good. Around 0.5 or lower, the config cannot be told
  apart from the best of noise. With one config the noise maximum is 0, and
  `dsr` is the probabilistic Sharpe ratio. When `dsr` cannot be computed,
  for example because there is no config to test or its trade returns are
  all equal, it is null and `note` says why.
- **Probability of backtest overfitting** (Bailey, Borwein, López de Prado
  and Zhu, 2017), by combinatorially symmetric cross-validation. The most
  recent walk-forward test folds are grouped into equal contiguous blocks:
  the even count from 4 to 16 that leaves out the fewest (oldest) folds, so
  at least 4 folds are needed. Each way to pick half of the blocks is a
  split, and it selects as validation does, from its in-sample half only.
  The candidates are the configs that meet `sample.min_trades_validation`
  pro-rated to the in-sample folds and rounded up (`in_sample_floor`). The
  candidate with the best in-sample mean net per trade at 1.0x (the
  statistic validation selection ranks by) is ranked among the candidates
  on the other half, where a candidate without trades earns 0. PBO is the
  share of splits where the pick ranks at or below the median. A split
  selects nothing and is skipped (`skipped_splits`) when it has fewer than
  two candidates, when every candidate ties in-sample, or when the best
  in-sample mean is not positive, as validation would select nothing then.
  The significance and stress gates are not re-run per split. Near 0 is
  good; 0.5 means picking the in-sample best is no better than chance. Read
  `value` together with `splits`: a PBO from a few splits, the rest skipped,
  says little.

## Benchmark

`result.json` has a `benchmark` block, summarized in `result.md`. It is
context: it never changes the label or the promotion decision. The method is
buy-and-hold, one unit long, priced like a strategy trade at unit weight.

- **Windows**: `validation` covers the walk-forward test folds, from the
  first fold's `test_start` to the last fold's `test_end`, as one position
  across fold boundaries, which no strategy trade does. `holdout` covers the
  holdout, but only when validation selected a config; while the holdout is
  sealed, the benchmark does not read it either.
- **Status**: each window has a `status`: `evaluated` (with the values
  below), `sealed`, `no_folds`, `not_applicable` (a panel portfolio, with a
  `note`), `too_short` (no close left to exit at after
  the fill), or `error`. A numeric failure (overflow, a non-finite value)
  is recorded as `error` with a `note` and never fails the run or changes
  its label. A window outside the table is a harness bug that the
  strategy's indexes share, so it still fails the run closed.
- **Trade**: decided at the window's first close and filled `latency_bars`
  later, like a strategy trade, then held to the window's last close.
  `bars_held` counts the bars from the fill to the exit. `gross_return` is
  the price return and `log_return` its log.
- **Funding**: both fields are positive when funding is received, like the
  strategy's `funding` block. When a funding column is declared, `funding` is
  the realized cashflow of a fixed quantity, paid on the notional at each
  held bar's close, as for a strategy trade; `net` uses it.
  `funding_constant_notional` is the same bars' cashflow at a constant
  notional: the negated sum of their rates. Both are `null` without a
  funding column.
- **Net**: one round trip plus funding, at 1.0x, 1.5x and 2.0x, stressed as
  for a strategy trade.
- **Per bar**: `mean_log_return_per_bar`, `stdev_log_return_per_bar` and
  `sharpe_per_bar` use the log returns between consecutive closes from the
  fill to the exit. They are not annualized, because the harness does not assume a
  bar length.

The strategy metrics are per trade and risk-scaled; the benchmark is one
unscaled position. Compare them with care, and remember that the best of
several configs on the same data is not an out-of-sample comparison.

## Provenance

The harness reads the provenance once, before the spec. `source_environment`
comes from `RESEARCH_ENV` (`DEV` by default, or `CI` or `VPS_RESEARCH`).
`source_commit` is `git rev-parse HEAD`, or `null` outside a checkout. A run
that fails closed later still records all three. A run refused before or
by the provenance check (unsafe mode, a malformed `RESEARCH_ENV` or
`RESEARCH_IMAGE_DIGEST`) records all three as `null`; its `reasons` name the
refusal.

`image_digest` comes from `RESEARCH_IMAGE_DIGEST` and is `null` when that is
unset. Once set, it must be `sha256:` followed by 64 lowercase hex digits;
anything else, including an empty value, fails the run closed with
`failure_kind: data_config`, so a broken image template never records a
wrong digest. No image build sets it yet: runs from a checkout record
`null`.

## Point-in-time checks

Point-in-time checks fail closed (no statistical label) for schema mismatch,
nulls, duplicate or backwards timestamps, gaps above `max_gap`, non-positive
prices, and an availability clock after the bar. `max_rows` caps the pull
(hard ceiling 2,000,000). A raw aggTrades scan must be aggregated to bars
first. Empty intervals are not zero-filled.

## Bar tables

`research.bar_tables` builds the Parquet table a spec reads from hist_etl
output, so its features are point-in-time by construction:

```bash
PYTHONPATH=src uv run --frozen python -m research.bar_tables trend \
  --root "$HIST_ARCHIVES_ROOT" --market um --symbol BTCUSDT --interval 1h \
  --start 2020-01-01 --end 2026-10-01 \
  --lookbacks 168,672,2016 --vol-window 168 --out path/to/bars.parquet
```

`trend` writes one row per bar. `ts` is the kline close time in milliseconds.

- `close`.
- `ret_<L>`: the log return over the last `L` bars, one column per lookback.
- `trend_score`: the mean sign of those returns, in [-1, 1].
- `realized_vol`: the sample stdev of the last `--vol-window` one-bar log
  returns, per bar.
- `funding_rate`: the Binance settlements in `(previous close, close]`,
  summed, which is the harness funding convention.
- `funding_tilt_<K>`, only with `--funding-means K1,K2,...` and
  `--funding-baseline B`: `B` minus the mean of the last `K` funding
  settlements at or before the bar's close, one column per `K`. It is
  positive when longs paid less than the baseline, so under
  `direction: signed` a positive threshold goes long when funding is low and
  short when it is high. A settlement that lands exactly on a close counts
  for that bar. The mean may reach back before the first output bar; the
  funding read reaches back too. The settlements any mean reads must share
  one `funding_interval_hours` (a missing value reads as 8, as in hist_etl),
  since one baseline cannot fit 8h and 4h rates. Each must follow the last
  within that interval, give or take a minute, so a missing settlement or a
  mislabelled interval fails closed. Older settlements no mean reads are not
  checked, and a tilt table cannot span an interval switch. Without the two options the
  table is unchanged.
- `available_ts`, equal to `ts`. Every value uses closes and settlements at or
  before its bar, so a spec reading it needs `latency_bars >= 1`.

Bars with a close in `[start, end)` are read. The first `max(longest
lookback, vol window)` bars are warm-up and are not written. A missing bar, a
non-positive close, or more than `--max-funding-gap-hours` (default 9) without
a funding settlement fails closed and writes nothing. Set `--end` to fix the
table's range, since the open month changes with every sync.

## Port a work package

Build a bar table in the warehouse, then point the spec at that view. The
harness will not invent features from 615M raw prints.

Confirm column names against the catalog (`DESCRIBE view`). Official raw
layouts below are the upstream files, not a promise that `hist_*` views kept
those names.

### WP1 — taker flow × volatility (Binance USD-M aggTrades)

Aggregate USD-M aggTrades to the horizon you declared (for example 1 minute
or 1 hour) before the harness. A convenient pre-registered feature is taker
imbalance inside the bar, known at bar close (`available_at_column` = bar
timestamp), crossed with a trailing realized-vol bucket from USD-M klines.
Keep spot out of this feature.

Official USD-M Vision aggTrades are the `/fapi/v1/aggTrades` layout: aggregate
trade id, price, quantity, first trade id, last trade id, timestamp, was the
buyer the maker. The published example timestamp is milliseconds. There is no
"best price match" column. Spot aggTrades are a different file: eight columns
including best-price match, and spot timestamps from 2025-01-01 are
microseconds. USD-M klines follow `/fapi/v1/klines` (open, high, low, close,
volume, close time, quote volume, trade count, taker buy base, taker buy
quote, ignore).

Sources:
[binance-public-data README](https://github.com/binance/binance-public-data/blob/master/README.md),
[data.binance.vision](https://data.binance.vision/).

### WP2 — spot versus USD-M flow

Spot and USD-M prints are different instruments and, from 2025-01-01, different
timestamp units. Build one bar per clock you choose, carry both flow features
as separate columns, and pre-register a single signal column (for example a
spot-minus-USD-M imbalance). Do not substitute a spot price into a USD-M
return. Quote both series as USDT; that still does not make them one market.

### WP3 — Kraken thin liquidity

Downloadable Kraken OHLCVT rows are `timestamp, open, high, low, close,
volume, trades` with **no header**. Only intervals that traded are present;
gaps are not zero-filled. Intervals in the archive are 1, 5, 15, 30, 60, 240,
720, and 1440 minutes. That file is not an L2 or L3 book. A "thin liquidity"
feature has to be something the file actually contains (trade count, volume)
or a separately retained book — do not invent depth.

Set `max_gap` to the silence you pre-registered. A larger hole fails the run.
Kraken XBTUSD is USD-quoted. Binance BTCUSDT is USDT-quoted. Do not treat the
raw close difference as an executable basis.

The public REST OHLC payload is a different shape
(`[time, open, high, low, close, vwap, volume, count]`) and is not this CSV.

Sources:
[Kraken OHLCVT](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data),
[Kraken historical data guide](https://docs.kraken.com/exchange/guides/general/historical-data).

### WP4 — funding / carry

The Binance public-data README does not document a funding-rate CSV schema.
Declare the columns you actually find on the warehouse view. Put the realized
funding rate per bar in a `role: funding` column and name it in
`costs.funding_column` (see [Funding](#funding)); the harness then charges or
credits it while a trade is held. A carry signal built from funding (for
example the last settled rate) is a separate feature: stamp its
`available_at_column` at the time the print was knowable, which is not the
start of the interval if the print arrives at the end.

## Reading the artifact

`result.json` is the machine record (`harness_version` 6, `status`, `label`,
`promotion_decision`, `spec_sha256`, `data_fingerprint`, the `costs` and
`sizing` blocks, validation family with Bonferroni, Holm, and BH p-values,
per-config `funding` and `mean_weight`, and holdout gross, funding and net at
1.0 / 1.5 / 2.0 when a config was selected, the `overfitting`
diagnostics, the buy-and-hold `benchmark`, and the provenance fields
`source_environment`, `source_commit` and `image_digest`). `result.md` is
the same conclusion in prose. A `failed_closed` status (gap, duplicate,
schema, look-ahead, lock, fingerprint mismatch, unsafe mode, a malformed
`RESEARCH_IMAGE_DIGEST`, a non-positive sizing volatility as
`failure_kind: sizing`) has `label: null` and `promotion_decision: forbidden`.

Limitations live in every artifact: the conservative t / HAC gate, per-trade
Sharpe, flat half-spread costs, funding only from a declared column (stressed
adversely), unit sizing unless `vol_target` is declared, overfitting
diagnostics that never gate, and no detection of a leaked feature that was
falsely stamped with the bar clock.
