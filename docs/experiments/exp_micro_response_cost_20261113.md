# Frozen micro entry information versus native trading costs

Register before labels, 2026-11-13; proposed study
`micro-response-cost-20261113-v1`, delivery #256, parent #244 / #237.
This is a price-response diagnostic, not a replay of portfolio positions,
the original eight strategies' PnL, a selector or trading admission.

## Question and fixed population

Do the four unique entry-feature policies of the original micro study predict
delayed observed BTC price movement large enough to cover its taker cost hurdle?
Preserve all old eight-config failures and source pins. Targets 1/2% share the
same entry features and are collapsed here; no new alpha or target/horizon
selection. Programme count remains 32/40 in four mechanism families.

Use ALL eleven originally preselected native Hyperliquid captures, 9,323 sealed
raw parts, original development/validation/diagnostic/recent groups, and every
one of their 3,159 conditional source-qualified windows. Exact original input:
`ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`.
The canonical window admission vector binds capture, group, sequential ID,
source-event count, start/end monotonic and receipt UTC times, and end reason:
SHA-256 `d7e6dd8b3abb9990a12471b713697a66cf4af1caa3341532fcd507907ff4a320`.
Its 563,379,199,649,170 nanoseconds are approximately 156.4942 observed hours.
Short and boundary-purged windows stay in coverage; those hours are not all
valid 300-second labels or days of continuous trading. All history is exposed.
No active capture or newly selected favorable campaign may enter this study.

First qualify the complete window census without forecasts or labels. Then
stream the exact same sources for the diagnostic; never accumulate all events
in RAM. Preserve original coin, physical row, payload/file hash, ordinal,
session, gap/blackout, stale trade and duplicate trade rules. A bounded exact
trade-identity set does not evict: exceeding its count or byte bound fails the
attempt. The original small cadence sample cannot replace this population.

## Frozen signals, clocks and labels

Reuse byte-identical original `micro_flow.Pressure` and precision/input helpers
from source `a52eeee4aec9743d1673ccae4f7ff711295216ee`. At every original
10-second monotonic grid, evaluate trailing 30/120-second history and strengths
0.2/0.4 using best-size imbalance, signed trade-notional imbalance and matching
BBO order-flow sign. No microprice double confirmation, new indicator or fit.
The known native-hour entry calendar remains minute 02 through before 52.
There is no position/busy gate, 100-USDC quantity allocation or bracket exit
here: overlapping forecasts diagnose entry information and are not actual trades.
The old cost-above-stop entry rejection is also omitted: it belongs to the full
order policy, whereas this diagnostic measures entry features and their cost
hurdle without selecting different base/stress signal cohorts from that gate.

Keep these THREE different inherited guards explicit:

- Input native-book timestamp age: receipt UTC minus native time must lie in
  **-1,000 through +2,000 milliseconds**. This is not measured network latency.
- Decision freshness: last accepted book receipt must be at most **one second
  old on the monotonic clock**. Heartbeats, ACKs and trade traffic do not renew it.
- Entry observation: first NEW qualified book at or after decision + **500ms
  base / 1,000ms stress**, at most **one further second** after that due time.

Preserve channel, original payload identity and actually observed depth counts.
BBO is changes-only and supplies a touch; L2 snapshots refresh observations,
but no deeper level or trade print is used for a price label. An older native
book timestamp cannot replace a newer book. Do not interpolate, use a cached
pre-delay book, infer current depth from an old snapshot, or shift source clocks.
Diagnostic decision UTC is explicitly estimated from the last observed receipt
UTC plus elapsed monotonic time; it is not a new NTP attestation. Day clustering
uses the actual entry-observation receipt date.

Primary endpoint is **300 seconds**. Fixed **1/5/30/120-second** endpoints only
describe response decay. Let E be the actual entry-observation time. For each
horizon h, use the first NEW qualified book at or after E+h, with at most
**15 seconds** label-observation wait. Report exact actual horizon and wait.
This is a future label bound, not an extended entry request or claimed fill.
If the whole E+h+15s window does not fit inside the prequalified source window,
mark that cell boundary-purged before reading a response. Otherwise expire
missing future quotes explicitly. Feature buffers reset at conditional window
boundaries; no label crosses a capture, session, source gap or group split.

## Denominators, conditional availability and controls

Record EVERY scheduled grid decision, including stale/unavailable quote,
warm-up, calendar exclusion, no signal and long/short signal. For every signal,
cost and horizon, record entry boundary purge or unavailable delayed quote,
observed entry, then observed label / boundary purge / unavailable future quote.
Enforce these denominator identities. Retain every short window and duration.
Source liveness (last inbound message), quote-receipt age and native timestamp
age are separate fields. A heartbeat/ACK proves neither complete BBO coverage
nor that an unchanged old quote would execute now.

