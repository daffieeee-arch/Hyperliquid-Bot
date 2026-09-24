# PAPER Operator Cockpit

A local Next.js operator workstation with a persistent sidebar and five
workspaces:

| Route | Answers |
|---|---|
| `/` | What is running, what needs a human, what research exists |
| `/markets` | Public HL mid and candles (5m/15m/1h/4h), bound instruments |
| `/research` | Run registry, capture health and WP-Q1 summaries tied to a run |
| `/paper` | COURSE-1 soak: what ran, why, assumed results, intent tape |
| `/system` | Run binding, DATA-1A detail, risk overlay, #65 gate catalog |

Every polled panel shares one refresh clock, so no zone drifts onto stale
numbers while the rest of the page moves on. The topbar shows the last tick
and can pause or force a refresh.

Three data states are kept apart everywhere: **missing** (never written),
**stale** (past the freshness bound) and **error** (present but unreadable).
`pending` covers written-at-stop artifacts where mid-run absence is expected.

Stack is €0 OSS: Next.js 16, Tailwind CSS v4, official free shadcn/ui (Radix;
sidebar/dashboard layout ideas only), Lucide, TanStack Table v9 (sorting),
recharts (real public closes only), TradingView Lightweight Charts
(Apache-2.0; `NOTICE`). Visual default **C Fail-Closed Amber** with optional
**Desk Dark**. Critique variants stay in `docs/design-previews/` only. Not a
clone of any commercial terminal and not a wholesale exchange-UI fork. PAPER
is badged. Missing fields stay **UNAVAILABLE**. Position and assumed overlay
PnL are labeled **not venue-reconciled**. Collectors are never started or
stopped from this UI.

The candle chart is created once and reused: refreshes call `setData` between
a `getVisibleLogicalRange` / `setVisibleLogicalRange` pair, so polling never
discards the zoom or pan you set. Documented `paper_risk` gate examples are a
separate type from tape rows and can never be mixed into run history.

The browser never signs orders and never holds keys. `TRADING_MODE` must be
unset or `PAPER`; `LIVE`, `TESTNET`, and `SHADOW` fail closed.

## What it shows

1. **DESK** compact run/mode banner: PAPER only (LIVE / TESTNET / SHADOW fail
   closed), bound paper-run identity, and live vs stale capture chips reused
   from `#54` freshness (`COCKPIT_CAPTURE_FRESH_MAX_S=180`). Bound run ids are
   copied from the strip provenance; they are not invented.
2. **MARKETS** first quote panel: last and BBO mid from the stored capture
   of every bound venue (HL / Binance / Bitvavo / Kraken). A missing tick
   stays **UNAVAILABLE**. The public HL mid is separate context and is not
   copied onto a venue row. Copied COURSE-1 `paper-pnl.json` `mark_price` is
   a soak mark, not a live last.
3. **RISK** first reconstructable overlay: PAPER position, assumed overlay PnL
   labeled as assumed and **not venue-reconciled**, fail-closed preflight
   bounds, COURSE-1 risk-rejection / 24/7 flags, the documented #65
   `paper_risk` gates (reduce-only-after-halt is the rule; per-run halt state
   stays **UNAVAILABLE**), and the DESK capture live-vs-stale summary.
   Leverage, margin, liquidation, VaR, and venue risk stay **UNAVAILABLE**.
4. Public Hyperliquid BTC-PERP mid from credentialless `POST /info` `allMids`,
   plus public `candleSnapshot` candles when that route answers. Neither is
   used to invent Paper PnL. Missing candles render **UNAVAILABLE**.
5. Paper position from reconstructable COURSE-1 JSON.
6. Assumed overlay Paper PnL from that same JSON. If the field is assumed /
   overlay, the screen says so. It never fabricates a second number.
7. COURSE-1 capture health from that same JSON. This is a bounded soak summary,
   not a 24/7 heartbeat.
