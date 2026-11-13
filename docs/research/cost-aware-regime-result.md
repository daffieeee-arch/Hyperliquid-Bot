# Persistent BTC regimes: a positive retrospective candidate

Checkpoint 2026-11-13; [delivery #245](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/245),
[discovery programme #237](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/237).

**The frozen development rule selects one positive retrospective candidate:
`regime-h720-plain`.** Its whole-period net simulated account return is
+16.4718% at base costs and +15.7015% at doubled costs, with 20 closed trades.
This is positive historical evidence, not established statistical alpha,
untouched OOS, native Hyperliquid profitability or trading admission. The 2025
stressed result is nearly flat, and every fold's descriptive block interval
includes zero. The active goal of finding supported profitable strategies
therefore remains open.

## What changed and what stayed frozen

The [prior daily/micro attempt](two-model-momentum-micro-result.md) remains
unchanged: daily losses and collateral-invalid curves, plus unresolved micro
exits. Its funding, costs, failure records and exposed periods are retained.
The new [pre-scoring protocol](../experiments/exp_cost_aware_regime_20261113.md)
asks whether carrying a fixed position through a persistent BTC regime can
pay costs better than closing/re-entering every day. It registers four new
configurations: 7/30-day momentum, with/without a slow price confirmation.
The programme has used **32/40 unique configurations, four mechanism families**.
There was no threshold, sizing or leverage search after these results.

Signals use only completed Binance USD-M BTCUSDT hourly closes. At each daily
00:00 decision, normalized momentum admits long/short at absolute score >=1;
it holds the same quantity while the sign agrees and the absolute score stays
at least 0.5; otherwise
it goes flat or reverses. Confirmed variants also require the slow 720-hour
price average to agree. Entries/closes execute at the next completed 00:01
trade-minute reference; opposite re-entry occurs at 00:02. No daily resizing,
mandatory daily trade, fixed profit-taking target or averaging down.

Entry notional is equity times min(0.5,10%/lagged annualized volatility). The
initial isolated research margin is **absolute quantity × entry mark / 2**,
frozen for the episode. This is an actual 2x research-collateral convention;
it is different from the old separately labeled 10x fill-notional reference.
The requested 20% margin target is reported diagnostically on this frozen
margin, never as 20% account return or a guaranteed daily winner.

Base costs are 4.5 bps fee plus 2 bps adverse friction per side; stress doubles
both on the same frozen schedule, carrying capital independently. Exact native
Binance funding settlements and their qualified settlement marks are charged.
They are not Hyperliquid funding: HL settles hourly using oracle prices.
Native execution spread, order precision and actual fills remain unverified.

## Data and all four outcomes

Primary period: January 1, 2024 through September 1, 2026, 974 calendar days.
Development is 2024; validation 2025; diagnostic January–August 2026. December
2023 provides complete 720-hour warm-up. All history is exposed retrospective,
with no fresh OOS or claim to have tested today's November downtrend.

Known missing-mark days August 12, 2024 and June 29, 2026 define three scheduled
source-qualified segments, identical for every variant/control. Positions close
at valid prices before those days; cash carries to the next segment. No monthly
or annual reset, interpolation or erased loss. The two unavailable days remain
flat/no-target days in the full denominator. This conditional archive treatment
does not demonstrate anticipation or live recovery through an unknown gap.

Returns and sampled drawdowns below are percentages of marked **account
equity**, not position margin. All four variants and eleven controls complete
both cost cases across all three segments with zero calculation failures.

| Configuration | Closed trades | Net base % | Net doubled costs % | Max DD base % | Max DD stress % | Disposition |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `regime-h168-confirmed` | 78 | +2.6088 | +0.1897 | 16.6751 | 17.7173 | reject_drawdown |
| `regime-h168-plain` | 80 | +5.9097 | +3.3587 | 15.1315 | 16.2188 | reject_drawdown |
| `regime-h720-confirmed` | 22 | +13.5535 | +12.7592 | 12.6570 | 12.8549 | reject_nonpositive_fold |
| `regime-h720-plain` | 20 | +16.4718 | +15.7015 | 10.7451 | 10.9142 | positive_retrospective_candidate_requires_fresh_oos_and_execution |

The selected 30-day plain variant wins the predeclared development ranking
without using validation/diagnostic results. Seven-day variants have positive
total returns but breach the 15% sampled drawdown rule and lose in 2025. The
confirmed 30-day variant loses slightly in 2025. No runner-up replaces a failed
selected candidate, and no rejected variant is quietly tuned or promoted.

## Selected candidate across chronological folds

| Fold | Closed episodes | Nonflat calendar days | Net base % | Net stress % | Daily Sharpe base / stress |
| --- | ---: | ---: | ---: | ---: | ---: |
| development | 7 | 157 | +10.0591 | +9.8142 | 0.9383 / 0.9175 |
| validation | 6 | 135 | +0.1998 | +0.0030 | 0.0632 / 0.0331 |
| diagnostic | 7 | 107 | +5.6156 | +5.3579 | 0.9526 / 0.9134 |

Fold returns use marked equity at fixed UTC boundaries, including a position
still open across a year. They do not allocate a later episode's full profit
to its entry year. The complete ledger carries equity across both source gaps.

Base gross price PnL is +1,973.9049 quote units. Subtract 50.2715 fees
and 22.3430 adverse friction, then add signed funding -254.1126 to obtain
net +1,647.1778 from 10,000 initial equity. Net long contribution is +987.5841;
short contribution +659.5937. At doubled costs those contributions are
+940.8372/+629.3128. Positive returns come from both sides in this history;
that is not proof of predictive edge in the current downtrend.

There are 20 completed episodes: ten winners and ten losers, median holding
17 days, mean 19.05 days, maximum 51 days. Three actually reach 20% net initial
margin ROI; the best/worst realized base margin returns are +63.8165%/-17.6750%.
The strategy has some exposure on 399 calendar days and none on 575, including
the two unavailable days. It is a multi-day regime strategy, not a model that
produces a 20% margin winner every day or hundreds of small trades.

The sampled maximum account drawdown is 10.7451% base / 10.9142% stress.
Every held minute's mark extremes and exact funding pass the conservative
isolated 2x collateral screen, but historical liquidation ordering/tier rules
remain unproven. Published gross-exposure peaks are sampled after execution:
about 0.374816 base / 0.374902 stress; these are not continuously enforced or
intraminute exposure limits. Hourly account drawdown is not tick drawdown.

## What prevents calling this a proven winner

**The stressed 2025 profit is only 0.333329 quote units**, +0.00303539% on
that fold's starting equity. Its base return is +0.19979947%. Under the recorded
fixed quantities and clocks, roughly 0.10046 additional bps of friction per
executed reference notional would consume the stressed year's profit. This is
a first-order break-even diagnostic over 33,178.9075 reference turnover, not a
new rerun, fee setting or capital-feedback simulation. Native spread, timing,
rounding or funding differences could therefore change that year's sign.

Deterministic 2,000-resample circular-block intervals, with predeclared 7-day
blocks and a 30-day sensitivity, cross zero in every fold. For example, the
selected candidate's stressed 2025 annualized arithmetic mean interval is
approximately -11.17% to +10.68% at 7-day blocks, and -7.96% to +8.75% at
30-day blocks. These are descriptive dependent-history intervals, not compound
return forecasts or post-search significance. The small exposed sample cannot
establish positive expected live returns.

Directional controls under the same costs are:

| Control | Net base % | Net stress % | Collateral status |
| --- | ---: | ---: | --- |
| `cash` | +0.0000 | +0.0000 | screen qualified |
| `persistent_long` | +9.7092 | +9.6189 | screen qualified |
| `persistent_short` | -9.6907 | -9.7714 | counterfactual risk invalid |
| `regime-h720-plain__matched_long` | +2.8037 | +2.1191 | screen qualified |
| `regime-h720-plain__matched_short` | -5.7388 | -6.3700 | counterfactual risk invalid |

Persistent controls enter once per source segment at the same entry-volatility
budget; matched controls retain every candidate entry/exit clock and absolute
risk weight but keep one direction. The matched-short control breaches the
isolated collateral screen, including development. Its retained continuation
is a counterfactual, not executable comparator profit. Therefore the positive
candidate is **not alpha validated**. A losing or invalid control cannot
certify timing skill. The profit/candidate classification is distinct from
statistical, comparator and native-execution admission.

## Reproducibility and independent acceptance

Actual clean merged source:
`bd4bf7c316daed18fbaf46429b579c6266ef494a` ([PR247](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/247)).
The source and protocol received independent nonauthor code/security and
quantitative reviews before scoring. Local and current-ready CI passed all
4,562 Python tests, including 62 new deterministic regressions, with global
format/lint/types. Existing scoped CI was used; no new jobs or dependencies.

Input SHA-256:
`ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`.
Plan SHA-256:
`a84f2b270bea433d23ae934f7e0a6f4dd6ffc81574b2575c8846cfb938a6222d`.
Result JSON SHA-256:
`7a395e2ced136ddb6dee01a6622970100386ee418f1a23dc69b0ffc609803a2f`.
The create-only private result bundle contains 187 files / 49,188,425 bytes;
bundle-manifest SHA-256:
`9d859003c59b956f430fc18db8c112739df0e42ce2886f298580ff1c6169cf20`.
All actual ledgers, trades, episodes, risk records and pre-case requests remain
outside Git under the private VM report root
`/home/chupa/.codex-workspaces/reports/hyperliquid/2026-11-13-regime-continuation/`.

The bounded offline run took 56.15s, at most two assigned VM CPUs and about
1,029.87MiB peak RSS. Actual HEAD, clean source, 135 source/plan identities and
input bindings were checked before and after. Completion is `complete` with
zero failed configurations and controls. No archive or NAS runtime was changed.

Independent full native-data/accounting reconstruction checked all 90 cases,
789,180 ledger points and 38,856,176 assertions, with no discrepancies within
recorded tolerances. It independently reads original ZIP/CSV/REST witnesses,
reconstructs fixed-unit cash, fees, funding, mark-based margin, every held-minute
collateral event, fold statistics and development selection without importing
the financial evaluator. Maximum recorded arithmetic deviation is 6.82e-13
quote units. Acceptance receipt SHA-256:
`caaaa7bd55772ee62e10ce491c33489d504a125778e167c939d20f9ff4e0b1e4`.

A second independent statistical/selection review reconstructs all 30 carried
accounts and 971,670 checks, including the 974 daily returns/account, global
pre/post-execution drawdown, fold Sharpe, shared block intervals, paired controls
and selected name. Final receipt SHA-256:
`859326b188d6185aeb306429b9eb52d14f1d531f1cc31a30fd18dd539f4612c4`.
The audit's earlier upper-middle hold-duration diagnostic was corrected to the
conventional 17-day median and the entire audit rerun; originals are retained.
No evaluator output, financial result or candidate eligibility changed.

## Frozen next step, not a promotion

The selected rule and source identity are frozen in a private candidate lock,
SHA-256 `9d3f752b7124a50497d6f2954409b802fdb08b32f997f68cb1884d571c71d9e9`.
[Follow-up #249](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/249)
qualifies native Hyperliquid execution/funding and a separately preregistered,
genuinely future validation period. Keep external Binance USD-M signal identity;
do not silently replace it with spot or HL features. No future test window or
strategy runtime is started by this result. Any native adapter/cost contract
must be reviewed and frozen before the confirmatory data are observed.

In parallel, the [micro source investigation](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/244#issuecomment-6862063498)
found slow full-book cadence incompatible with the old one-second execution
proof. A short credentialless fast-L2 probe succeeds, and [source PR248](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/248)
is draft; it has not changed the running collector. Depth consumption, partial
fills and carried residual exposure still need their own contract and financial
study. Neither a positive daily proxy nor a source feature admits PAPER strategy
operation, TESTNET orders, SHADOW, LIVE or real capital. The goal stays active.
