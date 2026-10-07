# Results: time-series momentum on BTC (exp_tsmom_btc)

PAPER research only. These are the results of the study pre-registered in
[`exp_tsmom_btc.md`](exp_tsmom_btc.md). They are not an edge claim and not a
trading authorization.

## Outcome

- **Label**: `interesting_but_fragile`, with promotion decision
  `forbidden`.
- **Selection**: no config passes Holm at alpha 0.05. The strongest,
  `all-1w`, has p = 0.0196, which Holm adjusts to 0.0783.
- **Holdout**: sealed for the strategy. Validation selected no config, so
  the harness did not read it, and no strategy return from 2025-01-01
  onwards was computed. The pre-registered buy-and-hold benchmark does read
  the holdout's prices and funding; see Benchmarks.
- **Decision**: the committed decision rules are not met. This family stops
  on BTC, and its lookbacks, thresholds and horizons are not re-tuned on
  this data.

## Run record

- **When**: once, on 2026-10-07; the harness wrote its result at
  2026-10-07T18:44:05Z.
- **How**: the Run script of the pre-registration, unchanged apart from the
  three exported paths.
- **Commit**: `9adc8323b4839fff519812df71c364e60addbdc3`, the #127 merge,
  from `commit.txt`. The code check against
  `71b0043755dbbc05544e03a93c1121e7a8d4229c` passed on a clean working
  tree.
- **Where**: a Claude Code cloud container, not the VPS, from a checkout
  rather than an image. `source_environment` is `DEV`. `image_digest` is
  null because the harness does not record one yet, on any run.
- **Sync**: 168 archives ready. The one gap line was for the open month of
  October 2026 in `bn-um-btcusdt-klines-1h`, which is outside the range.
- **Spec**: sha256
  `2c8def878612ce08cea11171209a26b65034cda84bc4df92039b150dc96c3e31`, as
  pre-registered. The copy the run used is byte-identical to
  `exp_tsmom_btc.spec.yaml`.
- **Table**: 57,144 rows. The data fingerprint
  `0553f54a851deae6759cc2a23619670000b07976d677e5fcf604ee9902a7eb09` and the
  Parquet sha256
  `fcdbbd9f3d168aaaa8918b942a5371a4badef668db28b9fe448993d2d8007ae7` both
  match the pre-registration.
- **Lock file**: [`exp_tsmom_btc.lock.json`](exp_tsmom_btc.lock.json),
  verbatim, sha256
  `7f0796459fc4e73fbbd5276399c68617735ac064a6f373a36b7972f8b046c9db`.
- **Harness output**: [`exp_tsmom_btc.result.json`](exp_tsmom_btc.result.json),
  verbatim from harness version 4, sha256
  `1928f613ddb400088437c4009085c1d2bbe14d4b515ebd241aa4a2881a6605cc`.
- **No earlier run**: before this run, only the table's shape had been
  checked. The harness had not run on this table.

## Validation

The validation period is 17 walk-forward folds, from 2020-09-21 to 2024-11-28.

- Values are means per trade, on the notional times the vol-target weight.
- Net values are after costs and funding.
- p is the larger of the iid and Newey-West HAC p-values, at 1.0× costs.

| config | trades | mean gross | mean funding | mean net 1.0× | net 1.5× | net 2.0× | p | Holm p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `any-1w` | 204 | +0.05% | −0.14% | −0.20% | −0.37% | −0.54% | 0.6363 | 0.9240 |
| `any-2w` | 102 | +1.15% | −0.27% | +0.76% | +0.48% | +0.21% | 0.2992 | 0.8976 |
| `all-1w` | 157 | +1.56% | −0.19% | +1.26% | +1.08% | +0.90% | 0.0196 | 0.0783 |
| `all-2w` | 86 | +0.57% | −0.33% | +0.13% | −0.17% | −0.45% | 0.4620 | 0.9240 |

The same configs, net at 1.0×:

