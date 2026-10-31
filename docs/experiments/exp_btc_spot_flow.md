# BTC spot-flow-confirmed Hyperliquid continuation, v1

**Preregistration for independent review, before any real price/flow analysis.**
A merge permits the recorded research recipe; it submits no orders, starts no
capture and authorizes no deployment. PAPER only. This is a new mechanism and
input set, not a rerun of the stopped BTC time-series-momentum/funding families,
not an H1 scaffold upgrade, and not a smaller rerun of S4.

## Hypothesis and fixed family

Binance BTCUSDT **Spot** aggressive notional imbalance confirms the direction
of the immediately preceding Hyperliquid BTC-PERP minute and predicts an
executable continuation over the following 1 or 5 minutes. H0 is nonpositive
mean return after both fills' modeled costs. H1 is positive mean return for a
validation-selected configuration on fresh prospective OOS under all declared
cost stresses, **conditional on the quote-fill and funding API-clock models**.
This tests those models, not actual funding-free execution or deployable
profitability. All result labels identify the conditional model and all
promotion fields remain forbidden, including prospective nested decisions.
The signal uses no USD-M mark/aggTrade fallback.

At each UTC minute boundary `t`, use only observations received by `t`:

1. Sum `price * quantity` over Spot trades received in `[t-60s,t)`.
   `buyer_was_maker=false` is buyer aggression; true is seller aggression.
   Imbalance is `(buy_notional-sell_notional)/(buy_notional+sell_notional)`.
   A missing minute is unavailable, never zero actual market flow. Duplicate
   trade IDs fail closed, rather than counting reconnect replay twice.
2. Obtain HL BBO mid as of `t` and `t-60s`, each no older than two receipt
   seconds and in the same session. Require a nonzero mid change whose sign
   matches imbalance. No future quote is substituted at the decision.
3. The signed signal is imbalance when confirmed, otherwise no order.
   The complete four-config grid is `|imbalance| >= {0.2,0.4}` crossed with
   holding `{1,5}` minutes. No magnitude fit, horizon search or ML is performed.

The canonical statistical spec is [exp_btc_spot_flow.spec.yaml](exp_btc_spot_flow.spec.yaml).
The runner reuses the existing harness folds, selection, multiplicity correction,
HAC/iid conservative p-values, holdout rules and cost-stress machinery.
`data.view=btc_flow_minute` describes the custom source's logical decision axis;
it is not external catalog SQL. Use `research.btc_flow_cli`, not the generic
close-price runner. A small explicit harness extension preserves a source's
complete observed execution-cost vector across folds; absent vectors retain the
original flat-bps behavior. Mixing the two models across folds is refused.

## Input identity, source clocks and gaps

Only a separately approved, hash-bound input manifest selects original closed
raw Parquet parts. The runner executes no archived catalog/database SQL and
writes no source/NAS data. It reads original inbound HL `bbo` and Binance Spot
`spot_trade` plus local quality markers; normalized local duplicate frames are
not additional economic events. Each selected raw payload hash is checked.
The existing Spot decoder requires its documented original trade fields.

Event time is used **only for freshness**. Binance `T` is explicitly
MICROSECONDS, HL `bbo.data.time` explicitly MILLISECONDS. Either may lag local
receipt by at most 5 seconds or lead it by at most 1 second; an unknown/stale
clock fails the whole selected study. Snapshot-flagged economic frames are
refused. A BBO is full bid/ask state, never a trade-flow observation. Every raw
frame becomes strategy-available at receipt, not at its earlier venue time.
Receipt UTC/monotonic regression is checked per inbound session/channel, not
across parallel WebSocket profiles or local normalization markers.

Known predecision gaps, mixed Spot sessions, absent flow or stale decision quotes
prevent entry. A gap/session change or missing/late quote affecting a possible
entry/held exit invalidates the entire affected study; no losing position is
silently erased or assigned zero PnL. The first unknown outcome is reported as
at least one; this is not an exhaustive gap ledger. Source hashes are rechecked
after evaluation. The simple fill model does not reconstruct an unobserved book.

## Execution and economics

There is one trading leg, HL BTC-PERP; Binance only supplies a dimensionless
signal. The two execution sides are entry and exit, not a spot/perp hedge.
No USDT/USD/USDC price-level parity or executable cross-venue basis is assumed.
Research accounting is in USDC, with a nominal $100 decision-sized position.
This is a unit research assumption, not live leverage or account authority.

Entry is the first received HL quote at or after `decision + 1 minute + 250ms`,
with at most 5 seconds additional wait. The minute delay preserves the existing
`latency_bars=1` contract; 250ms is an additional frozen physical-delay assumption.
Exit is the first quote at or after `actual_entry + horizon + 250ms`, again within
5 seconds. Decisions do not overlap positions. Boundary censoring uses worst-case
clocks before entry; no fill extends outside its validation/OOS window.

