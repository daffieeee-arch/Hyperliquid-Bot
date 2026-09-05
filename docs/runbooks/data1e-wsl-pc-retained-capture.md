# DATA-1E operator PC/WSL runbook — retained Bitvavo MD Pro BTC-EUR capture

Status: operator runbook for the reconstructable CLI
`python -m hyperliquid_bot.bitvavo_mdpro_research`. Windows 11 + WSL2 Ubuntu
workstation (TerraPC). This is **not** a 24/7 service, not D22-B, and not LIVE
trading.

Authenticated **View-only** Bitvavo Market Data Pro on the same Pro socket for
`BTC-EUR`: `book` (depth 1000) plus `trades`. Optional `ticker` is flagged
(`--include-ticker`) and is off by default. This is never DATA-1D Standard.
No trade, withdrawal, transfer, or signing keys. No OKX. The VPS counterpart
is [data1e-vps-retained-capture.md](data1e-vps-retained-capture.md).

**Do not start a multi-day DATA-1E retain now.** This PR is prepare-only.
Wait until CoS assigns this window. This document does **not** attach to,
resume, or stop tmux `hl-capture` or `bn-capture`.

## Why not a Cloud Agent

Cloud Agents are **unsuitable** for a multi-day DATA-1E retain:

- The VM is ephemeral and can freeze or be reclaimed mid-run.
- The committed DATA-1A live-evidence sample already shows a cloud-agent freeze
  after ~49 minutes of process time.
- This agent must not SSH to TerraPC or send `C-c` to `hl-capture`,
  `bn-capture`, or `bv-capture`.

Run the collector on the operator PC under WSL2, with the Windows host staying
awake, **only after** CoS assign. Keep artifacts on the WSL Linux filesystem,
not `/mnt/c`.

## Known-good TerraPC paths

| Item | Path / value |
| --- | --- |
| Repo checkout | `~/code/Hyperliquid-Bot-main` on `main` |
| Artifact root | `~/hyperliquid-artifacts/reconstructable` |
| Product layout | `data-1e/bitvavo/BTC-EUR/<run_id>/` |
| tmux session | `bv-capture` (**never** `hl-capture` or `bn-capture`) |
| Example retained duration | `259200` seconds (72 hours) |

`DEVELOPMENT.md` uses `~/code/Hyperliquid-Bot` as the generic example. On this
workstation the checkout name is `Hyperliquid-Bot-main`. Same repository.

```text
~/hyperliquid-artifacts/reconstructable/data-1e/bitvavo/BTC-EUR/<run_id>/
  capture-claim.json
  capture-health.json
  capture-<run_id>.log
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1e_run_paths(artifact_root, run_id)`. `run_id` must be 1–64
lowercase ASCII letters, digits, dot, dash, or underscore.

Keep `${ARTIFACT_ROOT}` **outside git**. Do not commit `raw/part-*.parquet` or
`research.duckdb`.

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

A required-stream death or integrity error still fails closed. Retained runs
raise the reconnect cap above the Phase-1 smoke (`max_reconnects=1`) so a
single disconnect does not end a 72h window; gaps remain explicit markers.
This is not lossless recovery.

The 2026-08-31 authenticated smoke remains evidence only for bounded
reachability.

## Channels on the same Pro socket

Default subscribe set (one `wss://ws-mdpro.bitvavo.com/v2/` connection):

| Channel | Required | Notes |
| --- | --- | --- |
| `book` | yes | Non-conflated Pro L2 plus in-band `getBook` depth **1000**. |
| `trades` | yes | Same Pro socket. Stored as `mdpro_trades`, never Standard `trades`. |
| `ticker` | no | Only when `--include-ticker` is set. Stored as `mdpro_ticker`. |

Claim `feed` is `bitvavo-mdpro-btc-eur-book-trades`, or
`bitvavo-mdpro-btc-eur-book-trades-ticker` when ticker is flagged.
`standard_fallback` is always false. There is no Standard URL or channel
fallback.

To include ticker on a later CoS-assigned retain, add `--include-ticker` to the
Python command. The create-only helpers stay default book+trades.

## Auth (read-only MD Pro via env)

