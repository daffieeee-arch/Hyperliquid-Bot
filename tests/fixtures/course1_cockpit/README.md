# COURSE-1 Cockpit path-contract sample

These JSON files are a **synthetic** create-only sample of the reconstructable
COURSE-1 PAPER artifacts Cockpit should later read. They were not captured from
a live soak and must not be described as production evidence.

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

`sample-run` uses empty sandbox orders/fills and assumed overlay PnL `0`. That
is an honest empty PAPER state, not a fabricated trading result. Funding remains
`0`. D22-B is not implemented. This is not a 24/7 service claim.

A separate live-public soak fixture lives in `live-public-soak/`. That run wrote
real create-only Cockpit files from public ticks and the assumed PAPER overlay.
The first cockpit screen reads those files by default
(`run_id` `20260904t001800z-live-paper`).
