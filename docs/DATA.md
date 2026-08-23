# Market Data Plan

## Policy

Phase 1 is **free-data-first**. We do not buy data simply because professional firms use expensive feeds. Paid data is introduced only when free data creates a measurable research or execution bottleneck.

The platform follows a separate rule for venues:

> **Observe many venues; trade on few venues.**

Public market-data adapters can be added without granting live order permissions. See [Venue Strategy](VENUES.md).

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

Primary research uses:

- perpetual momentum;
- basis/carry;
- funding and OI features;
- cross-exchange lead/lag;
- execution and microstructure.

### Bitvavo

Use Bitvavo as a free public data source for EUR and available USDC spot markets:

- trades and candles;
- best bid/offer;
- standard L2 order book;
- Market Data Pro non-conflated L2 updates with sequence tracking;
- market metadata, precision and order capabilities;
- EUR/USDC and asset quote-route comparison.

Primary research uses:

- spot momentum;
- EUR versus USDC liquidity and all-in cost;
- Bitvavo versus Hyperliquid/Kraken/Binance lead/lag;
- spot-led versus perpetual-led moves;
- future small-live spot execution modeling.

Not every asset has a USDC market. Supported markets and precision must be discovered dynamically through the public markets endpoint.

### Kraken

Use Kraken's public market data and official paper tooling for:

- spot and derivatives reference prices;
- order books, recent trades, OHLC and spreads;
- paper spot and futures execution against live prices;
- agent-assisted research through the local MCP interface.

During the initial phase, Kraken MCP is restricted to safe services such as `market`, `account` and `paper`. Live `trade`, `futures`, `funding` and transfer capabilities are not enabled.

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

### Other free venues

Bybit, OKX, Coinbase or Deribit may be added where a specific hypothesis requires them. Avoid collecting everything simply because it exists.

## Self-collected dataset

Realtime collectors should run from the beginning and persist data to ClickHouse. This creates a dataset with the same receipt path and timestamp discipline the future live bot will use.

Recommended normalized schema per instrument/time bucket includes:

```text
timestamp
receive_timestamp
venue
canonical_instrument_id
native_symbol
instrument_type
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
```

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

## Quote currencies and conversion data

EUR, USDC and USD-equivalent values must remain distinct in raw data. Conversion into a reporting currency happens through point-in-time FX/stablecoin rates, not by assuming EUR, USD and USDC are equal.

Persist:

- executable `USDC-EUR` bid/ask and depth;
- EUR/USD or equivalent reference rate where needed;
- conversion fee schedule effective at that time;
- quote-asset inventory;
- conversion-order fills and slippage;
- USDC deviation from its reference value;
- route cost estimates and selected route.

The research simulator must be able to compare:

```text
ASSET-EUR direct
EUR -> USDC -> ASSET-USDC
existing USDC -> ASSET-USDC
```

## Data quality controls

Every ingestion path should expose:

- last exchange event timestamp;
- local receive timestamp;
- sequence/gap detection where supported;
- duplicate count;
- reconnect count;
- stale state;
- null rate;
- schema/version metadata;
- venue API status/health;
- symbol-mapping status.

Data quality must itself be observable in Grafana and able to block trading.

## Point-in-time metadata

Maintain historical metadata for:

- listing/delisting;
- venue and native symbol;
- base/quote currency;
- instrument type;
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
