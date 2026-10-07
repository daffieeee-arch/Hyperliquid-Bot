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
- one or more configs (`threshold`, `horizon_bars`) — this is the whole grid
- costs in bps (`fee_bps`, `slippage_bps`, `spread_bps`) and `latency_bars`.
  `spread_bps` is the half-spread per side, not the full quoted spread
- optional `costs.funding_column` for a perp: see [Funding](#funding)
- optional `sizing`: `unit` (the default) or `vol_target`, see [Sizing](#sizing)
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

## Overfitting diagnostics

`result.json` has an `overfitting` block, summarized in `result.md`. Both
values are diagnostics: they never change the label or the promotion
decision. Both count only the pre-registered grid as trials, so exploration
done outside the harness is not deflated.

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
  `dsr` is the probabilistic Sharpe ratio.
- **Probability of backtest overfitting** (Bailey, Borwein, López de Prado
  and Zhu, 2017), by combinatorially symmetric cross-validation. The most
  recent walk-forward test folds are grouped into equal contiguous blocks:
  the even count from 4 to 16 that leaves out the fewest (oldest) folds, so
  at least 4 folds are needed. For every way to pick half of the blocks, the
  config with the best mean net per trade at 1.0x on that half (the
  statistic validation selection ranks by; a config without trades earns 0)
  is ranked on the other half. PBO is the share of splits where it ranks at
  or below the median. A split where every config ties in-sample selects
  nothing and is skipped. The selection gates (significance, stress) are
  not re-run per split. Near 0 is good; 0.5 means picking the in-sample best
  is no better than chance. It needs at least two configs.

## Point-in-time checks

Point-in-time checks fail closed (no statistical label) for schema mismatch,
nulls, duplicate or backwards timestamps, gaps above `max_gap`, non-positive
prices, and an availability clock after the bar. `max_rows` caps the pull
(hard ceiling 2,000,000). A raw aggTrades scan must be aggregated to bars
first. Empty intervals are not zero-filled.

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

`result.json` is the machine record (`harness_version` 4, `status`, `label`,
`promotion_decision`, `spec_sha256`, `data_fingerprint`, the `costs` and
`sizing` blocks, validation family with Bonferroni, Holm, and BH p-values,
per-config `funding` and `mean_weight`, and holdout gross, funding and net at
1.0 / 1.5 / 2.0 when a config was selected, and the `overfitting`
diagnostics). `result.md` is the same conclusion in prose. A `failed_closed`
status (gap, duplicate, schema, look-ahead, lock, fingerprint mismatch,
unsafe mode, a non-positive sizing volatility as `failure_kind: sizing`) has
`label: null` and `promotion_decision: forbidden`.

Limitations live in every artifact: the conservative t / HAC gate, per-trade
Sharpe, flat half-spread costs, funding only from a declared column (stressed
adversely), unit sizing unless `vol_target` is declared, overfitting
diagnostics that never gate, and no detection of a leaked feature that was
falsely stamped with the bar clock.
