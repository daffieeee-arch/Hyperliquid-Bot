# Runbook — first PAPER cockpit screen

Status: local fixture-backed PAPER Operator Cockpit workstation (Tailwind v4 +
official free shadcn/ui + Lucide + TanStack Table + recharts KPI sparklines +
Lightweight Charts; default C Fail-Closed Amber, optional A Desk Dark).
Sticky DESK chrome, then Health → Markets → Research (Quant P0) → PAPER →
Risk. PAPER only.
Not D22-B, not LIVE, not a risk engine, and not a 24/7 cockpit service. The
browser never signs orders and never starts/stops collectors. The visual
chrome is not a clone of a commercial terminal.

## What the screen reads

The server reads COURSE-1 create-only JSON and reconstructable capture
directories. It copies assumed overlay fields, observed soak health, DATA-1A
claim/health plus a cheap parquet-part listing, and a four-venue capture-health
strip (HL / Binance / Bitvavo / Kraken). **DESK** shows PAPER mode, the bound
paper run, and live vs stale capture chips from that strip
(`COCKPIT_CAPTURE_FRESH_MAX_S=180`). **MARKETS** shows the public Hyperliquid
BTC-PERP mid already used by the cockpit and fail-closes sibling last/BBO as
**UNAVAILABLE** (those quotes are not in cockpit APIs). **RISK** copies
reconstructable PAPER position, assumed overlay PnL, fail-closed preflight
bounds, and the capture live/stale summary; leverage, margin, liquidation, VaR,
and venue risk stay **UNAVAILABLE**. It does not invent PnL, position, prices,
or capture counts. Missing venue roots, run ids, directories, or claims are
explicit **MISSING** chips.

Canonical local fixture, `run_id` `20260904t001800z-live-paper`:

```text
tests/fixtures/course1_cockpit/live-public-soak/
  run-claim.json
  paper-position.json
  paper-pnl.json
  orders.json
  fills.json
  capture-health.json
```

Path contract for later VPS artifacts (same file names, no schema change):

```text
<artifact-root>/course1/live-public-paper/<run_id>/
  run-claim.json
  paper-position.json
  paper-pnl.json
  orders.json
  fills.json
  capture-health.json
```

`paper-pnl.json` is assumed D01 overlay economics (`assumed: true`,
`venue_pnl: false`, `funding_payment_usdc: "0"`). `capture-health.json` is a
bounded soak summary (`twenty_four_seven: false`).

Public BTC-PERP mid is fetched separately from Hyperliquid
`https://api.hyperliquid.xyz/info` with `{"type":"allMids"}`. No credentials.
The browser never talks to a signing endpoint.

## Open DESK / MARKETS / RISK locally

Same first PAPER screen. DESK is the sticky run/mode banner with hash nav
Health → Markets → Research → PAPER → Risk. Health holds the strip/picker.
MARKETS is the quote table. RESEARCH copies Quant P0 claims/health and optional
`research-out/**/panel-summary.json` (`GET /api/research-summaries`); missing
files stay **UNAVAILABLE**. RISK is the dense reconstructable overlay /
fail-closed bounds panel. Collectors stay running; the cockpit only reads
files. Missing RISK fields stay **UNAVAILABLE**.

```bash
export TRADING_MODE=PAPER
pnpm install --frozen-lockfile
pnpm --filter @hyperliquid-bot/cockpit dev
```

Open `http://127.0.0.1:3000`. Fixture PAPER JSON and the DATA-1A sample run
are enough to render DESK + MARKETS + RISK; sibling venue quotes and
leverage/margin/liquidation/VaR stay UNAVAILABLE.

## Point at VPS / reconstructable artifacts later

```bash
export TRADING_MODE=PAPER
export COCKPIT_ARTIFACT_ROOT=/var/lib/hyperliquid-bot/reconstructable
export COCKPIT_RUN_ID=20260904t001800z-live-paper
pnpm --filter @hyperliquid-bot/cockpit dev
```

The helper shape is `course1_cockpit_paths(artifact_root, run_id)` from
`src/hyperliquid_bot/reconstructable_paths.py`.

## Point at a DATA-1A reconstructable capture

Do not stop a running collector. The cockpit only reads files. Next.js loads
`apps/cockpit/.env.local` during `next dev`; exporting the same names in the
WSL shell also works. The repository-root `.env.example` is not loaded by
Next.js.

TerraPC WSL2 (current 72h retain). Paths are on the Linux filesystem, not
`/mnt/c`. HL / Bitvavo / Kraken share `20260905t232635z-live-retained`.
Binance is the post-#58 retain `20260906t101559z-live-retained`. Do not
prefer the stopped earlier Binance id `20260905t235830z-live-retained`.
Leaving venue run_ids unset auto-detects the freshest live retain per path
contract:

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/home/dmesdary/hyperliquid-artifacts/reconstructable
export DATA1A_RUN_ID=20260905t232635z-live-retained
export DATA1E_RUN_ID=20260905t232635z-live-retained
export DATA1B_RUN_ID=20260905t232635z-live-retained
export DATA1F_RUN_ID=20260906t101559z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

