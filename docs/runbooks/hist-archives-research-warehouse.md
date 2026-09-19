# Runbook — offline historical research warehouse (`hist-archives`)

Status: **operator-managed on Netcup Ubuntu 24.04 LTS VPS (`chupa`)**.
PAPER / free-data-first. Captures stay untouched: do **not** stop or restart
`hl-capture`, `bn-capture`, `bv-capture`, `kr-capture`, or `bv-std-capture` for
this warehouse. Do **not** write into live `data-capture/` run directories.

Canonical path (sibling of live retain, **not** under `data-capture/`):

```text
~/Hyperliquid Project/hist-archives/
```

Repo overview: [DATA.md — Offline historical research warehouse](../DATA.md#offline-historical-research-warehouse-vps).

## What this is (and is not)

| This warehouse | Live Phase A retain |
| --- | --- |
| Offline bulk history for research SQL | Continuous WS collectors + Parquet parts |
| Binance Vision ZIPs + Kraken OHLCVT CSVs → Parquet | DATA-1A/1B/1D/1E/1F under `data-capture/` |
| DuckDB `hist_*` views | Per-`run_id` claims + mid-run parts |
| Optional **read-only** globs of live parts | Authoritative soak / Phase A evidence |

It does **not** replace live WebSocket retain. Bitvavo book/tape and Kraken L3
remain live-only products — Vision aggTrades/klines/funding and Kraken OHLCVT
candles do not reconstruct those feeds.

## Official sources

- **Binance Vision (public market data ZIPs):**
  [https://data.binance.vision/](https://data.binance.vision/) —
  organization and schemas in
  [binance/binance-public-data](https://github.com/binance/binance-public-data).
  Spot timestamps from 2025-01-01 are **microseconds**; USD-M futures remain
  **milliseconds** (see upstream notes).
- **Kraken downloadable OHLCVT:**
  [Downloadable historical OHLCVT](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data)
  — CSV rows `timestamp,open,high,low,close,volume,trades` (no header); intervals
  with no trades are omitted (not zero-filled).

Do not invent alternate bulk URLs. Prefer checksum verification when upstream
publishes `.CHECKSUM` / SHA256 manifests.

## Layout on `chupa`

```text
~/Hyperliquid Project/hist-archives/
  README.md
  RESEARCH.md                 # local operator notes (may be Dutch/English mix)
  catalog.sql                 # DuckDB view definitions (__HIST__ / __DC__ placeholders)
  research.duckdb             # catalog DB (rebuilt by scripts; not in git)
  binance-vision/             # source ZIPs + CHECKSUM files (spot + futures-um)
  kraken-ohlcvt/              # full / quarterly ZIPs + extracted XBTUSD CSVs
  parquet/
    binance/{spot,um}/{aggtrades,klines_1m,klines_1h,funding}/
    kraken/xbtusd_{1m,5m,15m,1h,4h,12h,1d}.parquet
  scripts/
    convert_all.py            # ZIP/CSV → Parquet ETL (lives on VPS disk)
    build_catalog.sh
    open_research.sh          # open DuckDB catalog in a Python REPL
    run_etl.sh
    smoke_queries.py
  logs/                       # download / ETL logs
```

Parquet, ZIP, CSV, and `research.duckdb` stay **off git**. Only documentation
and tiny stubs belong in the Hyperliquid-Bot repo.

## How to open the catalog

On the VPS (Tailscale / local shell on `chupa`):

```bash
bash ~/Hyperliquid\ Project/hist-archives/scripts/open_research.sh
```

That rebuilds views from `catalog.sql` into `research.duckdb`, then drops you
into an interactive Python session with `con` bound (DuckDB via the bot's
`uv` environment). Example:

```python
con.execute("SHOW TABLES").fetchall()
con.execute("SELECT count(*), min(ts), max(ts) FROM hist_kr_xbtusd_1d").fetchall()
```

## Example SQL questions

```sql
SELECT count(*), min(ts), max(ts) FROM hist_bn_spot_aggtrades;
SELECT count(*), min(ts), max(ts) FROM hist_bn_um_klines_1h;
SELECT count(*), min(ts), max(ts) FROM hist_kr_xbtusd_1d;

-- Daily close overlap BN UM vs KR — USDT vs USD, not 1:1 FX-adjusted
WITH bn AS (
  SELECT CAST(open_time AS DATE) AS d, arg_max(close, open_time) AS bn_close
  FROM hist_bn_um_klines_1h GROUP BY 1
),
kr AS (
  SELECT CAST(ts AS DATE) AS d, close AS kr_close FROM hist_kr_xbtusd_1d
)
SELECT bn.d, bn.bn_close, kr.kr_close, bn.bn_close - kr.kr_close AS raw_diff
FROM bn JOIN kr USING (d)
ORDER BY bn.d DESC
LIMIT 10;
```

`catalog.sql` may also expose **read-only** `live_*_parts` views that glob
current Phase A retain paths under `data-capture/`. Those globs are for
research joins only — never write, rotate, or rename those parts from hist ETL.

## USDT vs USD caveat

Binance Vision BTCUSDT (spot and USD-M) is **USDT-quoted**. Kraken OHLCVT
**XBTUSD** is **USD-quoted**. Raw close differences are **not** a clean basis
or FX-neutral lead/lag signal. Document any conversion (or the deliberate
absence of one) in the experiment note. Do not treat `bn_close - kr_close` as
executable edge without quote-currency discipline
([DATA.md — Quote currencies](../DATA.md#quote-currencies-and-conversion-data)).

## Never merge hist into live `run_id`s

Hard rules:

1. Keep hist under `~/Hyperliquid Project/hist-archives/` only.
2. Do **not** copy hist Parquet into `data-capture/data-1*/**/raw/`.
3. Do **not** reuse a live `run_id` as a hist batch name or vice versa.
4. Do **not** stop Phase A tmux sessions to free disk for downloads — use disk
   budget planning, or wait for an explicit CoS/operator gate after the window.
5. LIVE, wallets, API secrets, and order paths stay out of this warehouse.

## Disk notes (operator)

Approximate sizes on `chupa` (change over time; re-check with `du`):

| Tree | Role | Order of magnitude |
| --- | --- | --- |
| `binance-vision/` | Source ZIPs | ~10+ GB |
| `kraken-ohlcvt/` | Full/quarterly ZIP + extract | ~10 GB class |
| `parquet/` | Research tables | several GB |
| `research.duckdb` | View catalog | hundreds of KB–MB |

Vision monthly aggTrades dominate growth. Prefer checksummed downloads, delete
duplicate split parts after a verified join, and keep ETL logs under
`hist-archives/logs/` rather than home clutter. Watch the same VPS volume that
hosts Phase A retain — hist and live share the disk but must stay in separate
trees.

## Host access

- Host: Netcup VPS hostname / Tailscale node **`chupa`**.
- Cockpit Tailscale Serve remains orthogonal (see [FRONTEND.md](../FRONTEND.md));
  this warehouse is shell + DuckDB, not a cockpit route.
- Cloud Agents and overnight jobs: **documentation and read-only SQL only**
  unless CoS explicitly assigns an ETL window. Never attach to capture tmux.

## Related docs

- [DATA.md](../DATA.md) — free-data policy and warehouse summary
- [RESEARCH_METHOD.md](../RESEARCH_METHOD.md) — point-in-time / partition discipline
- [phase-a-72h-joint-retained-capture.md](phase-a-72h-joint-retained-capture.md) — live retain (do not conflate)
- [scripts/research/README.md](../../scripts/research/README.md) — repo stub pointer
