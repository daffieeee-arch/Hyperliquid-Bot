# DATA-1A VPS runbook — retained Hyperliquid public BTC-PERP capture

Status: operator runbook for the existing reconstructable CLI from PR #32.
This is **not** a 24/7 service, not D22-B, and not LIVE trading.

Public Hyperliquid MAINNET market data only. No keys, no signing, no Binance,
and no extra venues.

For the Windows 11 + WSL2 Ubuntu operator PC (TerraPC), including tmux
`hl-capture` start/status/stop and Quant handoff, see
[data1a-wsl-pc-retained-capture.md](data1a-wsl-pc-retained-capture.md). Cloud
Agents are unsuitable for a multi-day retain and must not stop a capture that
is already running. The active host profile is
[linux-vps-reference-profile.md](linux-vps-reference-profile.md).

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

The Operator Cockpit first PAPER screen reads:

```text
<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1a_run_paths(artifact_root, run_id)`. Set `ARTIFACT_ROOT` plus
`DATA1A_RUN_ID` (or `COCKPIT_DATA1A_RUN_ID`) or open `/?data1a_run_id=<run_id>`.
Missing `capture-health.json` is expected while the writer is still running;
the cockpit shows **RUNNING (health JSON pending until stop)** when the claim
is present and last `raw/part-*.parquet` mtime is within
`COCKPIT_CAPTURE_FRESH_MAX_S=180`. The first PAPER screen polls
`/api/data1a-capture` every **5 seconds** (`Cache-Control: no-store`) so those
filesystem numbers update without a full page reload. Missing root or `run_id`
fails closed. Part count and last part mtime come from a cheap
`raw/part-*.parquet` listing; payloads are not read. For TerraPC WSL paths see
[data1a-wsl-pc-retained-capture.md](data1a-wsl-pc-retained-capture.md).

`run_id` must be 1–64 lowercase ASCII letters, digits, dot, dash, or underscore.

## Fail-closed start

```bash
# From the Netcup Ubuntu 24.04 LTS VPS.
# PAPER is irrelevant to this public collector; it never signs or submits orders.
unset TRADING_MODE
unset D41_EXECUTION_MODE
# Refuse to start if any protected Hyperliquid key name is present in the shell.
# Do not print or log secret values.

export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export PYTHONPATH=src
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
# Example retained window: 4 hours. Raise up to 604800 on the durable VPS store.
export DURATION_SECONDS=14400

test ! -e "${ARTIFACT_ROOT}/data-1a/hyperliquid/BTC-PERP/${RUN_ID}"

cd "$REPO_ROOT"
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

## Protect a 72h evidence window

Do **not** stop a mid-window 72h evidence run. Cloud Agents must not stop
captures. If systemd supervises the collector, use `Type=simple`,
`KillSignal=SIGINT`, and `Restart=no`. Do not `systemctl stop` or
`Restart=always` during an assigned 72h tape.

After stop, `duration_seconds` is the requested window and `elapsed_seconds`
is wall-clock time until stop. `OPERATOR_STOP` with a shorter elapsed time is
an operator interrupt, not a completed tape. Collector log:
`<run_dir>/capture-<run_id>.log`.

Heartbeat: application `{"method":"ping"}` every 45s plus a 60s receive
timeout. A ~3h disconnect is not a missed 60s idle ping.

## How to stop

```bash
# Ask the collector to finish the current in-memory segment and write health.
# Do not do this during an assigned 72h evidence window.
kill -TERM "${COLLECTOR_PID}"
```

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.
`elapsed_seconds` must be read separately from requested `duration_seconds`.

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
