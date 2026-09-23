# Phase A research inventory (read-only)

Status: **helper runbook for the Netcup VPS (`chupa`)**. PAPER only.
Use this while a 72h window is still open. The helpers stat directories and
scan capture logs. They do not open Parquet payloads, do not write under
`data-capture/`, and do not start, stop, or signal tmux sessions
(`hl-capture`, `bn-capture`, `bv-capture`, `kr-capture`, `bv-std-capture`,
`cockpit`).

Do not stop collectors to “finish” a tape before running these commands.
`capture-health.json` appears at stop. A missing health file means the run
may still be open.

## Paths

```bash
export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export HIST_ARCHIVES_ROOT="$HOME/Hyperliquid Project/hist-archives"
cd "$REPO_ROOT"
```

Lane layout (same contracts as the retain writers):

```text
$ARTIFACT_ROOT/data-1a/hyperliquid/BTC-PERP/<run_id>/
$ARTIFACT_ROOT/data-1b/kraken/BTC-USD/<run_id>/
$ARTIFACT_ROOT/data-1d/bitvavo/BTC-EUR/<run_id>/
$ARTIFACT_ROOT/data-1e/bitvavo/BTC-EUR/<run_id>/
$ARTIFACT_ROOT/data-1f/binance/BTCUSDT/<run_id>/
```

Each run directory may contain `capture-claim.json`, `capture-health.json`,
`capture-<run_id>.log`, and `raw/part-*.parquet`. Hist archives stay a
sibling tree. Do not copy hist Parquet into a live `run_id`.

## Inventory

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.phase_a_research_tools inventory
```

One line per run directory: lane, `run_id`, on-disk `part-*.parquet` count,
claim/health presence, health status, health gap counter, parquet mtime
window (UTC), and gap notes. Notes call out:

- missing claim, or health not written yet
- `FAILED` and `OPERATOR_STOP`
- health `parquet_files` / `events` that disagree with the files on disk
- directory names that are not valid `run_id`s, including `.FAILED` markers
- `continue` names, which are new directories (there is no resume)

The same command prints the filesystem half of the DuckDB catalog check
below. Add `--json` for a machine-readable report. Part counts are a
point-in-time snapshot while a writer is appending.

## DuckDB catalog smoke (hist + live)

`hist-archives/catalog.sql` defines `hist_*` views over offline Vision /
OHLCVT Parquet and `live_*` views over specific Phase A `run_id`s. Those
live globs are pins. A later `*-continue` directory is a different `run_id`
and is not inside the pinned view until the catalog is edited.

Check globs without rebuilding the database:

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.phase_a_research_tools duckdb-smoke
```

Exit 0 when every `read_parquet` glob matches at least one file. Exit 1 when
`catalog.sql` is missing or a glob is empty. A live pin that is older than
the newest `phase-a` directory is printed as a note and does not fail the
command. `--strict-pins` makes that note fatal.

Optional read-only row counts for small hist views (`*_1h`, `*_4h`, `*_12h`,
`*_1d`, `*_funding`). AggTrades and `live_*` views are not scanned, so a
probe does not walk an in-progress retain:

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.phase_a_research_tools \
  duckdb-smoke --probe-db
```

When two or more campaign `phase-a` directories on a lane have parts (not
smoke, gate, dry-check, or fgtest names), the command
prints a `CREATE OR REPLACE VIEW live_*_phase_a_parts` statement. That text
is not applied. To publish it, paste the statement into
`hist-archives/catalog.sql` (not into a live run directory) and rebuild only
the hist catalog:

```bash
bash "$HIST_ARCHIVES_ROOT/scripts/build_catalog.sh"
```

`build_catalog.sh` rewrites `hist-archives/research.duckdb`. It does not
write `data-capture/`. Do not point it at a capture tmux session. Skip
`--probe-db` if an ETL job already has `research.duckdb` open for write; the
filesystem glob check still works.

USDT (Binance) vs USD (Kraken XBTUSD) closes are not an FX-neutral basis.
See [hist-archives-research-warehouse.md](hist-archives-research-warehouse.md).

## Binance gaps (transport / starvation, then a new run)

Phase A Binance stops are often `required_stream_starved` after transport
disconnects. The next tape is a new `run_id`. This command reads
`capture-*.log` counters (disconnect, reconnect, starvation `liveness_gap`,
`liveness_error`, local operator alert) and the wall-clock hole between
parquet windows. Default hole threshold is 120 seconds. Default selection is
directory names containing `phase-a`.

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.phase_a_research_tools bn-gaps
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.phase_a_research_tools \
  bn-gaps --all-runs --hole-seconds 120
```

Log bodies are not printed. Webhook failures are not counted as alerts.
Directory names containing `smoke`, `gate`, `dry-check`, or `fgtest` stay in
`inventory` and are left out of this chain unless you pass `--all-runs`.

## Bitvavo Pro ping timeouts

DATA-1E logs a client keepalive failure as `keepalive ping timeout` (close
code 1011). Count those lines per run. Phase A runs with zero matches are
listed so a quiet log is visible. Parquet is not opened.

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.phase_a_research_tools bv-ping
```

A ping-timeout line means the socket closed and the collector logged it. It
does not by itself mean the run directory was resumed.

## Cross-venue continuity

Compare parquet mtime windows for HL, BN, KR, and BV-Pro (override with
`--lanes HL,BN,KR`). The same campaign filter as `bn-gaps` applies;
`--all-runs` puts smokes back in. Holes are per lane. Overlap is the intersection of
those windows. Shared `run_id` rows show which lanes have that directory
name and which do not. Continues use different ids, so a missing lane on one
id is not a failed writer by itself.

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.phase_a_research_tools continuity
```

Mtime windows are file-write times, not exchange event times. A hole is a
research flag, not a claim that the venue itself was down.

## What this does not do

- no LIVE, no orders, no secrets
- no `tmux`, no `data1*_start.sh` / `data1*_stop.sh`
- no rewrite of in-progress `raw/part-*.parquet` or per-run `research.duckdb`
- no proof of 24/7 retention, lossless crash recovery, or strategy edge
