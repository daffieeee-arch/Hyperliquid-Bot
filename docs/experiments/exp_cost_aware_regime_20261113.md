# BTC cost-aware regime continuation

Pre-scoring registration, 2026-11-13; issue #245, parent discovery #237.
Identity: `btc-cost-aware-regime-20261113-v1`.

## Question and retained search history

Can completed-history BTC momentum pay actual turnover costs when positions
persist through multi-day regimes, with explicit flat states and fixed held
quantities? The previous daily study lost after daily close/re-entry costs.
Its eight variants, four collateral-invalid curves and all micro failures stay
unchanged. This is a new hypothesis/identity, not a repaired profitable result
under the old name. Exactly four new configurations take the programme to
**32/40 unique configurations, still four mechanism families**. No long-only,
short-only, sizing, threshold or leverage sweep is authorized by this protocol.

## Universe, inputs and exposure status

BTCUSDT Binance USD-M only, January 1, 2024 00:00 UTC through September 1,
2026 00:00 UTC. December 2023 completed hourly closes provide warm-up. Reuse
the immutable two-model input SHA-256
`ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`:
native hourly trade closes, minute trade execution references, minute mark
low/high/close and complete exact native/REST funding with settlement marks.
All original file, checksum, inventory and funding-census checks remain active.
Keep at least 721 completed hourly closes across monthly input boundaries.

Development is 2024; validation 2025; diagnostic January–August 2026. **All
are exposed retrospective history. There is no fresh OOS in this calculation.**
November market context cannot select parameters or relabel those folds.
Positive results remain Binance price/funding proxies, not native Hyperliquid
execution proof. Earlier archives lacking funding settlement marks are excluded.

Two known unavailable mark days impose three predeclared conditional segments:

1. January 1, 2024 00:00 through August 12, 2024 00:00 UTC;
2. August 13, 2024 00:00 through June 29, 2026 00:00 UTC;
3. June 30, 2026 00:00 through September 1, 2026 00:00 UTC.

Close any position at the valid native minute reference at each segment end;
begin the following segment flat with the prior closing equity. All variants
and controls use exactly these boundaries. No monthly or annual flat/reset,
no mark interpolation, loss deletion or capital restoration. Missing initial
marks are permitted only while flat. The 974-day denominator retains both
unavailable days as flat/no-target days. These scheduled research boundaries
condition on known archive gaps; they do not demonstrate live gap anticipation
or recovery. A new unexpected held gap fails the attempt and preserves evidence.

## Four frozen causal policies

Configurations cross completed-hour horizon H=168/720 (7/30 days) with slow
confirmation false/true. Each daily decision at 00:00 uses observations through
that instant only. Set r_i=log(P_i/P_(i-1)) and
z=fsum(r_i over H)/sqrt(fsum(r_i squared over H)), using the deterministic
compensated hourly sums (mathematically log(P_t/P_(t-H)) in the numerator).
Missing/invalid history or
zero denominator requests flat. There is no partial warm-up or future access.

An initially flat policy enters sign(z) only when abs(z)>=1. Hold the original
quantity while sign still matches and abs(z)>=0.5; otherwise close. Opposite
admission requires the full abs(z)>=1 threshold. In confirmed variants, entry
and holding additionally require sign(P_t−SMA720)==the requested side; equality
or missing confirmation requests flat. SMA720 uses exactly the last 720
completed hourly closes. This correlated price filter is not independent news.

Execute flat entries and closes at 00:01's completed native trade-minute price.
For a reversal, close at 00:01 and open the opposite side at 00:02, charging
both actual legs. No same-direction resizing or repeated entry fee. All held
units stay fixed until an actual exit. Terminal segment closes are charged.
There is no forced daily trade, fixed take-profit, profit guarantee or martingale.

Initial equity is 10,000 quote units. Entry requested notional is
equity*min(0.5,0.10/sigma), where sigma=sqrt(365*24*mean(r_i squared)) over 720
preceding completed hours. Zero/missing/nonfinite sigma refuses entry. The 10%
annualized entry risk budget and 0.5-equity cap are fixed, not fitted. This is
an **entry** cap; fixed units drift as prices change. Report peak marked gross
exposure and time in market rather than claiming a continuously enforced cap.

Use isolated 2x collateral for this offline experiment: frozen initial margin
M0=abs(q)*entry mark/2. This follows mark-based margin, unlike the earlier
study's separately labeled 10x fill-notional convention. Fees, adverse fill/mark
differences and native funding reduce collateral; no automatic margin addition.
Actual realized net ROI uses M0, never a fictional 10x denominator. Report 20%
net-margin target hits diagnostically; do not force exits or trade quotas to
obtain them.

## Accounting, risk and controls

Reuse the unchanged corrected fixed-quantity ledger. Regular marked valuations
are hourly, with additional exact native funding events and daily 00:01/00:02
execution references. Charge funding on pre-execution units at its original
millisecond and certified Binance settlement mark; never floor it to an hour.
Hyperliquid funding instead uses its oracle and hourly calendar, so these cash
flows cannot be relabeled as executable Hyperliquid cash flows.
Normal entries are delayed until 00:01/00:02, but predetermined terminal gap/end
closes use midnight minute references. Those coarse fills cannot certify that
a real order finishes before funding a few milliseconds later; report that
boundary-execution limitation rather than claiming executable funding capture.

