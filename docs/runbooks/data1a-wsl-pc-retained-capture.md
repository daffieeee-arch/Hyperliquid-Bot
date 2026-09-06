# DATA-1A operator PC/WSL runbook — retained Hyperliquid public BTC-PERP capture

Status: operator runbook for the existing reconstructable CLI from PR #32, on
the Windows 11 + WSL2 Ubuntu workstation (TerraPC). This is **not** a 24/7
service, not D22-B, and not LIVE trading.

Public Hyperliquid MAINNET market data only. No keys, no signing, no Binance,
and no extra venues. The VPS counterpart is
[data1a-vps-retained-capture.md](data1a-vps-retained-capture.md).

This document is a parallel operator guide. It does **not** attach to, resume,
or stop a capture that is already running. For a later overlapping Binance
DATA-1F retain, see
[data1f-wsl-pc-retained-capture.md](data1f-wsl-pc-retained-capture.md). Do not
start that capture until this DATA-1A window finishes and CoS assigns it.

## Why not a Cloud Agent

Cloud Agents are **unsuitable** for a multi-day DATA-1A retain:

- The VM is ephemeral and can freeze or be reclaimed mid-run.
- The committed live-evidence sample (`20260904t001700z-live-retained`) already
  shows a cloud-agent freeze after ~49 minutes of process time.
- This agent must not SSH to TerraPC or send `C-c` to a live capture.

Run the collector on the operator PC under WSL2, with the Windows host staying
awake. Keep artifacts on the WSL Linux filesystem, not `/mnt/c`.

## Known-good TerraPC paths

Documented operator layout (do not invent a second tree):

| Item | Path / value |
| --- | --- |
| Repo checkout | `~/code/Hyperliquid-Bot-main` on `main` |
| Artifact root | `~/hyperliquid-artifacts/reconstructable` |
| Product layout | `data-1a/hyperliquid/BTC-PERP/<run_id>/` |
| Example running `run_id` | `20260904t134940z-live-retained` |
| tmux session | `hl-capture` |
| Requested duration | `259200` seconds (72 hours) |

`DEVELOPMENT.md` uses `~/code/Hyperliquid-Bot` as the generic example. On this
workstation the checkout name is `Hyperliquid-Bot-main`. Same repository.

Run directory contents (path contract Cockpit should later read):

```text
~/hyperliquid-artifacts/reconstructable/data-1a/hyperliquid/BTC-PERP/<run_id>/
  capture-claim.json
  capture-health.json
  capture-<run_id>.log
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1a_run_paths(artifact_root, run_id)`. `run_id` must be 1–64
lowercase ASCII letters, digits, dot, dash, or underscore.

Keep `${ARTIFACT_ROOT}` **outside git**. Do not commit `raw/part-*.parquet` or
`research.duckdb`.

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

This contract is independent of the COURSE-1 live-public PAPER soak (1–600s).

Quant's hypothesis entrypoint additionally requires a **72-hour receipt-clock
span** before a candidate baseline may run. A 259200s writer window is sized to
that gate; large gaps can still fail Quant as `not_enough_data`.

## Host posture (Windows 11 + WSL2)

The capture lives in the WSL2 VM. If Windows sleeps, hibernates, updates,
reboots, or runs `wsl --shutdown`, the collector dies. That is not crash-safe
recovery.

Before a multi-day retain:

1. Plug in AC power.
2. Disable AC sleep and hibernate. From **Windows PowerShell** (not WSL):

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```

3. Set lid-close on AC to "Do nothing" if the lid may close.
4. Pause Windows Update reboots for the capture window.
5. Do not run `wsl --shutdown` from PowerShell while `hl-capture` is alive.
6. Detached tmux survives closing Windows Terminal; it does **not** survive a
   WSL VM stop.

This feed is tiny (tens of KB/s). Bandwidth is not the limiting factor; host
sleep and WSL lifetime are.

The workstation still must not hold the Hyperliquid master-wallet key or any
LIVE agent key. Public capture does not need credentials.

## Fail-closed start

Copy-paste inside **WSL2 Ubuntu**. PAPER is irrelevant to this public
collector; it never signs or submits orders.

```bash
cd ~/code/Hyperliquid-Bot-main
git checkout main
git pull --ff-only

