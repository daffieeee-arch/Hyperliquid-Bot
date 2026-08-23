# Architecture Decision Register

This register records the current high-level decisions and the conditions under which they should be revisited.

## ADR-001 — Python for the quantitative/trading core

**Decision:** Use Python for market-data adapters, research, backtesting, strategies, portfolio/risk and initial Hyperliquid execution.

**Why:** Strong quantitative ecosystem, rapid iteration, official Hyperliquid Python SDK, and sufficient performance for the initial seconds-to-days strategy horizons.

**Alternative considered:** Rust/C++/Go/TypeScript for the full core.

**Revisit when:** profiling shows a specific Python path is an economically meaningful latency bottleneck. Prefer replacing that isolated path with Rust rather than rewriting the platform.

---

## ADR-002 — TypeScript + React + Next.js for the trading cockpit

**Decision:** Use TypeScript, React and Next.js for the professional operator UI.

**Why:** Best fit for a dense realtime application with synchronized panels, keyboard workflows, multi-monitor layouts, advanced tables/charts and long-term maintainability.

**Alternatives considered:** Streamlit/Dash for production UI.

**Revisit when:** only if the product requirements materially change. Python UI frameworks remain suitable for temporary research tools but not the primary terminal.

---

## ADR-003 — ClickHouse for analytical/time-series data

**Decision:** ClickHouse is the primary analytical store for market, feature, signal, fill, portfolio and backtest time series.

**Why:** Columnar analytical performance, compression and suitability for large append-heavy datasets; integrates well with Grafana.

**Alternative considered:** PostgreSQL as the only database.

**Revisit when:** a workload is transactional rather than analytical; that state belongs in PostgreSQL instead.

---

## ADR-004 — PostgreSQL for control/configuration state

**Decision:** PostgreSQL stores strategy registry, model metadata, risk policies, operator/configuration state, experiment metadata and approvals.

**Why:** These are durable relational/transactional concerns, distinct from analytical market data.

---

## ADR-005 — Redis for bounded realtime state/events

**Decision:** Redis provides fast current-state caches and lightweight event/stream distribution.

**Why:** Low-latency UI/service communication and decoupling.

**Constraint:** Redis is not the authoritative long-term market-data or execution ledger. Enforce memory limits.

---

## ADR-006 — Hyperliquid as initial primary execution venue

**Decision:** Hyperliquid is the first execution venue, while data architecture remains multi-venue.

**Why:** Strong automation support, public market data, spot/perpetual products and suitable API/agent-wallet model.

**Constraint:** Strategy interfaces must not become vendor-locked; execution and data are abstracted.

---

## ADR-007 — Free market data first

**Decision:** Use Hyperliquid, Binance and other useful free sources plus our own collector before purchasing institutional data.

**Why:** The first candidate edges can be researched without expensive subscriptions. Paid data does not guarantee alpha.

**Revisit when:** a specific missing dataset limits a promising hypothesis or fill model. Any paid feed should show measurable incremental out-of-sample value or substantial research-time savings.

---

## ADR-008 — Same strategy/risk path from paper to live

**Decision:** BACKTEST/PAPER/SHADOW/TESTNET/LIVE differ through execution adapters, not separate strategy implementations.

**Why:** Prevents paper/live behavioral drift and reduces the chance of introducing untested logic at promotion time.

---

## ADR-009 — Research plane separated from trading plane

**Decision:** Resource-intensive research jobs cannot share failure fate or unrestricted resources with realtime collectors/risk/execution.

**Why:** A runaway backtest must never impair live risk management or data ingestion.

---

## ADR-010 — AI assists research; it does not bypass governance

**Decision:** Hermes/LLMs may generate hypotheses, code, experiments, anomaly analysis and reports. They may not directly self-promote new strategies into live capital or override hard risk limits.

**Why:** Avoid uncontrolled online overfitting and preserve reproducibility/accountability.

---

## ADR-011 — Secrets isolated from frontend and master wallet

**Decision:** The master wallet seed/private key never resides on the trading host. Dedicated Hyperliquid agent/API wallets are used for automation. Browser code never receives signing keys.

**Why:** Minimize blast radius and make the production security boundary explicit from day one.

---

## ADR-012 — Keep infrastructure lean on TrueNAS

**Decision:** Start with ClickHouse + PostgreSQL + Redis + Grafana/Alloy + Python services + Next.js. Do not add Kafka, Kubernetes, Elasticsearch/OpenSearch or Spark without a demonstrated bottleneck.

**Why:** Preserve RAM, operational simplicity and reliability on the current 64 GB ECC TrueNAS host.