Base per-side costs: 4.5 bps taker fee plus 2 bps adverse friction. Stress:
9 bps fee plus 4 bps friction, same signal/entry/exit/risk-weight schedule with
independently carried capital and actual fixed quantities. No invented maker
rebate, queue priority or free reversal. Costs remain assumptions over archive
prices, not measured live spread/impact or order fills.

Audit collateral at actual entry, every held minute mark low/high, each exact
funding settlement and exit. Use the conservative current 1.25% small-tier
maintenance proxy, debit adverse intra-minute funding before the worst mark,
and retain any possible liquidation as risk-invalid/counterfactual. Never
discard that loss or claim the subsequent curve was executable. Minute-ordering
and historical tier/account mechanics remain limitations. Hourly account
drawdown is sampled and must not be described as minute or tick drawdown.

Controls share the segments, sizing, fees, delays and collateral model: cash;
persistent long and short at the first volatility-qualified entry per segment;
and per-configuration fixed-side long/short controls matching every candidate
entry/exit clock and absolute entry risk weight. Matching closes/reopens remain
charged. Distinguish persistent beta exposure from matched timing/volatility
evidence. A losing or collateral-invalid comparator cannot certify alpha.

## Selection, uncertainty and conclusions

Compute daily marked account returns from equity snapshots at each UTC
midnight, including same-timestamp settled cash flows. Missing-day flat equity
is carried, not a fabricated held valuation. Folds use these mark-to-market
snapshots even when an episode crosses a year: never allocate a later closed
episode's entire PnL to its entry year or use future loss flags to select DEV.

Choose at most one development finalist. Eligibility requires positive DEV
base and stress returns, at least 30 nonflat calendar days and three completed
DEV episodes under each cost case, with no DEV source/calculation/collateral
failure. Rank by the smaller base/stress annualized daily Sharpe (365 days,
sample standard deviation), then name. Cash-only/zero-variance scores cannot
win. No later result can select a runner-up. If a legacy whole-segment failure
prevents serializing any configuration's complete development ledger, selection
is explicitly unavailable for the entire batch; do not rank the remaining
known configurations against that unknown history.

A positive retrospective candidate needs positive base and stress net returns
in both later folds, no unresolved calculation/collateral failure, at least
30 exposed days and three completed episodes in each later fold, and sampled
whole-period account drawdown <=15% for both cost cases. Report every positive
return even when those criteria fail. Report directional-control differences
and paired daily-return uncertainty separately: historical profitability does
not by itself demonstrate edge beyond beta, statistical significance or
Hyperliquid execution. A finalist lacking comparator evidence is not alpha
validated and cannot proceed to trading admission.

Report all four configurations and all controls, DEV/validation/diagnostic
returns, total return and sampled drawdown, gross PnL/fees/friction/funding,
long/short contributions, actual margin ROI, targets/losses, hold durations,
drift and calendar coverage. Describe daily-return uncertainty with deterministic
2,000 circular moving-block resamples (seed 20261113), 7-day blocks and a fixed
30-day sensitivity, 95% intervals for annualized mean return. Use shared sampled
indices for candidate-minus-matched-control return differences. Do not select
the better block length or claim post-search independent significance.

Any finalist and its complete source/input/cost identity must be frozen before
a separately registered future confirmatory period. No fresh period is silently
reserved or tested by this protocol. Future evidence and independently admitted
PAPER operation are required; no TESTNET, SHADOW, LIVE, real capital, credentials,
risk-limit change or deployment is authorized here.

## Execution of the study

Before financial scoring, merge reviewed source and this plan after deterministic
tests, independent nonauthor code/security and quantitative review and existing
scoped CI. Bind actual Git HEAD, clean imported source, plan and input hashes
before and after the run. Use bounded local VM resources, private create-only
outputs, full ledgers/failures and explicit completion status. Independently
reconstruct financial results after calculation. Keep the NAS collector and
all original archives unchanged.

The legacy simulator does not serialize its partial state when it raises.
On that path preserve the complete attempted spec/target/source identity,
exception and earlier complete segment ledgers, block further account work,
and label failing-segment exposure/valuation unknown. Final account equity,
return and gross PnL remain null; earlier equity is named as the last completed
segment only. Do not fabricate a partial failing ledger or claim missing losses
are zero. Complete preceding DEV evidence remains usable; unknown DEV forbids
the whole batch from choosing a substitute finalist.

Primary references consulted November 13, 2026:

- [Time Series Momentum, Moskowitz/Ooi/Pedersen](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf): mechanism motivation; its diversified longer horizons do not validate this BTC study.
- [Hyperliquid fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees): current base taker schedule; no discount assumption.
- [Hyperliquid margining](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining): mark-based initial margin and isolated collateral.
- [Hyperliquid funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding): hourly payments use oracle price; Binance settlement marks are only a proxy here.
