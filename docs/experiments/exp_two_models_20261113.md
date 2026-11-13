# Two-model momentum and microstructure study

Registered before financial scoring, 2026-11-13; discovery programme #237.
Daily identity: `daily-momentum-20261113-v1`.
Micro identity: `micro-flow-20261113-v1`.
The operator requests deep tests of larger daily long/short movements and
frequent small trades. Targets are **net return on initial position margin**:
20% for the daily bracket; 1% or 2% for micro trades. They are not returns on
the whole account. No winning daily minimum or profitable trade quota is assumed.

## Registration and data boundary

Preserve all earlier experiments, their negative/insufficient outcomes, code
pins and holdouts. In particular, Nov2–5 remains insufficient/not tested, and
January discovery does not become fresh OOS. The earlier twelve configurations
covered two families. This study registers eight daily and eight micro configs:
**28 unique configurations of the programme's 40 ceiling, four families total**.
Benchmarks and paired execution stresses are controls, not additional alpha
searches. No hidden trials or post-result threshold changes are permitted.

Source qualification and bounded acquisition may inspect native schemas,
clocks, costs and funding censuses before scoring. Record their identities.
Review and merge the final plan/evaluator before calculating financial trials.
Both studies are exploratory retrospective work on exposed history. There is
**no untouched OOS in this run**. Reserve any later fresh test separately.

Public read-only acquisition is limited to Binance BTCUSDT funding-history
responses, January 2020 through September 1, 2026 plus a one-day boundary pad,
and Hyperliquid BTC funding-history censuses over the selected September and
November capture periods. Preserve complete raw responses, request bounds and
pagination. No keys, signatures, trading calls or downloads of unnecessary
venues. Store bounded exports locally on the VM; never change the NAS sources.

## Daily model: hypothesis, universe and folds

Hypothesis: completed-history time-series momentum identifies useful BTC
long/short directions beyond merely owning bullish or bearish market exposure.
Use BTCUSDT Binance USD-M native hourly trades, minute trades, minute marks
and exact native/REST funding. Existing source inventory covers January 2020
through August 2026. Early headerless CSVs are supported explicitly. Source
hashes, official filename-bound checksums, finite OHLC, instrument, complete
minute/hour grids and funding census must qualify before calculation.

Calculation starts January 8, 2020 00:00 UTC after seven fixed warm-up days;
ends September 1, 2026 00:00 UTC, including the final charged close. Development
is 2020–2023, validation is 2024, chronological diagnostics are 2025 and
January–August 2026. All are exposed retrospective folds, not untouched OOS.
Features always use only preceding completed observations; yearly/monthly
boundaries must not fit parameters or reset away losses. Select at most one
daily finalist from development net results under both cost cases, before
using later folds for its predeclared assessment.

Eight configurations: lookback **24/72 hours**, admission **every nonzero
momentum / normalized magnitude >=1**, exit **next-midnight / net-margin
bracket +20% and -10%**. At 00:00 UTC, direction is the sign of the sum of
completed hourly log returns; normalized magnitude divides its absolute value
by the square root of the sum of squared hourly log returns. Zero/unknown
history means unavailable, never a fabricated trade. The only instrument is
BTC; do not retrospectively substitute the best performing coin.

Enter at the first completed trade-minute close at 00:01 UTC. Request initial
absolute notional 0.5 of marked account equity, with fixed 10x reference leverage
for margin reporting. Initial equity is 10,000 quote units; margin denominator
is immutable actual entry-fill notional divided by ten. No leverage fitting,
martingale, additional margin or averaging down.

Bracket observations begin at 00:02 using completed trade-minute prices,
actual entry fill/fee, settled funding and an estimated charged immediate
exit. If prospective net ROI crosses +20% or -10% of initial margin, commit
an exit at the **following** completed minute close. Planned midnight exit
always wins the terminal deadline. These are close-observed triggers, not
claims that a historical OHLC touch filled a resting order. Gaps can overshoot
the stop or miss the target; preserve actual realized results. Every admitted
daily position closes by the next midnight, before the following entry.

Use the existing corrected fixed-quantity cash ledger. Trade prices execute;
marks value equity. Funding uses exact native settlement milliseconds and
qualified settlement prices. Native rates alone do not certify payment notional.
Base assumes 4.5 bps fee and 2 bps adverse execution friction per side; stress
doubles both on the **same base signal/exit schedule**. These are Hyperliquid-like
cost assumptions over Binance price/funding history, not historical executable
Hyperliquid economics. Keep that venue-proxy limitation explicit.

Carry base/stress equity independently through monthly processing; terminal
flatness is an invariant. Global drawdown spans all months and pre/post execution
valuations. Do not stitch independent reset accounts into a fictitious curve.
Audit held minute mark extremes and funding against a conservative isolated
10x maintenance proxy (current small BTC tier 1.25%); any possible liquidation
is risk-invalid/counterfactual, not a discarded losing trade or certified PnL.
Historical venue collateral and intraminute ordering remain unproven.

Report each calendar year's return, fees, friction, funding, marked drawdown,
long/short attribution, complete trades, no-trade days, target attainment across
all calendar days, realized margin ROI distribution and losing streaks. Include
cash and identical-calendar always-long/always-short time-exit benchmarks.
An exploratory daily candidate must have positive base and stress net results
in each later fold, beat both directional development benchmarks under the same
cost cases, and have no unresolved calculation or collateral failures. This
permits a candidate but never asserts significance or trading admission.

