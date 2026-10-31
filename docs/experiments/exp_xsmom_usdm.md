# Experiment: cross-sectional momentum on Binance USD-M perps (exp_xsmom_usdm)

> **Draft, not mergeable yet.** The code it pins is on main: the S4 panel
> change merged in #145 (`694497a`), which adds `--exclude-symbols`,
> `--rank-requires-funding` and the carry column (`carry_7d` in this
> table). The frozen classification is complete. The current source/
> environment amendment and uncompleted data gates are recorded in
> [S4 readiness](exp_xs_usdm.readiness.md). Panel, rebuild and shape
> placeholders remain unmeasured; no strategy has been scored.

PAPER research only. This document **pre-registers** the cross-sectional
momentum study, one of the two families of step S4 of the research plan. The
other is the funding carry study,
[`exp_xscarry_usdm`](exp_xscarry_usdm.md). It is not an edge claim and not a
trading authorization.

It was written before any return of a cross-sectional strategy was computed
in this project, and before the panel it uses was built. What had been seen
is listed under [Seen before](#seen-before).

- Spec: [`exp_xsmom_usdm.spec.yaml`](exp_xsmom_usdm.spec.yaml).
- Canonical sha256, from `python -m research.harness hash`:
  `c8c5d93142e27e468c642fd576d8cc7f2263dd05b70bdc2a0998c77285fbbd08`.

Any change to the spec is a new pre-registration with a new hash, not a
re-run.

## Hypothesis

Cross-sectional momentum: coins that did better than other coins over the
last few weeks keep doing better over the next week or two. Liu, Tsyvinski
and Wu (JF 2022) find it at 1- to 4-week lookbacks with weekly holding over
2014 to 2018 (the working paper's sample), and nothing at 8 weeks or more.
Borri, Liu, Tsyvinski and Wu (2026) find the 2-week version (CMOM) after
2020 too, at 2.1% a week (t 3.70), and nothing at 12 or 24 weeks. Among
large and liquid coins the short-horizon effect is continuation; reversal
is a small-coin effect (Zaremba et al. 2021; Fičura 2023).

The evidence is also weaker than those headlines. Momentum is significant in
only about half of the research designs Fieberg et al. (CTREND, JFQA 2025)
tried, and "disappoints" after 2017. Han, Kang and Ryu find the
cross-sectional effect weak once costs are paid, with losers that rebound
sharply. Starkiller Capital's out-of-sample spot test lost money. LTW's
effect is not significant in Fama-MacBeth regressions, which weight coins
nearly equally, as this study does. A top-50 equal-weight perp universe is
the least-studied case.

- **H0**: the long-short portfolio's mean net return per period is ≤ 0 after
  costs and funding.
- **H1**: the long-short portfolio's mean net return per period is > 0 after
  costs and funding, on the holdout, scored once. No cross-sectional
  strategy has been scored there, but the holdout's market has been seen
  (see [Seen before](#seen-before)), so it is not untouched; the forward
  test in the decision rules is the out-of-sample check on data untouched
  by any strategy of this study.

The test is balanced, not as strict as possible. This study is a screen: a
pass leads only to a forward PAPER test on data untouched by any strategy
of this study, which every pass needs before any strategy design and which
never leads directly to capital. So validation uses alpha **0.05** with
Holm across the grid's four configs, and the holdout a one-sided p-value of
at most 0.10 (rule 2). Each p-value is the larger of the iid t-test's and
the Newey-West HAC t-test's.

S4 tests two families, each at 0.05. When neither has an edge, the chance
that validation selects a config in at least one of them is at most about
0.10. That is accepted because of what a pass buys. With the holdout rule
on top, a family with no edge passes end to end in about 3 runs in 1,000
in the simulation, about 4 to 5 with independent configs, and at most about
5 in 1,000 in general (0.05 × 0.10; see Power under
[Limitations](#limitations)).

## Universe

Binance USD-M USDT perpetuals, every contract in the committed universe file
[`binance-um-usdt-1d-2026-10-08.json`](../../config/hist_etl/universe/binance-um-usdt-1d-2026-10-08.json),
delisted ones included. On each decision day the universe is, point in time:

1. the contracts that traded that day (`traded`), have a 30-day mean quote
   volume, are not on the non-crypto list below, and have a funding rate
   that day, ranked by that mean (`volume_rank`, 1 the largest; a tie goes
   to the symbol that sorts first);
2. of those, the 50 best ranked (`volume_rank <= 50`);
3. of those, the ones with a known 14-day return. A ranked name without one
   (a halt in the last 14 days) leaves the universe smaller than 50; the
   51st name does not replace it.

- **Non-crypto contracts are left out before ranking.** Their prices follow
  stock, commodity or currency markets, not crypto, and an index contract is
  a basket of other members. The rule: a contract is left out when its
  underlying is not a crypto-native token, which covers an index, a fiat
  currency or a token pegged to one at the contract's listing, a commodity
  or a token backed by one, an ETF or ETP, and a listed or private company's
  equity. A contract that cannot be verified as a crypto-native token is
  left out too.
  - **Every contract is classified the same way**, kept and excluded alike:
    from Binance's contract metadata (`exchangeInfo` `underlyingType` and
    `underlyingSubType`), else Binance's listing or delisting announcement,
    else, for a contract Binance no longer lists, a public token registry
    entry for that ticker's token. The class, source and fields of all 896
    symbols are in `config/research/binance-um-usdt-classification-2026-10-08.json`
    (sha256 `065f7ba001e4679ec22d2ca3ef5567dcd277ce0fbe0b7eab6b5078f96caacad7`); the list is its non-crypto symbols.
    The classification reads no price, volume or funding.
  - The list is `config/research/binance-um-usdt-non-crypto-2026-10-08.json`
    (sha256 `37dc30be8a1aa95c983ec0d2e091ed9297d9f452e85fd51ee03b75fbff5d5348`):
    221 contracts: 159 `equity`, 38 `etf_or_etp`, 10 `commodity`, 6 `index`, 6 `private_company`, 2 `fiat_pegged_or_fx` and none `unverified`; six of them list before the holdout, the indices BTCDOM, DEFI, DOTECO, FOOTBALL and BLUEBIRD, and USDC.
  - The panel builder takes it with `--exclude-symbols` (#145). A listed
    contract keeps its rows and features, but its `volume_rank` is empty on
    every row, so the next contract moves up; a filter applied after the
    rank would shrink the top 50 instead. The build fails, writing nothing,
    on an unknown or missing key, a format other than 1, an empty rule, a
    category outside `index`, `fiat_pegged_or_fx`, `commodity`,
    `etf_or_etp`, `equity`, `private_company` and `unverified`, a duplicate
    key, a `universe` that is not the file name of the group's universe
    entry, or a symbol the universe file does not hold. Its success line
    names how many symbols the list held and the list's sha256.
  - The draft of this document had a list made from tickers and listing
    months alone (`exchangeInfo` refused the container with HTTP 451): 239
    contracts, of which 52 `unverified`, and six listing before the
    holdout, the indices BTCDOM, DEFI, DOTECO, FOOTBALL and BLUEBIRD, and
    USDC. Its classifier was the author's recognition, which checked no
    kept ticker; see [Seen before](#seen-before). It is replaced by the
    classification above.
  - By the rule, USTC stays in: CoinGecko's record of the token says it lost
    its USD peg in May 2022 and has traded freely since, and Binance launched
    the contract on 2023-11-27. FRAX, STABLE, STBL and USUAL stay in too:
    CoinGecko maps their contracts to the governance tokens `frax-share`,
    `stable-2`, `stbl` and `usual`, none of them pegged. USDC, PAXG and XAUT,
    typed `COIN` by Binance, are left out by the same check (a fiat-backed
    stablecoin and two gold-backed tokens).
  - The 16 "1000x" contracts (1000PEPE, 1000SHIB, ...) are crypto with a
    price multiplier and stay in; the multiplier does not change returns.
- **A ranked contract has funding that day** (`--rank-requires-funding`,
  #145). The harness charges funding on every held day and fails the whole
  run closed on a traded day without a rate. Some contracts traded for
  months before Binance Vision has any funding archive for them: ICPUSDT
  from 2021-05 to 2022-08, TLMUSDT from 2021-07 to 2023-02, BNXUSDT from
  2022-04 to 2023-01, and single months of QTUMUSDT (2020-02), LENDUSDT
  (2020-11), BTCSTUSDT (2022-01, between its two funding runs) and JUPUSDT
  (2024-01). The edges of a funding run may also hold traded days without
  a rate where the builder allows it: the rest of a listing month before
  the first settlement, and the rest of a delisting month after the last.
  Every traded day without a rate is dropped from the rank, so the
  contract neither enters the universe nor takes a rank that day. A run's
  first day at its dataset's start (the first of its first month, or a
  manifest `start`) is another case: hist_etl keeps no settlement stamped
  before a dataset's start, so that day may miss its midnight settlement,
  but it loses at most that one and normally keeps a rate. It is then
  ranked as an uncovered day (rule 5), and it counts toward `carry_7d` only
  when covered. The rule reads only the day's own settlements, known at
  its close, and was set from archive month lists, not prices. ICPUSDT's
  well-known fall after its 2021 listing is therefore outside this
  universe.
  - The rule does not stop a position decided on a ranked day from holding
    **into** such a day, and the harness fails the whole run closed when
    one could. BTCSTUSDT decided in late 2021-12 and held into 2022-01 is
    the validation case. From 2025-07 on, 131 contracts have klines months
    without a funding month: 130 after their funding archive ends, and
    LITUSDT in its funding gap (see Rank). 124 of them have such months
    inside the panel; among the latest, ATAUSDT, MLNUSDT, PHBUSDT and
    SYSUSDT from 2026-06, TONUSDT, IPUSDT, COSUSDT, DUSDT, HIGHUSDT and
    MBOXUSDT from 2026-07, and NFPUSDT from 2026-08. Seven more have them
    only from 2026-09, after the panel's end, though their delisting month
    2026-08 may still hold traded days after the last settlement. Most are
    delisted contracts whose flat, untraded archive Binance keeps
    publishing, but the month lists cannot show which still traded.
  - The shape-only check before merge counts every such day any ranked
    name could hold (`reach.traded_days_without_rate` in
    `exp_xs_usdm.shape.json`) and requires 0 (the count is under Seen
    before). What is done when it is not was fixed before the count (see
    [Checks before merge](#checks-before-merge)): first a later universe
    snapshot, then a harness rule, the same for every symbol, that exits a
    position at the close of such a day as a forced exit.
- **Equal weight.** Each leg holds half the capital, split equally over its
  names. LTW's Fama-MacBeth null suggests equal weighting is the weaker
  design; it is chosen because it is what the harness supports and what a
  small PAPER book would hold.
- **No hand-picking.** The universe comes from rank, trading and the two
  rules above. No contract is dropped or added after any return is seen.
- **A proxy for Hyperliquid.** The study trades Binance prices and funding.
  Hyperliquid listed 178 active perps on 2026-10-09, fewer than Binance, so
  only names listed on both could be traded in PAPER.

## Data (point-in-time)

- **Datasets** (hist_etl): the `bn-um-usdt-1d` universe entry, which expands
  to 1,794 per-symbol klines and fundingRate datasets. The universe file's
  sha256 is
  `add8d0f26d6b7c2bdf18963cf1f77752f0b1aa8a943a64c868e097951f4ec520`.
- **Table**: `research.bar_tables panel` with `--group bn-um-usdt-1d
  --start 2020-01-01 --end 2026-09-01 --lookbacks 14 --vol-window 30
  --volume-window 30 --funding-window 7 --exclude-symbols
  config/research/binance-um-usdt-non-crypto-2026-10-08.json
  --rank-requires-funding`. The two flags and the `carry_7d` column come
  from the S4 panel change (#145). `vol_30d` and `carry_7d` are unused
  here; the table is the one [`exp_xscarry_usdm`](exp_xscarry_usdm.md)
  uses, so both families score the same universe on the same days.
- **Shape**: `<ROW_COUNT>` rows, `<SYMBOL_COUNT>` symbols, `<RANKED_COUNT>`
  of them ranked on some day, on an evenly spaced axis of 2,435 daily closes
  from 2020-01-01 23:59:59.999 to 2026-08-31 23:59:59.999 UTC. Funding
  settles every 8, 4 or 1 hours depending on the contract; `funding_rate` is
  the day's sum, so it is a daily rate for all of them.
- **Why the panel ends with 2026-08**: for a funding run still published,
  the builder reads the funding month after the window, when synced, to
  check the funding interval at the window's end. That month, 2026-09, is
  the universe file's latest month and every still-published run has it,
  so every build of this table reads the same month files. Its settlements
  belong to no day of the panel and enter no value; the build reads them
  for that check only, computing nothing from their rates beyond refusing
  a non-finite one, and it also refuses a settlement there with an empty
  time or rate, an interval outside 1 to 24 hours, or a time not after the
  settlement before it.
- **Pre-registered table**: pinned by the harness data fingerprint that
  `lock` writes.
  - The data fingerprint is `<DATA_FINGERPRINT>`.
  - It covers the row count, the first and last `ts` (`1577923199999` and
    `1788220799999` expected), and the Parquet's sha256,
    `<PARQUET_SHA256>`.
  - Independent builds give identical bytes, also with 1, 2, 8 and 32
    DuckDB threads: `<REBUILD_CHECK>`. A hash says nothing about returns.
  - The run refuses any other table, whether a different builder, a broken
    sync, another exclusion list or another DuckDB version produced it.
- **Pre-registered code**: everything under `src` is pinned to commit
  `0cd7666392928b0af3f4a0c0ef5c8fc34a516d58`, the documented source/environment
  amendment described in [S4 readiness](exp_xs_usdm.readiness.md), together with `uv.lock`, `pyproject.toml`,
  `.python-version` and the universe file. That covers the harness (version
  6, panel portfolios), the builder, hist_etl, the locked dependencies and
  the interpreter. If a later commit touches `src`, `uv.lock`,
  `pyproject.toml`, `.python-version` or the universe file
  (`config/hist_etl/universe/binance-um-usdt-1d-2026-10-08.json`) before
  this document merges, the pin moves to that commit and the checks before
  merge are run again. The
  run checks the pin as [`exp_funding_btc`](exp_funding_btc.md) does,
  including untracked and ignored files, before it syncs anything. The
  exclusion list, the overlap script and the beta script are checked
  against main, and the list also against `37dc30be8a1aa95c983ec0d2e091ed9297d9f452e85fd51ee03b75fbff5d5348`.
- **Signal**: `ret_14d`, the log return from the close 14 days earlier to
  the decision day's close. It needs 15 consecutive traded days, so a
  listing has none for its first two weeks and a halt restarts it.
- **Rank**: `volume_rank` orders by `qv_30d`, the mean quote volume over the
  last 30 days with untraded days counted as zero. It needs 30 consecutive
  daily rows, so a listing is first ranked on its 30th day. A contract whose
  archive rows run unbroken across a break in trading or funding is ranked
  again on its first traded day with a rate, without new seasoning: LITUSDT
  has klines from 2021-02 to 2026-09 while its funding stops after 2025-06
  and resumes in 2025-12, which may be a relisting or another asset under
  the same ticker (`docs/runbooks/hist-etl.md`). It can re-enter the
  universe in 2025-12 at once.
- **Availability**: every value uses closes and settlements at or before
  its day's close, so it is available at that close. The spec uses
  `latency_bars: 1`: decide at a close, fill at the next one.

## Periods

One bar is one day of the panel's date axis.

| Period | Days | Dates (UTC, daily closes) |
| --- | --- | --- |
| Warm-up and train (nothing is decided or fitted) | 371 | 2020-01-01 to 2021-01-05 |
| Validation: 16 expanding walk-forward test folds of 91 days | 1,456 | 2021-01-06 to 2024-12-31 |
| Unused | 0 | none |
| Holdout (strategy unscored; its market seen) | 608 | 2025-01-01 to 2026-08-31 |

- **The holdout** is 608 days, 1.6658 years of 365 days. It starts on the
  same day as the holdout of `exp_tsmom_btc` and `exp_funding_btc` but ends
  a month earlier: theirs runs to 2026-09-30.
- **Validation starts in 2021.** By 2021-01, 84 crypto contracts had an
  archive month, so a top 50 is a selection by liquidity. In 2020 there were
  11 to 80, and a top 50 would be most of the market, including fresh,
  thin listings. The 16 folds then end exactly where the holdout starts, so
  no day before the holdout is left unscored, and PBO uses all 16 folds.
- **Periods per window**: with 1-day latency a period is `horizon + 1` days,
  and each fold is scored from its own first day, so a fold of 91 days
  holds `floor(90 / (horizon + 1))` periods and the holdout
  `floor(607 / (horizon + 1))`:

  | Horizon | Days per period | Per fold | Validation (16 folds) | Holdout |
  | --- | --- | --- | --- | --- |
  | 7 | 8 | 11 | 176 | 75 |
  | 14 | 15 | 6 | 96 | 40 |

  A skipped decision shifts the schedule by a day, and a fold's last two
  (h7) or zero (h14) days hold no period. These counts, and 16 PBO blocks,
  were checked with both specs on synthetic panels of the same axis built
  with the pinned builder (no real data).
- **Sample floors**: `min_trades_validation` 72, a quarter below the h14
  configs' 96; `min_trades_holdout` 30, a quarter below the h14 configs'
  40 and well below the h7 configs' 75. By the period bound under Checks
  before merge, the holdout keeps 30 periods through up to 157 skipped
  decision days for h14 and 367 for h7. `min_folds` 16 is the split as
  designed. A config under the validation floor gets a p-value of 1 and
  cannot be selected.

The holdout is read once, and only if validation selects a config.

## Checks before merge

Before this document merges, the pinned table is built on the VPS and
checked by `exp_xs_usdm_shape.py`, which reads the trading flag, the rank,
the covered flag and whether a value is present, never a price, a funding
value, a return or a signal. Nothing waits for a date: the panel ends with
2026-08, and the month the builder reads after it, 2026-09, is already
published, so the shape-only build and the study's own build read the same
files. The classification of every contract, and the non-crypto list it
gives, are fixed before the build, and the build uses exactly that list
(`37dc30be8a1aa95c983ec0d2e091ed9297d9f452e85fd51ee03b75fbff5d5348`): a change to the list in review re-runs the build
and these checks. Each gate's remedy below was written in the draft, before
any count was seen. None drops or adds a symbol by hand, moves a period or
changes a decision rule's tolerance; what applied is recorded under
[Seen before](#seen-before).

| Count | Gate | If it fails |
| --- | --- | --- |
| Axis and symbol counts | as built and as expected | The table is wrong: fix the sync or the builder (a code fix moves the pin) and rebuild. |
| Excluded contracts ever ranked; ranked days without a rate | none | A bug of the panel change: fix it, move the pin, rebuild. |
| Median ranked contracts a day, each year from 2021 | at least 60 | S4 pauses; a redesign is a new pre-registration. |
| Periods per config left after the skips the universe depth implies | at least 72 in validation and 30 in the holdout | That config leaves the grid by this rule alone; Holm and the deflated Sharpe ratio count the rest, and a family left with no config is not registered. |
| Traded days without a rate that a ranked name could hold | 0 | First, if every such day lies in 2026-07 or 2026-08 in a funding run the universe file marks closed, a later universe snapshot and a rebuild. Otherwise a harness rule (version 7), the same for every symbol: a position exits at the close of such a day as a forced exit, that day's funding charged nothing and counted unfunded; the pin moves. |
| Non-fills and forced exits any leg could meet; the uncovered share of ranked rows | none, reported | Rule 5 stays at 1%. The docs say whether a part of it may bind: forced exits when the ranked names' rate is 0.5% or more, funding when the uncovered share is 1% or more. |
| Carry only: ranked rows with a rate but no `carry_7d`; ranked rows with an uncovered day in their carry window | none, reported | At 1% or more of a window's ranked rows, the carry document lists the symbols under Limitations. |

## Seen before

No cross-sectional return of any kind had been computed in this project, no
panel of this universe had been built, and none of the work behind this
document read a price series of a contract other than BTCUSDT. These had
been seen, and they are stated so the reader can judge their weight:

- **The holdout's market, through BTC.** `exp_tsmom_btc`'s benchmark (#128)
  published buy-and-hold BTCUSDT over its holdout, 2025-01-01 to
  2026-09-30, which is this holdout plus 2026-09: it fell 10.7%, and a long
  of constant notional paid 7.3% in funding, below the neutral rate's about
  19%. Over 2020-09-21 to 2024-11-28 it rose 776.8% (Sharpe 0.82) and a
  long paid 58.1%. So validation holds a strong bull market and the holdout
  a falling one. The same figures make BTC's 2026-09, the first month of
  the forward test, seen too.
  - Why it matters here: after a market falls, the past losers tend to be
    the high-beta names, so a long-short momentum book tends to be short
    the market. In a falling holdout it can gain from the market's
    direction rather than from momentum. That points for H1 on the
    holdout, so rule 4 requires the price return net of the book's market
    beta to be positive (see [Benchmarks](#benchmarks)).
- **Earlier BTC results**: `exp_tsmom_btc` was `interesting_but_fragile`
  and `exp_funding_btc` was `no_edge`. In `exp_tsmom_btc`, trend longs paid
  1.2 to 2.9 times the round trip in funding.
- **Published results on overlapping years.** The grid follows the
  literature, and some of it covers this study's years:
  - Borri et al. (2026): 2-week momentum after 2020 at 2.1% a week
    (t 3.70), through 2025-09, so through all of validation and the first
    nine months of the holdout; 12- and 24-week momentum not significant.
    The 14-day lookback and the absence of longer ones follow from this.
    It is a different universe (spot, value weighted, about 16,000 coins),
    but the lookback was not chosen blind to these years.
  - CTREND (2015-04 to 2022-05), Starkiller (2018-04 to 2022-11, negative
    out of sample from 2021-03) and Chi et al. (2017-11 to 2021-03)
    overlap the first validation years.
  - Fayez Junior (SSRN 6701738): 10 Binance perps from 2022-07 to 2026-04,
    funding and OHLCV signals with a rank IC of -0.0097 and a net Sharpe of
    -3.22. A negative result on most of validation and the holdout.
- **The universe file's shape** (#134), months only, no prices: crypto
  contracts with a klines archive month numbered 84 in 2021-01, 137 in
  2022-01, 154 in 2023-01, 256 in 2024-01 and 367 in 2024-12; in 2026-09,
  628 of 864. An archive month is not a trading month, so these overstate
  the traded universe after delistings.
- **The draft non-crypto list was classified by recognition.** It was made
  from tickers and listing months, without metadata: tickers the author
  knew as crypto were kept unchecked, and only unfamiliar ones were marked
  `unverified`. Recognition favours tokens that later became well known.
  The final list comes from the metadata classification of every symbol
  (see Universe); the draft's counts and its differences from the final
  list are recorded here: the
  draft held 239 contracts (5 `index`, 2 `fiat_pegged_or_fx`, 10
  `commodity`, 30 `etf_or_etp`, 138 `equity`, 2 `private_company`, 52
  `unverified`); 55 symbols changed class, each with its source in the
  classification file.
  - Kept by the draft, now left out (2): ALLUSDT, `index` (exchangeInfo
    `INDEX`; Binance's ALL Composite Index, launched 2025-08-06), and
    PENGUSDT, `equity` (exchangeInfo `EQUITY`; launch notice: Penguin
    Solutions, Inc. Common Stock), listed 2025-08 and 2026-07.
  - Left out by the draft, now kept (20), all exchangeInfo `COIN`:
    SLXUSDT (the draft's `etf_or_etp` guess, the VanEck Steel ETF; Binance's
    launch notice names SLX (Solstice)) and 19 `unverified`: ARXUSDT,
    BILLUSDT, BTWUSDT, CAPUSDT, CHIPUSDT, CTRUSDT, DATAIPUSDT, DOSUSDT,
    GENIUSUSDT, GRAMUSDT, GRVTUSDT, MARSCOINUSDT, OPGUSDT, OUSDT, PONSUSDT,
    PRLUSDT, REUSDT, STARUSDT, ZESTUSDT. All list from 2026-04 on.
  - `unverified` in the draft, now another excluded category (33), each
    from exchangeInfo and its launch notice: 20 `equity` (AGPUUSDT,
    BBXUSDT, BEUSDT, BNCUSDT, BOTUSDT, BSPUSDT, BXUSDT, CATUSDT, CBRSUSDT,
    CXMTUSDT, CYPHUSDT, PAYPUSDT, RUMUSDT, SECZUSDT, SHAZUSDT, SKHYUSDT,
    STXXUSDT, UNITREEUSDT, VUSDT, WENUSDT), 9 `etf_or_etp` (DRAMUSDT,
    INTWUSDT, KSTRUSDT, LYTEUSDT, MVLLUSDT, RAMUSDT, SKDDUSDT, SKUUUSDT,
    SNXXUSDT) and 4 `private_company` (MOONSHOTUSDT and OURAUSDT,
    exchangeInfo `PREMARKET`; QNTXUSDT and SPCXUSDT, `EQUITY` with Pre-IPO
    launch notices).
  - No symbol is `unverified` in the final list, so none listing before
    2025 needs naming.
- **Market microstructure, for costs**: Binance quoted spreads of 16
  contracts on 2024-03-20 (validation), Binance depth on 2026-10-06 and a
  Hyperliquid snapshot on 2026-10-09 (24h volume, open interest and books
  of its perps), both after the holdout. They were read for spreads,
  depth and rank only; the quotes are single days, not price series.
- **Between the draft and this revision no strategy return was computed.**
  The panel change (#145) and its review rounds, and the reviews of these
  drafts, used synthetic data only. This revision moved the panel's end to
  2026-09-01 and set the alpha, the holdout rule and the reading of the
  overfitting measures; none of it read data.
- **Shape-only counts from the panel**, from `exp_xs_usdm_shape.py`
  (output in `exp_xs_usdm.shape.json`): `<SHAPE_COUNTS>`. They come from
  the trading flag, the rank, the covered flag and whether a value is
  present; no price, funding, return or signal value was read. Any remedy
  a gate triggered (see [Checks before merge](#checks-before-merge), fixed
  before the counts) and its effect: `<GATE_OUTCOMES>`.
- **The grid is not independent of what was known.** Besides the papers
  above, the broad history of these years is common knowledge: the 2021
  alt-coin boom, the LUNA (2022-05) and FTX (2022-11) collapses, and the
  2025-26 decline.
- **More was considered than the four configs.** Lookbacks of 7, 21, 28,
  30 and 90 days, 30-day holds, decile legs, universes of 30 and 100 names
  and rank-tiered costs were considered on the literature and dropped
  before any data was built. Across S4 there are eight configs; Holm and
  the deflated Sharpe ratio count this family's four only (see Hypothesis
  for the two families together).

Because of the first and third points, a pass on this holdout is not enough
on its own; see the decision rules.

## Grid

These four configs are the whole family:

| id | quantile | horizon | Meaning |
| --- | --- | --- | --- |
| `q20-h7` | 0.2 | 7 | Long the top fifth of the universe by 14-day return, short the bottom fifth; hold 1 week |
| `q20-h14` | 0.2 | 14 | The same; hold 2 weeks |
| `q30-h7` | 0.3 | 7 | Long the top 30%, short the bottom 30%; hold 1 week |
| `q30-h14` | 0.3 | 14 | The same; hold 2 weeks |

- **Direction**: `signed`. A full universe of 50 gives 10 names a leg at
  0.2 and 15 at 0.3, each at `0.5 / names` of the capital.
- **Multiple testing**: Holm across the four configs at alpha 0.05; the
  deflated Sharpe ratio counts them as four trials.
- **Which config goes to the holdout**: of the configs that survive
  validation, the harness takes the one with the highest validation mean
  net return per period at 1.0×, not the highest Sharpe. That favours the
  2-week holds, whose periods are longer; rule 2 then tests that config on
  the holdout. `q20-h7`, the literature's design, is the primary config,
  which is only reported. PBO ranks configs the same way, by mean per
  period, which shapes how rule 3 reads it.
- **Lookback**: one per spec, 14 days. It is the middle of the 1- to 4-week
  range LTW support, and the one with evidence after 2020 (Borri et al.).
  Lookbacks of 8 weeks and more are not significant in either paper.
- **Horizons**: a week, because weekly rebalancing is the design the
  literature tests (LTW, Borri; Starkiller's best was 7 days); and 2 weeks,
  the signal's own length, which holds a position about as long as the
  signal that chose it and halves the round trips a year (24 instead of
  46). Monthly holds are not significant in Chi et al. and would leave 4
  periods a fold.
- **Quantiles**: 0.2 is the quintile sort of LTW and Borri. 0.3 is there
  because Han, Kang and Ryu find the losers leg prone to sharp rebounds: in
  a 15-name leg one rebounding loser moves the leg by a fifteenth, not a
  tenth. Deciles (5 names) were dropped as the most exposed to that and to
  single-name noise.
- **Universe size**: 50. The literature's continuation sits in large,
  liquid coins (LTW: 4.2% a week above median size, nothing below; Zaremba;
  Fičura). With 84 crypto contracts in 2021-01, 50 is about the most the
  first folds can rank while still selecting. On Hyperliquid, ranks 30 to
  50 are already thin (median depth within 10 bp of $16k), so a larger
  universe would not trade at the tested costs.
- **Floor**: `min_names_per_leg` 5, half a quintile leg. A decision with
  fewer names a leg is skipped.

## Costs and funding

Per side, as one flat rate for every name:

- **Fee**: 4.5 bps, Hyperliquid's base taker fee (Binance's is 5.0, 4.5
  with BNB).
- **Half-spread**: 1.5 bps. Binance's quoted full spreads were 0.02 to
  0.06 bp for BTC, ETH and SOL, 0.6 to 1.8 bp for mid-caps such as LINK,
  ATOM, DOGE and GALA, and 4.3 bp for ALGO (2024-03-20); Hyperliquid's
  ranks 30 to 50 had a median of 0.85 bp. Effective spreads widen more than
  tenfold in stress (He, Manela et al.), and very low-priced coins have
  coarse ticks (GALA on Hyperliquid: about 4.5 bp a tick), so 1.5 is above
  the typical half-spread and covers those on average.
- **Slippage**: 4.0 bps, for orders of about $25k a name. A linear-book
  estimate from Binance depth (2026-10-06) and Hyperliquid's book
  (2026-10-09) puts the all-in cost a side at about 6 bp for ranks 1 to 10,
  9 for ranks 11 to 30 and 13 for ranks 31 to 50, fee included: 1.5, 4.5
  and 8.5 bp beyond the fee. Over a universe of 50 that averages 5.5 bp of
  spread and slippage, which is 1.5 + 4.0.
- **Round trip**: 20 bps, also stressed at 1.5× and 2.0×. At 2.0× a side
  costs 20 bp, about the estimate for Hyperliquid's ranks 30 to 50 (13 to
  20 bp), so 2.0× is the realistic cost of the thinner half of the book.
  Validation requires a positive mean at 1.5× and 2.0×; the holdout at
  1.5×, and reports 2.0×.
- **Where the legs sit**: the 10 bp average weights ranks 1 to 50 equally.
  Both books trade the extremes of the cross-section, which may lean
  toward thinner names, while validation selection and the holdout test
  use the 1.0× rate. `exp_xs_usdm_overlap.py` reports, on validation days,
  the mean `volume_rank` of each leg and its share in ranks 31 to 50
  against the universe. Reading, fixed now: for a leg whose mean rank is
  above 30, or more than half of whose names sit in ranks 31 to 50, the
  2.0× figures are the realistic ones, and the results document reads that
  config's 1.0× figures as optimistic. It changes no rule.
- **When the quotes were taken**: the spreads are from 2024-03-20 and the
  depth from 2026-10, and the same rate is applied to 2021 to 2023, whose
  spreads and depth may have differed. There is no cost evidence from
  those years.
- **What it costs a year**: a full round trip every period is about 9.1% a
  year at 1-week holds (45.6 periods) and 4.9% at 2-week holds (24.3), at
  1.0×.
- **Funding**: Binance's realized funding, position by position on every
  held day, on the notional at that day's close. Longs pay a positive rate
  and shorts receive it. It is stressed adversely payment by payment at
  1.5× and 2.0×, in the same stress as the round trip (one multiplier for
  both): what is paid is multiplied, what is received divided.
- **Conservative and optimistic at once**: every period pays a full round
  trip on all its capital, even for names it keeps (CTREND reports 68%
  weekly turnover, so the real cost is lower). The flat rate overstates the
  cost of the largest names and understates the smallest at 1.0×.

## Fill model and sizing

- **Timing**: decide at day `d`'s close from that day's rank, trading flag
  and signal; fill at day `d+1`'s close; exit at the close `horizon` days
  later. The next decision is the exit day, so periods do not overlap and
  the book is flat for one day in every period. There is no calendar
  rebalance, and skipped decisions shift the schedule.
- **Size**: `sizing: unit`. Each leg holds half the capital at fill, equal
  weight within the leg, sized at the decision. Gross exposure is 1.0 and
  net exposure 0 when every order fills.
- **Non-fills**: a name that does not trade on the fill day is not opened
  and its capital sits idle. A leg that fills below the floor unwinds the
  period's fills at the fill close, paying the round trip for no return.
- **Halts and delistings**: a position is held to its exit day. It exits
  there at the close if the contract traded, otherwise at its last traded
  close before it (a forced exit). Funding is charged on every held day
  with a row, halts included, so a delisted contract's flat archive days
  pay their recorded default rate until the exit.
- **No stops, and no averaging down.**

## Benchmarks

- **Cash (zero)** is the benchmark H0 tests against. The harness's
  buy-and-hold benchmark does not apply to a panel.
- **The market, for beta.** `docs/experiments/exp_xs_usdm_beta.py`
  (committed with this document) runs after the harness, and only when
  `result.json` has a `holdout` block: validation has selected a config and
  the harness has scored it once on the holdout. It therefore reads nothing
  the harness had sealed, unlike `exp_tsmom_btc`'s deviation, which read the
  holdout's market when no config had been selected. It decides no
  selection.
  - With the pinned `PanelSource`, it rebuilds the selected config's
    holdout periods and their long and short legs, and fails closed unless
    they equal `PanelSource.window` and `result.json`.
  - For each period it computes two market returns over the same fill and
    exit days, by the harness's exit rule: the equal-weight price return of
    every name of the decision day's universe that fills, and BTCUSDT's.
  - It regresses the book's per-period gross return on each by ordinary
    least squares, with Newey-West standard errors (Bartlett kernel, the
    harness's lag rule), over the periods that held positions.
  - The **beta-adjusted** mean subtracts beta to the universe times each
    held period's universe return from that period's return, and averages
    over every period the harness scored. Rule 4 reads it.
- The book is dollar neutral at the fill, not over the hold, and its legs
  differ in beta; that is why beta is measured rather than assumed.

## Decision rules (committed now)

All figures are the selected config's, from `result.json`, except rule 4's
beta, from `beta.json` (the beta script's output). A **pass** needs rules
1, 2, 4 and 5. Rule 3 is reported with a fixed reading and decides nothing.

1. **Validation, as the harness does it**: a config survives when it has at
   least 72 periods, its Holm-adjusted p-value at 1.0× is at most 0.05, and
   its validation net mean is positive at 1.0×, 1.5× and 2.0×
   (`_survives_validation` in `src/research/harness/evaluate.py`). The
   harness takes the survivor with the highest validation mean per period
   to the holdout. With no survivor, `result.json` has no `holdout` block
   and the family stops. For the best of four, Holm needs an iid t of
   about 2.27.
2. **Holdout**: the selected config, scored once on the holdout with at
   least 30 periods (`min_trades_holdout`):
   - its net mean per period is > 0 at 1.0× and at 1.5×
     (`holdout.net["1.0"].mean_return` and `holdout.net["1.5"].mean_return`);
   - its conservative p-value at 1.0× (`holdout.net["1.0"].p_value`, the
     larger of the iid and HAC one-sided p-values) is ≤ 0.10. The holdout
     is one pre-selected config, so there is no Holm step there. With iid
     returns that needs a t of at least 1.29 (h7, 75 periods) or 1.30 (h14,
     40 periods), an observed annualized net Sharpe of about 1.0.
   - Reported, not tested: the net mean at 2.0×, and the annualized net
     Sharpe at each stress, `sharpe_per_trade × sqrt(trade_count / 1.6658)`
     from the holdout's `net` blocks, with 1.6658 = 608 / 365, the holdout
     in 365-day years. 2.0× is the cost of Hyperliquid's thinner names (see
     Costs), so a negative 2.0× mean is a cost the forward test must price.
   - This rule is decided from these `result.json` values, not from the
     harness's label. The label says `passes_h1` only when p ≤ 0.05 and the
     2.0× mean is positive too, and `interesting_but_fragile` otherwise, so
     a holdout can pass this rule under the label `interesting_but_fragile`
     (p between 0.05 and 0.10, or a negative 2.0× mean).
3. **Overfitting, reported with a fixed reading, not a gate**: the deflated
   Sharpe ratio and the PBO, as the harness reports them on validation net
   returns at 1.0× (`overfitting.deflated_sharpe.dsr` and
   `overfitting.pbo.value`).
   - Why not gates: Holm on validation and an independent holdout test
     already control selection. At 96 or 176 validation periods a deflated
     Sharpe ratio of 0.95 needs an iid t of about 2.7 with normal returns
     and about 2.9 with skew −0.5 and kurtosis 6, well above Holm's 2.27,
     so as a gate it would mostly cut power. PBO separates little on this
     grid: CSCV ranks configs by mean net return per period, and the grid
     mixes 8-day and 15-day periods, so with a steady daily edge the 2-week
     configs lead in sample and out of sample alike, and PBO mostly measures
     whether returns keep their sign and the horizon ranking holds.
     Simulations of this grid's 16 blocks under pure noise put PBO at or
     below 0.5 in about a third to a half of runs.
   - Reading, fixed now: a deflated Sharpe ratio of 0.95 or more says the
     selected config's validation Sharpe stands out from the best of four
     noise configs after its skew and kurtosis; below 0.95, that it does
     not clearly stand out, which the forward test then has to settle. A
     PBO above 0.5 says the configs' validation ranking did not hold across
     folds; a PBO at or below 0.5 is not read as evidence against
     overfitting. A value the harness did not compute (a null `dsr` or
     `value`, with the block's `note`) is reported as such, with its note.
     The results document states the reading that applies; it changes no
     outcome.
4. **Price, not funding, and not the market's direction**:
   - the holdout gross mean per period (`holdout.gross.mean_return`, the
     price return, before costs and funding) is > 0 and at least the
     holdout funding mean per period (`holdout.funding.mean_return`, the
     funding cashflow, positive when received). Momentum is a price
     hypothesis; a pass resting more on funding received is carry, which is
     the other family.
   - the **beta-adjusted holdout gross mean** per period, from `beta.json`
     (`beta_adjusted.gross_mean`, beta to the equal-weight universe), is
     > 0. A falling holdout pays a book that is short the market's beta;
     this part asks that the price return remain after that payment. A
     beta the script cannot compute (fewer than three held periods, a flat
     market series, or a held period without a universe return) fails this
     rule, and so does a script that fails closed; a script failure that is
     a bug, not a data fault, is fixed in a reviewed PR that changes no
     definition here, and the script is run again.
   - Reported with a fixed reading, not tested: beta to the universe and to
     BTC with their HAC t-statistics, and each leg's gross mean. A beta
     with |t| ≥ 2 means the book carried market exposure, which the forward
     test must then hedge or carry in its own hypothesis.
5. **Data the result rests on**: in the selected config's holdout
   `portfolio` block, with positions = `(periods - unwound_periods) ×
   (mean_long_names + mean_short_names)` and position-days = positions ×
   `horizon_bars`:
   - `uncovered_funding_days + unfunded_halt_days` ≤ 1% of position-days;
   - `forced_exits` ≤ 1% of positions;
   - fill sanity: `mean_long_names` and `mean_short_names` differ by at most
     5% of their mean. Both legs take the same number of names at the
     decision, so this only catches a leg that keeps failing to fill. It is
     not a test of market neutrality; rule 4's beta is.

   An uncovered day misses at most one settlement, and a forced exit
   assumes the last traded close was the price a holder got. A pass that
   leans on more of either rests on numbers the data does not have.

The outcome is then exactly one of these:

| Result | Outcome |
| --- | --- |
| A pass (rules 1, 2, 4 and 5; whatever the harness label, `passes_h1` or `interesting_but_fragile`) | A pre-registered forward test on data from 2026-09-01 onward, none of whose values this study uses (the build opens the 2026-09 funding files only for its end-of-window interval check, see [Data](#data-point-in-time), and BTC's 2026-09 was seen through `exp_tsmom_btc` and `exp_funding_btc`; the forward test's pre-registration states both), before any PAPER strategy design and never directly to capital. Its pre-registration is fixed before any of that data is read for this strategy. It must measure the book's beta to an equal-weight universe and to BTC, and restrict the universe to contracts listed on Hyperliquid. The holdout's market was seen (see [Seen before](#seen-before)), so a pass here is not enough on its own. |
| No config selected (`no_edge`, `interesting_but_fragile` or `not_enough_data`, with no `holdout` block), a holdout under 30 periods, or a selected config that fails rule 2, 4 or 5 | This family stops on this universe. A stop is not evidence that no effect exists: see Power under [Limitations](#limitations). |
| A failed-closed run whose `failure_kind` is `unsafe_mode`, `data_config`, `spec` or `lock`: raised by the environment (`TRADING_MODE`, `RESEARCH_ENV`, `RESEARCH_IMAGE_DIGEST`), the checkout or the spec's configuration, not by any value of the panel | Nothing was scored. Fix the cause and run again in a new `STUDY_DIR`; the results document records every attempt. |
| A failed-closed run of any other kind (`funding`, `schema`, `invariant`, ...): raised by the panel's values or the scoring | This family stops; the cause is recorded, and any new attempt is a new pre-registration. |
| The pinned table cannot be rebuilt (the fingerprint check fails, for example because Binance Vision republished or withdrew an archive after the shape-only build) | The family stays unrun. Any new pin is a new pre-registration that records why. |

A thin holdout is not expected: the universe file lists several hundred
crypto contracts through the holdout, and a 2-week config holds 40 periods
when no decision is skipped.

The checks before merge exist to make a failed-closed run after the panel
is loaded unlikely; it is not a reason to rebuild.

The harness's `promotion_decision` is not this decision. It says
`paper_candidate` only with the label `passes_h1`; rules 2 to 5 are applied
in the results document. A `paper_candidate` that fails rule 2, 4 or 5 is
not acted on, and a pass under the label `interesting_but_fragile` leads to
the forward test all the same.

The carry family is decided on its own. Its outcome changes nothing here,
and this family's alpha stays 0.05 whether or not the carry study runs.

When the family stops:

- The lookback, quantiles, horizons and universe are not re-tuned on this
  data.
- A new variant needs a new pre-registration and evidence from data from
  2026-09-01 onward.

## Robustness (reported)

- Joint stress at 1.5× and 2.0×: one multiplier scales the round trip and
  each funding payment made, and divides each one received. There is no
  separate cost-only or funding-only stress.
- HAC p-values. The folds are concatenated for HAC, so fold boundaries are
  treated as contiguous.
- The deflated Sharpe ratio and PBO by CSCV: 16 blocks of one fold each,
  none left out, read as rule 3 fixes.
- The grid itself: quantile and horizon.
- Per config, from the `portfolio` block: periods, skipped decisions, mean
  names a leg, unwound periods, forced exits, uncovered funding days and
  unfunded halt days, and the mean weight.
- The breakeven cost per side, `(gross mean + funding mean) / (2 × mean
  weight)`, at which the 1.0× net mean would be zero.
- Annualized Sharpe at each stress, by the formula of rule 2; on validation
  as a descriptive figure with 3.989 = 1,456 / 365.
- From `docs/experiments/exp_xs_usdm_overlap.py` (committed with this
  document), on validation days only; it reads no holdout day and decides
  nothing:
  - how far momentum and carry pick the same names: the mean and median
    Spearman correlation of `ret_14d` and `carry_7d` across the universe,
    and how many of momentum's top and bottom fifths sit in carry's;
  - where the legs sit: each leg's mean `volume_rank` and share in ranks 31
    to 50, against the universe's (read as fixed under Costs).
- From `docs/experiments/exp_xs_usdm_beta.py`, for the selected config's
  holdout only: each leg's gross mean, and beta to the equal-weight
  universe and to BTC with HAC t-statistics (rule 4 reads the
  beta-adjusted mean).

## Limitations

- **Proxy data**: Binance prices and funding stand in for Hyperliquid,
  whose funding settles hourly at its own level and cap, whose book is
  thinner at ranks 30 to 50, and which lists fewer contracts.
- **Data terms**: Binance Vision's terms (version 1.0 of 2026-08-26, CC
  BY-NC-SA 4.0) allow personal, non-production research backtests such as
  this one. Using signals derived from Binance data in live trading is a
  separate decision for Chupa before any LIVE step.
- **Delisting exits**: a position in a contract that stops trading exits at
  its last traded close. The price Binance settled a delisted contract at
  is not in the panel, so a short is credited and a long charged as if they
  got that close. Rule 5 bounds how much of a pass can rest on it.
- **Uncovered funding**: a day whose settlements the panel cannot prove
  whole (an interval switch from 4h back to 8h reads the same as a missing
  settlement) is charged its recorded sum; the error is at most one
  settlement a day. Rule 5 tolerates 1% of position-days.
- **Survivorship**: the universe includes every contract Binance Vision
  archives, delisted ones too, ranked by trailing volume each day. What is
  missing: contracts Binance never archived or removed; five contracts with
  Chinese names that the manifest cannot hold (meme tokens, `COIN` in
  exchangeInfo, onboarded 2025-10-20, 2026-01-21, 2026-03-11, 2026-08-30
  and 2026-09-06, so four of them trade in the holdout); GAIBUSDT, which has funding from
  2025-11 to 2026-04 but no 1d klines and so no panel row; and days a
  contract traded without a funding archive (see Universe).
- **Tickers are not assets**: renamed assets are separate tickers (MATIC
  and POL, RNDR and RENDER), so in a migration month a leg can hold both.
  One ticker can also change asset: a contract whose archive rows run
  unbroken across a break, such as LITUSDT's funding gap from 2025-07 to
  2025-11, is ranked again at once with a return that may span the break
  (see Rank).
- **Market beta, measured only on the holdout**: rule 4 removes the
  book's beta to the equal-weight universe, estimated on the same 40 to 75
  periods it is applied to; a beta that shifted within the holdout, or a
  market factor other than that universe, is not removed. Validation's
  beta is not measured.
- **Equal weight and one cost rate**: see Costs. The study does not test a
  value-weighted book, rank-tiered costs, or turnover-netted costs, and has
  no cost evidence from 2021 to 2023.
- **Schedule**: one period at a time, with a flat day between periods; no
  overlapping or staggered books.
- **Power**: a config has to survive validation and then pass the holdout,
  two close to independent tests. `docs/experiments/exp_xs_usdm_power.py`
  (committed with this document; a simulation that reads no data) gives
  how often, under a stated model:
  - net returns normal, accruing day by day independently over time, at 40%
    a year for every config, on the actual period counts with no skip (176
    or 96 in validation, 75 or 40 in the holdout);
  - the four configs correlated day by day, 0.85 within a horizon and 0.5
    across horizons;
  - validation as rule 1 (the harness's conservative p-values, Holm at
    0.05, positive means at 1.5× and 2.0× with half and one more 20 bp
    round trip a period) and the holdout as rule 2;
  - the config under test at the true net annualized Sharpe of the first
    column at 1.0×, the other three at 0; the last column puts all four at
    that Sharpe and counts a pass by any of them.

  Its output with no flags, 50,000 draws (`validation`: the config under
  test is selected; `holdout`: it passes rule 2; `both`: both happen):

  ```text
  draws 50000, seed 20261010, book volatility 40% a year
  periods: validation h7 176 h14 96, holdout h7 75 h14 40

  | True net annualized Sharpe | h7: validation | h7: holdout | h7: both | h14: validation | h14: holdout | h14: both | All four at S: family passes |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | 0.00 | 0.007 | 0.096 | 0.001 | 0.010 | 0.094 | 0.001 | 0.003 |
  | 0.50 | 0.085 | 0.247 | 0.021 | 0.097 | 0.244 | 0.024 | 0.054 |
  | 1.00 | 0.348 | 0.483 | 0.169 | 0.365 | 0.477 | 0.176 | 0.303 |
  | 1.25 | 0.539 | 0.609 | 0.328 | 0.557 | 0.604 | 0.339 | 0.489 |
  | 1.50 | 0.714 | 0.722 | 0.518 | 0.738 | 0.716 | 0.530 | 0.661 |
  | 2.00 | 0.933 | 0.891 | 0.831 | 0.946 | 0.886 | 0.838 | 0.881 |

  both, counting only the harness's passes_h1 label as a holdout pass:
    S 0.00: h7 0.000, h14 0.000
    S 0.50: h7 0.013, h14 0.014
    S 1.00: h7 0.117, h14 0.121
    S 1.25: h7 0.247, h14 0.252
    S 1.50: h7 0.418, h14 0.427
    S 2.00: h7 0.746, h14 0.750

  null, all four at 0: the family passes in 0.0030 of draws
  ```

  - Rules 4 and 5 and the stress of funding are left out, so these are
    upper bounds.
  - The harness's own label would pass about 0.12 at a true Sharpe of 1.0
    and 0.42 at 1.5; rule 2's 0.10 and 1.5× add about 0.05 and 0.10.
  - The model choices move little. Each sensitivity run is the same script
    with one flag, 50,000 draws and the same seed:
    - `--independent` (no shared factors): no per-config cell moves by
      more than 0.011; the all-four column rises, to 0.405 at 1.0, and the
      null family rate to 0.0045.
    - `--annual-vol 0.15`: every figure is identical to the output above.
      In this model, at 15% and at 40% a year, a mean that clears the
      p-value tests also clears the cost stresses, so the p-value tests
      decide.
    - `--iid-only` (the iid t-test's p-value alone): `both` is 0.183 (h7)
      and 0.193 (h14) at 1.0, and 0.538 and 0.555 at 1.5.
  - A single-config approximation (Holm's 0.0125 for the best of four, the
    holdout's 0.10, iid t only, the expected t as Sharpe times the root of
    the years) gives about 0.20 at 1.0 and 0.58 at 1.5. The simulation is a
    little lower, mostly because the conservative p-value also takes the
    HAC test; `--iid-only` above narrows most of the gap.
  - Borri et al.'s post-2020 5-1 spread, t 3.70 over about 5.75 years, is
    an annualized Sharpe of about 1.5 before costs on a broader,
    value-weighted universe. At a true net Sharpe of 1.5 one config passes
    about half the time, and below 1.0 less than one time in five, so
    "this family stops" is not evidence of absence there.
- **Exploration outside the harness** is not deflated (see Seen before).

## Run (after this document is merged)

Run from `main`, after this document is merged. The whole run is one
subshell with `set -euo pipefail`: it stops at the first failure without
closing your shell, and nothing syncs or runs before the code check passes.
The universe sync is large (about 43,000 monthly archives); the shape-only
build before merge synced it into the same archive root, so here it only
tops it up. Run it inside `tmux` all the same.

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
  git diff --exit-code origin/main -- docs/experiments/exp_xsmom_usdm.md \
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
  # gate: a missing or broken archive inside the range cannot give the
  # pinned table (the builder refuses it), and sync.log names the cause.
  status=0
  PYTHONPATH=src uv run --frozen python -m research.hist_etl sync \
    --dataset bn-um-usdt-1d 2>&1 | tee "$STUDY_DIR/sync.log" || status=$?
  test "$status" -eq 0 || test "$status" -eq 2
  if grep -q '^error' "$STUDY_DIR/sync.log"; then exit 1; fi
  cp docs/experiments/exp_xsmom_usdm.spec.yaml "$STUDY_DIR/spec.yaml"
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
    "c8c5d93142e27e468c642fd576d8cc7f2263dd05b70bdc2a0998c77285fbbd08",
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

The results go into `exp_xsmom_usdm.results.md` in a separate PR. That PR
records the commit from `commit.txt`, the lock file, `result.json`,
`overlap.json` and, when the holdout was scored, `beta.json`, each with its
sha256, and applies the decision rules to their values.
