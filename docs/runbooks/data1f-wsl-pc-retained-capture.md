# DATA-1F operator PC/WSL runbook — retained Binance public BTCUSDT capture

Status: operator runbook for the reconstructable CLI
`python -m hyperliquid_bot.binance_public_research`. Windows 11 + WSL2 Ubuntu
workstation (TerraPC). This is **not** a 24/7 service, not D22-B, and not LIVE
trading. It is a **secondary fallback**; the Netcup Ubuntu 24.04 LTS VPS and
its VPS runbook are primary.

Public Binance MAINNET market data only (Spot BTCUSDT + USDⓈ-M BTCUSDT). No keys,
no signing, no Bitvavo, no Kraken, and no extra venues. The VPS
counterpart is [data1f-vps-retained-capture.md](data1f-vps-retained-capture.md).

**Do not start a multi-day DATA-1F retain now.** Phase A is a joint four-lane
start after the ≤60 min smoke is STOPPED and Chupa gives explicit OK (CoS
assigns the 72h window together, not after DATA-1A finishes). See
[phase-a-72h-joint-retained-capture.md](phase-a-72h-joint-retained-capture.md).
This document does **not** attach to, resume, or stop tmux `hl-capture`.

## Why not a Cloud Agent

Cloud Agents are **unsuitable** for a multi-day DATA-1F retain:

- The VM is ephemeral and can freeze or be reclaimed mid-run.
- The committed DATA-1A live-evidence sample (`20260904t001700z-live-retained`)
  already shows a cloud-agent freeze after ~49 minutes of process time.
- This agent must not SSH to TerraPC or send `C-c` to `hl-capture` or
  `bn-capture`.

Run the collector on the operator PC under WSL2, with the Windows host staying
awake, **only after** CoS assign. Keep artifacts on the WSL Linux filesystem,
not `/mnt/c`.

## Wait for DATA-1A + CoS assign

The live Hyperliquid retain (do not touch):

| Field | Value |
| --- | --- |
| Host | TerraPC Windows 11 + WSL2 Ubuntu |
| tmux | `hl-capture` |
| `run_id` | `20260904t134940z-live-retained` |
| requested duration | `259200` seconds |
| product | Hyperliquid public BTC-PERP |

Do **not** start Binance capture while that session is the assigned DATA-1A
window. After it completes, CoS may assign a DATA-1F overlap window (possibly
against a later Hyperliquid series). This runbook is create-only preparation.

Quant `--baseline basis` needs `--binance-parquet-dir` pointing at completed
DATA-1F `raw/` Parquet. The basis slot still does not fit a model or claim
edge.

## Known-good TerraPC paths

Documented operator layout (do not invent a second tree):

| Item | Path / value |
| --- | --- |
| Repo checkout | `~/code/Hyperliquid-Bot-main` on `main` |
| Artifact root | `~/hyperliquid-artifacts/reconstructable` |
| Product layout | `data-1f/binance/BTCUSDT/<run_id>/` |
| tmux session | `bn-capture` (**never** `hl-capture`) |
| Example retained duration | `259200` seconds (72 hours) |

`DEVELOPMENT.md` uses `~/code/Hyperliquid-Bot` as the generic example. On this
workstation the checkout name is `Hyperliquid-Bot-main`. Same repository.

The helpers now default to the primary VPS layout. For this secondary WSL
runbook, export the legacy WSL override before any `scripts/data1f_*.sh`
command:

```bash
export REPO_ROOT="$HOME/code/Hyperliquid-Bot-main"
export ARTIFACT_ROOT="$HOME/hyperliquid-artifacts/reconstructable"
```

Run directory contents (path contract; Hypothesis `--binance-parquet-dir` is
the `raw/` directory):

```text
~/hyperliquid-artifacts/reconstructable/data-1f/binance/BTCUSDT/<run_id>/
  capture-claim.json
  capture-health.json
  capture-<run_id>.log
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1f_run_paths(artifact_root, run_id)`. `run_id` must be 1–64
lowercase ASCII letters, digits, dot, dash, or underscore.

Keep `${ARTIFACT_ROOT}` **outside git**. Do not commit `raw/part-*.parquet` or
`research.duckdb`.

