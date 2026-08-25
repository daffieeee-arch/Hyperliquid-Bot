# Venue Strategy

## Decision summary

The platform is designed to **observe many venues but trade on few venues**.

Multiple exchanges are useful for price discovery, lead/lag analysis, basis, funding, liquidity comparison and operational redundancy. However, every authenticated execution venue adds substantial complexity: credentials, balances, fees, order state machines, partial fills, reconciliation, outages, reports and venue-specific risk.

The initial target is therefore:

| Venue | Initial role | Initial live execution |
|---|---|---|
| Hyperliquid | Perpetuals, basis/carry, public market data | Later, after paper/shadow gates |
| Bitvavo | EUR on-ramp, spot markets, public L2 data | Preferred first small live spot venue |
| Kraken | Public data, official paper engine, MCP research interface, optional hedge/backup venue | No, unless a later promotion case is demonstrated |
| Binance | External price, spot/perp, funding and lead/lag reference data | No |

This role assignment is provisional and evidence-driven. No venue is selected solely because it offers the most features.

## Why multi-venue can create value

A multi-venue data plane can support:

- cross-exchange price discovery and lead/lag research;
- basis and funding differentials;
- spot/perpetual hedges;
- all-in execution-cost comparison;
- liquidity and spread comparison;
- venue outage detection;
- independent reference prices;
- reduced dependence on one market structure.

The strategy layer must consume normalized instruments and features, not vendor-specific client calls.

## Why execution must remain limited

Each additional live execution adapter requires production support for:

- authentication and key rotation;
- exchange-specific precision and minimum-order rules;
- order acknowledgement, rejection and timeout handling;
- partial fills and cancel/replace behavior;
- balance, position and fill reconciliation;
- fee, funding and borrow accounting;
- rate-limit handling;
- maintenance/outage behavior;
- venue-specific reports and audit records;
- operational and counterparty concentration limits.

Complexity grows faster than the number of venues. A new live venue is admitted only when its expected benefit exceeds its engineering and operational cost.

## Phased rollout

### Phase 1 — Public data and unified paper execution

- Collect public Hyperliquid, Bitvavo, Kraken and selected Binance data.
- Store normalized events and point-in-time metadata in ClickHouse.
- Use one venue-independent paper broker.
- Model each venue's fees, spread, depth, latency assumptions and order constraints.
- No private exchange credentials are required for public market data.
- Kraken MCP may expose only the safe `market`, `account` and `paper` services; live `trade`, `futures`, `funding` and transfer capabilities remain disabled.

### Phase 2 — Read-only account integration

- Add read-only balances, orders and fills where useful.
- Validate reconciliation and reporting without order permissions.
- Keep withdrawal permissions disabled for every automated credential.

### Phase 3 — Limited live execution

- Bitvavo: very-small-capital spot execution after paper/shadow approval.
- Hyperliquid: very-small-capital perpetual execution after separate execution and risk approval.
- Kraken remains a data/paper venue unless a concrete use case is approved.

### Phase 4 — Optional additional execution venue

Kraken or another venue may be promoted only when it demonstrates one or more measurable benefits:

- materially lower all-in costs for an active strategy;
- better liquidity or fill quality;
- access to an instrument needed for a validated hedge;
- meaningful operational redundancy;
- a profitable cross-venue strategy that cannot be implemented otherwise.

## Bitvavo EUR and USDC handling

Bitvavo exposes a `USDC-EUR` market and supports authenticated order placement through its REST/WebSocket APIs. Therefore, the engine can buy or sell USDC with EUR automatically; manual conversion is not technically required.

The engine must not blindly convert EUR to USDC before every trade. It should compare executable routes:

```text
Route A: EUR -> ASSET -> EUR
Route B: EUR -> USDC -> ASSET -> USDC -> EUR
Route C: existing USDC inventory -> ASSET -> USDC
```

For each route, estimate:

```text
all_in_cost = trading_fees
            + bid_ask_spread
            + expected_slippage
            + currency_conversion_cost
            + expected funding/borrow cost
            + risk buffer
```

Pair availability and fee tiers must be fetched dynamically. Not every Bitvavo asset has a USDC market, and fee schedules can change.

### Treasury / quote-asset policy

The platform should include a small treasury policy rather than treating conversion as part of strategy logic:

- configurable target balances for EUR and USDC;
- minimum and maximum USDC inventory;
- maximum conversion size per order and per day;
- limit-order preference when urgency is low;
- slippage and spread guards;
- no conversion when market data is stale;
- full audit trail linking conversion orders to their purpose;
- optional human approval above a configured threshold.

