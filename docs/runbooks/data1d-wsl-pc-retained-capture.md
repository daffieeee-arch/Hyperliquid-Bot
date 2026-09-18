# DATA-1D WSL/PC runbook — retained Bitvavo Standard BTC-EUR capture

Status: operator companion for TerraPC (Windows 11 + WSL2 Ubuntu).
Authoritative duration, disk, candles, and coexistence rules live in
[data1d-vps-retained-capture.md](data1d-vps-retained-capture.md).
The Netcup Ubuntu 24.04 LTS VPS is the primary PAPER/capture host (ADR-024).

Credential-free Bitvavo Standard: `trades` / `ticker` / `book`, optional
`--include-candles`. Never DATA-1E Market Data Pro. Never silent Pro path
fallback.

Cloud Agents are **unsuitable** for a multi-day retain and must not SSH to
stop `hl-capture`, `bn-capture`, `bv-capture`, `kr-capture`, `bv-std-capture`,
or `cockpit`. See [phase-a-72h-joint-retained-capture.md](phase-a-72h-joint-retained-capture.md)
for joint Phase A posture. **Do not start a multi-day DATA-1D retain now**
without CoS assignment and Chupa OK for `DURATION_SECONDS=259200`.

## Known-good operator paths (WSL)

| Item | Path / value |
| --- | --- |
| Repo checkout | `~/code/Hyperliquid-Bot-main` |
| Artifact root | `~/hyperliquid-artifacts/reconstructable` |
| Product layout | `data-1d/bitvavo/BTC-EUR/<run_id>/` |
| tmux session | `bv-std-capture` (never `bv-capture`) |
| Example retained duration | `259200` seconds (72 hours) |
| Module | `python -m hyperliquid_bot.bitvavo_standard_research` |

```text
~/hyperliquid-artifacts/reconstructable/data-1d/bitvavo/BTC-EUR/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1d_run_paths(artifact_root, run_id)`. `run_id` must be 1–64
lowercase ASCII letters, digits, dot, dash, or underscore.

## Host posture (WSL)

Keep AC plugged in. Disable sleep that would reclaim the VM mid-retain
(`standby-timeout-ac 0`). Prefer not to `wsl --shutdown` during an assigned
window. Prefer a pinned checkout; do not capture from a shared dirty worktree.

## Start / status / stop

```bash
export REPO_ROOT="$HOME/code/Hyperliquid-Bot-main"
export ARTIFACT_ROOT="$HOME/hyperliquid-artifacts/reconstructable"
export DURATION_SECONDS=259200
# optional:
# export INCLUDE_CANDLES=1
# export CANDLE_INTERVAL=1m

cd "$REPO_ROOT"
DURATION_SECONDS=259200 ./scripts/data1d_start.sh --check-only
# After assignment + PHASE_A_CHUPA_OK=1:
# DURATION_SECONDS=259200 ./scripts/data1d_start.sh

./scripts/data1d_status.sh
# Protect a 72h evidence window: Do **not** send `C-c` mid-window.
# ./scripts/data1d_stop.sh
# tmux send-keys -t bv-std-capture C-c
```

Create-only: never resume. Prefer the freshest live retain when `RUN_ID` is
unset on status. Channels on the same Standard socket: `trades`, `ticker`,
`book`; optional `candles` via `--include-candles`. `getBook` depth **1000**.
Feed id: `bitvavo-standard-btc-eur-trades-ticker-book` (or with candles suffix).
`mdpro_fallback` / `data1e_path_fallback` stay false. `elapsed_seconds` is
distinct from requested `duration_seconds`. Collector log: `capture-<run_id>.log`.

## Coexistence

Pro MD Pro remains on tmux `bv-capture` and `data-1e/...`. Standard uses
`bv-std-capture` and `data-1d/...`. Disk estimate for Standard 72h: about
**+3-5 GB** (see VPS runbook). LIVE trading is out of scope.
