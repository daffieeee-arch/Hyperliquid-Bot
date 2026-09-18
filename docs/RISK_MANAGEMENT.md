# Risk Management

## Core principle

Edge comes first; leverage only scales an already validated edge. Position size is determined from portfolio risk, volatility, stop distance, liquidity and correlation—not from a fixed leverage target.

Risk includes more than market movement. The platform also treats data quality, software version, deployment state, runtime health, venue concentration and credential boundaries as risk inputs.

## Initial conservative defaults for paper research

These values are starting hypotheses, not immutable production limits:

- risk per trade: roughly 0.20-0.30% of equity;
- maximum simultaneous positions: initially small, for example 3-5;
- daily loss guard: approximately 1%;
- weekly loss guard: approximately 3%;
- hard strategy/portfolio drawdown kill threshold: approximately 7-10% depending on strategy mix;
- no martingale;
- no uncontrolled DCA;
- no averaging down outside predeclared rules;
- every leveraged directional position has an explicit loss-control mechanism.

Paper defaults are not automatically copied into live configuration. Live limits require separate approval.

## PAPER Phase 1A implemented gates

The project-owned module `hyperliquid_bot.paper_risk` implements the documented
stop-based sizing rule and fail-closed portfolio limits for PAPER only. It
extends the COURSE-1 soak and D22-A pre-submit path; it does not replace the
frozen D01 publication smoke-risk function, relax a D01 rejection, or authorize
LIVE / TESTNET / SHADOW.

Sizing (never rounded up):

```text
account_risk_budget = equity * risk_per_trade_fraction
effective_stop = stop_distance_fraction * volatility_multiple   # multiple >= 1
position_notional = account_risk_budget / effective_stop
```

Default PAPER fractions (starting hypotheses, not live limits):

| Gate | Default | Breach behavior |
| --- | --- | --- |
| `risk_based_size` | 0.25% equity / stop distance | reject new entry |
| `max_paper_leverage` | 1.0x equity | reject new entry |
| `max_gross_exposure` | 1.0x equity | reject new entry |
| `max_net_exposure` | 1.0x equity | reject new entry |
| `max_simultaneous_positions` | 3 | reject new entry |
| `max_asset_concentration` | 50% equity | reject new entry |
| `max_strategy_concentration` | 50% equity | reject new entry |
| `max_venue_concentration` | 100% equity (single-venue PAPER) | reject new entry |
| `daily_loss_guard` | 1% equity already lost | reject new entry |
| `weekly_loss_guard` | 3% equity already lost | reject new entry |
| `drawdown_kill` | 7% from peak equity | reject new entry |
| `no_averaging_down` | no add to an open name | reject new entry |

Reduce-only exits remain allowed when an entry halt is already breached so an
open PAPER position can still flatten. Invalid or incomplete portfolio state
raises rather than passing. Hard limits are never relaxed by this module.

The bounded COURSE-1 / D22-A smoke book currently supplies a clean snapshot
(starting cash, current position, mark). Daily, weekly and drawdown gates are
implemented and unit-tested; they bind as soon as a caller provides realized
PnL / peak equity. FastAPI `/health` and `/ready` live in
`hyperliquid_bot.control_service` and are not part of this risk gate.

## Position sizing

For a simple stop-based position:

```text
position_notional = account_risk_budget / stop_distance_fraction
```

Example: $50,000 equity, 0.30% risk ($150), 2% stop distance -> $7,500 notional. Effective portfolio leverage is therefore 0.15x, not an arbitrary 5x.

Position sizing is capped by:

- venue maximum and internal maximum leverage;
- minimum liquidation distance;
- order-book depth and expected slippage;
- per-asset/strategy/venue concentration;
- current data/runtime health;
- total gross and net portfolio limits.

## Portfolio-level risk

Track at minimum:

- gross exposure;
- net exposure;
- effective leverage;
- exposure by asset/sector/strategy;
- exposure by venue and quote currency;
- BTC/ETH beta;
- realized and forecast volatility;
- correlation clusters;
- drawdown;
- VaR / Expected Shortfall as monitoring tools;
- liquidation distance for leveraged positions;
- liquidity/capacity risk;
- venue/counterparty concentration;
- EUR/USD and stablecoin exposure.

## Strategy risk budgets

Each strategy receives a risk budget separate from capital allocation. A high-correlation portfolio of strategies must not appear diversified merely because it has many names.

Risk allocation is attached to a specific strategy/configuration version and deployment environment. PAPER evidence does not authorize LIVE risk.

## Operational and deployment risk

The risk engine receives health signals including:

- market-data freshness and gaps;
- collector/queue pressure;
- ClickHouse/control-state health;
- reconciliation status;
- venue/API status;
- active environment;
- approved image digest versus deployed digest;
- configuration version;
- restart/crash state;
- resource pressure and clock health.

A mismatch between approved and deployed software/configuration blocks new live risk.

The Windows development workstation is never part of the continuous execution dependency chain. Turning it off must not affect PAPER/SHADOW/LIVE services on the independently operated runtime.

## Dynamic risk controls

Safe adaptation may reduce position sizing when:

- volatility rises;
- spread widens;
- liquidity falls;
- execution slippage worsens;
- strategy drawdown deepens;
- correlation between strategies rises;
- data quality becomes uncertain;
- venue health degrades;
- runtime resource pressure rises;
- observed fills diverge from the simulator.

Hard limits are never relaxed automatically by an ML model, LLM, frontend or deployment process.

## Strategy health states

```text
ACTIVE -> REDUCED -> QUARANTINED -> RETIRED
```

A strategy may be reduced or quarantined when paper/live outcomes materially diverge from its validated distribution, for example via persistent negative expectancy, abnormal slippage, higher drawdown or changed market regime.

Software health is tracked separately. A strategy may remain valid while an image is rolled back, and software may remain healthy while a strategy is quarantined.

## Kill switches

The production control plane provides:

- `HALT NEW ORDERS` — no new entries; existing positions still managed safely;
- `FLATTEN & HALT` — controlled close-out then disable execution;
- automatic stale-data halt;
- API/connectivity failure halt;
- drawdown/loss-limit halt;
- reconciliation mismatch halt;
- deployment/configuration mismatch halt;
- venue-health halt;
- resource-pressure halt where continued operation is unsafe.

A kill switch lives in the trading/control layer, not only in the UI, Grafana, Hermes or Codex.

## Stress scenarios

Before live promotion test scenarios such as:

- BTC/ETH sudden gap or cascade;
- spread multiple times normal;
- funding reversal;
- partial fill of only one hedge leg;
- WebSocket disconnect during an open position;
- exchange API rejects/cancel delay;
- restart with open positions;
- stale local state versus exchange state;
- ClickHouse/control database unavailable;
- Redis unavailable when introduced;
- wrong/stale image digest or configuration;
- runtime container restart or host reboot;
- Windows workstation and Hermes unavailable;
- rollback during an open paper/shadow position;
- venue outage while a multi-leg hedge is incomplete.

## Runtime maturity gate

The optional existing TrueNAS 26 BETA.3 profile may be used for PAPER research with monitoring and backups. Before material live risk, use a supported stable runtime or explicitly approve and document the operating-system risk after soak, recovery and rollback testing.
