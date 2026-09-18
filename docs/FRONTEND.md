# Frontend Vision

## Goal

Build a professional trading workstation, not a decorative dashboard. The operator should understand portfolio state, strategy health, execution quality and market context within seconds, then drill down to the exact cause of any trade or anomaly.

## Technology

- TypeScript;
- React;
- Next.js 15 App Router;
- Tailwind CSS v4 + official free shadcn/ui (Radix, New York; sidebar/dashboard-01 layout ideas only) + Lucide + TanStack Table + recharts (KPI sparklines from real public candle closes only) + TradingView Lightweight Charts (Apache-2.0; see `apps/cockpit/NOTICE`);
- visual default **C Fail-Closed Amber** (status-first) with optional **Desk Dark** density. Critique variants stay in `docs/design-previews/` only — not prod chrome. Research Lab sparse theme is later, not this screen;
- WebSocket for realtime state (planned; first screen polls HTTP);
- REST/HTTP for control/query operations;
- strict typing and component tests;
- production build packaged as a Linux container image.

The browser never receives trading secrets.

### Real project data vs fixtures / gaps

| Surface | Real / copied | Fixture default | Fail-closed gap |
|---|---|---|---|
| PAPER mode banner | Fail-closed `TRADING_MODE` | n/a | LIVE / TESTNET / SHADOW refuse |
| COURSE-1 soak identity | `run-claim.json` + position | `tests/fixtures/course1_cockpit/live-public-soak` | **UNAVAILABLE** if JSON missing |
| DATA retain identity | four-venue strip binds | DATA-1A sample-run; BN/BV/KR **MISSING** unless `ARTIFACT_ROOT` | never blended with soak PnL |
| Public HL mid / candles | credentialless `/info` `allMids` + `candleSnapshot` | n/a | **UNAVAILABLE** if the public route fails |
| Position / assumed PnL | `paper-position.json` / `paper-pnl.json` | soak fixture | labeled **not venue-reconciled**; never D22-B |
| Intents → fills | `orders.json` / `fills.json` | soak fixture (entry/exit) | REJECT rows always show a #65/`paper_risk` gate; soak is ACCEPT-only so the tape inserts a documented catalog demo reject (not LIVE, not a soak fill) |
| `#65` gate catalog | documented `paper_risk` defaults | n/a | per-run halt state **UNAVAILABLE** |
| Preflight caps | `run-claim.json` preflight | soak fixture | **not recorded** / **UNAVAILABLE** if omitted |
| D01 bind | `same_d01_smoke_risk` | soak fixture | **UNAVAILABLE** if preflight omitted |
| BN `usdm_public` | documented DATA-1F profile + copied BN chip | n/a | G/R stay n/a without health JSON |
| RESEARCH P0 | strip claims/health + optional `research-out/**/panel-summary.json` | none in-repo | **UNAVAILABLE** if missing; no edge / no strategy PnL |
| Capture run IDs | operator docs / picker hint only | unset on VPS | auto-detect prefers a live retain |

LAN phone: `PORT=3001 pnpm --filter @hyperliquid-bot/cockpit dev:lan` then
`http://<LAN-IP>:3001` on the same Wi-Fi (the PC's LAN IP, for example
`http://192.168.x.x:3001`). Collectors are never started or
stopped from the cockpit.

The cockpit is a routed PAPER operator workstation (incremental upgrade of
`apps/cockpit`, not a template replace). A persistent sidebar carries the
workspaces, a topbar carries the PAPER pill, capture pulse and the refresh
control, and on mobile the sidebar collapses into a drawer. Routes:

1. **`/` Overview:** the answer to "what should I look at". Four stat tiles
   (capture, attention count, research verdict, assumed PnL), a
   severity-ranked **attention list** that links into the workspace that can
   explain each item, public market context, a PAPER digest, the bound
   capture strip and a research-availability card.
2. **`/markets`:** public HL BTC-PERP mid plus the public `candleSnapshot`
   chart with a 5m/15m/1h/4h interval control; sibling last/BBO stay
   **UNAVAILABLE**. A recharts sparkline renders only from real public
   closes. Public mid is labeled **not research truth**.
3. **`/research` (Quant P0):** artifact and summary viewer only — run
   registry, capture health, WP-Q1 sufficiency, instrument identity and the
   overlap clock. No live mid chart lives here. Missing files stay
   **UNAVAILABLE**; there is no edge, no strategy PnL and no 72h claim
   mid-run.
