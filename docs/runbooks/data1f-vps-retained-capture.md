# DATA-1F VPS runbook — retained Binance public BTCUSDT capture

Status: operator runbook for the reconstructable CLI
`python -m hyperliquid_bot.binance_public_research`.
This is **not** a 24/7 service, not D22-B, and not LIVE trading.

Public Binance MAINNET market data only (Spot BTCUSDT + USDⓈ-M BTCUSDT). No
keys, no signing, no Bitvavo, no Kraken, and no extra venues.

For the Windows 11 + WSL2 Ubuntu operator PC (TerraPC), including tmux
`bn-capture` start/status/stop, the joint Phase A 72h campaign, and Quant
handoff, see [data1f-wsl-pc-retained-capture.md](data1f-wsl-pc-retained-capture.md)
and [phase-a-72h-joint-retained-capture.md](phase-a-72h-joint-retained-capture.md).
Cloud Agents are unsuitable for a multi-day retain and must not stop
`hl-capture` or `bn-capture`. The active host profile is
[linux-vps-reference-profile.md](linux-vps-reference-profile.md).

**Do not start a multi-day DATA-1F retain without assignment.** Phase A is a
joint four-lane VPS retain after smoke + Chupa OK.

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

Spot `depth@100ms` is heavier than DATA-1A. Budget tens of GB for a 72h retain
on the durable volume.

## Path contract

```text
<artifact-root>/data-1f/binance/BTCUSDT/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1f_run_paths(artifact_root, run_id)`. Hypothesis
`--binance-parquet-dir` is the `raw/` directory of a **completed** run.

`run_id` must be 1–64 lowercase ASCII letters, digits, dot, dash, or underscore.

## Fail-closed start

```bash
# From the Netcup Ubuntu 24.04 LTS VPS.
# PAPER is irrelevant to this public collector; it never signs or submits orders.
# Do not start until CoS assigns this window after the DATA-1A 72h series.
unset TRADING_MODE
unset D41_EXECUTION_MODE
# Refuse to start if any protected key name is present in the shell.
# Do not print or log secret values.

export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export PYTHONPATH=src
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
# Example retained window: 4 hours. Raise up to 604800 on the durable VPS store.
export DURATION_SECONDS=14400

test ! -e "${ARTIFACT_ROOT}/data-1f/binance/BTCUSDT/${RUN_ID}"

cd "$REPO_ROOT"
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.binance_public_research \
  --artifact-root "${ARTIFACT_ROOT}" \
  --run-id "${RUN_ID}" \
  --duration-seconds "${DURATION_SECONDS}"
```

Use **either** `--artifact-root` + `--run-id` **or** the disposable
`--output-dir` + `--database` smoke pair. Mixing them fails closed.

The reconstructable command is create-only. An existing run directory is refused.

## How retain works

- Set `--duration-seconds` above 180 and at most 604800.
- Keep `${ARTIFACT_ROOT}` on the durable VPS volume, not inside git.
- Do not commit `raw/part-*.parquet` or `research.duckdb`.
- The process still exits at the requested duration or on SIGINT/SIGTERM.
- USD-M `bookTicker` uses a dedicated `/public` combined socket.
  `aggTrade`/`markPrice`/`forceOrder` stay on `/market`. Reconnect
  accounting has three independent WebSocket profiles (`spot`,
  `usdm_market`, `usdm_public`). Combined streams must not mix categories.

## Protect a 72h evidence window

Do **not** stop a mid-window 72h evidence run. Cloud Agents must not stop
captures. If systemd supervises the collector, use `Type=simple`,
`KillSignal=SIGINT`, and `Restart=no`. Do not `systemctl stop` during an
assigned 72h tape.

After stop, `duration_seconds` is the requested window and `elapsed_seconds`
is wall-clock time until stop. Collector log: `<run_dir>/capture-<run_id>.log`.

## How to stop

```bash
# Ask the collector to finish the current in-memory segment and write health.
# Do not do this during an assigned 72h evidence window.
kill -TERM "${COLLECTOR_PID}"
```

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.
`elapsed_seconds` must be read separately from requested `duration_seconds`.
Required-stream application silence past 60s (`required_stream_starvation_seconds`)
on a **previously observed** stream records a transport `gap`
(`reason=required_stream_starved`) and force-reconnects that profile only — it
does **not** fail the whole multi-socket retain. A Spot depth REST snapshot
transport failure retries `GET /api/v3/depth` inside the session (official
local-book step 3), then force-reconnects **that profile only**
(`reason=profile_transport_error`). It does not set the shared stop. Empty /
never-observed required streams, or starve after the reconnect bound is
exhausted, still fail closed mid-run with `liveness_error` and status `FAILED`;
that is not a transport `gap`. A profile that still stops the shared run before
the requested duration writes `FAILED` and one `capture_operator_alert`, not
`COMPLETED`.
Official Spot JSON/SBE (checked 2026-09-27,
`binance-spot-api-docs` `web-socket-streams.md`): server ping every 20s, pong
within 1 minute, connection ~24h, `serverShutdown`. The February 2025 changelog
notice moved Spot from a 3-minute ping / 10-minute pong to that 20s / 1 minute
window. Official USD-M Connect (2026-06-17 page, re-checked 2026-09-27): server
ping every 3 minutes, pong within 10 minutes (≫ the 60s app bound). The USD-M
**Important WebSocket Change Notice** is the `/public` vs `/market` split:
`bookTicker` stays on `/public`; `aggTrade` / `markPrice@1s` / `forceOrder` stay
on `/market`. Do not mix them. Spot limit: **300 connection attempts / 5 minutes
/ IP**. USD-M also caps 10 client messages/s and 1024 streams; repeated
disconnects can ban an IP.

