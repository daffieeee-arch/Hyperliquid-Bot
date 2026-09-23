# Research scripts (repo stub)

This directory is a **pointer only**. The offline historical research warehouse
lives on the Netcup VPS (`chupa`), outside this git tree:

```text
~/Hyperliquid Project/hist-archives/
```

That tree holds Binance Vision BTCUSDT ZIPs, Kraken OHLCVT XBTUSD CSVs,
converted Parquet, and `research.duckdb`. It is a sibling of `data-capture/` —
never merge hist into live `run_id` directories, and never commit Parquet / ZIP /
CSV / DuckDB binaries here.

Operator runbook:
[docs/runbooks/hist-archives-research-warehouse.md](../../docs/runbooks/hist-archives-research-warehouse.md).

Open the catalog on the VPS:

```bash
bash ~/Hyperliquid\ Project/hist-archives/scripts/open_research.sh
```

Read-only Phase A inventory, DuckDB glob smoke, Binance gap notes, Bitvavo Pro
ping-timeout counts, and cross-venue continuity live in the repo (they do not
start or stop captures):

```bash
cd ~/Hyperliquid\ Project/Hyperliquid-Bot
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.phase_a_research_tools inventory
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.phase_a_research_tools duckdb-smoke
```

How-to: [docs/runbooks/phase-a-research-inventory.md](../../docs/runbooks/phase-a-research-inventory.md).

PAPER / free-data-first. No LIVE, no secrets, no capture restarts from this path.