(
  set -euo pipefail
  unset TRADING_MODE
  unset D41_EXECUTION_MODE
  # Refuse to start if any protected Hyperliquid key name is present.
  # Do not print or log secret values.
  for name in HYPERLIQUID_PK HYPERLIQUID_TESTNET_PK HYPERLIQUID_VAULT \
              HYPERLIQUID_TESTNET_VAULT HYPERLIQUID_ACCOUNT_ADDRESS
  do
    if [ -n "${!name:-}" ]; then
      echo "Refuse to start: protected environment name ${name} is set." >&2
      echo "Value not printed." >&2
      exit 1
    fi
  done

  export PYTHONPATH=src
  export ARTIFACT_ROOT="${HOME}/hyperliquid-artifacts/reconstructable"
  export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
  export DURATION_SECONDS=259200
  export TMUX_SESSION=hl-capture

  mkdir -p "${ARTIFACT_ROOT}"
  test ! -e "${ARTIFACT_ROOT}/data-1a/hyperliquid/BTC-PERP/${RUN_ID}"
  test -z "$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -x "${TMUX_SESSION}" || true)"

  tmux new-session -d -s "${TMUX_SESSION}" \
    "cd ${HOME}/code/Hyperliquid-Bot-main && \
     unset TRADING_MODE D41_EXECUTION_MODE && \
     export PYTHONPATH=src && \
     exec uv run --frozen python -m hyperliquid_bot.hyperliquid_raw_research \
       --artifact-root ${ARTIFACT_ROOT} \
       --run-id ${RUN_ID} \
       --duration-seconds ${DURATION_SECONDS}"

  echo "started run_id=${RUN_ID} session=${TMUX_SESSION} duration=${DURATION_SECONDS}"
)
```

Use **either** `--artifact-root` + `--run-id` **or** the disposable
`--output-dir` + `--database` smoke pair. Mixing them fails closed.

The reconstructable command is create-only. An existing run directory is refused.

Optional helper (same fail-closed checks; create-only):

```bash
cd ~/code/Hyperliquid-Bot-main
DURATION_SECONDS=259200 ./scripts/data1a_start.sh
```

`./scripts/data1a_start.sh --check-only` validates env/paths without creating a
tmux session or a run directory.

## Status

```bash
cd ~/code/Hyperliquid-Bot-main
export ARTIFACT_ROOT="${HOME}/hyperliquid-artifacts/reconstructable"
# Optional; omit RUN_ID to auto-detect the freshest live retain.
export RUN_ID=20260904t134940z-live-retained
export TMUX_SESSION=hl-capture

tmux has-session -t "${TMUX_SESSION}" && echo "tmux_alive=yes" || echo "tmux_alive=no"
tmux list-panes -t "${TMUX_SESSION}" -F '#{pane_pid} #{pane_current_command}' 2>/dev/null || true

