# Architecture Decision Register

This register records current high-level decisions and the conditions under which they should be revisited.

## ADR-001 — Python for the quantitative/trading core

**Decision:** Use Python for market-data adapters, research, backtesting, strategies, portfolio/risk and initial execution.

**Why:** Strong quantitative ecosystem, rapid iteration, official Hyperliquid Python SDK, and sufficient performance for the initial seconds-to-days strategy horizons.

**Alternative considered:** Rust/C++/Go/TypeScript for the full core.

**Revisit when:** profiling shows a specific Python path is an economically meaningful latency bottleneck. Prefer replacing that isolated path with Rust rather than rewriting the platform.

---

## ADR-002 — TypeScript + React + Next.js for the trading cockpit

**Decision:** Use TypeScript, React and Next.js for the professional operator UI.

**Why:** Best fit for a dense realtime application with synchronized panels, keyboard workflows, multi-monitor layouts, advanced tables/charts and long-term maintainability.

**Alternatives considered:** Streamlit/Dash for the production UI.

**Revisit when:** only if product requirements materially change. Python UI frameworks remain suitable for temporary research tools but not the primary terminal.

---

## ADR-003 — ClickHouse for analytical/time-series data

**Decision:** ClickHouse is the primary analytical store for market, feature, signal, fill, portfolio and backtest time series.

**Why:** Columnar analytical performance, compression and suitability for large append-heavy datasets; integrates well with Grafana.

**Alternative considered:** PostgreSQL as the only database.

**Implementation note:** use a disposable local ClickHouse container in DEV and a managed persistent instance/database on TrueNAS for 24/7 PAPER/SHADOW/LIVE data.

---

## ADR-004 — PostgreSQL for durable control/configuration state, introduced when needed

**Decision:** PostgreSQL stores strategy registry, model metadata, risk policies, operator/configuration state, experiment metadata and approvals when the durable control plane is introduced.

**Why:** These are relational/transactional concerns distinct from analytical market data.

**Constraint:** PostgreSQL is not a blocker for the first local vertical slice. Begin with explicit versioned contracts/configuration and introduce PostgreSQL before durable multi-service control state requires it.

---

## ADR-005 — Redis for bounded shared realtime state/events, introduced when needed

**Decision:** Redis provides fast current-state caches and lightweight event/stream distribution once multiple processes need shared low-latency state.

**Why:** Low-latency UI/service communication and decoupling.

**Constraint:** The first local slice may use bounded in-process queues. Redis is not the authoritative market-data or execution ledger and must use explicit memory limits.

---

## ADR-006 — Hyperliquid as primary derivatives venue

**Decision:** Hyperliquid is the initial primary venue for perpetuals and basis/carry. Bitvavo is the preferred candidate for the first very-small-capital spot deployment. The data architecture remains multi-venue.

**Why:** Hyperliquid offers strong automation support, public market data, perpetual products and a suitable API/agent-wallet model. Bitvavo provides an existing verified account, EUR funding, spot markets and useful free L2 data.

**Constraint:** Strategy interfaces must not become vendor-locked; execution and data are abstracted. Neither venue receives live capital before its own promotion gates are met.

---

## ADR-007 — Free market data first

**Decision:** Use Hyperliquid, Bitvavo, Kraken, Binance and other useful free sources plus our own collectors before purchasing institutional data.

**Why:** The first candidate edges can be researched without expensive subscriptions. Paid data does not guarantee alpha.

**Revisit when:** a specific missing dataset limits a promising hypothesis or fill model. Any paid feed should show measurable incremental out-of-sample value or substantial research-time savings.

---

## ADR-008 — Same strategy/risk path from paper to live

**Decision:** BACKTEST/PAPER/SHADOW/TESTNET/LIVE differ through execution adapters and configuration, not separate strategy implementations.

**Why:** Prevents paper/live behavioral drift and reduces the chance of introducing untested logic at promotion time.

---

## ADR-009 — Research plane separated from trading plane

**Decision:** Resource-intensive research jobs cannot share failure fate or unrestricted resources with realtime collectors/risk/execution.

**Why:** A runaway backtest must never impair continuous risk management or data ingestion.

---

## ADR-010 — AI assists research; it does not bypass governance

**Decision:** Hermes/LLMs may generate hypotheses, code, experiments, anomaly analysis and reports. They may not directly self-promote new strategies into live capital or override hard risk limits.

**Why:** Avoid uncontrolled online overfitting and preserve reproducibility/accountability.

---

## ADR-011 — Secrets isolated from frontend and master wallet

**Decision:** The Hyperliquid master-wallet seed/private key never resides on the development workstation or trading host. Dedicated Hyperliquid agent/API wallets are used for automation. Centralized-exchange credentials use minimum permissions with withdrawals disabled. Browser code never receives signing keys.

**Why:** Minimize blast radius and make the security boundary explicit from day one.

---

## ADR-012 — Keep runtime infrastructure lean on TrueNAS

**Decision:** Start with ClickHouse + Python services + Next.js + Grafana/Alloy, adding PostgreSQL and Redis when their responsibilities become necessary. Do not add Kafka, Kubernetes, Elasticsearch/OpenSearch or Spark without a demonstrated bottleneck.

**Why:** Preserve RAM, operational simplicity and reliability on the shared 64 GB TrueNAS host.

