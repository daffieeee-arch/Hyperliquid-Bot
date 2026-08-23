# Hyperliquid Quant Trading Platform

Private multi-strategy crypto quantitative research and trading platform, designed for research-first development on TrueNAS SCALE and eventual controlled live execution. Hyperliquid is the initial primary derivatives venue, while Bitvavo and Kraken are incorporated selectively through a phased multi-venue architecture.

> **Current stage:** Architecture / Research Foundation  
> **Default trading mode:** PAPER  
> **Live capital:** Disabled by design until explicit promotion gates are met.

## Mission

Build a professional, evidence-driven crypto trading platform that searches for multiple independent sources of edge, validates them out-of-sample, allocates capital only to strategies with current statistical support, and automatically reduces or quarantines strategies whose edge degrades.

The project is deliberately **not** a single indicator bot. Initial research tracks run in parallel:

1. Spot / cash momentum
2. Long-short perpetual momentum
3. Market-neutral basis & carry
4. Relative-strength / hedged portfolios
5. Order-flow and microstructure research

See [Product Vision](docs/PRODUCT_VISION.md) and [Strategies](docs/STRATEGIES.md).

## Core design principles

- Research first; production trading second.
- Multiple independent strategies instead of one monolithic model.
- Free market data first. Paid data is considered only after measured incremental value.
- **Observe many venues; trade on few venues.**
- The same strategy and risk code path is used in paper, shadow, testnet and live modes.
- Leverage is a consequence of risk-based position sizing, never the source of the edge.
- No martingale, uncontrolled averaging down or discretionary LLM order placement.
- Every trade must be explainable and reproducible from point-in-time data.
- Master-wallet secrets never live on the trading server.
- Human approval is required to promote research into capital-bearing live operation.

## Initial venue roles

| Venue | Initial role |
|---|---|
| Hyperliquid | Perpetuals, basis/carry and public market data |
| Bitvavo | EUR on-ramp, spot data and candidate first small-live spot venue |
| Kraken | Public data, paper/MCP research and optional future hedge/backup venue |
| Binance | Public reference data only initially |

See [Venue Strategy](docs/VENUES.md).

## Technology direction

| Layer | Choice |
|---|---|
| Quant research / strategies / risk / execution | Python |
| Backend API | FastAPI / Python |
| Trading cockpit | TypeScript + React + Next.js |
| Analytical time-series store | ClickHouse |
| Configuration / control state | PostgreSQL |
| Realtime state / event distribution | Redis |
| Observability | Grafana + Grafana Alloy / OpenTelemetry |
| Runtime | Docker Compose / TrueNAS SCALE Custom Apps |
| CI/CD | GitHub Actions |

See [Architecture](docs/ARCHITECTURE.md) and [Technology Decisions](docs/DECISIONS/).

## Planned repository layout

```text
apps/
  cockpit/              # Next.js / React terminal
  api/                  # FastAPI control gateway
  collector/            # realtime market-data collectors
  trader/               # execution service
  research-worker/      # isolated research jobs
quant/
  strategies/
  features/
  portfolio/
  risk/
  execution/
  treasury/
  backtest/
  validation/
data/
  adapters/
  schemas/
  instruments/
infra/
  clickhouse/
  postgres/
  redis/
  grafana/
  alloy/
docs/
  ...
```

## Development path

```text
RESEARCH
   ↓
BACKTEST
   ↓
WALK-FORWARD / UNTOUCHED OOS
   ↓
PAPER
   ↓
SHADOW
   ↓
TESTNET
   ↓
SMALL LIVE
   ↓
PRODUCTION
```

No stage may be skipped merely because an in-sample backtest looks attractive. See [Research Method](docs/RESEARCH_METHOD.md), [Risk Management](docs/RISK_MANAGEMENT.md), and [Paper to Live](docs/PAPER_TO_LIVE.md).

## Initial delivery expectation

The aim is to get useful output early rather than disappear into a months-long build:

- Week 1-2: data ingestion, ClickHouse, initial Grafana dashboards, basic cockpit shell, paper broker, first strategy baselines.
- Week 3-6: robust research, validation, live market-data paper trading and execution simulation.
- Week 6-12: production-grade execution, recovery, reconciliation, security and live-readiness work.
- Following weeks: shadow and very-small-capital validation before any material live allocation.

These are planning ranges, not promises of profitability.

## Documentation

- [Product Vision](docs/PRODUCT_VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Venue Strategy](docs/VENUES.md)
- [Roadmap](docs/ROADMAP.md)
- [Strategies](docs/STRATEGIES.md)
- [Research Method](docs/RESEARCH_METHOD.md)
- [Market Data](docs/DATA.md)
- [Risk Management](docs/RISK_MANAGEMENT.md)
- [Execution](docs/EXECUTION.md)
- [Paper to Live](docs/PAPER_TO_LIVE.md)
- [Frontend](docs/FRONTEND.md)
- [Grafana & Observability](docs/GRAFANA.md)
- [Security](docs/SECURITY.md)
- [Hardware & TrueNAS](docs/HARDWARE.md)
- [Architecture Decision Records](docs/DECISIONS/README.md)

## Important

This repository is a research and engineering project. No strategy described here is assumed profitable until it survives realistic costs, point-in-time data controls, out-of-sample validation and live-market paper/shadow testing.
