# Product Vision

## What we are building

A professional crypto quantitative research and trading platform centered on Hyperliquid execution, but informed by multiple free market-data sources.

The platform is designed to behave more like a small systematic trading desk than a single retail bot. It should continuously test multiple independent sources of edge, measure whether those edges survive realistic costs, allocate capital according to current evidence, and reduce or quarantine strategies whose live behavior diverges from their validated distribution.

## Strategic objective

The objective is not to maximize the number of trades or to chase a fixed percentage target. The objective is to maximize **risk-adjusted, after-cost expected return** while keeping drawdowns, concentration, leverage, operational risk and model risk within explicit limits.

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

The platform has five logical planes:

- **Data Plane** — collection, normalization and point-in-time storage.
- **Research Plane** — hypotheses, backtests, validation and experiment registry.
- **Trading Plane** — signals, portfolio construction, risk and execution.
- **Control Plane** — configuration, strategy lifecycle, permissions and operator actions.
- **Observability Plane** — Grafana, traces, logs, alerts and forensic reconstruction.

## What self-learning means here

The platform may adapt, but not by allowing an LLM to rewrite live strategy parameters after a few losses.

Safe adaptation includes:

- volatility-aware position sizing;
- dynamic exposure limits;
- strategy allocation based on rolling evidence;
- regime-dependent strategy activation;
- execution model calibration;
- strategy health monitoring and automatic quarantine.

AI/Hermes can assist research by proposing hypotheses, running experiments, comparing models and producing reports. A newly discovered model may not directly enter live trading. Promotion always follows the research-to-live gate sequence.

## Free-data-first policy

The first research stage uses free sources only:

- Hyperliquid public market data;
- Hyperliquid historical data where available;
- Binance public historical/realtime data;
- Bybit/other free exchange data where useful;
- self-collected WebSocket data stored in ClickHouse.

Paid data is only considered when an explicit research bottleneck exists and incremental value can be measured with an A/B-style feature comparison.

## Success criteria

Success is not a pretty backtest. A strategy is interesting only when it demonstrates:

- positive after-cost expectancy;
- robustness across nearby parameters;
- stability across multiple assets and regimes;
- acceptable drawdown;
- out-of-sample persistence;
- realistic fill assumptions;
- stress resilience when costs are increased;
- consistent paper/shadow behavior relative to simulation.

## Non-goals

We are not building:

- a martingale bot;
- a fixed-leverage gambler;
- an LLM that directly decides BUY/SELL in production;
- a strategy that depends on hidden look-ahead information;
- a system that automatically promotes new research into live capital;
- an HFT system requiring microsecond co-location as the first objective.