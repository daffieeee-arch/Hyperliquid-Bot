# Daily momentum and frequent micro trades: actual results

Result checkpoint: 2026-11-13; [delivery #241](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/241),
[discovery programme #237](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/237).

**Neither model produces an admitted candidate.** The daily calculation completes
all eight configurations: four bracket variants reject and four time-exit variants
are collateral-invalid counterfactuals. Every whole-period net return is negative.
The micro calculation preserves eight failed configurations with unclosed exposure;
it does not establish complete-period performance or prove that no micro edge exists.
Neither study selects a development finalist. No order or deployment follows.

## Frozen questions, costs and data

The [pre-scoring protocol](../experiments/exp_two_models_20261113.md) fixes all
sixteen new configurations, inputs, folds, controls and costs. Together with the
[earlier twelve](discovery-batch-two-result.md), this is **28 of 40 unique
configurations across four mechanism families**. Twelve configurations remain;
a new family would require an explicit programme amendment. No thresholds were
changed after these financial outcomes.

The daily question is whether completed 24/72-hour BTC momentum predicts useful
long/short directions; filtered variants require normalized magnitude >=1.
Each variant exits at next midnight or a next-minute-executed +20%/-10%
**net initial-margin** bracket. Primary Binance BTCUSDT USD-M history covers
January 2024 through August 2026, with December 2023 warm-up. Development is 2024,
validation 2025, diagnostic January–August 2026. The 974 calendar days include two
prequalified unavailable/no-target days, August 12, 2024 and June 29, 2026.
Earlier archives exist, but missing funding settlement marks exclude them from
this calculation. This is Binance price/funding evidence with Hyperliquid-like
assumed costs, not executable native Hyperliquid performance.

The micro question is whether contemporaneously observed BTC book imbalance,
signed trade flow and BBO order flow justify short long/short trades. Eight
configurations cross 30/120-second history, 0.2/0.4 confluence and 1%/2% net-margin
targets. Eleven closed native Hyperliquid captures contain 9,323 hash-qualified
parts and 156.4942 hours across 3,159 conditional intact windows. September
18 captures are development; September 23 validation; September 24/27 diagnostic;
six November 13 captures are recent diagnostic. Current active recordings are
excluded. Known fragmentation and clock/quote qualification reduce opportunity.

Both models start at 10,000 quote units and report margin as immutable actual
entry notional divided by a fixed 10x reference. Daily requested notional is
0.5 of equity; micro additionally caps it at 100 USDC and displayed entry depth.
The latter uses at most approximately 10 USDC initial margin: a 1–2% margin
winner means approximately 0.10–0.20 USDC at that cap, not 1–2% account growth.

Daily base costs per side are 4.5 bps fee plus 2 bps adverse friction; stress
doubles both on the same base signal/exit schedule. Micro base costs are 4.5 bps
fee plus 1 bp friction beyond the observed spread, with 500ms delayed fills;
stress uses 9/2 bps and 1000ms. Micro re-evaluates the same fee-aware policy,
so stressed exits and counts can differ. Native funding is charged exactly in
daily accounting; the micro fixed hourly eligibility calendar requires complete
native funding censuses proving flatness around settlements.

**All periods are exposed retrospective history, not untouched OOS.** The recent
bearish market is context, not a reason to change thresholds or select winners.
The daily period ends in August and cannot measure today's downtrend. Micro
source qualification includes November data, but its failed account chains do
not reach that group for financial scoring. No November micro performance claim
is supported by this attempt.

## Daily: all registered outcomes

Returns and maximum drawdowns are percentages of simulated marked **account
equity**, not margin. Target counts concern realized **net initial-margin ROI**.
All cases have zero calculation failures; collateral-invalid curves remain
visible but cannot represent executable performance.

| Configuration | Closed trades | Net base | Net 2× costs | Max DD base | +20% margin hits / 974 days | Disposition |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `daily-l24-filtered-bracket` | 298 | -8.0946 | -24.2758 | 14.9197 | 47 | reject |
| `daily-l24-filtered-time` | 298 | -7.1875 | -23.5416 | 25.5603 | 40 | counterfactual_risk_invalid |
| `daily-l24-forced-bracket` | 972 | -40.2017 | -68.2258 | 42.7344 | 148 | reject |
| `daily-l24-forced-time` | 972 | -44.6891 | -70.6225 | 53.2215 | 146 | counterfactual_risk_invalid |
| `daily-l72-filtered-bracket` | 305 | -19.2043 | -33.7433 | 22.3346 | 48 | reject |
| `daily-l72-filtered-time` | 305 | -24.8147 | -38.3658 | 42.1339 | 45 | counterfactual_risk_invalid |
| `daily-l72-forced-bracket` | 972 | -46.6254 | -71.6435 | 49.6471 | 146 | reject |
| `daily-l72-forced-time` | 972 | -36.7068 | -66.3796 | 54.1292 | 145 | counterfactual_risk_invalid |

Four time-exit variants breach the conservative isolated-10x collateral screen.
For example the worst completed base trade has net margin ROI around -121.31%;
its continued ledger is a retained counterfactual, not an acceptable loss model.
The screen uses minute mark extremes and a current small-tier maintenance proxy;
it does not certify historical venue liquidation ordering.

For the four collateral-qualified bracket variants, the fold results are:

| Configuration | Dev 2024 base / stress | Validation 2025 base / stress | Diagnostic 2026 base / stress |
| --- | ---: | ---: | ---: |
| `daily-l24-filtered-bracket` | -12.1893 / -18.6256 | +1.1218 / -5.7315 | +3.5020 / -1.2856 |
| `daily-l24-forced-bracket` | -24.4388 / -40.4151 | -20.7522 / -37.5046 | -0.1374 / -14.6721 |
| `daily-l72-filtered-bracket` | -10.9262 / -17.9882 | -2.6358 / -9.0677 | -6.8380 / -11.1545 |
| `daily-l72-forced-bracket` | -13.1139 / -31.4796 | -24.4719 / -40.4431 | -18.6653 / -30.5134 |

The filtered 24h bracket has the least negative whole-period result among the
qualified daily variants, -8.0946% base and -24.2758% stress. This hindsight
comparison does not select it as a finalist. Its 47 realized target hits cover
only 4.8255% of all 974 calendar days (15.7718% of its 298 trades); 676 days have
no trade, including the two unavailable days. It has eleven consecutive losing
trades and a worst realized margin loss of -38.6075%, despite a -10% trigger.
Delayed minute execution and gaps can overshoot a trigger.

Its same-quantity gross price PnL is +988.1413 quote units; fees are 1,234.6124,
adverse friction 548.7167 and signed funding -14.2680. These reconcile to net
-809.4558. Thus recognizing some direction does not establish an edge after
turnover costs. Its descriptive break-even total execution cost is about
3.5496 bps of actual turnover, below the assumed 6.5 bps. This is not a measured
live execution quote or permission to lower costs after seeing losses.

Long-side net PnL is -1,659.1465 and short-side +849.6907 in this particular
base case, yet the whole strategy loses and the doubled-cost short contribution
falls to +5.7575. Selecting shorts after this outcome would create a new
hypothesis, not validate the frozen two-sided model.

### Identical-calendar directional controls

| Control | Net base | Net 2× costs | Collateral status |
| --- | ---: | ---: | --- |
| `always_long_bracket` | -50.6775 | -73.7952 | screen qualified |
| `always_long_time` | -29.9337 | -62.7754 | counterfactual risk invalid |
| `always_short_bracket` | -49.7175 | -73.2877 | screen qualified |
| `always_short_time` | -65.3174 | -81.5860 | counterfactual risk invalid |
| `cash` | +0.0000 | +0.0000 | screen qualified |

Time exits compare only to time controls, brackets only to bracket controls.
Cash returns zero. Frequent close/re-entry, costs, funding and varying capital
make always-long daily trading different from buying and holding BTC. Beating
a losing control does not create positive expected net PnL. The registered
development-only rule selects no daily finalist.

## Micro: retained prefixes, not complete performance

Every configuration-cost chain stops permanently after its first unknown held
exit. Later windows are source-qualified but never used to erase exposure or
restart that chain with fresh equity. Both the plain momentum control's cost
cases fail too. Unknown exits arise because the opposite touch cannot cover
the held quantity, or no sufficiently fresh delayed quote arrives by the
registered deadline. These are failures of execution proof in this model;
they are not observed exchange order rejections or evidence that a real order
would never fill deeper in the book.

The following values are **failed prefixes**. Net account change includes the
last observed mark of an unclosed position and omits any unknown future close
cost; it is not final realized profit. Hours sum full qualified windows admitted
through the failed case, including
flat intervals and potentially the unprocessed tail of its final window. They
are not elapsed time to failure, executed-prefix coverage or hours at risk.

| Configuration | Base hours | Closed base trades | Base target hits | Base account change % | Stress hours | Closed stress trades | Stress account change % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `flow-w120-s20-tp1` | 77.4184 | 94 | 4 | -0.1043 | 6.5223 | 23 | -0.0525 |
| `flow-w120-s20-tp2` | 77.4184 | 93 | 0 | -0.1014 | 6.5223 | 23 | -0.0525 |
| `flow-w120-s40-tp1` | 10.2661 | 36 | 1 | -0.0432 | 9.3610 | 31 | -0.0736 |
| `flow-w120-s40-tp2` | 10.2661 | 36 | 0 | -0.0431 | 9.3610 | 31 | -0.0736 |
| `flow-w30-s20-tp1` | 1.9992 | 16 | 0 | -0.0173 | 3.1818 | 19 | -0.0423 |
| `flow-w30-s20-tp2` | 1.9992 | 16 | 0 | -0.0173 | 3.1818 | 19 | -0.0423 |
| `flow-w30-s40-tp1` | 1.2746 | 10 | 0 | -0.0125 | 3.1818 | 18 | -0.0441 |
| `flow-w30-s40-tp2` | 1.2746 | 10 | 0 | -0.0125 | 3.1818 | 18 | -0.0441 |

All stressed prefix target counts are zero. The 120s/0.2/1% base configuration
closes 94 trades and records four actual 1%-margin target hits before failing
in validation; its 77.4184 admitted-window hours include the final window in full
and do not establish elapsed-to-failure coverage or a full-period activity rate.
Its mean closed-trade net margin ROI is -1.1222%. The corresponding 2%
target closes 93 trades without a realized 2% hit. The longest 1%-target prefix
has just +0.00294 USDC gross book-mid PnL on closed trades, minus 8.206094 fees,
0.119590 observed spread and 1.999800 adverse friction: -10.322544 closed net
PnL, plus -0.111386 of retained open marked exposure. This observed prefix has
no meaningful gross directional edge to pay its trading costs. These observations cannot
justify hundreds of profitable daily trades. Accounts are small-order examples;
the small account drawdowns reflect mostly unused cash and must not be mistaken
for low risk at larger exposure.

This attempt cannot evaluate comparative micro alpha across validation,
diagnostic and recent-bear groups. Record **insufficient execution evidence /
retained trial failures**, not eight ordinary negative-alpha rejections. There
is no selected micro finalist or per-day extrapolation. Displayed-touch taker
fills remain modeled assumptions subject to delay, size and friction; no maker
queue position or exchange fill is certified.

## Reproducibility and independent acceptance

Actual clean merged source is
`a52eeee4aec9743d1673ccae4f7ff711295216ee` ([PR242](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/242)).
Source and plan were independently reviewed before financial scoring; all
4,500 local and current-ready CI tests passed. Jobs rechecked actual source,
all eleven code/plan pins and input identity before and after calculation.
The input SHA-256 is
`ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`;
plan SHA-256 is
`967ec132e3827e208a99dcea0d372050ab6fd0d3021c0bb27269754158649345`.

Daily result JSON SHA-256:
`e78a9b4af75203027d69633b3640b71114df8bb24e54c2820ec901efe4cf96bb`.
Micro result JSON SHA-256:
`ed0a98cb3b920c319935027b3629802292090399a1dce2ffabe86c6ef2bfdce0`.
Daily completion is `complete`, zero failed configurations; micro completion is
`complete_with_trial_errors`, eight failed configurations (process exit 2).
Every ledger, execution, episode, failure and zero-entry proof remains retained
outside Git under the private VM report root
`/home/chupa/.codex-workspaces/reports/hyperliquid/2026-11-13-two-models/`.
At run completion the daily bundle has 293 files / 575,843,015 bytes; micro has
6,430 files / 28,510,434 bytes. Daily used 496.64s and 702.43MiB peak RSS;
micro 367.77s and 241.33MiB. Each bounded job used at most two assigned VM CPUs.
No NAS input archive, continuous collector or trading mode was changed.

Independent nonauthor result reconstruction found no financial discrepancies.
The daily audit independently checked all 832 monthly variant/control cost
cases, 32 months and 165,641,325 arithmetic/chronology/summary assertions,
without importing the financial evaluator. A separate native collateral
reconstruction checked 17,964 episodes and 77,406 assertions; all flags agree.
All 2,922 primary native funding events match REST rates exactly by Decimal
comparison, with positive settlement marks and exact terminal ownership.

The micro audit checked 3,913,175 accounting/chronology/summary assertions over
310 actual case artifacts, 2,945 flat-proof windows, 201 replayed windows and
13 zero-duration windows. It independently sampled 54 native book/fill
references across 73 hash-qualified parts, including every one of the five
opposite-touch capacity failures. All eighteen accounts (eight configurations
plus momentum control, each base/stress) correctly retain an unresolved
position; thirteen fail quote deadlines and five fail touch capacity. These
are accepted failure records, not eighteen successful execution tests.
Daily acceptance receipt SHA-256:
`6d6dfabd44ee3183487464f14fa47d287c5a43353221c2e9ee585f8a3300d91a`.
Micro acceptance summary SHA-256:
`47a99eac2b38f18339e2bcbf32140ad6e1301959baf08ec063dab9b3b98af514`.
Audit PASS certifies the stated accounting/disposition within these assumptions,
not trading profitability or complete-period micro performance.

The immutable output-bundle manifest covers 6,723 files / 604,353,449 bytes;
its SHA-256 is
`28ba67389e73d1a45d6e0fcd310d0d3691de4b21c4a64d14eea28ec9754ea7e5`.
Independent scripts, detailed receipts and audited per-case/fold tables remain
beside the private result bundles; no raw market data or credentials enter Git.

Execution assumptions remain material: daily minute-boundary fills do not prove
which side of a millisecond funding settlement a real order would fill. Native
cashflow timestamps are preserved, but this coarse execution model cannot
certify Hyperliquid funding capture. Micro qualification conditions on known
intact windows and observed clock bounds; it does not prove live gap recovery,
queue priority, full-depth exit capacity or native liquidation ordering.

## Concrete next decision

Preserve both identities and all failures. Do not optimize these exposed folds
until they look profitable. The highest-priority micro prerequisite is a
separately reviewed full-depth/partial-fill, delayed-exit and carried-exposure
contract, measured against native book evidence; no optimistic synthetic close
or looser quote guard may silently rescue this attempt. Only a newly registered
continuation may then test the recent bear interval financially.

For daily research, costs and conditional short contribution motivate a
separate cost-aware regime hypothesis with less turnover and explicit flat
states, assessed against matching exposure controls across broader regimes.
This is a proposed direction, not a result or authorization to spend the
remaining twelve configurations on repeated threshold searches. Any new batch
must define its selection rule, costs, position/margin risk and fresh OOS
boundary before outcomes. A fifth mechanism family requires amending the
four-family budget. No PAPER strategy admission, TESTNET orders, SHADOW or LIVE
follows from this research.
