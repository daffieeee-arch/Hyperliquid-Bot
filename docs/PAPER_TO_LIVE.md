# Paper to Live Promotion Gates

## Principle

The codebase is designed for live trading from the beginning, but real capital is the final stage of two controlled promotion processes:

1. **strategy promotion** based on statistical and execution evidence;
2. **software artifact promotion** based on CI, deployment and operational evidence.

Both must pass. A profitable strategy on unapproved software cannot go live, and a technically sound image does not make an unproven strategy profitable.

## Strategy lifecycle

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

## Software lifecycle

```text
WINDOWS/WSL2 DEV
  ↓
PULL REQUEST
  ↓
CI
  ↓
PRIVATE GHCR IMAGE
  ↓
TRUENAS PAPER DEPLOYMENT
  ↓
SOAK / RECOVERY / ROLLBACK TESTS
  ↓
SHADOW DEPLOYMENT
  ↓
SMALL LIVE DEPLOYMENT
```

Where practical, the exact same image digest is promoted through PAPER, SHADOW and SMALL LIVE. Rebuilding creates a new artifact and requires relevant revalidation.

## RESEARCH -> BACKTEST

Require:

- explicit hypothesis;
- defined universe;
- defined data requirements;
- predeclared cost assumptions;
- reproducible experiment configuration;
- source commit and experiment version.

## BACKTEST -> OOS

Require:

- positive net expectancy after modeled costs;
- adequate trade count;
- acceptable drawdown;
- no obvious dependence on one asset or month;
- nearby parameter stability;
- no material look-ahead or survivorship issue.

## OOS -> PAPER

Require:

- untouched holdout remains positive;
- cost stress remains viable;
- walk-forward behavior is acceptable;
- no material point-in-time/data-quality issue found;
- risk model is defined;
- paper-only broker and safety tests pass;
- approved CI image exists.

## Software -> TrueNAS PAPER

Require:

- pull request reviewed/merged;
- Python and TypeScript checks pass;
- deterministic replay tests pass;
- secret scan passes;
- release image built in CI;
- image tag, digest and source commit recorded;
- no secrets baked into image;
- TrueNAS PAPER configuration references the approved digest;
- rollback target is known.

Merge to `main` alone does not deploy.

## PAPER -> SHADOW

Require:

- live feed stability;
- paper execution behavior consistent with assumptions;
- strategy output understandable via why-this-trade records;
- no unexplained state drift;
- alerting and risk limits operational;
- prolonged TrueNAS service stability with the Windows development PC switched off;
- restart/reconnect/data-gap tests pass;
- paper deployment rollback proven;
- Grafana and cockpit agree on state.

## SHADOW -> TESTNET / SMALL LIVE

Require:

- observed signals and hypothetical fills remain statistically consistent;
- exchange reconciliation proven;
- restart recovery proven;
- dead-man protection proven;
- kill switches tested;
- agent-wallet security reviewed;
- maximum-notional and loss limits enforced server-side;
- separate LIVE configuration, secrets, database namespace and deployment identity;
- image digest explicitly approved for LIVE;
- no dependency on Windows, Codex, Hermes or an interactive session for continuous risk management;
- stable runtime operating system preferred.

TrueNAS 26 BETA.3 is acceptable for PAPER research, but material live capital should wait for a stable runtime or require explicit documented risk acceptance and repeat recovery/soak testing.

## SMALL LIVE -> PRODUCTION

Require sufficient live evidence rather than a fixed calendar promise. Evaluate:

- realized slippage versus model;
- maker/taker behavior;
- operational incidents;
- drawdown versus expected distribution;
- live expectancy;
- capacity/liquidity;
- strategy health over multiple regimes where practical;
- software/runtime stability;
- alert response and rollback drills;
- venue and credential concentration.

Allocation increases gradually and can be reversed immediately.

## Non-negotiable rules

- A strong backtest never skips paper/shadow stages.
- A successful deployment never skips strategy validation.
- A strategy may not self-promote.
- An LLM/research agent may recommend promotion but cannot authorize capital.
- Live and paper use the same strategy/risk code path.
- `PAPER` is the system default.
- Any material state mismatch blocks new live risk.
- A frontend toggle cannot enable live trading.
- No floating `latest` image is used in production-like environments.
- Source is not edited inside running TrueNAS containers.
- Deployment approval and strategy approval are distinct records.

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
- operational instability;
- deployment regression;
- image/configuration mismatch;
- runtime operating-system instability.

Software may independently roll back to a previous digest without changing the strategy's research status. A strategy may be quarantined even when the software remains healthy.
