# PR #67 — cockpit workspace redesign previews

PAPER cockpit screenshots for review. Not product claims. Not LIVE. Collectors
were not started or stopped for these shots.

Captured at 1600×1000 (desktop) and 390×844 (iPhone) against
`TRADING_MODE=PAPER`.

## What the shots are bound to

The review VM has no access to the TerraPC/WSL captures, so the desktop and
mobile shots point `ARTIFACT_ROOT` at a **simulated live retain** produced by
`tests/fixtures/market_tape/generate_fixtures.py --out /tmp/sim-retain
--base-utc now` (same DuckDB/ZSTD writer contract as the collectors, fresh
mtimes, no `capture-health.json`). That is why every venue reads `LIVE CAPTURE`
/ `RUNNING` and `bound via auto-detect`: the cockpit found the run directories
itself; no run id was configured. It is **not** a live validation against the
real collectors — see the PR description.

The PAPER desk still reads the committed `default-fixture` soak, so it is
labelled `DEMO FIXTURE` + `HISTORICAL SNAPSHOT` on purpose.

`COCKPIT_RESEARCH_OUT` holds two `panel-summary.json` files that name the
simulated runs per venue: one `not_enough_data` summary for the exact bound
(HL, Binance) run pair, and one `panel_ready` summary for the same HL run but
an older Binance run. The cockpit picks the first and marks the second
`MISMATCHED`.

| File | Screen |
|---|---|
| `desktop_overview.png` | Overview: capture, attention, research verdict, PAPER digest, bound runs, stored data strip |
| `desktop_overview_research_failed.png` | Overview while `/api/research-p0` returns 500 (browser-simulated): banner, `ERROR` attention item, previous values kept with their last successful read time |
| `desktop_markets.png` | Markets: public mid/candles (labelled public, not research truth), bound instrument table |
| `desktop_markets_stored.png` | Markets: stored market data per venue — run, parts, bytes, newest part, last data, publication lag, per-instrument last trade / bid-ask / spread, spot vs perp vs quote kept apart |
| `desktop_research.png` | Research: summary attributed per venue run → `MATCHED`, verdict `not_enough_data` shown neutral, never green |
| `desktop_research_mismatched.png` | Research when only the summary with the stale Binance run exists → `MISMATCHED`, `panel_ready` shown amber with the reason |
| `desktop_paper.png` | PAPER: `DEMO FIXTURE` + `HISTORICAL SNAPSHOT`, intent tape without invented rejects, gate catalog separate |
| `desktop_system.png` | System & risk: run binding, published data per venue, DATA-1A facts |
| `mobile_overview.png` | Overview on iPhone |
| `mobile_markets_stored.png` | Stored market data on iPhone (table scrolls inside the card) |
| `mobile_paper.png` | PAPER on iPhone |
| `mobile_navigation.png` | Mobile navigation drawer |
