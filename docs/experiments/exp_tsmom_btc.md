# Experiment: time-series momentum on BTC (exp_tsmom_btc)

PAPER research only. This document **pre-registers** the first study of step
4 of the research plan. It is not an edge claim and not a trading
authorization.

It was written before any return in the study range was computed. Only the
bar table's shape was checked: the row count, that the bars are contiguous,
and the funding cadence.

- Spec: [`exp_tsmom_btc.spec.yaml`](exp_tsmom_btc.spec.yaml).
- Canonical sha256, from `python -m research.harness hash`:
  `2c8def878612ce08cea11171209a26b65034cda84bc4df92039b150dc96c3e31`.

Any change to the spec is a new pre-registration with a new hash, not a
re-run.

## Hypothesis

Time-series momentum: an asset's own trailing return predicts the sign of its
next return. It is documented across asset classes, at 1- to 12-month
lookbacks (Moskowitz, Ooi and Pedersen, 2012; Hurst, Ooi and Pedersen, 2017).
For crypto, the evidence points to weekly to monthly horizons (Liu and
Tsyvinski, 2021). The earlier research report ranked it as the strongest
candidate, and it noted that many naive variants die on costs and drawdowns.

- **H0**: the trend rule's mean net return per trade is ≤ 0 after costs and
  funding.
- **H1**: the trend rule's mean net return per trade is > 0 after costs and
  funding, on the untouched holdout.

The test uses alpha 0.05 with Holm across the grid. Each p-value is the
larger, so the more conservative, of the iid t-test's and the Newey-West HAC
t-test's p-values.

## Universe

One instrument: the BTCUSDT USD-M perpetual on Binance, in 1h bars. It
stands in for the Hyperliquid BTC perp the bot would trade, since the two
prices track each other within basis points. No coin is chosen from a larger
set, so there is no selection bias across coins. ETH and SOL would each be a
separate pre-registration after this one.

## Data (point-in-time)

- **Datasets** (hist_etl): `bn-um-btcusdt-klines-1h-2020` (new, opt-in) with
  `bn-um-btcusdt-klines-1h`, and `bn-um-btcusdt-funding-2020` (opt-in) with
  `bn-um-btcusdt-funding`.
