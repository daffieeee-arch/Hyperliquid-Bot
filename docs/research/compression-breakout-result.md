# BTC compression breakout: margin winners, losing account

Actual result, 2026-11-14; delivery [#270](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/270),
programme [#237](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/237).
The single [registered compression policy](../experiments/exp_compression_breakout_20261114.md)
completed and passed independent full native/accounting reconstruction.
It loses **8.8130% of account equity at base and 28.9688% at stress** despite
90/76 trades attaining at least 20% net initial-margin ROI. No candidate is
admitted. Stop this registered attempt; do not tune its compression threshold,
expiry, target, leverage or direction after observing these results.

The operator's two objectives remain separate: 20% net initial position margin
per large winning trade and 1–2% per frequent small winning trade. They are not
20% account/day or a promise that every trade wins. This study tests only the
large-trade mechanism; it neither certifies nor refutes a different frequent
cross-venue strategy. The old positive, slow h720 result and the rejected hourly,
OI and pressure studies retain their original outcomes and limits.

## Frozen question and execution

One configuration asks whether unusually narrow completed hours precede
cost-covering directional expansion. At whole hour S, arm the completed H1 box
when its positive normalized range is at most half the median of 24 prior H1
ranges. The first strict minute-close breakout during S+1 through S+59 commits
a long/short entry, consumes the box even if busy, and executes the next minute.
Whole-hour decisions, zero-range boxes and equality are not breakouts.

Quantity is fixed at the decision using 25% of then-known equity divided by
then-known mark. Future entry prices cannot change or cancel that quantity.
Initial margin is immutable **abs(quantity) times actual entry MARK / 10**.
This is an offline 10x collateral reference, not a real leverage/risk change.
Starting after entry, a hypothetical charged close at +20% or -10% net margin
commits an exit for the next minute. Maximum holding is 24 hours, with paid
known source-segment endpoint closes. Targets are triggers, not guaranteed fills.

Fills are Binance native TRADE-minute close proxies; valuation and collateral
use separate MARK data. Base costs are 4.5 bps fee plus 2 bps adverse friction
per side; stress costs are 9 plus 4 bps per side. Approximate roundtrip hurdles
are 13/26 bps before funding and notional effects. Funding belongs to pre-execution
units at exact native Binance settlement milliseconds and marks. These are not
Hyperliquid funding, executable prices, lot/grid/depth/latency/partial-fill proof
or native Hyperliquid performance. Actual receipt before each decision remains
an archive-availability assumption.

Base and stress apply the same policy with their own charged bracket triggers,
capital and busy exclusions. Their exit clocks and trade counts differ; this is
not a same-fill cost sensitivity. No selector or new parameter sweep is used.
Registration increases programme usage from 36 to **37/40**, within four families.
The previous hourly-family stop and old experiment pins remain intact.

## History, gaps and every outcome

Universe is selected-survivor Binance USD-M BTCUSDT, January 1, 2024 through
September 1, 2026 UTC, ending boundary excluded from decisions: 974 calendar days.
2024 is development; 2025 and January–August 2026 are diagnostics. All are exposed
retrospective history, not untouched OOS or the current November market.
December 2023 supplies warm-up. Missing MARK days August 12, 2024 and June 29,
2026 remain flat in denominators. Three valid segments carry actual prior
capital with paid endpoint closes; years/months do not reset state or equity.
This archive-informed flatness cannot anticipate a live source outage.

All 24 account/cost/segment cases complete, with no calculation failure or
counterfactual collateral-invalid chain. Starting equity is 10,000 USDT.
Returns and sampled drawdowns in this report are **account percentages**;
margin statistics are explicitly identified separately.

| Cost | Trades | Account net % | Sampled DD % | Realized >=20% margin trades | Positive-net trades |
| --- | ---: | ---: | ---: | ---: | ---: |
| Base | 635 | -8.8130 | 12.3604 | 90 | 236 |
| Stress | 652 | -28.9688 | 30.4080 | 76 | 210 |

| Period | Base net % | Stress net % | Base / stress closed trades |
| --- | ---: | ---: | ---: |
| 2024 development | -5.8981 | -13.6929 | 240 / 245 |
| 2025 diagnostic | -5.6636 | -14.9036 | 247 / 256 |
| Jan–Aug 2026 diagnostic | +2.7201 | -3.2854 | 148 / 151 |

Base gross price PnL is +1,026.7928 USDT, less 1,322.4674 fees and 587.7634
friction, plus 2.1429 signed funding: -881.2951 net. Stress gross is +600.4476,
less 2,416.2520 fees and 1,073.8899 friction, minus 7.1822 funding: -2,896.8766.
Cost-dependent exits and carried capital explain the different gross results.
Base long/short episodes contribute -240.3287 / -640.9665; stress
-1,194.7626 / -1,702.1139. No direction is selected after this attribution.

The attempt carries `reject_cost`, `reject_drawdown` and
`reject_regime_robustness`: stress DD exceeds the 15% research screen and costs
and chronological diagnostics fail. Both sampled curves also exceed the
unchanged 7% PAPER drawdown limit. No retrospective halt was inserted; passing
the distinct collateral screen does not admit these curves to PAPER.

## Margin tails, delayed exits and frequency

Base median holding is 577 minutes; stress is 506.5 minutes; both maximums are
1,440 minutes. Mean completed trades per calendar day are 0.6520/0.6694, maximum
five under either cost. There are 421/417 days with no completed trade and
90/75 days with a realized >=20% margin winner. These are descriptive exposed
history, not a daily trading or income commitment.

| Diagnostic | Base | Stress |
| --- | ---: | ---: |
| Median net initial-margin ROI % | -9.0528 | -9.5186 |
| Fifth percentile net initial-margin ROI % | -12.2438 | -12.1503 |
| Worst / best net initial-margin ROI % | -17.5904 / +29.9689 | -16.6321 / +28.6891 |
| Maximum consecutive losing trades | 12 | 13 |
| Target-triggered exits / realized target misses | 136 / 46 | 111 / 35 |
| Realized exits below -10% margin | 223 | 260 |
| Stop / time / segment-end exits | 343 / 155 / 1 | 389 / 151 / 1 |

The >=20% winners alone cannot establish expectancy: all losing trades, fees,
funding and delayed-fill misses stay in the account result. Targets and stops
can overshoot at the next-minute execution. Frozen decision quantity also means
an entry gap can change actual initial margin and the nominal stop budget.
No margin additions, averaging or recovery sizing are used.

The inherited result field `no_trade_calendar_days` means **flat calendar days**
(266/273), not days with zero completed trades. The account CSV uses the explicit
names `flat_calendar_days` and `zero_completed_trade_days`. Close-day counts use
(UTC day start, UTC day end], so a midnight close belongs to the previous day.

## Controls, uncertainty and risk limits

The [account CSV](compression-breakout-accounts.csv) retains all eight
account/cost rows: candidate, cash and matched long/short under both costs.
Controls use each candidate cost case's exact entry/exit clocks and absolute
quantity-per-equity coefficients; their own capital changes actual quantities.
They are conditional timing comparisons, not newly selected standalone policies.
All controls pass the separate collateral screen.

| Control | Base account net % | Stress account net % |
| --- | ---: | ---: |
| Cash | 0 | 0 |
| Matched long | -15.8762 | -31.7637 |
| Matched short | -21.8521 | -37.6166 |

Beating losing matched controls does not turn a losing candidate profitable.
The [paired CSV](compression-breakout-comparisons.csv) preserves all 48 rows:
three controls, two costs, whole-period/three chronological scopes and 7/30-day
blocks. Forty-two intervals span zero; six are wholly negative versus cash
(stress whole-period, 2024 and 2025, both blocks). None is wholly positive.
The existing circular-block procedure uses 2,000 resamples and seed 20261113.
Bounds describe annualized arithmetic daily-return differences, not cumulative
compounded account return or post-search significance on independent OOS data.

CSV returns, drawdowns, exposure, margin ROI and uncertainty bounds are fractions;
money is USDT, turnover quote notional, holding minutes. Cash holding/margin
statistics are empty because no episodes exist; numeric zero stays zero.
Empty control dispositions mean the candidate-only disposition function is not
applied, not admission. Unknown/failure values would remain unavailable rather
than becoming profitable zeroes; this run has no failed chain.

Collateral uses immutable initial margin, entry fee, marked PnL, owned funding,
every held native minute's adverse MARK extreme and conservative adverse-first
funding timing, against a 1.25% maintenance proxy. It is not an exchange
liquidation engine or historical tier certification. Maximum sampled gross
exposure is about 0.255x under each cost; no sampled 1x gross, 0.5 asset/strategy,
1% UTC daily-loss or 1% sampled intraday-loss breach is reported. Both breach
PAPER DD7%. Sampled minute/exact-funding drawdown is not continuous tick risk.
Actual trading modes and all real risk limits remain unchanged.

## Reproduction and independent acceptance

Source [PR271](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/271) was
independently CODE/security and QUANT reviewed, passed ready-head CI including
4,891 tests, and separately squash-merged before financial execution.
The actual detached checkout uses source commit
`ecea825dd633be9465ce19ed261689bb097f51cb`; the original protocol SHA256 is
`6c3b823a20f9cd2ff0948b5c44956a64540d886f9738acaa8a42e513b31ed70b`.
Input index SHA256 is
`ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`.
Source-only admission validated 1,400,714 minute/exact-funding frames and 24,120
H1 bars before labels, in 22.524 seconds with 1,661,576 KiB peak RSS.

Run from that source with its frozen environment, explicit `PYTHONPATH=src`
and `TRADING_MODE=PAPER`, using `python -B -m research.compression_breakout_cli`,
`--input` set to the bound private index, `--output` to a new local directory,
`--source-commit` set to the source commit above and `--expected-plan-sha256`
set to the unchanged protocol hash above.
Retained run receipts record exact paths and arguments. The actual financial
run exited zero in 182.953 seconds with 2,424,284 KiB peak RSS, two CPUs,
nice10 and an 8 GiB address-space cap. Its bundle has 66 files / 162,064,800 bytes.
An earlier import-only preflight failed before source/strategy I/O because
`PYTHONPATH` was missing; its error and environment receipts are preserved.
Only that launcher environment was corrected, with no model or parameter change.

A nonauthor independently reread native archives and reconstructed policy,
quantity, funding, wallet, margin, collateral and outputs using a separate
online Decimal implementation, rather than trusting production summaries.
All 24 cases / eight account-cost chains / 83,234,310 checks pass, with full
input/code/lock/result bindings unchanged before and after. Largest absolute
summary difference is 1.82e-8 on approximately 2.52 million turnover, within the
predeclared numeric tolerance. The independent audit took 305.106 seconds and
722,544 KiB peak RSS. Accounting acceptance confirms a failed strategy; it does
not certify performance, actual fills or a native GitHub approval.

| Retained private evidence | SHA256 |
| --- | --- |
| Actual result | `438a0570c84912d4ad6298f0f4976b28d4ec642c3347b6e381291fab7be8aee0` |
| Claim | `3fdf3b8e3fc7252bfd88cdc3842d987601da4a796eb3b7b531983d089aba546e` |
| Completion | `0c7b4266b78d0aa765442c9e57b1f6bbf0841984cc79ae0d6124d9dd664dc304` |
| All 66 result files manifest | `46e5d924fb24dde905cdf2b52fe20fa3b965a7863fd0febb304d430a230238c8` |
| Independent native audit | `a0e7097ec93aac412fd831d7ebbc98de8b617b573e3d4e68f8ee56fc25363259` |
| Independent actual acceptance | `b069a8b71315cfddd7aa268e8fccc9013cdb4af68c48c88157a9eab7470efeda` |

Raw market data, source bindings, ledgers and detailed receipts stay outside
Git on their existing retained locations. This publication adds no collector,
dependency, runtime, risk, credential or CI behavior. It starts no orders or
service deployment. A different frequent-trade hypothesis requires its own
frozen protocol, source qualification and independent review before labels;
these failed large-trade results do not authorize post-result tuning.
