# Market Data Plan

## Policy

Phase 1 is **free-data-first**. We do not buy data simply because professional firms use expensive feeds. Paid data is introduced only when free data creates a measurable research or execution bottleneck.

The platform follows a separate venue rule:

> **Observe many venues; trade on few venues.**

Public market-data adapters can be added without granting live order permissions. See [Venue Strategy](VENUES.md).

## Development and runtime data split

### Windows/WSL2 development

Use:

- synthetic fixtures;
- small captured public-data samples;
- deterministic replay files;
- disposable local ClickHouse containers;
- bounded exports from TrueNAS for specific research questions.

The local environment is optimized for fast, reproducible tests. It is not the authoritative 24/7 dataset and should not need production credentials.

### TrueNAS runtime

TrueNAS is the system of record for:

- continuous public-market ingestion;
- raw/normalized market history;
- paper/shadow/live signals and decisions;
- fills and execution simulation;
- portfolio/equity history;
- operational data-quality records.

Windows development must not directly mutate the TrueNAS ClickHouse database. Read-only remote analysis may be permitted, while large experiments run through a controlled research worker or bounded export.

### Fixture policy

Commit only small sanitized fixtures needed for deterministic tests. Do not commit:

- large raw captures;
- account/private order data;
- exchange credentials;
- personal balance history;
- proprietary experiment outputs that belong in the experiment store;
- data whose license forbids redistribution.

Each fixture records its source or schema basis, schema version and any transformations. A captured
fixture also records its capture window; a synthetic fixture states explicitly that no capture
window exists and must not be described as captured production data.

## Initial free sources

### Hyperliquid

Collect and normalize available public data such as:

- trades;
- candles;
- L2 order book;
- best bid/offer;
- mark/oracle prices;
- funding;
- open interest / asset context;
- account/order/fill streams for our own accounts only in later authorized environments.

Use official historical datasets where practical, while explicitly validating gaps, timestamp semantics and schema changes.

Primary research uses:

- perpetual momentum;
- basis/carry;
- funding and OI features;
- cross-exchange lead/lag;
- execution and microstructure.

### Bitvavo

Use Bitvavo Standard as a free public data source for EUR and available USDC spot markets:

- trades and candles;
- best bid/offer;
- standard L2 order book;
- market metadata, precision and order capabilities;
- EUR/USDC and asset quote-route comparison.

Bitvavo Market Data Pro is a distinct, committed future authenticated read-only comparison feed,
not part of the free public Standard feed. Its future access and simultaneous Standard/Pro
collection requirements are defined under feed-product identity below.

Primary research uses:

- spot momentum;
- EUR versus USDC liquidity and all-in cost;
- Bitvavo versus Hyperliquid/Kraken/Binance lead/lag;
- spot-led versus perpetual-led moves;
- future small-live spot execution modeling.

Not every asset has a USDC market. Supported markets and precision must be discovered dynamically through public metadata.

### Kraken

Use Kraken's public market data and official paper tooling for:

- spot and derivatives reference prices;
- order books, recent trades, OHLC and spreads;
- paper spot and futures execution against live prices;
- agent-assisted research through the local MCP interface.

During the initial phase, Kraken MCP is restricted to safe services such as market and paper/futures-paper. Live trade, funding and transfer capabilities are not enabled.

Primary research uses:

- independent reference pricing;
- paper execution experiments;
- cross-venue liquidity and basis comparison;
- possible future hedge/backup venue evaluation.

### Binance

Use public historical/realtime data as an external reference for:

- spot prices;
- perpetual prices;
- trades;
- candles;
- funding;
- cross-exchange basis;
- lead/lag research.

Binance is initially a data venue, not an execution venue.

#### Phase 1A-3A Binance Spot raw-trade boundary

The Phase 1A-3A adapter accepts one already JSON-decoded Binance Spot raw `@trade` event per
invocation. It does not accept `@aggTrade`, combined-stream wrappers, futures events or any
transport message. All nine documented fields (`e`, `E`, `s`, `t`, `p`, `q`, `T`, `m` and `M`)
are required; unknown additive fields are tolerated.

The caller explicitly supplies either `MILLISECONDS` or `MICROSECONDS` for both timestamps. The
adapter never infers a unit from integer magnitude and uses exact integer arithmetic:

- `T` is the trade execution time and becomes the schema-v2 envelope `event_time`;
- `E` is the exchange event time and remains available, with the raw `T` and selected
  unit, on the immutable Binance source DTO;