Longs buy ask and sell bid; shorts sell bid and buy ask. Entry quantity is sized
from the known decision mid, rounded down and capped by displayed touch size;
sub-$10 entry capacity is a known non-fill. An undersized exit is unknown and
fails closed. No maker touch/queue fill or unlimited depth is assumed.
Existing PAPER adverse tick rounding is reused. Lot `0.00001 BTC`, at most one
price decimal and five significant figures, $10 minimum and $100 nominal sizing
are explicitly **frozen research assumptions**, not certified historical metadata.
Future production use needs point-in-time instrument/risk validation.

Each fill pays 4.5 bps taker fee on its actual fill notional and receives 1 bp
adverse slippage before adverse grid rounding. Observed spread is already paid
through bid/ask, so extra flat `spread_bps=0` avoids double counting. No staking,
referral, volume-tier discount, maker rebate or builder fee is assumed. Tier-0
fee reference: [Hyperliquid fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)
(checked before preregistration on 2026-10-31). Base net is exact simulated cash
price difference minus both fees, divided by $100. Stress multiplies the entire
observed base execution-cost amount by 1.0/1.5/2.0; it does not claim a replay of
new quotes at a different latency.

The ±60-second UTC-hour guard is only a conservative engineering entry filter;
it does **not** prove that actual funding payments occur inside that guard.
Its earliest-entry/latest-exit budgets, including both latency/wait intervals,
remain unchanged. Official hourly/end-of-interval documentation does not provide
a hard UTC phase or maximum payment delay. Neither an API record timestamp nor
historical API timestamp jitter proves an onchain account payment time.

The separate `hl-fundingHistory-api-clock-v1` model requires hash-bound original
native BTC `fundingHistory` JSON snapshots and independently accepted provenance
before any financial frame is read. There must be exactly one record in every
native hourly slot covering the selected 72 hours plus one hour on each side,
without missing/duplicate slots or known-hole waivers. Existing `slot_of` is
used only to qualify that coverage. Exposure uses the **exact returned `time`
in milliseconds**, never the mapped hour. An API event inside a closed actual
quote-entry/exit holding interval invalidates the **whole study window** for
any of the four configurations or the momentum benchmark; it never removes a
trade, skips a configuration or becomes a fabricated cashflow. Nanosecond entry
is rounded down and exit up to milliseconds for conservative boundary checks.
Missing or uncertain exits already invalidate the window independently.

Within this explicitly conditional model, complete event coverage and zero
position/event intersections permit zero modeled funding cashflow without
interpreting funding rates or inventing oracle prices. This is analogous to
the declared quote-fill assumptions: it is **not** proof of zero actual onchain
funding, whose settlement phase is unverified. Realized funding/oracle inputs
and a new reviewed contract are needed to make that stronger claim or permit
event-crossing positions. No funding cap or rate retuning is introduced. Missing
or invalid timing qualification records metadata-only `funding_unqualified`,
with no financial decode, scoring or override flag. S4's unrelated native
funding failures are neither a dependency nor waived.

## Chronological periods and immutable OOS

Historical September tapes already have prior research artifacts. Every
historical run is **exploratory only**, regardless of its statistical outcome;
its reported label is prefixed `exploratory_` and promotion remains forbidden.
They cannot establish a fresh holdout or be relabeled prospective.

The confirmatory capture window starts at the first UTC midnight **at least
24 hours after this preregistration's actual merge** and lasts exactly 72 hours.
The approved input manifest records that merge SHA/time, exact window and closed
part paths/hashes. The CLI requires a clean committed source on `origin/main`,
identical committed spec and unchanged research/decoder/precision code since the
prospective preregistration commit. Merge-time metadata must be independently
checked when approving the manifest; it is not a self-authenticating signature.
No real capture or extraction is started by this PR.

The first 12 hours are training/history (no parameter fitting). The next 36 hours
are three expanding 12-hour validation folds. The final 24 hours are reserved
OOS. The reader initially decodes and aggregates only the first 48 hours. The
existing harness requests the OOS trade window only after validation selects a
configuration; that request first opens the final 24 hours plus one previously
decoded minute for fixed feature warm-up. Validation benchmarks use the same
staged source, and their OOS benchmark is also selection-gated. Stale clocks,
malformed payloads and quality markers in sealed OOS cannot affect validation
through eager financial decoding. Opaque complete-file hashes may be verified
before selection. At least three folds, 30 validation fills and 30 OOS fills are
required. Shorter input spans produce `not_enough_data` before financial-frame
reads; no shorter split, relaxed sample floor or selected good-days subset is
substituted. If the prospective window is insufficient, record that verdict;
any further experiment requires a new preregistration and fresh OOS.