RUN_DIR="${ARTIFACT_ROOT}/data-1a/hyperliquid/BTC-PERP/${RUN_ID}"
ls -ld "${RUN_DIR}"
test -f "${RUN_DIR}/capture-claim.json" && echo "claim_present=yes"
test -f "${RUN_DIR}/capture-health.json" && echo "health_present=yes" || echo "health_present=no"
# health is written at stop/complete; missing while running is expected.
printf "parquet_parts=%s\n" "$(find "${RUN_DIR}/raw" -maxdepth 1 -type f -name 'part-*.parquet' | wc -l)"
ls -lt "${RUN_DIR}/raw"/part-*.parquet 2>/dev/null | head
```

Optional helper:

```bash
cd ~/code/Hyperliquid-Bot-main
# Omit RUN_ID to auto-detect the freshest live retain.
RUN_ID=20260904t134940z-live-retained ./scripts/data1a_status.sh
```

When `RUN_ID` is unset, status defaults to the freshest live retain under
the venue path contract (claim present, fresh `raw/part-*.parquet` mtime;
health JSON may be absent mid-run). Stopped `COMPLETED` / `FAILED` /
`OPERATOR_STOP` dirs are not preferred when a live candidate exists. The
chosen `run_id` is printed. The helper also prints tmux liveness,
claim/health presence, published part count, and reconnect/gap hints per
`transport_profile` when those counts are already in health or labeled in
`capture-*.log` (`n/a` if unknown). It never prints payload bytes or secret
values.

## Watch from the PAPER Operator Cockpit (read-only)

Do **not** stop, attach-and-interrupt, or SSH-signal `hl-capture` to "refresh"
the cockpit. The first PAPER screen only reads files.

From the same WSL checkout, with Next.js loading `apps/cockpit/.env.local` or
the exported names below:

```bash
cd ~/code/Hyperliquid-Bot-main
export TRADING_MODE=PAPER
export ARTIFACT_ROOT=/home/dmesdary/hyperliquid-artifacts/reconstructable
export DATA1A_RUN_ID=20260904t134940z-live-retained
pnpm --filter @hyperliquid-bot/cockpit dev
```

Open `http://127.0.0.1:3000`. `COCKPIT_DATA1A_RUN_ID` is an equivalent alias.
With `ARTIFACT_ROOT` already set you can also use
`http://127.0.0.1:3000/?data1a_run_id=20260904t134940z-live-retained`.

While `capture-health.json` is absent (normal until stop), claim present plus
fresh `raw/part-*.parquet` files (`now - last_part_mtime <=
COCKPIT_CAPTURE_FRESH_MAX_S=180`, tunable) are the liveness signal. The panel
labels that **RUNNING (health JSON pending until stop)** and polls
`/api/data1a-capture` every **5 seconds** (`Cache-Control: no-store`) so
duration, part count, bytes on disk, and last mtime move without a full page
reload. A stale last part mtime is **STALE (stale_mtime)**, not RUNNING. Host
clock must be sane. Missing root or `run_id` fails closed; the cockpit does not
invent PnL or part counts. The same first PAPER screen also shows a four-venue
strip (HL / Binance / Bitvavo / Kraken) via `/api/venue-capture-health`; sibling
venues stay MISSING unless `DATA1F_RUN_ID` / `DATA1E_RUN_ID` / `DATA1B_RUN_ID`
are set on the same `ARTIFACT_ROOT`. Do not stop `hl-capture` to refresh the
cockpit.

Copy `apps/cockpit/.env.example` to `apps/cockpit/.env.local` (gitignored) for
the same pair. The repository-root `.env.example` documents the names but is
not loaded by `next dev`.

## Protect a 72h evidence window

When the assigned goal is a 72-hour reconstructable tape (`DURATION_SECONDS=259200`):

- Do **not** send `C-c`, SIGINT, SIGTERM, `tmux kill-session`, or `wsl --shutdown`.
- Cloud Agents must not SSH, attach, or stop tmux `hl-capture` / `bn-capture` /
  `bv-capture` / `kr-capture`.
- Detached tmux survives closing the terminal; it does not survive a WSL VM stop
  or host sleep.
- `capture-health.json` is written at stop. Missing health while tmux is alive
  is expected.
- After stop, read health as:
  - `duration_seconds` = requested window (example `259200`)
  - `elapsed_seconds` = wall-clock time the process actually ran
  - `status=OPERATOR_STOP` with `elapsed_seconds` < `duration_seconds` is an
    operator interrupt, **not** a completed 72h tape
  - `gaps` / `reconnects` are transport-only and also appear under
    `transport_profiles`
  - `sequence_gap` and other integrity events fail the run and are **not**
    counted in `gaps`

Collector INFO log (session/disconnect/reconnect, close code, exception class;
no payloads or secrets):

```text
<run_dir>/capture-<run_id>.log
```

Optional tmux stdout/stderr copy:

```text
~/hyperliquid-artifacts/reconstructable/logs/capture-<run_id>.log
```

Heartbeat: the writer sends Hyperliquid `{"method":"ping"}` every 45s (below the
official 60s server-outbound-idle timeout) and applies a 60s receive timeout.
Protocol WebSocket pings stay off. A ~3h disconnect cadence is not a missed
60s idle ping; persist `close_code` / `exception_class` and reconnect.