Maintaining a reusable USDC inventory can avoid repeated EUR/USDC conversion on every spot trade. It also introduces explicit risks:

- EUR/USD foreign-exchange exposure;
- USDC issuer/depeg risk;
- additional venue concentration;
- conversion spread and liquidity risk.

These risks must be visible in the portfolio and risk workspaces.

## Venue router

Strategies submit venue-neutral intents. They do not call `bitvavo.buy`, `kraken.buy` or `hyperliquid.order` directly.

Conceptually:

```python
intent = TradeIntent(
    asset="SOL",
    instrument_type="spot",
    side="buy",
    risk_budget=..., 
    urgency="normal",
)
```

The execution/venue router evaluates eligible instruments and venues using:

- all-in expected cost;
- available balance/collateral;
- spread and depth;
- expected fill probability;
- venue health;
- strategy allowlist;
- risk and concentration limits;
- quote-asset inventory;
- regulatory/account constraints.

The router produces a deterministic execution plan that is stored before orders are submitted.

## Symbol and instrument normalization

An instrument identifies one concrete market. Its identity fields are normalized as follows:

- `venue_market_id` is the stable unique identifier of one concrete market within a venue and instrument type;
- `native_symbol` is a separately retained adapter alias and does not determine canonical identity, dataclass equality or hash;
- aliases for the same concrete market map to the same `venue_market_id`;
- Hyperliquid HIP-3 markets use the complete `{dex}:{coin}` name as `venue_market_id` so different perp DEX namespaces cannot collide;
- `contract_expiry` is exactly Python `datetime.date | None`, is required for `future`, forbidden for `spot` and `perpetual`, and serializes as `YYYY-MM-DD`.

The canonical instrument ID is a versioned compact JSON array. Its component order is exactly:

1. version;
2. venue;
3. instrument type;
4. `venue_market_id`;
5. base asset;
6. quote asset;
7. `contract_expiry`.

Examples:

```json
["instrument-v1","bitvavo","spot","SOL-EUR","SOL","EUR",null]
["instrument-v1","binance","future","BTCUSDT_260925","BTC","USDT","2026-09-25"]
```

Venue adapters own mapping from venue metadata and `native_symbol` aliases to this normalized identity, plus venue-native precision rules.

### Hyperliquid public-trade boundary

The offline Hyperliquid trades adapter accepts only a `trades` channel frame whose `data` value is
an array of documented `WsTrade` objects. Every object requires `coin`, `side`, `px`, `sz`, `hash`,
`time`, `tid` and `users`. Unknown additive frame and trade fields are ignored, while every required
field remains strictly validated.

Normalization resolves `coin` by exact `native_symbol` lookup in an injected Hyperliquid instrument
registry. It never derives the base asset, quote asset or instrument type from the text. This exact
lookup preserves a HIP-3 identifier such as `{dex}:{coin}` and fails closed when no instrument or
more than one instrument matches.

Hyperliquid trade-side notation describes the aggressing order: `B` maps to venue-neutral `buy` and
`A` maps to venue-neutral `sell`. Prices and sizes remain exact positive finite decimals. The
documented `[buyer, seller]` user ordering is retained in the adapter DTO but is not part of the
venue-neutral trade event.

Normalized Hyperliquid event provenance uses these separate fields:

- `source_event_id` is the compact versioned JSON array
  `["hyperliquid-trade-v1", time_ms, coin, tid]`;
- `source_transaction_id` preserves the source `hash`;
- `source_sequence` remains null because `tid` is a 50-bit trade hash, not an ordered sequence;
- `correlation_id` is not overloaded with source identity.

The decoder and normalizer are pure and offline. Receipt time, process-local monotonic time,
collector version, collector commit, gap state and the instrument registry are explicit caller
inputs.

### Hyperliquid public-trades WebSocket lifecycle

The Phase 1A collector uses one unauthenticated connection to the fixed public mainnet endpoint
`wss://api.hyperliquid.xyz/ws`. It sorts a non-empty immutable Hyperliquid instrument registry by
exact `native_symbol`, enforces the documented 1,000-subscription ceiling before connecting, and
sends one `trades` subscription per configured symbol. Names are neither inferred nor rewritten:
regular names, HIP-3 `{dex}:{coin}` names and spot identifiers such as `@107` stay exact.

An application-message router separates the official-SDK greeting, `subscriptionResponse`, `pong`
and `trades` before the trade decoder. It accepts text frames only, rejects malformed JSON,
duplicate object keys and non-finite JSON numbers, and fails closed on unknown channels or invalid
control structures. Unknown additive fields inside otherwise valid known messages remain tolerated.
Acknowledgements are correlated by exact coin; order is irrelevant, exact duplicates are
idempotent and counted, and a coin's trades cannot be published before that coin is acknowledged.