- `t` is retained as the trade ID and in the deterministic source identity, not presented as a
  contiguous gap or replay sequence;
- `m=true` means the buyer was maker and therefore maps to sell aggressor; `m=false` maps to buy;
- `M` is retained on the source DTO without assigning normalized meaning to it.

The deterministic source ID is the compact JSON array
`["binance-spot-trade-v1", symbol, trade_id]`. Exact, case-sensitive symbol resolution uses only an
explicit injected Binance Spot instrument registry; the adapter never derives assets, quote,
instrument type or canonical identity from the symbol. Schema version 2 is unchanged. No Binance
collector, account integration, storage path, dashboard or execution capability is introduced by
this offline boundary.

### Other free venues

Bybit, OKX, Coinbase or Deribit may be added where a specific hypothesis requires them. Avoid collecting everything simply because it exists.

## Feed-product identity and access tiers

A venue and instrument do not uniquely identify a market-data feed product. A future generic feed
contract must distinguish public, authenticated read-only, account- or tier-gated, institutional
and node-provided products, including their L2, L3, order-level/L4, trade, BBO, snapshot, delta,
funding, open-interest, liquidation, option, implied-volatility and Greek capabilities.

Bitvavo Standard and Market Data Pro are distinct feeds. Market Data Pro is a committed future
authenticated read-only comparison feed. Any future Pro key is dedicated to data only, receives no
Trade or Withdraw permission, is IP-allowlisted where possible, is never reused as an execution
key, and may never silently fall back to Standard while claiming Pro identity. Standard and Pro
must later support simultaneous A/B collection. This phase creates no Bitvavo key or integration.

Potential future gated candidates include Kraken L3; Coinbase Exchange full/L3/direct; Deribit raw;
OKX higher-tier 10-ms/SBE feeds; Bybit institutional feeds; and Hyperliquid node/L4 data. Listing
them here neither implements them nor authorizes credentials or expansion of the current `Venue`
enum.

The dormant Phase 1A-3B1A contracts now define feed identity and capabilities, access requirements
and entitlement classes, feed/run/session/subscription identities, immutable raw-record values,
coverage primitives, versioned event-family bindings and Bronze/Silver/Gold boundaries. The two
initial feed-product catalogue identities are exact versioned products for Hyperliquid production
mainnet public WebSocket market data and Binance production mainnet Spot JSON market streams.
Capabilities are sorted, bounded, append-only point-in-time observations; they describe a product
but never grant access or record credentials.

Subscription identities admit only closed public-semantic fields for their exact feed binding.
The current Hyperliquid public-trades spec is exactly one `subscribe`/`trades` request with an exact
coin; Binance Spot uses the exact public `SUBSCRIBE`/`@trade` shape. API keys, tokens, signatures,
account or credential state, authorization state, secret endpoints and runtime timeout/retry
controls are not valid identity fields. Full validated plan content is independently inspectable,
while the stable `SubscriptionPlanId` is a bounded versioned SHA-256 content address. The current
Hyperliquid trade binding requires an acknowledged attempt before event materialization.

Point-in-time metadata selection first filters to observations visible at raw receipt for one
requested canonical instrument and authority. Future observations and other authorities do not
affect that historical slice; explicit full-catalogue validation is separate. Selection is the
only supported path to `ResolvedInstrumentMetadata`. Canonical instrument IDs are structurally
validated against `Instrument`, not truncated by a generic metadata-text limit. When both
derivative boundaries exist, settlement must equal or follow last trading.

Trade-family-v2 provenance requires exactly one `TRADE_EXECUTION_TIME` whose UTC instant equals
the selected metadata and envelope event time; exchange event time remains a separate optional
source fact. Coverage scopes bind the family schema version, and epochs bind scope, collector run,
ordinal and explicit UTC/monotonic activation boundaries. Initial completeness begins only at
typed activation evidence. Definite rejection of a successfully received raw record makes Bronze
ingress confirmed incomplete; ambiguous sink acceptance remains uncertain. ACK, reconnect or a
state snapshot cannot repair historical trade coverage, and 3B1A defines no recovery proof. Event
materialization additionally requires both embedded coverage epochs to belong to the raw record's
collector run.

