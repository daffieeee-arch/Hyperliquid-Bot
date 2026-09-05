# DATA-1B operator PC/WSL runbook — retained Kraken public BTC-EUR capture

Status: operator runbook for the reconstructable CLI
`python -m hyperliquid_bot.kraken_l3_research`. Windows 11 + WSL2 Ubuntu
workstation (TerraPC). This is **not** a 24/7 service, not D22-B, and not LIVE
trading.

Default retained path: public Kraken Spot `BTC/EUR` **L2 book + trades** at
`wss://ws.kraken.com/v2`, subscribed depth **100**. Authenticated L3 is optional
(default depth 100 when enabled) and never a silent fallback. No OKX. The VPS
counterpart is [data1b-vps-retained-capture.md](data1b-vps-retained-capture.md).

**Do not start a multi-day DATA-1B retain now.** This PR is prepare-only.
Wait until CoS assigns this window. This document does **not** attach to,
resume, or stop tmux `hl-capture`, `bn-capture`, or `bv-capture`.

## Why not a Cloud Agent

Cloud Agents are **unsuitable** for a multi-day DATA-1B retain:

- The VM is ephemeral and can freeze or be reclaimed mid-run.
- The committed DATA-1A live-evidence sample already shows a cloud-agent freeze
  after ~49 minutes of process time.
- This agent must not SSH to TerraPC or send `C-c` to `hl-capture`,
  `bn-capture`, `bv-capture`, or `kr-capture`.

Run the collector on the operator PC under WSL2, with the Windows host staying
awake, **only after** CoS assign. Keep artifacts on the WSL Linux filesystem,
not `/mnt/c`.

## Known-good TerraPC paths

| Item | Path / value |
| --- | --- |
| Repo checkout | `~/code/Hyperliquid-Bot-main` on `main` |
| Artifact root | `~/hyperliquid-artifacts/reconstructable` |
| Product layout | `data-1b/kraken/BTC-EUR/<run_id>/` |
| Wire product | `BTC/EUR` |
| tmux session | `kr-capture` (**never** `hl-capture`, `bn-capture`, or `bv-capture`) |
| Example retained duration | `259200` seconds (72 hours) |

`DEVELOPMENT.md` uses `~/code/Hyperliquid-Bot` as the generic example. On this
workstation the checkout name is `Hyperliquid-Bot-main`. Same repository.

```text
~/hyperliquid-artifacts/reconstructable/data-1b/kraken/BTC-EUR/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1b_run_paths(artifact_root, run_id)`. `run_id` must be 1–64
lowercase ASCII letters, digits, dot, dash, or underscore. The path segment is
`BTC-EUR` (filesystem-safe); the wire symbol remains `BTC/EUR`.

Keep `${ARTIFACT_ROOT}` **outside git**. Do not commit `raw/part-*.parquet` or
`research.duckdb`.

## Duration contract

`hyperliquid_bot.kraken_l3_research` accepts an explicit duration of
**1 through 604800 seconds** (7 days).

| Window | Meaning |
| --- | --- |
| 1–600s | Historical DATA-1B smoke window. Still valid. |
| 600.1–604800s | Retained research capture (`retained: true`). |
| >604800s | Fail closed. |
| No duration / always-on | Not implemented. |

SIGINT or SIGTERM may stop earlier. A hard crash can lose the in-memory Parquet
segment; already published `raw/part-*.parquet` files remain readable.

Public reconnects already loop until the duration ends. Authenticated L3, when
enabled, still fails closed with no silent public-only downgrade.

The 2026-08-31 authenticated smoke remains evidence only for bounded
reachability. That smoke used historical depth-10.

## Depth, CRC, disk and bandwidth

CLI defaults: `--l2-depth 100` and `--l3-depth 100`. Existing Kraken sets remain
valid (`10/25/100/500/1000` for L2, `10/100/1000` for L3). Helpers inherit the
Python defaults and do not override them.

CRC32 / checksum still covers **only the best 10 price levels**, asks before
bids, even at subscribed depth 100. Levels 11–100 are retained and locally
`scope_truncate`d when they leave the window; a matching checksum is not proof
that those deeper levels are complete.

Depth-100 snapshots are about 10× a depth-10 snapshot on each connect or
reconnect. Incremental updates are not automatically 10×. Optional L3 at depth
100 is heavier (individual orders × more levels). No 72-hour depth-100 retain
has been measured. Budget more disk and bandwidth than the depth-10 smoke:
watch WSL free space on the Linux filesystem (not `/mnt/c`), and expect
low-single-digit to low-tens of GB plus sustained KB/s–tens-of-KB/s for a
public 72h L2+trades retain, more if L3 is enabled. This is an operator
budget, not a measured rate.

## Auth (public default; optional L3 via env)

Default retained path needs **no keys**.

Optional L3 environment names (values never printed, logged, or committed):

| Name | Purpose |
| --- | --- |
| `KRAKEN_WS_API_KEY` | WebSocket-interface-only API key |
| `KRAKEN_WS_API_SECRET` | Matching base64 secret |

The only allowed permission is `WebSocket interface - On` (`Access WebSockets
API`). Query Funds, order/trade queries, ledger, export, order
create/modify/cancel, deposit, withdrawal, transfer, Earn, and
account-management stay off. Both names must be set together; a single name
fails closed.

