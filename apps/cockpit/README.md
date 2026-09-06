# First PAPER cockpit screen

One local Next.js screen. PAPER Operator Cockpit workstation: **DESK** banner,
separate COURSE-1 soak vs DATA retain identity cards, **MARKETS** (public HL
mid + public candle chart), RESEARCH stub, PAPER bot what/why/results,
**RISK**, and the existing four-venue strip / picker / DATA-1A panel.

Stack is €0 OSS: Next.js 15, Tailwind CSS v4, shadcn/ui (Radix), Lucide,
TradingView Lightweight Charts. Visual variants: **A terminal**, **B shadcn
workstation**, **C hybrid** (default). Not a clone of any commercial terminal
and not a wholesale exchange-UI fork. PAPER is badged and watermarked.
Missing fields stay **UNAVAILABLE**. Position and assumed overlay PnL are
labeled **not venue-reconciled**. Collectors are never started or stopped
from this UI.

The browser never signs orders and never holds keys. `TRADING_MODE` must be
unset or `PAPER`; `LIVE`, `TESTNET`, and `SHADOW` fail closed.

## What it shows

1. **DESK** compact run/mode banner: PAPER only (LIVE / TESTNET / SHADOW fail
   closed), bound paper-run identity, and live vs stale capture chips reused
   from `#54` freshness (`COCKPIT_CAPTURE_FRESH_MAX_S=180`). Bound run ids are
   copied from the strip provenance; they are not invented.
2. **MARKETS** first quote panel: bound HL BTC-PERP public mid from
   `/api/public-btc-perp`. Binance / Bitvavo / Kraken last/BBO stay
   **UNAVAILABLE** because those quotes are not in cockpit APIs. Copied
   COURSE-1 `paper-pnl.json` `mark_price` is a soak mark, not a live last.
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
   file, the panel shows **RUNNING (health JSON pending until stop)** instead of
   inventing zeros. A stale last part mtime is **STALE (stale_mtime)**, not
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

Those file names match the COURSE-1 path contract. Later VPS artifacts use the
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

LAN phone on the same Wi-Fi (not cellular). Official Next.js `next dev`
`-H` / `--hostname` binds the hostname; `0.0.0.0` listens on all interfaces.
`PORT` must be set in the shell (Next.js starts the HTTP server before `.env`
files load; `dev:lan` therefore omits `--port` so `PORT=3001` wins). TerraPC
example (cockpit on **3001**):

```bash
PORT=3001 pnpm --filter @hyperliquid-bot/cockpit dev:lan
```

Then open `http://192.168.1.2:3001` from the phone. Allow inbound TCP on that
port in the Windows / WSL firewall. `127.0.0.1` is loopback only. Cellular /
4G will not reach the home LAN.

To read a path-contract directory instead of the fixture:

```bash
export TRADING_MODE=PAPER
export COCKPIT_ARTIFACT_ROOT=/var/lib/hyperliquid-bot/reconstructable
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

TerraPC WSL2 (current 72h retain; Linux filesystem, not `/mnt/c`).
HL / Bitvavo / Kraken share `20260905t232635z-live-retained`. Binance is the
post-#58 retain `20260906t101559z-live-retained`. Do not prefer the stopped
earlier Binance id `20260905t235830z-live-retained`.
Leaving the venue run_ids unset auto-detects the freshest live retain on each
path contract. Explicit env / query still wins:

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/home/dmesdary/hyperliquid-artifacts/reconstructable
# Optional; omit to auto-detect the freshest live retain per venue:
export DATA1A_RUN_ID=20260905t232635z-live-retained
export DATA1E_RUN_ID=20260905t232635z-live-retained
export DATA1B_RUN_ID=20260905t232635z-live-retained
export DATA1F_RUN_ID=20260906t101559z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

Or copy `apps/cockpit/.env.example` to `apps/cockpit/.env.local` and uncomment
the TerraPC lines. `COCKPIT_DATA1A_RUN_ID` is an equivalent alias.
With `ARTIFACT_ROOT` already exported you can also open
`http://127.0.0.1:3000/?data1a_run_id=20260905t232635z-live-retained`.

Query aliases: `?data1f_run_id=`, `?data1e_run_id=`, `?data1b_run_id=`.
`COCKPIT_DATA1F_RUN_ID` / `COCKPIT_DATA1E_RUN_ID` / `COCKPIT_DATA1B_RUN_ID`
are equivalent. Query overrides env. Auto-detect runs only when that venue
run_id is unset. Stale or stopped retains are listed in the picker but are
never auto-bound as RUNNING.

The DATA-1A panel polls `/api/data1a-capture` every 5 seconds. The venue strip
polls `/api/venue-capture-health` on the same interval. Leave the tab open; do
not stop any collector to "refresh" numbers. Both routes send
`Cache-Control: no-store`. Optional: `COCKPIT_CAPTURE_FRESH_MAX_S=180` (seconds;
tunable). Host clock must be sane.

Later VPS path-contract root (same file names):

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/var/lib/hyperliquid-bot/reconstructable
export DATA1A_RUN_ID=20260904t134940z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

A missing root fails closed. A missing venue run_id with `ARTIFACT_ROOT`
auto-detects a live retain or stays MISSING. COURSE-1 PAPER JSON stays on the
fixture unless `COCKPIT_ARTIFACT_ROOT` + `COCKPIT_RUN_ID` are also set. DESK,
MARKETS, and RISK reuse those same binds. Missing RISK fields stay
UNAVAILABLE.

See `docs/runbooks/cockpit-first-paper-screen.md`,
`docs/runbooks/data1a-wsl-pc-retained-capture.md`, and
`docs/runbooks/data1a-vps-retained-capture.md`.
