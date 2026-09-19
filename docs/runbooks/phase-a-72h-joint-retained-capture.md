# Phase A — joint 72h PAPER retained capture (primary VPS)

Status: **prepare-only**. This document is the campaign runbook for one
bounded, overlapping 72-hour PAPER retain of the four production lanes that
already have start/status/stop helpers. Cloud Agents must not start this
campaign or send `C-c` to live sessions. Run it on the Netcup Ubuntu 24.04
LTS VPS only after the explicit operator gate.

This is a **new** campaign. Prior mid-window `OPERATOR_STOP` retains are not
success evidence and must not be resumed or overwritten.

## In scope

| Lane | Venue / product | tmux | Helper prefix |
| --- | --- | --- | --- |
| DATA-1A | Hyperliquid BTC-PERP (public) | `hl-capture` | `scripts/data1a_*.sh` |
| DATA-1F | Binance BTCUSDT spot + USD-M context | `bn-capture` | `scripts/data1f_*.sh` |
| DATA-1E | Bitvavo BTC-EUR MD Pro (no silent Standard fallback) | `bv-capture` | `scripts/data1e_*.sh` |
| DATA-1B | Kraken BTC/USD L2+tape, L3 when WS keys present (not BTC/EUR) | `kr-capture` | `scripts/data1b_*.sh` |

Primary per-lane detail is in the VPS runbooks:

- [data1a-vps-retained-capture.md](data1a-vps-retained-capture.md)
- [data1f-vps-retained-capture.md](data1f-vps-retained-capture.md)
- [data1e-vps-retained-capture.md](data1e-vps-retained-capture.md)
- [data1b-vps-retained-capture.md](data1b-vps-retained-capture.md)

## Out of scope

OKX, Deribit, and Polymarket retained capture (research modules only — do not
claim eight venues ready). No Coinbase, Aster, or new venues. No historical
bulk download. No LIVE / orders / wallets / VPN. Cloud Agents must not start
or stop collectors.

## Official contracts checked (2026-09-11)

Do not change stream/API/venue/instrument code from memory. Sources used for
this campaign:

- Hyperliquid WS: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket
- Hyperliquid subscriptions (`trades`, `bbo`, `l2Book`, `activeAssetCtx`; coins
  `BTC` / `ETH` / `SOL`): https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
- Hyperliquid idle/ping (`{"method":"ping"}`, 60s server idle):
  https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/timeouts-and-heartbeats
- Binance USD-M `/public` (high-frequency `bookTicker` / `depth`) vs `/market`
  (regular `aggTrade` / `markPrice` / `forceOrder`):
  https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice
- Bitvavo MD Pro `wss://ws-mdpro.bitvavo.com/v2/`, market `BTC-EUR`:
  https://docs.bitvavo.com/docs/ws-market-data-pro-api/
- Kraken Spot WS v2 `BTC/USD` (not `XBT/USD`), public `wss://ws.kraken.com/v2`,
  authenticated L3 `wss://ws-l3.kraken.com/v2`:
  https://docs.kraken.com/exchange/api-reference/spot-websocket
  https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/level3

DATA-1F already fail-closes if USD-M `bookTicker` is placed on `/market`.
DATA-1E must not silently fall back to Standard. DATA-1B must not silently
fall back to BTC/EUR.

## ETH / SOL add-on (Hyperliquid only)

Explicit instrument configs exist for official coins `ETH` and `SOL`
(`trades`, `bbo`, `l2Book`, `activeAssetCtx` mark/funding/OI). They are
**optional add-on channels**, never BTC replacements.

Phase A **start policy is `deferred_at_start`**. Enabling ETH+SOL triples the
Hyperliquid `l2Book` snapshot cadence (official book push ≥0.5s per coin) on
top of Binance `depth@100ms` and optional Kraken L3. That is a documented
bandwidth conflict until a measured joint smoke says otherwise. Do not
silently drop BTC channels to make room.

To enable later (not this campaign start):

```bash
# Only after Chupa OK and a measured smoke that fits the cap.
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.hyperliquid_raw_research \
  --artifact-root "$ARTIFACT_ROOT" \
  --run-id "$RUN_ID" \
  --duration-seconds 259200 \
  --addon-coins ETH,SOL \
  --enable-addons
```