Required environment names (values never printed, logged, or committed):

| Name | Purpose |
| --- | --- |
| `BITVAVO_MDPRO_API_KEY` | View-only / Read-only Market Data Pro key |
| `BITVAVO_MDPRO_API_SECRET` | Matching secret |

The key must have only UI `View access` (called `Read-only` in the Pro
introduction). Trade, withdrawal, transfer, administrative, and subaccount
permissions stay off.

Refuse to start if any of these **protected** names are set (values not
printed): `HYPERLIQUID_PK`, `HYPERLIQUID_TESTNET_PK`, `HYPERLIQUID_VAULT`,
`HYPERLIQUID_TESTNET_VAULT`, `HYPERLIQUID_ACCOUNT_ADDRESS`, `BINANCE_API_KEY`,
`BINANCE_API_SECRET`, `BINANCE_SECRET`, `BINANCE_API_KEY_TESTNET`,
`BINANCE_TESTNET_API_SECRET`, `BITVAVO_API_KEY`, `BITVAVO_API_SECRET`,
`BITVAVO_ACCESS_KEY`, `BITVAVO_SECRET`, `BITVAVO_SIGNING_KEY`, `KRAKEN_API_KEY`,
`KRAKEN_API_SECRET`, `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE`.

Generic `BITVAVO_API_KEY` / `BITVAVO_API_SECRET` names are treated as
trade-capable and fail closed. Use only the `BITVAVO_MDPRO_*` names.

## Host posture (Windows 11 + WSL2)

The capture lives in the WSL2 VM. If Windows sleeps, hibernates, updates,
reboots, or runs `wsl --shutdown`, the collector dies. That is not crash-safe
recovery.

Before a multi-day retain (after CoS assign):

1. Plug in AC power.
2. Disable AC sleep and hibernate. From **Windows PowerShell** (not WSL):

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```

3. Set lid-close on AC to "Do nothing" if the lid may close.
4. Pause Windows Update reboots for the capture window.
5. Do not run `wsl --shutdown` from PowerShell while `bv-capture`,
   `bn-capture`, or `hl-capture` is alive.
6. Detached tmux survives closing Windows Terminal; it does **not** survive a
   WSL VM stop.

The workstation still must not hold the Hyperliquid master-wallet key or any
LIVE agent key.

## Fail-closed start

Copy-paste inside **WSL2 Ubuntu** only after CoS assign. PAPER is irrelevant
to this collector; it never signs or submits orders.

```bash
cd ~/code/Hyperliquid-Bot-main
git checkout main
git pull --ff-only

(
  set -euo pipefail
  unset TRADING_MODE
  unset D41_EXECUTION_MODE
  export PYTHONPATH=src
  export ARTIFACT_ROOT="${HOME}/hyperliquid-artifacts/reconstructable"
  export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
  export DURATION_SECONDS=259200
  export TMUX_SESSION=bv-capture

  test "${TMUX_SESSION}" != "hl-capture"
  test "${TMUX_SESSION}" != "bn-capture"
  test -n "${BITVAVO_MDPRO_API_KEY:-}"
  test -n "${BITVAVO_MDPRO_API_SECRET:-}"
  mkdir -p "${ARTIFACT_ROOT}"
  test ! -e "${ARTIFACT_ROOT}/data-1e/bitvavo/BTC-EUR/${RUN_ID}"
  test -z "$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -x "${TMUX_SESSION}" || true)"

  tmux new-session -d -s "${TMUX_SESSION}" \
    "cd ${HOME}/code/Hyperliquid-Bot-main && \
     unset TRADING_MODE D41_EXECUTION_MODE && \
     export PYTHONPATH=src && \
     exec uv run --frozen python -m hyperliquid_bot.bitvavo_mdpro_research \
       --artifact-root ${ARTIFACT_ROOT} \
       --run-id ${RUN_ID} \
       --duration-seconds ${DURATION_SECONDS}"

  echo "started run_id=${RUN_ID} session=${TMUX_SESSION} duration=${DURATION_SECONDS}"
)
```

Use **either** `--artifact-root` + `--run-id` **or** the disposable
`--output-dir` + `--database` smoke pair. Mixing them fails closed.

The reconstructable command is create-only. An existing run directory is refused.

Optional helper (same fail-closed checks; create-only; refuses `hl-capture` and
`bn-capture`):

```bash
cd ~/code/Hyperliquid-Bot-main
DURATION_SECONDS=259200 ./scripts/data1e_start.sh
```

`./scripts/data1e_start.sh --check-only` validates env/paths without creating a
tmux session or a run directory. It does **not** start capture.

## Status

```bash
cd ~/code/Hyperliquid-Bot-main
export ARTIFACT_ROOT="${HOME}/hyperliquid-artifacts/reconstructable"
export RUN_ID=20260904t000000z-live-retained   # replace with the assigned run
export TMUX_SESSION=bv-capture