| config | mean weight | Sharpe per trade | annualized Sharpe | win rate | max drawdown | gross iid t (gating p) |
| --- | --- | --- | --- | --- | --- | --- |
| `any-1w` | 0.95 | −0.024 | −0.17 | 49.5% | 198% | 0.08 (0.468) |
| `any-2w` | 0.98 | 0.060 | 0.30 | 58.8% | 162% | 0.90 (0.220) |
| `all-1w` | 0.92 | 0.166 | 1.02 | 53.5% | 58% | 2.55 (0.006) |
| `all-2w` | 0.93 | 0.011 | 0.05 | 51.2% | 145% | 0.44 (0.340) |

- **Annualized Sharpe**: descriptive and not a decision input. It is
  `sharpe_per_trade × sqrt(trades / 4.192)`, where 4.192 is validation's
  length in years (36,720 / 8,760). This mirrors decision rule 2.
- **Max drawdown**: the harness sums simple per-trade returns without
  compounding.
- **Gating p**: the larger of the iid and HAC p-values, as in the first
  table. It is not always the iid t's own p: for `any-2w`, `any-1w` and
  `all-2w` it is the HAC p.

## Overfitting diagnostics

- **Deflated Sharpe ratio**: 0.855, for `all-1w`. No config was selected,
  so the harness used the one with the best validation mean.
  - It counts 4 trials.
  - The Sharpe per trade is 0.166, against an expected noise maximum of
    0.084.
  - The rules ask for at least 0.95.
- **PBO**: 0.334, by CSCV over 16 blocks of one fold each.
  - CSCV needs equal blocks, so, as pre-registered, PBO leaves out the
    oldest of the 17 folds (2020-09-21 to 2020-12-19). Every other metric
    uses all 17.
  - 12,763 of the 12,870 splits were used and 107 were skipped.
  - The in-sample trade floor was 19.
  - The rules ask for at most 0.5.

## Benchmarks

Buy-and-hold of the BTCUSDT perp, computed from the same `bars.parquet`.

| | Validation | Holdout |
| --- | --- | --- |
| Bars | 36,720 | 15,312 |
| Log return | 2.171 | −0.113 |
| Simple return | +776.8% | −10.7% |
| Annualized vol of hourly log returns | 63.3% | 43.9% |
| Annualized Sharpe of hourly log returns, before funding | 0.82 | −0.15 |
| Funding paid by a constant-notional long (sum of rates) | 58.1% | 7.3% |

- **Zero**: cash returns 0.
- **Funding row**: the sum of the funding rates, which is what a long of
  constant notional pays. A long of fixed BTC quantity pays on a notional
  that grew with the price, so it paid more.
- **The holdout column is a deviation**: it reads holdout rows, while no
  config was selected.
  - The pre-registration conflicts with itself here. Its Benchmarks section
    asks for buy-and-hold over the holdout. Its Periods section says the
    holdout is read only if validation selects a config.
  - This run followed Benchmarks. It read the holdout's prices and funding,
    which are public market history, but not the strategy's holdout returns.
  - The 2025–2026 market is now known to whoever writes the next spec. That
    is one more reason a new variant needs data from after 2026-09-30.
- **Not comparable with the strategy**: the strategy rows are net per-trade
  means of risk-scaled positions, while buy-and-hold is unscaled, hourly and
  before funding. `all-1w` is also the best of four configs on this same
  data. This document therefore does not compare their Sharpe ratios.

## Decision rules

| Rule | Required | Found | Met |
| --- | --- | --- | --- |
| 1. Label | `passes_h1` | `interesting_but_fragile` | no |
| 2. Holdout annualized Sharpe at 1.0× | ≥ 0.5 | not evaluated; holdout sealed | no |
| 3a. Deflated Sharpe ratio | ≥ 0.95 | 0.855 | no |
| 3b. PBO | ≤ 0.5 | 0.334 | yes |

Not all rules hold, so this family stops on BTC.

## Reading

- **One config with gross edge**: only `all-1w` shows one, with a gross
  iid t of 2.55 and a p of 0.006. It trades when all three lookbacks agree
  and holds for one week. The majority-sign rule has no gross edge at one
  week, and a weak one at two.