Optional official WS `candle` (including `1m`) is also flagged and **off by
default** (`--include-candles` / `INCLUDE_CANDLES=1`). Enable only on a **new
`run_id` after** the current Phase A 72h ends — never mid-run. See
[data1a-vps-retained-capture.md](data1a-vps-retained-capture.md).

## Collectors stay independent of Cursor / Codex / chat

Use the existing detached **tmux** sessions (`hl-capture`, `bn-capture`,
`bv-capture`, `kr-capture`) or equivalent **systemd --user** units that exec
the same `uv run --frozen python -m …` commands. Do not invent a new
orchestrator. Closing the chat must not stop the retain.

Tests and CI use isolated fake session names (`pytest-hl-isolated`, …) and
refuse to send `C-c` to live names. Never run pytest against a checkout that
shares those live tmux names without the isolation helpers.
No `pkill`, `killall`, or `tmux kill-server`.

## Pinned checkout and per-feed outputs

Do not share a development worktree or its venv with the retain.

```bash
REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
```

Pin `REPO_ROOT` to the merged commit SHA of this campaign (or a later
approved tag). Create a dedicated venv in that checkout (`uv sync --frozen`).
Per-feed directories stay on the existing path contracts:

```text
$ARTIFACT_ROOT/data-1a/hyperliquid/BTC-PERP/<run_id>/
$ARTIFACT_ROOT/data-1f/binance/BTCUSDT/<run_id>/
$ARTIFACT_ROOT/data-1e/bitvavo/BTC-EUR/<run_id>/
$ARTIFACT_ROOT/data-1b/kraken/BTC-USD/<run_id>/
```

Create-only: never resume or overwrite an existing `run_id`. Mask LAN
addresses as `192.168.x.x` / `<LAN-IP>` in public notes.

## Distinct 72h profile (not DATA-2A)

DATA-2A short-pilot / smoke limits (1–3600s, historical 600s classification)
are **not** valid for three days. The Phase A retain is exactly
`DURATION_SECONDS=259200` (`retained_72h`).

Precompute (operator budgets, not measured rates):

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.retained_capture_profile \
  --artifact-root "$ARTIFACT_ROOT" \
  --duration-seconds 259200
```

| Item | Value |
| --- | --- |
| Expected parts / lane (60s rotation lower bound) | 4320 |
| Combined disk budget | ~93 GiB |
| Free-space reserve | ≥50 GiB (20% of budget if larger) |
| Writer semantics | unchanged (atomic parts, no silent drops) |

Heartbeats / pongs keep a socket alive. They are **not** market-data validity.
A `market_data_stale_despite_heartbeat` marker means the tape had no
trades/BBO/L2/ctx while ping/pong continued. Integrity / queue / reconnect
labels stay honest.

## Bandwidth policy

Nominal home downlink ~1 Gbit. Cap the **capture process set only** (not the
household and not the whole WSL NIC) to:

- ≤ **100 Mbit/s** download
- ≤ **5 Mbit/s** upload
- and ≤ **10%** of the operator-measured real upload

Supported process-scoped limiter: Ubuntu `trickle` wrapping each tmux
command (`trickle -s -d 12800 -u 640`). Alternative: operator-attested
cgroup/netns limiter with `CAPTURE_BANDWIDTH_ENFORCED=attested` after the
operator verifies the cap.

A monitor (`nethogs`, `ss`, cgroup counters) is a **measure hook**, not a
hard limiter. Do not sleep in receive loops, sample, or silently drop. If
the measured capture rate would exceed the cap, **report conflict** and
stop the start — do not raise the budget or degrade quality.

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.capture_bandwidth_policy
```

72h start refuses unless `trickle` is installed or the attested flag is set.
Joint smoke (≤60 min) may run without the hard limiter so the operator can
measure; it still must not exceed the cap.

## Credentials

Use the existing data-only env files / Cursor secret names already used
locally (`BITVAVO_MDPRO_API_KEY` / `BITVAVO_MDPRO_API_SECRET`,
`KRAKEN_WS_API_KEY` / `KRAKEN_WS_API_SECRET`). Never print secrets in chat,
argv, logs, artifacts, or git. Hyperliquid and Binance lanes stay
credentialless. Refuse `HYPERLIQUID_*` signing names and execution keys.

## Secondary WSL host checklist

On the Windows host, before smoke or 72h:

1. `powercfg /change standby-timeout-ac 0` and disable sleep / hibernate
   while on AC.
