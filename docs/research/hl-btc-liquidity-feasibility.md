# Hyperliquid BTC liquidity information — feasibility decision

Status: **research design / preregistration pending**. No strategy returns,
feature/target associations, fitted model or economic result have been computed
for this proposal. No future period is reserved by this document. The existing
[Spot-flow protocol](../experiments/exp_btc_spot_flow.md), its Nov2–5 window,
funding gate and every earlier stopped family retain their original identities.

## Research question and economic purpose

For Hyperliquid BTC-PERP, does the **currently observable best bid/ask size
imbalance** add information about a subsequent, fixed-clock price movement
beyond a time-only baseline, after accounting for observation age and execution
latency? A predictive result must separately demonstrate economically useful
magnitude after costs before a standalone trading hypothesis is proposed.

Use one continuous feature in a first predictive baseline:

```text
I1(t) = (BTC_size_best_bid(t) - BTC_size_best_ask(t))
        / (BTC_size_best_bid(t) + BTC_size_best_ask(t))
```

Both levels must belong to one valid received native message and its instrument
and session. Zero/invalid sums are unavailable, never neutral imbalance. The
feature is a snapshot of displayed size, not aggressive trade flow, an order
arrival/cancellation count, true event OFI or the size of a tradable maker queue.
This differs from the stopped price-trend, trade-flow, reversal, funding and
calendar rules in the [study register](historical-study-register.md). Adding
this feature to one of those failed rules would require a separately justified
new mechanism; it is not permission to reopen their results or holdouts.

[Strategy direction](../STRATEGIES.md) initially treats microstructure as an
execution/timing input. A prediction probe can establish whether further work
is justified; it does not amend the frozen Spot-flow strategy or admit trading.

## Observable data and choice of feed

