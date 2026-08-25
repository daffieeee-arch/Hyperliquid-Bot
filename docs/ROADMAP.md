# Roadmap

## Delivery model

Development and runtime are deliberately separated:

- **Windows 11 + WSL2 + Codex:** source development, local tests and small research;
- **GitHub Actions:** independent CI and image construction;
- **TrueNAS SCALE:** 24/7 data collection, PAPER/SHADOW and eventual LIVE runtime.

Fases overlap by workstream. A strategy may be in PAPER while another remains in RESEARCH, and production hardening can continue while live-market paper evidence accumulates.

## Phase 0A — Blueprint and project decisions

Status: **substantially complete**

Deliverables:

- product vision;
- architecture and venue model;
- strategy/research framework;
- risk/security principles;
- frontend/Grafana vision;
- architecture decision register;
- roadmap and promotion methodology.

## Phase 0B — Windows/WSL2 development foundation

Status: **complete**

Completed through PR #2 and merge commit
`9ab3c0eb795e0ede365da7c5cabe2fe900d74518`. Python, TypeScript and secret-scanning
checks passed. Nothing was deployed and LIVE was not enabled.

Target: first development days.

Deliverables:

- current WSL2 Ubuntu environment;
- ChatGPT/Codex agent configured to run in WSL2;
- mobile Remote connectivity verified;
- repository cloned inside the WSL Linux filesystem;
- `AGENTS.md` project instructions;
- Python/Node/package versions pinned;
- Docker Desktop WSL integration verified;
- branch/PR workflow established;
- initial GitHub Actions CI skeleton;
- secret-scanning and paper-only safety baseline.

Exit gate:

- Codex can modify a feature branch, run tests and open a pull request without touching TrueNAS or using live trading credentials.

## Phase 1A — Local foundation vertical slice

Status: **started, not complete**

Target: useful local output within approximately the first 1-2 weeks of active development.

Deliverables:

- canonical venue/instrument/market-event contracts;
- strict PAPER-only startup gate;
- Hyperliquid public realtime collector;
- one external public reference adapter, initially Binance;
- bounded queues and reconnect/backpressure behavior;
- deterministic fixture capture/replay;
- disposable local ClickHouse;
- paper broker interface and append-only ledger baseline;
- risk-based position-sizing baseline;
- FastAPI health/read endpoints;
- Grafana provisioning as code;
- basic Next.js cockpit shell;
- Python/TypeScript tests and container build smoke tests.

Redis and PostgreSQL are not mandatory for this thin slice. They are introduced when multi-process shared state and durable control-plane requirements justify them.

## Phase 1B — TrueNAS 24/7 PAPER deployment

Target: approximately weeks 2-4, overlapping with completion of Phase 1A.

Deliverables:

- private GHCR image publication;
- digest-pinned TrueNAS Custom App deployment;
- separate paper datasets and configuration;
- managed ClickHouse Hyperliquid database/users;
- 24/7 public-data collection;
- paper strategy execution;
- Grafana market/system dashboards;
- cockpit access;
- resource limits, health checks and restart policies;
- deployment annotations containing commit and image digest;
- rollback to a previous known-good image;
- soak, reconnect and data-gap tests.

Exit gate:

- the Windows PC can be switched off while the TrueNAS paper environment continues collecting, paper trading and monitoring correctly.

## Phase 2 — First alpha engines

Target: begin parallel research as soon as stable contracts and replay exist.

Deliverables:

- spot momentum baseline;
- perpetual momentum baseline;
- basis/carry baseline;
- realistic fee/funding model;
- basic slippage model;
- event-driven backtest framework;
- experiment registry;
- Bitvavo public-data adapter for spot research;
- Kraken public/MCP paper integration when it serves a concrete experiment.

Possible parallel status:

| Strategy | Example status |
|---|---|
| Spot momentum | PAPER |
| Perp momentum | WALK-FORWARD |
| Basis/carry | BACKTEST |
| Relative strength | RESEARCH |
| Order flow | IDEA |

## Phase 3 — Robust validation and live-market paper trading

Approximate active-development window: weeks 3-6 and beyond as evidence accumulates.

Deliverables:

- walk-forward validation;
- untouched OOS evaluation;
- cost stress testing;
- Monte Carlo/bootstrap analysis;
- point-in-time universe handling;
- PAPER mode fed by live market data;
- strategy leaderboard;
- paper-vs-backtest comparison;
- initial portfolio/risk engine;
- why-this-trade records;
- model/strategy health and quarantine logic;
- simulator-versus-observed paper fill analysis.

## Phase 4 — Production-grade execution

Approximate active-development window: weeks 6-12, overlapping with paper validation.

Deliverables:

- Hyperliquid testnet/live execution adapter;
- agent-wallet integration isolated on the runtime host;
- full order state machine;
- partial-fill handling;
- reconciliation;
- restart recovery;
- dead-man protection;
- kill switches;
- transaction-cost analytics;
- stronger observability/tracing;
- separate SHADOW and LIVE deployments;
- protected software/deployment promotion gates;
- disaster-recovery and rollback drills.

Development still occurs on Windows/WSL2. Authenticated runtime testing occurs only in the appropriate isolated TrueNAS environment.

## Phase 5 — Advanced alpha and portfolio layer

Only after initial strategies and the data path are stable.

Candidates:

- relative-strength hedging;
- cross-sectional portfolios;
- mean reversion;
- order-flow/microstructure features;
- lead/lag research;
- quarter-hour effects;
- regime classification;
- adaptive strategy allocation;
- optional GPU-assisted model training on the Windows workstation.

## Phase 6 — Shadow and very small live

Calendar validation cannot be compressed merely by coding faster.

Deliverables:

- shadow trading with live feeds;
- testnet where useful;
- simulator-vs-observed fill comparison;
- very-small-capital live account;
- strict risk limits;
- same tested image digest promoted from paper where practical;
- separate runtime secrets and data namespaces;
- staged allocation increases only when evidence remains consistent.

Before material live capital, prefer a stable TrueNAS release or another stable isolated runtime rather than relying on an early-release operating system without explicit risk acceptance.

## Parallel workstreams

These workstreams can progress simultaneously after shared contracts are agreed:

### A. Development platform and CI

- WSL2/Codex;
- repository bootstrap;
- tests;
- image builds;
- GitHub workflows.

### B. Market data

- adapters;
- schemas;
- normalization;
- data quality;
- ClickHouse ingestion.

### C. Research and strategies

- backtester;
- costs;
- momentum;
- basis/carry;
- validation.

### D. Cockpit and Grafana

- UI shell;
- dashboard provisioning;
- venue/strategy/risk views;
- observability.

### E. Risk and execution

- paper broker;
- sizing;
- limits;
- order state machine;
- later authenticated venue adapters.

### F. Runtime and operations

- TrueNAS images/datasets;
- health/restart behavior;
- backups;
- deployment/rollback;
- MCP-assisted operations.

Cross-cutting architecture changes are coordinated centrally; workstreams may not independently replace shared contracts or core infrastructure.

## Longer-term direction

Potential additions only when justified:

- paid L2/tick history;
- options/on-chain context;
- Rust fast paths for measured latency bottlenecks;
- more advanced portfolio optimization;
- automated research hypothesis generation;
- richer multi-monitor terminal workflows;
- automated but approval-gated TrueNAS deployments.

## Planning interpretation

The goal is not to spend months before seeing results. The local vertical slice should appear quickly, followed by a 24/7 TrueNAS paper deployment built from the same repository and CI pipeline. Production hardening and out-of-sample evidence then accumulate in parallel.

Time ranges are engineering/research estimates, not guarantees of strategy profitability.