These are definitions, not runtime claims. Indexed normalization outcomes require their frame
evidence to equal the exact canonical union of index evidence. Attached normalization-failure or
source-conflict transitions must name the same raw record, exact affected index and exact Silver
scope. A normalization failure carries its source-event ID when decoding established one and
otherwise carries null; that nullable value must exactly equal the rejected index outcome's
logical source identity. Source conflicts always require a non-null exact source-event ID. The v3
envelope contracts remain dormant; no runtime producer constructs or emits
`MarketEventEnvelopeV3`, and v2 remains the only active Silver envelope. Phase 1A-3B1B activates
`RawMarketDataRecord` and
`NormalizationOutcome` for Hyperliquid public trades: mandatory bounded sinks accept the raw
application-message record before parsing and the one frame outcome before any runtime commit.
Acceptance means ownership at the storage-neutral sink boundary, not durable persistence. No
operational coverage tracker, delivery outcome or deterministic replay exists yet. The producer
and collector cutover occurs atomically only in Phase 1A-3B1D. ClickHouse storage,
Grafana/Alloy/OpenTelemetry observability, FastAPI services and the Bloomberg/EMS-inspired cockpit
remain downstream. Nothing was deployed, and SHADOW/LIVE remain disabled.

## Self-collected dataset

Realtime collectors should run on TrueNAS from the beginning of Phase 1B and persist data to ClickHouse. This creates a dataset with the same receipt path and timestamp discipline the future live bot will use.

Recommended normalized schema per instrument/time bucket includes:

```text
timestamp
receive_timestamp
venue
canonical_instrument_id
venue_market_id
native_symbol
instrument_type
contract_expiry
base_asset
quote_asset
listing_status
bid
ask
mid
mark
oracle
spread_bps
volume
funding
open_interest_coin
open_interest_usd
impact_bid
impact_ask
depth_1bps
depth_5bps
depth_10bps
book_imbalance
signed_flow_10s
signed_flow_1m
signed_flow_5m
returns_1m
returns_15m
returns_1h
returns_4h
returns_24h
sequence_start
sequence_end
is_gap
schema_version
collector_version
```

`venue_market_id` identifies one concrete market within its venue and instrument type.
`contract_expiry` is point-in-time metadata for futures and is null for spot and perpetual instruments.

Cross-exchange and route features may add:

```text
hl_vs_binance_spot_basis_bps
hl_vs_kraken_basis_bps
bitvavo_eur_vs_usdc_route_cost_bps
lead_lag_features
funding_differential
volume_share
venue_price_dislocation_bps
```

## Ingestion architecture

Collectors publish canonical events through bounded queues with explicit backpressure behavior. The first local slice may use in-process queues; Redis is introduced only when service separation requires a shared event layer.

Every event includes at minimum:

- venue and canonical instrument;
- source timestamp;
- monotonic local receive timestamp;
- a stable source-event identifier;
- source transaction, sequence and correlation identifiers where their distinct semantics apply;
- schema version;
- collector build/commit metadata;
- gap/quality status.

### Market-event schema v2

Market-event schema version 2 makes `aggressor_side` (`buy` or `sell`) mandatory on every
`TradeEvent`, alongside its exact decimal `price` and `quantity`. This is a breaking change from
schema version 1; v1 payloads must not be presented as v2 without an explicit migration.

The envelope keeps different provenance concepts separate:

- `source_event_id` is mandatory and identifies the source event deterministically;
- `source_transaction_id` is optional transaction provenance;
- `source_sequence` is reserved for an ordered source sequence;
- `correlation_id` is reserved for cross-operation correlation, not source-event identity.

For Hyperliquid public trades, `source_event_id` is the compact JSON array
`["hyperliquid-trade-v1", time_ms, coin, tid]`. The complete `coin` string is preserved, including a
HIP-3 `{dex}:{coin}` namespace. The transaction `hash` is retained as `source_transaction_id`.
Hyperliquid documents `tid` as a 50-bit hash rather than an ordered sequence, so
`source_sequence` remains null. The source `users` array is retained in buyer/seller order by the
adapter DTO but is not yet copied into the venue-neutral trade contract.

### Dormant provenance-complete envelope v3

Phase 1A-3B1A defines a mandatory future outer envelope version 3 while leaving every active v2
producer unchanged. The trade event family remains independently versioned as family version 2,
with the current immutable `TradeEvent` payload. A later breaking trade-payload change requires a
new family version and binding; it does not silently change trade-family-v2 semantics.

