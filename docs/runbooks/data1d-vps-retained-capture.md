# DATA-1D VPS runbook — retained Bitvavo Standard BTC-EUR capture

Status: operator runbook for the reconstructable CLI
`python -m hyperliquid_bot.bitvavo_standard_research`.
This is **not** a 24/7 service, not D22-B, and not LIVE trading.

Credential-free public Bitvavo Standard on `wss://ws.bitvavo.com/v2/` for
`BTC-EUR`: `trades`, `ticker`, and `book` (depth 1000). Optional `candles` is
flagged (`--include-candles` / `INCLUDE_CANDLES=1`) and is off by default.
Candle intervals follow the official Standard WebSocket candles subscription
([candles subscription](https://docs.bitvavo.com/docs/websocket-api/candles-subscription/)):
`1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`.

This path never falls back to DATA-1E Market Data Pro (`data-1e/...`, tmux
`bv-capture`, or `wss://ws-mdpro.bitvavo.com`). Standard and Pro may run
alongside each other with distinct tmux sessions and artifact trees.

For the Windows 11 + WSL2 Ubuntu operator PC (TerraPC), see
[data1d-wsl-pc-retained-capture.md](data1d-wsl-pc-retained-capture.md).
Cloud Agents are unsuitable for a multi-day retain and must not stop
`hl-capture`, `bn-capture`, `bv-capture`, `kr-capture`, or `cockpit`. The
active host profile is
[linux-vps-reference-profile.md](linux-vps-reference-profile.md).

**Do not start a multi-day DATA-1D retain without assignment.** Wait for CoS
to assign the window. Do not start a live Standard capture that would collide
with Pro `bv-capture` session naming — use **`bv-std-capture`** only.

## Duration contract

`hyperliquid_bot.bitvavo_standard_research` accepts an explicit duration of
**1 through 604800 seconds** (7 days). Example retained window: **259200** (72h).

| Window | Meaning |
| --- | --- |
| 1–600s | Historical DATA-1D smoke window. Still valid. |
| 600.1–604800s | Retained research capture (`retained: true`). |
| >604800s | Fail closed. |
| No duration / always-on | Not implemented. |

SIGINT or SIGTERM may stop earlier. A hard crash can lose the in-memory Parquet
segment; already published `raw/part-*.parquet` files remain readable.

## Disk estimate

Budget about **+3-5 GB per 72h** for Standard trades/ticker/book on BTC-EUR
(operator estimate; not a measured VPS rate). Optional candles add little
relative to the L2 book. The Netcup VPS durable volume has hundreds of GB free
headroom in normal PAPER capture posture — still confirm `df -h` before a
joint retain.

## Path contract

```text
<artifact-root>/data-1d/bitvavo/BTC-EUR/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1d_run_paths(artifact_root, run_id)`.

**Never** write under `data-1e/bitvavo/BTC-EUR/` from this collector.

`run_id` must be 1–64 lowercase ASCII letters, digits, dot, dash, or underscore.

## Operator helpers (tmux `bv-std-capture`)

```bash
export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export DURATION_SECONDS=259200
# optional candles:
# export INCLUDE_CANDLES=1
# export CANDLE_INTERVAL=1m

DURATION_SECONDS=259200 ./scripts/data1d_start.sh --check-only
# After CoS assignment + PHASE_A_CHUPA_OK=1 for 72h:
# DURATION_SECONDS=259200 ./scripts/data1d_start.sh
./scripts/data1d_status.sh
# Do not stop during an assigned 72h evidence window:
# ./scripts/data1d_stop.sh
```

Default tmux session: **`bv-std-capture`**. Never use `bv-capture` (Pro).

## Fail-closed start (direct CLI)

```bash
# From the Netcup Ubuntu 24.04 LTS VPS.
# Do not start until CoS assigns this window.
unset TRADING_MODE
unset D41_EXECUTION_MODE

export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export PYTHONPATH=src
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
export DURATION_SECONDS=14400

test ! -e "${ARTIFACT_ROOT}/data-1d/bitvavo/BTC-EUR/${RUN_ID}"
test ! -e "${ARTIFACT_ROOT}/data-1e/bitvavo/BTC-EUR/${RUN_ID}"

cd "$REPO_ROOT"
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.bitvavo_standard_research \
  --artifact-root "${ARTIFACT_ROOT}" \
  --run-id "${RUN_ID}" \
  --duration-seconds "${DURATION_SECONDS}"
# Optional:
#   --include-candles --candle-interval 1m
```

The reconstructable command is create-only. An existing run directory is refused.
Claim `feed` is `bitvavo-standard-btc-eur-trades-ticker-book` (or
`...-candles-<interval>`). `mdpro_fallback` and `data1e_path_fallback` are false.

## Coexistence with DATA-1E Pro

| Lane | Feed | tmux | Artifact prefix |
| --- | --- | --- | --- |
| DATA-1D | Standard public | `bv-std-capture` | `data-1d/bitvavo/BTC-EUR/` |
| DATA-1E | MD Pro authenticated | `bv-capture` | `data-1e/bitvavo/BTC-EUR/` |

Both may be alive at once. Stop helpers for one lane must never target the other.

## Protect a 72h evidence window

Do **not** stop a mid-window 72h evidence run. Cloud Agents must not stop
captures. If systemd supervises the collector, use `Type=simple`,
`KillSignal=SIGINT`, and `Restart=no`.

After stop, `duration_seconds` is the requested window and `elapsed_seconds`
is wall-clock time until stop. Collector log: `<run_dir>/capture-<run_id>.log`.

## How to stop

```bash
# Do not do this during an assigned 72h evidence window.
./scripts/data1d_stop.sh
# or: tmux send-keys -t bv-std-capture C-c
```

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.

## How to continue later (there is no resume)

```text
resume_policy: never resume or overwrite an existing DATA-1D run directory
```

To retain more data after a stop, start a **new** `run_id`.

## Explicitly not proven

- 24/7 collection or an always-on host service
- lossless WAL / crash-safe in-memory segment recovery
- Cloud Agent multi-day capture
- starting this capture in this PR
- Market Data Pro behavior or Pro/Standard equivalence
- D22-B venue-authoritative reconciliation
- funding settlement, signing, TESTNET, SHADOW, LIVE
- profitability or strategy promotion
