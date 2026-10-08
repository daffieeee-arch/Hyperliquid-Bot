# Results: contrarian funding on BTC (exp_funding_btc)

PAPER research only. These are the results of the study pre-registered in
[`exp_funding_btc.md`](exp_funding_btc.md). They are not an edge claim and
not a trading authorization.

## Outcome

- **Label**: `no_edge`, promotion decision `forbidden`.
- **Selection**: no config passes Holm at alpha 0.05. All four have a
  positive validation mean net per trade, but none is significant: the
  smallest p is 0.0814 (`near-1w`), and Holm adjusts every config to 0.3254.
- **Holdout**: sealed. Validation selected no config, so neither the
  strategy nor the harness's buy-and-hold benchmark read it.
- **Decision**: per the pre-registered outcome table, `no_edge` stops this
  family on BTC. Thresholds, the funding window and horizons are not
  re-tuned on this data. No forward test follows; that was reserved for a
  pass.

## Run record

- **When**: once, on 2026-10-08; the harness wrote its result at
  2026-10-08T07:26:06Z.
- **How**: the Run script of the pre-registration, unchanged apart from the
  three exported paths.
- **Commit**: `19a452441cd589eb06dd847e6d3b48b0c5657a47`, the #132 merge,
  from `commit.txt`, equal to `source_commit` in the harness output.
- **Gates**:
  - HEAD (`19a4524`) was an ancestor of `origin/main`, which the script
    checks; at the run it was also main's tip. The document matched main.
  - `STUDY_DIR` was outside the repository.
  - The code check against `9a294cd9a6244bd33fe6ee067456ad47dff1f17f`
    passed on a clean tree.
- **Where**: a Claude Code cloud container, not the VPS, from a checkout
  rather than an image. `source_environment` is `DEV` and `image_digest` is
  null (no `RESEARCH_IMAGE_DIGEST` was set).
- **Sync**: 169 archives ready, and sync exited 2 with one gap line,
  `refused_overwrite` for `BTCUSDT-2026-10.parquet`.
  - The archive root was the one `exp_tsmom_btc` and the dry runs used.
  - Since then, the 2026-10-07 daily archive had been published: 169 ready
    against 168 then. So the open October 2026 klines Parquet built earlier
    no longer matched its sources, and hist_etl refused to overwrite it.
  - That file lies outside the range, since the table ends at 2026-09-30.
  - There was no `error` line, and the fingerprint check below passed.
- **Spec**: canonical sha256
  `52eb9a64ff094b80ed6c350a1d51a9ae95c5b33d84cdad907f39bcc2a2ad8d5d`, as
  pre-registered.
- **Table**: 57,144 rows. The data fingerprint is
  `33d21ae1eea95941c1910d2e137ab9e08cf8171650a90bf2c838434a052dc833`, and
  the Parquet sha256 is
  `b277f5a612799d0adb41d25076d7a0788699105e899bbd7cd8897bb96e1ec696`. Both
  match the pre-registration.
- **Lock file**: [`exp_funding_btc.lock.json`](exp_funding_btc.lock.json),
  verbatim, with sha256
  `629cdcb90b5a0e46b5aa2aa042d6fe28682d1845522dd2b595183895fcd9f385`.
- **Harness output**: [`exp_funding_btc.result.json`](exp_funding_btc.result.json),
  verbatim from harness version 5, with sha256
  `c79e60339b6e16add9e5e12f495fec807126b4a2d5eb5f44adb994c346448585`.
- **Artifacts kept**: only the lock file and the harness output. The rest of
  `STUDY_DIR` lived in the container and is not kept.

## Validation

The validation period is 17 walk-forward folds, from 2020-09-21 to
2024-11-28.

- Values are means per trade, on the notional times the vol-target weight.
- Funding is positive when received.
- Net values are after costs and funding.
- p is the larger of the iid and HAC p-values, at 1.0× costs.

| config | trades | mean gross | mean funding | mean net 1.0× | net 1.5× | net 2.0× | p | Holm p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `near-3d` | 281 | +0.37% | +0.06% | +0.32% | +0.22% | +0.14% | 0.1385 | 0.3254 |
| `near-1w` | 130 | +0.95% | +0.13% | +0.97% | +0.81% | +0.69% | 0.0814 | 0.3254 |
| `far-3d` | 125 | +0.36% | +0.17% | +0.43% | +0.33% | +0.25% | 0.1507 | 0.3254 |
| `far-1w` | 62 | +0.93% | +0.34% | +1.18% | +1.00% | +0.88% | 0.1028 | 0.3254 |