## Micro model: hypothesis, tape and folds

Hypothesis: same-venue observed order-book pressure and native signed trade
flow identify short price moves large enough to pay executable spread and
fees. Primary input is genuine HL BTC perpetual trades/BBO/full-L2 snapshots,
not January mark-candle movement counts or a synthetic fill tape. Filter BTC
by native payload coin even where an outer product contains ETH/SOL addons.
Native trade identity is `(time, coin, tid)`; replay order uses local monotonic
receipt time and ordinal. B/A signs follow the project decoder; source semantics
and any inferred aggressor interpretation remain explicit.

Use all preselected hash-pinned closed September captures and the six completed
Nov13 publications. September development is the first <=72h capture group
(Sep18–21); validation is the Sep23 capture; diagnostic is the Sep24–26 capture
and remaining closed September captures. Nov13 is a separately reported recent
bear-market diagnostic. All are exposed/selected coverage, not fresh OOS.
Do not select campaigns by PnL. Old statuses and missing producer identities or
NTP attestations remain documented; new qualification is not retroactive old
study admission. Current active recordings are excluded.

Before financial reads, declare clock/session-contiguous research windows from
capture metadata, ordinal/monotonic continuity and known gaps. Retain every
qualified window, all exclusions and durations. Reset features between windows;
require warm-up and enough scheduled time for the full holding/deferred exit.
This measures performance **conditional on intact windows**, not live recovery
through unplanned gaps; the latter remains an operational limitation. An
unexpected held gap/unknown exit fails its case with partial evidence retained,
never deletes a losing position. Missing/integrity-invalid source data cannot
be silently repaired or converted into successful skips.

Eight configurations: trailing lookback **30/120 seconds**, confluence threshold
**0.2/0.4**, net-margin target **1%/2%**. On a ten-second monotonic decision grid,
require current best-level quantity imbalance and trailing signed trade-notional
imbalance above threshold with the same sign, plus matching observed BBO order
flow imbalance. Zero flow means no signal. Microprice is a diagnostic, not an
independent confirmation of the algebraically equivalent best-level imbalance.
L2 is a full snapshot and refreshes unchanged best quotes; BBO only arrives on
changes. The observed pressure proxy is not full L3 order/cancellation history.

One position at a time, initial account 10,000 USDC, requested notional 0.5 of
marked equity, capped by executable displayed entry size, valid BTC lot/grid
rounding and minimum notional. Immutable margin is actual filled entry notional
divided by ten. Stop is -2% net initial-margin ROI; maximum holding is 300 seconds.
No forced daily quantity or minimum net winner count.

Long enters ask and exits bid; short enters bid and exits ask. Use the first
qualified book **after 500ms** base latency, at most one further second wait;
book freshness <=1s. Base fees are 4.5 bps per side plus 1 bp adverse friction
per side beyond observed spread. Stress is 1000ms latency, 9 bps fee and 2 bps
friction per side. The same frozen policy is replayed under stressed conditions;
fee-aware triggers and delayed fills may therefore differ. Do not call it an
identical-fill stress or select whichever interpretation performs best. Triggers
use already observed hypothetical charged-close ROI; realize the next delayed
fillable quote, preserving target misses, stop overshoots and nonfills.

Maker fills are not primary evidence: aggregate L2 cannot prove queue position,
and a touched limit is not a fill. Any later maker sensitivity needs a separately
recorded conservative queue/trade-through contract. No queue edge is invented.

Funding avoidance is a fixed calendar eligibility rule: enter only after minute
02 and before minute 52 of the known native venue hour; close before minute 58.
Qualify the complete actual funding-event census over padded source windows and
prove no settlement intersects each conservative possible holding interval,
including native clock uncertainty. Older host NTP is not assumed. `activeAssetCtx`
funding is not realized cash, and an oracle cannot be replaced with book mid.
If coverage or no-exposure proof fails, retain failure; never assume zero.

Report base/stress net equity, fees, spread/friction, marked risk, every fill,
nonfill, signal/rejection count, immutable per-trade margin ROI and target
attainment, activity per observed hour/day (not claimed 24h extrapolation),
turnover and long/short contribution. Cash and a fixed 30s price-momentum/2%
target benchmark share eligibility, fills, stop and costs. Choose at most one
micro finalist on development only; candidate assessment requires positive
base/stress results in each qualified later group, improvement over the same
benchmark in development, and no unresolved exposure/funding/calculation risks.
Small day counts, conditional windows and unavailable liquidation/queue evidence
remain limitations even for a positive exploratory candidate.

## Completion and operational boundary

Persist every attempted trial, failures, full execution/marked ledgers, source
and code identities, exact fold boundaries, costs and benchmark results. Bind
actual reviewed Git HEAD, clean imported source and source/plan hashes before
decoding outcomes and recheck before completion. Research strategy modules have
no venue API, credentials or signing. Bounded local VM jobs must not disturb
the NAS collector or modify durable input archives. No additional CI stack.

Independent code/security and quantitative/data review precede separate explicit
expected-head squash merge. Source CI is followed by actual registered runs and
independent result verification. Report negative, positive and insufficient
outcomes honestly. No PAPER strategy admission, TESTNET orders, SHADOW, LIVE,
capital, credentials or real risk settings are enabled by this study or merge.
