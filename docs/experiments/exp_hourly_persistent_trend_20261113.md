# Hourly persistent BTC trend: pre-label registration

Study `btc-hourly-persistent-trend-20261113-v1`, 2026-11-13; delivery #264,
programme #237. This is a new hypothesis/clock, not a repair of the original
cost-aware daily regime study or any failed micro/OI attempt. Four new policies
consume **36/40 unique configurations** across the same four mechanism families.
The existing daily h720 benchmark adds zero new configurations. No selector,
ranking, leverage/threshold fit, fixed trade quota or strategy admission.

## Hypothesis and universe

Does completed 24/72-hour BTC momentum, evaluated hourly with persistent fixed
positions, detect cost-covering trends sooner than the old slow daily policy?
The prior daily h720 candidate motivates the question; it does not establish
that faster decisions improve returns. Evaluate ALL four combinations of
lookback 24/72 hours and slow confirmation false/true. No favourable line can
replace a failed one or acquire an unregistered parameter after results.

Universe: selected-survivor Binance USD-M BTCUSDT, January 1, 2024 00:00 UTC
through September 1, 2026 00:00 UTC, 974 calendar days. 2024 is development;
2025 and January–August 2026 are separate diagnostic groups. ALL history is
exposed retrospective; there is no fresh OOS, native Hyperliquid execution
qualification or test of current November conditions. No later recent period
is included or secretly reserved by this protocol.

## Exact causal policy

At each closed UTC hour T in a qualified segment [start,end), read exactly
721 completed hourly closes through T. For H=24 or 72, score is compensated
sum of the last H hourly log returns divided by square root of their compensated
squared sum. Missing history or zero denominator requests flat. Finite positive
price admission, 720-return annualized volatility and 720-close arithmetic-mean
confirmation come from the unchanged original feature helper. The short score
is computed separately; old configuration validation and old studies are intact.
No future bar, expanding partial warm-up, native timestamp shift or interpolation.

Flat/opposite entry requires abs(score)>=1. Hold the original same-side units
while sign agrees and abs(score)>=0.5; otherwise close. Confirmation variants
also require close versus SMA720 direction to agree for both entry and holding.
At equality with the mean they request flat. Requested entry notional is current
equity times min(0.5,0.10/sigma720). No same-side resizing, forced hourly/daily
trade, stop/target search, averaging, martingale or leverage fitting.

Use completed native TRADE-minute close T+1m for flat entry or closing. A
reversal closes at T+1m and opens the opposite side at T+2m, paying both legs.
This is a new decision each hour, not a forced new trade. The two delayed legs
finish before the next hourly decision. Margin is immutable actual initial
abs(quantity)*ENTRY MARK/2. Fee/fill differences and funding reduce collateral;
there is no automatic margin addition or artificial 10x ROI denominator.

Base costs per side: 4.5 bps fee plus 2 bps adverse friction. Stress: 9/4,
identical signal/entry/exit/risk-weight schedule with independent capital carry.
The approximate price roundtrip hurdle is 13/26 bps before funding and small
fee-notional/mark-reference differences; at 2x initial margin this is roughly
0.26%/0.52% margin cost. This algebra is not an observed return or forecast.
No maker rebates, touch fills, capacity, spread or actual execution is inferred.

## Source and required hourly execution projection

Retain original input SHA-256
`ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`.
All source inventory, native CSV, archive CRC, exact official filename/checksum,
funding census, instrument/unit/clock and byte/hash checks remain active.
December 2023 supplies the full 720-hour warm-up across month boundaries.

The original regime adapter retains only DAILY 00:01/00:02 execution frames;
it is insufficient for the new clock. A new adapter uses the unchanged native
minute iterator, retaining every hour, every hour+1m/+2m and every exact funding
event. Require every necessary TRADE reference and separate mark before labels;
never use the H1 trade close or mark as a minute fill. Initial segment marks
may be absent only while flat; every subsequent retained mark is qualified.
Keep complete held-minute mark low/high separately from sampled valuations.

Source-only admission before labels found 23,328 eligible hourly clocks,
46,656 +1m/+2m references and 71,018 total retained valuation/execution/funding
frames over the same 972 valid days. Ordered segment/frame clock SHA-256:
`a78ca54e527cba6009ad876fd32819307902611c8981b599bb141203efda36d8`.
All source data are unchanged. The extra frames make sampled drawdown/exposure
denser than the old sparse policy grid; they are not tick or continuous minute
account drawdown. The minute-extreme collateral screen stays separate.

Closed bars are assumed available by the following execution minute. Archives
provide no historical receipt/publication latency or revision-vintage proof.
A completed kline close plus adverse friction is a conditional execution proxy.
Native Hyperliquid precision, spread/depth/queue/partial-fill timing and hourly
oracle-based funding remain unqualified; Binance settlement cash flows cannot
be renamed Hyperliquid funding. This study neither requires nor changes a bot.

## Source gaps, years, terminal closes and failure ownership

Keep the old two unavailable mark days August 12, 2024 and June 29, 2026,
and its three predeclared valid source segments. Close any position at each
valid segment endpoint using its paid native TRADE-minute reference; restart
the next segment flat with ACTUAL prior closing capital. The two unavailable
days remain explicit in the full 974-day denominator. Archive-informed gap
flatness is conditional and cannot demonstrate live gap anticipation/recovery.
Hourly trade history on a bad MARK day may still support later features.

