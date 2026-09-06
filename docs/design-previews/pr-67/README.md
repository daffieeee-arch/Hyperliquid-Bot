# PR #67 — cockpit workspace redesign previews

PAPER cockpit screenshots for review. Not product claims. Not LIVE. Collectors
were not started or stopped for these shots.

Captured at 1600×1000 (desktop) and 390×844 (iPhone) against
`TRADING_MODE=PAPER` with the in-repo fixtures.

The Research shots additionally point `COCKPIT_RESEARCH_OUT` at a scratch
`research-out/` tree holding two `panel-summary.json` files — one for the
bound `sample-run` (`not_enough_data`) and one for an unrelated run
(`panel_ready`). That is what makes the verdict-colour and run-attribution
fixes visible: the cockpit selects the summary belonging to the bound run and
refuses to render its failed gate as a positive result.

| File | Screen |
|---|---|
| `desktop_overview.png` | Overview: stat tiles, attention list, market context, PAPER digest |
| `desktop_markets.png` | Markets: interval control, candles, bound instrument table |
| `desktop_research.png` | Research: attributed WP-Q1 summary, registry, capture health |
| `desktop_paper.png` | PAPER: What / Why / Results, intent tape, separated gate catalog |
| `desktop_system.png` | System & risk: run binding, capture states, gate catalog |
| `mobile_overview.png` | Overview on iPhone |
| `mobile_paper.png` | PAPER on iPhone |
| `mobile_navigation.png` | Mobile navigation drawer |