4. **`/paper` (COURSE-1 soak only):** What ran, why the last decision was
   accepted or rejected, assumed-overlay results, the intent tape and the
   read-only preflight caps. DATA retain stays a separate identity card.
5. **`/system`:** run binding and picker, DATA-1A detail, the D01 /
   `paper_risk` bind, the Binance `usdm_public` callout, the reconstructable
   risk overlay and the documented #65 gate catalog. Start/stop is vetoed.

### Cross-cutting display rules

- **One refresh clock.** `CockpitRefreshProvider` owns a single token; every
  polled panel keys its fetch on it, so no zone can sit on stale numbers while
  the rest of the page has moved on. The topbar shows the last tick and can
  pause or force a refresh.
- **Missing ≠ stale ≠ error.** `lib/data-state.ts` is the shared vocabulary:
  `missing` was never written, `stale` is past the freshness bound, `error` is
  present-but-unreadable, `pending` is written-at-stop. Notices and badges are
  coloured from that enum, never ad hoc.
- **Charts are created once.** The Lightweight Charts instance and series are
  built on mount; refreshes call `series.setData` between a
  `getVisibleLogicalRange` / `setVisibleLogicalRange` pair so zoom and pan
  survive polling. Only first load and an interval change refit.
- **Verdicts are allowlisted.** `researchVerdictTone` greens only an
  explicitly positive verdict that is attributable to the bound runs;
  `not_enough_data` and anything unrecognised never borrow the success colour.
- **Summaries are attributed.** A `panel-summary.json` is matched to the bound
  `run_id`; a non-matching summary is still shown but flagged `mismatched` or
  `unknown` and never presented as a verdict about the current selection.
- **Examples are not history.** Documented `paper_risk` gate examples are a
  separate type from `IntentFillRow`, so an illustrative reject cannot be
  appended to a real intent tape.

PAPER is badged. The layout is dense and mobile-friendly. Inspiration is
professional workstation IA (shadcn/ui blocks, Tabler, TailAdmin free tier),
not a fork of a commercial exchange UI. Missing values stay **UNAVAILABLE**;
the cockpit never invents prices, PnL, leverage, margin, liquidation, VaR,
wallets, or L2 ladders.
Later screens must keep using the create-only reconstructable contracts rather
than inventing a second store:

```text
<artifact-root>/course1/live-public-paper/<run_id>/
  run-claim.json
  paper-position.json
  paper-pnl.json
  orders.json
  fills.json
  capture-health.json

<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
<artifact-root>/data-1f/binance/BTCUSDT/<run_id>/
<artifact-root>/data-1e/bitvavo/BTC-EUR/<run_id>/
<artifact-root>/data-1b/kraken/BTC-USD/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb

<artifact-root>/research-out/**/panel-summary.json
```

Optional `COCKPIT_RESEARCH_OUT` overrides the research-out root. `GET
/api/research-summaries` lists or reads `panel-summary.json` only. No
collectors. Missing files stay **UNAVAILABLE**.