- **Multiple testing decides it**: with four configs, Holm multiplies
  `all-1w`'s p of 0.0196 by four, to 0.0783. At 2.0× costs its unadjusted p
  is already 0.069.
- **Funding was the larger cost**: every config paid net funding on
  average, between 1.2 and 2.9 times its round-trip trading cost. On net,
  longs paid funding over this period: a constant-notional long paid 58% of
  its notional over validation.
- **PBO**: 0.33 means the in-sample best config usually stayed above the
  median out of sample. The ranking is not pure noise, but with four
  configs that is weak evidence.

## What follows

- **No promotion and no re-tuning**: `all-1w` is not promoted or tuned.
  Picking it now would be selection on validation, which Holm is there to
  correct.
- **Holdout**: the strategy's holdout returns were never computed. Scoring
  `all-1w` alone on the holdout now would be a test chosen after seeing
  validation. The committed rules do not allow it, so it is not done.
- **A new variant**: per the committed rules, it needs a new
  pre-registration and evidence from after 2026-09-30, such as a forward or
  PAPER test.
- **For later specs**: funding cost more than trading here. Trend specs on
  perps should treat funding as a first-order cost.

## Benchmark computation

The script below is byte-for-byte the file that produced the output. Its
sha256 is `c4c159b8206d5e6f4f4bff6b0c5a25f18543f3a1024dd8c3a3ebf8496de795e3`.
It is not committed as a file. To rerun it, save the block as
`tsmom_benchmark.py`, check the hash, and run it with the locked
environment (Python 3.13.15, DuckDB 1.5.5):

```bash
uv run --frozen python -I tsmom_benchmark.py "$STUDY_DIR/bars.parquet"
```

- **Row ranges** count from 0 in `ts` order and are inclusive.
- **Validation**: `4320..41039` is `test_start` of fold 0 to `test_end - 1`
  of fold 16 in `split` of `exp_tsmom_btc.result.json`.
- **Holdout**: `41832..57143` is `holdout_start` to `holdout_end - 1`.

```python
"""Pre-registered benchmark for exp_tsmom_btc: buy-and-hold BTCUSDT perp.

Usage: python tsmom_benchmark.py <bars.parquet>

Windows are row ranges of the pre-registered table: validation = the 17 test
folds (rows 4320..41039), holdout = rows 41832..57143. For each window:
the log return from the close before the window to its last close, the
annualized vol and Sharpe of hourly log returns (sqrt(8760)), and the summed
funding rate a constant long pays over the window's bars.
"""

import sys

import duckdb

WINDOWS = {"validation": (4320, 41039), "holdout": (41832, 57143)}

connection = duckdb.connect()
connection.execute(
    "CREATE TABLE bars AS SELECT row_number() OVER (ORDER BY ts) - 1 AS row, ts, close, "
    "funding_rate FROM read_parquet(?)",
    [sys.argv[1]],
)
connection.execute(
    "CREATE TABLE returns AS SELECT row, ts, ln(close / lag(close) OVER (ORDER BY row)) AS r, "
    "funding_rate FROM bars"
)
for name, (first, last) in WINDOWS.items():
    total, mean, stdev, funding, count = connection.execute(
        "SELECT sum(r), avg(r), stddev_samp(r), sum(funding_rate), count(*) "
        "FROM returns WHERE row BETWEEN ? AND ?",
        [first, last],
    ).fetchone()
    print(
        f"{name}\tbars={count}\tlog_return={total:.4f}\tsimple_return={pow(2.718281828459045, total) - 1:.4f}"
        f"\tann_vol={stdev * 8760 ** 0.5:.4f}\tann_sharpe={mean / stdev * 8760 ** 0.5:.4f}"
        f"\tlong_funding_paid={funding:.4f}"
    )
```

Output:

```text
validation	bars=36720	log_return=2.1711	simple_return=7.7678	ann_vol=0.6333	ann_sharpe=0.8179	long_funding_paid=0.5806
holdout	bars=15312	log_return=-0.1127	simple_return=-0.1066	ann_vol=0.4386	ann_sharpe=-0.1470	long_funding_paid=0.0732
```
