# First PAPER cockpit screen

One local Next.js screen. It is not DESK, MARKETS, RISK, or a clone of any
commercial terminal. The first screen uses a dense dark workstation layout so
price, position, assumed overlay PnL, COURSE-1 soak health, and DATA-1A capture
health are readable at a glance. PAPER is badged in the masthead and watermarked
on the page.

The browser never signs orders and never holds keys. `TRADING_MODE` must be
unset or `PAPER`; `LIVE`, `TESTNET`, and `SHADOW` fail closed.

## What it shows

1. Public Hyperliquid BTC-PERP mid from credentialless `POST /info` `allMids`.
   This price is not used to invent Paper PnL.
2. Paper position from reconstructable COURSE-1 JSON.
3. Assumed overlay Paper PnL from that same JSON. If the field is assumed /
   overlay, the screen says so. It never fabricates a second number.
4. COURSE-1 capture health from that same JSON. This is a bounded soak summary,
   not a 24/7 heartbeat.
5. DATA-1A capture health from `capture-claim.json` plus optional
   `capture-health.json` and a cheap `raw/part-*.parquet` listing. While a live
   run has a claim, growing `raw/part-*.parquet` files, and no health file, the
   panel shows **RUNNING (health JSON pending until stop)** instead of inventing
   zeros. Parquet payloads are not read. Missing artifact root or `run_id`
   fails closed with an explicit empty state.
6. PAPER intent/fill blotter copied from `orders.json` / `fills.json`. Empty
   runs stay empty; rows are not invented.

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
reconstructable run uses:

```text
<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

## Run locally

From the repository root, with `TRADING_MODE=PAPER` or unset:

```bash
pnpm install --frozen-lockfile
pnpm --filter @hyperliquid-bot/cockpit dev
```

Open `http://127.0.0.1:3000`.

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

TerraPC WSL2 (current live retain; Linux filesystem, not `/mnt/c`):

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/home/dmesdary/hyperliquid-artifacts/reconstructable
export DATA1A_RUN_ID=20260904t134940z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

Or copy `apps/cockpit/.env.example` to `apps/cockpit/.env.local` and uncomment
those two DATA-1A lines. `COCKPIT_DATA1A_RUN_ID` is an equivalent alias.
With `ARTIFACT_ROOT` already exported you can also open
`http://127.0.0.1:3000/?data1a_run_id=20260904t134940z-live-retained`.

Later VPS path-contract root (same file names):

```bash
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/var/lib/hyperliquid-bot/reconstructable
export DATA1A_RUN_ID=20260904t134940z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

A missing root or `run_id` fails closed. COURSE-1 PAPER JSON stays on the
fixture unless `COCKPIT_ARTIFACT_ROOT` + `COCKPIT_RUN_ID` are also set. DESK /
MARKETS / RISK screens are not built.

See `docs/runbooks/cockpit-first-paper-screen.md`,
`docs/runbooks/data1a-wsl-pc-retained-capture.md`, and
`docs/runbooks/data1a-vps-retained-capture.md`.
