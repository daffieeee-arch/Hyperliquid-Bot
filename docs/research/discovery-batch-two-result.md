# Discovery batch two: first real-history comparison

Result checkpoint: 2026-11-13; issue [#237](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/237).

**No admitted candidate: ten rejected configurations and two insufficient.**
All twelve registered configurations completed without calculation errors.
Four have positive whole-month base returns; two remain positive at doubled
costs, but those two have no development trades. Positive total returns alone
therefore do not satisfy the registered cross-period candidate rule.

## Inputs and retained attempts

- Run source: `6e4cb78dc85aee7189add459245bad5bfd9cbf25` ([PR239](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/239)); evaluator registration: [PR238](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/238).
- Study: `discovery-real-history-20261113-b2`; [frozen amendment](../experiments/exp_discovery_batch_two.md) inherits [batch one](../experiments/exp_discovery_batch_one.md).
- Outer input SHA-256: `0e15ec375ecb4f7e2c4a7d7ae1e7219ec30acfc890645c7da4b466b36569e9c7`.
- Plan SHA-256: `e41086ee8c2845420991b6663a7d2519f60fc89f641501e0c739012cf1b8f6b3`.
- 89 source files, 3,688,609 bytes: BTC/ETH/SOL native hourly prices, minute marks, exact funding settlements and 31 daily BTC OI archives with checksums.
- First attempt `discovery-real-history-20261113-b1` failed **before any financial trial**, because five native OI values were zero. Its claim, input, failure and resource receipt remain retained. The numerical configurations were never changed.
- Batch two records 8,928 contiguous OI observations and five zeros as unavailable. None is selected by the hourly feature clock; no interpolation or forward fill.

Both chronological halves are exposed retrospective history, not fresh OOS:
development January 1–15, diagnostic January 16 through January 31 23:00 UTC,
2025. Initial equity is 10,000 USDT; initial absolute exposure is at most 0.5.
Base costs are 5 bps fee plus 2 bps adverse friction per side; stress is 10/4.
Native funding, fixed quantities and the final charged close are included.

## All registered configurations

Returns and drawdowns below are percentages of simulated marked equity.
Episodes count complete entry/exit windows, not independent statistical samples.

| Configuration | Episodes dev/diag | Net dev | Net diag | Net whole | Net whole, 2× costs | Max DD base | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `residual-continuation-l24-h4` | 72/76 | -3.9168 | -7.8596 | -11.4686 | -20.1951 | 12.4140 | reject |
| `residual-continuation-l24-h12` | 27/29 | +1.4262 | -0.9871 | +0.4250 | -3.4392 | 6.0395 | reject |
| `residual-continuation-l72-h4` | 72/76 | -3.7804 | -8.8083 | -12.2557 | -20.9035 | 12.8243 | reject |
| `residual-continuation-l72-h12` | 27/29 | -0.4623 | -4.1530 | -4.5960 | -8.2703 | 8.4747 | reject |
| `residual-reversion-l24-h4` | 72/76 | -5.9292 | -2.5743 | -8.3508 | -17.3826 | 8.3534 | reject |
| `residual-reversion-l24-h12` | 27/29 | -5.0888 | -3.1186 | -8.0486 | -11.5923 | 9.7538 | reject |
| `residual-reversion-l72-h4` | 72/76 | -6.0592 | -1.5445 | -7.5101 | -16.6226 | 11.4491 | reject |
| `residual-reversion-l72-h12` | 27/29 | -3.2904 | +0.0900 | -3.2033 | -6.9303 | 8.7193 | reject |
| `oi-recovery-p50-h4` | 5/11 | -2.6846 | +0.2893 | -2.4030 | -3.4925 | 3.7386 | reject |
| `oi-recovery-p50-h12` | 4/9 | -3.6298 | +4.3653 | +0.5771 | -0.3380 | 4.6375 | reject |
| `oi-recovery-p100-h4` | 0/6 | +0.0000 | +0.8216 | +0.8216 | +0.3988 | 1.4072 | insufficient |
| `oi-recovery-p100-h12` | 0/5 | +0.0000 | +1.4681 | +1.4681 | +1.1111 | 3.3406 | insufficient |

## Interpretation and benchmarks

The eight residual-dispersion variants all fail the registered criteria.
The least unfavorable whole-month base case, 24-hour continuation held for
12 hours, returns +0.4250% but falls to −3.4392% at doubled costs and loses in
the diagnostic half. Four-hour variants turn over frequently: positive gross
price movement in some reversion variants is consumed by assumed execution
costs. Lowering costs after seeing this result would be a new assumption,
not evidence that these variants work.

The two 1%-price-shock OI-recovery variants return +0.8216%/+0.3988% (4h)
and +1.4681%/+1.1111% (12h) at base/stress costs. They have only six/five
episodes, all in the diagnostic half, and no development episodes. Their
mechanical development ranks are first/second because a flat 0% beats other
negative development scores. **Those ranks are not positive development
evidence or admitted finalists.** Do not select a winner from diagnostic PnL.

For the 12h OI variant, base net PnL is 146.8099 USDT; fees are 25.4018,
execution friction 10.1607 and signed funding −0.7837 USDT. Net PnL per
episode is 29.3620 USDT, from only five observations. Its approximate
same-quantity break-even total friction is 35.8975 bps of reference turnover;
this includes funding and is not an executable quote estimate.

Cash returns 0%. The fixed 0.5-equity BTC buy-and-hold benchmark returns +3.7061% at base costs and +3.6332% at doubled costs,
with 6.8261% marked drawdown. It has different time-in-market and directional
exposure; this is not a risk-matched performance comparison.

## Reproducibility, acceptance and limits

The local result bundle contains all twelve full ledgers, executions and
episodes, the leaderboard, both benchmark ledgers, source/plan/code identities,
quality census and completion record. It occupies 302,494,452 bytes; the run
took 16.32 seconds and peaked at 282.86 MiB RSS on one VM CPU. The original
archives and the continuous collector were not modified.

Independent result audit **PASS**: all 89 inputs and nine source/plan pins
were rehashed. A separate nonauthor reviewer independently reconstructed all
24 trial cost cases and four benchmark cases from native prices, marks and
279 native/REST funding events, without importing the evaluator. Across
5,123,362 arithmetic checks, the maximum cash/equity discrepancy was zero.
Fees, fills, funding, attribution, gross/expectancy/friction, drawdowns,
chronological endpoints, terminal flatness and development ranking agree.
The audit and all source artifacts remain outside Git in the private run folder.

CI ran 4,290 tests on each final source PR. Independent code/security-scope
and quantitative/data reviews passed before each separate expected-head squash
merge. The registered run used the actual merged source, with source and input
identities checked again before completion.

Retain the no-candidate disposition. The next research decision should examine
whether a predeclared broader-history comparison can establish repeatable
development evidence for deleveraging recovery, alongside a different
cost-aware mechanism. That needs its own registered data/period contract;
do not silently retune this diagnostic period or promote its five-trade lead.
The cumulative unique-configuration count is 12 of the 40-configuration pilot
budget, across two of at most four planned mechanism families.

All results retain survivor selection, exposed-period, assumed OI availability,
archive-vintage and proxy-fill limitations. Minute marks do not measure every
intraminute risk; collateral/liquidation and Hyperliquid execution are unproven.
No OOS result, PAPER admission, TESTNET orders, SHADOW or LIVE activation follows.