## How to stop

Ask the collector to finish the current in-memory segment and write health
**only when the assigned window is complete or CoS orders a stop**. Do not C-c
a live 72h evidence run.

```bash
tmux send-keys -t hl-capture C-c
```

Optional helper:

```bash
cd ~/code/Hyperliquid-Bot-main
./scripts/data1a_stop.sh
```

Do **not** `tmux kill-session` as the first choice; a hard kill can drop the
in-memory segment. Wait until `capture-health.json` appears.

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.

Confirm:

```bash
tmux has-session -t hl-capture && echo still_alive || echo stopped
python3 -c 'import json,pathlib,os; p=pathlib.Path(os.path.expanduser("~/hyperliquid-artifacts/reconstructable/data-1a/hyperliquid/BTC-PERP/'"${RUN_ID}"'/capture-health.json")); print(json.loads(p.read_text())["status"])'
```

## How to continue later (there is no resume)

The claim records:

```text
resume_policy: never resume or overwrite an existing DATA-1A run directory
```

Never resume the same `run_id`. After a stop, start a **new** `run_id`. Do not
reuse the previous directory, do not append to published Parquet, and do not
copy a live DuckDB catalog into a new run.

## Quant handoff (after ≥72h receipt-clock span)

When the run has finished (`capture-health.json` present) and Quant wants the
PAPER hypothesis entrypoint:

```bash
cd ~/code/Hyperliquid-Bot-main
export PYTHONPATH=src
export ARTIFACT_ROOT="${HOME}/hyperliquid-artifacts/reconstructable"
export RUN_ID=20260904t134940z-live-retained   # completed run only

PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.hypothesis_research \
  --artifact-root "${ARTIFACT_ROOT}" \
  --run-id "${RUN_ID}" \
  --baseline momentum
```

This is PAPER-only research. It does not capture data, does not thaw v3
coverage, and never assigns `edge`. Thresholds (72h span, trade/BBO/mid counts,
5% incomplete-hour cap) live in `docs/DATA.md`. A 1–600s smoke is not
hypothesis-usable.

Do not start Quant against a still-running directory as a promotion step. The
DuckDB catalog is rebuilt at collector stop; mid-run `research.duckdb` is not a
handoff artifact.

## Git-safe sample

After at least one `raw/part-*.parquet` exists:

```bash
cd ~/code/Hyperliquid-Bot-main
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.data1a_git_safe_sample \
  --artifact-root "${HOME}/hyperliquid-artifacts/reconstructable" \
  --run-id "${RUN_ID}" \
  --output-dir "/tmp/data1a-git-safe-${RUN_ID}" \
  --max-rows 8
```

The sample copies claim/health plus metadata-only rows (`payload_sha256` and
byte length). It never copies `payload_bytes`.

## Example running retain (observational)

Do not SSH, attach in a way that stops the process, or send `C-c` to this run
from a Cloud Agent.

| Field | Value |
| --- | --- |
| Host | TerraPC Windows 11 + WSL2 Ubuntu |
| `artifact-root` | `~/hyperliquid-artifacts/reconstructable` |
| `run_id` | `20260904t134940z-live-retained` |
| tmux | `hl-capture` |
| requested duration | `259200` seconds |
| product | Hyperliquid public BTC-PERP (`trades`, `bbo`, `l2Book`, `activeAssetCtx`) |
| websocket | `wss://api.hyperliquid.xyz/ws` |
| credentialless | yes |
| LIVE / keys | no |

## Explicitly not proven

- 24/7 collection or an always-on host service
- lossless WAL / crash-safe in-memory segment recovery
- Cloud Agent multi-day capture
- D22-B venue-authoritative reconciliation
- funding settlement, signing, TESTNET, SHADOW, LIVE
- profitability or strategy promotion
- TrueNAS / ClickHouse reuse
- Bitvavo DATA-1E / Kraken DATA-1B multi-day retain (operator docs exist;
  `bv-capture` / `kr-capture`; do not start until CoS assigns)
- Binance DATA-1F multi-day retain (operator docs exist; do not start while
  this DATA-1A `hl-capture` window is the assigned TerraPC capture)
