# Paper to Live Promotion Gates

## Principle

The codebase is designed for live trading from the beginning, but real capital is the final stage of a controlled promotion process.

```text
RESEARCH
  ↓
BACKTEST
  ↓
WALK-FORWARD / UNTOUCHED OOS
  ↓
PAPER
  ↓
SHADOW
  ↓
TESTNET
  ↓
SMALL LIVE
  ↓
PRODUCTION
```

## RESEARCH -> BACKTEST

Require:
- explicit hypothesis;
- defined universe;
- defined data requirements;
- predeclared cost assumptions;
- reproducible experiment configuration.

## BACKTEST -> OOS

Require:
- positive net expectancy after modeled costs;
- adequate trade count;
- acceptable drawdown;
- no obvious dependence on one asset or month;
- nearby parameter stability.

## OOS -> PAPER

Require:
- untouched holdout remains positive;
- cost stress remains viable;
- walk-forward behavior is acceptable;
- no material look-ahead/survivorship issues found;
- risk model is defined.

## PAPER -> SHADOW

Require:
- live feed stability;
- paper execution behavior consistent with assumptions;
- strategy output understandable via why-this-trade records;
- no unexplained state drift;
- alerting and risk limits operational.

## SHADOW -> TESTNET / SMALL LIVE

Require:
- observed signals and hypothetical fills remain statistically consistent;
- exchange reconciliation proven;
- restart recovery proven;
- dead-man protection proven;
- kill switches tested;
- agent-wallet security reviewed;
- maximum-notional and loss limits enforced server-side.

## SMALL LIVE -> PRODUCTION

Require sufficient live evidence rather than a fixed calendar promise. Evaluate:
- realized slippage versus model;
- maker/taker behavior;
- operational incidents;
- drawdown versus expected distribution;
- live expectancy;
- capacity/liquidity;
- strategy health over multiple regimes where practical.

Allocation increases gradually and can be reversed immediately.

## Non-negotiable rules

- A strong backtest never skips paper/shadow stages.
- A strategy may not self-promote.
- An LLM/research agent may recommend promotion but cannot authorize capital.
- Live and paper use the same strategy/risk code path.
- `PAPER` is the system default.
- Any material state mismatch blocks new live risk.

## De-promotion

Promotion is reversible:

```text
ACTIVE -> REDUCED -> QUARANTINED -> RETIRED
```

Triggers can include:
- significant expectancy decay;
- abnormal execution costs;
- excessive drawdown;
- changed liquidity/capacity;
- data-quality failures;
- model drift;
- operational instability.