# Fixed BTC cross-venue catch-up: pre-label C100 registration

Study `hl-bn-cost-target-catchup-20261114-v1`, programme #237. ONE predictive
configuration proposes an explicit extension of the existing fourth
microstructure/predictive-taker family, bringing registered use to **38/40**
after compression #270. Independent review must accept this family/count
classification or require an explicit programme amendment BEFORE labels.
No quiet fifth family, last-four tuning, selector or repair of failed studies.

The final operator targets are 20% NET initial position margin per large
winning trade and 1–2% per frequent small winning trade; neither is account/day.
This FAST policy has exactly ONE primary **1%** target; 2% attainment is secondary
and never changes exits. No minimum wins, frequency or profits are guaranteed.
The eight failed micro chains, old H1 and protected Nov2–5 holdout remain intact.
The initial private v2/v3 designs stay pinned:
`a693876d355e3310fbc4cd249a977090cdcefeb08ff3bd98209ed8cf00022263` /
`6e5c589439dcf41ed59add171acbe716eb5f13d06f9ffb62e0d12cf9d2c837a1`.

## Scope and source gates

Trade only modeled native HL BTC USDC linear perp. Binance BTCUSDT USD-M is
external information, never the execution venue or a Spot/mark/COIN-M fallback.
The first fixed economic scope is the CLOSED native standard pair
`hl-cont-20261113203809-be480ab9-0000` on Nov13, not a dynamic latest capture.
The exact complete part/metadata/funding index must be independently qualified
and frozen by its SHA before labels. The oldest ten-minute Oct31 pilot is
engineering evidence only, not this economic population or statistical validation.
Its acceptance SHA is
`18f23946de767fb498400b523dbb6c575267f8ebf819c322532914275303c37e`.

Every source has explicit path/byte/SHA binding, native receipt schema, payload
hash, original physical ordinal, product/channel/session and both clocks.
Validate required raw decimals, USD-M `s/st/ps` epoch identity, exact aggressor
`m`, aggregate IDs and quantities; missing historical `nq` may remain unavailable
but is never invented or used as a substitute for `q`. Preserve observed `nq`.
An aggregate is not one whale order. LOCAL normalized rows are NOT extra flow.
Malformed/conflicting native IDs, changed bytes, wrong units or unknown required
identity fail source admission. Keep control markers and all source exclusions.

The fixed fundingHistory request must cover the whole source interval padded
by one hour each side, with exact complete native BTC hourly event census,
duplicate/conflict/cap checks and retained original event times/rates. Empty or
unqueried ranges never become zero. The policy admits entries only in native
hour minutes [02,52); it must be flat before minute58. Any modeled possible
holding or failed exit overlapping a native settlement is UNKNOWN, not zero
cash. No HLP/exact-oracle/owned-wallet framework is needed for proved flatness.

## Exactly four baseline capital chains

Cross two fixed availability models with two costs: RAW/base, RAW/stress,
WITNESS/base, WITNESS/stress. All start with 10,000 USDC, fixed C100, and carry
their own actual marked equity; no restart with fresh capital after a failure.
Base is 4.5 bps fee plus 1 bp adverse friction PER FILL, beyond observed spread;
500ms delay. Stress is 9/2 bps per fill, 1000ms. Fee-aware limits and resulting
cohorts may differ. Every chain, nonfill, partial and failure is retained.
500/2500-USDC capacity variants remain **NOT_RUN**, with no implicit permission
to activate them. They need separate capacity/budget review and root assignment
after independently complete positive baseline. They are not hidden trials.

RAW is an explicitly hypothetical prompt raw-data strategy fanout: retain each
required WS session's physical inbound order and verify nondecreasing original
receipt clocks; k-way merge their current heads by RAW receipt. Do not globally
sort/repair an inconsistent session, use exchange time as availability, or claim
the recorder's actual strategy publication ran at this speed.

WITNESS walks immutable lane physical ordinals, maintaining H=max(all past and
current original record monotonic clocks), including LOCAL/outbound clocks as
progress evidence only. Buffer required Binance raw events until their linked
LOCAL normalized marker is encountered ONLINE; release the ORIGINAL raw event
at current H, never by a future marker lookup or derived economic fields. HL
releases each inbound market/control event only when the next inbound in that
session witnesses later receiver progress. Unwitnessed tails stay unavailable.
Merge the resulting nondecreasing lane heads; retain witness ordinal/source IDs.
H/witness is a latency sensitivity, NOT a certified publication upper bound.

At each fixed 1-second decision t use only releases STRICTLY before t. Equal-time
events form a batch after that decision; no favorable cross-profile tie order.
Freshness/flow expiry uses ORIGINAL receipt/native clocks, not delayed release.
A 22-second-late event never becomes fresh five-second flow. Lagged samples are
not retroactively repaired. A delayed fill needs a NEW observed book with both
original receipt and availability >= decision+delay, original local age<=1s,
and at most1s additional entry wait. Never fill a later order on an older book
that was merely published late. Modeled fill time is availability, not backdated
raw time. Source clock-at-start/shared-container evidence and absent historical
boot/continuous-clock proof remain explicit conditional assumptions.

## One causal feature policy and frozen entry intent

On a complete 5-second receipt history use sign(sum native USD-M signed p*q),
where m=true is taker-sell. No fitted flow-strength threshold. A zero sum is flat.
At fixed one-second ticks in (t-305s,t-5s], collect valid contemporaneous
HL-mid/Binance-USD-M-mid ratios; require>=240 of300 paired samples, complete
305-second history and no unresolved source/session boundary. Use their median,
averaging the middle two. This lagged basis is never fit to future convergence.
Reference bid/ask is latest received Binance bid/ask times that median.
USDT/USDC and fast basis stability are modeled assumptions; not an oracle.
Both quotes must be<=1s old. Native ages [-1000,+2000]ms remain a declared clock
assumption, not network-latency proof. Invalid or unavailable quotes are counted.