`paper-pnl.json` is assumed PAPER overlay economics, not venue PnL. COURSE-1
`capture-health.json` is a bounded-run summary, not a 24/7 heartbeat. DATA-1A
`capture-claim.json` / `capture-health.json` are a reconstructable public capture
claim and an end-of-run health file. While a live retain has a claim, fresh
`raw/part-*.parquet` files (`now - last_part_mtime <=
COCKPIT_CAPTURE_FRESH_MAX_S=180`), and no health file, the panel shows
**RUNNING (health JSON pending until stop)** rather than invented zeros. A
stale last part mtime is **STALE (stale_mtime)**, not RUNNING. Host clock must
be sane.
The DATA-1A panel polls `/api/data1a-capture` every 5 seconds with
`Cache-Control: no-store`. The four-venue strip polls
`/api/venue-capture-health` on the same interval and fail-closes each missing
venue as **MISSING** (no invented zeros or PnL). When `ARTIFACT_ROOT` is set
and a venue run_id is unset, the strip / DATA-1A panel auto-detects the
freshest live retain on that path contract (claim + fresh parts, no health
JSON). Stale or stopped directories are listed in the run picker but are not
auto-bound as RUNNING. Missing artifact root remains an explicit empty state.
The strip is a compact table: status, last part age, part count, last
mtime, bind source, and run_id per venue. Gaps, raw reconnect attempts and
optional 5-second `reconnect_clusters` are copied from `capture-health.json`
when present and stay n/a while health is pending. The parser also preserves
the additive `close_code_rcvd` / `close_code_sent`, sanitized
`close_reason_rcvd` / `close_reason_sent`, `exception_class` and `errno`
fields when a health producer supplies them at capture or transport-profile
level. Current collectors persist those disconnect details in local markers
and capture logs rather than the end health summary, so the cockpit does not
invent or expose them when they are absent.
Overlap start is shown when one venue (often Binance) started later. DATA-1A capture duration is 1–604800
seconds; the COURSE-1 soak remains 1–600 seconds. Locally
the first screen defaults to
`tests/fixtures/course1_cockpit/live-public-soak/` (`run_id`
`20260904t001800z-live-paper`) and `tests/fixtures/data_1a_retained/sample-run/`
(`run_id` `sample-run`). Set `COCKPIT_ARTIFACT_ROOT` + `COCKPIT_RUN_ID` for PAPER
JSON, and `ARTIFACT_ROOT` plus optional `DATA1A_RUN_ID` (or `COCKPIT_DATA1A_RUN_ID` /
`?data1a_run_id=`) for a live DATA-1A directory. Optional sibling run ids on the
same root: `DATA1F_RUN_ID` (Binance), `DATA1E_RUN_ID` (Bitvavo),
`DATA1B_RUN_ID` (Kraken). Primary VPS example:
`ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"` with
HL/Bitvavo/Kraken `20260905t232635z-live-retained` and Binance
`20260906t101559z-live-retained` (do not prefer stopped
`20260905t235830z-live-retained`; copy `apps/cockpit/.env.example`
to `apps/cockpit/.env.local`, or export in the WSL shell before `next dev`).
For a phone on the same Wi-Fi, bind `next dev --hostname 0.0.0.0` (`dev:lan`)
and open `http://<LAN-IP>:3001` (the PC's LAN IP) — not cellular. See `docs/DATA.md`,
`docs/runbooks/data1a-vps-retained-capture.md`,
`docs/runbooks/data1a-wsl-pc-retained-capture.md`,
`docs/runbooks/cockpit-first-paper-screen.md`, `apps/cockpit/README.md`, and
`vertical_slices/course1_live_public_paper/README.md`.

## Development model

Frontend development happens primarily on the Netcup Ubuntu 24.04 LTS VPS.
Use Cursor Remote SSH or a CLI agent there and forward the Next.js port over
SSH when browser access is needed. TerraPC/WSL2 remains a secondary option.

The local frontend uses:

- mock/fixture data;
- a local FastAPI instance ([control-service runbook](runbooks/control-service-local.md);
  cockpit remains on Next.js API routes and is not wired yet);
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

First PAPER slice (now on the first screen): compact run/mode banner, PAPER-only
fail-closed, bound capture provenance, and live vs stale chips reused from
`COCKPIT_CAPTURE_FRESH_MAX_S=180`. Not equity/PnL promotion and not RISK.

Later command center:

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

First PAPER slice (now on the first screen): bound HL BTC-PERP public mid from
existing `/api/public-btc-perp`, capture freshness on the same bound runs, and
fail-closed **UNAVAILABLE** for Binance / Bitvavo / Kraken last/BBO (those
quotes are not in cockpit APIs). Copied COURSE-1 soak `mark_price` is labeled
as a soak mark, not a live last and not venue PnL. No L2/L3 ladders.

Later TradingView/Bookmap-inspired market workspace:

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

First PAPER slice (now on the first screen): reconstructable PAPER position,
assumed overlay PnL labeled as assumed, fail-closed preflight bounds from
`run-claim.json`, COURSE-1 risk-rejection / 24/7 flags, and the same capture
live-vs-stale summary as DESK. Anything not in those contracts stays
**UNAVAILABLE**. Not a risk engine and not venue margin/liquidation.

Later professional risk workspace:

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

First PAPER slice (now on the first screen): Quant P0 only. Run registry,
capture health, WP-Q1 sufficiency, instrument identity, and overlap clock.
Hypothesis / OOS / H1 stay **UNAVAILABLE**. No edge, no mixed Spot+USDM
price, no promotion.

Later: experiment registry, backtest comparison, promotion gates, parameter
stability and paper-vs-backtest decay.

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
