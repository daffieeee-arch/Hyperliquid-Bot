# DATA-1A retained-capture path-contract sample

Tiny **synthetic** claim/health files for the reconstructable Hyperliquid
BTC-PERP capture layout. No Parquet, DuckDB, or raw WebSocket payloads are
committed.

Path contract:

```text
<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

`sample-run` documents the JSON contract only. Runtime capture writes ZSTD
Parquet under `raw/` and a DuckDB catalog at `research.duckdb`. Those files
belong on the selected runtime store, not in git.

This sample is not a 24/7 service claim, not a live capture, and not a
trading edge.
