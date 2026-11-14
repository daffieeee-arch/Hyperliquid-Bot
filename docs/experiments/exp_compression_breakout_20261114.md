# BTC compression breakout: pre-label registration

Study `btc-compression-breakout-20261114-v1`, programme #237. This registers
ONE new configuration under the existing price-continuation/momentum family:
**37/40 configurations, four families**. It is a distinct compression mechanism,
not a retune of the rejected directional-hourly clock/horizon family. All old
studies, their source pins, failures and family stop remain unchanged. No selector,
parameter sweep, ablation, additional alpha or automated follow-up is authorized.

## Objective, hypothesis and exposed universe

The operator's LARGE track targets **20% net on immutable initial POSITION
MARGIN per winning trade**. The separate frequent track targets 1–2% net position
margin. Neither is 20% account/day, a promised winner, daily minimum or quota.
The old positive but slow h720 result remains historical and is deprioritized.
The proposed economic mechanism is directional expansion after an unusually
narrow completed-hour range. Test whether it covers delayed taker costs and
produces useful account growth/frequency as well as margin winners.

Only Binance USD-M BTCUSDT: 2024-01-01 <= t < 2026-09-01, all 974 calendar days.
2024 is development; 2025 and Jan–Aug2026 are diagnostics. All are exposed
retrospective history, not untouched OOS/current November/native Hyperliquid.
No selection uses diagnostic results. Native Hyperliquid/fresh validation and
separate PAPER admission are necessary even after a positive retrospective result.

## Fixed causal event policy

At each whole UTC hour S inspect the H1 TRADE candle whose native open=S−1h,
native close=S−1ms, availability label=S. Its normalized range R=(high−low)/close
must satisfy 0<R<=B/2, where B is the median of the 24 PRIOR complete H1 ranges
ending S−24h through S−1h, excluding the candidate box. B must be positive;
even median averages the central pair. Require all 25 bars and positive finite
OHLC; missing/invalid history fails admission, zero-range boxes are counted.
No directional-return score, SMA, volume or imbalance condition is used.

A qualified box arms S+1m through S+59m. First minute TRADE CLOSE strictly above
its high is long, strictly below its low is short; equality is not a breakout.
Consume the first breakout even if busy/pending, recording that exclusion rather
than choosing a later crossing. At a whole hour expire the old box before forming
the new box, including while held; no breakout decision occurs on whole hours.
An order already committed by S+59m survives the next hour rollover.

At each timestamp: funding belongs to PRE-execution units, then committed
executions occur, then completed-minute data may inform new decisions. A close
may execute before a fresh signal on that same minute; its new entry is delayed
another minute. While held/pending no reversal, stacking, averaging or margin add.
No intrabar touch trigger, forced midnight entry/exit, interpolation or same-clock
predictive fill. Archive availability by the decision clock is an explicit
unverified received-time assumption, not evidence of timely real receipt.

## Causal quantity, execution and immutable marked margin

While flat at signal D lock c=side*0.25/MARK_D and committed q=c*E_D, using only
then-known mark and account equity. Execute the SAME q at F=D+1m, unconditionally
under this coarse model. E_F=E_D before entry fees while flat. The unchanged
fixed-quantity ledger accepts target weight=c*TRADE_F solely to cancel its price
denominator, q=E_F*weight/TRADE_F. This transport is NOT future-price sizing.
Persist D, MARK_D, E_D, c, committed q and F; independently perturbing future
TRADE_F/MARK_F must preserve q. A future entry gap cannot cancel a losing order.

Freeze M0=abs(q)*ACTUAL_ENTRY_MARK/10. **10x is an offline collateral reference**,
not a real leverage or risk-limit change. Old ENTRY_FILL/10 or MARK/2 summaries
cannot be relabeled; the new adapter computes its own explicit MARK/10 result.
Use native TRADE-minute closes for fills, separate MARK for valuation/collateral.
Each leg's adverse fill is reference*(1+sign(delta)*friction); fee is fee_rate
*abs(delta*actual_fill). Base fee/friction=4.5/2 bps per side; stress=9/4.
No real spread, lot/grid, depth/capacity, queue, latency/partial fill or executable
Hyperliquid profit is certified by those scenario assumptions.

From the first completed minute AFTER entry, estimate an immediate charged close:
[q*(hypothetical_exit_fill-entry_fill)−entry_fee−hypothetical_exit_fee
 +funding actually owned over (entry,T]] / M0.
At >=+0.20 or <=−0.10 commit full exit for T+1m. Record observed trigger and
actual delayed realized ROI separately: target misses/stop overshoots remain.
Exit no later than entry+24h or the known source-segment endpoint. A time/terminal
close wins a simultaneous delayed-bracket deadline. No observation at the entry
minute can trigger a bracket. Funding is exact native Binance settlement cash
at original millisecond timestamps and settlement mark, before same-time trades:
entry excludes same-time funding, exit includes it, +1ms after exit is unowned.
This is not Hyperliquid funding or an oracle approximation.

