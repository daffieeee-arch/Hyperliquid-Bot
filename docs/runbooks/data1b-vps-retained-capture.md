# DATA-1B VPS runbook — retained Kraken public BTC-USD capture

Status: operator runbook for the reconstructable CLI
`python -m hyperliquid_bot.kraken_l3_research`.
This is **not** a 24/7 service, not D22-B, and not LIVE trading.

Default path is public Kraken Spot `BTC/USD` L2 + trades at depth **100**.
Optional authenticated L3 uses `KRAKEN_WS_API_KEY` / `KRAKEN_WS_API_SECRET`
only and also defaults to depth 100. No trade keys, no signing, no OKX.

**Quote-world split:** EUR microstructure is **Bitvavo DATA-1E**. Kraken DATA-1B
is dense USD L3/tape aligned with the Hyperliquid / Binance quote world. Wire
symbol is Kraken Spot WebSocket v2 `BTC/USD` (not REST/v1 `XBT/USD`).
`BTC/EUR` / `XBT/EUR` / `XBT/USD` fail closed. There is **no silent EUR
fallback**. Path contract `data-1b-kraken-btc-usd-v1` replaced retired
`data-1b-kraken-btc-eur-v1`.

For the Windows 11 + WSL2 Ubuntu operator PC (TerraPC), including tmux
`kr-capture` start/status/stop, see
[data1b-wsl-pc-retained-capture.md](data1b-wsl-pc-retained-capture.md).
Cloud Agents are unsuitable for a multi-day retain and must not stop
`hl-capture`, `bn-capture`, `bv-capture`, or `kr-capture`. The active host
profile is
[linux-vps-reference-profile.md](linux-vps-reference-profile.md).

**Do not start a multi-day DATA-1B retain without assignment.** Wait for CoS
to assign the window via the joint Phase A checklist on this primary VPS.

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

## Path contract

```text
<artifact-root>/data-1b/kraken/BTC-USD/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

Helpers: `data1b_run_paths(artifact_root, run_id)`. The path segment is
`BTC-USD`; the wire symbol is `BTC/USD`. Retired `BTC-EUR` is not current.

`run_id` must be 1–64 lowercase ASCII letters, digits, dot, dash, or underscore.

## Depth, CRC, disk and bandwidth

CLI defaults: `--l2-depth 100` and `--l3-depth 100`. Existing Kraken depths
remain valid. CRC32 still covers **only the best 10 price levels** even at
subscribed depth 100; deeper retained levels are local scope, not CRC-covered.

Depth-100 snapshots are about 10× a depth-10 snapshot on connect/reconnect.
Incremental updates are not automatically 10×. Optional L3 at depth 100 is
heavier. No 72-hour depth-100 retain has been measured. Budget more disk and
bandwidth than the historical depth-10 smoke: watch the VPS volume, and expect
low-single-digit to low-tens of GB plus sustained KB/s–tens-of-KB/s for a
public 72h L2+trades retain, more if L3 is enabled. This is an operator
budget, not a measured rate.

## Auth env names

Public default: no keys.

Optional L3 (values never printed): `KRAKEN_WS_API_KEY`,
`KRAKEN_WS_API_SECRET`. Both or neither.

Refuse generic `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` and other protected
trade/signing names listed in the WSL runbook.

## Fail-closed start

```bash
# From the Netcup Ubuntu 24.04 LTS VPS.
# Do not start until CoS assigns this window.
unset TRADING_MODE
unset D41_EXECUTION_MODE

export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export PYTHONPATH=src
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
export DURATION_SECONDS=14400

test ! -e "${ARTIFACT_ROOT}/data-1b/kraken/BTC-USD/${RUN_ID}"

cd "$REPO_ROOT"
# Python defaults: --l2-depth 100 --l3-depth 100. CRC32 still covers only the best 10 levels.
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.kraken_l3_research \
  --artifact-root "${ARTIFACT_ROOT}" \
  --run-id "${RUN_ID}" \
  --duration-seconds "${DURATION_SECONDS}"
```

The reconstructable command is create-only. An existing run directory is refused.

## Protect a 72h evidence window

Do **not** stop a mid-window 72h evidence run. Cloud Agents must not stop
captures. If systemd supervises the collector, use `Type=simple`,
`KillSignal=SIGINT`, and `Restart=no`.

After stop, `duration_seconds` is the requested window and `elapsed_seconds`
is wall-clock time until stop. Collector log: `<run_dir>/capture-<run_id>.log`.

## How to stop

```bash
# Do not do this during an assigned 72h evidence window.
kill -TERM "${COLLECTOR_PID}"
```

Expected health statuses: `COMPLETED`, `OPERATOR_STOP`, or `FAILED`.
`elapsed_seconds` must be read separately from requested `duration_seconds`.

## How to continue later (there is no resume)

```text
resume_policy: never resume or overwrite an existing DATA-1B run directory
```

To retain more data after a stop, start a **new** `run_id`.

## Explicitly not proven

- 24/7 collection or an always-on host service
- lossless WAL / crash-safe in-memory segment recovery
- Cloud Agent multi-day capture
- starting this capture in this PR
- D22-B venue-authoritative reconciliation
- funding settlement, signing, TESTNET, SHADOW, LIVE
- profitability or strategy promotion
- OKX
