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
- bounded exports from an approved runtime store for specific research questions.

The local environment is optimized for fast, reproducible tests. It is not the authoritative 24/7 dataset and should not need production credentials.

### Durable runtime data

The selected durable runtime store is the system of record for:

- continuous public-market ingestion;
- raw/normalized market history;
- paper/shadow/live signals and decisions;
- fills and execution simulation;
- portfolio/equity history;
- operational data-quality records.

Windows development must not directly mutate a runtime database. Read-only remote analysis may be permitted, while large experiments run through a controlled research worker or bounded export. Existing TrueNAS/ClickHouse data remains protected until retention or migration is explicitly approved.

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

Bitvavo Market Data Pro is a distinct authenticated read-only comparison feed, not part of the
free public Standard feed. DATA-1E implements its bounded BTC-EUR Pro book adapter and smoke;
simultaneous Standard/Pro collection requirements remain defined under feed-product identity below.

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

Bitvavo Standard and Market Data Pro are distinct feeds. DATA-1E provides one bounded authenticated
Pro book adapter and smoke; it does not create a persistent account or credential integration. Any
Pro key is dedicated to data only, receives no Trade or Withdraw permission, is IP-allowlisted
where possible, is never reused as an execution key, and may never silently fall back to Standard
while claiming Pro identity. Standard and Pro must later support simultaneous A/B collection.

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
operational coverage tracker, delivery linearization/composite queue item or deterministic replay
exists yet. The producer
and collector cutover occurs atomically only in Phase 1A-3B1D. ClickHouse storage,
Grafana/Alloy/OpenTelemetry observability, FastAPI services and the Bloomberg/EMS-inspired cockpit
remain downstream. Nothing was deployed, and SHADOW/LIVE remain disabled.

## D10 — multi-venue market data and feed coverage: DATA-1A local slice

DATA-1A is a separate, bounded research path for the four public Hyperliquid BTC perpetual
subscriptions `trades`, `bbo`, default `l2Book` and `activeAssetCtx`. It deliberately does not
extend the dormant canonical/provenance contracts or the existing trades-only collector. It uses
no account, API key, wallet or signing capability and cannot submit orders.

Each inbound WebSocket application message is timestamped immediately when `recv()` returns. Its
text or binary application payload is copied to immutable bytes before JSON routing; the stored
BLOB is never a reserialized JSON document. For a text frame this means the exact UTF-8 bytes of
the string delivered by the WebSocket library, not TLS, TCP, compressed WebSocket or framing
bytes. Rows also contain schema version 1, venue, product, routed channel, connection-session ID,
a run-wide local ordinal, UTC and monotonic nanoseconds, direction, frame type, encoding and a
SHA-256 check value. Outbound subscription/ping payloads and local session, subscription,
disconnect, reconnect and gap markers use the same flat row shape but are explicitly labelled as
`outbound` or `local`; their timestamp columns are local observation times, not venue receipt
claims.

DuckDB is the only added dependency. It binds Python `bytes` directly to `BLOB`, writes native
ZSTD-Parquet and creates the research catalog, so PyArrow and a separate Zstandard package add no
necessary DATA-1A capability. The writer buffers a bounded segment and publishes each complete
part with a same-directory atomic rename. It never appends to Parquet and does not create a file
per message. A hard process or host crash can lose the active in-memory segment and leave a hidden
`.partial` file; previously published parts remain queryable and readers ignore partials. A
lossless WAL and 24/7 durability belong to a later runtime-storage decision, not this local slice.

The bounded command requires an explicit output directory, DuckDB path and duration from 1 through
600 seconds:

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.hyperliquid_raw_research \
  --output-dir /tmp/data-1a/raw \
  --database /tmp/data-1a/research.duckdb \
  --duration-seconds 60
```

The command prints counts and byte totals only; it never prints payload contents. The catalog has
`raw_records`, `trades`, `bbo`, `l2`, `derivative_context`, `sessions`, `subscription_events` and
`data_quality_events` views. Price, size, funding, open-interest, mark and oracle values remain
text in the views, including trailing zeros. Example research checks are:

```sql
SELECT channel, direction, count(*) FROM raw_records GROUP BY ALL ORDER BY ALL;
SELECT price, size, event_time_ms FROM trades ORDER BY received_monotonic_ns;
SELECT side, level_index, price, size FROM l2 ORDER BY message_ordinal, side, level_index;
SELECT event, reason FROM data_quality_events ORDER BY message_ordinal;
```

Hyperliquid supplies no sequence ID on these feeds. The standard `l2Book` stream is a sequence of
book snapshots, not L3/MBO and not a trade backfill. A disconnect therefore creates a conservative
gap marker; a later snapshot restores current L2 state but cannot reconstruct missed trades or
queue history. A short successful smoke is evidence only for this local route, not 24-hour feed
reliability or a trading edge.

The immediate next gate under D10 — multi-venue market data and feed coverage is DATA-1B — Kraken
authenticated L3 capture, because true order-level history cannot be reconstructed later. Any
future Kraken or Bitvavo credential must be a separate minimal read-only data key with no trading,
withdrawal or transfer authority and must never enter chat, source, fixtures, artifacts or logs.
Bitvavo internal personal research does not require a prior redistribution review. OKX is the next
data-only increment after DATA-1B.

### DATA-1B — Kraken BTC/EUR authenticated L3 research slice

Phase 1 of DATA-1B under D10 — multi-venue market data and feed coverage is deliberately offline.
It adds no credential loader or command and performs no authenticated smoke. The Kraken adapter is
fixed to Spot `BTC/EUR` and runs two required connections as one fail-closed capture:

- public `trade` and depth-10 `book` at `wss://ws.kraken.com/v2`;
- token-gated depth-10 `level3` at `wss://ws-l3.kraken.com/v2`.

The channel-specific L3 reference and Kraken's 2 December 2025 changelog are authoritative for the
second endpoint; the generic Spot WebSocket overview still lists `ws-auth` as the private endpoint.
No silent endpoint substitution or downgrade from L3 to public L2 is allowed.

