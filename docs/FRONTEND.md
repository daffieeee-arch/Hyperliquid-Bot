# Frontend Vision

## Goal

Build a professional trading workstation, not a decorative dashboard. The operator should understand portfolio state, strategy health, execution quality and market context within seconds, then drill down to the exact cause of any trade or anomaly.

## Technology

- TypeScript;
- React;
- Next.js;
- WebSocket for realtime state;
- REST/HTTP for control/query operations;
- strict typing and component tests;
- production build packaged as a Linux container image.

The browser never receives trading secrets.

The first cockpit screen now reads COURSE-1 PAPER JSON and a thin DATA-1A
capture-health panel. It shows public BTC-PERP mid, paper position, assumed
overlay PnL, COURSE-1 soak health, DATA-1A reconstructable capture health, and
the copied PAPER intent/fill blotter. The layout is a dense dark terminal so
those values are readable at a glance; PAPER is badged and watermarked. It is
inspired by professional market workstations, not a clone of a commercial UI.
DESK / MARKETS / RISK are not built. Later screens must keep using the
create-only reconstructable contracts rather than inventing a second store:

```text
<artifact-root>/course1/live-public-paper/<run_id>/
  run-claim.json
  paper-position.json
  paper-pnl.json
  orders.json
  fills.json
  capture-health.json

<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

`paper-pnl.json` is assumed PAPER overlay economics, not venue PnL. COURSE-1
`capture-health.json` is a bounded-run summary, not a 24/7 heartbeat. DATA-1A
`capture-claim.json` / `capture-health.json` are a reconstructable public capture
claim and an end-of-run health file. While a live retain has a claim, growing
`raw/part-*.parquet` files, and no health file, the panel shows
**RUNNING (health JSON pending until stop)** rather than invented zeros.
Missing artifact root or `run_id` is an explicit empty state. DATA-1A capture
duration is 1–604800 seconds; the COURSE-1 soak remains 1–600 seconds. Locally
the first screen defaults to
`tests/fixtures/course1_cockpit/live-public-soak/` (`run_id`
`20260904t001800z-live-paper`) and `tests/fixtures/data_1a_retained/sample-run/`
(`run_id` `sample-run`). Set `COCKPIT_ARTIFACT_ROOT` + `COCKPIT_RUN_ID` for PAPER
JSON, and `ARTIFACT_ROOT` + `DATA1A_RUN_ID` (or `COCKPIT_DATA1A_RUN_ID` /
`?data1a_run_id=`) for a live DATA-1A directory. TerraPC WSL example:
`ARTIFACT_ROOT=/home/dmesdary/hyperliquid-artifacts/reconstructable` and
`DATA1A_RUN_ID=20260904t134940z-live-retained` (copy `apps/cockpit/.env.example`
to `apps/cockpit/.env.local`, or export in the WSL shell before `next dev`).
See `docs/DATA.md`,
`docs/runbooks/data1a-vps-retained-capture.md`,
`docs/runbooks/data1a-wsl-pc-retained-capture.md`,
`docs/runbooks/cockpit-first-paper-screen.md`, `apps/cockpit/README.md`, and
`vertical_slices/course1_live_public_paper/README.md`.

## Development model

Frontend development happens in WSL2 on the Windows workstation. Next.js hot reload may be viewed from the Windows browser through localhost while the source and toolchain remain in the WSL Linux filesystem.

The local frontend uses:

- mock/fixture data;
- a local FastAPI instance;
- disposable local ClickHouse/Grafana where needed;
- explicit environment banners;
- no real trading credentials.

The runtime host does not compile or hot-reload the frontend. GitHub Actions creates a production image, and the approved runtime pulls a pinned tag/digest.

Runtime configuration that differs between DEV/PAPER/SHADOW/LIVE must be supplied through safe server-side configuration. No secret may be exposed through `NEXT_PUBLIC_*` variables or browser bundles.

## Version and environment identity

The cockpit always displays:

- environment (`DEV`, `PAPER`, `SHADOW`, `LIVE`);
- backend connectivity/state;
- source commit;
- frontend image digest/build version;
- active strategy/configuration version;
- latest deployment time.

A frontend/backend version incompatibility is visible and may disable mutating controls.

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
- kill-switch status;
- environment and deployment version.

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
- strategy signal overlays;
- venue/data-quality health.

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
- opportunity cost;
- order/reconciliation state;
- client order and correlation IDs.

### PORTFOLIO

- holdings/positions;
- realized/unrealized PnL;
- strategy attribution;
- asset attribution;
- cash/collateral;
- hedge relationships;
- venue and quote-currency allocation.

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
- risk-budget consumption;
- venue/counterparty risk.

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

Collector status, ClickHouse/Redis/PostgreSQL state, WebSocket health, resource use, data gaps, recent deployments, image digests and incidents.

## Why-this-trade panel

Every paper/live position should be explainable:

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
Code commit / image digest: ...
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
- clear distinction between informational and action controls;
- deep links to relevant Grafana forensic views;
- stale/loading/error states that cannot be mistaken for valid market state.

## PAPER versus LIVE

Modes must be visually impossible to confuse.

PAPER prominently displays:

```text
PAPER TRADING — NO REAL CAPITAL
```

LIVE uses a clearly different visual state and requires explicit backend authorization. The frontend does not decide whether live mode exists.

Global controls:

- `HALT NEW ORDERS`;
- `FLATTEN & HALT`.

These controls invoke backend risk/execution actions; the UI is not itself the kill switch. Critical actions require confirmation and produce an audit record.

## Strategy Lab

A key view compares lifecycle performance:

```text
Strategy              Backtest   Paper   Shadow   Live
Basis/Carry            ...        ...     ...      ...
Spot Momentum          ...        ...     ...      ...
Perp Momentum          ...        ...     ...      ...
```

The purpose is to expose backtest decay rather than hide it.

## Build and test expectations

Before a frontend image is releasable:

- TypeScript strict typecheck passes;
- lint/format checks pass;
- component/unit tests pass;
- production Next.js build passes;
- API schema/client compatibility is verified;
- no secrets appear in the output bundle;
- environment banner tests pass;
- destructive controls remain disabled against mock/read-only backends;
- image health check succeeds.

## Design inspiration

Borrow workflow principles—not visual cloning—from:

- Bloomberg/EMS-style dense information hierarchy;
- TradingView synchronized charting;
- Bookmap liquidity/order-flow context;
- institutional OMS/EMS order blotters;
- professional risk dashboards.

The finished product should feel like one coherent terminal even though Grafana remains available for deep observability and forensics.
