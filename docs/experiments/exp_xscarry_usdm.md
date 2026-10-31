# Experiment: cross-sectional funding carry on Binance USD-M perps (exp_xscarry_usdm)

> **Draft, not mergeable yet.** Its signal `carry_7d` (minus the 7-day mean
> funding over traded days with a rate, ties broken by a hashed draw) comes
> from the S4 panel change, merged in #145 (`694497a`), which this study
> uses. The current source/environment amendment and uncompleted data
> gates are recorded in [S4 readiness](exp_xs_usdm.readiness.md). Values
> in angle brackets remain unmeasured; no strategy has been scored.

PAPER research only. This document **pre-registers** the cross-sectional
funding carry study, one of the two families of step S4 of the research
plan. The other is the momentum study,
[`exp_xsmom_usdm`](exp_xsmom_usdm.md). It is not an edge claim and not a
trading authorization.

It was written before any return of a cross-sectional strategy was computed
in this project, and before the panel it uses was built. What had been seen
is listed under [Seen before](#seen-before).

- Spec: [`exp_xscarry_usdm.spec.yaml`](exp_xscarry_usdm.spec.yaml).
- Canonical sha256, from `python -m research.harness hash`:
  `457009762711c0ac113719360fc7d04431f67f63ce10a88a6d02b74e0f0db44a`.

Any change to the spec is a new pre-registration with a new hash, not a
re-run.

## Hypothesis

A perp's funding rate is what longs pay shorts to keep the perp near spot.
Across contracts, it sorts names by how crowded their longs are. The carry
book goes long the contracts with the lowest recent funding and short those
with the highest. It earns the funding spread between them: its short leg
receives the high rates and its long leg pays the low ones, or receives when
they are negative.

Whether it earns more than that spread is open:

- **For the price legs**: a high carry predicts later crashes (BIS WP 1087,
  for BTC and ETH), so the short leg may also gain in price. Chi et al.
  (JFM 2023) find a large premium for long backwardated and short contango
  dated futures across 12 coins, the dated-futures version of this sort,
  but only at 1-day holds; it is gone at weekly and monthly holds.
- **Against**: carry is driven by trend-chasing longs (BIS), so the
  highest-funding names tend to be recent winners. If momentum persists,
  the short leg loses in price what it earns in funding.
- **The rest**: the hedged trade the literature covers (long spot, short
  perp, per coin) earns 9.6% to 34% a year per coin with a small spread
  between coins (Christin et al., 18 Binance coins, 2020-08 to 2022-06).
  Fan et al. report a cross-sectional carry of 43.4% a year (Sharpe 0.74;
  the rate's definition not verified); Presto Labs found a top-50 Binance
  cross-sectional funding sort promising before costs with very high
  turnover; Fayez Junior found funding and OHLCV signals on 10 Binance
  perps negative after costs. No peer-reviewed net-of-cost test of a weekly
  funding sort on top-50 perps was found.

- **H0**: the carry portfolio's mean net return per period is ≤ 0 after
  costs and funding.
- **H1**: the carry portfolio's mean net return per period is > 0 after
  costs and funding, on the holdout, scored once. No cross-sectional
  strategy has been scored there, but the holdout's market has been seen
  (see [Seen before](#seen-before)), so it is not untouched; the forward
  test in the decision rules is the out-of-sample check on data untouched
  by any strategy of this study.

The test is balanced, not as strict as possible, for the reasons given in
[`exp_xsmom_usdm`](exp_xsmom_usdm.md#hypothesis): a pass leads only to a
forward PAPER test on data untouched by any strategy of this study, never
directly to capital. Validation uses alpha **0.05** with Holm across the
grid's four configs, and the holdout a one-sided p-value of at most 0.10
(rule 2). Each p-value is the larger of the iid t-test's and the
Newey-West HAC t-test's. With both S4 families at 0.05, the chance that validation selects a config in at least
one of them when neither has an edge is at most about 0.10, accepted
because of what a pass buys.

This is the cross-sectional half of the carry work. The hedged carry (long
spot, short perp) is a separate pre-registration.

## Universe

The same as [`exp_xsmom_usdm`](exp_xsmom_usdm.md#universe): Binance USD-M
USDT perpetuals from the committed universe file, delisted ones included;
on each decision day the 50 best by `volume_rank` among the contracts that
traded, have a 30-day mean quote volume and a funding rate that day, and
are not on the non-crypto list
(`config/research/binance-um-usdt-non-crypto-2026-10-08.json`, sha256
`37dc30be8a1aa95c983ec0d2e091ed9297d9f452e85fd51ee03b75fbff5d5348`, from the classification of every contract,
221 contracts: 159 `equity`, 38 `etf_or_etp`, 10 `commodity`, 6 `index`, 6 `private_company`, 2 `fiat_pegged_or_fx` and none `unverified`; six of them list before the holdout, the indices BTCDOM, DEFI, DOTECO, FOOTBALL and BLUEBIRD, and USDC). The reasons for both rules, the list's checks, the
classification and the contracts that trade without a funding archive are
given there.

Of those 50, the universe keeps the names with a known `carry_7d`, which
needs seven consecutive days that each traded and have a funding rate. A
ranked name without one leaves the universe smaller than 50; the 51st name
does not replace it.

- **Why traded days with a rate, not covered days.** The panel's
  `funding_7d` needs seven days whose settlements the close can prove
  whole, and a day that returns from 4h to 8h funding can read as not
  whole, which blanks it for seven days. Binance changes a contract's
  interval mainly when its rate runs at the cap or floor (its announced
  practice; not checked against the archive here), so a signal on
  `funding_7d` would drop, for a week, the names coming off extreme
  funding: the tails this book sorts on. `carry_7d` takes such a day at its
  recorded sum, the funding a holder was charged that day, known at its
  close, and what the harness charges a held position for it. Like the
  price features, it reads no day that did not trade: no one paid a
  delisted contract's flat archive funding, and the build does not check
  it.
- **What an uncovered day does to the signal.** A day with fewer
  settlements than usual counts at what it charged. On a traded day inside
  a funding run a missing settlement fails the build, so there such a day
  is an interval switch or a listing or delisting day. Two cases are not
  caught: at the close, a day cut short by a delisting cannot be told from
  one short of an archived settlement inside a delisting month, which the
  build allows; and one settlement missing right where an interval changes.
  Either enters the window short by at most the missing part of one day's
  funding in seven, which makes the name's funding look lower, so the
  low-funding long leg may favour it. This study tolerates any number of
  such rows. The shape-only check counts the ranked rows with an uncovered
  day in their carry window (`covered_mean_missing`); at 1% or more of a
  window's ranked rows, the symbols are listed under Limitations. Rule 5
  bounds the uncovered funding days a pass rests on.
- **Who is still left out**: a ranked name with a day in the last seven
  that did not trade or has no rate (a listing whose funding archive starts
  late, a name back from a funding gap, a halt), and a name whose window
  holds a funding dataset's first day (the first of its first month, or a
  manifest `start`) that is not covered: hist_etl keeps no settlement
  stamped before a dataset's start, so that day's midnight settlement may
  be cut away, and the day counts only when covered. The shape-only check
  counts these rows (`without_carry`; see [Seen before](#seen-before)).

Equal weight, no hand-picking, and Binance as a proxy for Hyperliquid, as
for momentum. For carry the proxy matters more: the return is partly the
funding itself, and Hyperliquid's funding settles hourly at its own level.

## Data (point-in-time)

- **Datasets and table**: the `bn-um-usdt-1d` universe entry and the same
  `research.bar_tables panel` table as `exp_xsmom_usdm`: `--group
  bn-um-usdt-1d --start 2020-01-01 --end 2026-09-01 --lookbacks 14
  --vol-window 30 --volume-window 30 --funding-window 7 --exclude-symbols
  config/research/binance-um-usdt-non-crypto-2026-10-08.json
  --rank-requires-funding`. `ret_14d` and `vol_30d` are unused here.
- **Shape**: `<ROW_COUNT>` rows, `<SYMBOL_COUNT>` symbols, 2,435 daily
  closes from 2020-01-01 to 2026-08-31, as for momentum. The panel ends
  with 2026-08 so that the funding month the builder reads after the
  window, 2026-09, is already published (see
  [momentum's Data](exp_xsmom_usdm.md#data-point-in-time)).
- **Pre-registered table**: the data fingerprint is `<DATA_FINGERPRINT>`,
  over the row count, the first and last `ts` (`1577923199999` and
  `1788220799999` expected) and the Parquet's sha256 `<PARQUET_SHA256>`.
  Both families lock the same table, so the fingerprint is the same.
  Independent builds give identical bytes: `<REBUILD_CHECK>`. The run
  refuses any other table.
- **Pre-registered code**: everything under `src` is pinned to commit
  `0cd7666392928b0af3f4a0c0ef5c8fc34a516d58`, the documented source/environment
  amendment described in [S4 readiness](exp_xs_usdm.readiness.md), with `uv.lock`, `pyproject.toml`, `.python-version` and
  the universe file, checked as in `exp_xsmom_usdm` before anything syncs.
  If a later commit touches `src`, `uv.lock`, `pyproject.toml`,
  `.python-version` or the universe file
  (`config/hist_etl/universe/binance-um-usdt-1d-2026-10-08.json`) before
  this document merges, the pin moves to that commit and the checks before
  merge are run again.
- **Signal**: `carry_7d`, minus the mean of the daily `funding_rate` over
  the seven consecutive days ending that day, in one stretch, each of
  which traded and has a rate, covered or not, except that a funding
  dataset's first day counts only when covered (see Universe), with the
  window's sum counted in steps of 1e-9 and ties broken by a draw.
  - `funding_rate` is the sum of the day's settlements, positive when longs
    pay, so 8-hour, 4-hour and 1-hour contracts compare as daily rates.
  - Positive `carry_7d`: shorts paid longs over the week. The harness longs
    the top of the signal and shorts the bottom, so the long leg holds the
    lowest funding and the short leg the highest.
  - A week, not a day: Binance's rate equals its neutral interest rate
    whenever the premium index sits within 0.05% of it (the clamp in
    `F = P + clamp(I - P, ±0.05%)`), so on a single day many names print
    exactly neutral (on BitMEX, BTC's funding printed exactly 0.01% for 78%
    of 2025-Q3, a holdout statistic listed under Seen before). A week's mean
    separates the names that sat there from those that did not.
  - **Ties**: names at their neutral rate all week still tie, exactly or up
    to floating-point rounding, also across 8h, 4h and 1h contracts
    (3 × 0.0001, 6 × 0.00005 and 24 × 0.0000125 sum to the same double).
    The harness breaks equal signals by symbol, which would fill a leg's
    tied slots alphabetically: the digit-prefixed "1000x" contracts first
    in the long leg and the last symbols in the short leg, a fixed tilt in
    whichever leg the neutral block reaches. The panel removes it at the
    source. It counts the window's sum in steps of 1e-9, so sums that
    differ only by floating-point rounding are an exact tie; Binance's
    archives print rates with 8 decimals, so distinct sums are multiples of
    1e-8 and stay 10 steps apart (a rate with more decimals would only make
    sums closer than about 1e-9 count as tied). A tie is then ordered by a
    number drawn from the sha256 of the symbol and the row's `ts`, which
    moves the value by less than a tenth of a step: it reads no market
    data, is the same in every build, and changes from day to day, so tied
    slots are filled at random and no symbol is favoured across decisions.
    With very many names tied at a large sum two draws can round to the
    same value (near the neutral rate the draw has about 2e8 distinct
    values at seven days); such a pair falls back to the order by symbol. A
    zero is never written as `-0.0`.
- **Rank and availability**: as for momentum. `latency_bars: 1`.

## Periods

The same as `exp_xsmom_usdm`:

| Period | Days | Dates (UTC, daily closes) |
| --- | --- | --- |
| Warm-up and train (nothing is decided or fitted) | 371 | 2020-01-01 to 2021-01-05 |
| Validation: 16 expanding walk-forward test folds of 91 days | 1,456 | 2021-01-06 to 2024-12-31 |
| Unused | 0 | none |
| Holdout (strategy unscored; its market seen) | 608 | 2025-01-01 to 2026-08-31 |

| Horizon | Days per period | Per fold | Validation (16 folds) | Holdout |
| --- | --- | --- | --- | --- |
| 7 | 8 | 11 | 176 | 75 |
| 14 | 15 | 6 | 96 | 40 |

- **The holdout** is 608 days, 1.6658 years of 365 days. It starts with the
  holdout of `exp_tsmom_btc` and `exp_funding_btc` but ends a month
  earlier: theirs runs to 2026-09-30.
- **Sample floors**: `min_trades_validation` 72, `min_trades_holdout` 30,
  `min_folds` 16. The holdout floor leaves the h14 configs a quarter of
  their 40 periods (up to 157 skipped decision days) and the h7 configs 45
  of their 75.
- **Skips**: a 10% leg needs 40 names with a `carry_7d` for 4 names a leg
  (the floor); below 40 the decision is skipped and the schedule moves a
  day. How often the universe falls below 40 is a shape-only count before
  merge, and a config whose periods it would push below a floor leaves the
  grid by the rule in
  [`exp_xsmom_usdm`'s Checks before merge](exp_xsmom_usdm.md#checks-before-merge).

The holdout is read once, and only if validation selects a config.

## Seen before

No cross-sectional return of any kind had been computed in this project, no
panel of this universe had been built, and none of the work behind this
document read a price or funding series of a contract other than BTCUSDT.
These had been seen:

- **BTC's funding and market**: from `exp_tsmom_btc` and `exp_funding_btc`,
  a constant BTC long paid 58.1% in funding over their validation
  (2020-09-21 to 2024-11-28), above the neutral rate's about 46%, and 7.3%
  over their holdout (2025-01-01 to 2026-09-30, this holdout plus 2026-09),
  below its about 19%; that holdout's mean BTC funding was about 0.000038
  per 8h. BTC fell 10.7% over that holdout and rose 776.8% over their
  validation. Funding was therefore high in validation and low in the
  holdout, at least for BTC; a carry book earns most when the spread
  between names is wide. BTC's 2026-09, the first month of the forward
  test, is inside those figures.
- **BitMEX's BTC funding in 2025-Q3**, inside the holdout: it printed
  exactly 0.01% for 78% of the quarter. It is quoted under Signal to
  explain why a single day is mostly at the neutral rate; the 7-day window
  itself is the one `exp_funding_btc` and the panel template use. It is
  holdout-period knowledge about how often funding sits at neutral.
- **The earlier funding study was `no_edge`**: contrarian funding on BTC
  alone, all four configs positive but none significant.
- **Published results on overlapping years**:
  - Borri et al. (2026): BTC's carry Sharpe was 6.45 over 2020 to 2025 and
    4.06 from 2024, and turned negative in 2025, inside the holdout.
  - Christin et al. (2020-08 to 2022-06), Chi et al. (2017-11 to 2021-03)
    and BIS WP 1087 cover the first validation years.
  - Fayez Junior (2022-07 to 2026-04): negative, on most of validation and
    the holdout.
- **The universe file's shape, the draft non-crypto list's classification
  by recognition, and the microstructure snapshots**, as listed in
  [`exp_xsmom_usdm`](exp_xsmom_usdm.md#seen-before).
- **Between the draft and this revision no strategy return was computed.**
  The panel change (#145) and its review rounds, and the reviews of these
  drafts, used synthetic data only. This revision moved the panel's end to
  2026-09-01 and set the alpha, the holdout rule and the reading of the
  overfitting measures; none of it read data.
- **Shape-only counts from the panel**, from `exp_xs_usdm_shape.py`
  (output in `exp_xs_usdm.shape.json`): `<SHAPE_COUNTS>`, and for carry the
  ranked rows with a rate but no `carry_7d`, and those with an uncovered
  day in their carry window, which the covered-day mean would have
  dropped: `<CARRY_PRESENCE>`. As for momentum, they read no price, no
  funding value and no return. Gate outcomes and any remedy applied:
  `<GATE_OUTCOMES>`.
- **No `carry_7d` value is read before the run.** An earlier draft planned
  to count ties on validation days before merge, which would have read
  funding values; it never ran. The hashed tie-break made it unnecessary,
  and tie counts are now a post-run diagnostic.
- **The grid is not independent of what was known**: the BTC funding facts
  above, the papers, and the common knowledge of these years (the 2021
  boom, the 2022 collapses, the 2025-26 decline).
- **More was considered than the four configs.** Funding windows of 1, 3,
  14 and 30 days, holds of 1 and 3 days, 30% legs, a funding tilt against
  the neutral rate (a constant shift, which ranks the same) and rank-tiered
  costs were considered on the literature and dropped before any data was
  built. Across S4 there are eight configs; Holm and the deflated Sharpe
  ratio count this family's four only (see Hypothesis for the two families
  together).

Because of BTC's holdout market and funding and the published results on
overlapping years, a pass on this holdout is not enough on its own; see the
decision rules.

## Grid

These four configs are the whole family:

| id | quantile | horizon | Meaning |
| --- | --- | --- | --- |
| `q10-h7` | 0.1 | 7 | Long the tenth of the universe with the lowest 7-day funding, short the highest tenth; hold 1 week |
| `q10-h14` | 0.1 | 14 | The same; hold 2 weeks |
| `q20-h7` | 0.2 | 7 | Long the lowest fifth, short the highest fifth; hold 1 week |
| `q20-h14` | 0.2 | 14 | The same; hold 2 weeks |

- **Direction**: `signed`. A full universe of 50 gives 5 names a leg at 0.1
  (4 from 40 to 49 names) and 10 at 0.2, each at `0.5 / names` of the
  capital.
- **Multiple testing**: Holm across the four configs at alpha 0.05; the
  deflated Sharpe ratio counts them as four trials.
- **Which config goes to the holdout**: the highest validation mean net
  return per period at 1.0× among the survivors, not the highest Sharpe.
  That favours the 2-week holds, which collect more funding per period;
  rule 2 then tests that config on the holdout. `q10-h7` is the primary
  config, which is only reported. PBO ranks configs the same way, which
  shapes how rule 3 reads it (as for momentum).
- **Funding window**: 7 days, the length the panel template uses and the
  one `exp_funding_btc` chose for the same reason: one day is noisy and
  mostly at the neutral rate, and crowding that matters should persist.
  Its days are traded days with a rate, not covered days (see Universe).
- **Horizons**: a week, the signal's own length, and 2 weeks, which collects
  about twice the funding per round trip. Holds of 1 and 3 days were
  dropped: at 20 bp a round trip they cost about 36% and 18% a year, more
  than the 9.6% to 34% a year Christin et al. find for hedged carry per
  coin, so they would mostly test costs. Chi et al.'s daily premium is
  measured on dated futures, whose return includes their convergence to
  spot, which a perp does not have, and it is gone at weekly holds.
- **Quantiles**: the funding signal lives in the tails, and the middle of
  the ranking is full of names at the neutral rate. 0.1 takes only the
  tails; 0.2 is the momentum family's quintile, so the two families' legs
  can be compared in the overlap diagnostic.
- **Floor**: `min_names_per_leg` 4, so the 10% leg trades from 40 names.

## Costs and funding

The same as [`exp_xsmom_usdm`](exp_xsmom_usdm.md#costs-and-funding): 4.5
bps fee, 4.0 bps slippage and 1.5 bps half-spread per side, a 20 bp round
trip, stressed at 1.5× and 2.0×; Binance's realized funding position by
position on every held day, stressed adversely payment by payment in the
same multiplier.

For carry the stress bites twice: at 2.0× the short leg receives half its
funding and the long leg pays double. Validation's positive net mean at
2.0× (rule 1) is therefore a strong requirement for this family, by design:
the funding is Binance's realized funding, which need not repeat, nor
carry over to Hyperliquid. The holdout requires a positive mean at 1.5×
and reports 2.0×.

The flat 10 bp a side assumes legs spread evenly over ranks 1 to 50. A
carry leg holds the tails of the funding ranking, and its short leg the
most crowded, squeeze-prone names, which may sit among the thinner ones.
The overlap diagnostic reports each leg's mean `volume_rank` and share in
ranks 31 to 50 on validation days, and the reading fixed under
[momentum's Costs](exp_xsmom_usdm.md#costs-and-funding) applies: for a leg
whose mean rank is above 30, or more than half of whose names sit in ranks
31 to 50, the 2.0× figures are the realistic ones. There is no cost
evidence from 2021 to 2023.

## Fill model and sizing

As for momentum: decide at a day's close, fill at the next, exit `horizon`
days later; one period at a time; equal weight within each leg, half the
capital a leg (`sizing: unit`); non-fills sit idle and a leg below the
floor at the fill unwinds at cost; a halted or delisted contract exits at
its last traded close; funding is charged on every held day with a row. No
stops, and no averaging down.

## Benchmarks

Cash (zero), as for momentum; the harness's buy-and-hold benchmark does not
apply to a panel. The market enters through beta, as for momentum (see
[its Benchmarks](exp_xsmom_usdm.md#benchmarks)):
`docs/experiments/exp_xs_usdm_beta.py` runs only after the harness has
scored the selected config on the holdout, rebuilds its periods with the
pinned code, and regresses their gross return on the equal-weight return
of the decision day's universe and on BTCUSDT's over the same fill and exit
days. A carry book can carry beta too: its short leg holds the
high-funding names, which tend to be the recent winners.

## Decision rules (committed now)

All figures are the selected config's, from `result.json`, except rule 4's
beta, from `beta.json`. A **pass** needs rules 1, 2, 4 and 5. Rule 3 is
reported with a fixed reading and decides nothing.

1. **Validation, as the harness does it**: a config survives when it has at
   least 72 periods, its Holm-adjusted p-value at 1.0× is at most 0.05, and
   its validation net mean is positive at 1.0×, 1.5× and 2.0×
   (`_survives_validation` in `src/research/harness/evaluate.py`). The
   harness takes the survivor with the highest validation mean per period
   to the holdout. With no survivor, `result.json` has no `holdout` block
   and the family stops.
2. **Holdout**: the selected config, scored once on the holdout with at
   least 30 periods:
   - its net mean per period is > 0 at 1.0× and at 1.5×
     (`holdout.net["1.0"].mean_return` and `holdout.net["1.5"].mean_return`);
   - its conservative p-value at 1.0× (`holdout.net["1.0"].p_value`) is
     ≤ 0.10. There is no Holm step on the holdout. With iid returns that
     needs a t of at least 1.29 (h7, 75 periods) or 1.30 (h14, 40 periods),
     an observed annualized net Sharpe of about 1.0.
   - Reported, not tested: the net mean at 2.0×, and the annualized net
     Sharpe at each stress, `sharpe_per_trade × sqrt(trade_count / 1.6658)`
     from the holdout's `net` blocks, with 1.6658 = 608 / 365.
   - This rule is decided from these `result.json` values, not from the
     harness's label, which says `passes_h1` only when p ≤ 0.05 and the
     2.0× mean is positive too. A holdout can pass this rule under the
     label `interesting_but_fragile`.
3. **Overfitting, reported with a fixed reading, not a gate**: the deflated
   Sharpe ratio and the PBO, as the harness reports them on validation net
   returns at 1.0× (`overfitting.deflated_sharpe.dsr` and
   `overfitting.pbo.value`; a null value is reported as not computed, with
   its block's `note`), read exactly as rule 3 of
   [`exp_xsmom_usdm`](exp_xsmom_usdm.md#decision-rules-committed-now) fixes,
   and not gates for the same reasons: Holm on validation and an
   independent holdout test already control selection, a deflated Sharpe
   ratio of 0.95 needs an iid t of about 2.7 to 2.9 at these sample sizes,
   and PBO on this grid of 8-day and 15-day periods is at or below 0.5 in a
   third to a half of pure-noise runs. Carry's returns are more likely to
   be skewed and fat-tailed, which the deflated Sharpe ratio takes into
   account; the results document reports its skew and kurtosis.
4. **Carry, priced fairly, and not the market's direction**. The hypothesis
   is the carry premium: the book earns the funding spread, and price may
   give some of it back. So:
   - the holdout funding mean per period (`holdout.funding.mean_return`,
     positive when received) is > 0.
     This is a sanity check that the book was a carry book, not evidence:
     the legs are sorted on recent funding, which persists, so it is close
     to always true.
   - **price not significantly against the book**: the holdout `gross`
     block's `p_value` (`holdout.gross.p_value`) is < 0.95. That `p_value`
     is the larger of the iid and HAC one-sided p-values for a positive
     mean, so 0.95 or more means at least one of the two tests puts the
     gross mean below zero at the one-sided 5% level. The price legs may take back part of the funding,
     as the Against case expects, but not by a margin the data can tell
     from zero. With no price effect at all this part fails a little more
     than one time in twenty, since either test can trigger it.
   - the **beta-adjusted holdout net mean** at 1.0× per period, from
     `beta.json` (`beta_adjusted.net_mean_1x`, beta to the equal-weight
     universe), is > 0: the net return remains after removing what the
     book's market beta earned over the holdout. A beta the script cannot
     compute, or a script that fails closed, fails this rule, under the
     same terms as momentum's rule 4.
   - Reported with a fixed reading, not tested: beta to the universe and to
     BTC with HAC t-statistics, and each leg's gross mean. A beta with
     |t| ≥ 2 means the book carried market exposure, which the forward
     test must hedge or carry in its own hypothesis.
5. **Data the result rests on**: in the selected config's holdout
   `portfolio` block, with positions = `(periods - unwound_periods) ×
   (mean_long_names + mean_short_names)` and position-days = positions ×
   `horizon_bars`, `uncovered_funding_days + unfunded_halt_days` ≤ 1% of
   position-days and `forced_exits` ≤ 1% of positions. A carry book's
   short leg holds the most crowded names, so a funding day the data cannot
   prove whole, or a delisting exit at an assumed price, weighs more here.
   Fill sanity, as for momentum: `mean_long_names` and `mean_short_names`
   differ by at most 5% of their mean, which only catches a leg that keeps
   failing to fill; market neutrality is rule 4's beta.

The outcome is then exactly one of these:

| Result | Outcome |
| --- | --- |
| A pass (rules 1, 2, 4 and 5; whatever the harness label, `passes_h1` or `interesting_but_fragile`) | A pre-registered forward test on data from 2026-09-01 onward, none of whose values this study uses (the build opens the 2026-09 funding files only for its end-of-window interval check, see [momentum's Data](exp_xsmom_usdm.md#data-point-in-time), and BTC's 2026-09 was seen through `exp_tsmom_btc` and `exp_funding_btc`; the forward test's pre-registration states both), before any PAPER strategy design and never directly to capital. Its pre-registration is fixed before any of that data is read for this strategy. It must use Hyperliquid's own funding for contracts listed there (hist_etl's `hyperliquid` funding source), restrict the universe to them, and measure the book's beta to an equal-weight universe and to BTC. The holdout's market was seen (see [Seen before](#seen-before)), so a pass here is not enough on its own. |
| No config selected (`no_edge`, `interesting_but_fragile` or `not_enough_data`, with no `holdout` block), a holdout under 30 periods, or a selected config that fails rule 2, 4 or 5 | This family stops on this universe. A stop is not evidence that no premium exists: see Power under [Limitations](#limitations). |
| A failed-closed run whose `failure_kind` is `unsafe_mode`, `data_config`, `spec` or `lock`: raised by the environment (`TRADING_MODE`, `RESEARCH_ENV`, `RESEARCH_IMAGE_DIGEST`), the checkout or the spec's configuration, not by any value of the panel | Nothing was scored. Fix the cause and run again in a new `STUDY_DIR`; the results document records every attempt. |
| A failed-closed run of any other kind (`funding`, `schema`, `invariant`, ...): raised by the panel's values or the scoring | This family stops; the cause is recorded, and any new attempt is a new pre-registration. |
| The pinned table cannot be rebuilt (the fingerprint check fails, for example because Binance Vision republished or withdrew an archive after the shape-only build) | The family stays unrun. Any new pin is a new pre-registration that records why. |

The 10% configs skip a decision whenever fewer than 40 ranked names have a
`carry_7d`, which a day in the week that did not trade or has no rate may
cause. The shape-only check counts those days before merge, and a config
they would push below a floor leaves the grid then, so a thin holdout
cannot become an outcome that never refutes the 10% configs.

The harness's `promotion_decision` is not this decision. It says
`paper_candidate` only with the label `passes_h1`; rules 2 to 5 are applied
in the results document. A `paper_candidate` that fails rule 2, 4 or 5 is
not acted on, and a pass under the label `interesting_but_fragile` leads to
the forward test all the same.

The momentum family is decided on its own. Its outcome changes nothing
here, and this family's alpha stays 0.05 whether or not the momentum study
runs. If both pass, the overlap diagnostic says how far they are one bet.

When the family stops:

- The funding window, quantiles, horizons and universe are not re-tuned on
  this data.
- A new variant needs a new pre-registration and evidence from data from
  2026-09-01 onward.

## Robustness (reported)

- Joint stress at 1.5× and 2.0×, one multiplier for costs and funding.
- HAC p-values; the folds are concatenated, so fold boundaries are treated
  as contiguous.
- The deflated Sharpe ratio and PBO by CSCV: 16 blocks of one fold each,
  none left out, read as rule 3 fixes.
- The grid itself: quantile and horizon.
- The split of each config's return into price (gross) and funding, on
  validation for every config and on the holdout for the selected one.
- Per config, from the `portfolio` block: periods, skipped decisions, mean
  names a leg, unwound periods, forced exits, uncovered funding days and
  unfunded halt days, and the mean weight.
- The breakeven cost per side, `(gross mean + funding mean) / (2 × mean
  weight)`.
- Annualized Sharpe at each stress, by the formula of rule 2; on validation
  as a descriptive figure with 3.989 = 1,456 / 365.
- From `docs/experiments/exp_xs_usdm_overlap.py`, on validation days only
  (it reads no holdout day and decides nothing):
  - the overlap with momentum: the Spearman correlation of `carry_7d` and
    `ret_14d`, and how many names the legs share. A strongly negative
    correlation would mean this book is largely short momentum;
  - where the legs sit: each leg's mean `volume_rank` and share in ranks 31
    to 50, against the universe's (read as fixed under Costs);
  - ties: per quantile and leg, how often the leg's boundary falls inside a
    block of equal funding sums (in the panel's steps of 1e-9), how many
    slots such blocks fill, and how many of those slots, against the
    universe's share, go to symbols that start with a digit. With the
    hashed tie-break the two shares should be close; a clearly higher share
    in one leg would mean the tie-break does not work as described, and the
    results document says so.
- From `docs/experiments/exp_xs_usdm_beta.py`, for the selected config's
  holdout only: each leg's gross mean, and beta to the equal-weight
  universe and to BTC with HAC t-statistics.

## Limitations

- **Proxy funding**: Binance's realized funding stands in for
  Hyperliquid's, which settles hourly, at its own level and cap, and whose
  history starts in 2023. Of everything in this study, the funding transfers
  least.
- **Data terms**: Binance Vision's terms (version 1.0 of 2026-08-26, CC
  BY-NC-SA 4.0) allow personal, non-production research backtests such as
  this one. Using signals derived from Binance data in live trading is a
  separate decision for Chupa before any LIVE step.
- **Ties**: names at the neutral rate are ordered at random by the hashed
  tie-break. That removes the alphabetical tilt but not the dilution: tied
  slots hold names the signal does not tell apart, which weakens the sort
  without biasing it. The tie counts say how many slots that is.
- **Funding window over traded days with a rate**: a day the close cannot
  prove whole enters the 7-day mean at its recorded sum, short by at most
  the missing part of one day's funding, which makes the name's funding
  look lower and may pull it into the long leg (see Universe; the study
  tolerates any number, and the count is under Seen before). Names with a
  day in the week that did not trade or has no rate are still left out.
- **Flat archive funding**: a delisted contract's flat archive days keep a
  default rate, which a held short is credited until its exit; rule 5 and
  `forced_exits` bound it. The signal never reads those untraded days.
- **Uncovered funding**: charged as recorded, at most one settlement off a
  day; rule 5 tolerates 1% of position-days.
- **Delisting exits, survivorship (GAIBUSDT has no panel row), tickers
  that are not one asset (renamings; LITUSDT ranked again at once after its
  funding gap), market beta measured only on the holdout, schedule and
  equal weight**: as in [`exp_xsmom_usdm`](exp_xsmom_usdm.md#limitations).
- **Small legs**: a 10% leg of 4 or 5 names puts 10% to 12.5% of the
  capital on each, and the short leg holds the most crowded, squeeze-prone
  names. Heavy tails slow the t-tests' approach to normality at 40 to 176
  periods.
- **Power**: as for momentum (see its table, from
  `docs/experiments/exp_xs_usdm_power.py`): one config passes validation
  and the holdout with probability about 0.17 to 0.18 at a true net
  annualized Sharpe of 1.0, 0.33 to 0.34 at 1.25 and 0.52 to 0.53 at 1.5,
  before rules 4 and 5. A family with no edge passes in about 3 runs in
  1,000 in the simulation, about 4 to 5 with independent configs, and at
  most about 5 in 1,000 in general (0.05 × 0.10). For carry the table is a
  looser upper bound: the model leaves out the stress of funding, and at
  2.0× validation halves the funding received and doubles the funding
  paid, so a book whose return is mostly funding can fail that stress while
  its 1.0× mean is significant. A stop is not evidence that no premium
  exists.
- **Exploration outside the harness** is not deflated (see Seen before).

## Run (after this document is merged)

The same as [`exp_xsmom_usdm`](exp_xsmom_usdm.md#run-after-this-document-is-merged),
with this document, this spec and its hash. It builds the same table into
its own `STUDY_DIR`, so the two families can run in either order; the
archive root is shared, so the second sync has little left to fetch.

```bash
export REPO_ROOT=...            # the repository checkout, on main
export HIST_ARCHIVES_ROOT=...   # the hist_etl archive root, outside data-capture
export STUDY_DIR=...            # must not exist yet; outside the repository
(
  set -euo pipefail
  cd "$REPO_ROOT"
  # Run from main as merged: HEAD is on origin/main, so commit.txt is too.
  git fetch --quiet origin +main:refs/remotes/origin/main
  git merge-base --is-ancestor HEAD origin/main
  # ... with these decision rules, the exclusion list and the overlap and
  # beta scripts as they stand on main today. The spec is checked by its
  # hash below, the exclusion list also by the sha256 the build was made with.
  git diff --exit-code origin/main -- docs/experiments/exp_xscarry_usdm.md \
    config/research/binance-um-usdt-non-crypto-2026-10-08.json \
    docs/experiments/exp_xs_usdm_overlap.py docs/experiments/exp_xs_usdm_beta.py
  echo "37dc30be8a1aa95c983ec0d2e091ed9297d9f452e85fd51ee03b75fbff5d5348  config/research/binance-um-usdt-non-crypto-2026-10-08.json" \
    | sha256sum -c -
  # STUDY_DIR stays outside the repository, so the run cannot dirty it.
  study="$(realpath -m "$STUDY_DIR")"
  repo="$(realpath "$REPO_ROOT")"
  case "$study/" in "$repo"/*) echo "STUDY_DIR is inside the repository" >&2; exit 1 ;; esac
  pin=0cd7666392928b0af3f4a0c0ef5c8fc34a516d58
  pinned=(src uv.lock pyproject.toml .python-version
    config/hist_etl/universe/binance-um-usdt-1d-2026-10-08.json)
  # The pre-registered code: no change, tracked, untracked or ignored.
  git diff --exit-code "$pin" -- "${pinned[@]}"
  changed="$(git status --porcelain --ignored --untracked-files=all -- "${pinned[@]}")"
  stray="$(printf '%s\n' "$changed" | grep -v -e '^$' -e '/__pycache__/[^/]*\.pyc$' || true)"
  test -z "$stray"
  # The whole working tree is clean, so commit.txt describes what runs.
  dirty="$(git status --porcelain --untracked-files=all)"
  test -z "$dirty"
  mkdir "$STUDY_DIR"
  git rev-parse HEAD > "$STUDY_DIR/commit.txt"
  # Bytecode only from a fresh folder: no cached .pyc from the checkout runs.
  export PYTHONPYCACHEPREFIX="$STUDY_DIR/pycache"
  # Exit 2 is normal while the current month is open or a contract delisted
  # after the universe file stopped publishing, but hist_etl also reports
  # some data failures as gap lines with exit 2. An `error` line or any
  # other code stops here; for the rest, the fingerprint check below is the
  # gate, and sync.log names the cause.
  status=0
  PYTHONPATH=src uv run --frozen python -m research.hist_etl sync \
    --dataset bn-um-usdt-1d 2>&1 | tee "$STUDY_DIR/sync.log" || status=$?
  test "$status" -eq 0 || test "$status" -eq 2
  if grep -q '^error' "$STUDY_DIR/sync.log"; then exit 1; fi
  cp docs/experiments/exp_xscarry_usdm.spec.yaml "$STUDY_DIR/spec.yaml"
  PYTHONPATH=src uv run --frozen python -m research.bar_tables panel \
    --group bn-um-usdt-1d --start 2020-01-01 --end 2026-09-01 \
    --lookbacks 14 --vol-window 30 --volume-window 30 --funding-window 7 \
    --exclude-symbols config/research/binance-um-usdt-non-crypto-2026-10-08.json \
    --rank-requires-funding --out "$STUDY_DIR/panel.parquet"
  PYTHONPATH=src uv run --frozen python -m research.harness lock "$STUDY_DIR/spec.yaml"
  PYTHONPATH=src uv run --frozen python -c '
import json, sys
lock = json.load(open(sys.argv[1]))
found = (lock.get("spec_sha256"), (lock.get("data_fingerprint") or {}).get("fingerprint_sha256"))
expected = (
    "457009762711c0ac113719360fc7d04431f67f63ce10a88a6d02b74e0f0db44a",
    "<DATA_FINGERPRINT>",
)
sys.exit(0 if found == expected else f"not the pre-registered spec and table: {found}")
' "$STUDY_DIR/spec.yaml.lock.json"
  PYTHONPATH=src uv run --frozen python -m research.harness run "$STUDY_DIR/spec.yaml" \
    --output-dir "$STUDY_DIR/out"
  # Rule 4's beta: only when the harness scored a selected config on the
  # holdout. The script fails closed on any other result.json. It runs
  # before the overlap script, so that one cannot keep it from running.
  if PYTHONPATH=src uv run --frozen python -c '
import json, sys
sys.exit(0 if json.load(open(sys.argv[1])).get("holdout") else 1)
' "$STUDY_DIR/out/result.json"; then
    PYTHONPATH=src uv run --frozen python docs/experiments/exp_xs_usdm_beta.py \
      "$STUDY_DIR/spec.yaml" "$STUDY_DIR/out/result.json" > "$STUDY_DIR/beta.json"
  fi
  # Descriptive only, validation days only; it decides nothing.
  PYTHONPATH=src uv run --frozen python docs/experiments/exp_xs_usdm_overlap.py \
    "$STUDY_DIR/panel.parquet" > "$STUDY_DIR/overlap.json"
)
```

The study runs only after both checks pass. If the fingerprint check fails,
`sync.log` shows whether an archive inside the range is missing.

The beta script runs before the overlap script, so a failure of the
descriptive overlap script cannot leave rule 4 without `beta.json`. Such a
failure decides nothing: the overlap script is fixed in a reviewed PR and
run again on its own. A `beta.json` missing because the beta script never
ran is produced by running it; a beta script that failed closed is read as
rule 4 says.

If code under `src` or another pinned file has changed on main since this
was merged, the pin check refuses the newest main. Then check out the
commit that merged this document instead: it is on main, it holds this
document, and its code is the pinned code. Never edit the pin.

The results go into `exp_xscarry_usdm.results.md` in a separate PR. That PR
records the commit from `commit.txt`, the lock file, `result.json`,
`overlap.json` and, when the holdout was scored, `beta.json`, each with its
sha256, and applies the decision rules to their values.
