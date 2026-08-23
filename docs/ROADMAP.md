# Roadmap

## Phase 0 — Blueprint and repository foundation

Status: **in progress**

Deliverables:
- architecture and product documentation;
- repository structure;
- technology decisions;
- initial CI skeleton;
- risk/security principles;
- roadmap and research methodology.

## Phase 1 — Data and platform foundation

Target: useful output within the first 1-2 weeks of active development.

Deliverables:
- Hyperliquid realtime collector;
- Binance reference-data adapter;
- ClickHouse schemas;
- Redis realtime state/event layer;
- PostgreSQL control/config database;
- FastAPI service shell;
- Grafana provisioning;
- first system/market dashboards;
- basic Next.js cockpit shell;
- paper broker interface.

## Phase 2 — First alpha engines

Target: begin parallel research quickly rather than wait for the whole platform.

Deliverables:
- spot momentum baseline;
- perpetual momentum baseline;
- basis/carry baseline;
- realistic fee/funding model;
- basic slippage model;
- event-driven backtest framework;
- experiment registry.

## Phase 3 — Robust validation and live-market paper trading

Approximate active-development window: weeks 3-6.

Deliverables:
- walk-forward validation;
- untouched OOS evaluation;
- cost stress testing;
- Monte Carlo/bootstrap analysis;
- point-in-time universe handling;
- PAPER mode fed by live market data;
- strategy leaderboard;
- paper-vs-backtest comparison;
- initial risk engine.

## Phase 4 — Production-grade execution

Approximate active-development window: weeks 6-12, overlapping with paper validation.

Deliverables:
- Hyperliquid testnet/live execution adapter;
- agent-wallet integration;
- full order state machine;
- partial-fill handling;
- reconciliation;
- restart recovery;
- dead-man protection;
- kill switches;
- transaction-cost analytics;
- stronger observability/tracing.

## Phase 5 — Advanced alpha and portfolio layer

Only after the initial strategies/data path are stable.

Candidates:
- relative-strength hedging;
- cross-sectional portfolios;
- mean reversion;
- order-flow/microstructure features;
- lead/lag research;
- quarter-hour effects;
- regime classification;
- adaptive strategy allocation.

## Phase 6 — Shadow and small live

Calendar validation cannot be compressed merely by coding faster.

Deliverables:
- shadow trading with live feeds;
- testnet where useful;
- simulator-vs-observed fill comparison;
- very-small-capital live account;
- strict risk limits;
- staged allocation increases only when evidence remains consistent.

## Longer-term direction

Potential additions only when justified:
- paid L2/tick history;
- options/on-chain context;
- Rust fast paths for measured latency bottlenecks;
- more advanced portfolio optimization;
- automated research hypothesis generation;
- richer multi-monitor terminal workflows.

## Planning interpretation

The goal is not to spend months before seeing results. Data collection, dashboards and first paper strategies should become visible early, while production hardening and out-of-sample evidence accumulate in parallel.

Time ranges are engineering/research estimates, not guarantees of strategy profitability.