Source and observation facts are separate. `SourceProvenance` contains the stable source-event ID,
optional source-transaction ID, exact typed source-time facts and typed source-sequence ranges.
`ObservationProvenance` contains feed product, collector run, connection session, subscription
plan/spec/attempt, raw record/index, receive wall and monotonic clocks, collector build,
normalization run and normalizer build. Existing Hyperliquid and Binance source-event strings do
not change. The logical source key is `(feed_product_id, source_event_id)`; the observation key is
`(raw_record_id, raw_event_index)`; the materialization key adds normalization run, event family,
family version and payload type.

For a successfully returned `websockets` 17 application message, Hyperliquid Bronze TEXT bytes are
exactly `message.encode("utf-8")`; BINARY bytes are unchanged. This is the post-extension,
reassembled application-message boundary, not control/close frames, fragment or compression wire
bytes, TCP/TLS bytes, or invalid UTF-8 rejected before `recv()` returns. Decode failure does not
prevent a raw value from being represented. Derived raw length, payload SHA-256, locator ID and
full-record digest are recomputed by stored-record verification. Contiguous supplied ordinals
cannot detect a missing tail; sealed run manifests belong to deterministic replay in 3B2.

Coverage has independent Bronze-ingress, Silver-normalization and Silver-delivery domains. A v3
event carries exactly ingress and normalization references known when materialized. Delivery is a
separate immutable outcome: Silver delivery means bounded collector-output-queue acceptance, not
downstream consumption or durable persistence. ACK or reconnect alone never repairs degraded
coverage. A definitively rejected successfully received raw record establishes confirmed Bronze
incompleteness; a positively identified in-scope market message that cannot normalize establishes
confirmed Silver-normalization incompleteness. Unclassified terminal or sink-acceptance ambiguity
remains uncertain. Source-event conflict remains distinct integrity evidence.
Source-event conflict in Silver normalization is positively identified integrity loss and is
therefore `CONFIRMED_INCOMPLETE`, both at initial activation and on a later transition; it is not
an ambiguity state and cannot be cleared by ACK, reconnect or unrelated evidence.

Point-in-time derivative metadata separates content-addressed instrument specifications from
append-only authority observations. Selection is authority-explicit and requires both
`observed_at <= raw received_time` and validity at event time, preventing look-ahead. Quantity
unit, positive exact contract multiplier, linear/inverse form, settlement asset, expiry and
explicit last-trading/settlement timestamps are retained without parsing symbol text. Leverage is
account/position context and is not a public trade-event field.

These v3 envelope contracts are defined and tested but dormant. No runtime producer constructs or
emits `MarketEventEnvelopeV3`; v2 remains the only active Silver envelope. Hyperliquid now performs
storage-neutral raw
and normalization-outcome acceptance, but there is no concrete persistence, operational coverage
tracker, delivery outcome or deterministic replay. The atomic producer and collector cutover
happens only in 3B1D. Nothing was deployed, and SHADOW/LIVE remain disabled.

Subscription state changes retain the exact immutable transition produced by the pure reducer in
an append-only current-session journal. Its finite limit is three state-changing transitions per
wire spec; duplicate acknowledgements do not append, and each reconnect starts a new session
journal. Bronze ACK records remain immutable pre-parse snapshots and are never rewritten.

Index-specific normalization outcomes derive a closed raw/spec/attempt/public-selector/
canonical-instrument/Silver-scope binding from the supplied immutable raw record and decoded-index
context. Attached coverage transitions are limited to exact in-scope normalization failures or
source-event conflicts from that same raw record, normalization run and event index. A pre-index
parse failure uses a separately factory-validated raw-frame scope containing every plan spec with
the matching event-family/version/payload binding and every instrument bound to those specs.
Without typed route evidence it cannot select a narrower arbitrary plan subset. Transport,
reconnect, raw-sink, delivery and unrelated historical transitions cannot be attached to a frame
outcome. Pre-index frame evidence is a positive closed allowlist containing only protocol or
decoder rejection, unknown instrument, unavailable metadata, provenance mismatch and local
contract failure. Source-event conflict and frame-atomic abort require indexed outcomes and are
invalid before indexing; future evidence categories remain rejected until explicitly classified.

Ordinary validation exceptions and their traceback frames remain private implementation details:
traceback locals can retain rejected bytes or text even when bounded exception arguments,
`__cause__` and `__context__` do not. Phase 1A-3B1B must catch and classify such failures inside a
private boundary and export only a frozen category-only `SanitizedValidationFailure`. Public
producer and consumer operations use standalone module-level coroutine boundaries: after the
private operation finishes, they discard its exception, traceback and coroutine reference before
creating a fresh bounded public error. No library frame on that exported error graph retains the
collector, connection, sinks, queue, raw values, source values or destination values. Internal
exceptions and `exc_info` may never be logged, stored or otherwise exported.

