# DATA-1E VPS runbook — retained Bitvavo MD Pro BTC-EUR capture

Status: operator runbook for the reconstructable CLI
`python -m hyperliquid_bot.bitvavo_mdpro_research`.
This is **not** a 24/7 service, not D22-B, and not LIVE trading.

Authenticated View-only Bitvavo Market Data Pro book for `BTC-EUR` only.
No trade keys, no signing, no OKX.

For the Windows 11 + WSL2 Ubuntu operator PC (TerraPC), including tmux
`bv-capture` start/status/stop, see
[data1e-wsl-pc-retained-capture.md](data1e-wsl-pc-retained-capture.md).
Cloud Agents are unsuitable for a multi-day retain and must not SSH to or stop
`hl-capture`, `bn-capture`, or `bv-capture`.

**Do not start a multi-day DATA-1E retain now.** Wait for CoS to assign this
window.

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

## Fail-closed start

```bash
# From a supported Ubuntu LTS VPS checkout of a tested commit / image.
# Do not start until CoS assigns this window.
unset TRADING_MODE
unset D41_EXECUTION_MODE

export PYTHONPATH=src
export ARTIFACT_ROOT=/var/lib/hyperliquid-bot/reconstructable
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
export DURATION_SECONDS=14400

test -n "${BITVAVO_MDPRO_API_KEY:-}"
test -n "${BITVAVO_MDPRO_API_SECRET:-}"
test ! -e "${ARTIFACT_ROOT}/data-1e/bitvavo/BTC-EUR/${RUN_ID}"

PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.bitvavo_mdpro_research \
  --artifact-root "${ARTIFACT_ROOT}" \
  --run-id "${RUN_ID}" \
  --duration-seconds "${DURATION_SECONDS}"
```

The reconstructable command is create-only. An existing run directory is refused.

## How to stop

```bash
kill -TERM "${COLLECTOR_PID}"
```

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.

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
- TrueNAS / ClickHouse reuse
- OKX