## Duration contract

`hyperliquid_bot.binance_public_research` accepts an explicit duration of
**1 through 604800 seconds** (7 days).

| Window | Meaning |
| --- | --- |
| 1–180s | Historical DATA-1F smoke window. Still valid. |
| 180.1–604800s | Retained research capture (`retained: true`). |
| >604800s | Fail closed. |
| No duration / always-on | Not implemented. |

SIGINT or SIGTERM may stop earlier. A hard crash can lose the in-memory Parquet
segment; already published `raw/part-*.parquet` files remain readable.

A required-stream death or integrity error still fails closed. Retained runs
raise the reconnect cap above the Phase-1 smoke (`max_reconnects=1`) so a
single disconnect does not end a 72h window; gaps remain explicit markers.
This is not lossless recovery.

The 60-second 2026-08-31 smoke remains evidence only for bounded reachability.

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
5. Do not run `wsl --shutdown` from PowerShell while `bn-capture` or
   `hl-capture` is alive.
6. Detached tmux survives closing Windows Terminal; it does **not** survive a
   WSL VM stop.

Spot `depth@100ms` is heavier than DATA-1A (the 60s smoke wrote ~8 MB payload /
~1.6 MB Parquet). Budget **tens of GB** and well above tens of KB/s for a 72h
retain; watch free disk on the WSL filesystem. Bandwidth and disk both matter;
host sleep still kills the collector.

The workstation still must not hold the Hyperliquid master-wallet key, any
LIVE agent key, or any Binance API key. Public capture does not need
credentials.

## Fail-closed start

Copy-paste inside **WSL2 Ubuntu** only after CoS assign. PAPER is irrelevant
to this public collector; it never signs or submits orders.

```bash
cd ~/code/Hyperliquid-Bot-main
git checkout main
git pull --ff-only

(
  set -euo pipefail
  unset TRADING_MODE
  unset D41_EXECUTION_MODE
  # Refuse to start if any protected key name is present.
  # Do not print or log secret values.
  for name in HYPERLIQUID_PK HYPERLIQUID_TESTNET_PK HYPERLIQUID_VAULT \
              HYPERLIQUID_TESTNET_VAULT HYPERLIQUID_ACCOUNT_ADDRESS \
              BINANCE_API_KEY BINANCE_API_SECRET BINANCE_SECRET \
              BINANCE_API_KEY_TESTNET BINANCE_TESTNET_API_SECRET
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
  export TMUX_SESSION=bn-capture

  test "${TMUX_SESSION}" != "hl-capture"
  mkdir -p "${ARTIFACT_ROOT}"
  test ! -e "${ARTIFACT_ROOT}/data-1f/binance/BTCUSDT/${RUN_ID}"
  test -z "$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -x "${TMUX_SESSION}" || true)"

  tmux new-session -d -s "${TMUX_SESSION}" \
    "cd ${HOME}/code/Hyperliquid-Bot-main && \
     unset TRADING_MODE D41_EXECUTION_MODE && \
     export PYTHONPATH=src && \
     exec uv run --frozen python -m hyperliquid_bot.binance_public_research \
       --artifact-root ${ARTIFACT_ROOT} \
       --run-id ${RUN_ID} \
       --duration-seconds ${DURATION_SECONDS}"

  echo "started run_id=${RUN_ID} session=${TMUX_SESSION} duration=${DURATION_SECONDS}"
)
```

Use **either** `--artifact-root` + `--run-id` **or** the disposable
`--output-dir` + `--database` smoke pair. Mixing them fails closed.

The reconstructable command is create-only. An existing run directory is refused.

Optional helper (same fail-closed checks; create-only; refuses `hl-capture`):

```bash
cd ~/code/Hyperliquid-Bot-main
DURATION_SECONDS=259200 ./scripts/data1f_start.sh
```

`./scripts/data1f_start.sh --check-only` validates env/paths without creating a
tmux session or a run directory. It does **not** start capture.

## Status