8. Multi-venue capture-health strip for **HL / Binance / Bitvavo / Kraken**.
   Each chip copies status, last part age (from last `raw/part-*.parquet`
   mtime), and part count from that venue's reconstructable layout. A chip is
   **RUNNING** only when the claim is PAPER / fail-closed (signing off) **and**
   `now - last_part_mtime <= COCKPIT_CAPTURE_FRESH_MAX_S` (default **180**s,
   tunable). A present claim with a stale mtime is **STALE** (`stale_mtime`),
   not RUNNING.    Missing artifact root, venue directory, or `capture-claim.json` is
   **MISSING** — zeros and PnL are not invented. When `ARTIFACT_ROOT` is set
   and a venue run_id is unset, the cockpit auto-detects the freshest **live**
   retain (claim, fresh last part mtime, no health JSON) or stays MISSING.
   Operators can pick another claimed run from the strip picker
   (`?data1a_run_id=` / `?data1f_run_id=` / `?data1e_run_id=` /
   `?data1b_run_id=`). The browser polls `/api/venue-capture-health` every
   **5 seconds**. The strip is a compact HL/BN/BV/KR table: status, last
   part age, part count, last mtime, bind source, and run_id. Gaps/reconnects
   appear only when `capture-health.json` already has those fields (n/a while
   health is pending). Overlap start is shown when Binance (or any venue)
   started later than the others.
9. DATA-1A capture health from `capture-claim.json` plus optional
   `capture-health.json` and a cheap `raw/part-*.parquet` listing. While a live
   run has a claim, fresh `raw/part-*.parquet` files
   (`now - last_part_mtime <= COCKPIT_CAPTURE_FRESH_MAX_S=180`), and no health
   file, the panel shows **UNKNOWN (storage activity only; capture-health.json not written yet)** instead of
   a healthy RUNNING feed or invented zeros. A stale last part mtime is **STALE (stale_mtime)**, not
   RUNNING. Host clock must be sane (wall-clock compare). The browser polls
   `/api/data1a-capture` every **5 seconds**
   (   `cache: no-store`) so duration, published parts, bytes on disk, last part
   mtime, and `observed_at` update without a full page reload. Parquet payloads
   are not read. Missing artifact root fails closed. A missing run_id with
   `ARTIFACT_ROOT` auto-detects the freshest live DATA-1A retain or stays empty.
10. PAPER intent/fill blotter copied from `orders.json` / `fills.json`. Empty
   runs stay empty; rows are not invented.
11. Run provenance &amp; preflight from `run-claim.json`: `run_identity`,
   `config_sha256`, `source_sha256`, `websocket_url`, `resume_policy`, and the
   pre-submit risk caps (`strategy_class`, `order_quantity_btc`,
   `max_entry_notional_usdc`, `max_assumed_loss_usdc`, `same_d01_smoke_risk`).
   These fields are optional in the claim contract: when a run omits them the
   panel shows **not recorded** rather than fabricating an identity, and a
   present-but-malformed block fails the desk closed.

## JSON it reads

Default local fixture (canonical example `run_id`):

```text
tests/fixtures/course1_cockpit/live-public-soak/
  run-claim.json
  paper-position.json
  paper-pnl.json
  orders.json
  fills.json
  capture-health.json
```

Those file names match the COURSE-1 path contract. VPS artifacts use the
same names under:

```text
<artifact-root>/course1/live-public-paper/<run_id>/
```

DATA-1A defaults to `tests/fixtures/data_1a_retained/sample-run/`. A live
reconstructable run uses the same claim / health / `raw/part-*.parquet` layout
as the other public venues:

```text
<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
<artifact-root>/data-1f/binance/BTCUSDT/<run_id>/
<artifact-root>/data-1e/bitvavo/BTC-EUR/<run_id>/
<artifact-root>/data-1b/kraken/BTC-USD/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

The strip always renders four chips. Venues without a pointed root/claim, and
venues with `ARTIFACT_ROOT` but no live retain and no selected run_id, show
**MISSING**. There is no committed Binance / Bitvavo / Kraken cockpit fixture;
do not invent sample counts.

## Run locally

From the repository root, with `TRADING_MODE=PAPER` or unset:

```bash
pnpm install --frozen-lockfile
pnpm --filter @hyperliquid-bot/cockpit dev
```

Open `http://127.0.0.1:3000`.

