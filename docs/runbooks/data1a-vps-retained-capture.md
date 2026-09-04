# DATA-1A VPS runbook — retained Hyperliquid public BTC-PERP capture

Status: operator runbook for the existing reconstructable CLI from PR #32.
This is **not** a 24/7 service, not D22-B, and not LIVE trading.

Public Hyperliquid MAINNET market data only. No keys, no signing, no Binance,
and no extra venues.

## Duration contract

`hyperliquid_bot.hyperliquid_raw_research` accepts an explicit duration of
**1 through 604800 seconds** (7 days).

| Window | Meaning |
| --- | --- |
| 1–600s | Historical smoke window. Still valid. |
| 600.1–604800s | Retained research capture (`retained: true`). |
| >604800s | Fail closed. |
| No duration / always-on | Not implemented. |

SIGINT or SIGTERM may stop earlier. A hard crash can lose the in-memory Parquet
segment; already published `raw/part-*.parquet` files remain readable.

This contract is independent of the COURSE-1 live-public PAPER soak, which stays
bounded to **1–600 seconds**.

## Path contract

Cockpit should later read:

```text
<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1a_run_paths(artifact_root, run_id)`.

`run_id` must be 1–64 lowercase ASCII letters, digits, dot, dash, or underscore.

## Fail-closed start

```bash
# From a supported Ubuntu LTS VPS checkout of a tested commit / image.
# PAPER is irrelevant to this public collector; it never signs or submits orders.
unset TRADING_MODE
unset D41_EXECUTION_MODE
# Refuse to start if any protected Hyperliquid key name is present in the shell.
# Do not print or log secret values.

export PYTHONPATH=src
export ARTIFACT_ROOT=/var/lib/hyperliquid-bot/reconstructable
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
# Example retained window: 4 hours. Raise up to 604800 on the durable VPS store.
export DURATION_SECONDS=14400

test ! -e "${ARTIFACT_ROOT}/data-1a/hyperliquid/BTC-PERP/${RUN_ID}"

PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.hyperliquid_raw_research \
  --artifact-root "${ARTIFACT_ROOT}" \
  --run-id "${RUN_ID}" \
  --duration-seconds "${DURATION_SECONDS}"
```

Use **either** `--artifact-root` + `--run-id` **or** the disposable
`--output-dir` + `--database` smoke pair. Mixing them fails closed.

The reconstructable command is create-only. An existing run directory is refused.

## How retain works

- Set `--duration-seconds` above 600 and at most 604800.
- Keep `${ARTIFACT_ROOT}` on the durable VPS volume, not inside git.
- Do not commit `raw/part-*.parquet` or `research.duckdb`.
- The process still exits at the requested duration or on SIGINT/SIGTERM.

## How to stop

```bash
# Ask the collector to finish the current in-memory segment and write health.
kill -TERM "${COLLECTOR_PID}"
```

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.

## How to continue later (there is no resume)

The claim records:

```text
resume_policy: never resume or overwrite an existing DATA-1A run directory
```

To retain more data after a stop, start a **new** `run_id`. Do not reuse the
previous directory, do not append to published Parquet, and do not copy a live
DuckDB catalog into a new run.

## Git-safe sample

After at least one `raw/part-*.parquet` exists:

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.data1a_git_safe_sample \
  --artifact-root "${ARTIFACT_ROOT}" \
  --run-id "${RUN_ID}" \
  --output-dir "/tmp/data1a-git-safe-${RUN_ID}" \
  --max-rows 8
```

The sample copies claim/health plus metadata-only rows (`payload_sha256` and
byte length). It never copies `payload_bytes`.

## Cloud-agent evidence

| Field | Value |
| --- | --- |
| `artifact-root` | `/workspace/var/reconstructable` |
| `run_id` | `20260904t001700z-live-retained` |
| requested duration | `14400` seconds (4 hours) |
| start UTC | `2026-09-04T00:17:03Z` |
| end UTC | `2026-09-04T04:17:58Z` |
| health | `OPERATOR_STOP` |
| published parts | `41` (`1822908` bytes) |
| events | `19404` (trades 2975, bbo 13936, l2Book 375, activeAssetCtx 1967) |
| gaps / reconnects | `1` / `1` |
| websocket | `wss://api.hyperliquid.xyz/ws` |
| product | Hyperliquid public BTC-PERP (`trades`, `bbo`, `l2Book`, `activeAssetCtx`) |

The cloud-agent VM froze after ~49 minutes of process time. Last substantial
Parquet part was `01:06Z`; marker-only parts continued through `01:20Z`. On
wake the collector was stopped with SIGTERM instead of waiting out a stale
socket. That is an operator stop, not a 24/7 crash-recovery claim. Continue
later only with a **new** `run_id`.

Raw parts stay under `var/reconstructable/` (gitignored). Committed evidence
lives in `tests/fixtures/data_1a_live_evidence/`.

## Explicitly not proven

- 24/7 collection or an always-on host service
- lossless WAL / crash-safe in-memory segment recovery
- D22-B venue-authoritative reconciliation
- funding settlement, signing, TESTNET, SHADOW, LIVE
- profitability or strategy promotion
- TrueNAS / ClickHouse reuse