2. Keep the lid policy from sleeping the machine.
3. Do **not** run `wsl --shutdown` from PowerShell while any of
   `hl-capture`, `bn-capture`, `bv-capture`, or `kr-capture` is alive.
4. Keep artifacts on the Linux filesystem, not `/mnt/c`.
5. Confirm the pinned checkout and dedicated venv, not a dirty worktree.

## Operator checklist

### 1. Preflight (this repo, offline)

- [ ] Feature branch merged or pinned SHA recorded
- [ ] `uv run --frozen pytest` on the isolation / profile / instrument tests
- [ ] `python -m hyperliquid_bot.retained_capture_profile --artifact-root …`
- [ ] `python -m hyperliquid_bot.capture_bandwidth_policy`
- [ ] `DURATION_SECONDS=259200 ./scripts/data1{a,f,e,b}_start.sh --check-only`
- [ ] Free space ≥ required_free_bytes
- [ ] `trickle` installed **or** attested limiter documented
- [ ] Bitvavo MD Pro and Kraken WS secrets present in the capture env (values
      not printed)
- [ ] Host sleep / WSL checklist done

### 2. Offline checks

- [ ] `TRADING_MODE` unset or `PAPER`
- [ ] No `HYPERLIQUID_PK` / vault / account-address names set
- [ ] Four `run_id` directories do not exist (create-only)
- [ ] Live tmux names are free
- [ ] ETH/SOL remain deferred

### 3. Joint VPS smoke (≤60 min) — operator only

Cloud Agents must not do this step.

```bash
export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export DURATION_SECONDS=3600
export PHASE_A_JOINT_SMOKE=1
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-phase-a-smoke"
# start all four helpers; then status; then STOP
./scripts/data1a_start.sh
./scripts/data1f_start.sh
./scripts/data1e_start.sh
./scripts/data1b_start.sh
```

Status:

```bash
./scripts/data1a_status.sh
./scripts/data1f_status.sh
./scripts/data1e_status.sh
./scripts/data1b_status.sh
```

### 4. STOP and wait for Chupa explicit OK

```bash
./scripts/data1a_stop.sh
./scripts/data1f_stop.sh
./scripts/data1e_stop.sh
./scripts/data1b_stop.sh
```

Drain: wait until each `capture-health.json` exists and the tmux session is
gone. Confirm `elapsed_seconds` vs requested smoke duration. Do **not**
treat this smoke as the 72h tape. Do **not** start 72h until Chupa says OK
in the operator channel.

### 5. 72h retain with a fixed end time

Only after Chupa explicit OK:

```bash
export DURATION_SECONDS=259200
export PHASE_A_CHUPA_OK=1
export CAPTURE_BANDWIDTH_ENFORCED=attested   # if trickle is not the wrapper
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-phase-a-72h"
# record UTC start and UTC end = start + 259200s
./scripts/data1a_start.sh
./scripts/data1f_start.sh
./scripts/data1e_start.sh
./scripts/data1b_start.sh
```

The process ends at the requested duration or on operator SIGINT. After
natural end, confirm four `capture-health.json` files, published
`raw/part-*.parquet`, and DuckDB catalogs. End-of-run drain is the writer
`aclose()` plus health write — wait for health before copying or analyzing.

Do **not** send `C-c` during the evidence window. Cloud Agents must not
stop a live 72h tape. `OPERATOR_STOP` with `elapsed_seconds` <
`duration_seconds` is an interrupt, not a completed 72h retain.

## Status / stop (live names, operator only)

```bash
./scripts/data1a_status.sh   # tmux hl-capture
./scripts/data1f_status.sh   # tmux bn-capture
./scripts/data1e_status.sh   # tmux bv-capture
./scripts/data1b_status.sh   # tmux kr-capture

# Only after the fixed end time, or Chupa emergency OK:
./scripts/data1a_stop.sh
./scripts/data1f_stop.sh
./scripts/data1e_stop.sh
./scripts/data1b_stop.sh
```

## Residual risks (not started from this document)

- `trickle` is userspace and can miss some sockets; attest a cgroup/netns
  limiter if measurement shows leak-through.
- Disk budgets are operator estimates; watch `df` during smoke.
- Kraken public `trade` can be quiet; L3 requires data-only WS keys.
- Bitvavo MD Pro is a distinct product; Standard is not a fallback.
- ETH/SOL remain deferred; enabling them is a new bandwidth decision.
- This campaign has **not** started. Pin, smoke, Chupa OK, then 72h.