---

## ADR-013 — Observe many venues; trade on few venues

**Decision:** Public market data may be collected from multiple venues, but live execution venues are introduced one at a time through separate approval gates.

**Initial role split:**

- Hyperliquid: perpetuals, basis/carry and public data;
- Bitvavo: EUR on-ramp, spot and candidate first small-live spot venue;
- Kraken: public data, official paper/MCP research and optional future hedge/backup venue;
- Binance: public reference data initially.

**Why:** Multi-venue data improves price discovery and strategy research, while each live venue adds nonlinear operational, reconciliation, security and counterparty complexity.

**Promotion requirement:** A new live venue must demonstrate measurable benefit such as lower all-in costs, better liquidity, a required hedge, profitable cross-venue functionality or meaningful operational redundancy.

---

## ADR-014 — EUR/USDC conversion belongs to treasury routing

**Decision:** EUR-to-USDC conversion may be automated through a venue adapter, including Bitvavo's `USDC-EUR` market, but conversion is controlled by a dedicated treasury/quote-asset policy rather than individual strategy code.

**Why:** The cheapest route can vary among direct EUR trading, conversion to USDC, and use of existing USDC inventory. Repeated conversion can create unnecessary fees, spread, FX exposure and stablecoin risk.

**Required controls:**

- dynamically discovered markets and fee tiers;
- all-in route-cost comparison;
- target/min/max EUR and USDC balances;
- per-order and daily conversion limits;
- stale-data and slippage guards;
- optional human approval for large conversions;
- explicit EUR/USD and USDC risk reporting;
- complete conversion audit trail.

---

## ADR-015 — Windows 11 + WSL2 is the primary development environment

**Decision:** Develop the project on the Windows workstation using WSL2 Ubuntu, with Codex configured to run in WSL. Keep the repository inside the WSL Linux filesystem.

**Why:** Provides Linux parity, fast local iteration, strong Codex/desktop/mobile Remote workflows, better frontend/debugging ergonomics and access to the workstation's CPU/GPU without destabilizing TrueNAS.

**Alternatives considered:** direct source development on TrueNAS; native Windows/PowerShell as the primary runtime; source on an SMB share.

**Constraints:** no normal live trading credentials on the workstation; use workspace-scoped Codex permissions; store source under the Linux home filesystem rather than `/mnt/c` or SMB.

---

## ADR-016 — TrueNAS is a deployment/runtime host, not the interactive development host

**Decision:** TrueNAS runs CI-built container images for 24/7 PAPER/SHADOW/LIVE services. Do not run production-like services from a mutable repository checkout and do not hot-edit running containers.

**Why:** Isolates experimentation from persistent services, reduces failure blast radius, enables deterministic rollback and keeps the Windows PC optional during continuous operation.

**Constraint:** Hermes/TrueNAS MCP may operate deployments and project resources, but source changes return through Git and CI.

---

## ADR-017 — Build once in CI and deploy by immutable image digest

**Decision:** GitHub Actions builds release images, publishes them to private GHCR and records source commit, tag and digest. TrueNAS deploys a reviewed digest, never a floating `latest` tag.

**Why:** Reproducibility, supply-chain traceability, reliable rollback and confidence that PAPER/SHADOW/LIVE can use the same artifact.

**Constraint:** ordinary merge does not automatically deploy. PAPER deployment initially requires explicit approval; LIVE always remains protected.

---

## ADR-018 — Separate software promotion from strategy promotion

**Decision:** A strategy and a software artifact each pass their own gates. Live eligibility requires an approved strategy/version, approved image digest, approved configuration and approved runtime environment.

**Why:** A profitable model can run on broken software, while perfect software can execute an unprofitable model. Combining the approvals obscures risk.

---

## ADR-019 — Local development uses disposable data; TrueNAS owns continuous history

**Decision:** DEV uses synthetic fixtures, deterministic replay and bounded data exports in disposable local services. TrueNAS holds the authoritative self-collected 24/7 dataset.

**Why:** Keeps development fast/reproducible and prevents accidental mutation or copying of large production-like datasets.

**Constraint:** Windows may query approved TrueNAS data read-only or receive bounded exports; it does not directly mutate the runtime database.

---

## ADR-020 — Maximum useful MCP capability with scoped standing privilege

**Decision:** Do not make all MCPs globally read-only. Give Hermes the capabilities required to operate the project, while separating routine permissions from destructive/admin permissions.

**Examples:**

- Grafana MCP may edit Hyperliquid dashboards/alerts, while its ClickHouse datasource stays read-only;
- TrueNAS MCP may inspect and operate project apps/datasets, while destructive pool/dataset/update/reboot actions require explicit approval;
- ClickHouse research is read-only, while migrations use a separate controlled identity;
- paper trading tools may be available, while withdrawal/transfer tools are never exposed.

**Why:** Enables meaningful autonomy without giving every agent permanent, unrelated, high-blast-radius authority.

---

## ADR-021 — Early-release TrueNAS is PAPER-only by default

**Decision:** TrueNAS 26 BETA.3 may host research and PAPER services with backups and monitoring. Material live capital should use a stable runtime release or require explicit documented risk acceptance and repeated soak/recovery testing.

**Why:** Operating-system maturity is part of execution risk, not merely infrastructure preference.
