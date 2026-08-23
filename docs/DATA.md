# Market Data Plan

## Policy

Phase 1 is **free-data-first**. We do not buy data simply because professional firms use expensive feeds. Paid data is introduced only when free data creates a measurable research or execution bottleneck.

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
- account/order/fill streams for our own accounts.

Use official historical datasets where practical, while explicitly validating gaps, timestamp semantics and schema changes.

### Binance
Use public historical/realtime data as an external reference for:

- spot prices;
- perpetual prices;
- trades;
- candles;
- funding;
- cross-exchange basis;
- lead/lag research.

### Other free venues
Bybit, OKX, Coinbase or Deribit may be added where a specific hypothesis requires them. Avoid collecting everything simply because it exists.

## Self-collected dataset

A realtime collector should run from the beginning and persist data to ClickHouse. This creates a dataset with the same receipt path and timestamp discipline the future live bot will use.

Recommended derived schema per symbol/time bucket includes:

```text
timestamp
exchange
symbol
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
```

Cross-exchange features may add:

```text
hl_vs_binance_spot_basis_bps
hl_vs_binance_perp_basis_bps
lead_lag_features
funding_differential
volume_share
```

## Data quality controls

Every ingestion path should expose:

- last event timestamp;
- local receive timestamp;
- sequence/gap detection where supported;
- duplicate count;
- reconnect count;
- stale state;
- null rate;
- schema/version metadata.

Data quality must itself be observable in Grafana and able to block trading.

## Point-in-time metadata

Maintain historical metadata for:

- listing/delisting;
- tick size;
- size precision;
- fee schedule where relevant;
- symbol mapping;
- contract specification.

Do not backfill current market metadata into historical periods without evidence it was valid then.

## Storage tiers

Use SSD-backed storage for:

- active ClickHouse partitions;
- recent raw feed data;
- features;
- live execution data;
- Redis/PostgreSQL.

Move large cold archives and old raw market data to the HDD pool according to retention policy.

## Paid data decision rule

A future paid provider must solve an explicit problem, for example:

- insufficient historical L2 depth;
- inability to reconstruct realistic fills;
- missing cross-exchange derivatives history;
- missing options/on-chain feature history.

The paid feature set should then be evaluated against a no-paid-data baseline. A subscription is justified only if it materially improves out-of-sample return, risk, execution quality, or research speed relative to its cost.