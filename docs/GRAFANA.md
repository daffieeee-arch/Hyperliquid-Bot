# Grafana & Observability

## Role

Grafana is not the primary trading UI. It is the professional observability, research-analysis and forensic layer behind the trading cockpit.

## Stack

- Grafana
- ClickHouse datasource
- Grafana Alloy / OpenTelemetry collection
- Prometheus-compatible metrics where useful
- optional logs/traces retained in ClickHouse or a dedicated backend if scale later requires it

Avoid adding Kafka, Elasticsearch/OpenSearch, Kubernetes, Spark or other heavy infrastructure until there is a proven need.

## Dashboard families

### Executive
- equity curve;
- daily/weekly/monthly PnL;
- Sharpe/Sortino;
- max drawdown;
- gross/net exposure;
- strategy allocation;
- active incidents.

### Strategies
- PnL by strategy;
- expectancy;
- rolling Sharpe;
- win/loss distribution;
- MAE/MFE;
- turnover;
- drawdown;
- backtest/paper/shadow/live comparison.

### Markets
- price;
- funding;
- OI;
- basis;
- volume;
- volatility;
- market breadth;
- cross-venue spreads.

### Microstructure
- spread;
- L2 depth;
- book imbalance;
- signed trade flow;
- realized slippage;
- liquidity changes.

### Execution
- fill ratio;
- partial fills;
- rejects;
- maker/taker ratio;
- decision-to-ack latency;
- decision-to-fill latency;
- expected vs realized slippage;
- opportunity cost.

### Risk
- gross/net leverage;
- correlation heatmap;
- concentration;
- VaR/ES monitoring;
- drawdown;
- liquidation-distance metrics;
- risk-limit consumption.

### Research
- experiment results;
- parameter stability;
- cost stress;
- OOS metrics;
- paper-vs-backtest decay.

### Infrastructure
- CPU/RAM;
- disk IO;
- ClickHouse query pressure;
- Redis memory;
- database health;
- collector message rate;
- WebSocket reconnects;
- stale feed status;
- API errors.

## Annotations

Use annotations extensively to align market/trading events with system events:

```text
SIGNAL
ORDER_SENT
PARTIAL_FILL
FULL_FILL
STOP_MOVED
EXIT
STRATEGY_PAUSED
MODEL_VERSION_CHANGE
DATA_GAP
WEBSOCKET_RECONNECT
DEPLOYMENT
```

A poor trade should be reconstructable against the exact market state, strategy state and system state.

## Alerts

Important alert classes:
- no fresh market data;
- unexpected data gap;
- strategy drawdown threshold;
- portfolio loss threshold;
- execution latency spike;
- fill-rate collapse;
- slippage anomaly;
- exchange/local reconciliation mismatch;
- collector down;
- ClickHouse/Redis/PostgreSQL unhealthy;
- disk space or memory pressure.

Critical trading alerts must also feed the backend risk engine where appropriate; an alert should not be the only safeguard.

## OpenTelemetry traces

Trace the full signal-to-fill path:

```text
market event
  -> feature calculation
  -> strategy decision
  -> portfolio allocation
  -> risk approval
  -> execution request
  -> exchange acknowledgement
  -> fill
```

This allows direct measurement of where latency or alpha decay occurs.

## Security

Grafana receives read-only database credentials wherever possible. Observability must not be able to sign or submit exchange orders.