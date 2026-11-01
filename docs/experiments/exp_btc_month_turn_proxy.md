# BTC month-turn calendar exposure — historical proxy v1

This registers one new, bounded **exploratory** mechanism before its returns are
computed. It uses already exposed Binance history, not untouched OOS and not
Hyperliquid execution evidence. Every top-level and nested promotion decision
must be `forbidden`, including any positive internal harness result. No PAPER,
TESTNET, SHADOW, LIVE, account or deployment permission follows.

## Mechanism and prior exposure

A recurring month boundary may coordinate portfolio/capital flows even without
price, funding or aggressive-flow triggers. This is a hypothesis, not a claimed
cause. Use one fixed long calendar window: the last UTC calendar day of a month
and the first three days of the following month. No weekday/business-day
substitution, calendar-window optimization, alternative sign, holding period,
threshold, asset or regime is selected after seeing results.

This differs from the stopped BTC own-price trend, contrarian funding, flow,
flow-divergence, thin-liquidity **move-reversal** and funding-pressure rules in
[the historical register](../research/historical-study-register.md). It neither
changes their verdicts nor reopens their holdouts. The market history is known
from earlier studies and benchmark reports; all results here remain historical
exploration even though this rule has not been scored by this run.

Primary literature motivates a falsifiable question, not a transferred edge:
[Qadan, Aharon and Eichel (2022)](https://doi.org/10.1016/j.frl.2021.102354)
find few calendar effects across cryptocurrencies, while
[Ziegler and Moebius (2023)](https://doi.org/10.47782/oeba202304028401)
report that Bitcoin's month-turn effect is not persistent across subperiods.
These do not establish net performance for this rule, venue or period.

## Universe and input admission

One instrument: **Binance USD-M BTCUSDT perpetual**, hourly traded-price candles
and its original native funding records, Jan 1 2020 through Aug 31 2026 inclusive.
This is a conditional Binance proxy experiment. Binance prices/funding are not
Hyperliquid prices/funding, and BTCUSDT is not an executable USDC conversion.
ICP and other instruments are outside this single-asset study.

Before any financial scoring, independently accept a complete hash-bound census
of all 80 monthly candle files, all 80 monthly funding files and their original
ZIP/CHECKSUM provenance. Require exact original CSV-versus-Parquet equality,
valid OHLC/nonnegative volume, ordered unique contiguous hourly timestamps and
complete unique native 8h funding slots. Never repair missing rows by zero-fill,
mask a losing interval, infer silence as zero funding, execute archived catalog
SQL, or overwrite the original warehouse. All58440 bars are required; known
incomplete September2026 bars are outside this prospectively fixed input range.

Retain the original funding event time in milliseconds, including print jitter.
Build each hourly rate by assigning actual events to `(previous_close, close]`;
non-event hours can be zero only after complete native event coverage passes.
Require actual-event membership in every modeled holding interval to agree with
the bucketed membership. Any disagreement invalidates the study, not one trade.
A hash certifies byte identity, not historical availability or source truth.

## Known calendar, decisions and fills

`ts` is the original hourly candle close, `open_time + 3599999` milliseconds.
The complete calendar is known without reading future prices. Set `month_turn=1`
only at the decision close one hour before a fill on the last calendar day at
`00:59:59.999 UTC`; other decisions have signal zero. A deterministic example:
Jan 30 23:59:59.999 decision -> Jan 31 00:59:59.999 entry ->
Feb 4 00:59:59.999 exit. This is exactly 96 hourly bars of exposure after entry.
One unit of entry notional is long; there is no leverage sizing or compounding.

The unchanged harness applies one full hourly-bar latency and nonoverlapping
positions. A bar's observed close is a **modeled taker fill**, not a guaranteed
quote or historical executable depth. No maker/queue, partial-fill, instrument
precision, account fee-tier or actual order claim is made. A trade is eligible
only if decision, entry and full exit fall inside the known stage window;
calendar-censored entries are determined before reading their returns.

## Costs and funding valuation model

Each fill assumes 5bps fee, 1bp adverse slippage and 0.5bp half-spread. These are
frozen research assumptions, not a certification of historical account fees.
The fee reference is Binance's regular USDT-margined futures taker schedule
([official table](https://www.binance.com/en-BH/fee/futureFee), checked 2026-11-01).
The unchanged flat-cost model charges 13bps round trip at 1x, 19.5bps at1.5x and
26bps at 2x. No discount, rebate, fee optimization or cost retuning is permitted.

Native funding is included for every held event. The unchanged harness values
each funding payment using that hourly traded close relative to the entry
close, equivalent to a fixed-quantity position. This is an explicit **bar-close
funding valuation model**, not verified settlement notional/oracle/mark price or
actual cash received by an account. Funding paid is multiplied by each stress;
funding received is divided by it. Do not substitute Hyperliquid funding or
claim actual historical settlement economics. These limitations apply to every
strategy and benchmark result.

## Chronology, benchmark and tests

The [canonical harness spec](exp_btc_month_turn_proxy.spec.yaml) fixes one config:
threshold 0.5 on the known calendar indicator, 96h holding, no fitted parameters.
The 58,440-hour range is split as follows:

- First 8,784 bars:2020 history/training; no price-dependent parameter fit.
- Four expanding validation folds of 8,760 hours. These are fixed365-day index
  windows, not silently adjusted calendar years; publish their exact dates.
- The 24 remaining pre-test bars are unused by the existing splitter.
- Final 14,592 bars:Jan 1 2025 through Aug 31 2026, **exposed historical test**.
  `holdout_bars` is the harness field name, not an untouched-data claim.
- Future untouched OOS: **not assigned or run**. Any candidate needs a separately
  frozen future period and Hyperliquid-specific data/execution qualification.
  The existing Nov2–5 Spot-flow window is not reused or altered by this study.

Require at least 4 validation folds, 36 pooled validation trades and 18 historical
test trades; report `not_enough_data` rather than lower these floors. Cash is a
zero-return benchmark. Unchanged harness buy-and-hold is descriptive only and
must stay behind the historical-test selection gate for that stage.

A **mandatory matched control** distinguishes calendar timing from ordinary BTC
beta. For each primary eligible target month, long the same 96h from that month's
15th day at 00:59:59.999UTC through its19th day at the same clock. Same quantity,
latency, stage boundaries, prices, costs, funding model and stresses. Determine
the common eligible month set solely from planned clocks before reading prices.
No control-window search, independent control selection or outcome-based pair
removal. Missing required primary/control input invalidates the whole study.

There are two predeclared one-sided positive-mean validation claims:
(1) primary net return, and (2) per-month `primary_net - control_net`. Allocate
0.025 to each (Bonferroni family upper bound 0.05), using the larger of existing
iid and HAC p-values. Require positive means and p<=0.025 for **both** at all
1x / 1.5x / 2x stresses and the stated sample/fold floors. Stress variants are
intersection robustness requirements, not a search for the most favorable one.
The YAML alpha 0.025 controls the primary harness claim; it does not replace the
paired-control gate. The control is not a second selectable configuration.

Exact validation close ranges from the unchanged splitter (endpoints inclusive):

| Fold | First validation close (UTC) | Last validation close (UTC) | Planned pairs |
| --- | --- | --- | --- |
| 0 | 2021-01-01 00:59:59.999 | 2021-12-31 23:59:59.999 | 11 |
| 1 | 2022-01-01 00:59:59.999 | 2022-12-31 23:59:59.999 | 11 |
| 2 | 2023-01-01 00:59:59.999 | 2023-12-31 23:59:59.999 | 11 |
| 3 | 2024-01-01 00:59:59.999 | 2024-12-30 23:59:59.999 | 11 |

The unused 24 closes are Dec31 2024. The exposed test has 19 planned pairs.
These counts come from calendar clocks only, without reading any prices or
returns, and must not be altered after results. The target month is the month
that begins during the primary holding interval: Jan31-to-Feb4 pairs with
**Feb15-to-Feb19**, not January's control window.

Compute and freeze validation evidence first. If either claim fails validation,
record the failed hypothesis and do not decode historical-test strategy/control
returns. Only after both pass may the unchanged harness and the same paired
control evaluate the exposed historical test. Require the existing harness's primary `passes_h1` verdict, at least 18 pairs,
and positive paired excess means under all stresses. Report paired historical-
test p-values diagnostically; they are not an extra threshold or an untouched
confirmation claim. Positive
results may be labelled only `historical_interesting_but_unconfirmed`; all other
labels retain a `historical_` prefix. The historical test's p-values are
conditional descriptive evidence, not a new claim of unseen confirmation.

## Reproducibility and limitations

Use a clean reviewed merged source, unchanged spec and locked dependencies.
Record source commit, input/derived-data hashes, implementation identity, native
funding clocks, source acceptance and create-only output location. Lock before
scoring; preserve all partial/error outputs; do not resume or retry with changed
parameters. Independently review deterministic clock/fold/funding/pair/cost
fixtures and the final diff before any real returns are computed.

This PR only registers the recipe. The runner and actual operator input binding
require their own independent review and execution gate. The new implementation
must not modify `src/research`, the existing Binance decoder/precision rules,
Spot-flow spec/holdout, S4 or runtime configuration. No strategy calls an API.
Even a positive result is a small monthly sample on one already exposed
instrument, under bar-fill/funding assumptions, with no capacity, borrow/margin,
liquidation, regime persistence or Hyperliquid tradability proof. It cannot
establish a deployable profitable strategy or authorize trading promotion.