The same configs, net at 1.0×:

| config | mean weight | Sharpe per trade | annualized Sharpe | win rate | max drawdown |
| --- | --- | --- | --- | --- | --- |
| `near-3d` | 0.90 | 0.066 | 0.54 | 48.8% | 52% |
| `near-1w` | 0.90 | 0.125 | 0.69 | 50.8% | 58% |
| `far-3d` | 0.74 | 0.099 | 0.54 | 50.4% | 48% |
| `far-1w` | 0.74 | 0.162 | 0.62 | 48.4% | 50% |

- **Annualized Sharpe**: descriptive, not a decision input:
  `sharpe_per_trade × sqrt(trades / 4.192)`.
- **Max drawdown**: sums simple per-trade returns without compounding.
- **Trade counts**: they are a little below the pre-registration's signal
  counts (281 against 288 for `near-3d`, for example). The harness keeps
  every trade inside its fold, while the count ran over validation as one
  window.

## Overfitting diagnostics

- **Deflated Sharpe ratio**: 0.593, for `far-1w`. No config was selected,
  so this is the best validation mean.
  - Its Sharpe per trade is 0.162 over 62 trades, against an expected noise
    maximum of 0.135 for 4 trials.
  - The pre-registration notes that more than 4 combinations were looked
    at, so this already-low value is if anything generous.
- **PBO**: 0.425, by CSCV over 16 blocks.
  - 12,020 of the 12,870 splits were used and 850 were skipped.
  - The in-sample trade floor was 19.

## Benchmark

Harness version 5's buy-and-hold over the validation folds: one unit long,
filled after `latency_bars`, priced like a strategy trade.

| | Validation |
| --- | --- |
| Bars held | 36,718 |
| Gross return | +774.9% |
| Log return | 2.169 |
| Funding, fixed quantity (positive when received) | −246.3% |
| Funding, constant notional | −58.1% |
| Net at 1.0× | +528.5% |
| Sharpe per bar (hourly log returns) | 0.0087 |

- **Holdout benchmark**: sealed, like the strategy.
- **Fixed quantity versus constant notional**: a fixed quantity of BTC paid
  funding on a notional that grew about 8.75 times, so it paid far more than
  a long of constant notional.
- **Not comparable**: the strategy rows are per-trade means of risk-scaled
  positions, while this is one unscaled position over four years.

## Decision

| Rule | Required | Found | Met |
| --- | --- | --- | --- |
| 1. Label | `passes_h1` | `no_edge` | no |
| 2. Holdout annualized Sharpe | ≥ 0.5 | not evaluated; holdout sealed | no |
| 3. DSR, PBO | ≥ 0.95, ≤ 0.5 | 0.593, 0.425 | no, yes |
| 4. Gross ≥ funding on the holdout | yes | not evaluated | no |

The outcome is "anything else" in the pre-registered table, so this family
stops on BTC.

## Reading

- **Positive but not significant**: every config made money on average in
  validation, after costs and at 2.0× stress. The evidence is too weak to
  separate that from luck: no config's own p is below 0.08, and four were
  tried.
- **The price return carried most of it**: gross means of 0.36% to 0.95% per
  trade against funding received of 0.06% to 0.34%. The rule did not live
  mainly on carry in validation.
- **Longer holds did better per trade**: the 1-week holds had about three
  times the per-trade mean of the 3-day holds (0.97% against 0.32%, and
  1.18% against 0.43%), and 1.6 to 1.9 times the Sharpe per trade. With 62
  to 130 trades, that difference is itself within noise.
- **Compared with `exp_tsmom_btc`**: both BTC studies found positive
  validation means that do not survive multiple testing. Neither earns a
  holdout test.

## What follows

- **No re-tuning**: no config is promoted or tuned. In particular, `far-1w`
  and `near-1w` are not carried forward on this evidence.
- **Holdout**: the strategy's holdout returns were never computed.
- **A new variant**: per the committed rules, a new variant needs a new
  pre-registration and evidence from data after 2026-09-30.
- **The hedged carry** (long spot, short perp) stays a separate study, with
  its own pre-registration.
