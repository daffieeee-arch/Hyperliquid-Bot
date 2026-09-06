# Hyperliquid Quant Trading Platform

Private multi-strategy crypto quantitative research and trading platform.

The project is developed on a Windows 11 workstation through **WSL2 + Codex**, validated in GitHub Actions and packaged as immutable Linux/amd64 OCI images. The 24/7 architecture is host-neutral; a supported Ubuntu LTS VPS is the intended primary deployment profile, while the existing TrueNAS SCALE environment remains optional.

> **Current stage:** COURSE-1 core-engine fit gate, BTC-PERP vertical-slice planning, and first PAPER cockpit screen<br>
> **Default trading mode:** PAPER<br>
> **Live capital:** Disabled by design until explicit promotion gates are met<br>
> **Primary development host:** Windows 11 + WSL2<br>
> **24/7 runtime boundary:** Host-neutral Linux/amd64 OCI<br>
> **Intended primary deployment profile:** Supported Ubuntu LTS VPS, after the local slice<br>
> **Optional existing profile:** TrueNAS SCALE

The current delivery priority is one credentialless Hyperliquid BTC perpetual path from a bounded,
deterministic replay through strategy, risk and PAPER execution to reproducible orders, fills,
position and PnL. Further expansion of the dormant provenance/v3 path is deferred without deleting
or weakening it. Existing trading engines are evaluated before the project builds backtesting,
paper execution, order management or reconciliation infrastructure itself; no external engine has
yet been adopted. See [Architecture](docs/ARCHITECTURE.md) and [Roadmap](docs/ROADMAP.md).

## Mission

Build a professional, evidence-driven crypto trading platform that searches for multiple independent sources of edge, validates them out-of-sample, allocates capital only to strategies with current statistical support, and reduces or quarantines strategies whose edge degrades.

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
- Develop away from the 24/7 runtime host.
- Build once in CI and promote the same image artifact through paper, shadow and live stages.
- The same strategy and risk code path is used in backtest, paper, shadow, testnet and live modes.
- Leverage is a consequence of risk-based position sizing, never the source of the edge.
- No martingale, uncontrolled averaging down or discretionary LLM order placement.
- Every trade must be explainable and reproducible from point-in-time data.
- Master-wallet secrets never live on the development workstation or in the repository.
- Human approval is required to promote research into capital-bearing live operation.

## Environment model

```text
Windows 11 workstation
└── WSL2 Ubuntu
    ├── Codex / ChatGPT desktop
    ├── source repository
    ├── Python + TypeScript toolchains
    ├── disposable local Docker stack
    └── unit/integration tests
             │
             ▼
          GitHub
    pull request + CI
             │
             ▼
     private GHCR images
   pinned tag + image digest
             │
             ▼
 Linux/amd64 OCI runtime
    Ubuntu LTS VPS primary profile
    TrueNAS optional existing profile
       PAPER / SHADOW / LIVE
```

The Windows computer is a development workstation, not a trading server. It may be powered off without interrupting services on the independently operated 24/7 runtime.

See [Development Workflow](docs/DEVELOPMENT.md) and [Deployment & Environments](docs/DEPLOYMENT.md).

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
| Python dependency management | `uv` with a committed lockfile |
| Backend API | FastAPI / Python |
| Trading cockpit | TypeScript + React + Next.js |
| JavaScript package management | `pnpm` with a committed lockfile |
| Analytical time-series store | ClickHouse |
| Configuration / control state | PostgreSQL when the control plane requires it |
| Realtime state / event distribution | Redis when multi-process realtime state requires it |
| Observability | Grafana + Grafana Alloy / OpenTelemetry |
| Local development | Windows 11 + WSL2 + Docker Desktop |
| Runtime | Host-neutral Linux/amd64 OCI; Ubuntu LTS VPS intended primary profile |
| CI/CD | GitHub Actions + private GitHub Container Registry |

Redis and PostgreSQL remain planned platform components, but they are not mandatory blockers for the first thin vertical slice.

See [Architecture](docs/ARCHITECTURE.md) and [Technology Decisions](docs/DECISIONS/).

## Planned repository layout

```text
apps/
  cockpit/              # Next.js / React terminal
  api/                  # FastAPI control gateway
  collector/            # realtime market-data collectors
  trader/               # paper/shadow/live execution service
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
  dev/                  # disposable local compose stack
  images/               # Dockerfiles/build targets
  truenas/              # optional existing TrueNAS profile
  clickhouse/
  postgres/
  redis/
  grafana/
  alloy/
docs/
  ...
```

## Strategy promotion path

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

Software artifacts have a separate promotion path:

```text
LOCAL DEV -> PR -> CI -> IMAGE -> APPROVED RUNTIME PAPER -> SHADOW -> LIVE
```

No stage may be skipped merely because an in-sample backtest looks attractive or because a build passed CI. See [Research Method](docs/RESEARCH_METHOD.md), [Risk Management](docs/RISK_MANAGEMENT.md), [Paper to Live](docs/PAPER_TO_LIVE.md), and [Deployment](docs/DEPLOYMENT.md).

## Initial delivery expectation

The aim is to get useful output early rather than disappear into a months-long build:

- Initial days: WSL2/Codex workspace, repository bootstrap, CI and local disposable services.
- Current: time-boxed core-engine fit gate, then one local BTC-PERP replay-to-PAPER slice. A first PAPER cockpit screen now reads reconstructable COURSE-1 JSON plus public BTC-PERP mid, with a compact DESK banner and fail-closed MARKETS panel; RISK is not built.
- After the local vertical slice: record the runtime ADR, migrate to the approved Ubuntu LTS VPS profile and begin 24/7 public-data collection and paper trading; retain TrueNAS only where explicitly chosen.
- Week 3-6: robust research, validation, execution simulation and paper-vs-backtest comparison.
- Week 6-12: production-grade recovery, reconciliation, security and live-readiness work while paper evidence accumulates.
- Following weeks: shadow and very-small-capital validation before any material live allocation.

These are planning ranges, not promises of profitability.

## Documentation

- [Product Vision](docs/PRODUCT_VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Development Workflow](docs/DEVELOPMENT.md)
- [Deployment & Environments](docs/DEPLOYMENT.md)
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
- [Hardware & Runtime Hosts](docs/HARDWARE.md)
- [Linux VPS reference profile (PAPER capture target)](docs/runbooks/linux-vps-reference-profile.md)
- [Architecture Decision Records](docs/DECISIONS/README.md)

## Important

This repository is a research and engineering project. No strategy described here is assumed profitable until it survives realistic costs, point-in-time data controls, out-of-sample validation and live-market paper/shadow testing.