`ping_interval=None` (Binance server ping only; library auto-pong). A leftover
client keepalive Ping times out as **close_code=1011** on `/public` bookTicker.
#58 stopped that client ping. `websockets` pauses socket reads once `max_queue`
(1024) is full, so a server PING is not answered while recv waits on Parquet.
Each profile drains the socket into a bounded queue (`BINANCE_SOCKET_DRAIN_MAX`,
16384 frames, about one Spot pong window at ~150 bookTicker frames/s) before
append. The queue does not drop frames. `required_stream_starved` still
force-reconnects a profile only when that socket is idle and the drain queue
is empty. A slow append is not starvation. Health field
`reconnect_microstructure` is `fragile` when `reconnects > 0` (minute bins can
still be complete; Spot depth sync resets). `twenty_four_seven` stays false.
Short reconnects do not emit `capture_operator_alert`. Phase A code-restart
`20260924t164936z-phase-a-72h-code-restart` still saw **269** reconnects
(spot ~138, `usdm_public` ~128, `usdm_market` ~3) from server pong misses and
the 60s starve gate. Minute-bar `gap_frac` stayed ~0. That is not silence.

Reconnect wait is 3s / 6s / 12s, cap 24s, plus per-profile jitter ≤20%. The
total stays under the 60s starve bound. Fail closed, as `integrity_liveness`
and not as an extra gap, when either budget trips:

- 6 admitted reconnects / 5 min on one profile, or 15 admitted reconnects /
  5 min across the three profiles; the next attempt is refused
  (`reconnect_storm`)
- 8 admitted pong-class closes or `required_stream_starved` reconnects /
  60 min on one profile; the next attempt is refused
  (`pong_starvation_pattern`)

`capture-health.json` lists `spot`, `usdm_market`, and `usdm_public` separately
with reconnects, gaps, `close_code_counts`, and `exception_class_counts` even
when `gaps` is 0. `forceOrder` silence is optional and is not starvation. An
empty required stream must not be accepted as a healthy retain on
`OPERATOR_STOP`. Terminal `FAILED` emits one event-driven
`capture_operator_alert`. Optional `CAPTURE_ALERT_WEBHOOK_URL` plus
`CAPTURE_ALERT_WEBHOOK_AUTHORIZATION` (header value, never logged), also read
from `~/.config/hyperliquid-bot/capture-alert.env` when unset in the process:
up to 3 POSTs of 5s; 401/403 are not retried; failures stay in the log as
`http_status` and do not crash the writer. Ochtendbriefing stays separate.
A dead process is covered by `scripts/capture_alert.sh watch`
(`docs/runbooks/capture-failure-alert.md`).

**Apply path:** BN process restart is required before backoff, storm gates,
and the socket drain are live. CoS / VPS ops only. Do not restart `bn-capture`
from a cloud agent or from this note. Live HL/BV/KR untouched.

## How to continue later (there is no resume)

The claim records:

```text
resume_policy: never resume or overwrite an existing DATA-1F run directory
```

To retain more data after a stop, start a **new** `run_id`. Do not reuse the
previous directory, do not append to published Parquet, and do not copy a live
DuckDB catalog into a new run.

BN-only continue after an early `FAILED` (do not touch `hl-capture`,
`bv-capture`, `kr-capture`, `bv-std-capture`, or `cockpit`). `bn-capture` must
already be down. Pick a new `run_id`. Set `DURATION_SECONDS` to the seconds
left until the original planned end, not a fresh 259200, unless Chupa asks for
a new full window (`PHASE_A_CHUPA_OK=1` is required only when the duration is
exactly 259200). Checkout the merged SHA in the pinned retain checkout first.

```bash
export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export TMUX_SESSION=bn-capture
export DURATION_SECONDS="$(python3 - <<'PY'
import math
from datetime import UTC, datetime
end = datetime(2026, 9, 26, 13, 2, 54, tzinfo=UTC)
print(max(1, math.floor((end - datetime.now(UTC)).total_seconds())))
PY
)"
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-phase-a-72h-bn-continue"
./scripts/data1f_start.sh
```

BV-Std was not in the 2026-09-23 joint run. Joining it is a separate
`data1d_start.sh` decision. Do not start it from this note. BV-Pro ping-timeout
closes already reconnect; they are not this Binance stop.

## Explicitly not proven

- 24/7 collection or an always-on host service
- lossless WAL / crash-safe in-memory segment recovery
- Cloud Agent multi-day capture
- overlapping calendar coverage with the live TerraPC DATA-1A 72h series
- D22-B venue-authoritative reconciliation
- funding settlement, signing, TESTNET, SHADOW, LIVE
- profitability or strategy promotion
- Bitvavo DATA-1E / Kraken DATA-1B multi-day retain (operator docs exist;
  do not start until CoS assigns)