When pressure exceeds capacity, the system must not silently accumulate unbounded memory. It applies an explicit per-stream policy: backpressure, reconnect/replay, sampling for non-critical telemetry, or fail/stale state. Trading-relevant feeds may not silently drop without marking the data invalid.

### Phase 1A Hyperliquid trades collector policy

Every successful `recv()` return first captures its one UTC/monotonic receive-time pair, exact
frame kind and exact post-extension, reassembled application-message bytes. The run-wide ingress
ordinal is never reused and continues across connection sessions. The complete current-session
attempt table is snapshotted before parsing. A mandatory raw sink must accept and echo the exact
raw locator ID, full-record integrity digest and declared destination identity within its
one-second default bound before any router or decoder runs. Malformed JSON, duplicate keys,
non-finite JSON, unknown channels, greeting,
acknowledgement, pong, empty trade arrays, binary messages, duplicate trades and source conflicts
all cross this same raw-first boundary. Failures before a successful application-message return do
not fabricate a Bronze record.

After pure routing and candidate calculation, a distinct mandatory outcome sink must accept and
echo exactly one immutable frame outcome within its one-second default bound. Verified raw
acceptance increments only `accepted_raw_record_count`; verified outcome acceptance then increments
only `accepted_normalization_outcome_count`, and the latter never exceeds the former. Before
outcome acceptance, acknowledgement, pong, deduplication, delivery, received-message counters and
the schema-v2 queue remain unchanged. Raw or outcome rejection is terminal; a timeout or arbitrary
sink exception is acceptance-ambiguous, terminal and never retried. The collector owns both sinks
for one run, bounds their closes by their respective acceptance timeout, and attempts outcome close
before raw close at most once each. Successful sink acceptance means ownership of the complete
value, not persistence.

In Phase 1A-3B1B summary wording, an unqualified reference to counters at the outcome commit
boundary means ordinary control, trade, pong, acknowledgement, deduplication and delivery counters;
it does not include the two explicitly named sink-acceptance telemetry counters above.

Sink acceptance and close use a collector-level deadline race rather than waiting for child
cancellation to complete. Once the deadline wins, no late success is accepted and no processing or
runtime commit follows; the child is cancelled and any eventual result or exception is privately
consumed. This is a scheduling and fail-stop guarantee for the collector, not forced termination
of arbitrary in-process Python. Conforming sinks must be cancellation-cooperative. A sink that
deliberately suppresses cancellation may outlive the collector decision and require process
teardown; future sinks needing an absolute kill boundary must run in an isolated worker or process.

The local public-trades collector puts each fully validated WebSocket frame into the in-process
queue as one immutable tuple of schema-v2 envelopes. Queue capacity counts frame batches, is finite
and positive, and a bounded `put` wait provides backpressure. If capacity remains unavailable, no
part of that frame is published, the collector fails closed, and sticky gap state is set. Empty
trade arrays are valid no-ops.

Receipt wall time and process-local monotonic nanoseconds are captured once immediately after each
WebSocket receive. Every trade in that frame receives the same pair. All trade objects are decoded,
resolved by exact configured and acknowledged `native_symbol`, and normalized before any queue
publication. Mixed-coin frames are allowed when every coin independently passes those checks.

A bounded process-local LRU uses the existing deterministic `source_event_id` to suppress overlap
within one frame, across frames and across reconnects while preserving the wire order of new
events. Each ID maps to a bounded SHA-256 digest of an unambiguous semantic preimage covering coin,
side, normalized price and size, event time, trade ID, transaction hash, ordered users and canonical
instrument identity; the cache retains neither users nor the transaction hash themselves. A
matching digest is an ordinary replay and refreshes LRU recency; reuse of the same ID with different
semantics is a terminal source conflict. Frame-local fingerprints survive candidate LRU eviction,
so a later conflicting reuse in that same frame cannot be mistaken for a new event. Conflict
detection completes before any queue,
cache, emitted-count or duplicate-count mutation for that frame. Receipt clocks, collector version,
collector commit and gap state are deliberately excluded from replay identity. The cache is not
persisted and cannot guarantee exactly-once delivery after process restart or after an ID is
evicted.

