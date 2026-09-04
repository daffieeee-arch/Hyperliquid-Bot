# Research Method

## Objective

Prevent false discovery, overfitting, survivorship bias, look-ahead bias and unrealistic execution assumptions from creating fictitious trading edges.

Research must also be reproducible across the Windows/WSL2 development environment, GitHub CI and the approved Linux/OCI research/runtime environment.

## Research environments

### Windows/WSL2

Use for:

- hypothesis development;
- deterministic unit/replay tests;
- small and medium backtests;
- frontend/reporting work;
- bounded sample datasets;
- later GPU-assisted experiments.

### GitHub CI

Use for:

- deterministic regression experiments;
- strategy safety tests;
- schema/contract checks;
- reproducible build verification.

CI is not the place for large stochastic research sweeps whose result changes every run.

### Runtime research worker

Use for:

- large ClickHouse scans;
- longer historical studies;
- continuous paper data;
- low-priority batch research against the authoritative self-collected dataset.

The research worker has explicit CPU/RAM/I/O limits and cannot share failure fate with collectors or trading/risk services.

## Dataset discipline

All research preserves point-in-time correctness:

- only assets tradable at that historical moment may be included;
- listings/delistings are timestamped;
- funding/OI/metadata may only be used once actually available;
- missing values are not silently converted to zero;
- exchange timestamps and local receipt timestamps are distinguished;
- fee schedules match the relevant historical period where possible;
- dataset extraction time and transformation code are recorded;
- local exports retain provenance back to the runtime/ClickHouse source version.

## Dataset partitions

Default structure:

- **Train** — fit/derive the hypothesis.
- **Validation** — select among a small number of predeclared variants.
- **Untouched OOS** — final holdout not used for feature/parameter tuning.
- **Walk-forward** — repeated rolling retrain/retest where appropriate.

The untouched OOS period must not be reused as a tuning dataset after a disappointing result.

## Hyperliquid BTC-PERP retained-series gate

Before any candidate baseline (momentum lookback or later basis) may run against Hyperliquid
BTC-PERP, the PAPER entrypoint `python -m hyperliquid_bot.hypothesis_research` must pass the
published DATA-1A sufficiency thresholds documented in [DATA.md](DATA.md) (72-hour receipt-clock
span, trade/BBO/mid counts, and a 5% incomplete-hour cap).

Fail closed with verdict `not_enough_data` when those gates fail. Do not fit lookbacks, do not
score in-sample expectancy, and do not treat committed fixtures, D01 routing events or a 1-600s
DATA-1A smoke as a hypothesis-usable series. Trading must retain a multi-day DATA-1A run at
`<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/` (schema version 1, UTC ns receipt
clocks, `capture-claim.json` with `retained: true`) outside git before Quant can move past this
gate. Trading may retain that series on the operator WSL PC
([DATA-1A operator PC/WSL runbook](runbooks/data1a-wsl-pc-retained-capture.md)) or on a VPS
([DATA-1A VPS runbook](runbooks/data1a-vps-retained-capture.md)). Cloud Agents are unsuitable
for a multi-day retain. A writer duration above 600 seconds is still not 24/7 service and is
not by itself an edge.

A passing sanity report is not evidence of edge. The current momentum slot is a fixed-lookback
scaffold that may only return `noise`. `edge` is reserved and is not assigned by this entrypoint.
Binance DATA-1F remains optional and is required only if the reserved basis stub is selected.
Operator docs for a later retained Binance capture are in
[DATA-1F operator PC/WSL runbook](runbooks/data1f-wsl-pc-retained-capture.md) and
[DATA-1F VPS runbook](runbooks/data1f-vps-retained-capture.md). Do not start that capture
until the TerraPC DATA-1A 72h series finishes and CoS assigns the window. Cloud Agents are
unsuitable for a multi-day retain.

## Reproducible execution

Every experiment runs from:

- a committed code state;
- pinned dependencies/lockfiles;
- versioned strategy/configuration;
- identified dataset version/range;
- explicit random seed where stochastic methods are used;
- declared compute environment;
- recorded image/commit when run on a runtime worker.

Do not treat a notebook's uncommitted interactive state as a validated experiment.

## Cost model

Every strategy report includes:

- maker/taker fees;
- spread;
- slippage;
- funding;
- partial-fill assumptions where relevant;
- hedge costs for multi-leg trades;
- optional latency/adverse-selection penalties;
- quote-currency conversion costs where relevant;
- venue-specific borrow/carry costs where relevant.

Stress at minimum with higher-than-expected costs, such as 1.5x and 2.0x modeled slippage/cost components where meaningful.

## Fill realism

A limit price being touched does not imply a full maker fill. Event-driven simulation becomes increasingly conservative as order-book data improves. Queue position, available depth, latency and adverse selection must be considered for microstructure strategies.

Backtests, local replay and deployed PAPER must expose fill-model assumptions so paper-versus-simulation decay can be measured.

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
- different start dates;
- data gaps/reconnect scenarios;
- deployment/runtime version changes where relevant.

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
- fees, funding and slippage contribution;
- result by venue, asset and regime;
- paper-versus-backtest divergence.

## Promotion criteria

There is no single magic threshold, but an initial candidate should normally demonstrate:

- positive after-cost OOS expectancy;
- sufficient sample size;
- no dependence on one asset/month;
- acceptable drawdown for its target risk budget;
- reasonable parameter stability;
- resilience to cost stress;
- paper/shadow behavior consistent with simulation;
- reproducibility from a clean checkout/container;
- no material dependency on the Windows workstation for 24/7 operation.

A high in-sample Sharpe alone is explicitly insufficient.

## Experiment registry

Each experiment records:

- immutable experiment ID;
- code commit SHA;
- container image digest where applicable;
- dataset/version/range;
- source environment (`DEV`, `CI`, `TRUENAS_RESEARCH`);
- feature set;
- strategy/model version;
- parameters;
- random seed where applicable;
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
- bypass risk limits;
- depend on a GPU in the production critical path unless separately justified and tested.

The Windows RTX GPU is a research accelerator, not a mandatory runtime dependency.

## Research question examples

Examples of properly testable questions:

- Does spot-led momentum outperform perp-led momentum over the next 4h/24h?
- Does neutral funding improve continuation probability after a breakout?
- Does extreme OI growth reduce subsequent momentum expectancy?
- Does Hyperliquid lag Binance for specific assets/regimes enough to overcome costs?
- Does basis convergence remain profitable after both-leg execution costs?
- Does order-book imbalance improve entry quality rather than merely predict tiny pre-cost returns?
- Does a feature improve results out-of-sample and in 24/7 runtime paper data, not only in a local backtest?
