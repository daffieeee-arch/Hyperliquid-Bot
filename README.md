# Hyperliquid Quant Trading Platform

Multi-strategy crypto quantitative research and trading platform. The GitHub
repository is public; trading credentials and durable research data stay outside Git.

Primary development has moved to an **Ubuntu VM on TrueNAS SCALE**
(operator-reported host placement; observed Ubuntu **26.04.1 LTS**, KVM).
The source checkout is `$HOME/coding-projects/Hyperliquid-Bot`. GitHub Actions
validates source; release-image construction is still planned. **ADR-025**
records this development move without approving a migrated PAPER runtime.
Capture data, historical archives and continuous services have not been
verified on the VM. See the [migration status](docs/runbooks/ubuntu-vm-migration-status.md).

> **Current stage:** research/PAPER engineering; development migration verification<br>
> **Default trading mode:** PAPER<br>
> **Live capital:** Disabled by design until explicit promotion gates are met<br>
> **Primary development host:** Ubuntu VM on TrueNAS SCALE (**ADR-025**)<br>
> **24/7 runtime boundary:** Host-neutral Linux/amd64 OCI<br>
> **PAPER/capture migration:** unverified; Netcup profile retained as historical reference (**ADR-024**)<br>
> **Secondary operator environment:** TerraPC/WSL2

The current delivery priority is one credentialless Hyperliquid BTC perpetual path from a bounded,
deterministic replay through strategy, risk and PAPER execution to reproducible orders, fills,
position and PnL. Further expansion of the dormant provenance/v3 path is deferred without deleting
or weakening it. Existing trading engines are evaluated before the project builds backtesting,
paper execution, order management or reconciliation infrastructure itself. NautilusTrader is wrapped
only for the bounded COURSE-1 proof; production-core adoption has not been approved. See [Architecture](docs/ARCHITECTURE.md) and [Roadmap](docs/ROADMAP.md).

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
- Keep interactive source work separate from running service containers and
  persistent runtime volumes, even when they share a host.
- Build once in CI and promote the same image artifact through paper, shadow and live stages.
- The same strategy and risk code path is used in backtest, paper, shadow, testnet and live modes.
- Leverage is a consequence of risk-based position sizing, never the source of the edge.
- No martingale, uncontrolled averaging down or discretionary LLM order placement.
- Every trade must be explainable and reproducible from point-in-time data.
- Master-wallet secrets never live on the development workstation or in the repository.
- Human approval is required to promote research into capital-bearing live operation.

## Environment model

```text
Ubuntu VM on TrueNAS SCALE (development)
├── source: $HOME/coding-projects/Hyperliquid-Bot
├── feature branches / isolated worktrees
├── pinned Python + TypeScript toolchains
└── unit/integration tests
             │
             ▼
       public GitHub repository
    pull request + independent review + CI
             │
             ▼
      planned release-image pipeline
       pinned tag + image digest
             │
             ▼
  separately approved Linux/amd64 OCI runtime
      PAPER first; deployment not verified
```

TerraPC/WSL2 remains a secondary operator environment. Source synchronization
does not prove that captures, historical datasets or runtime services moved.

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
| Local development | Ubuntu VM on TrueNAS SCALE; TerraPC/WSL2 secondary |
| Runtime | Host-neutral Linux/amd64 OCI; VM capture/runtime migration unverified (ADR-025) |
| CI/CD | GitHub Actions checks; GHCR release pipeline planned |

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

- Initial days: repository bootstrap, CI and disposable development services.
- Current: bounded BTC-PERP replay/PAPER proofs, a PAPER-only strategy/risk engine, research harness and operator cockpit. PAPER simulation remains distinct from venue reconciliation and strategy promotion.
- Current operations: **ADR-025** records the Ubuntu VM development move.
  Confirm durable data locations, catalog bindings and runtime ownership before
  running research or capture. **ADR-024** and VPS runbooks remain historical
  references; they do not approve deployment on the VM.
- Week 3-6: robust research, validation, execution simulation and paper-vs-backtest comparison.
- Week 6-12: production-grade recovery, reconciliation, security and live-readiness work while paper evidence accumulates.
- Following weeks: shadow and very-small-capital validation before any material live allocation.

These are planning ranges, not promises of profitability.

## Documentation

- [Product Vision](docs/PRODUCT_VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Development Workflow](docs/DEVELOPMENT.md)
- [GitHub workflow and independent review](docs/GITHUB_WORKFLOW.md)
- [Ubuntu VM migration status](docs/runbooks/ubuntu-vm-migration-status.md)
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
- [Historical Linux VPS reference profile](docs/runbooks/linux-vps-reference-profile.md)
- [PAPER-only control-service health/readiness](docs/runbooks/control-service-local.md)
- [Architecture Decision Records](docs/DECISIONS/README.md)

## Important

This repository is a research and engineering project. No strategy described here is assumed profitable until it survives realistic costs, point-in-time data controls, out-of-sample validation and live-market paper/shadow testing.
