# Broader BTC OI recovery: no cost-robust winner

Actual result, 2026-11-13; delivery [#261](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/261).
The [registered experiment](../experiments/exp_oi_recovery_broader_20261113.md)
ran all four original OI-recovery hypotheses over 974 calendar days. One has
a positive aggregate base return, but it loses in 2025 and at doubled costs.
The other three have negative aggregate returns under both cost cases.
**None establishes a robust profitable strategy.** No selector, finalist or
trading admission is introduced by this report.

This is a new, explicitly registered broader-source continuation. The original
failed source attempt and the January study's insufficient evidence remain
preserved. The [slow regime candidate](cost-aware-regime-result.md), its
[recent diagnostic](locked-regime-recent-result.md), and the negative
[micro price-response diagnostic](micro-response-cost-result.md) retain their
separate results and limitations.

## Frozen hypothesis and source coverage

Universe: selected-survivor Binance USD-M BTCUSDT, January 1, 2024 through
September 1, 2026 UTC, with the latter boundary excluded from decisions.
Development is 2024; 2025 and January–August 2026 are separate diagnostics.
All are already-exposed history. This study does not test the November decline
or constitute fresh OOS/native Hyperliquid evidence.

At completed hour T, the unchanged four policies require a 0.5%/1% price-hour
shock and at least 1% contraction in base-BTC open interest, then fade the
price move. OI uses exactly T-5m against T-65m, with UTC and feature arrival lag
explicitly assumed. Entry is T+1h, holding 4/12h, one episode at a time, fixed
quantity and 0.5 initial marked-equity notional. No stop, target, averaging,
leverage fit or post-result direction change is applied. Programme usage stays
at 32/40 unique configurations; this study adds zero new alpha configurations.

The new source contract indexes actual native timestamps independently of
physical CSV order. It preserves the complete 280,512-slot mask, original
decimal text and physical row identities: 130 missing slots, 330 numeric-zero
unavailable observations, 107 complete permuted days and three incomplete days.
Duplicates, off-grid/wrong-day timestamps, negative/nonfinite quantities and
changed bytes still fail admission. The old strict loader and archives are
unchanged. No missing/zero OI becomes a deleveraging signal or forward fill;
the first two decision hours remain unavailable without pre-2024 OI.

Qualified price segments exclude the same two known missing-mark days,
August 12, 2024 and June 29, 2026. Decision-to-exit crossings of source gaps
and registered year boundaries are excluded prospectively from every strategy
and matched control. An exit exactly at a boundary is allowed. Cash carries
across gaps and years; there is no account reset. This conditional historical
gap policy is not a claim that live trading anticipates an unplanned outage.

Execution uses native closed **trade-hour** prices, preserving the original OI
definition rather than silently adopting regime's minute-trade references.
Marks, exact native Binance funding settlements and held-minute risk extremes
remain separate. Funding is charged on held units before same-time execution
and the paid terminal close. Each cost/line starts with 10,000 USDT. Base
fee/friction are 5/2 bps per side, stress 10/4; base/stress capital carries
independently. These are execution proxies, not observed Hyperliquid fills.

## Every strategy and both cost cases

Returns and drawdowns below are percentages of marked **account equity** over
the full historical period, not annual returns or position-margin percentages.
All 90 registered segment/cost cases complete with zero calculation failures;
all four strategy lines pass the additional counterfactual collateral screen.

| Price-hour shock / hold | Trades | Net base % | Net stress % | Max sampled DD base % | Max sampled DD stress % |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0.5% / 4h | 441 | -39.5436 | -55.6234 | 42.6337 | 56.5248 |
| 0.5% / 12h | 369 | -13.0518 | -32.8749 | 23.4407 | 36.5869 |
| 1% / 4h | 227 | -16.5692 | -28.8429 | 24.7744 | 33.8176 |
| 1% / 12h | 200 | +11.3783 | -3.1890 | 10.4912 | 12.0387 |

The unique positive aggregate base line, 1%/12h, has the following chronological
returns; it was not selected through a new development ranking:

| Group | Base account % | Stress account % |
| --- | ---: | ---: |
| 2024 development | +3.9833 | -3.3240 |
| 2025 diagnostic | -2.2127 | -5.9141 |
| Jan–Aug 2026 diagnostic | +9.5355 | +6.4343 |

Its base gross price PnL is +2,606.8406 USDT; fees 1,031.0364 and adverse
friction 412.4147, plus signed funding -25.5572, leave net +1,137.8324.
Stress gross price PnL is +2,395.5680; fees 1,921.7008, friction 768.6807 and
signed funding -24.0850 leave net -318.8986. Gross amounts differ because
independently carried base/stress capital changes subsequent quantities.
Base long episodes contribute +1,757.5465 and shorts -619.7142; at stress
these are +725.7008 and -1,044.5994. Attribution is descriptive, not a new
long-only model or permission to discard unfavorable episodes.

## Controls, uncertainty and requested margin goals

The [complete account CSV](oi-recovery-broader-accounts.csv) includes all fifteen
accounts under both costs: four strategies, cash, persistent long/short and
matched long/short on each strategy's own clocks. It retains all chronological
returns, costs, risk flags, sampled drawdown and descriptive uncertainty.
The [paired-comparison CSV](oi-recovery-broader-comparisons.csv) publishes all
32 registered cost/control/block descriptions. No positive control is selected
as another successful strategy.

Cash earns zero. Persistent long earns +19.8356% base / +19.5722% stress with
three segment episodes. Persistent short's -20.5940% / -20.7972% is
**collateral-invalid** and cannot certify comparative alpha. Matched controls
pass their collateral screens. On the 1%/12h clocks, always-long earns
+12.5596% / -2.1604%, exceeding the strategy under both costs; matched short
earns -34.3146% / -42.9280%. Persistent and matched controls answer different
questions and have different exposure calendars.

Fixed 2,000-resample, seed-20261113, 7-day/30-day circular blocks describe
dependent daily returns. For 1%/12h, both 2024 and 2025 intervals span zero
under both costs. The 2026 base 30-day sensitivity has a positive lower bound,
but its 7-day interval and both stressed intervals span zero. This isolated
exposed-period sensitivity does not establish post-search significance or
overcome negative aggregate stress and 2025 results. All descriptions remain
visible in the tables and immutable bundle. Against matched long, both paired
intervals span zero. Against the weaker matched short, both paired intervals
are positive under both costs: the base 30-day interval is approximately
+2.12% to +36.91% for the annualized arithmetic daily-return difference.
That comparison is retained; it does not establish superiority to matched
long, positive stressed aggregate returns or post-search significance.

The research margin denominator is immutable absolute quantity times entry
**mark divided by two**, not fill notional or account equity. The inherited
1.25% maintenance proxy and every held native minute extreme provide an
additional 2x isolated-collateral diagnostic, not actual venue liquidation
proof. Entry exposure is 0.5; fixed held quantities can drift: the 1%/12h
sampled gross-exposure peak is approximately 0.5284 base / 0.5286 stress.
Neither the sizing nor this report enforces a continuous 0.5 exposure cap.

For the user's earlier margin goals, the following are secondary descriptive
counts from completed episodes, including execution costs and funding. They
do not select a target or alter the registered fixed holding exit:

| Policy | Episodes | Base >=1% / >=2% net margin | Stress >=1% / >=2% net margin | >=20% base / stress |
| --- | ---: | ---: | ---: | ---: |
| 0.5% / 4h | 441 | 94 / 49 | 81 / 42 | 0 / 0 |
| 0.5% / 12h | 369 | 133 / 90 | 124 / 78 | 1 / 1 |
| 1% / 4h | 227 | 58 / 35 | 54 / 32 | 0 / 0 |
| 1% / 12h | 200 | 82 / 58 | 76 / 51 | 1 / 1 |

The four lines average roughly 0.21–0.45 completed trades per calendar day,
not hundreds per day. The 1%/12h line has 108 winning / 92 losing episodes at
base and 99 / 101 at stress; it is exposed on 299 days and flat on 675.
Observed target counts are not guaranteed future wins. The 2x collateral basis
also differs from the old unqualified 10x reference; do not compare them as
equivalent margin-return measurements.
Both 12-hour variants' 20% hits are the same historical August 4 20:00 to
August 5 08:00 UTC episode in 2024, not two independent confirmations.

## Reproducibility and delivery boundary

Actual source `ba4cabcdb8d4ffddff27a8dd4fe1e9c0c70a7eec` was merged after
separate nonauthor code/security and quantitative reviews and current ready CI
with 4,815 tests. The separate [source merge gate](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/262#issuecomment-6866379876)
verified the exact head, base and diff. The unconfirmed local full-suite
session was explicitly excluded from that evidence; the complete ready-CI
suite actually ran. Source-only qualification and independent archive/mask
reconstruction preceded financial scoring.

The actual offline VM run took 80.3149 seconds and observed 1,168,796 KiB peak
RSS, two assigned CPUs, nice 10 and a 6 GiB address-space limit. All 198 bundle
files total 56,564,839 bytes. All source and input identities passed before/after
checks. Original archives and both running collectors were unchanged.

- Protocol SHA-256: `7c9adccf98088b0ca5a12de39d1c71952450fc3837584ece37de33322476b6a5`.
- Price input SHA-256: `ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`.
- OI source inventory SHA-256: `fabf9fad203c0190d766f082ec9ab026b651f22544870ccd0f396cb1055c093a`.
- Full native OI availability/physical-row mask SHA-256: `920e1bc1ac89bea41653c1745e18db5df261ddc4db1fa7c09b3bfac595792844`.
- Actual result SHA-256: `2c84d36258bb0d0d0c0683aaf10066d5173bd5cc49a8de9e9d433f98110b6cc0`.
- Exact 198-file bundle manifest SHA-256: `ebac70d012597ce124658f506f432e0290d5d167060ec6ea7264020e44da32a4`.

Independent nonauthor reconstruction passes all 90 cases, 789,180 ledger
points, 14,868 fills, 7,434 case-level episodes, 9,018,000 held-minute risk
checks and 1,087,217 risk-event records, including the invalid persistent-short
counterfactual. Repeated costs/controls are not independent market observations.
The auditor independently rebuilds native OI schedules, H1 execution, cash,
funding, actual initial-margin ROI, every daily series and paired block
statistics without importing production financial/model helpers as truth.
It uses explicitly pinned and attributed arithmetic/risk/statistics code from
the earlier independent regime auditor, with its own OI/H1 adaptation.

All 1,948 OI ZIP/sidecar files, 268 price/funding source files and 198 bundle
files pass pre/post identities. There are zero discrepancies outside the
recorded tolerances; timestamp/count/key/schedule checks are exact. The audit
took 213.8220 seconds and 1,073,684 KiB peak RSS under two CPUs, nice 10 and
6 GiB AS, with at most two months of minute marks cached. Its 103,930,632
validation checks include repeated native checks on cache reads and should
not be interpreted as that many independent observations. No audit rerun or
actual-result correction was needed.

Independent exact-source/result/manifest-bound acceptance SHA-256:
`a35d4002735f17009034d25b0c058333825ac06d80b7f1a5ff74100c39ea3852`.

No fresh OOS, native Hyperliquid execution/funding, PAPER strategy admission,
TESTNET, SHADOW, LIVE or capital action follows from this experiment.
