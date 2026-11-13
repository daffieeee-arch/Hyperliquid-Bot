# Discovery batch one: residual dispersion and OI deleveraging

Frozen before financial decoding, 2026-11-13; issue #237.
Identity: `discovery-real-history-20261113-b1`. Register, independently review
and merge this specification and evaluator before executing this batch.
This is a new exploratory study, not a re-registration of a failed study.
Existing studies, code pins, results and untouched holdouts remain unchanged.

## Question, universe and periods

Can relative-price dispersion or an open-interest contraction identify a useful
directional mechanism after explicit costs and funding? Use Binance USD-M
BTCUSDT, ETHUSDT and SOLUSDT linear USDT perpetuals. These are three selected
survivors with accounting-complete data, not a representative historical universe.
ICP is excluded. This proxy study cannot establish Hyperliquid profitability.

Warm up with December 2024 hourly trade closes. Development is January 1–15,
2025 UTC; the diagnostic period starts January 16 00:00 UTC and ends January 31
23:00 UTC. **Both halves are already-exposed history. Neither is untouched OOS.**
There is no fitting period or optimization of risk scale. The diagnostic half
must not select configurations or thresholds. Do not read the original
prospective November 2–5 holdout or relabel existing history as fresh.

## Inputs and availability

Reuse the accepted accounting input index, SHA-256
`fcc687f5272ab355579f4f9dfe990a4f7553101427b87f32ebf4608e49ce7172`, as source
provenance only. Select its 27 BTC/ETH/SOL records: native December/January hourly
klines, January minute marks, complete native funding and independently acquired
settlement responses, including official checksums. Preserve its old identity.

Add all 31 native daily BTCUSDT January metrics ZIPs and official checksums.
The metadata inventory, fixed before outcome inspection, has SHA-256
`1524a9230edde0ec36eadde406ed77df3b61345d16711375c085ed93a8c6ab0f`.
Validate the eight-column schema, exact instrument, positive finite base-unit
open interest and 288 contiguous UTC five-minute observations per day.
Never infer missing observations or use open-interest value in place of size.

For a completed-hour decision at T, use OI at exactly T−5 minutes and compare
with the snapshot one hour earlier. Native archive times are modeled as UTC;
the five-minute availability lag is an **assumption**, not captured receipt
evidence. Binance publishes daily archives the next day and may revise them.
This models a hypothetical contemporaneous API feature, not a same-day archive
download strategy. Vintage/revision and point-in-time receipt evidence are
missing. Mark `features_availability_assumed: true` in every result bundle.
See [Binance public data](https://github.com/binance/binance-public-data) and
[Open Interest Statistics](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).

## Fixed configuration budget

This batch contains exactly **12 configurations**, within the pilot ceiling of
40. Cost stresses are paired checks on the same configurations. Log all trials,
including failures and no-signal trials; never silently drop an unfavorable run.

1. **Residual dispersion, eight configurations.** Estimate each altcoin's beta
   to BTC from the previous 168 completed hourly simple returns. Zero BTC
   variance or absolute beta above five makes that observation unavailable.
   Residual is the altcoin's 24-hour or 72-hour simple return minus beta times
   BTC's corresponding return. Choose ETH or SOL by largest absolute nonzero
   residual, lexical symbol order for exact ties. Trade continuation or reversion,
   hedge the BTC beta, and hold for four or twelve hours. Normalize the two
   initial weights to total absolute exposure 0.5 of pretrade marked equity.
2. **OI deleveraging recovery, four configurations.** BTC open interest must
   fall at least 1% over the lagged hour and the last completed price-hour must
   move at least 0.5% or 1% in either direction. Fade that price move with
   absolute initial BTC weight 0.5; hold four or twelve hours. Missing OI is
   unavailable, not zero or a forward fill.

Decide only from completed hours at T; enter at T+1 hour. Commit the exit at
entry plus the stated holding horizon. Retain fixed quantities while held.
Only one episode is open per configuration. A decision at the previous exit
can enter an hour later, leaving a full hour flat. Prospectively exclude any
episode whose decision-to-exit window crosses the known diagnostic split or
ends beyond the terminal timestamp. Count exclusions without reading outcomes.
No per-trade stop, take-profit, trailing, averaging down or leverage fitting is
included in this first batch. Bankruptcy fails the calculation; this linear-PnL
sandbox does not certify collateral, liquidation or live risk controls.

## Accounting, benchmarks and robustness

Reuse the corrected fixed-quantity cash ledger without changing it. Initial
equity is 10,000 USDT. Each execution pays five basis points taker fee and two
basis points adverse execution friction; stress doubles both to ten/four.
These are spread/slippage proxies, not venue-specific executable quote proof.
Trade-hour closes are execution references; minute perpetual marks value risk.
Complete native funding coverage is checked before certifying intervening zero
rates. Use exact settlement milliseconds and settlement marks, funding quantities
held immediately before same-timestamp executions. Charge the terminal close.
Missing or changed source identities, marks or funding refuse completion.

Report cash and a fixed 0.5-equity BTC buy-and-hold benchmark, entering at the
first delayed hour and closing at the terminal event with the same cost/funding
model. The latter may remain exposed across the split; do not describe its
segments as independent flat-start trades. Retain one continuous ledger rather
than stitching independent compounded segment simulations.

Persist each trial's complete marked ledger, executions and episodes separately.
Report development/diagnostic and whole-period net returns, marked drawdown,
same-quantity gross price return, separate funding/fee/slippage contributions,
turnover, per-asset PnL/concentration, daily returns, sample counts and exclusions.
Report net and same-quantity gross price PnL per completed episode (undefined
without episodes). Approximate break-even aggregate friction is net PnL plus
charged costs, divided by reference turnover and expressed in basis points;
it includes funding and holds actual quantities fixed. It is not a rerun with
different reinvestment or proof that this friction is executable.
The gross price return adds ledger costs back on those actual quantities; it is
not a separate zero-cost reinvestment simulation. Risk between minute samples,
queue position, depth, latency variation and partial fills remain unmodeled.

Rank at most two finalists by development-only minimum net return across base
and doubled-cost cases, lexical configuration name for ties. A config with a
failed calculation cannot rank. Zero complete episodes in either segment means
`insufficient`; fewer than ten is explicitly low sample, not hidden. Positive
net returns in both chronological halves under both cost cases allow only
`exploratory_candidate`; other completed trials are `reject`. These labels do
not imply statistical significance, fresh validation or trading admission.

## Execution boundary and next decision

Read sources without modifying them. Bind the new outer index to this plan,
source inventory, actual reviewed Git commit and implementation file hashes.
Require the entire imported source tree to be clean at both binding checks.
Create private bounded local VM output; preserve partial failures and finalize
only after source identities are checked again. Run one trial at a time to
avoid retaining twelve complete ledgers in memory. No downloads, credentials,
broker orders, NAS runtime deployment or collector configuration changes.

Synthetic tests must cover delayed/prefix-invariant signals, split boundaries,
funding/cash attribution, input corruption, independent beta behavior and
development-only ranking. Obtain independent code and quantitative review.
After the registered run, examine genuine results once and retain every trial.
Any changed hypothesis needs a new recorded batch. Later candidates need
broader regime tests, deliberately reserved fresh data and Hyperliquid-specific
execution evidence before a separately approved PAPER/TESTNET step. This batch
grants no SHADOW, TESTNET or LIVE permission and promises no profitable result.