tmux has-session -t "${TMUX_SESSION}" && echo "tmux_alive=yes" || echo "tmux_alive=no"
tmux has-session -t hl-capture && echo "hl_capture_tmux_alive=yes" || echo "hl_capture_tmux_alive=no"
tmux has-session -t bn-capture && echo "bn_capture_tmux_alive=yes" || echo "bn_capture_tmux_alive=no"

RUN_DIR="${ARTIFACT_ROOT}/data-1e/bitvavo/BTC-EUR/${RUN_ID}"
ls -ld "${RUN_DIR}"
test -f "${RUN_DIR}/capture-claim.json" && echo "claim_present=yes"
test -f "${RUN_DIR}/capture-health.json" && echo "health_present=yes" || echo "health_present=no"
printf "parquet_parts=%s\n" "$(find "${RUN_DIR}/raw" -maxdepth 1 -type f -name 'part-*.parquet' | wc -l)"
```

Optional helper:

```bash
cd ~/code/Hyperliquid-Bot-main
RUN_ID=20260904t000000z-live-retained ./scripts/data1e_status.sh
```

## Protect a 72h evidence window

When the assigned goal is a 72-hour reconstructable tape (`DURATION_SECONDS=259200`):

- Do **not** send `C-c`, SIGINT, SIGTERM, `tmux kill-session`, or `wsl --shutdown`.
- Cloud Agents must not SSH, attach, or stop tmux `hl-capture` / `bn-capture` /
  `bv-capture` / `kr-capture`.
- After stop, `duration_seconds` is the requested window and `elapsed_seconds`
  is wall-clock time until stop. `OPERATOR_STOP` with
  `elapsed_seconds` < `duration_seconds` is an operator interrupt, not a
  completed 72h tape.
- Transport `gaps` / `reconnects` also appear under `transport_profiles`.

Collector INFO log: `<run_dir>/capture-<run_id>.log`. Optional tmux copy:
`<artifact-root>/logs/capture-<run_id>.log`.

## How to stop

Ask the collector to finish the current in-memory segment and write health
**only when the assigned window is complete or CoS orders a stop**. Do not C-c
a live 72h evidence run.

```bash
tmux send-keys -t bv-capture C-c
```

Optional helper:

```bash
cd ~/code/Hyperliquid-Bot-main
./scripts/data1e_stop.sh
```

Do **not** `tmux kill-session` as the first choice; a hard kill can drop the
in-memory segment. Wait until `capture-health.json` appears.

Do **not** run `tmux send-keys -t hl-capture C-c` or
`tmux send-keys -t bn-capture C-c` from this runbook.

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.

Confirm:

```bash
tmux has-session -t bv-capture && echo still_alive || echo stopped
python3 -c 'import json,pathlib,os; p=pathlib.Path(os.path.expanduser("~/hyperliquid-artifacts/reconstructable/data-1e/bitvavo/BTC-EUR/'"${RUN_ID}"'/capture-health.json")); print(json.loads(p.read_text())["status"])'
```

## How to continue later (there is no resume)

The claim records:

```text
resume_policy: never resume or overwrite an existing DATA-1E run directory
```

Never resume the same `run_id`. After a stop, start a **new** `run_id`. Do not
reuse the previous directory, do not append to published Parquet, and do not
copy a live DuckDB catalog into a new run.

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
- a complete book beyond requested depth-1000, checksum coverage, or live Pro trades/ticker retain coverage