Official [subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
describe aggregated `l2Book` snapshots with price, size and order-count fields,
with five levels in fast mode and twenty in slow mode. Individual order IDs,
cancellations and queue priority cannot be recovered from snapshot differences.
Documented `bbo` messages also carry the best levels. If those size fields are
valid in the retained BBO product, I1 should use that native message directly;
it need not inherit the slower deeper-book cadence. This is an observation
choice, not proof that the faster product has profitable predictive content.

### Completed bounded observation audit (2026-11-01)

The read-only probe selected the first, middle and last four closed raw parts
from each of `hl-native-public-20261101k-0000` and `-0001` before decoding:
24 parts, 9,053 rows and 823,331 bytes. Hashes and file identities were checked
again after reading. Cadence uses only contiguous parts within the same session;
no interval bridges the unobserved segments.

| Observation | Bounded finding |
| --- | --- |
| Native subscription | Retained acknowledgement: `l2Book`, BTC, `fast:false`, `nSigFigs:null`, `mantissa:null`. |
| Deeper book | 262 snapshots, each 20 bid and 20 ask levels; 256 within-segment receipt intervals: median 5.3848s, p95 5.5804s. |
| Best levels | 5,952 BBO messages; both touches non-null with `px/sz/n` keys and positive finite sizes; complete price/order-count and uncrossed-quote validation remains pending; 5,946 receipt intervals: median 0.1408s, p95 0.7776s. |
| Native-time to receipt difference | BBO median 335.3ms, p95 601.5ms, max 1,854.0ms; L2 median 419.8ms. These include transport, batching and clock offset; they are not calibrated one-way latency or order-ack latency. |
| Causal cross-feed comparison | 260/262 books had an earlier received same-session BBO; its receipt age median 0.1818s, p95 1.0074s, maximum 2.1465s. All 84 pairs with equal native timestamps also had equal touch `px/sz/n`. |
| Availability conclusion | I1 is computable from retained native BBO at its faster observed cadence. This does not establish persistence, predictiveness or profitability. |
| Transport health | Each closed four-hour run declares one gap/reconnect; `research_ready:false`. Sampled metadata cannot locate the full gap duration or infer lost-message counts. |

The full local reports are `bounded-feasibility.json` (SHA-256
`b1205d6a7e3aa01f3cc4cfb504355903efe4bd9abff48363a7ddceff60341214`)
and `covariate-feasibility.json` (SHA-256
`1456fd1cd71dc9beed6843237b99c722f3f64641c88c214b9ec5bcca2187fcd6`),
under the operator report directory `2026-11-01-liquidity-feasibility`.
They preserve selected part identities and audit-script hashes. Review evidence
and artifact references belong to the PR linked from issue #217.

A trade batch contained native events up to 31.25s older than receipt; all 45
sampled events with lag over 5s occur in the first trade envelope of a sampled
segment, which is not necessarily its actual session start. Do not fuse trades
with current BBO on receipt time while treating them as contemporaneous. Trades
are not a feature of this single-feature proposal.

This purposeful sample is **feature-exposed development evidence**, not a
random sample, full-period admission or OOS data. No forward-price targets or
returns were decoded. Successful storage publication certifies neither
uninterrupted observation nor research suitability.

## Literature and the hypothesis boundary

[Cont, Kukanov and Stoikov](https://arxiv.org/abs/1011.6402) relate contemporaneous
order-flow imbalance to price impact in US stocks. This is not evidence of
forward net profitability. [Gould and Bonart](https://arxiv.org/abs/1512.03492)
study next-mid-price-change direction from queue imbalance in ten Nasdaq stocks.
Their target and market differ from a delayed, fixed-clock Hyperliquid trade.
A next received quote is also not necessarily the next exchange tick; batching
or unobserved intervening events can separate those targets. More critically,
its native event can precede the decision receipt: the observed BBO receipt lag
is often larger than the interval between receipts. Receipt-causal features
alone therefore do not make a target a tradable future. Admission must establish
that the target native event follows decision/execution time with bounded clock
uncertainty. Otherwise any result is only a received-stream forecast, with no
claim about executable future-price prediction.
Neither paper establishes that information persists beyond our measured feed
cadence, yields enough basis points after latency, or supports maker fills.

No threshold, holding horizon or predictive protocol is frozen here. The
first candidate model is an intercept plus I1 logistic baseline, with no feature
selection or ML sweep. Its target must be defined before any feature/forward
price association is calculated. If a three-way up/unchanged/down fixed-clock
target is used, unchanged observations remain a category rather than being
removed retrospectively. A next-event target would need its own censoring and
waiting-time contract; it is not interchangeable with a fixed holding horizon.

## The cost hurdle

The current [tier-zero perpetual fee schedule](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)
lists 4.5bps taker per fill. With an illustrative adverse slippage assumption of
1bp per fill, a near-flat-price round trip starts around **11bps plus the
observed spread and any funding cost**. This arithmetic is a design hurdle,
not a finalized historical fee/funding model or an account-specific tariff.
Fees must ultimately use each actual modeled fill notional. Longs buy ask/sell
bid and shorts sell bid/buy ask; observed spread must not be charged twice.

Correct next-tick direction can still be economically useless. The measured
observation/latency scale and a plausible source of movement magnitude must
justify any eventual holding horizon. Enlarging it merely to hide costs changes
the question. Funding cannot be fabricated as zero from an hour-boundary guard;
a reviewed native event/valuation contract is required for exposed positions.
L2/BBO data alone do not establish maker queue position or actual fill priority.

## Next deliverable and implementation boundary

The bounded observation audit above is complete; its independent review is the
current acceptance gate. The next research deliverable is **one finalized
predictive protocol for native BBO I1**, using its observed faster cadence and
explicit receipt lag. The five decisions below must be frozen together before
label decoding. If a plausible fixed-clock effect cannot survive that measured
lag and the economic hurdle, record no-go instead of selecting a longer horizon
by its returns.

Source admission for that protocol must cover the entire selected closed
interval, not repeat the same bounded sample or treat it as a complete census.
Reuse the existing BBO decoding and no-follow hash-bound raw-part/publication
readers. A minimal reusable admission report should retain source/manifest/part
hashes, original subscription/schema/units, null/invalid levels, session/channel
boundaries, separate native and receipt clocks, repeated timestamps, quality
gaps and causal quote-age distributions. It must not produce future-price
differences, labels, correlations, accuracy, model coefficients or PnL. Any new
reader implementation needs synthetic tests for session/clock regressions,
future-message refusal, null sizes, duplicates, instrument mismatch, hash
changes and publication gaps; no new event-order-book engine is needed.

Only after the observation report passes independent review can a separate
preregistration fix the following five remaining choices as one complete
contract; an unresolved row means no predictive or financial run:

| Required decision | Acceptance before outcome decoding |
| --- | --- |
| Target and causal sampling | One fixed-clock horizon and sampling grid justified by actual cadence, stale-quote limits and mechanism; exact receipt-time feature joins and genuinely future native-event targets after decision/execution time with measured clock uncertainty, zero-return category, boundary/gap/unknown-outcome handling and execution-latency assumption. No horizon search on returns. |
| Chronology and source selection | Complete native publication/part census; exact non-overlapping train/validation/untouched future dates with label-span purge/embargo. All Nov2–5 data are excluded from this study; that interval belongs to the original Spot-flow protocol. Existing inspected history stays exploratory. |
| Model and comparison | Fixed intercept-plus-I1 model and fitting/convergence rules; training-only transforms and class prior. Compare against intercept-only and declared time-only baseline with identical samples. No label-dependent deletion, retrospective threshold selection or expanding feature menu. |
| Statistical sufficiency | One primary proper-score improvement measure, dependence-aware uncertainty, significance rule and minimum independent time blocks chosen before labels. Determine sample/power requirements from a predeclared economically relevant effect and synthetic assumptions; millions of correlated frames do not establish an adequate sample. Seal later stages if earlier gates fail. |
| Economic interpretation | Predictive improvement alone is not an edge. A later fixed trading rule needs actual-notional fees, bid/ask size capacity and grid rounding, conservative fills/latency/slippage, native funding, cash/simple exposure benchmarks and 1x/1.5x/2x stress before any return test. |

## Current decision

**No-go for event-OFI, queue reconstruction, maker-profit or profitable
high-frequency claims from the existing aggregated snapshots.** Best-level
imbalance **is observable in the audited native BBO sample**. Proceed to its
single predictive protocol after independent audit review; complete-period
quality, target/latency justification and statistical sufficiency remain gates.
A prediction study is not yet authorized by this incomplete preregistration.

No strategy return was computed and no promotion is granted. This design does
not change a collector, subscribe to another feed, alter storage, install a
service, reserve an OOS window, or enable PAPER/TESTNET/SHADOW/LIVE execution.