The only permission needed to request the temporary token is `WebSocket interface - On`, named
`Access WebSockets API` in the current permission guide. Query Funds, order/trade queries, ledger,
export, order create/modify/cancel, deposit, withdrawal, transfer, Earn and account-management
permissions stay off. `API-Sign` is only the HMAC authentication header for
`GetWebSocketsToken`; it grants no order-signing or trading authority. The adapter form-encodes one
strictly increasing nonce and implements the official HMAC-SHA512 algorithm with Python's standard
library. A fresh token is obtained for every L3 connect or reconnect. The request, response and
token never enter the raw sink; the token-bearing subscription exists only in memory and is
represented on disk by a local marker containing channel, product and `authenticated=true`.

Only recognized inbound market-data frames are stored byte-exactly. Subscription acknowledgements
are reduced to non-secret local markers and heartbeats are validated then ignored in memory. Kraken
automatically sends `status` on every successful WebSocket connection; it is validated and reduced
to a local `venue_status` session marker rather than being classified as raw market data. Before
any inbound L3 bytes can be persisted, the active token is checked against the frame so an
unexpected token echo fails closed. Every new public or L3 connection receives a new session ID.
L3 reconnect also creates a new authentication boundary, discards old state and requires a fresh
snapshot. Transport disconnects create gap markers; invalid auth, failed reconnect,
oversize/truncated input, malformed schema, ordering failure or checksum mismatch stops the combined
capture. On a terminal failure, the peer stream exits through its stop boundary so an in-progress
atomic Parquet publication is not cancelled.

A bounded run is successful only after both public subscription acknowledgements, a valid L2
snapshot, the authenticated L3 acknowledgement and a valid L3 snapshot. Reaching the duration or
an external stop before those gates is a visible subscription/authentication or missing-snapshot
failure; it never becomes a public-L2-only success. Token request, send, echo and unexpected
authenticated transport failures are collapsed at the outer boundary to context-free errors so
credential-bearing lower frames and locals cannot reach logs or tracebacks.

The subscription acknowledgement's `depth` is checked against the request when Kraken includes it,
but its documented omission is accepted. A supplied wrong depth still fails closed. Each L3 order
timestamp remains mandatory; the message-level L3 timestamp is nullable because Kraken's current
official client records that field as absent in captured payloads.

Kraken's L2 and L3 checksums are validated after applying every update in wire-array order and
truncating to subscribed depth. CRC32 always covers the best ten price levels, asks before bids.
For L3 it covers every visible order in timestamp priority within a level, so it also checks queue
state. This follows Kraken's official Go client, which updates an order timestamp on `modify` and
then re-sorts that level. Equal timestamps retain local arrival order as a deterministic tie-break;
a checksum mismatch still stops capture. Kraken provides no numeric L2 or L3 sequence ID, so
DATA-1B invents none. The sequential `trade_id` is preserved only with its documented trade-feed
meaning. Local `message_ordinal`, `data_index`, `side_index` and `wire_order` are labelled as local
structure, not venue sequence.

Kraken can encode decimal fields as JSON numbers. Direct DuckDB JSON extraction would therefore
round or canonicalize some values. After validating each exact raw frame, the adapter writes one
explicitly local normalized-frame record whose prices and quantities are the original JSON number
lexemes represented as strings. This is the small research layer, not a second recorder or a new
canonical/provenance contract. The shared DATA-1A Parquet writer still provides batching, ZSTD,
atomic publication and crash behavior. The additive catalog views are:

```text
kraken_spot_trades
kraken_spot_l2_events
kraken_spot_l3_order_events
kraken_spot_l3_order_lifecycle
```

The lifecycle is only what was observed inside subscribed depth. Snapshot orders may predate the
session, `add` may mean a previously out-of-scope level became visible, out-of-depth truncation has
no wire `delete`, and `delete` does not distinguish cancel from full fill. Local
`scope_truncate` events are labelled `event_source=local_scope`; no strategy edge or complete order
history is claimed.

On 2026-08-31, a bounded phase 2 smoke completed two short authenticated BTC/EUR L3 sessions
using two token requests and one controlled session restart. Each session received a positive
acknowledgement, built a fresh snapshot before updates, validated CRC32, and validated
post-snapshot updates. Exact raw-byte round-trip, Parquet/DuckDB readback, and credential-redaction
checks passed. The public `trade` channel produced zero messages, so the smoke does not establish
live trade coverage. The 48 `scope_truncate` events with `event_source=local_scope` were normal
local depth-10 scope accounting, not corrupted frames, feed gaps, or payload truncation. The smoke
did not establish live `modify` coverage, 24-hour reliability, hard-crash durability, a strategy
edge, deployment readiness, or production suitability.

Official contracts used for this slice:

- https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/level3
- https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/book
- https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/trade
- https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/status
- https://docs.kraken.com/exchange/guides/websockets/l3-checksum-v2
- https://docs.kraken.com/exchange/guides/websockets/book-checksum-v2
- https://docs.kraken.com/exchange/guides/rest/authentication
- https://docs.kraken.com/api-reference/trading/get-websockets-token
- https://docs.kraken.com/exchange/guides/rest/api-keys
- https://docs.kraken.com/exchange/changelog
- https://github.com/krakenfx/api-go/blob/a8484bc5ec985fd5ce5bcc0580f659727d8f7603/pkg/book/level.go
- https://github.com/krakenfx/api-go/blob/a8484bc5ec985fd5ce5bcc0580f659727d8f7603/pkg/book/checksum.go
- https://github.com/krakenfx/kraken-cli/blob/aa56e5976be5afa6d8267eb6741f3a8844678fe9/crates/kraken-core/src/subscribe/message/level3.rs
- https://github.com/krakenfx/kraken-cli/blob/aa56e5976be5afa6d8267eb6741f3a8844678fe9/crates/kraken-core/src/response/result.rs

### DATA-1C — OKX BTC-USDT-SWAP public research slice