```bash
cd ~/code/Hyperliquid-Bot-main
export ARTIFACT_ROOT="${HOME}/hyperliquid-artifacts/reconstructable"
# Optional; omit RUN_ID to auto-detect the freshest live retain.
export RUN_ID=20260904t000000z-live-retained
export TMUX_SESSION=bn-capture

tmux has-session -t "${TMUX_SESSION}" && echo "tmux_alive=yes" || echo "tmux_alive=no"
tmux has-session -t hl-capture && echo "hl_capture_tmux_alive=yes" || echo "hl_capture_tmux_alive=no"
tmux list-panes -t "${TMUX_SESSION}" -F '#{pane_pid} #{pane_current_command}' 2>/dev/null || true

RUN_DIR="${ARTIFACT_ROOT}/data-1f/binance/BTCUSDT/${RUN_ID}"
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
RUN_ID=20260904t000000z-live-retained ./scripts/data1f_status.sh
```

When `RUN_ID` is unset, status defaults to the freshest live retain under
the Binance path contract (claim present, fresh `raw/part-*.parquet` mtime;
health JSON may be absent mid-run). It never prefers a stopped
`20260905t235830z-live-retained` over a live
`20260906t101559z-live-retained` (or any newer live run). Stopped
`COMPLETED` / `FAILED` / `OPERATOR_STOP` dirs are not preferred when a live
candidate exists. The chosen `run_id` is printed. The helper also prints
tmux liveness for `bn-capture`, observational `hl-capture` liveness,
claim/health presence, published part count, and reconnect/gap hints per
`transport_profile` (especially `usdm_public`) when those counts are already
in health or labeled in `capture-*.log` (`n/a` if unknown). It never prints
payload bytes or secret values and never sends keys to `hl-capture`.

## Protect a 72h evidence window

When the assigned goal is a 72-hour reconstructable tape (`DURATION_SECONDS=259200`):

- Do **not** send `C-c`, SIGINT, SIGTERM, `tmux kill-session`, or `wsl --shutdown`.
- Cloud Agents must not SSH, attach, or stop tmux `hl-capture` / `bn-capture` /
  `bv-capture` / `kr-capture`.
- USD-M `bookTicker` uses a dedicated `/public` combined socket. Regular
  `aggTrade`/`markPrice`/`forceOrder` stay on `/market`. Reconnect accounting
  has three independent WebSocket profiles (`spot`, `usdm_market`,
  `usdm_public`). Combined streams must not mix `/public` and `/market`
  categories.
- After stop, `duration_seconds` is the requested window and `elapsed_seconds`
  is wall-clock time until stop. `OPERATOR_STOP` with
  `elapsed_seconds` < `duration_seconds` is an operator interrupt, not a
  completed 72h tape.
- Transport `gaps` / `reconnects` are also listed under `transport_profiles`.
  Integrity events such as `sequence_gap` or `liveness_error` still fail the
  run and are not counted in `gaps`.
- Required streams (`spot` trade/bookTicker/depth@100ms, `usdm_market`
  aggTrade/markPrice@1s, `usdm_public` bookTicker) are watched mid-run. Official
  Spot JSON/SBE docs: server ping ~20s, pong within 1 minute, connection ~24h,
  `serverShutdown`. Official USD-M Connect: server ping every 3 minutes, pong
  within 10 minutes. Official required-stream update speeds are real-time or
  100ms–1s. DATA-1F sets `ping_interval=None` so only Binance server pings drive
  keepalive; the library still auto-pongs. The previous `websockets` default
  client Ping timed out as **close_code=1011** on high-frequency `/public`
  bookTicker and caused `usdm_public` reconnect churn. Spot and `usdm_public`
  use `max_queue=1024` so HF frames do not stall the default 16-frame buffer.
  Gaps remain recorded.
  Mild reconnect backoff (cap 24s) stays under the 300 connections / 5 minutes /
  IP limit and under the 60s starve bound.   Application silence past `required_stream_starvation_seconds` (60s) on a
  previously observed required stream records a transport `gap`
  (`reason=required_stream_starved`) and force-reconnects that profile only.
  Empty / never-observed streams, or starve after the reconnect bound is
  exhausted, still fail closed with `liveness_error` and health status
  `FAILED`. `forceOrder` is optional; liquidation silence is not starvation.
  An empty required stream must not pass as a healthy retain on
  `OPERATOR_STOP`. Terminal `FAILED` emits one event-driven
  `capture_operator_alert` (optional `CAPTURE_ALERT_WEBHOOK_URL`); no polling
  cron.
