# DATA-1A live retained-capture evidence

Payload-free sample from a real public Hyperliquid BTC-PERP reconstructable
run. Raw `part-*.parquet` and `research.duckdb` stay outside git.

| Field | Value |
| --- | --- |
| `artifact-root` | `/workspace/var/reconstructable` |
| `run_id` | `20260904t001700z-live-retained` |
| requested duration | `14400` seconds (4 hours) |
| start UTC | `2026-09-04T00:17:03Z` |
| end UTC | `2026-09-04T04:17:58Z` |
| health status | `OPERATOR_STOP` |
| published parts | `41` |
| parquet bytes | `1822908` |
| events | `19404` |
| gaps / reconnects | `1` / `1` |

The requested window was 14400s. The cloud-agent VM froze after about 49
minutes of process time (last substantial part `01:06Z`, marker-only parts
through `01:20Z`). On wake the collector was SIGTERM'd rather than left on a
stale socket for the remaining monotonic duration. Published parts remain
readable. This is still a retained run (`duration_seconds` 14400, live capture
well above 600s), not a 24/7 service.

Path contract:

```text
<artifact-root>/data-1a/hyperliquid/BTC-PERP/<run_id>/
  capture-claim.json
  capture-health.json
  raw/part-*.parquet
  research.duckdb
```

`sample-rows.json` keeps channel, clocks, SHA-256, and payload length only.
See `docs/runbooks/data1a-vps-retained-capture.md` and
`docs/runbooks/data1a-wsl-pc-retained-capture.md`. Cloud Agents are unsuitable
for a multi-day retain.

This is not 24/7 service evidence, not a trading edge, and not LIVE trading.
