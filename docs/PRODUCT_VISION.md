# Product Vision

## What we are building

A professional crypto quantitative research and trading platform centered on Hyperliquid execution, but informed by multiple free market-data sources.

The platform is designed to behave more like a small systematic trading desk than a single retail bot. It should continuously test multiple independent sources of edge, measure whether those edges survive realistic costs, allocate capital according to current evidence, and reduce or quarantine strategies whose live behavior diverges from their validated distribution.

Development happens on a Windows 11 workstation through WSL2 and Codex. Tested, versioned Linux/amd64 OCI images run on an independent host-neutral runtime. A supported Ubuntu LTS VPS is the definitive primary PAPER profile (**ADR-024**); provisioning and TerraPC cutover remain separate CoS steps. TrueNAS remains an optional existing protected profile.

## Strategic objective

The objective is not to maximize the number of trades or chase a fixed percentage target. The objective is to maximize **risk-adjusted, after-cost expected return** while keeping drawdowns, concentration, leverage, operational risk and model risk within explicit limits.

## Initial alpha engines

### 1. Spot Momentum

Hold high-relative-strength spot assets for hours to days, allowing exceptional winners to run while controlling downside with volatility-aware exits.

### 2. Perpetual Momentum

Long or short liquid perpetual markets depending on regime and momentum. Leverage is determined by risk sizing and volatility, not chosen as a fixed multiplier.

### 3. Basis & Carry

Market-neutral or low-beta relative-value trades using spot/perpetual or cross-venue price dislocations, funding and basis convergence.

### 4. Relative Strength

Long stronger assets and hedge broad market beta with BTC/ETH or weaker assets where appropriate.

### 5. Order Flow / Microstructure

Use spreads, depth, imbalance, aggressive flow and cross-exchange lead/lag primarily to improve timing and execution; only promote to standalone alpha if it proves independently robust.

## Professional operating model

The platform has six logical planes:

- **Data Plane** — collection, normalization and point-in-time storage.
- **Research Plane** — hypotheses, backtests, validation and experiment registry.
- **Trading Plane** — signals, portfolio construction, risk and execution.
- **Control Plane** — configuration, strategy lifecycle, permissions and operator actions.
- **Observability Plane** — Grafana, traces, logs, alerts and forensic reconstruction.
- **Build & Deployment Plane** — Windows/WSL2 development, GitHub CI, private images and approved-runtime promotion/rollback.

The build/deployment plane ensures that source changes do not directly affect 24/7 services. PAPER, SHADOW and LIVE are distinct runtime environments with separate configuration, secrets and approval gates.

## What self-learning means here

The platform may adapt, but not by allowing an LLM to rewrite live strategy parameters after a few losses.

Safe adaptation includes:

- volatility-aware position sizing;
- dynamic exposure limits;
- strategy allocation based on rolling evidence;
- regime-dependent strategy activation;
- execution model calibration;
- strategy health monitoring and automatic quarantine.

AI/Hermes/Codex can assist research and engineering by proposing hypotheses, implementing code, running experiments, comparing models and producing reports. A newly discovered model may not directly enter live trading. Promotion always follows both strategy and software-artifact gates.

## Free-data-first policy

The first research stage uses free sources only:

- Hyperliquid public market data and historical data where available;
- Binance public historical/realtime reference data;
- Bitvavo public EUR/USDC spot and L2 data;
- Kraken public market data and paper/MCP capabilities;
- other free exchange data where a specific hypothesis requires it;
- self-collected WebSocket data stored in ClickHouse.

Paid data is only considered when an explicit research bottleneck exists and incremental value can be measured against the free-data baseline.

## Engineering objective

The platform must be reproducible and operable, not merely correct on one computer.

Success requires:

- source controlled in GitHub;
- deterministic local and CI tests;
- version-pinned dependencies;
- immutable container artifacts;
- digest-pinned host-neutral Linux/OCI deployments;
- deployment health checks and rollback;
- no dependence on the Windows PC for 24/7 operation;
- every trade attributable to strategy, configuration, commit and image digest.

## Success criteria

Success is not a pretty backtest. A strategy is interesting only when it demonstrates:

- positive after-cost expectancy;
- robustness across nearby parameters;
- stability across multiple assets and regimes;
- acceptable drawdown;
- out-of-sample persistence;
- realistic fill assumptions;
- stress resilience when costs are increased;
- consistent paper/shadow behavior relative to simulation;
- stable operation on the runtime host.

## Non-goals

We are not building:

- a martingale bot;
- a fixed-leverage gambler;
- an LLM that directly decides BUY/SELL in production;
- a strategy that depends on hidden look-ahead information;
- a system that automatically promotes new research into live capital;
- an HFT system requiring microsecond co-location as the first objective;
- a production system that runs from a mutable source checkout on any runtime host.
