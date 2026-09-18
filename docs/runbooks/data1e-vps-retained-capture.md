# DATA-1E VPS runbook — retained Bitvavo MD Pro BTC-EUR capture

Status: operator runbook for the reconstructable CLI
`python -m hyperliquid_bot.bitvavo_mdpro_research`.
This is **not** a 24/7 service, not D22-B, and not LIVE trading.

Authenticated View-only Bitvavo Market Data Pro on the same Pro socket for
`BTC-EUR`: `book` (depth 1000) plus `trades`. Optional `ticker` is flagged
(`--include-ticker`) and is off by default. Never DATA-1D Standard.
No trade keys, no signing, no OKX.

For the Windows 11 + WSL2 Ubuntu operator PC (TerraPC), including tmux
`bv-capture` start/status/stop, see
[data1e-wsl-pc-retained-capture.md](data1e-wsl-pc-retained-capture.md).
Cloud Agents are unsuitable for a multi-day retain and must not stop
`hl-capture`, `bn-capture`, or `bv-capture`. The active host profile is
[linux-vps-reference-profile.md](linux-vps-reference-profile.md).

**Do not start a multi-day DATA-1E retain without assignment.** Wait for CoS
to assign the window via the joint Phase A checklist on this primary VPS.

## Duration contract

`hyperliquid_bot.bitvavo_mdpro_research` accepts an explicit duration of
**1 through 604800 seconds** (7 days).

| Window | Meaning |
| --- | --- |
| 1–600s | Historical DATA-1E smoke window. Still valid. |
| 600.1–604800s | Retained research capture (`retained: true`). |
| >604800s | Fail closed. |
| No duration / always-on | Not implemented. |

SIGINT or SIGTERM may stop earlier. A hard crash can lose the in-memory Parquet
segment; already published `raw/part-*.parquet` files remain readable.

## Path contract

```text
<artifact-root>/data-1e/bitvavo/BTC-EUR/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1e_run_paths(artifact_root, run_id)`.

`run_id` must be 1–64 lowercase ASCII letters, digits, dot, dash, or underscore.

## Auth env names

Required (values never printed): `BITVAVO_MDPRO_API_KEY`,
`BITVAVO_MDPRO_API_SECRET`. View-only only.

Refuse generic `BITVAVO_API_KEY` / `BITVAVO_API_SECRET` and other protected
trade/signing names listed in the WSL runbook.

Default channels on `wss://ws-mdpro.bitvavo.com/v2/`: `book` + `trades`, then
`getBook` depth 1000. Add `--include-ticker` only when CoS wants the optional
Pro ticker on the same socket. Claim `feed` is
`bitvavo-mdpro-btc-eur-book-trades` (or `...-book-trades-ticker`).
`standard_fallback` is false.

## Fail-closed start

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

test -n "${BITVAVO_MDPRO_API_KEY:-}"
test -n "${BITVAVO_MDPRO_API_SECRET:-}"
test ! -e "${ARTIFACT_ROOT}/data-1e/bitvavo/BTC-EUR/${RUN_ID}"

cd "$REPO_ROOT"
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.bitvavo_mdpro_research \
  --artifact-root "${ARTIFACT_ROOT}" \
  --run-id "${RUN_ID}" \
  --duration-seconds "${DURATION_SECONDS}"
```

The reconstructable command is create-only. An existing run directory is refused.

## Protect a 72h evidence window

Do **not** stop a mid-window 72h evidence run. Cloud Agents must not stop
captures. If systemd supervises the collector, use `Type=simple`,
`KillSignal=SIGINT`, and `Restart=no`.

After stop, `duration_seconds` is the requested window and `elapsed_seconds`
is wall-clock time until stop. Collector log: `<run_dir>/capture-<run_id>.log`.

## How to stop

```bash
# Do not do this during an assigned 72h evidence window.
kill -TERM "${COLLECTOR_PID}"
```

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.
`elapsed_seconds` must be read separately from requested `duration_seconds`.

## How to continue later (there is no resume)

```text
resume_policy: never resume or overwrite an existing DATA-1E run directory
```

To retain more data after a stop, start a **new** `run_id`.

## Explicitly not proven

- 24/7 collection or an always-on host service
- lossless WAL / crash-safe in-memory segment recovery
- Cloud Agent multi-day capture
- starting this capture in this PR
- D22-B venue-authoritative reconciliation
- funding settlement, signing, TESTNET, SHADOW, LIVE
- profitability or strategy promotion
- OKX
- live Pro trades/ticker retain coverage (subscribe-set is prepare-only until CoS starts)