Phase 1 of DATA-1C under D10 — multi-venue market data and feed coverage is an offline-only,
credential-free adapter for the EEA production contracts. It fixes the product to
`BTC-USDT-SWAP` and uses the public socket at
`wss://wseea.okx.com:8443/ws/v5/public` for `bbo-tbt`, ordinary `books`, `funding-rate`,
`open-interest`, `mark-price`, and the `BTC-USDT` `index-tickers` reference. Individual
`trades-all` messages use the separate public business socket at
`wss://wseea.okx.com:8443/ws/v5/business`. No VIP channel, API key, account, signing, execution
path, SDK, MCP, command, or live smoke is part of this phase.

Every received application frame is timestamped and copied to immutable bytes at callback entry
before JSON decoding, then written through the shared DATA-1A raw record and ZSTD-Parquet writer.
The two sockets share one run-wide local ordinal while each connection or reconnect receives a
fresh session ID. Subscription sends and acknowledgements, disconnects, reconnects, gaps,
snapshots, resnapshots, and documented sequence resets remain explicit local markers. Validated
local normalization preserves every decimal lexeme as text and adds only four research views:

```text
okx_swap_trades
okx_swap_bbo
okx_swap_l2_events
okx_swap_derivative_context
```

Ordinary `books` is a public 400-level snapshot-plus-incremental L2 feed, not L3/MBO. DATA-1C
requires the first `snapshot`, `prevSeqId=-1`, and thereafter an exact
`prevSeqId`-to-previous-`seqId` chain. It accepts only the documented empty no-update message and
maintenance sequence reset exceptions. An unexplained break, update before snapshot, wrong
channel/instrument, malformed schema, oversize/truncated input, or writer failure stops the whole
slice; reconnect starts empty state and requires a new snapshot. Since 23 June 2026 the JSON
`books` checksum field is fixed to `0`; the adapter checks that wire contract but never calculates
or uses it for integrity. `bbo-tbt` has only its documented `seqId`, so DATA-1C does not invent a
`prevSeqId` or checksum for BBO.

The ordinary `books` and `bbo-tbt` feeds exclude RPI liquidity and therefore represent only the
organic book. `trades-all` can still report RPI executions through `source=1`, including an
execution outside the organic BBO. Even the separate consolidated `books-rpi` feed can omit
temporarily hidden but tradeable RPI orders; DATA-1C does not subscribe to it and makes no
complete-book claim.

The public `liquidation-orders` channel was audited and deliberately deferred. Its SWAP
subscription is not filterable to one instrument, its data is explicitly incomplete, and records
are not chronological. Adding that SWAP-wide stream would widen this BTC-only slice; whenever it
is added later, absence must never be interpreted as zero liquidations.

All fixtures and transport tests in phase 1 are synthetic adaptations of the official schemas.
They prove bounded offline parsing, exact-byte storage, sequencing, reconnect and query behavior;
they do not prove endpoint reachability, continuous trade or context coverage, 24-hour
reliability, hard-crash durability, a strategy edge, deployment readiness, or production
suitability. No dependency, canonical/provenance contract, D15 — reproducible Gold features and
aggregates, or D22 — durable PAPER ledger and reconciliation is extended.

On 2026-08-31, a credential-free 150-second phase-2 acceptance smoke used one public and one
business WebSocket session without a reconnect. All seven subscriptions received positive
acknowledgements and produced inbound frames: 4,015 `trades-all`, 5,036 `bbo-tbt`, 1,458 `books`,
3 funding-rate, 13 open-interest, 728 mark-price, and 629 index-tickers frames. The `books` stream
started with one fresh snapshot and then delivered 1,457 validated incremental updates with an
unbroken `prevSeqId` to `seqId` chain; every deprecated checksum value was the required literal
`0`. Live evidence also confirmed that the documented 400-level limit applies to the initial
snapshot, while a 100-ms incremental change batch can contain more than 400 entries on one side.
The adapter therefore retains the 400-level snapshot bound and validates complete incremental
batches within the application-payload bound.

All 23,801 run-wide ordinals and their exact payload bytes and SHA-256 values survived readback.
Six atomically published ZSTD-Parquet parts contained 20,967,047 raw payload bytes in 3,292,982
bytes on disk, with no partial files; all four DuckDB views were queryable and non-empty. Writer,
schema, sequence, truncation, reconnect, and secret checks were clean. This short smoke proves only
current public endpoint reachability and the observed payload, sequence, storage, and query paths.
It does not prove 24-hour reliability, hard-crash durability, complete trade or context coverage,
RPI or liquidation coverage, a strategy edge, deployment readiness, or production suitability.

Official contracts used for this slice:

- https://my.okx.com/docs-v5/en/#overview-production-trading-services
- https://my.okx.com/docs-v5/en/#overview-websocket
- https://my.okx.com/docs-v5/en/#order-book-trading-market-data-ws-all-trades-channel
- https://my.okx.com/docs-v5/en/#order-book-trading-market-data-ws-order-book-channel
- https://my.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel
- https://my.okx.com/docs-v5/en/#public-data-websocket-open-interest-channel
- https://my.okx.com/docs-v5/en/#public-data-websocket-mark-price-channel
- https://my.okx.com/docs-v5/en/#order-book-trading-market-data-ws-index-tickers-channel
- https://my.okx.com/docs-v5/en/#public-data-websocket-liquidation-orders-channel
- https://www.okx.com/en-us/help/okx-order-book-channels-checksum-field-deprecation
- https://www.okx.com/en-eu/help/okx-retail-price-improvement-program-rpi
- https://www.okx.com/docs-v5/log_en/#2026-07-28

### DATA-1D — Bitvavo Standard BTC-EUR public research slice

Phase 1 of DATA-1D under D10 — multi-venue market data and feed coverage is an offline-only,
credential-free adapter for public Bitvavo Standard data at `wss://ws.bitvavo.com/v2/`. Its fixed
scope is `BTC-EUR`: individual `trades`, incremental `ticker` BBO/last-price fields, and price-level `book`
updates joined to a public WebSocket `getBook` snapshot with depth 1,000. It does not authenticate,
use an account, call a trading or account action, use the Market Data Pro endpoint, or silently
substitute Standard data for Pro evidence. No SDK or new dependency is needed.

