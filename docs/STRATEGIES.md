# Strategy Research Tracks

## Principle

No single strategy is assumed to be the winner. Multiple independent alpha engines are researched in parallel and evaluated on after-cost, out-of-sample performance.

## 1. Spot Momentum

**Hypothesis:** sustained relative strength accompanied by healthy spot-led volume and non-crowded derivatives positioning can continue over hours to days.

Candidate inputs:
- 15m/1h/4h/12h/24h returns;
- breakout distance;
- momentum acceleration;
- realized volatility;
- volume versus trailing baseline;
- spot vs perp leadership;
- funding and OI crowding filters;
- liquidity/spread constraints.

Position management should allow large winners to run. Fixed take-profit targets are not mandatory; trailing exits and trend deterioration are preferred research candidates.

## 2. Perpetual Momentum

**Hypothesis:** directional trends persist sufficiently to overcome fees, slippage and funding when filtered by volatility, regime and positioning.

Research both long and short variants. Leverage is determined by stop distance, volatility and portfolio risk budget—not a fixed 5x/10x rule.

## 3. Basis & Carry

**Hypothesis:** temporary relative mispricing between Hyperliquid perpetuals and spot or external reference venues converges, potentially augmented by funding carry.

Expected edge:

```text
expected convergence
+ expected net funding
- entry fees
- exit fees
- spread
- slippage
- hedge costs
- safety margin
```

Primary early universe: BTC, ETH, SOL, then additional liquid markets.

## 4. Relative Strength / Market-Neutral

**Hypothesis:** relative performance between assets is more predictable/robust than absolute market direction in some regimes.

Examples:
- long strongest assets / short BTC or ETH beta;
- long top-ranked basket / short weak-ranked basket;
- pairs/cointegration research.

Key objective: reduce broad crypto beta while capturing cross-sectional dispersion.

## 5. Mean Reversion

Research overshoot/reversal behavior after large short-horizon moves, liquidation events, spread dislocations and temporary cross-venue divergences.

This strategy receives lower initial priority than momentum and basis/carry because crypto can trend violently and repeatedly invalidate naive mean-reversion signals.

## 6. Order Flow / Microstructure

Initial role: execution filter and timing layer.

Features:
- spread;
- depth at 1/5/10 bps;
- book imbalance;
- aggressive buy/sell flow;
- microprice;
- order-book replenishment;
- cross-venue lead/lag;
- realized slippage.

Promote to standalone alpha only if realistic queue/fill assumptions still produce an edge.

## 7. Quarter-Hour / Intraday Structure

Research recurring behavior around :00, :15, :30 and :45 boundaries, especially in combination with signed volume, momentum and order-flow confirmation.

Treat as an experimental feature, not a presumed edge.

## 8. Strategy Allocation

Once multiple engines are validated, build a conservative portfolio allocator using:
- rolling risk-adjusted performance;
- cross-strategy correlations;
- regime compatibility;
- drawdown state;
- capacity/liquidity;
- uncertainty/confidence.

Potential methods include constrained Bayesian allocation or contextual bandit methods. Hard risk limits always override adaptive allocation.

## Strategy lifecycle

```text
IDEA
  ↓
RESEARCH
  ↓
VALIDATED
  ↓
PAPER
  ↓
SHADOW
  ↓
SMALL LIVE
  ↓
ACTIVE
  ↓
REDUCED / QUARANTINED / RETIRED
```

A strategy can lose capital allocation when observed behavior diverges materially from its validated distribution.