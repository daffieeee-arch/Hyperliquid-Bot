# Risk Management

## Core principle

Edge comes first; leverage only scales an already validated edge. Position size is determined from portfolio risk, volatility, stop distance, liquidity and correlation—not from a fixed leverage target.

## Initial conservative defaults for paper research

These values are starting hypotheses, not immutable production limits:

- risk per trade: roughly 0.20-0.30% of equity;
- maximum simultaneous positions: initially small (for example 3-5);
- daily loss guard: approximately 1%;
- weekly loss guard: approximately 3%;
- hard strategy/portfolio drawdown kill threshold: approximately 7-10% depending on strategy mix;
- no martingale;
- no uncontrolled DCA;
- no averaging down outside predeclared rules;
- every leveraged directional position has an explicit loss-control mechanism.

## Position sizing

For a simple stop-based position:

```text
position_notional = account_risk_budget / stop_distance_fraction
```

Example: $50,000 equity, 0.30% risk ($150), 2% stop distance -> $7,500 notional. Effective portfolio leverage is therefore 0.15x, not an arbitrary 5x.

## Portfolio-level risk

Track at minimum:

- gross exposure;
- net exposure;
- effective leverage;
- exposure by asset/sector/strategy;
- BTC/ETH beta;
- realized and forecast volatility;
- correlation clusters;
- drawdown;
- VaR / Expected Shortfall as monitoring tools;
- liquidation distance for leveraged positions;
- liquidity/capacity risk.

## Strategy risk budgets

Each strategy receives a risk budget separate from capital allocation. A high-correlation portfolio of strategies must not appear diversified merely because it has many names.

## Dynamic risk controls

Safe adaptation may reduce position sizing when:

- volatility rises;
- spread widens;
- liquidity falls;
- execution slippage worsens;
- strategy drawdown deepens;
- correlation between strategies rises;
- data quality becomes uncertain.

Hard limits are never relaxed automatically by an ML model.

## Strategy health states

```text
ACTIVE -> REDUCED -> QUARANTINED -> RETIRED
```

A strategy may be reduced or quarantined when live/paper outcomes materially diverge from its validated distribution, for example via persistent negative expectancy, abnormal slippage, higher drawdown or changed market regime.

## Kill switches

The production control plane must provide:

- `HALT NEW ORDERS` — no new entries; existing positions still managed safely;
- `FLATTEN & HALT` — controlled close-out then disable execution;
- automatic stale-data halt;
- API/connectivity failure halt;
- drawdown/loss-limit halt;
- reconciliation mismatch halt.

A kill switch must live in the trading/control layer, not only in the UI.

## Stress scenarios

Before live promotion test scenarios such as:

- BTC/ETH sudden gap or cascade;
- spread multiple times normal;
- funding reversal;
- partial fill of only one hedge leg;
- WebSocket disconnect during open position;
- exchange API rejects/cancel delay;
- restart with open positions;
- stale local state versus exchange state.