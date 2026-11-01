# Historical study register — checkpoint 2026-11-01

This register preserves previous outcomes while a genuinely new historical
mechanism is prepared. It does not re-score, repair or re-register a stopped
family. Archived reports are operator-retained evidence; their calculations
have not been independently recomputed by this inventory. A recorded historical
holdout remains governed by its original seal and is not fresh OOS for a new
study merely because its strategy returns were not printed.

| Study | Recorded disposition | Preserved source record |
| --- | --- | --- |
| BTC time-series momentum | `interesting_but_fragile`; no configuration passes the preregistered adjusted test; stopped | [Committed result](../experiments/exp_tsmom_btc.results.md) |
| BTC contrarian funding | `no_edge`; no selected configuration; stopped | [Committed result](../experiments/exp_funding_btc.results.md) |
| WP1 aggressive USD-M flow continuation | 0/12 admitted; small recorded reversal cost-killed; no admitted edge | Warehouse `research/results/hist-first-20261005/REPORT_FOR_COS.md` |
| WP2 Spot-versus-USD-M flow divergence | `noise`; 0/8 admitted, including insufficient configurations | Warehouse `research/results/hist-first-20261006/wp2/REPORT_WP2_FOR_COS.md` |
| WP3 Kraken thin-liquidity move reversal | `noise`; 0/6 admitted | Warehouse `research/results/hist-first-20261006/wp3/REPORT_WP3_FOR_COS.md` |
| WP4 funding-settlement pressure / crowded-carry reversal | `noise` / `noise`; 0/8 admitted | Warehouse `research/results/hist-first-20261006/wp4/REPORT_WP4_FOR_COS.md` |
| H-HL-1 funding spread | `no_edge`; no selected configuration | Warehouse `research/results/hist-first-20261006-hl/REPORT_D_FOR_COS.md` |
| H-HL-2 own-venue carry | `deferred` for missing historical mid-price inputs | Same warehouse report; no replacement price or outcome is invented |
| H-XS-1 funding/carry and H-XS-2 momentum | `noise` / `noise`; no selected configuration | Warehouse `research/results/hist-first-20261006-xs/REPORT_D_FOR_COS.md` |
| S4 multi-asset study | Existing draft remains blocked on its frozen funding-source contract; no new panel/scoring permission | [Operator universe](operator-universe.md), issues 150/190 and draft 146 |
| Prospective BTC Spot-flow / HL continuation | Implemented, fixed Nov2–5 window; not scored at this checkpoint | [Frozen protocol](../experiments/exp_btc_spot_flow.md) |
| BTC month-turn calendar proxy | `historical_no_edge`; 44 validation pairs, negative net mean at all cost/funding stresses; historical test sealed; fixed family stopped | [Independently accepted result](../experiments/exp_btc_month_turn_proxy.results.md), [protocol](../experiments/exp_btc_month_turn_proxy.md) |

Warehouse paths are relative to the verified operator archive root, not executable
SQL or a machine path to be copied into code. Existing reports include historical
cost/venue/model limitations. Their names do not certify Hyperliquid performance.
The operator's no-ICP selection and every original source/spec/holdout identity
remain unchanged. New calendar research uses Binance-only proxy economics and
known calendar decisions; no old signal threshold, lookback or horizon is retuned.