- **Apply path:** this keepalive fix needs a BN process restart. Prefer after the
  current 72h retain unless Chupa explicitly OKs a BN-only restart (CoS gates).
  Do not stop `hl-capture` / `bv-capture` / `kr-capture`. Until restart, the
  live tape is not silent but Quant `bn_gap_fraction` may stay high.

Collector INFO log: `<run_dir>/capture-<run_id>.log` (must be non-empty; no
payloads or secrets). Optional tmux copy:
`<artifact-root>/logs/capture-<run_id>.log`.

## How to stop

Ask the collector to finish the current in-memory segment and write health
**only when the assigned window is complete or CoS orders a stop**. Do not C-c
a live 72h evidence run.

```bash
tmux send-keys -t bn-capture C-c
```

Optional helper:

```bash
cd ~/code/Hyperliquid-Bot-main
./scripts/data1f_stop.sh
```

Do **not** `tmux kill-session` as the first choice; a hard kill can drop the
in-memory segment. Wait until `capture-health.json` appears.

Do **not** run `tmux send-keys -t hl-capture C-c` from this runbook.

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.

Confirm:

```bash
tmux has-session -t bn-capture && echo still_alive || echo stopped
python3 -c 'import json,pathlib,os; p=pathlib.Path(os.path.expanduser("~/hyperliquid-artifacts/reconstructable/data-1f/binance/BTCUSDT/'"${RUN_ID}"'/capture-health.json")); print(json.loads(p.read_text())["status"])'
```

## How to continue later (there is no resume)

The claim records:

```text
resume_policy: never resume or overwrite an existing DATA-1F run directory
```

Never resume the same `run_id`. After a stop, start a **new** `run_id`. Do not
reuse the previous directory, do not append to published Parquet, and do not
copy a live DuckDB catalog into a new run.

## Quant handoff (PAPER basis stub)

When the run has finished (`capture-health.json` present) and Quant wants the
reserved PAPER basis slot against a **completed** DATA-1A Hyperliquid series:

```bash
cd ~/code/Hyperliquid-Bot-main
export PYTHONPATH=src
export ARTIFACT_ROOT="${HOME}/hyperliquid-artifacts/reconstructable"
export HL_RUN_ID=20260904t134940z-live-retained   # completed DATA-1A run only
export BN_RUN_ID=20260904t000000z-live-retained   # completed DATA-1F run only

PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.hypothesis_research \
  --artifact-root "${ARTIFACT_ROOT}" \
  --run-id "${HL_RUN_ID}" \
  --baseline basis \
  --binance-parquet-dir "${ARTIFACT_ROOT}/data-1f/binance/BTCUSDT/${BN_RUN_ID}/raw"
```

This is PAPER-only research. It does not capture data, does not thaw v3
coverage, and never assigns `edge`. The basis slot still does not fit a
comparison even when the Binance directory is present. Thresholds (72h span,
trade/BBO/mid counts, 5% incomplete-hour cap) live in `docs/DATA.md` and apply
to the Hyperliquid series.

Do not start Quant against a still-running directory as a promotion step. The
DuckDB catalog is rebuilt at collector stop; mid-run `research.duckdb` is not a
handoff artifact.

## Explicitly not proven

- 24/7 collection or an always-on host service
- lossless WAL / crash-safe in-memory segment recovery
- Cloud Agent multi-day capture
- overlapping calendar coverage with the live TerraPC DATA-1A 72h series
  (this PR prepares the operator path; it does not start capture)
- D22-B venue-authoritative reconciliation
- funding settlement, signing, TESTNET, SHADOW, LIVE
- profitability or strategy promotion
- Bitvavo DATA-1E / Kraken DATA-1B multi-day retain (operator docs exist;
  do not start until CoS assigns)
- a complete Spot tape, full-book depth, or USD-M liquidation/OI history