Each received application frame is timestamped and copied to immutable bytes at callback entry
before JSON decoding, then written through the existing DATA-1A raw record and atomic
ZSTD-Parquet path. One run-wide ordinal spans all sessions. Every transport reconnect gets a new
session ID, empty acknowledgements and book state, a fresh subscription, and a fresh snapshot;
disconnects and the resulting unknowable interval remain explicit gap events. The adapter exposes
only three source-linked, string-preserving DuckDB views:

```text
bitvavo_spot_trades
bitvavo_spot_bbo
bitvavo_spot_l2_events
```

The Standard book is aggregated price-level L2, never L3/MBO. It has no documented checksum. A
`nonce` is the sequential version of one market book: after bootstrap, every update must be exactly
the previous nonce plus one. Duplicate, out-of-order, reset, or skipped nonces, an identity/schema
error, an oversize or truncated application frame, or a writer failure stops fail-closed. A new
snapshot is required after reconnect. Quantity `"0"` means deletion; side order and wire order are
retained, and all prices, quantities, IDs, timestamps, and nonce lexemes avoid float conversion.
Ticker has neither a venue timestamp nor a sequence. Its fields are optional update fields: live
evidence can carry only the changed bid pair, ask pair, or last price. The BBO view therefore
forward-fills each observed field only within one session and exposes `bbo_complete`; it never
fills across a reconnect or presents an incomplete initial state as a full BBO. Trades have a
unique ID but no documented gap sequence, so their live completeness cannot be inferred from IDs
or event counts.

The current local-book guide has a notable internal tension: its example shows an initial update
newer than the snapshot, while its written rule says the snapshot must be strictly newer than the
first buffered update. DATA-1D follows the written rule without guessing. It waits for the first
buffered update before requesting `getBook`, accepts only `snapshot_nonce > first_buffered_nonce`,
discards buffered updates covered by that snapshot, and then requires an exact `+1` join. A stale
or equal snapshot may be requested again only within a small configured bound. The accepted
phase-2 public smoke used one snapshot request and was configured to fail rather than retry; it
therefore tested the live contract without masking a mismatch.

The depth-1,000 snapshot is not proof of a complete order book outside that captured depth. The
`timestamp` on Standard book events and WebSocket snapshots is documented as the nanosecond time
of the last transaction event, not local receipt time and not necessarily snapshot generation
time. It remains nullable because the local-book guide and formal examples do not consistently
require it. Standard and a future Pro capture must use separate Parquet corpora; this catalog labels
the three views `feed_product='standard'` and makes no equivalence claim.

Phase 1 fixtures are sanitized synthetic adaptations of the official public schemas. They prove
only deterministic offline parsing, callback-entry byte capture, bounded buffering, nonce-chain
validation, reconnect state isolation, Parquet byte/SHA round-trip, and local query behavior. No
accepted public smoke is part of that offline proof. An initial phase-2 attempt opened the public
socket and stopped fail-closed when a live ticker carried only a bid pair, which the initial parser
incorrectly required to be a complete BBO. The targeted repair accepts only structurally paired
partial updates and tests causal, session-scoped reconstruction.

On 2026-08-31, the one allowed repair smoke then ran the public socket for 90 seconds in one
session, with one snapshot request and no reconnect. All three requested channels were positively
acknowledged. The capture observed 26 trades, 1,601 ticker updates, 895 book updates, and one
wrapped book snapshot. The book path installed one fresh snapshot and validated 893
post-snapshot updates with an unbroken `+1` nonce chain. After each side had been observed, 1,599
ticker rows exposed a complete session-local BBO state; this is causal forward-fill evidence, not
a venue sequence or completeness guarantee.

All 5,059 run-wide ordinals and exact payload bytes/SHA-256 values survived readback. Three
atomically published ZSTD-Parquet parts stored 2,500,969 payload bytes in 493,678 bytes with no
partial file, and the trades, BBO, and L2 DuckDB views were queryable and non-empty. There was no
schema, sequence, writer, truncation, gap, reconnect, retry, credential, or secret event. The
bounded run took 94.8 seconds including catalog creation and readback. It proves only the observed
public endpoint, wire shapes, sequence path, storage path, and query path. It does not prove
continuous or complete trades/BBO coverage, full-depth completeness, checksum coverage, 24-hour
reliability, hard-crash durability, Market Data Pro behavior, strategy edge, deployment readiness,
or production suitability.

Official contracts checked for this slice:

- https://docs.bitvavo.com/docs/websocket-api/
- https://docs.bitvavo.com/docs/websocket-api/trades-subscription/
- https://docs.bitvavo.com/docs/websocket-api/ticker-subscription/
- https://docs.bitvavo.com/docs/websocket-api/book-subscription/
- https://docs.bitvavo.com/docs/websocket-api/get-order-book/
- https://docs.bitvavo.com/docs/manage-order-book/
- https://docs.bitvavo.com/docs/faqs/
- https://docs.bitvavo.com/docs/rate-limits/
- https://docs.bitvavo.com/api-specs/exchange-websocket-api.yaml

### DATA-1E — Bitvavo Market Data Pro BTC-EUR research slice

Phase 1 of DATA-1E under D10 — multi-venue market data and feed coverage is an offline-only,
authenticated-boundary adapter for the `book` channel and in-band `getBook` snapshot at
`wss://ws-mdpro.bitvavo.com/v2/`. It fixes the product to `BTC-EUR` and deliberately excludes Pro
trades and ticker: DATA-1D already captures their public Standard counterparts, while the distinct
documented value of Market Data Pro for this slice is non-conflated price-level L2 with explicit
sequence ranges. This is L2, never L3/MBO. There is no documented checksum, and the deprecated Pro
`nonce` is retained only as an optional source field; it is neither an integrity input nor
comparable to Standard or REST nonces.