Each base/stress chain uses the SAME net-bracket POLICY with its own fees,
friction, capital and funding. Exit clocks and busy-excluded entries MAY differ.
Never imply common cost-scenario exits or delete a cost-specific losing cohort.

## Source, endpoints, gaps and ownership

Original index SHA256:
`ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`.
Reuse unchanged strict native archive/CRC/filename/hash/checksum/OHLC/timegrid
and exact funding census validators. Read all minute execution references, marks
and minute MARK low/high; H1 boxes use H1 OHLC, not minute/H1 substitutions.
Existing December2023 H1 history supplies the 25-bar warm-up across months.
Bind source bytes before/after; changed/conflicting/duplicate/missing required
input fails instead of dropping a losing clock. No download or runtime mutation.

Known MARK gaps 2024-08-12 and 2026-06-29 retain the existing three qualified
segments [2024-01-01,2024-08-12], [2024-08-13,2026-06-29],
[2026-06-30,2026-09-01]. Segment endpoints are paid flat closes; new entry whose
execution is AT/AFTER endpoint is excluded prospectively and counted. Clear
armed boxes across gaps; complete TRADE history may seed later features.
Carry actual prior closing capital across gaps and calendar years/months, with
no account reset. The two flat gap days remain in 974-day denominators. This is
conditional archive-informed outage flatness, not foreknowledge available live.
Any other missing held source fails with unknown failed-segment exposure and
NULL final account result; never resume or reset. Preserve attempted requests,
completed segment evidence and errors. Aggregate account add is transactional
across funding/points/episodes/trades/risks/equity, including partial mutation.

## Risk screens and conditional controls

At entry flag marked exposure>0.5*decision equity, M0+entry_fee>available equity,
and collateral insufficiency. Keep the committed fill and numeric result as
unsafe/counterfactual risk-invalid rather than retrospectively filtering it.
Audit M0−entry_fee+q*(mark−entry_fill)+owned funding at entry, exact settlement,
sampled marks and EVERY held-minute adverse MARK low/high. Maintenance proxy
is 1.25%*abs(q)*mark. Debit adverse intraminute funding before the worst mark;
do not credit positive funding before its unknown within-bar timing. Report
possible liquidation, not a true exchange liquidation engine. Keep full outcomes.

Existing PAPER gross1x, perasset/strategy0.5, daily-loss1% and drawdown7% stay
unchanged; report marked gross, entry-cap, UTC daily-loss and drawdown breaches
separately. Those are retrospective diagnostics, not an injected halt or claim
that PAPER would admit this research curve. Research sampled-DD15% rejection
is separate from collateral invalidity and PAPER drawdown7% breaches.

For EACH cost candidate use cash and matched always-long/always-short controls
on that candidate's exact entry/exit clocks. Controls use the same absolute
quantity-per-equity coefficient (same decision-mark/risk budget), allowing their
own capital to produce different filled units. They are conditional timing
comparators, not new alpha configurations or standalone selectable strategies.
No control's exit is independently optimized. Invalid control collateral cannot
certify alpha. Preserve all controls, including failures and negative results.

## Outputs, falsification and resource admission

Persist all hourly setup/range/zero-range/compression/expiry and first-breakout
records; all signals, busy/terminal exclusions, decisions, targets, triggers and
executions. Include source/cost/scenario identities, decision equity/mark/qcommit,
entry mark/M0/net initial-margin ROI, target-trigger versus realized success,
24h/gap exits, holdings, marked MAE/MFE, loss tails/streaks, trade/day and winner/
no-trade-day counts, full ledger, fees/friction/funding/gross/turnover/long-short,
UTC day/year returns and drawdowns. All calendar/signal denominators survive.
Record base/stress separately; counts and repeated controls are not independent.
Use the existing fixed 7/30-day block descriptive uncertainty and paired control
return differences; not an IID trade bootstrap or multiple-testing-proof alpha.

Predeclared diagnostic flags: failed calculation, collateral/entry risk-invalid,
sampled DD>15%, nonpositive base/stress account return, any nonpositive 2025/2026
fold, fewer than3 completed episodes or30 nonflat calendar days per fold/cost.
All outcomes are published without selector. A failing one-config attempt ends;
no automatic threshold, TTL, leverage, volume-filter or direction retune.
Even success is retrospective and cannot promote PAPER/TESTNET/SHADOW/LIVE.

One account/cost chain at a time with bounded source cache; propose two CPUs,
nice10, worker address space8GiB on the24GiB DEV VM. Measure source-only load
wall/RSS before financial labels; do not drop source to fit. Serialize cases
create-only on local DEV storage and release full chain ledgers after summary;
retain compact day/episode/summary evidence. No32-account simultaneous ledgers.
PAPER gate occurs before input/output I/O. Clean committed code/protocol/source
identities and original helper byte parity are checked before and after run.
No actual market financial labels until independent CODE/security+QUANT review,
current ready CI and a separate merger have admitted source. Merge never deploys.
