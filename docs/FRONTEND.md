# Frontend Vision

## Goal

Build a professional trading workstation, not a decorative dashboard. The operator should be able to understand portfolio state, strategy health, execution quality and market context within seconds, then drill down to the exact cause of any trade or anomaly.

## Technology

- TypeScript
- React
- Next.js
- WebSocket for realtime state
- REST/HTTP for control/query operations

The browser never receives trading secrets.

## Primary workspaces

### DESK
The command center:
- equity / PnL;
- gross and net exposure;
- drawdown;
- active strategies;
- open positions;
- largest risks;
- alerts/incidents;
- kill-switch status.

### MARKETS
TradingView/Bookmap-inspired market workspace:
- synchronized charts;
- watchlists;
- depth and liquidity heatmap;
- trades tape;
- funding;
- OI;
- basis;
- cross-exchange spread;
- volatility;
- market breadth;
- strategy signal overlays.

### EXECUTION
Professional OMS/TCA-style blotter:
- working orders;
- fills;
- partial fills;
- rejects;
- maker/taker;
- slippage;
- queue/fill quality;
- latency;
- opportunity cost.

### PORTFOLIO
- holdings/positions;
- realized/unrealized PnL;
- strategy attribution;
- asset attribution;
- cash/collateral;
- hedge relationships.

### RISK
- gross/net exposure;
- leverage;
- liquidation distance;
- correlation matrix;
- concentration;
- volatility;
- drawdown;
- VaR/Expected Shortfall monitoring;
- scenario stress tests;
- risk-budget consumption.

### STRATEGIES
Leaderboard and lifecycle management:

```text
Strategy           Mode       Allocation   Sharpe   Max DD   Health
Spot Momentum      PAPER      30%          ...      ...      HEALTHY
Perp Momentum      PAPER      20%          ...      ...      STABLE
Basis/Carry        PAPER      30%          ...      ...      STRONG
Relative Strength  SHADOW      0%          ...      ...      LEARNING
Mean Reversion     QUARANTINE  0%          ...      ...      DEGRADED
```

### RESEARCH
Experiment registry, backtest comparison, promotion gates, parameter stability and paper-vs-backtest decay.

### SYSTEM
Collector status, ClickHouse/Redis/Postgres state, WebSocket health, resource use, recent deployments and incidents.

## Why-this-trade panel

Every live/paper position should be explainable:

```text
Strategy: Spot Momentum v0.3.2
Signal time: ...
Entry thesis:
  ✓ 4h trend
  ✓ relative strength
  ✓ volume acceleration
  ✓ healthy liquidity
  ✓ funding not crowded
  ✗ options confirmation unavailable
Expected edge: ...
Risk budget: ...
Stop / exit logic: ...
Model/feature version: ...
```

This is mandatory for auditability and model debugging.

## Interaction design

Professional workflow matters more than visual decoration:
- keyboard shortcuts;
- sortable/filterable tables;
- synchronized symbol selection;
- saved workspaces;
- dockable/resizable panels;
- multi-monitor friendly layout;
- fast drill-down from portfolio -> strategy -> trade -> raw market/execution timeline;
- clear distinction between informational and action controls.

## PAPER versus LIVE

Modes must be visually impossible to confuse.

PAPER should prominently display `PAPER TRADING — NO REAL CAPITAL`.

LIVE requires a clearly different state treatment and explicit operator action before capital-bearing strategies are enabled.

Global controls:
- `HALT NEW ORDERS`
- `FLATTEN & HALT`

These controls invoke backend risk/execution actions; the UI is not itself the kill switch.

## Strategy Lab

A key view compares lifecycle performance:

```text
Strategy              Backtest   Paper   Shadow   Live
Basis/Carry            ...        ...     ...      ...
Spot Momentum          ...        ...     ...      ...
Perp Momentum          ...        ...     ...      ...
```

The purpose is to make backtest decay visible rather than hide it.

## Design inspiration

Borrow workflow principles—not visual cloning—from:
- Bloomberg/EMS-style dense information hierarchy;
- TradingView synchronized charting;
- Bookmap liquidity/order-flow context;
- institutional OMS/EMS order blotters;
- professional risk dashboards.

The finished product should feel like one coherent terminal even though Grafana remains available for deep observability/forensics.