Every connection must authenticate before subscription. The signed preimage is exactly
`<timestamp_ms>GET/v2/websocket`, with a hexadecimal HMAC-SHA256 signature. The implementation uses
only the standard library plus the existing WebSocket dependency. The official Bitvavo SDKs do not
currently implement this Pro endpoint, so no SDK or dependency is added. Authentication requests,
signatures, authentication responses/errors, and subscription controls are never stored as exact
raw data. They produce only fixed, sanitized local markers. Every inbound market-data application
frame is timestamped and copied to immutable bytes at callback entry before decoding and uses the
existing DATA-1A raw-record and atomic ZSTD-Parquet path.

The official Pro schema uses `event="book"` for the book confirmation while the sibling Pro
subscription schemas use `event="subscribed"`. The adapter accepts only those two documented event
forms and still requires the subscriptions map to equal exactly `{"book":["BTC-EUR"]}`; neither
form can be mistaken for a market-data update because an update must carry the market and book
fields instead.

The one new source-linked DuckDB view is:

```text
bitvavo_mdpro_spot_l2_events
```

It exposes `feed_product='market_data_pro'`, the exact source ordinal and receipt clocks, payload
SHA-256, snapshot or update type, deprecated nonce, venue nanosecond timestamp, start/end sequence,
wire order, side, action, and unchanged decimal strings. Distinct `mdpro_book` and
`mdpro_book_snapshot` source channels prevent this view from selecting DATA-1D Standard rows. A
Pro capture still uses a separate Parquet corpus; no Standard/Pro equivalence is implied.

The snapshot `mdSeqNo` is the last engine event included. A buffered update wholly covered by that
snapshot (`endMdSeqNo <= mdSeqNo`) is discarded. The first retained update must start at
`mdSeqNo + 1`; each active update must then start exactly at the previous `endMdSeqNo + 1`, after
which the local sequence advances to its own `endMdSeqNo`. Bitvavo documents that a single message
may group multiple engine events under load. DATA-1E keeps that range as one wire event and never
fabricates intermediate changes. The official guide does not safely define a range that straddles
the snapshot boundary (`startMdSeqNo <= mdSeqNo < endMdSeqNo`), so that case fails closed instead
of being skipped or partially replayed. Duplicate, stale, out-of-order, gap, identity, schema,
buffer, truncation, sensitive-frame, or writer failures stop the capture and require a new
snapshot. A reconnect creates a new session, new signature/authentication boundary, empty state,
and fresh snapshot; it never falls back to Standard.

Fixtures are synthetic and credential-free adaptations of the official schemas. Phase 1 proves
only deterministic authentication-message construction, redaction, snapshot/range validation,
reconnect isolation, decimal preservation, exact market-byte/SHA Parquet round-trip, and local
query behavior. It uses no real credential and makes no live access, entitlement, completeness,
latency, 24-hour reliability, hard-crash durability, strategy-edge, deployment, production, or
execution claim.

Any bounded phase-2 smoke requires a dedicated Bitvavo key with only the UI `View access`
permission (called `Read-only` in the Pro introduction), all trade, withdrawal, transfer,
administrative, and subaccount permissions disabled, and IP allowlisting where practical. That
permission can expose account information even though this adapter calls only authenticate,
subscribe, and `getBook`; the key is therefore still sensitive. It may enter only through hidden
local `/dev/tty` prompts, never chat, environment variables, arguments, files, fixtures, logs, or
artifacts. Authentication or access rejection defers DATA-1E; it never justifies broader rights.

On 2026-08-31, the final bounded phase-2 smoke completed two short authenticated BTC-EUR Pro book
sessions with one controlled session restart and no automatic reconnect. Each session received
positive authentication and book-subscription acknowledgements, built a fresh depth-1,000
`getBook` snapshot, and validated at least one strictly post-snapshot update with an exact
`mdSeqNo` range transition. The run received 26 book-update frames and two snapshots. All 50
run-wide raw ordinals and payload SHA-256 values survived exact readback; one atomically published
ZSTD-Parquet part stored 94,522 inbound payload bytes in 50,394 bytes without a partial or writer
failure. Full materialization of the 4,003-row price-level event view, source linkage, and the
generic DuckDB views passed. The in-process exact credential scan completed over physical and
decompressed artifacts and reported no API key, secret, or generated signature material.

This controlled restart is not evidence for spontaneous transport-reconnect reliability. The
smoke does not prove checksum coverage, price levels beyond the requested snapshot depth, live Pro
trades or ticker coverage, every possible update shape, continuous completeness, 24-hour
reliability, hard-crash durability, strategy edge, deployment readiness, production suitability,
or execution capability. It remains L2 price-level research data, never L3/MBO.

Official contracts checked for this slice:

- https://docs.bitvavo.com/docs/ws-market-data-pro-api/introduction/
- https://docs.bitvavo.com/docs/ws-market-data-pro-sync/
- https://docs.bitvavo.com/docs/ws-market-data-pro-api/book-subscription/
- https://docs.bitvavo.com/docs/ws-market-data-pro-api/get-order-book/
- https://docs.bitvavo.com/api-specs/ws-market-data-pro-api.yaml
- https://docs.bitvavo.com/docs/get-started/
- https://docs.bitvavo.com/docs/rate-limits/
- https://docs.bitvavo.com/docs/errors/

## Self-collected dataset

After the local slice and definitive runtime ADR, realtime collectors should run on the approved 24/7 runtime and persist data to ClickHouse. This creates a dataset with the same receipt path and timestamp discipline the future live bot will use.

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
tracker, delivery linearization/composite queue item or deterministic replay. Pure delivery-
knowledge contracts are defined but dormant. The atomic producer and collector cutover happens
only in 3B1D. Nothing was deployed, and SHADOW/LIVE remain disabled.

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
successful delivery record. Phase 1A-3B1C-1 defines only dormant delivery-knowledge and coverage-
commit contracts; 3B1C-2 and 3B1C-3 will add operational coverage and atomic delivery. Phase
1A-3B1B deliberately creates neither runtime boundary.

