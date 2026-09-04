# DATA-1A live retained-capture evidence

Payload-free sample from a real public Hyperliquid BTC-PERP reconstructable
run. Raw `part-*.parquet` and `research.duckdb` stay outside git.

| Field | Value |
| --- | --- |
| `artifact-root` | `/workspace/var/reconstructable` |
| `run_id` | `20260904t001700z-live-retained` |
| requested duration | `14400` seconds (4 hours) |
| start UTC | `2026-09-04T00:17:03Z` |

Path contract:

```text
<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

`sample-rows.json` keeps channel, clocks, SHA-256, and payload length only.
`run-summary.json` is a snapshot of **published** parts (the writer may still be
running). `capture-health.json` is added only after COMPLETED / OPERATOR_STOP /
FAILED. See `docs/runbooks/data1a-vps-retained-capture.md`.

This is not 24/7 service evidence, not a trading edge, and not LIVE trading.
