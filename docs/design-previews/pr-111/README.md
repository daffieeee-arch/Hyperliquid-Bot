# PR #111 Design review shots

PAPER cockpit hypothesis results. Not a live research claim. Not LIVE.

The shots use `tests/fixtures/hypothesis_results` only. WP1 is a complete synthetic row. WP2 omits net bps, H1, holdout, OOS, `run_id`, `product`, and `path_contract`, so those cells stay UNAVAILABLE. Captures were not started or stopped.

| File | What to look at |
|---|---|
| `desktop-list.png` | Desktop table. Net before gross. Short Amsterdam dates. WP2 is the partial row. |
| `desktop-detail-partial.png` | Desktop detail for WP2. `run_id`, `product`, and `path_contract` are present and UNAVAILABLE. Full CEST/CET timestamps stay in the detail. Promotion gate is the paper banner, not the stale clock. |
| `phone-list.png` | Phone width (~390px). The nowrap table scrolls horizontally; the work-package column stays pinned and readable; the promotion badge is in view. |
| `phone-detail-partial.png` | Phone width. WP2 detail, including UNAVAILABLE identity fields and the missing report. |