Do NOT flatten, reset capital or restart policy state at month/year boundaries.
Held quantities and state cross them. Group/daily returns use fixed UTC equity
snapshots, not a closed episode's entire future PnL assigned to its entry year.
No decision at END. Charge exact native funding to units held immediately
before same-time executions, including terminal close; nonzero-ms funding
AFTER close is outside exposure. Coarse minute fills do not prove real
completion just before a subsequent funding millisecond.

Unexpected required missing/conflicting source frames fail admission, rather
than remove losing clocks. Any ledger exception retains attempted spec/targets,
source/error and earlier completed ledgers. Partial failed exposure is UNKNOWN;
final equity/return stay null, later segments blocked. The new CLI checkpoints
all five account aggregate lengths (funding, points, episodes, trades, risks)
and equity before each case; a caught failure rolls every aggregate/equity back
to its last complete segment, including partially completed `Account.add` extends.
No failed-segment points/episodes/funding may leak into completed-only summaries.
Never fabricate flat
unprocessed days or restore initial equity. Baseline financial mismatch is a
study integrity failure, not a competing alpha's ordinary negative outcome.

## Accounts, benchmark parity and reporting

Sixteen accounts, separately carried base/stress capital: four new policies;
cash; persistent volatility-budgeted long/short entering at first qualified
hour; matched long/short per new policy; and the unchanged DAILY locked
`regime-h720-plain` benchmark. Three source segments × two costs × 16 accounts
= 96 cases. Matched controls preserve every candidate entry/exit clock and
absolute risk weight, charging same-direction close/reopen when present.
No control becomes a newly selected alternative. A failed/collateral-invalid
comparison cannot certify alpha.

The old benchmark's six accepted full cases and summary are pinned by private
baseline-index SHA-256
`2b1653f765a4d5f480bcc963e93f7c410eede584d96e95298b8673402e6e8232`;
original summary SHA-256
`7a395e2ced136ddb6dee01a6622970100386ee418f1a23dc69b0ffc609803a2f`.
Require every original native trade field, fees, friction, exact funding and
carried final equity to reproduce (1e-7 absolute/1e-10 relative float tolerance).
The baseline is evaluated first under BOTH costs; six case and whole-account
parity must pass before ANY new hourly-alpha schedule is built or scored.
Denser sampled drawdown/exposure are separately new observations. No old
published financial result or earlier source helper is rewritten.

Persist all schedules/decisions, exact requests, full cases/ledgers, episodes,
failures and summaries. Report every line/cost/group; gross price PnL, actual
fees/friction, signed funding, turnover, long/short contributions, actual
initial-mark margin ROI, median/p95/max holding, hourly action denominators,
completed trades per day, no-trade days and coverage. Report diagnostic 1/2/20%
margin target attainment without changing exits or promising target hits.

Keep `risk_invalid` EXCLUSIVELY for actual collateral-screen events: original
2x margin, current conservative 1.25% maintenance proxy, worst held native
minute mark, exact sampled funding and conservative intraminute payment order.
Retain counterfactual accounting, never delete a liquidated loss. Historical
maintenance tiers/liquidation mechanics and intraminute ordering are limitations.
`reject_drawdown` is a separate descriptive flag for sampled account DD>15%.
Other independent per-line flags: `reject_cost` if a complete base/stress whole
return is nonpositive; `reject_regime_robustness` for any nonpositive diagnostic
fold/cost; `insufficient_sample` for a missing group, <3 closed episodes or <30
exposed days in a group/cost; `failed_calculation` for an incomplete chain.
No flag selects a winner or erases numeric accounting. Passing all flags is
only a positive retrospective diagnostic, never trading admission.

Daily-return uncertainty uses the unchanged deterministic 2,000 circular-block
resamples, seed 20261113, seven-day blocks and fixed 30-day sensitivity. Matched
paired daily-return differences share indices. Report all intervals; dependence
and exposed search history prevent post-search significance or profit forecasts.
If all four fail, pause this clock/horizon family; no automatic spend of the
remaining four configurations, retrospective threshold or leverage adjustment.

## Delivery and bounded execution

PAPER refusal precedes source/output I/O. Actual financial labels require
independent nonauthor code/security and quantitative review, relevant tests,
current ready-PR CI and separate expected-head merge. Bind clean actual HEAD,
all imported source, original/new plans and both source/baseline identities
before/after. Private create-only bounded VM outputs; no NAS/runtime mutation,
download, credential, dependency, CI expansion or venue API/trading action.

Process one COMPLETE account/cost chain over three segments at a time. Write
full ledgers/requests/cases then retain only summary, daily and episode evidence;
never keep 32 full point/risk/trade lists. Use serial two-CPU, 6-GiB address-space,
nice10 execution. Source qualification measured 635,592 KiB peak RSS in 20.19s
under those limits. Every run/test log and exit status are persisted privately;
resource failure preserves evidence rather than dropping source or clocks.
Actual run plus independent reconstruction and all-outcome publication remain
open in #264; merging source alone does not close the study. Positive historical
results still require separately registered native execution/fresh future
validation and separately admitted PAPER; no TESTNET/SHADOW/LIVE promotion.
