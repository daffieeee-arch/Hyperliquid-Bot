# Architecture Decision Register

This register records the current high-level decisions and the conditions under which they should be revisited.

## ADR-001 — Python for the quantitative/trading core

**Decision:** Use Python for market-data adapters, research, backtesting, strategies, portfolio/risk and initial execution.

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

**Decision:** The Hyperliquid master wallet seed/private key never resides on the trading host. Dedicated Hyperliquid agent/API wallets are used for automation. Centralized-exchange credentials use minimum permissions with withdrawals disabled. Browser code never receives signing keys.

**Why:** Minimize blast radius and make the production security boundary explicit from day one.

---

## ADR-012 — Keep infrastructure lean on TrueNAS

**Decision:** Start with ClickHouse + PostgreSQL + Redis + Grafana/Alloy + Python services + Next.js. Do not add Kafka, Kubernetes, Elasticsearch/OpenSearch or Spark without a demonstrated bottleneck.

**Why:** Preserve RAM, operational simplicity and reliability on the current 64 GB ECC TrueNAS host.

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

**Why:** The cheapest route can vary among direct EUR trading, conversion to USDC, and use of existing USDC inventory. Repeated automatic conversion can create unnecessary fees, spread, FX exposure and stablecoin risk.

**Required controls:**

- dynamically discovered markets and fee tiers;
- all-in route-cost comparison;
- target/min/max EUR and USDC balances;
- per-order and daily conversion limits;
- stale-data and slippage guards;
- optional human approval for large conversions;
- explicit EUR/USD and USDC risk reporting;
- complete conversion audit trail.
