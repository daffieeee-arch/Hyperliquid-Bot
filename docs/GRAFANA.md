# Grafana & Observability

## Role

Grafana is not the primary trading UI. It is the professional observability, research-analysis and forensic layer behind the trading cockpit.

Grafana runs continuously on TrueNAS. Dashboard definitions, datasource provisioning and alert rules are developed/versioned in Git and promoted with the software release. Hermes may also use Grafana MCP to create and refine project dashboards, provided those changes are exported back to version control.

## Stack

- Grafana;
- ClickHouse datasource;
- Grafana Alloy / OpenTelemetry collection;
- Prometheus-compatible metrics where useful;
- optional logs/traces retained in ClickHouse or a dedicated backend if scale later requires it.

Avoid adding Kafka, Elasticsearch/OpenSearch, Kubernetes, Spark or other heavy infrastructure until there is a proven need.

## Development and deployment model

### Local development

A disposable local Grafana container may be used in Windows/WSL2 for:

- dashboard provisioning tests;
- sample ClickHouse queries;
- panel/layout development;
- frontend deep-link testing;
- validating alert definitions against fixtures.

It contains no production credentials and may be recreated at any time.

### TrueNAS runtime

The existing TrueNAS Grafana instance is reused where practical. Hyperliquid assets live in a dedicated folder and are backed by version-controlled provisioning files.

Every deployed dashboard should expose or link to:

- environment (`PAPER`, `SHADOW`, `LIVE`);
- Git commit;
- image digest;
- configuration version;
- last deployment annotation.

A deployment is not accepted until Grafana and the cockpit agree on core position, strategy and PnL state.

## Grafana MCP permissions

Grafana MCP does not need to be globally read-only. The intended model is:

- **Editor capability** for the Hyperliquid folder, dashboards, panels, annotations and alert rules;
- read access to relevant datasources and project incidents;
- no standing organization/server administration;
- no ability to read datasource secret values;
- no deletion or modification of unrelated Solana/homelab dashboards without explicit approval.

The Grafana MCP service account is separate from the ClickHouse datasource account. The datasource uses read-only SQL credentials even when Hermes can edit the dashboard that contains the query.

Changes made through MCP should be reviewed and exported/provisioned so TrueNAS state does not become the only copy.

## Dashboard families

### Executive

- equity curve;
- daily/weekly/monthly PnL;
- Sharpe/Sortino;
- max drawdown;
- gross/net exposure;
- strategy allocation;
- active incidents;
- deployment version.

### Strategies

- PnL by strategy;
- expectancy;
- rolling Sharpe;
- win/loss distribution;
- MAE/MFE;
- turnover;
- drawdown;
- backtest/paper/shadow/live comparison;
- strategy health/de-promotion status.

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
- liquidity changes;
- sequence gaps and queue pressure.

### Execution

- fill ratio;
- partial fills;
- rejects;
- maker/taker ratio;
- decision-to-ack latency;
- decision-to-fill latency;
- expected vs realized slippage;
- opportunity cost;
- reconciliation state.

### Risk

- gross/net leverage;
- correlation heatmap;
- concentration;
- VaR/ES monitoring;
- drawdown;
- liquidation-distance metrics;
- risk-limit consumption;
- venue/counterparty exposure.

### Research

- experiment results;
- parameter stability;
- cost stress;
- OOS metrics;
- paper-vs-backtest decay;
- model/feature version comparison.

### Infrastructure

- CPU/RAM and temperature;
- disk I/O and free capacity;
- ClickHouse query/ingest pressure;
- Redis memory when introduced;
- PostgreSQL health when introduced;
- collector message rate;
- WebSocket reconnects;
- stale feed status;
- API errors;
- container restarts;
- deployed image digest.

## Annotations

Use annotations extensively to align market/trading events with system and software events:

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
ROLLBACK
CONFIG_CHANGE
RISK_LIMIT_CHANGE
```

A poor trade should be reconstructable against the exact market state, strategy state, software version and system state.

## Alerts

Important alert classes:

- no fresh market data;
- unexpected data gap;
- queue/backpressure threshold;
- strategy drawdown threshold;
- portfolio loss threshold;
- execution latency spike;
- fill-rate collapse;
- slippage anomaly;
- exchange/local reconciliation mismatch;
- collector down;
- ClickHouse/Redis/PostgreSQL unhealthy;
- disk space, temperature or memory pressure;
- repeated container restart;
- paper/live mode mismatch;
- deployed digest not matching approved release.

Critical trading alerts also feed the backend risk engine where appropriate; an alert must not be the only safeguard.

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

Trace attributes include environment, venue, strategy version, commit and image digest. This allows direct measurement of latency and alpha decay while distinguishing a strategy problem from a deployment regression.

## Security

- Grafana has no exchange signing capability.
- Database datasources use read-only users wherever possible.
- Dashboard editing rights do not imply database write rights.
- Secrets never appear in panels, variables, annotations or exported JSON.
- Grafana admin and datasource management remain separately gated.