- **Table**: `research.bar_tables trend` (#126) with `--start 2020-01-01
  --end 2026-10-01 --lookbacks 168,672,2016 --vol-window 168`.
- **Shape**: 57,144 contiguous hourly bars, from 2020-03-25 00:59:59.999 to
  2026-09-30 23:59:59.999 UTC, with one funding settlement every 8 hours.
- **Pre-registered table**: the spec hash does not cover the data or the
  code that builds it, so the table itself is pinned by the harness data
  fingerprint that `lock` writes.
  - The data fingerprint is
    `0553f54a851deae6759cc2a23619670000b07976d677e5fcf604ee9902a7eb09`.
  - It covers the row count, the first and last `ts`
    (1585097999999 and 1790812799999), and the Parquet's sha256,
    `fcdbbd9f3d168aaaa8918b942a5371a4badef668db28b9fe448993d2d8007ae7`.
  - Independent builds gave identical bytes, also with 1, 2, 8 and 32
    DuckDB threads (DuckDB 1.5.5 from `uv.lock`). A hash says nothing about
    returns.
  - The run refuses any other table, whether a different builder, a broken
    sync or another DuckDB version produced it.
- **Pre-registered code**: everything under `src` is pinned to commit
  `71b0043755dbbc05544e03a93c1121e7a8d4229c` (#126), together
  with `uv.lock`, `pyproject.toml` and `.python-version`. That covers the
  harness that scores the study, the builder, hist_etl, the locked
  dependencies and the interpreter.
  - The run refuses a checkout where any of these differ from that commit,
    including untracked and ignored files.
  - `__pycache__` folders are the one exception. The run reads and writes
    bytecode only in a fresh folder inside `STUDY_DIR`, so no cached file
    from the checkout runs.
  - The rest of the working tree must be clean too, so the recorded commit
    describes what ran.
  - The check happens before anything is synced.
- **Signal**: `trend_score` is the mean sign of the 1-, 4- and 12-week log
  returns (168, 672 and 2016 bars).
  - The lookbacks are fixed in advance and combined, not chosen, as Hurst,
    Ooi and Pedersen combine 1-, 3- and 12-month signals. No lookback is
    selected on data.
- **Volatility**: `realized_vol` is the sample stdev of the last 168 hourly
  log returns.
- **Availability**: every value uses closes at or before its bar, so it is
  available at the bar close. The spec uses `latency_bars: 1`.

## Periods

| Period | Bars | Dates (UTC, bar closes) |
| --- | --- | --- |
| Warm-up, not traded | 2,016 | 2020-01-01 to 2020-03-24 |
| Train (only delays the first fold; nothing is fitted) | 4,320 | 2020-03-25 to 2020-09-20 |
| Validation: 17 expanding walk-forward test folds of 2,160 bars (90 days) | 36,720 | 2020-09-21 to 2024-11-28 |
| Unused (a partial fold) | 792 | 2024-11-29 to 2024-12-31 |
| Untouched holdout | 15,312 | 2025-01-01 to 2026-09-30 |

The holdout is read once, and only if validation selects a config.

## Grid

These four configs are the whole family:

| id | threshold | horizon | Meaning |
| --- | --- | --- | --- |
| `any-1w` | 0.0 | 168 | Trade the majority sign of the three lookbacks; hold 1 week |
| `any-2w` | 0.0 | 336 | Majority sign; hold 2 weeks |
| `all-1w` | 0.5 | 168 | Trade only when all three lookbacks agree; hold 1 week |
| `all-2w` | 0.5 | 336 | All three agree; hold 2 weeks |

- **Direction**: long when the score is above the threshold, short when it
  is below minus the threshold (`direction: signed`).
- **Multiple testing**: Holm corrects across the four configs, and the
  deflated Sharpe ratio counts them as four trials.
- **Horizons**: weekly and biweekly holds keep turnover low. A 4-week hold
  would leave about 22 holdout trades, below the floor. A daily hold would
  pay about 12 bps a day.

## Costs and funding

- **Hyperliquid base taker fee**: 4.5 bps per side.
- **Slippage**: 1.0 bp per side.
- **Half-spread**: 0.5 bp per side.
- **Round trip**: 12 bps in total, also stressed at 1.5× and 2.0×.
- **Funding**: Binance's realized funding stands in for Hyperliquid's,
  whose own history only starts in 2023. It is charged on the notional for
  every bar the trade holds. At 1.5× and 2.0× it is stressed adversely,
  payment by payment.
- **Conservative bias**: every trade pays a full round trip, even when the
  next trade keeps the same side. A real position would roll instead, so
  costs are overstated, which biases the test against H1.

## Fill model and sizing

- **Timing**: decide at a bar's close and fill at the next bar's close, one
  hour later. Exit at the close `horizon` bars after the fill. Trades do not
  overlap.
- **Size**: `min(2.0, 0.005 / realized_vol)` per unit of notional. 0.005 per
  hour is about 47% annualized. Leverage is capped at 2.
- **No stops, and no averaging down.**

## Benchmarks

These are reported next to the result. They are not tested.

1. **Buy-and-hold** of the BTCUSDT perp, over the validation folds and over
   the holdout:
   - the log return;
   - the annualized vol and Sharpe of hourly returns;
   - the funding a constant long pays.

   It is computed from the same `bars.parquet`.
2. **Zero**, which is cash.

## Decision rules (committed now)

Proceed to a PAPER design only if all of these hold:

1. **Label**: the label is `passes_h1`. That already requires a positive
   holdout net mean at 1.0×, 1.5× and 2.0× costs, with p ≤ alpha.
2. **Annualized Sharpe**: the holdout net Sharpe at 1.0× is ≥ 0.5,
   annualized as `sharpe_per_trade × sqrt(trades / 1.748)`. Here `trades` is
   the selected config's holdout trade count and 1.748 is the holdout's
   length in years (15,312 / 8,760). A config that is flat part of the time
   is not credited for trades it did not make.
3. **Overfitting**: the deflated Sharpe ratio is ≥ 0.95 and the PBO is
   ≤ 0.5. If not, treat the result as overfit even if it passes.

Otherwise this family stops on BTC:

- Lookbacks, thresholds and horizons are not re-tuned on this data.
- A new variant needs a new pre-registration. Because this holdout will then
  have been seen, it also needs evidence from data after 2026-09-30, such as
  a forward or PAPER test, not this holdout.

## Robustness (reported)

- Cost stress at 1.5× and 2.0×, and adverse funding stress.
- HAC p-values.
- The deflated Sharpe ratio and PBO by CSCV (16 blocks of one fold). CSCV
  needs equal blocks, so PBO leaves out the oldest of the 17 folds
  (2020-09-21 to 2020-12-19). The validation metrics still use all 17.
- The grid itself: threshold and horizon.
- The mean position weight.

Not done here: other coins, other venues' prices, or sub-periods inside the
holdout.

## Limitations

- **Proxy data**: Binance prices and funding stand in for Hyperliquid. Fills
  are at bar closes, with no queue or impact model beyond the bps costs.
- **Short history**: one instrument over about six and a half years, with at
  most 90 holdout trades at a weekly hold.
- **Modest power**: a true annual Sharpe near 1 gives a holdout t-stat of
  about 1.3. A real but modest edge may therefore still be labelled
  `interesting_but_fragile`.
- **Conservative costs**: trades do not overlap, so each roll pays costs
  again.
- **Different test statistic**: volatility targeting changes what the t-test
  measures, to risk-scaled returns per trade.
- **Exploration outside the harness**: only the pre-registered grid is
  deflated. The literature and the earlier research report are exploration
  outside the harness.

## Run (after this document is merged)

Run from `main`, after this document is merged. The whole run is one
subshell with `set -euo pipefail`: it stops at the first failure without
closing your shell, and nothing syncs or runs before the code check passes.

```bash
export REPO_ROOT=...            # the repository checkout, on main
export HIST_ARCHIVES_ROOT=...   # the hist_etl archive root
export STUDY_DIR=...            # must not exist yet; outside the repository
(
  set -euo pipefail
  cd "$REPO_ROOT"
  pin=71b0043755dbbc05544e03a93c1121e7a8d4229c
  pinned=(src uv.lock pyproject.toml .python-version)
  # The pre-registered code: no change, tracked, untracked or ignored.
  git diff --exit-code "$pin" -- "${pinned[@]}"
  changed="$(git status --porcelain --ignored --untracked-files=all -- "${pinned[@]}")"
  stray="$(printf '%s\n' "$changed" | grep -v -e '^$' -e '/__pycache__/' || true)"
  test -z "$stray"
  # The whole working tree is clean, so commit.txt describes what runs.
  dirty="$(git status --porcelain --untracked-files=all)"
  test -z "$dirty"
  mkdir "$STUDY_DIR"
  git rev-parse HEAD > "$STUDY_DIR/commit.txt"
  # Bytecode only from a fresh folder: no cached .pyc from the checkout runs.
  export PYTHONPYCACHEPREFIX="$STUDY_DIR/pycache"
  # Exit 2 is normal while the current month is open. Any missing or broken
  # archive inside the range fails the fingerprint check below.
  PYTHONPATH=src uv run --frozen python -m research.hist_etl sync \
    --dataset bn-um-btcusdt-klines-1h-2020 --dataset bn-um-btcusdt-klines-1h \
    --dataset bn-um-btcusdt-funding-2020 --dataset bn-um-btcusdt-funding \
    2>&1 | tee "$STUDY_DIR/sync.log" || true
  cp docs/experiments/exp_tsmom_btc.spec.yaml "$STUDY_DIR/spec.yaml"
  PYTHONPATH=src uv run --frozen python -m research.bar_tables trend \
    --symbol BTCUSDT --start 2020-01-01 --end 2026-10-01 \
    --lookbacks 168,672,2016 --vol-window 168 --out "$STUDY_DIR/bars.parquet"
  PYTHONPATH=src uv run --frozen python -m research.harness lock "$STUDY_DIR/spec.yaml"
  PYTHONPATH=src uv run --frozen python -c '
import json, sys
lock = json.load(open(sys.argv[1]))
found = (lock.get("spec_sha256"), (lock.get("data_fingerprint") or {}).get("fingerprint_sha256"))
expected = (
    "2c8def878612ce08cea11171209a26b65034cda84bc4df92039b150dc96c3e31",
    "0553f54a851deae6759cc2a23619670000b07976d677e5fcf604ee9902a7eb09",
)
sys.exit(0 if found == expected else f"not the pre-registered spec and table: {found}")
' "$STUDY_DIR/spec.yaml.lock.json"
  PYTHONPATH=src uv run --frozen python -m research.harness run "$STUDY_DIR/spec.yaml" \
    --output-dir "$STUDY_DIR/out"
)
```

The study runs only after both checks pass. If the fingerprint check fails,
`sync.log` shows whether an archive inside the range is missing.

The results go into `exp_tsmom_btc.results.md` in a separate PR. That PR
records:

- the commit, from `commit.txt`;
- the lock file;
- the benchmarks.
