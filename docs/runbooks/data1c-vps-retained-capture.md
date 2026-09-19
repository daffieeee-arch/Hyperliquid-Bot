# DATA-1C VPS runbook — retained OKX public BTC-USDT-SWAP capture

Status: **prepare-only** operator runbook for the reconstructable CLI
`python -m hyperliquid_bot.okx_public_research`.
This is **not** a 24/7 service, not D22-B, and not LIVE trading.

Public OKX EEA market data only (`BTC-USDT-SWAP` on public + business
sockets). No VIP channels, no API keys, no signing, and no extra venues.

For the Windows 11 + WSL2 Ubuntu operator PC (TerraPC), see
[data1c-wsl-pc-retained-capture.md](data1c-wsl-pc-retained-capture.md).
Cloud Agents must not stop Phase A sessions (`hl-capture`, `bn-capture`,
`bv-capture`, `bv-std-capture`, `kr-capture`) or start OKX without CoS
assignment. The active host profile is
[linux-vps-reference-profile.md](linux-vps-reference-profile.md).

**Do not start a multi-day DATA-1C retain without CoS assignment.** Phase A
72h (HL/BN/BV/KR) stays untouched. OKX is out of the current joint campaign.

## Duration contract

`hyperliquid_bot.okx_public_research` accepts an explicit duration of
**1 through 604800 seconds** (7 days).

| Window | Meaning |
| --- | --- |
| 1–600s | Historical smoke window. Still valid. |
| 600.1–604800s | Retained research capture (`retained: true`). |
| >604800s | Fail closed. |
| No duration / always-on | Not implemented. |

SIGINT or SIGTERM may stop earlier. A hard crash can lose the in-memory Parquet
segment; already published `raw/part-*.parquet` files remain readable.

## Path contract

```text
<artifact-root>/data-1c/okx/BTC-USDT-SWAP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1c_run_paths(artifact_root, run_id)`. tmux session:
`okx-capture` (never `hl-capture` / `bn-capture` / `bv-capture` /
`bv-std-capture` / `kr-capture`).

`run_id` must be 1–64 lowercase ASCII letters, digits, dot, dash, or underscore.

## Fail-closed start (prepare-only)

```bash
# From the Netcup Ubuntu 24.04 LTS VPS.
# Do not start until CoS assigns this window after Phase A.
unset TRADING_MODE
unset D41_EXECUTION_MODE

export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export PYTHONPATH=src
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
export DURATION_SECONDS=14400
export TMUX_SESSION=okx-capture

# Prefer the operator helper (check-only first):
DURATION_SECONDS=60 bash "$REPO_ROOT/scripts/data1c_start.sh" --check-only

# After CoS assign, start a create-only retain:
# DURATION_SECONDS=259200 bash "$REPO_ROOT/scripts/data1c_start.sh"
```

Use **either** `--artifact-root` + `--run-id` **or** the disposable
`--output-dir` + `--database` smoke pair. Mixing them fails closed.

The reconstructable command is create-only. An existing run directory is refused.

## Status / stop

```bash
bash "$REPO_ROOT/scripts/data1c_status.sh"
# After CoS assign and only outside a protected evidence window:
# bash "$REPO_ROOT/scripts/data1c_stop.sh"
```

## Explicitly not proven / out of scope

- Starting OKX during the active Phase A 72h joint retain
- VIP / 10-ms / SBE / authenticated OKX feeds
- RPI books or liquidation-orders coverage
- 24/7 collection or an always-on host service
- LIVE / signing / withdrawals