Changes-only eligibility and missing future quotes may depend on activity and
outcomes: complete-case distributions are conditional and potentially MNAR.
Never drop an unfavorable or unobservable clock and extrapolate surviving
signals to 24-hour trades, cash returns or guaranteed daily margin gains.

Controls are constant long, constant short and the original completed-history
30-second price-momentum direction. Compare EACH feature policy on exactly its
own signal cohort and the same cost-specific observed entry/label pair. If the
momentum direction is unavailable/zero, retain that unavailable comparison;
do not invent a zero-return control. Control clocks do not select another alpha.

## Price economics and funding limits

Report signed gross mid-price response in basis points and an EXACT idealized
unit price opportunity. Long opens at observed ask plus adverse friction and
closes at observed bid minus friction; short reverses those sides. Use existing
adverse price-grid rounding. Base fee/friction are 4.5/1 bps PER SIDE; stress
9/2 bps, with the different fixed observation delay above. Paired costs share
scheduled signal clocks, not necessarily observed quotes. No maker rebate,
queue priority, volume discount, minimum position or leverage fitting.

The unit price expression is
`10000 * (side*(exit_price-entry_price) - fee*(entry_price+exit_price))/entry_price`.
It includes observed spread and assumed fees/friction, but not quantity,
available capacity, partial orders, market impact, true latency, funding cash
or realized execution. NEVER call it realized trade/account/net margin profit.
No 10x margin return is emitted; earlier targets remain requests for different
strategy work. Constant-direction controls use the same expression and quotes.

Qualify the original complete native HL funding-event time/rate census and its
boundary pads. Each padded observation interval must fit wholly inside ONE
individually qualified request range. September and November requests are
disjoint; the union's first/last events cannot certify the unqueried gap.
Record whether the actual entry-to-label native-time interval,
with 60-second clock padding, intersects any settlement. Funding cash remains
NULL, even when no intersection is observed: this study does not own a position
or settle funding. No oracle, mark or book-mid surrogate cash is invented.
An intersecting observation is visibly unqualified for any funding-inclusive
profit interpretation; its price-only observation remains retained.

## Statistics and interpretation

Report every policy/group/cost/horizon, counts and actual contributing UTC days
and captures. Preserve per-day metric sums/counts so results are independently
reconstructable. Report both event-weighted and equal-observed-day means for
gross response, charged price opportunity and paired differences versus controls.
Also report one explicitly labeled all-exposed aggregate; it is not a new
validation cohort or selection pool.

Fixed descriptive uncertainty: 2,000 resamples, seed 20261113, sampling complete
observed UTC-day clusters with replacement. Summarize equal-day means; shared
indices apply to the policy and paired-control differences in each cell.
Use ordered resample positions 49/1949 for the two-sided 95% description.
Require at least FIVE contributing UTC days; otherwise interval is unavailable.
If a paired metric lacks a contributing day, its interval is unavailable,
rather than silently selecting a different day cohort. Day clustering does
not prove days independent or remove capture/regime selection and overlapping
forecast dependence. No significance, win-rate, account-growth or promotion
claim follows from positive descriptive means or narrow intervals.

There is NO selector, development finalist or profitable-strategy disposition.
Negative 300-second response does not mathematically reject every asymmetric
stop/target rule or an earlier transient effect. Shorter horizons may describe
that effect; they cannot become a selected winner after results. This measures
frozen entry information and its assumed cost hurdle, not the original eight
strategies' realized PnL. Publish positive, negative and insufficient cells.

## Resource and delivery boundary

Use existing sealed 4-MiB raw parts and 1,024-row batches; bounded 30/120-second
feature rings and pending entry/future-label queues. Fixed hard bounds: at most
3,000,000 exact trade IDs and 384 MiB estimated dedup bytes per capture; at most
1,000,000 ring items and 256 MiB estimated ring bytes per feature lookback;
4,096 pending requests / 16 MiB estimated pending bytes; 10,000 window records
and 8 MiB serialized metadata per capture. These are fail-closed count/byte
admission bounds, not formal Python RSS limits. No high-activity data eviction.

Create-only private local-VM artifacts: each JSONL record <=16KiB, 4-MiB
uncompressed output chunks, <=10,000,000 records, <=4GiB total uncompressed
records and <=2,048 chunk entries. Preserve source-qualified windows, every
decision/outcome, fixed cells, failure, claim and completion. Bind all imported
source/Git HEAD, both protocols and source index before/after; rehash every
original part and funding reference before/after. PAPER refusal precedes reads.
No dependency, new broker, workflow, NAS/source mutation or runtime operation.

Review/merge source and this protocol, run current CI and the separate merge
gate BEFORE actual labels. Then execute the bounded private diagnostic, obtain
independent result reconstruction and publish. Partial/depth execution, native
funding/recovery and the separate fast recorder retain their own gates.
No PAPER strategy admission, TESTNET, SHADOW, LIVE or capital action follows.