`COCKPIT_DATA1A_RUN_ID` is an equivalent alias for `DATA1A_RUN_ID`. Or keep
`ARTIFACT_ROOT` and open
`http://127.0.0.1:3000/?data1a_run_id=20260905t232635z-live-retained`.
DESK live-vs-stale chips and MARKETS bound-instrument rows use the same binds.
Sibling last/BBO stay UNAVAILABLE.

LAN phone on the same Wi-Fi (not 4G). Official Next.js `next dev --hostname 0.0.0.0`
listens on all interfaces (`pnpm --filter @hyperliquid-bot/cockpit dev:lan`).
Set `PORT` in the shell — Next.js does not read `PORT` from `.env`:

```bash
PORT=3001 pnpm --filter @hyperliquid-bot/cockpit dev:lan
```

Open `http://192.168.1.2:3001` from the phone. Allow inbound TCP on that port
on the Windows / WSL firewall. `127.0.0.1` is loopback-only.

Later VPS path-contract root (same file names):

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/var/lib/hyperliquid-bot/reconstructable
export DATA1A_RUN_ID=20260904t134940z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

Path helper: `data1a_run_paths(artifact_root, run_id)`. Health JSON is written at
process end. While the run is live (claim present, published `raw/part-*.parquet`
files with `now - last_part_mtime <= COCKPIT_CAPTURE_FRESH_MAX_S=180`, no
`capture-health.json`) the panel shows
**RUNNING (health JSON pending until stop)** plus filesystem part count / last
mtime, and `n/a` for gaps/reconnects. A present claim with a stale last part
mtime is **STALE (stale_mtime)**, not RUNNING. Host clock must be sane. The
browser polls `/api/data1a-capture` every **5 seconds** with `cache: no-store`
so duration, published parts, bytes on disk, last part mtime, and `observed_at`
update without a full page reload. Missing root or `run_id` is an explicit empty
state; zeros and PnL are not invented.

## Point at multi-venue reconstructable captures

The first PAPER screen also shows a dense HL / Binance / Bitvavo / Kraken
strip (compact table: status, age, parts, last mtime, optional
gaps/reconnects from `capture-health.json`, bind, run_id). Each row reuses
the DATA-1A claim / health / `raw/part-*.parquet` pattern on that venue's
documented path contract. Shared artifact root, no secrets, do not stop any
collector:

```text
<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
<artifact-root>/data-1f/binance/BTCUSDT/<run_id>/
<artifact-root>/data-1e/bitvavo/BTC-EUR/<run_id>/
<artifact-root>/data-1b/kraken/BTC-USD/<run_id>/
```

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/home/dmesdary/hyperliquid-artifacts/reconstructable
# Optional; omit to auto-detect the freshest live retain per venue:
export DATA1A_RUN_ID=20260905t232635z-live-retained
export DATA1F_RUN_ID=20260906t101559z-live-retained
export DATA1E_RUN_ID=20260905t232635z-live-retained
export DATA1B_RUN_ID=20260905t232635z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

Query aliases: `?data1a_run_id=`, `?data1f_run_id=`, `?data1e_run_id=`,
`?data1b_run_id=`. The strip polls `/api/venue-capture-health` every 5 seconds.
The run picker writes those query params. Auto-detect never binds a stale or
stopped retain as RUNNING. Provenance shows the bound `run_id` per venue and
the comparable overlap start when one venue started later.
A chip is **RUNNING** only when that venue has a PAPER / fail-closed claim
(signing off), a last `raw/part-*.parquet` mtime within
`COCKPIT_CAPTURE_FRESH_MAX_S=180` (tunable), and no health file yet. A present
claim with a stale mtime is **STALE** (`stale_mtime`), not RUNNING. **STOPPED**
copies a finished health status (COMPLETED / OPERATOR_STOP). **DEGRADED** is an
unreadable/failed/not-written health file. **MISSING** is fail-closed empty (no
root, run, directory, or claim). Part age and part count stay `n/a` on MISSING
chips. Host clock must be sane.

Cockpit CI lives in `.github/workflows/cockpit.yml`. It is a separate
workflow so the hashed D01 publication file `.github/workflows/ci.yml`
stays byte-identical.

## Fail closed

- `TRADING_MODE` unset or `PAPER` only
- COURSE-1 JSON `mode` must be `PAPER`
- missing JSON fails the matching panel; zeros are not invented
- missing DATA-1A artifact root is an explicit empty capture panel
- `ARTIFACT_ROOT` without a venue run_id auto-detects a live retain or stays
  empty / **MISSING**; stale files are not auto-bound as RUNNING
- missing Binance / Bitvavo / Kraken root, directory, or claim is a
  **MISSING** strip chip; zeros are not invented
- DATA-1A `twenty_four_seven: true` or `signing: true` is refused
- `LIVE` / `TESTNET` / `SHADOW` refuse to start the paper reader, DATA-1A view,
  venue strip, DESK glance, and MARKETS public mid
- MARKETS never invents a last/BBO; sibling venues stay **UNAVAILABLE**
- RISK copies only reconstructable PAPER / COURSE-1 / strip fields; missing
  leverage, margin, liquidation, VaR, and venue risk stay **UNAVAILABLE**
