# COURSE-1 live-public PAPER soak evidence

Real Cockpit JSON from one credentialless Hyperliquid public BTC-PERP PAPER
soak. PnL is the assumed D01 overlay, not venue PnL. Funding remains `0`.

| Field | Value |
| --- | --- |
| `artifact-root` | `/workspace/var/reconstructable` |
| `run_id` | `20260904t001800z-live-paper` |
| `artifact-dir` | `/workspace/var/reconstructable/course1/live-public-paper/20260904t001800z-live-paper` |
| requested seconds | `180` |
| start UTC | `2026-09-04T00:20:12Z` |
| status | `COMPLETED_FLAT` |
| verify | `VERIFIED` |

The smoke lifecycle entered and flattened on public ticks before the 180s bound
elapsed. That is a documented soak outcome, not a duration-contract change.

Path contract:

```text
<artifact-root>/course1/live-public-paper/<run_id>/
  run-claim.json
  paper-position.json
  paper-pnl.json
  orders.json
  fills.json
  capture-health.json
```

See `docs/runbooks/course1-live-public-paper-soak.md`. This is not D22-B, not
LIVE trading, and not 24/7 PAPER.
