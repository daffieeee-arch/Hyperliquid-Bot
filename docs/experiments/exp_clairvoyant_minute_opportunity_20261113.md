# CLAIRVOYANT_NONTRADABLE minute-grid opportunity diagnostic

Register before financial labels, 2026-11-13. Study identity
`btc-clairvoyant-minute-opportunity-20261113-v1`, delivery #268, programme #237.
All outputs are **CLAIRVOYANT_NONTRADABLE**. This is neither an alpha policy,
selector nor a repaired old backtest. Programme usage remains **36/40**; every
old failure and the hourly-family stop remain unchanged. Source/engineering
review, current ready CI and separate merge precede real financial calculation.

## Fixed question and source universe

On the existing BTCUSDT Binance USD-M minute-close grid, how much conditional
price opportunity would perfect FUTURE knowledge capture after assumed costs?
The operator's objective is 20% NET of TOTAL ACCOUNT per UTC day with many
trades, not position-margin ROI. This diagnostic measures only a deliberately
nontradable price/cost reference hurdle; it does not satisfy that objective.

Use ALL 974 UTC days January 1, 2024 <= day < September 1, 2026. Include the two
known MARK-gap days: no mark data are used or required. All history is exposed
retrospective, with no new/fresh OOS. BTC is preselected; no ex-post asset search.

The original source metadata inventory SHA-256 is
`184c0d05f3f21d17539f4bafb78727b7665b60f878cc5572f992ce6e4af5fe21`.
Select its 33 existing TRADE1m monthly ZIP/checksum records, December 2023
through August 2026, without downloads. The new exact source-index SHA-256 is
`4a19eeced1b487b723136dc6cc05a60a3333f465eac7fa186231045665a3e0f8`.
December's last minute CLOSE supplies January 1 00:00; do not substitute January's
first OPEN, silently drop the first interval or replace a native close with mark.
Last endpoint is August 31's final close at September 1 00:00. Each day needs
exactly 1,441 minute boundaries; globally 1,402,561 distinct boundaries.

Reuse unchanged strict native filename/checksum, CRC, bounded single CSV,
header, full minute-grid, OHLC/volume and finite-positive validators. Preserve
original native close decimal strings as exact rational prices, not rounded
floats. Bind every source, native clock and close-text hash before/after. Any
missing/duplicate/corrupt/negative/off-grid/changed source HARD FAILS the source
attempt before labels. Do not infer availability from a filename alone.

## Exactly 24 fixed scenarios

Cross nonoverlapping UTC grids G=1/5/15/60 minutes, costs zero/base/stress and
entry reference notional fraction w=0.5/1.0: 24 cases per day, ALL reported.
Intervals are [00:00,00:G], [00:G,00:2G], ... through 24:00. There are
1,440/288/96/24 opportunities respectively. Every interval is an independent
roundtrip, flat at both endpoints; entering the next one incurs a new entry
leg even if direction remains the same. No best grid/horizon or winning case
is chosen as a strategy. Midnight exit belongs to the day whose interval ends.

Each day has a fixed reference equity E=1 quote unit for exact normalization
(equivalently 10,000 reference units by linear scaling). This is NOT a carried,
continuously compounded trading account, and not a failure repaired by daily
capital resets. Within each day every entry sizes q=s*w*E/P0 from the SAME
starting-day reference, with s=+1 long or -1 short. Held quantity stays fixed
for that interval. Intraday gains/losses never change the next reference size.
The 1.0 case explicitly RELAXES default 0.5 asset/strategy PAPER exposure caps;
it changes no risk-engine setting, real leverage, order or trading mode.

For per-side adverse fraction a and fee fraction f:
entry fill=P0*(1+s*a), exit fill=P1*(1-s*a);
gross=q*(P1-P0); friction=abs(q)*a*(P0+P1);
fees=abs(q)*f*(entry_fill+exit_fill);
net=q*(exit_fill-entry_fill)-fees=gross-friction-fees.
Charge fees on actual side-specific entry AND exit fill notionals. Entry
reference notional is w*E; turnover reports both reference and fill notional.
Use exact rational arithmetic on native decimal prices/fees/weights. Compare
both directions and FLAT=0 using P1 from the FUTURE deliberately. Choose the
largest positive net only; any zero/nonpositive best value selects FLAT.
Ties between positive directions prefer long deterministically (with these
nonnegative costs such a tie is not expected); zero ties remain flat.

Zero is theoretical f=a=0, not an executable venue schedule. Base is per-side
4.5 bps fee/2 bps adverse friction; stress 9/4. Fractional quantities/precision,
instantaneous full fills and same-price reentry are assumptions, not capacity
proof. Price path, costs and all choices are paired across all fixed cases.

## Evidence, metrics and interpretation

Persist once per day the native 1,441 close texts, boundary/price hash and all
24 exact daily aggregates. For each grid/cost, a streaming digest commits each
interval's timestamps, BOTH long/short unit-weight net/gross/fees/friction/
quantity/turnover and chosen long/short/flat. Weight 0.5 is exact linear scaling
of weight1, so no huge duplicate ledgers are needed. Original native sources
plus retained minute prices permit independent reconstruction of every choice,
including rejected negative directions and flats. Digests are not evidence of
actual orders/fills. Native clock/price digests are separate from financial ones.

For every day/case report normalized net PRICE PnL after assumed fees/friction,
gross, costs, long/short/flat counts, roundtrip count and turnover. Report the
inclusive >=20% reference-equity PRICE threshold flag. This is not observed
net total-account profit: funding and other real costs/risks are omitted.
Across all calendar days report count, threshold-day count/fraction, arithmetic
mean, median, fixed nearest-rank 5/25/75/95% quantiles, min/max. Never hide quiet
days, failed cases or select a favourable clock/cost/notional after results.
There is no annualized promise, independence/significance test or promotion.

No funding is modeled; omitted funding is not certified zero cash. No true HL
spread/depth, queue, delay, partial fills, market impact or slippage proof.
No intrabar stop/collateral/liquidation/entry-recovery/daily-loss controls are
simulated. Do not call this current PAPER-compliant or executable Hyperliquid
profitability. Mandatory grid closes can cost more than variable holding;
subminute paths/other assets/funding are outside scope. This is NOT a universal
market ceiling or proof that no other strategy can reach the operator target.
If many oracle days pass, that establishes only conditional price room, not a
causal signal, actual fill capacity or future frequency of 20%-profit days.

## Bounded delivery and failures

Local PAPER guard precedes input/output I/O. Source-only index preparation and
clock/source admission may run before merge, with financial scoring disabled.
Calculation binds clean actual committed source/protocol and immutable sources
before/after. At most one month and one day of price cache, serial two-CPU/
2-GiB address-space proposal; measure source-only peak before an actual run.
Private DEV create-only output, no NAS mutation, downloads, network, new
dependency, database, CI stack or trading action. No old study/helper is edited.

Persist explicit claim, source qualification, attempted day/case identity,
failures and completion counts. Exceptions leave the study incomplete; no
missing day is set flat/zero and no result is resumed from fresh capital.
Only after independent code/security + quantitative source review, ready CI
and separate expected-head merge may root authorize the financial run. Then
independent native/arithmetic reconstruction and all-outcome publication remain
required; source PR alone must NOT close #268 or parent #237.
