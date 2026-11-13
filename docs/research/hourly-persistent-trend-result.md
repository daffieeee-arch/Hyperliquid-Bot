# Hourly persistent BTC trend: faster decisions do not survive costs

Actual result, 2026-11-13; delivery [#264](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/264),
programme [#237](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/237).
All four [registered hourly policies](../experiments/exp_hourly_persistent_trend_20261113.md)
completed, and an independent full native/accounting reconstruction passes.
Three lose under both cost cases; 72h-confirmed earns +6.5332% at base but
-1.6552% at stress. All four lose in 2025. **No hourly candidate is admitted.**
The predefined stop applies: stop this clock/horizon family rather than tune
its thresholds or automatically spend the remaining four configurations.

The old [daily h720 candidate](cost-aware-regime-result.md) reproduces its
financial results before any new hourly policy is evaluated. Its positive
retrospective result and fragile 2025 stress remain separate from these failures.
The [recent locked diagnostic](locked-regime-recent-result.md),
[micro entry-response result](micro-response-cost-result.md) and
[broader OI result](oi-recovery-broader-result.md) retain their own limitations.

## Frozen question, clock and accounting

The question is whether completed 24/72-hour momentum, evaluated every hour
with persistent positions, detects cost-covering trends earlier than the slow
daily policy. Four combinations of lookback 24/72 hours and slow confirmation
off/on add four configurations: programme usage is now **36/40**. There is no
selector, new stop/target, leverage fit, forced trade quota or retuned exit.

Universe is selected-survivor Binance USD-M BTCUSDT, January 1, 2024 through
September 1, 2026 UTC, latter boundary excluded from decisions: 974 calendar
days. 2024 is development; 2025 and January–August 2026 are diagnostic groups.
All are exposed retrospective history, not fresh OOS or a test of the current
November decline. December 2023 supplies the complete warm-up.

At completed hour T, use 721 hourly closes. Entry requires absolute normalized
momentum score >=1; keep the same-side quantity while sign agrees and absolute score
>=0.5. Confirmed policies also require the price/SMA720 direction for both
entry and holding. Entry weight is min(0.5,0.10/sigma720), with annualized
720-hour RMS volatility. Units stay fixed; no same-side resizing or averaging.
This evaluates hourly without forcing a new trade each hour.

Execution uses the native completed TRADE-minute close at T+1m; reversal closes
there and opens the opposite side at T+2m, charging both legs. These are
conditional delayed-price proxies, not realized fills. Fees/adverse friction
are 4.5/2 bps **per side** at base and 9/4 at stress. Approximate roundtrip
price hurdles are 13/26 bps before funding and small notional/reference effects.
Both costs start at 10,000 USDT and carry actual capital independently.
Binance native funding uses separate exact settlement times and marks;
it is not Hyperliquid oracle-based funding.

The same two missing-mark days, August 12, 2024 and June 29, 2026, remain in
the 974-day denominator. Positions close with paid prices at the three valid
segment endpoints; the next segment starts flat with actual prior equity.
Year boundaries do not close positions or reset policy state/capital. Daily
marked returns allocate performance across years. Archive-informed source-gap
flattening cannot demonstrate anticipation of a live outage.

## All four outcomes and chronological diagnostics

Returns and sampled drawdowns below are percentages of **account equity** over
the full historical period, not annual returns or position-margin returns.
All 96 segment/cost cases complete; no chain failed. All four model lines pass
the separate counterfactual collateral screen.

| Policy | Trades | Net base % | Net stress % | Sampled DD base % | Sampled DD stress % |
| --- | ---: | ---: | ---: | ---: | ---: |
| 24h plain | 729 | -14.0030 | -30.7615 | 24.7036 | 38.4720 |
| 24h confirmed | 498 | -1.8940 | -15.5153 | 15.6381 | 24.8140 |
| 72h plain | 304 | -2.5984 | -11.1388 | 17.7624 | 22.3271 |
| 72h confirmed | 258 | +6.5332 | -1.6552 | 10.2566 | 14.6119 |

| Policy | 2024 base / stress % | 2025 base / stress % | Jan–Aug 2026 base / stress % |
| --- | ---: | ---: | ---: |
| 24h plain | -0.7748 / -7.5919 | -18.4154 / -25.3673 | +6.2315 / +0.3942 |
| 24h confirmed | +2.9658 / -1.8253 | -13.3117 / -18.6047 | +9.9112 / +5.7253 |
| 72h plain | +6.5215 / +3.3275 | -8.5758 / -11.5756 | +0.0156 / -2.7423 |
| 72h confirmed | +6.4868 / +3.9242 | -3.6841 / -6.7038 | +3.8702 / +1.4310 |

All lines carry `reject_cost` and `reject_regime_robustness`. All except
72h-confirmed also carry `reject_drawdown` for sampled DD above 15%.
Those descriptive drawdown/cost flags are distinct from `risk_invalid`, which
only denotes actual counterfactual collateral-screen events. Passing the latter
does not establish profitability or venue liquidation safety.

The only aggregate base-positive line, 72h-confirmed, has gross price PnL
+1,561.3866 USDT, fees 571.1281, friction 253.8348 and signed funding -83.1029,
leaving +653.3209 net. Stress gross is +1,502.7453, fees 1,098.1197,
friction 488.0534 and funding -82.0940, leaving -165.5219. Gross differs because
costs change subsequent carried capital and quantities. Base long/short episodes
contribute +1,244.4634 / -591.1424; stress +784.1081 / -949.6299.
This attribution is descriptive, not a newly selected long-only policy.

## Controls, uncertainty and baseline reproduction

The [complete account CSV](hourly-persistent-trend-accounts.csv) publishes all
16 accounts under both costs: four policies, cash, persistent long/short,
eight matched controls and the original daily h720 baseline. It includes each
fold's return and 7/30-day descriptive intervals, costs, collateral status,
sampled drawdown, exposure, holding, frequency and actual-margin target counts.
CSV returns, drawdowns and uncertainty bounds are fractions; money is USDT,
turnover is quote notional and holding times are hours. Empty holding statistics
for cash mean no completed episodes; numeric zero remains zero. Empty
`dispositions` for controls mean the model-only disposition function is not
applied, not strategy admission.

Persistent long earns +9.7092% base / +9.6189% stress on this hourly entry
clock. Persistent short returns -9.6907% / -9.7714% but is collateral-invalid
under both costs and cannot certify alpha. All eight matched controls are
collateral-valid. On the 72h-confirmed clocks, matched long earns
+10.1803% / +1.7122%, exceeding the model; matched short earns
-23.9424% / -29.7996%. Clock/exposure differences mean persistent controls
are not interchangeable with the previous OI study's controls.

The [complete paired CSV](hourly-persistent-trend-comparisons.csv) retains all
32 policy/control/cost/block intervals. **Every paired interval spans zero**.
The unchanged deterministic circular-block procedure uses 2,000 resamples,
seed 20261113, seven-day blocks and a 30-day sensitivity, with shared indices
for paired daily-return differences. Bounds describe annualized arithmetic
daily-return differences, not cumulative compounded profit. Dependent exposed
history and the prior search prevent a post-search significance claim.

The old daily `regime-h720-plain` baseline reproduces all six case financial
results and both aggregate results **before new hourly schedules are built**:
+16.4718% base / +15.7015% stress, 20 completed episodes. Added hourly,
execution and funding samples make its newly sampled DD 10.7804% / 10.9494%,
versus the original sparser 10.7451% / 10.9142%. This is a denser observation
grid, not a change to baseline trades, fees, funding or final equity. Neither
grid is continuous minute/tick account drawdown. Held native minute extremes
remain a separate collateral diagnostic. The slow candidate's uncertainty,
fragile 2025 stress and native/fresh-OOS gaps still block admission.

## Frequency and the requested net margin goals

Margin is immutable abs(quantity) times **entry mark divided by two**. Fees,
friction and funding are included in episode net margin ROI; neither account
equity nor an artificial 10x denominator is substituted. The 2x isolated-margin,
1.25% maintenance proxy with every held native minute extreme is conditional:
historical tiers, intraminute order and actual liquidation remain unqualified.

| Policy | Mean / max completed trades per UTC day | Median / max hold hours | Base >=1% / >=2% net margin | Stress >=1% / >=2% net margin | >=20% base / stress |
| --- | ---: | ---: | ---: | ---: | ---: |
| 24h plain | 0.7485 / 3 | 10 / 72 | 153 / 108 | 135 / 98 | 3 / 3 |
| 24h confirmed | 0.5113 / 4 | 9 / 72 | 104 / 74 | 92 / 66 | 3 / 3 |
| 72h plain | 0.3121 / 3 | 23 / 165 | 69 / 58 | 66 / 54 | 5 / 5 |
| 72h confirmed | 0.2649 / 4 | 19 / 131 | 56 / 45 | 51 / 43 | 5 / 5 |

Means use all 974 days, not only active days. Completion belongs to
`(UTC_day_start, UTC_day_end]`, including paid terminal closes. These are
completed-trade counts, not hourly evaluations or independent target bets.
The same market episode can appear in several policies; do not sum target
hits as independent confirmations. Target counts are descriptions of the
fixed exits; they do not tune targets or guarantee future wins.

72h-confirmed has 73 winning / 185 losing episodes at base and 69 / 189 at
stress; median/p95/max holding is 19/103/131 hours. It has exposure on 547 days
and is flat on 427. The inherited `no_trade_calendar_days` field means flat
days, distinct from days without a completed trade. Fixed quantities can drift
in account exposure. These results meet neither one >=20%-margin win each day
nor hundreds of >=1–2%-margin net wins per day.

## Reproducibility and remaining work

Actual source `d850ae21652e4bf9bdaeb701ee4b7627fd0486fe` was separately
code/security- and quant-reviewed, with the partial-account-failure rollback
finding fixed before any financial run. The current ready CI ran 4,848 tests.
The [separate source merge gate](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/265#issuecomment-6867053839)
verified reviewed head/base/diff and actual checks. A source-only native clock
census qualified 23,328 decisions, 46,656 required +1m/+2m references and
71,018 retained valuation/execution/funding frames before labels.

The actual offline VM run took 108.7199 seconds, observed 853,952 KiB peak RSS,
two assigned CPUs, nice 10 and 6 GiB address space. All 243 bundle files total
110,999,632 bytes. Input/source identities pass before and after; original
archives and both running collectors were unchanged.

- Protocol SHA-256: `04e69f96b3e107812d02b7addb66b67ae4ce36a5869d7dfc25af4760ad532ea3`.
- Input SHA-256: `ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`.
- Baseline index SHA-256: `2b1653f765a4d5f480bcc963e93f7c410eede584d96e95298b8673402e6e8232`.
- Original baseline summary SHA-256: `7a395e2ced136ddb6dee01a6622970100386ee418f1a23dc69b0ffc609803a2f`.
- Native retained-clock SHA-256: `a78ca54e527cba6009ad876fd32819307902611c8981b599bb141203efda36d8`.
- Actual result SHA-256: `9de037547b579f2ee5c56d8223df7f15bbc20e1a4830646ad03a2337187d169d`.
- Exact 243-file bundle manifest SHA-256: `d9455537cdfb994ddf0a111a92de46cd595bd944842e15acf69c01be3bb9d91b`.

Independent nonauthor reconstruction passes all 96 cases, 2,272,576 ledger
points, 21,572 fills, 10,786 case-level episodes, 19,440,308 held-minute risk
checks and 1,146,069 risk-event records. Repeated costs/controls are not
independent market samples. Native inputs, new feature schedules, executions,
cash/funding, collateral, every daily series and paired statistics are rebuilt
without importing production financial/model helpers as truth. The auditor
reuses explicitly pinned and attributed independent regime arithmetic/risk/stat
code with its own new hourly-clock adaptation; 17 synthetic cases precede the
actual audit. The six old cases and whole-account baseline also reproduce.

All 268 native price/funding source files, seven old baseline files and 243
bundle files pass pre/post identities. The audit has zero discrepancies
outside its recorded floating tolerances; clocks, counts and identities are
exact. It took 343.3315 seconds and 594,960 KiB peak RSS, two CPUs, nice 10,
6 GiB AS and at most two cached mark months. No result correction or audit
restart was needed. Its 130,352,504 assertions include repeated cache checks;
that is not an effective sample size.

Independent exact-source/result/manifest-bound acceptance SHA-256:
`85ad0425626f835370372d7bf71b0aec31851b819722bb9fccdc95b2a3f6c5d2`.

Next work qualifies causal closed-hour input receipts and native Hyperliquid
cost/funding/precision for the existing slow candidate, and considers a
distinct economic mechanism for the rejected fast track. Binance archives
cannot prove receipt latency, revision vintage, native Hyperliquid depth,
partial fills or oracle funding. No fresh validation period is silently
reserved. This report changes no collector, risk limit or trading mode and
does not admit PAPER, TESTNET, SHADOW, LIVE or capital deployment.