The collector uses Hyperliquid's JSON application heartbeat (`{"method":"ping"}` and
`{"channel":"pong"}`), not only a WebSocket protocol-level ping. Its interval is strictly below
the documented 60-second server-outbound-idle timeout, and subscribe/ping sends plus pong, receive,
open and close waits are finite. Heartbeat processing coordinates with the single reader while that
reader applies bounded queue backpressure, so it cannot race a second receive operation. Implicit
proxy discovery is disabled while ordinary TLS certificate verification stays enabled. Every
reconnect creates a fresh connection and resubscribes each configured coin once. Conservative
message and reconnect budgets remain below the documented venue limits. Only transport-class
failures are retried with capped exponential backoff and injected bounded jitter; malformed
protocol, decoder, schema and publisher failures are terminal.

After each completed application-ping send, the next heartbeat interval runs concurrently with pong
processing. The conservative bound between completed sends is
`max(heartbeat_interval, pong_timeout + publish_timeout) + publish_timeout + send_timeout`, which
must remain below 60 seconds and is 54 seconds with the defaults. Normal, going-away, abnormal,
server-error, restart, temporary and bad-gateway close conditions reconnect. Protocol,
unsupported-data, invalid-payload, policy, oversized-message, incompatible-extension and unknown
close codes fail closed. Raw close reasons are never retained or exposed.

Each decoded frame becomes one immutable queue item, so a validated frame is either published in
wire order as a whole or not published. The in-process queue, the WebSocket frame buffers and the
source-event-ID LRU cache all have finite positive capacities. A full application queue applies
bounded backpressure; expiry is terminal, emits no partial prefix, and marks gap state. The LRU
maps each source event ID to its exact source-trade semantic fingerprint. Exact replays within
frames, across frames and across reconnect overlap are suppressed and refresh recency; conflicting
reuse of an ID fails the complete frame atomically. It is process-local only: restart or eviction
removes that protection, so this is not durable exactly-once delivery.

The first session begins with `is_gap=false` while coverage is certain. Once a subscription send is
attempted, delivery may be ambiguous; a later retryable disconnect, send/receive/subscription timeout
or heartbeat failure makes gap state sticky even without an acknowledgement. A connection failure
before any send attempt does not. Resubscription and acknowledgements never clear the flag.
Hyperliquid does not provide a documented public-trade replay boundary that proves complete
recovery, so only a future explicit backfill/reconciliation mechanism may clear it.

One logical consumer drains already queued immutable batches before observing a sanitized terminal
outcome. Health exposes only a bounded last-failure category. Neither consumer termination nor
health stores raw frames, exception objects, users, hashes or close reasons. Dependency-level
WebSocket frame logging uses an isolated disabled logger and remains suppressed even when
application-wide or root DEBUG logging is enabled. The collector has no private stream, credential,
wallet, order, HTTP metadata, storage or trading capability.

## Security boundaries

- Bitvavo/Kraken live keys: view/trade only; withdrawals disabled; IP allowlist where supported.
- Hyperliquid: dedicated agent/API wallet; master wallet key never on TrueNAS.
- Kraken MCP: safe services only during research and paper phases.
- LLMs never hold unrestricted withdrawal/funding permissions.
- Every live venue has an independent kill switch and a platform-wide halt.

## Documentation and UI requirements

The cockpit and Grafana must show venue explicitly for every balance, order, fill, position, fee and PnL component.

Required views include:

- balances by venue and quote currency;
- exposure and counterparty concentration by venue;
- EUR, USDC and USD-equivalent treasury exposure;
- venue health and data freshness;
- route selection and rejected alternatives;
- execution quality by venue;
- cross-venue basis and spread;
- reconciliation status;
- fee and conversion-cost attribution.

## Official references

- Hyperliquid WebSocket subscriptions: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
- Hyperliquid WebSocket lifecycle: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket
- Hyperliquid timeouts and heartbeats: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/timeouts-and-heartbeats
- Hyperliquid rate limits: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits
- Hyperliquid notation: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/notation
- Hyperliquid asset IDs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids
- Bitvavo Create Order API: https://docs.bitvavo.com/docs/rest-api/create-order/
- Bitvavo Get Markets API: https://docs.bitvavo.com/docs/rest-api/get-markets/
- Bitvavo fee schedule: https://bitvavo.com/en/fees
- Kraken MCP: https://docs.kraken.com/home/mcp
- Kraken CLI and paper trading: https://docs.kraken.com/home/cli