Let T=.01, L=10, f=cost-specific fee, a=friction, b=.0005 buffer. Model long exit
X=adverse_grid(reference_bid*(1-b)*(1-a),SELL), short exit
X=adverse_grid(reference_ask*(1+b)*(1+a),BUY). These are hypotheses, not future
fills. Freeze charged-entry ceilings/floors:

- Long C=protective_grid(X*(1-f)/(1+f+T/L),BUY).
- Short C=protective_grid(X*(1+f)/(1-f-T/L),SELL), positive denominator required.

Native raw-book IOC limits are SEPARATE: BUY protective_grid(C/(1+a),BUY),
SELL protective_grid(C/(1-a),SELL). Require raw level inside its fixed limit
AND the quote helper's adverse-rounded fee-bearing fill inside C. Entry friction
is applied exactly once. Current modeled entry and signed external flow must
agree with this budget before an intent is emitted. No future BN feature filter.
Freeze decision inputs/source IDs, side, raw/charged limits, quantity, 1% symmetric
decision price band, equity/risk snapshot and version. No favorable tie choice.

N_risk=existing risk-size(E,.005,.0025); N=min(100,.5*E,N_risk,observed eligible
decision depth notional). Freeze charged upper price band U and lot-rounded
q<=N/U. All modeled fills must have charged price<=U and total quantity<=q, hence
entry notional<=N. A short above the favorable upper band is conservatively
rejected; this model restriction is not native one-sided IOC semantics. No
future upsize. Observe one fresh BBO (one level) OR actual L2 levels; never mix
fresh touch with stale depth. Walk only that observation within frozen guards.
One entry IOC; cancel its unfilled remainder. A valid partial entry owns its
actual position and fees; no later add. Minimum entry10USDC, except genuine
reduce-only exits. Missing/invalid precision is an explicit assumption/failure,
never rounded-up size. Reuse existing precision/fill/cash/risk pure helpers.

## Position, exit and failure ownership

Immutable INITIAL REFERENCE margin M=sum actual entry-fill notionals/10. This
is entry-FILL based research ROI, not certified venue entry-MARK margin or an
actual10x leverage setting. Charge fees on every actual modeled fill notional.
Actual cash and signed quantity give marked account equity; quote-mid marking
is sampled and not a venue-authoritative liquidation/oracle valuation.

Primary target is COMPLETE hypothetical executable charged-close ROI>=1% M;
stop<=-5% M, time exit60s after first fill, or signed(reference_mid/HL_mid-1)<=0.
No fictional full-size touch close when observed depth is insufficient: start
a reduce-only liquidity exit and keep uncomputable close ROI unavailable.
Priority: source/stale/risk/liquidity guard, stop, mandatory time/calendar,
target, gap closure. Freeze1% adverse exit band at trigger, preserve its limit
and original trigger+5s deadline; later reasons never reset/widen either.
Retry on distinct NEW delayed observations, consuming only actual remaining
size. Identical snapshots do not replenish already simulated consumption.
Credit a level only after an explicit observed size increase or price change,
retaining the conservative modeled depletion record. No entry while exiting.

If residual quantity survives deadline, held source gap or source end, retain
all fills/fees/remaining position and **FAILED_NULLACCOUNT**. Final account
return stays null and no later window/group repairs that chain. A failed
partial entry also cannot disappear. Targets/stops can overshoot at the next
delayed fill; realized1%/2% hits count only completely closed episodes including
all costs. Disarm after an episode until the cost-gated condition is false for
five consecutive qualified ticks; no averaging down or forced trades.

Current PAPER hard limits remain unchanged, including gross1x, asset/strategy
.5x, loss1% daily/3% weekly and7% peak drawdown; enforce these in the research
state using existing contracts. Reduce-only exits remain allowed during halts.
Modeled10x reference margin does not increase account exposure or admit orders.
No sampled book/stop calculation proves continuous collateral/liquidation safety.

## Reproducibility and delivery

Persist full input/source/protocol/code bindings; every decision/rejection,
intent/limit/q, fill/nonfill/partial/exit, actual source/witness/time IDs, margin,
cash/equity/fees/quantity, episode ROI and failure. Report all four chains,
eligible/exposed durations, actual completed trade counts and observed-hour
frequency, loss streaks/tails, risk halts and sampled account drawdown. No
certified full UTC day means no daily-return or24h-frequency extrapolation.
This2h exposed smoke cannot establish a profitable strategy or fresh OOS.
No future T0 is reserved; no PAPER/TESTNET/SHADOW/LIVE admission follows.

Source qualification streams one sealed part at a time, with one bounded
private local projection per required session/availability lane if needed for
replay; source projections contain no signals/outcomes. Bound source part4MiB,
capture parts5000, raw rows10million, projection4GiB and buffered linkage32MiB.
Run four capital chains serially; propose2CPUs/nice10/8GiB address space on the
24GiB DEV VM, measure before full labels. No source/NAS writes or archive
deletion, API/network/trading clients, new dependency, database or CI stack.
PAPER guard precedes I/O; local create-only outputs retain failed evidence and
pre/post immutable source/code checks. Original validators/studies stay unchanged.
Independent CODE/security+QUANT and family/budget review, current ready CI and
separate expected-head merge precede ANY new financial labels. Source merge
does not close the actual-study delivery issue or deploy anything.
