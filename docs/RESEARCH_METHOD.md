# Research Method

## Objective

Prevent false discovery, overfitting, survivorship bias, look-ahead bias and unrealistic execution assumptions from creating fictitious trading edges.

## Dataset discipline

All research must preserve point-in-time correctness:

- only assets tradable at that historical moment may be included;
- listings/delistings must be timestamped;
- funding/OI/metadata may only be used once they were actually available;
- missing values are not silently converted to zero;
- exchange timestamps and local receipt timestamps are distinguished;
- fee schedules must match the relevant historical period where possible.

## Dataset partitions

Default structure:

- **Train** — fit/derive the hypothesis.
- **Validation** — select among a small number of predeclared variants.
- **Untouched OOS** — final holdout not used for feature/parameter tuning.
- **Walk-forward** — repeated rolling retrain/retest where appropriate.

The untouched OOS period must not be reused as a tuning dataset after a disappointing result.

## Cost model

Every strategy report must include:

- maker/taker fees;
- spread;
- slippage;
- funding;
- partial-fill assumptions where relevant;
- hedge costs for multi-leg trades;
- optional latency/adverse-selection penalties.

Stress at minimum with higher-than-expected costs (for example 1.5x and 2.0x modeled slippage/cost components where meaningful).

## Fill realism

A limit price being touched does not imply a full maker fill. Event-driven simulation should become increasingly conservative as order-book data improves. Queue position, available depth and adverse selection must be considered for microstructure strategies.

## Robustness tests

Candidate strategies should be tested for:

- nearby parameter stability;
- multiple assets;
- multiple market regimes;
- removal of the best month;
- removal of the best asset;
- bootstrapped/Monte Carlo trade sequencing;
- higher costs;
- delayed entry/exit assumptions;
- reduced fill rates;
- different start dates.

## Core metrics

At minimum report:

- net return;
- CAGR where applicable;
- volatility;
- Sharpe / Sortino;
- maximum drawdown;
- Calmar;
- profit factor;
- expectancy per trade;
- win rate;
- average win/loss;
- MAE/MFE;
- turnover;
- trade count;
- market beta / net and gross exposure where relevant;
- fees, funding and slippage contribution.

## Promotion criteria

There is no single magic threshold, but an initial candidate should normally demonstrate:

- positive after-cost OOS expectancy;
- sufficient sample size;
- no dependence on one asset/month;
- acceptable drawdown for its target risk budget;
- reasonable parameter stability;
- resilience to cost stress;
- paper/shadow behavior consistent with simulation.

A high in-sample Sharpe alone is explicitly insufficient.

## Experiment registry

Each experiment should record:

- immutable experiment ID;
- code commit SHA;
- dataset/version/range;
- feature set;
- strategy/model version;
- parameters;
- cost assumptions;
- metrics;
- artifacts;
- verdict;
- promotion decision.

This makes every result reproducible and prevents accidental cherry-picking.

## Machine learning governance

ML may rank assets, estimate conditional returns, classify regimes, predict execution quality or allocate between already validated strategies.

An ML model must not:

- train on future information;
- dynamically rewrite its own live objective;
- promote itself into live capital;
- bypass risk limits.

## Research question examples

Examples of properly testable questions:

- Does spot-led momentum outperform perp-led momentum over the next 4h/24h?
- Does neutral funding improve continuation probability after a breakout?
- Does extreme OI growth reduce subsequent momentum expectancy?
- Does Hyperliquid lag Binance for specific assets/regimes enough to overcome costs?
- Does basis convergence remain profitable after both-leg execution costs?
- Does order-book imbalance improve entry quality rather than merely predict tiny pre-cost returns?