Benchmarks are flat cash (zero trading return) and HL minute continuation without
Spot-flow confirmation, under identical execution, funding/calendar and cost
rules. The latter is diagnostic, uses the primary/selected horizon, and never
selects a configuration. Its OOS is computed only after the primary validation
selection. H1 tests positive net, **not** statistical superiority over that
benchmark. Robustness covers all three cost stresses and the predeclared nearby
threshold/horizon grid; no extra configurations are chosen after results.
A 72-hour pilot proves neither multi-regime stability nor deployable profitability.

## Explicit command and artifacts

After review/merge and separate data qualification, use a new local output root:

```bash
PYTHONPATH=src python -B -X pycache_prefix=/path/to/fresh-private-pycache -m research.btc_flow_cli \
  --spec docs/experiments/exp_btc_spot_flow.spec.yaml \
  --inputs /path/to/approved-inputs.json --inputs-sha256 <approved-manifest-sha256> \
  --source-commit <reviewed-main-sha> --output /path/to/new-private-study
```

The input JSON has exactly `format:2`, `phase` (`exploratory`/`prospective`),
`start_ns`, `end_ns`, `preregistration_commit`, `preregistration_merged_ns`, and
`parts:[{path:<absolute-part>,sha256:<opaque-file-hash>},...]`, plus
`funding:null` or `funding:{path:<absolute-proof-json>,sha256:<proof-hash>}`.
Exploratory merge
fields are null. Qualifying metadata/claims/session records must be accepted
before generating that manifest; hashing alone is not evidence of data quality.

The funding proof has exactly `format:1`, `model:hl-fundingHistory-api-clock-v1`,
`endpoint:https://api.hyperliquid.xyz/info`, `coin:BTC`, `snapshot_cutoff_ns`,
`frozen_utc_ns`, `known_holes:[]` and `sources:[{path,sha256},...]` (one or two
native monthly JSONs). Freeze it after the fixed window plus right padding,
before outcomes are computed; record the acquisition cutoff and never resync
because of a result. A native snapshot retains the existing `source`, `coin`,
`start_ms`, `end_ms`, `rows:[{coin,fundingRate,premium,time},...]` format. The
reader checks times, coin, structural types and complete unique slots only;
rate/premium strings are opaque unused fields, and no oracle is imputed.
Native snapshots are capped at 1 MiB/800 rows each, proof at 16 KiB, and all
paths are literal/no-follow. Complete funding timing metadata may be qualified
before validation, while OOS quotes/flow remain sealed. Cutoff/freeze assertions
are **not self-authenticating**: independent source/provenance acceptance must
precede approval of the input-manifest hash. Funding proof/raw hashes are
rechecked after scoring and included in the result and spec/input lock.

Outputs are create-only private local `result.json`, copied `spec.yaml` and its
spec/input hash lock. Failure/insufficiency records contain no invented PnL;
incomplete directories remain retained and cannot be resumed/overwritten.
Fixed engineering admission limits are 70,000 parts / 32 MiB manifest /
100 GiB source bytes, 512 MiB per part, two million HL quotes / 25 million Spot
trades **per staged load** (48h validation; 24h OOS plus one warm-up minute).
These limits accommodate native small-part capture layout; they change no
sample floor, economic parameter or quality rule. Bounds fail, never truncate.
DuckDB uses two threads / 2 GB, no spill or extension autoload/install. The CLI
caps total process address space at 8 GiB (or a stricter inherited limit); run
one heavy study at a time on the 12-vCPU/23-GiB VM. Source hashing is sequential,
financial parsing uses bounded batches, and OOS has no second eager full-72h
copy. Exceeding the cap is an incomplete fail-closed attempt, not permission to
retry with a changed cap or reuse the prospective OOS. No new CI jobs, runtime,
network dependency, credentials, order path or automatic promotion is introduced.

HL add-on captures may use the container product `BTC-PERP` for BTC, ETH and SOL
frames. The parser therefore checks **payload `data.coin`**, discards non-BTC
identities before decoding price/size fields, and fails on a missing/malformed
coin identity. Container/path labels alone never identify the economic asset.

Approved part paths are literal closed files: `*`, `?`, brackets and braces are
refused before hashing or DuckDB access. Relevant HL/Binance local quality
markers map the explicit gap/liveness/schema/truncation/sequence/snapshot error
set to conservative gaps. Unknown or malformed quality markers fail the entire
reader. Other venues' markers are outside this study, not silently certified.