This phase supports one logical batch consumer. A separate terminal event wakes a waiting consumer
without consuming bounded queue capacity. Already queued batches are returned first; once the queue
is empty, producer failure or cancellation raises a sanitized collector-termination result. Health
retains only a bounded failure category, never the exception, raw frame, users, transaction hash or
WebSocket close reason. Dependency-level WebSocket frame logging is routed to an isolated disabled
logger and remains suppressed even when application-wide or root DEBUG logging is enabled.

The initial session emits `is_gap=false` only while coverage remains certain. Immediately before a
subscription send is attempted, delivery becomes potentially ambiguous; any subsequent retryable
disconnect or timeout makes `is_gap=true` sticky even when no acknowledgement was observed.
Handshake failure before any send attempt does not create a gap. Later acknowledgements do not prove
replay completeness and cannot clear the flag. No `recentTrades` backfill, storage, queue replay or
gap repair exists in this phase.

Normalization-outcome acceptance is the current normalization commit boundary, not the delivery
boundary. A subsequent schema-v2 queue timeout can therefore leave an accepted outcome without a
successful delivery record. Phase 1A-3B1C will add separate `DeliveryOutcome` and operational
coverage transitions; Phase 1A-3B1B deliberately creates neither.

## ClickHouse environments

### Local DEV

A disposable ClickHouse container supports:

- schema/migration tests;
- sample ingestion;
- deterministic replay;
- API/Grafana/frontend integration tests.

It may be destroyed and recreated.

### TrueNAS PAPER/SHADOW/LIVE

Use a managed ClickHouse instance with:

- separate Hyperliquid database/schema namespace;
- explicit ingestion writer;
- Grafana read-only user;
- Hermes/research read-only user;
- controlled migration identity;
- resource quotas;
- snapshots/backups;
- retention/tier policies;
- safe network binding.

Do not initialize a second instance or overwrite the existing 90+ GB data layout until the existing ClickHouse lifecycle and datasets are fully understood.

## Quote currencies and conversion data

EUR, USDC and USD-equivalent values remain distinct in raw data. Conversion into a reporting currency happens through point-in-time FX/stablecoin rates, not by assuming EUR, USD and USDC are equal.

Persist:

- executable `USDC-EUR` bid/ask and depth;
- EUR/USD or equivalent reference rate where needed;
- conversion fee schedule effective at that time;
- quote-asset inventory;
- conversion-order fills and slippage;
- USDC deviation from its reference value;
- route cost estimates and selected route.

The research simulator must compare:

```text
ASSET-EUR direct
EUR -> USDC -> ASSET-USDC
existing USDC -> ASSET-USDC
```

## Data quality controls

Every ingestion path exposes:

- last exchange event timestamp;
- local receive timestamp;
- sequence/gap detection where supported;
- duplicate count;
- reconnect count;
- stale state;
- null rate;
- schema/version metadata;
- venue API status/health;
- symbol-mapping status;
- queue depth/backpressure/drop metrics;
- deployed image digest and collector commit.

Future Grafana/Alloy/OpenTelemetry integration must make data quality observable, and deterministic
stale/gap controls may then block trading. Those integrations are not implemented by the offline
Phase 1A-3A boundary.

## Point-in-time metadata

Maintain historical metadata for:

- listing/delisting;
- venue, `venue_market_id` and native symbol;
- base/quote currency;
- instrument type;
- `contract_expiry` for futures;
- tick size;
- size precision;
- minimum order size;
- supported order types;
- fee schedule where relevant;
- symbol mapping;
- contract specification;
- margin/funding conventions.

Do not backfill current market metadata into historical periods without evidence it was valid then.

## Storage tiers

Use SSD-backed TrueNAS storage for:

- active ClickHouse partitions;
- recent raw feed data;
- features;
- paper/live execution data;
- Redis/PostgreSQL when introduced.

Move large cold archives and old raw market data to the HDD pool according to retention policy.

The Windows workstation stores only source, build caches and bounded development datasets. It is not a backup of the TrueNAS data lake.

## Paid data decision rule

A future paid provider must solve an explicit problem, for example:

- insufficient historical L2 depth;
- inability to reconstruct realistic fills;
- missing cross-exchange derivatives history;
- missing options/on-chain feature history.

The paid feature set is evaluated against a no-paid-data baseline. A subscription is justified only if it materially improves out-of-sample return, risk, execution quality or research speed relative to its cost.
