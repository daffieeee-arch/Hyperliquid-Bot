# Experiment: contrarian funding on BTC (exp_funding_btc)

PAPER research only. This document **pre-registers** the directional funding
study, the first half of the funding/carry work of step 4 of the research
plan. It is not an edge claim and not a trading authorization.

It was written before any return of this rule was computed. What had been
seen before it is listed under [Seen before](#seen-before).

- Spec: [`exp_funding_btc.spec.yaml`](exp_funding_btc.spec.yaml).
- Canonical sha256, from `python -m research.harness hash`:
  `85d38e7ecc2f1a1dad7080f60f0913dbc4441f4d7cdf16334de1fb890ebb9ed1`.

Any change to the spec is a new pre-registration with a new hash, not a
re-run.

## Hypothesis

A perp's funding rate is what longs pay shorts to keep the perp near spot.
When it sits well above its usual level, longs are crowded and pay a lot to
stay in; when it sits below, the crowd leans short. The contrarian view is
that crowded positioning unwinds: high funding precedes weak returns and low
or negative funding precedes strong ones. A short against high funding also
receives the funding the crowd pays.

- **H0**: the contrarian rule's mean net return per trade is ≤ 0 after costs
  and funding.
- **H1**: the contrarian rule's mean net return per trade is > 0 after costs
  and funding, on the untouched holdout.

The test uses alpha 0.05 with Holm across the grid. Each p-value is the
larger of the iid t-test's and the Newey-West HAC t-test's.

This is the directional half of the funding work. The hedged carry (long
spot, short perp) is a separate pre-registration.

## Universe

One instrument: the BTCUSDT USD-M perpetual on Binance, in 1h bars, as in
[`exp_tsmom_btc`](exp_tsmom_btc.md). It stands in for the Hyperliquid BTC
perp. Binance's realized funding stands in for Hyperliquid's, whose history
only starts in 2023.

## Data (point-in-time)

- **Datasets** (hist_etl): `bn-um-btcusdt-klines-1h-2020` with
  `bn-um-btcusdt-klines-1h`, and `bn-um-btcusdt-funding-2020` with
  `bn-um-btcusdt-funding`, the same as `exp_tsmom_btc`.
- **Table**: `research.bar_tables trend` (#126, funding tilts from #131) with
  `--start 2020-01-01 --end 2026-10-01 --lookbacks 168,672,2016
  --vol-window 168 --funding-means 21 --funding-baseline 0.0001`. The
  lookbacks are unused by this spec. They keep the table's rows identical to
  `exp_tsmom_btc`'s, so the two studies share their periods.
- **Shape**: 57,144 contiguous hourly bars, from 2020-03-25 00:59:59.999 to
  2026-09-30 23:59:59.999 UTC. BTCUSDT settles funding every 8 hours
  throughout (`funding_interval_hours` 8 on all 7,395 settlements).
- **Pre-registered table**: pinned by the harness data fingerprint that
  `lock` writes.
  - The data fingerprint is
    `33d21ae1eea95941c1910d2e137ab9e08cf8171650a90bf2c838434a052dc833`.
  - It covers the row count, the first and last `ts` (1585097999999 and
    1790812799999), and the Parquet's sha256,
    `b277f5a612799d0adb41d25076d7a0788699105e899bbd7cd8897bb96e1ec696`.
  - Two independent builds gave identical bytes.
  - The run refuses any other table.
- **Pre-registered code**: everything under `src` is pinned to commit
  `9a294cd9a6244bd33fe6ee067456ad47dff1f17f` (#131), together with
  `uv.lock`, `pyproject.toml` and `.python-version`. That covers the harness
  (version 5, with its buy-and-hold benchmark), the builder, hist_etl, the
  locked dependencies and the interpreter. The run checks this as for
  `exp_tsmom_btc`, including untracked and ignored files, before it syncs
  anything.
- **Signal**: `funding_tilt_21` is `0.0001` minus the mean of the last 21
  funding settlements (7 days at 8h) at or before the bar's close.
  - 0.0001 per 8h is Binance's default rate when the perp trades at the
    index (its interest-rate component), so the tilt measures funding
    against its neutral level.
  - Positive: longs paid less than neutral, so the crowd leans short; the
    rule goes long. Negative: longs paid more; the rule goes short.
  - A week, not a day: a single settlement is noisy, and crowding that
    matters should persist.
- **Volatility**: `realized_vol`, the sample stdev of the last 168 hourly log
  returns, as in `exp_tsmom_btc`.
- **Availability**: every value uses closes and settlements at or before its
  bar, so it is available at the bar close. The spec uses `latency_bars: 1`.

## Periods

The same as `exp_tsmom_btc`:

| Period | Bars | Dates (UTC, bar closes) |
| --- | --- | --- |
| Warm-up, not traded | 2,016 | 2020-01-01 to 2020-03-24 |
| Train (only delays the first fold; nothing is fitted) | 4,320 | 2020-03-25 to 2020-09-20 |
| Validation: 17 expanding walk-forward test folds of 2,160 bars (90 days) | 36,720 | 2020-09-21 to 2024-11-28 |
| Unused (a partial fold) | 792 | 2024-11-29 to 2024-12-31 |
| Untouched holdout | 15,312 | 2025-01-01 to 2026-09-30 |

The holdout is read once, and only if validation selects a config. The
harness's buy-and-hold benchmark follows the same rule.

## Seen before

Nothing about this rule's returns had been seen. These had, and they are
stated so the reader can judge their weight:

- **The holdout's market**: the `exp_tsmom_btc` benchmark (#128) published
  buy-and-hold over the same holdout: BTC fell 10.7%, and a long of constant
  notional paid 7.3% in funding over 1.75 years, below the neutral rate's
  about 19%. So funding in the holdout was on average below neutral, which
  pushes this rule long in a falling market. That points against H1 on the
  holdout, not for it, but it is knowledge no pre-registration should have.
- **Validation funding**: over validation a constant long paid 58.1%, from
  the same benchmark.
- **Signal counts, not returns**: to set the grid, the trades the signal
  alone would open over the validation folds were counted, with no price
  read and no holdout row. For the mean of 21 settlements:

  | Threshold | Hold | Trades | Long | Short |
  | --- | --- | --- | --- | --- |
  | 0.00005 | 1 day | 785 | 503 | 282 |
  | 0.00005 | 3 days | 288 | 188 | 100 |
  | 0.00005 | 1 week | 135 | 90 | 45 |
  | 0.0001 | 1 day | 336 | 114 | 222 |
  | 0.0001 | 3 days | 127 | 46 | 81 |
  | 0.0001 | 1 week | 64 | 26 | 38 |
  | 0.0002 | 3 days | 63 | 4 | 59 |
  | 0.0003 | 3 days | 41 | 0 | 41 |

  Thresholds of 0.0002 and up trade almost only short, which would test a
  short-BTC bet rather than crowding in both directions, so the grid stops at
  0.0001. A mean of 3 settlements gave similar counts and was dropped for
  noise, as above.
- **The grid is not independent of what was known.** Validation was a
  strong bull market in which longs paid funding above neutral (58.1%
  against about 46% at the neutral rate). Dropping the mostly-short
  thresholds therefore also dropped the configs that market would punish.
  The reason given above is the real one, but the choice was not blind to
  that market.
- **More was looked at than the four configs.** Six threshold and hold
  combinations and two funding windows were counted before the grid was
  fixed. The deflated Sharpe ratio counts only the four pre-registered
  configs as trials, so it deflates less than the full search would.
- **A first draft of the grid held for 1 day.** Review pointed out that a
  1-day hold on a 7-day signal mostly re-buys the same position every day at
  a full round trip, so those configs would mainly test costs. They were
  replaced by 1-week holds before anything was run.

Because of the first point, a pass on this holdout is not enough on its own;
see the decision rules.

## Grid

These four configs are the whole family:

| id | threshold | horizon | Meaning |
| --- | --- | --- | --- |
| `near-3d` | 0.00005 | 72 | Long when the weekly mean is below 0.00005, short above 0.00015; hold 3 days |
| `near-1w` | 0.00005 | 168 | The same; hold 1 week |
| `far-3d` | 0.0001 | 72 | Long when the weekly mean is below 0, short above 0.0002; hold 3 days |
| `far-1w` | 0.0001 | 168 | The same; hold 1 week |

- **Direction**: `signed`. Long when the tilt is above the threshold, short
  when it is below minus the threshold.
- **Multiple testing**: Holm across the four configs; the deflated Sharpe
  ratio counts them as four trials.
- **Horizons**: three days and a week. The signal is a 7-day mean, so it
  moves slowly; a hold near its own length lets positioning unwind without
  paying a round trip every day for the same position.

## Costs and funding

As in `exp_tsmom_btc`:

- **Hyperliquid base taker fee**: 4.5 bps per side.
- **Slippage**: 1.0 bp per side.
- **Half-spread**: 0.5 bp per side.
- **Round trip**: 12 bps, also stressed at 1.5× and 2.0×.
- **Funding**: Binance's realized funding, charged or credited on the
  notional for every bar a trade holds, and stressed adversely payment by
  payment at 1.5× and 2.0×. A short against high funding receives it, and
  the stress halves what it receives at 2.0×.
- **Conservative bias**: every trade pays a full round trip, even when the
  next trade keeps the same side.

## Fill model and sizing

- **Timing**: decide at a bar's close and fill at the next bar's close. Exit
  `horizon` bars after the fill. Trades do not overlap.
- **Size**: `min(2.0, 0.005 / realized_vol)` per unit of notional, as in
  `exp_tsmom_btc`.
- **No stops, and no averaging down.**

## Benchmarks

The harness reports buy-and-hold of the BTCUSDT perp itself (harness version
5): over the validation folds, and over the holdout only if a config is
selected. Cash (zero) is the other benchmark. Neither is tested.

## Decision rules (committed now)

Proceed only if all of these hold:

1. **Label**: the label is `passes_h1`. That requires a positive holdout net
   mean at 1.0×, 1.5× and 2.0× costs, and p ≤ alpha at 1.0× only. The
   holdout is one pre-selected config, so there is no Holm step there.
2. **Annualized Sharpe**: the holdout net Sharpe at 1.0× is ≥ 0.5, as
   `sharpe_per_trade × sqrt(trades / 1.748)`, with the selected config's
   holdout trade count and the holdout's length in years.
3. **Overfitting**: the deflated Sharpe ratio is ≥ 0.95 and the PBO is
   ≤ 0.5.
4. **Not carry alone**: the selected config's holdout gross mean per trade
   (the price return, before costs and funding) is > 0. The hypothesis is
   that crowding unwinds in price. A pass that rests only on funding
   received is carry, which belongs to the separate hedged study.
5. **Forward confirmation**: because the holdout's market was seen (see
   [Seen before](#seen-before)), a pass leads to a pre-registered forward
   test on data after 2026-09-30 before any PAPER strategy design, not
   straight to one.

A selected config with too few holdout trades (`not_enough_data`) is not a
pass and not a refutation. Holdout funding sat near neutral, so the `far`
configs, which trade only below 0 or above 0.0002, may trade little there.
That outcome also leads to the forward test, with nothing re-tuned.

Otherwise this family stops on BTC:

- Thresholds, the funding window and horizons are not re-tuned on this data.
- A new variant needs a new pre-registration and evidence from data after
  2026-09-30.

## Robustness (reported)

- Cost stress at 1.5× and 2.0×, and adverse funding stress.
- HAC p-values.
- The deflated Sharpe ratio and PBO by CSCV (16 blocks of one fold; the
  oldest of the 17 folds is left out, as in `exp_tsmom_btc`).
- The grid itself: threshold and horizon.
- The mean position weight and the buy-and-hold benchmark.

## Limitations

- **Proxy data**: Binance prices and funding stand in for Hyperliquid, whose
  funding settles hourly and can differ in level.
- **One instrument**, about six and a half years, and a holdout whose market
  is known (see above).
- **A fixed neutral rate**: 0.0001 per 8h is Binance's default. Other venues
  or a changed default would move the neutral level.
- **Directional exposure**: this rule carries BTC's price risk. The hedged
  carry, which does not, is a separate study.
- **Different test statistic**: volatility targeting changes what the t-test
  measures, to risk-scaled returns per trade.

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
  pin=9a294cd9a6244bd33fe6ee067456ad47dff1f17f
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
  cp docs/experiments/exp_funding_btc.spec.yaml "$STUDY_DIR/spec.yaml"
  PYTHONPATH=src uv run --frozen python -m research.bar_tables trend \
    --symbol BTCUSDT --start 2020-01-01 --end 2026-10-01 \
    --lookbacks 168,672,2016 --vol-window 168 \
    --funding-means 21 --funding-baseline 0.0001 --out "$STUDY_DIR/bars.parquet"
  PYTHONPATH=src uv run --frozen python -m research.harness lock "$STUDY_DIR/spec.yaml"
  PYTHONPATH=src uv run --frozen python -c '
import json, sys
lock = json.load(open(sys.argv[1]))
found = (lock.get("spec_sha256"), (lock.get("data_fingerprint") or {}).get("fingerprint_sha256"))
expected = (
    "85d38e7ecc2f1a1dad7080f60f0913dbc4441f4d7cdf16334de1fb890ebb9ed1",
    "33d21ae1eea95941c1910d2e137ab9e08cf8171650a90bf2c838434a052dc833",
)
sys.exit(0 if found == expected else f"not the pre-registered spec and table: {found}")
' "$STUDY_DIR/spec.yaml.lock.json"
  PYTHONPATH=src uv run --frozen python -m research.harness run "$STUDY_DIR/spec.yaml" \
    --output-dir "$STUDY_DIR/out"
)
```

The study runs only after both checks pass. If the fingerprint check fails,
`sync.log` shows whether an archive inside the range is missing.

The results go into `exp_funding_btc.results.md` in a separate PR. That PR
records the commit from `commit.txt`, the lock file, and the harness output,
which now carries the benchmark itself.