#### Phase 1A-3B1C contract closure

The coverage/delivery audit found five representation gaps, so 3B1C is split into pure contract
closure (3B1C-1), its bounded runtime-binding contract correction (3B1C-1A), operational coverage
(3B1C-2) and atomic output delivery (3B1C-3). The intervening 3B1C-1B, 3B1C-1C and 3B1C-1D corrections
remain pure as well. These contract slices add only immutable, deterministic values and validators;
they perform no async work and change no collector, sink, queue, health or consumer behavior.

Ordinal-zero coverage has its own versioned initialization identity. Initializations and prepared
transitions are exposed through one tagged state-reference identity, while a prepared mutation
batch binds the complete expected pre-state, cause-derived plan membership, proposed changes,
already-current no-ops and complete resulting state-reference set. The prepared batch is only a
compare-and-swap proposal. A matching `CoverageCommitAcceptance`, not a transition or batch ID by
itself, is the future proof of an atomic state commit. A repeated failure on a scope already at or
beyond the requested degraded state preserves its state reference and ordinal while retaining the
new typed evidence in an explicit no-op.

Prepared state references are candidate tokens, including their exact predecessor chain. They
cannot be cited as committed event or upstream coverage on their own. `CommittedCoverageState`
binds one candidate to a content-addressed commit acceptance whose complete result set contains
that exact state; `EventCoverage` and upstream-state evidence accept only this committed binding.
The Bronze and Silver event states must belong to the same collector run. An upstream-derived
Silver state must cite the exact committed Bronze state paired with it, not merely another state
with the same scope or status.

Fan-out membership is derived from an immutable subscription plan and attempt snapshot rather than
a caller-selected scope list. It distinguishes configured, possibly delivered, acknowledged,
exactly routed and unclassifiable-possibly-active populations. A failure before any send targets no
scope; one ambiguous sent spec cannot degrade unrelated specs; an indexed item selects its exact
spec and instrument; and an unclassifiable returned market message can select only the complete
relevant possibly-active slice. Mutation evidence must bind that fan-out's exact connection session
and selected attempt where applicable. Every raw-backed mutation also binds the raw record's exact
full-record digest and complete attempt-status snapshot; that snapshot must reproduce the prepared
fan-out, so sharing only a collector-run or connection-session ID is insufficient.

The 3B1C-1A correction makes acknowledgement selection explicit. The proof retains the complete
current snapshot, including earlier acknowledgements, while a separate non-empty sorted/unique
attempt-ID tuple selects only attempts whose current status is exactly `ACKNOWLEDGED`. The target
set is all and only the requested-domain leaf scopes belonging to those selected specs. Selection
is an explicit caller fact for the intended leaf initialization; the proof does not reconstruct ACK
transition history from one current snapshot.

The 3B1C-1B correction represents an exact indexed rejection before acknowledgement without
pretending that the subscription was active or falling back to plan-wide pre-index uncertainty. A
new rejection-only target accepts exactly `PENDING`, `SEND_STARTED` or `SENT` from the raw record's
immutable attempt snapshot. It binds that status plus the exact public selector, spec, attempt,
session, canonical instrument and family/version/payload to one Silver-normalization leaf. The
corresponding typed outcome is necessarily `REJECTED_AFTER_INDEXING`, with a `REJECTED` item,
`PROVENANCE_MISMATCH` frame evidence and a `PROVENANCE_MISMATCH` normalization-failure category.
That evidence establishes `CONFIRMED_INCOMPLETE` only for the identified Silver leaf; it never
mutates Bronze or authorizes activation, delivery, recovery, conflicts, duplicates or
materialization. Multiple wire indexes on the same route retain separate raw-index/source-ID evidence
while sharing one scope mutation. For later outcome-sink failure, the new proof can additionally
bind the exact acknowledged route partition so the aggregate validator requires the complete decoded
scope/attempt union. Existing transition-empty 3B1B outcomes remain valid, and no active runtime
imports this dormant path yet.

The 3B1C-1C correction keeps complete fan-out atomic when the subscription plan approaches its
1,024-spec bound. The flattened v1 mutation content measured 16,762,723 characters for 957 targets
and failed at 958 against the existing 16,777,216-character ceiling; an already-transitioned
300-target mutation measured 11,824,581 characters. Neither a larger limit nor multiple batches is
acceptable: the former only delays the same failure and the latter loses all-target CAS semantics.
Mutation batch v2 instead retains every full immutable typed operation while its top-level content
commits, in target order, to the exact scope, pre-state or null, mutation disposition, selected
initialization/transition/no-op and resulting state. Commit acceptance v2 binds the exact ordered
result set. Normalization lineage v3 similarly retains every primary, abort, conflict, transition,
no-op and resulting-state value while committing each complete ordered role independently. Counts,
ordinals and domain/version tags are part of the hash input, and stored verification recomputes all
levels from typed values. Previous batch/acceptance v1 and lineage v2 IDs remain parser-only and
cannot be interpreted as current identities. This changes no capture, coverage runtime or Silver
output; v2 and `is_gap` remain active, v3 remains dormant, and 3B1C-2 resumes only after merge.

The 3B1C-1D correction preserves two independent primary causes in one reliably indexed frame.
`MIXED_INDEXED_FAILURE` requires at least one `REJECTED` and one `SOURCE_EVENT_CONFLICT`; it may
also contain only genuinely new frame-aborted peers and exact duplicates. The complete index range,
exact canonical evidence union, typed failure/conflict lineage and frame-atomic zero-materialization
rule remain mandatory. The older rejected and conflict statuses still reject each other's primary
disposition. New factories emit `normalization-outcome-v2`, which retains the existing ordered outer
components and content digest; parser-only v1 accepts only its historical status set.