Refuse to start if any of these **protected** names are set (values not
printed): `HYPERLIQUID_PK`, `HYPERLIQUID_TESTNET_PK`, `HYPERLIQUID_VAULT`,
`HYPERLIQUID_TESTNET_VAULT`, `HYPERLIQUID_ACCOUNT_ADDRESS`, `BINANCE_API_KEY`,
`BINANCE_API_SECRET`, `BINANCE_SECRET`, `BINANCE_API_KEY_TESTNET`,
`BINANCE_TESTNET_API_SECRET`, `BITVAVO_API_KEY`, `BITVAVO_API_SECRET`,
`BITVAVO_ACCESS_KEY`, `BITVAVO_SECRET`, `BITVAVO_SIGNING_KEY`, `KRAKEN_API_KEY`,
`KRAKEN_API_SECRET`, `KRAKEN_PRIVATE_KEY`, `OKX_API_KEY`, `OKX_SECRET_KEY`,
`OKX_PASSPHRASE`.

Generic `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` names are the wrong key type and
fail closed. Use only `KRAKEN_WS_*` when enabling L3.

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
5. Do not run `wsl --shutdown` from PowerShell while `kr-capture`,
   `bv-capture`, `bn-capture`, or `hl-capture` is alive.
6. Detached tmux survives closing Windows Terminal; it does **not** survive a
   WSL VM stop.

The workstation still must not hold the Hyperliquid master-wallet key or any
LIVE agent key.

## Fail-closed start

Copy-paste inside **WSL2 Ubuntu** only after CoS assign. PAPER is irrelevant
to this collector; it never signs or submits orders. Public default needs no
keys.

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
  export TMUX_SESSION=kr-capture

  test "${TMUX_SESSION}" != "hl-capture"
  test "${TMUX_SESSION}" != "bn-capture"
  test "${TMUX_SESSION}" != "bv-capture"
  mkdir -p "${ARTIFACT_ROOT}"
  test ! -e "${ARTIFACT_ROOT}/data-1b/kraken/BTC-EUR/${RUN_ID}"
  test -z "$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -x "${TMUX_SESSION}" || true)"

  # Python defaults: --l2-depth 100 --l3-depth 100. CRC32 still covers only the best 10 levels.
  tmux new-session -d -s "${TMUX_SESSION}" \
    "cd ${HOME}/code/Hyperliquid-Bot-main && \
     unset TRADING_MODE D41_EXECUTION_MODE && \
     export PYTHONPATH=src && \
     exec uv run --frozen python -m hyperliquid_bot.kraken_l3_research \
       --artifact-root ${ARTIFACT_ROOT} \
       --run-id ${RUN_ID} \
       --duration-seconds ${DURATION_SECONDS}"

  echo "started run_id=${RUN_ID} session=${TMUX_SESSION} duration=${DURATION_SECONDS}"
)
```

Use **either** `--artifact-root` + `--run-id` **or** the disposable
`--output-dir` + `--database` smoke pair. Mixing them fails closed.

The reconstructable command is create-only. An existing run directory is refused.

Optional helper (same fail-closed checks; create-only; refuses `hl-capture`,
`bn-capture`, and `bv-capture`):

```bash
cd ~/code/Hyperliquid-Bot-main
DURATION_SECONDS=259200 ./scripts/data1b_start.sh
```

`./scripts/data1b_start.sh --check-only` validates env/paths without creating a
tmux session or a run directory. It does **not** start capture.

## Status

```bash
cd ~/code/Hyperliquid-Bot-main
export ARTIFACT_ROOT="${HOME}/hyperliquid-artifacts/reconstructable"
export RUN_ID=20260904t000000z-live-retained   # replace with the assigned run
export TMUX_SESSION=kr-capture

tmux has-session -t "${TMUX_SESSION}" && echo "tmux_alive=yes" || echo "tmux_alive=no"
tmux has-session -t hl-capture && echo "hl_capture_tmux_alive=yes" || echo "hl_capture_tmux_alive=no"
tmux has-session -t bn-capture && echo "bn_capture_tmux_alive=yes" || echo "bn_capture_tmux_alive=no"
tmux has-session -t bv-capture && echo "bv_capture_tmux_alive=yes" || echo "bv_capture_tmux_alive=no"

RUN_DIR="${ARTIFACT_ROOT}/data-1b/kraken/BTC-EUR/${RUN_ID}"
ls -ld "${RUN_DIR}"
test -f "${RUN_DIR}/capture-claim.json" && echo "claim_present=yes"
test -f "${RUN_DIR}/capture-health.json" && echo "health_present=yes" || echo "health_present=no"
printf "parquet_parts=%s\n" "$(find "${RUN_DIR}/raw" -maxdepth 1 -type f -name 'part-*.parquet' | wc -l)"
```

Optional helper:

```bash
cd ~/code/Hyperliquid-Bot-main
RUN_ID=20260904t000000z-live-retained ./scripts/data1b_status.sh
```

## How to stop

Ask the collector to finish the current in-memory segment and write health:

```bash
tmux send-keys -t kr-capture C-c
```

Optional helper:

```bash
cd ~/code/Hyperliquid-Bot-main
./scripts/data1b_stop.sh
```

Do **not** `tmux kill-session` as the first choice; a hard kill can drop the
in-memory segment. Wait until `capture-health.json` appears.

Do **not** run `tmux send-keys -t hl-capture C-c`,
`tmux send-keys -t bn-capture C-c`, or `tmux send-keys -t bv-capture C-c`
from this runbook.

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.

Confirm:

```bash
tmux has-session -t kr-capture && echo still_alive || echo stopped
python3 -c 'import json,pathlib,os; p=pathlib.Path(os.path.expanduser("~/hyperliquid-artifacts/reconstructable/data-1b/kraken/BTC-EUR/'"${RUN_ID}"'/capture-health.json")); print(json.loads(p.read_text())["status"])'
```

## How to continue later (there is no resume)

The claim records:

```text
resume_policy: never resume or overwrite an existing DATA-1B run directory
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
- complete L3 order history, live `modify` coverage, or a strategy edge
