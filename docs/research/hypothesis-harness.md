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
was evaluated. Net subtracts the round trip
`2 * (fee_bps + slippage_bps + spread_bps) / 10000`, multiplied by the stress.
`spread_bps` is the half-spread per side, so the round trip charges it on
entry and again on exit. Latency is not a bps add-on. A fill uses the close of
`decision_bar + latency_bars`, and the exit is `horizon_bars` later. When the
signal's availability clock is the bar timestamp column, `latency_bars` must
be at least 1. `latency_bars: 0` is accepted only with
`costs.allow_zero_latency: true`, and that flag is printed in the report.

Each metric block reports the iid one-sided t and a Newey-West HAC t
(Bartlett kernel, lag `floor(4 * (n / 100) ** (2 / 9))`). Bonferroni, Holm,
and BH, and the holdout check, use the larger of the two p-values.

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
Declare the columns you actually find on the warehouse view. The harness cost
block is fees, slippage, spread, and latency only. It does not accrue funding.
If H1 is about carry, put that cashflow into the bar return in the ETL you
pre-register, and say so in `h1` and `dataset_version`. Stamp
`available_at_column` at the time the funding print was knowable, which is not
the start of the interval if the print arrives at the end.

## Reading the artifact

`result.json` is the machine record (`status`, `label`, `promotion_decision`,
`spec_sha256`, `data_fingerprint`, validation family with Bonferroni, Holm,
and BH p-values, and holdout gross and net at 1.0 / 1.5 / 2.0 when a config
was selected). `result.md` is the same conclusion in prose. A `failed_closed`
status (gap, duplicate, schema, look-ahead, lock, fingerprint mismatch,
unsafe mode) has `label: null` and `promotion_decision: forbidden`.

Limitations live in every artifact: the conservative t / HAC gate, per-trade
Sharpe, flat half-spread costs, no funding cashflow, and no detection of a
leaked feature that was falsely stamped with the bar clock.