### VPS: Tailscale Serve (preferred remote access)

On the Netcup VPS, keep `next dev` on **localhost only**. Remote browsers use
Tailscale Serve — URL pattern `https://chupa.<tailnet>.ts.net` — never a
public `:3000`. List that Serve hostname in `next.config.ts`
`allowedDevOrigins` (already set for the active tailnet host). SSH forward to
`127.0.0.1:3000` remains valid when Serve is off.

LAN phone on the same Wi-Fi (not cellular). Official Next.js `next dev`
`-H` / `--hostname` binds the hostname; `0.0.0.0` listens on all interfaces.
`PORT` must be set in the shell (Next.js starts the HTTP server before `.env`
files load; `dev:lan` therefore omits `--port` so `PORT=3001` wins). TerraPC
example (cockpit on **3001**):

```bash
PORT=3001 pnpm --filter @hyperliquid-bot/cockpit dev:lan
```

Then open `http://<LAN-IP>:3001` from the phone (use the PC's LAN IP on the
same Wi-Fi, for example `http://192.168.x.x:3001`). Allow inbound TCP on that
port in the Windows / WSL firewall. `127.0.0.1` is loopback only. Cellular /
4G will not reach the home LAN.

To read a path-contract directory instead of the fixture:

```bash
export TRADING_MODE=PAPER
export COCKPIT_ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export COCKPIT_RUN_ID=20260904t001800z-live-paper
pnpm --filter @hyperliquid-bot/cockpit dev
```

To point at any unpacked run folder that already contains those six JSON files:

```bash
export COCKPIT_PAPER_RUN_DIR=tests/fixtures/course1_cockpit/sample-run
```

To watch a DATA-1A reconstructable capture on the same machine (do not stop the
collector). Next.js loads `apps/cockpit/.env.local`; the repository-root
`.env.example` is documentation only.

Netcup Ubuntu 24.04 LTS VPS (primary capture host). The recommended binding is
**auto-detect**: set only `ARTIFACT_ROOT` and leave every
venue run_id unset. Per venue the cockpit then binds the freshest run that has a
`capture-claim.json`, no `capture-health.json` (i.e. not stopped) and a
`raw/part-*.parquet` newer than `COCKPIT_CAPTURE_FRESH_MAX_S` (180s). A venue
that restarted — Binance after DATA-1F #58, for example — is therefore picked
up by its new run directory without editing any configuration, and a stopped
run is never bound as RUNNING:

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
pnpm --filter @hyperliquid-bot/cockpit dev
```

Before pinning a run ID by hand, check what is actually on disk. Recorded IDs
describe one moment and go stale as soon as a collector restarts:

```bash
ls -lt "$ARTIFACT_ROOT"/data-1f/binance/BTCUSDT/          # newest run dir first
ls -lt "$ARTIFACT_ROOT"/data-1f/binance/BTCUSDT/<run>/raw | head   # parts still growing?
```

Explicit env / query still wins over auto-detect when you need to inspect a
specific (possibly historical) run — every screen then labels it
`HISTORICAL RUN` instead of `LIVE CAPTURE`:

```bash
export DATA1A_RUN_ID=<run id you verified above>
export DATA1F_RUN_ID=<run id you verified above>
```

Or copy `apps/cockpit/.env.example` to `apps/cockpit/.env.local`.
`COCKPIT_DATA1A_RUN_ID` is an equivalent alias. With `ARTIFACT_ROOT` already
exported you can also open `http://127.0.0.1:3000/?data1a_run_id=<run id>`.

Query aliases: `?data1f_run_id=`, `?data1e_run_id=`, `?data1b_run_id=`.
`COCKPIT_DATA1F_RUN_ID` / `COCKPIT_DATA1E_RUN_ID` / `COCKPIT_DATA1B_RUN_ID`
are equivalent. Query overrides env. Auto-detect runs only when that venue
run_id is unset. Stale or stopped retains are listed in the picker but are
never auto-bound as RUNNING.