The same correction adds `BatchVerifiedCoverageDerivation`, a sealed factory-only boundary over one
exact mutation batch v2, matching acceptance v2 and complete ordered result set. It performs shared
batch, acceptance, membership, run, epoch, status and ordinal verification once, then exposes the
existing byte-identical `CommittedCoverageState` and upstream evidence-source values with O(1)
per-leaf lookup. It creates no new semantic ID, commit proof, cache or partial result. This removes
the prior O(N²) revalidation path while retaining every typed leaf and stored-verification rule.
The later runtime must keep synchronous coverage CPU work within `C=1` second so the complete
heartbeat expression remains strictly bounded at 57 seconds with current defaults. No runtime
coverage or delivery queue is activated here.

The 3B1C-1E correction changes only the internal execution of that same factory. A call-local
verification transcript now rederives scope, epoch, evidence, initialization/transition, result,
batch and acceptance facts once and reuses them during leaf construction. Successful canonical
parses live only for the duration of the call; there is no global, mutable or cross-call cache and
no caller-supplied validation token. Retained raw-fan-out fields are format-checked, their
raw-ID feed/run/session lineage is cross-bound, and their exact content, content digest and outer ID
are rederived before the batch is accepted. The originating raw-record factory computes the
full-record and complete-snapshot digests; this retained-only boundary cannot reconstruct those two
preimages because it intentionally retains neither the raw record nor its complete attempt table.
All public objects, ordered tuples, canonical JSON, version tags, digests and IDs remain byte-exact.

The reserved pure-contract `from_commit` budget is 0.8 seconds, allocating at least 0.2 seconds of
the later `C=1` runtime budget for CAS and state-snapshot work. With fixture construction outside the interval,
three unreported warmups, nine sequential complete `from_commit` calls and garbage collection
enabled, TerraPC 1,024-target medians/maxima were 0.283136466/0.302709014 seconds for
initialization, 0.569355146/0.587829245 seconds for `COMPLETE -> UNCERTAIN`, and
0.707791138/0.723139806 seconds for a full no-op. Median 512-to-1,024 ratios remained between
1.885200742 and 1.916039095. ADR-022 records every ordered sample. This is host- and fixture-bound
evidence, not an absolute latency guarantee. It applies only to `from_commit`: a separate diagnostic
serial construction of 1,024 ordinary upstream evidence values took 18.257364 seconds, with about
739 canonical parses per leaf. Thus the 0.2-second remainder is an unproven runtime allocation, not
a current Bronze-to-Silver result. 3B1C-2 must optimize or separately close that path and re-prove
the complete warm synchronous median at no more than 1.0 second. No runtime coverage or delivery
behavior is activated.

The 3B1C-1F correction retains the complete typed `CoverageTargetCatalog` inside every newly
written fan-out v3 proof. Because that catalog already retains its exact `SubscriptionPlanIdentity`,
one stored proof can rederive the full plan and catalog content, digests and IDs, reconstruct the
kind-specific membership selection and reject omitted, added, reordered or foreign leaves without
an external mapping. Fan-out v1 and v2 remain byte-exact parser-only values. The active v3 compact
preimage contains only plan/catalog IDs, session, kind-specific source row and sorted scope IDs; it
does not duplicate full plan or catalog content.

`BatchVerifiedUpstreamCoveragePreparation.from_committed_upstream` fuses one fully accepted
Bronze batch with one exact Silver fan-out. The first API deliberately supports only matching
`ALL_POSSIBLY_ACTIVE` slices with the same feed, run, plan, catalog, session and complete attempt
snapshot. It verifies the upstream batch/acceptance/result set and both fan-outs in one call-local
linear transcript, builds O(1) semantic-scope indexes and pairs every Bronze leaf once. Initial
degraded states copy the exact upstream boundary; later degradation requires the exact committed
upstream transition; equal or worse current Silver state creates a typed no-op. No recovery to
`COMPLETE`, partial prefix or fabricated Silver acceptance exists. Optional raw bindings must be
absent on both sides or retain the same raw-record ID, full-record digest and complete attempt
snapshot.

Compact v2 identities prevent the retained graph from recursively embedding complete JSON text.
Upstream-derived state references bind initialization, compact predecessor-ID and latest-transition
ID; committed states bind the compact state ID, exact acceptance ID and `result_ordinal`; upstream
evidence binds its full typed committed-state parent through a compact state/transition source row.
The complete typed parents remain retained and are fully reverified on load. Upstream evidence and
committed states now write v2 only; non-upstream evidence and ordinary ordinal-zero state references
may still write v1, and a first v2 transition may retain a fully verified v1 predecessor. A no-op
returns the exact prior state without a new transition or ordinal.

All failure benchmark series remain recorded, including Series 6's and Series 9's narrow fused
full-no-op median failures. In the final green Series 11 run, all 1,024-leaf medians were at most
0.793836825 seconds, all samples were at most 0.831679216 seconds, the worst 1,024/512 ratio was
2.070689029 and peak isolated-process RSS was at most 449,732,608 bytes. Series 10 follows a
family-filtered retained-proof correction and removes only duplicate construction-time validation;
retained-load verification remains complete. Series 11 follows the metadata-only correction that
makes the public fan-out writer tag v3 while preserving parser support and canonical bytes. The
deterministic 67,108,863-byte joint
composability limit charges every unique verified v2 evidence/state/committed ID in both the
ordinary and fused writers. At 1,024 leaves the worst-case escaped run-ID probe accepts width 550
at 67,065,684 bytes and rejects width 551 without returning a partial graph; independently valid
widest scalars therefore need not compose with maximum fan-out. Representative byte volume grows
linearly from 512 to 1,024. Timing remains host- and fixture-bound; 3B1C-2 still must integrate this
dormant pure path and prove the complete runtime median at no more than 1.0 second. No event schema
or active runtime behavior changes here.

