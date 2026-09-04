# Runbook — first PAPER cockpit screen

Status: local fixture-backed first screen. PAPER only. Not D22-B, not LIVE,
and not a 24/7 cockpit service.

## What the screen reads

The server reads COURSE-1 create-only JSON. It copies assumed overlay fields
and observed soak health. It does not invent PnL, position, or capture counts.

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

Cockpit CI lives in `.github/workflows/cockpit.yml`. It is a separate
workflow so the hashed D01 publication file `.github/workflows/ci.yml`
stays byte-identical.

## Fail closed

- `TRADING_MODE` unset or `PAPER` only
- JSON `mode` must be `PAPER`
- missing JSON fails the panel; zeros are not invented
- `LIVE` / `TESTNET` / `SHADOW` refuse to start the paper reader