The DATA-1A panel polls `/api/data1a-capture` every 5 seconds. The venue strip
polls `/api/venue-capture-health` on the same interval. RESEARCH reads
`GET /api/research-summaries` for `panel-summary.json` under
`COCKPIT_RESEARCH_OUT` or `ARTIFACT_ROOT/research-out`. Leave the tab open; do
not stop any collector to "refresh" numbers. Routes send
`Cache-Control: no-store`. Optional: `COCKPIT_CAPTURE_FRESH_MAX_S=180` (seconds;
tunable). Host clock must be sane.

Primary VPS path-contract root (same file names):

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
pnpm --filter @hyperliquid-bot/cockpit dev
```

A missing root fails closed. A missing venue run_id with `ARTIFACT_ROOT`
auto-detects a live retain or stays MISSING. COURSE-1 PAPER JSON stays on the
fixture unless `COCKPIT_ARTIFACT_ROOT` + `COCKPIT_RUN_ID` are also set. DESK,
MARKETS, and RISK reuse those same binds. Missing RISK fields stay
UNAVAILABLE.

### Data origin, read time and source time

Every screen carries three separate signals so a ticking clock is never taken
as proof of fresh data:

- **Origin badge** (`LIVE CAPTURE` / `HISTORICAL RUN` / `DEMO FIXTURE` /
  `UNBOUND` / `MIXED SOURCES`): where the numbers come from. The topbar badge
  summarises the bound venues; each page head and each venue card repeats its
  own. The repository fixture is always `DEMO FIXTURE`, even when it is fresh.
- **read HH:MM:SSZ**: the last *successful* backend read, per panel. A failed
  tick turns it red (`read failed …`), keeps the previous values on screen and
  adds a "request failed" item to the Overview attention list.
- **Source time** (`newest part`, `last event`, `registry read`): the timestamp
  the data itself carries. `data … old` on a venue card is the age of the newest
  stored event, not of the read.

Finished PAPER runs stay on screen as `HISTORICAL SNAPSHOT`; they are re-read on
every tick but their values do not change.

### Stored market data (`/api/market-tape`)

Markets, Overview and System show what the collectors actually wrote: last
trade, best bid/offer, spread (quote units and bps), recent trades, published
part count and volume, newest part, last data time and publication lag. Spot,
perpetual and quote currency are separate rows keyed by the collector's own
product string (`BTC-PERP`, `BTCUSDT-SPOT`, `BTCUSDT-USDS-M-PERPETUAL`,
`BTC-EUR`, `BTC/USD`).

The backend reads the published `raw/part-*.parquet` files directly (DuckDB
ZSTD Parquet via `hyparquet`; the writer's hidden `.partial` files are never
opened). Parsed parts are cached per run in the Node process, so a browser
refresh only costs the parts published since the previous read. On the first
visit to a run only the newest three parts are decoded; older parts are counted
towards `published`/volume but not re-read (`… older counted only`). Values
therefore lag the collector by at most one unpublished segment (≤60s or 5 000
records); the card shows that lag explicitly. No venue API is called and no
credentials are involved.

To exercise the whole chain without touching a real collector, generate a
throw-away simulated retain and publish extra parts into it:

```bash
PYTHONPATH=src python3 tests/fixtures/market_tape/generate_fixtures.py \
  --out /tmp/sim-retain --base-utc now
TRADING_MODE=PAPER ARTIFACT_ROOT=/tmp/sim-retain pnpm --filter @hyperliquid-bot/cockpit dev
# later, while the cockpit is open:
PYTHONPATH=src python3 tests/fixtures/market_tape/generate_fixtures.py \
  --out /tmp/sim-retain --base-utc now --append-part
```

The new part appears on the next refresh tick; after 180s without a new part
auto-detect stops binding the simulated runs and the venues go MISSING, exactly
as they would for a stopped collector.

See `docs/runbooks/cockpit-first-paper-screen.md`,
`docs/runbooks/data1a-wsl-pc-retained-capture.md`, and
`docs/runbooks/data1a-vps-retained-capture.md`.