The 3B1C-1G correction makes the pre-parse raw-rejection knowledge boundary explicit. A definitive
raw-sink rejection proves that the expected Bronze record was not accepted, so Bronze is
`CONFIRMED_INCOMPLETE`. Projection membership is nevertheless unknown before parsing: no typed fact
yet proves that the rejected record contained a Silver-relevant item. The closed projection table
is `INCLUDED` to Silver `CONFIRMED_INCOMPLETE`, `EXCLUDED` to no Silver degradation from this cause,
and pre-parse `UNKNOWN` to Silver `UNCERTAIN`. Only the final row is added here; it is not a generic
status downgrade and it does not alter the existing exact-status upstream route or
`EventCoverage` invariant. That exact-status route remains available for its existing valid causes
but rejects this pre-parse raw-rejection cause unless a separate typed `INCLUDED` proof exists.

The opt-in raw-rejection relation retains the full accepted Bronze batch, exact positional
committed state, typed raw-record rejection evidence, raw fan-out binding and exact paired Silver
scope. Its compact source row binds those parent IDs, `result_ordinal`, raw-record identity and
full-record digest; stored verification walks the retained plan, catalog, session, attempt,
fan-out, commit and rejection lineage before rederiving the source, evidence content, digest and
outer ID. New evidence v3 is written only for this `UNKNOWN` relation. Existing non-upstream
evidence v1 and exact upstream state/transition evidence v2 remain writer-active on their existing
routes and byte-exact; their parsers and all other coverage identities are unchanged. Ordinary
standalone source/evidence factories remain the fully validating semantic oracle and are not a
plan-scale composition path. The verified positional ordinary transcript and fused complete
preparation both produce byte-equivalent values with bounded linear growth through 1,024 targets;
the fused factory is the integrated atomic convenience boundary. This contract remains dormant:
raw rejection emits no fabricated normalization outcome or v2 event, schema v2 and `is_gap`
remain active, market-event v3 remains dormant, and no runtime or delivery behavior is activated.

Normalization-outcome sink failure is represented by the exact canonical
`["normalization-outcome-evidence-v1",normalization_outcome_id,coverage_scope_id]` source row.
Only explicit typed rejection establishes definite non-acceptance and Silver-normalization
`CONFIRMED_INCOMPLETE`. Timeout, arbitrary exception, wrong result type or wrong ID/destination
echo leaves sink acceptance ambiguous and therefore Silver normalization `UNCERTAIN`. Intentional
cancellation creates no failure evidence. Every such source is bound to the outcome's raw record,
feed, collector run and exact Silver scope, and requires the raw record's full fan-out binding.
All targets in one mutation cite the same outcome and the same failure knowledge. This evidence is
created after the outcome exists, so it is never written back into that failed outcome's own
normalization lineage. `materialized`, `duplicates_only`, `mixed_success`,
`rejected_after_indexing`, `source_event_conflict` and `mixed_indexed_failure` require exact routed
fan-out;
`rejected_before_indexing` requires the complete possibly-active plan slice. `control_no_event` and
`valid_empty_market_frame` cannot create a coverage mutation through this post-outcome evidence.
The lower evidence source and prepared mutation alone do not prove that this target selection
belongs to the concrete decoded outcome. The dormant
`NormalizationOutcomeSinkFailureCoverageBinding` supplies that upper-layer proof by validating the
exact indexed scope/attempt union or pre-index aggregate against the raw binding. It covers each
target exactly once across initializations, transitions and already-degraded no-ops. The 3B1C-2
runtime must accept this complete aggregate and may not treat a loose lower-layer batch as the
outcome-to-coverage proof.

Frame-atomic abort evidence identifies each otherwise valid new candidate that was prevented from
materializing by canonical primary failure/conflict causes elsewhere in the same raw frame. It
never creates its own coverage transition or materialization. The strict normalization-outcome
path accepts typed prepared coverage lineage and distinguishes it from later commit acceptance.
Its raw-fan-out binding and every indexed or pre-index scope binding must carry the same exact
full-record integrity SHA-256; sharing only the lower raw-record locator is insufficient. The
existing active 3B1B factory remains transition-empty and byte-compatible.

Delivery contracts bind one non-empty successful `NormalizationOutcome`, its complete ordered
materialization set, a per-item event-content SHA-256 and a derived aggregate digest. Knowledge is
one of accepted, definitely not accepted or acceptance uncertain; bounded reasons do not masquerade
as knowledge. Accepted delivery requires a matching commit-acceptance value. A non-acceptance result
cannot contain an event batch, and control, empty, duplicate-only, rejected and conflicting frames
cannot create a delivery batch. Actual v2-event serialization and a composite audited queue item
remain 3B1C-3 work. Queue acceptance will mean bounded collector-output-queue acceptance only, not
dequeue, downstream processing or persistence.

All 3B1C-1 through 3B1C-1G coverage paths remain dormant. Existing 3B1B raw
capture still emits normalization outcomes with empty coverage lineage, now under the current outer
outcome v2 identity; schema v2 remains the only active Silver envelope and operational `is_gap`
remains unchanged. No coverage runtime, delivery linearization,
raw persistence or replay exists yet.

## ClickHouse environments

### Local DEV

A disposable ClickHouse container supports:

- schema/migration tests;
- sample ingestion;
- deterministic replay;
- API/Grafana/frontend integration tests.

It may be destroyed and recreated.

### PAPER/SHADOW/LIVE runtime

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

Use SSD-backed durable runtime storage for:

- active ClickHouse partitions;
- recent raw feed data;
- features;
- paper/live execution data;
- Redis/PostgreSQL when introduced.

Move large cold archives and old raw market data to the chosen archive tier according to retention policy; the existing TrueNAS HDD pool remains an option while that profile is retained.

The Windows workstation stores only source, build caches and bounded development datasets. It is not a backup of the durable runtime data store.

## Paid data decision rule

A future paid provider must solve an explicit problem, for example:

- insufficient historical L2 depth;
- inability to reconstruct realistic fills;
- missing cross-exchange derivatives history;
- missing options/on-chain feature history.

The paid feature set is evaluated against a no-paid-data baseline. A subscription is justified only if it materially improves out-of-sample return, risk, execution quality or research speed relative to its cost.
