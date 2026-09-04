# Runbook — first PAPER cockpit screen

Status: local fixture-backed first screen with a dense dark PAPER terminal
layout. PAPER only. Not D22-B, not LIVE, and not a 24/7 cockpit service. The
browser never signs orders. The visual chrome is not a clone of a commercial
terminal.

## What the screen reads

The server reads COURSE-1 create-only JSON and a separate DATA-1A reconstructable
capture directory. It copies assumed overlay fields, observed soak health, and
DATA-1A claim/health plus a cheap parquet-part listing. It does not invent PnL,
position, or capture counts.

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

## Open locally

```bash
export TRADING_MODE=PAPER
pnpm install --frozen-lockfile
pnpm --filter @hyperliquid-bot/cockpit dev
```

Open `http://127.0.0.1:3000`.

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

TerraPC WSL2 (current live retain). Paths are on the Linux filesystem, not
`/mnt/c`:

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/home/dmesdary/hyperliquid-artifacts/reconstructable
export DATA1A_RUN_ID=20260904t134940z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

`COCKPIT_DATA1A_RUN_ID` is an equivalent alias for `DATA1A_RUN_ID`. Or keep
`ARTIFACT_ROOT` and open
`http://127.0.0.1:3000/?data1a_run_id=20260904t134940z-live-retained`.

Later VPS path-contract root (same file names):

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/var/lib/hyperliquid-bot/reconstructable
export DATA1A_RUN_ID=20260904t134940z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

Path helper: `data1a_run_paths(artifact_root, run_id)`. Health JSON is written at
process end. While the run is live (claim present, published `raw/part-*.parquet`
files growing, no `capture-health.json`) the panel shows
**RUNNING (health JSON pending until stop)** plus filesystem part count / last
mtime, and `n/a` for gaps/reconnects. Missing root or `run_id` is an explicit
empty state; zeros and PnL are not invented.

Cockpit CI lives in `.github/workflows/cockpit.yml`. It is a separate
workflow so the hashed D01 publication file `.github/workflows/ci.yml`
stays byte-identical.

## Fail closed

- `TRADING_MODE` unset or `PAPER` only
- COURSE-1 JSON `mode` must be `PAPER`
- missing JSON fails the matching panel; zeros are not invented
- missing DATA-1A artifact root or `run_id` is an explicit empty capture panel
- DATA-1A `twenty_four_seven: true` or `signing: true` is refused
- `LIVE` / `TESTNET` / `SHADOW` refuse to start the paper reader and DATA-1A view
- DESK / MARKETS / RISK screens are not built and must not be filled with fake data
