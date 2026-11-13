# Locked BTC regime: recent exposed diagnostic

Registered before financial scoring, 2026-11-13; issue #251, parent #237.
Identity: `btc-locked-regime-recent-diagnostic-20261113-v1`.

## Question, frozen policy and budget

Does the already development-selected `regime-h720-plain` rule retain positive
net results in the more recent market, under the same two cost assumptions?
This is a fixed-candidate diagnostic, not another selection or threshold search.
The positive [primary result](../research/cost-aware-regime-result.md) and all
earlier failures remain unchanged. In particular, its nearly flat stressed
2025 result is not repaired, hidden or replaced by recent profits.

Candidate lock SHA-256:
`9d3f752b7124a50497d6f2954409b802fdb08b32f997f68cb1884d571c71d9e9`.
Frozen alpha/ledger source: `bd4bf7c316daed18fbaf46429b579c6266ef494a`.
Require `regime_momentum.py` and `repaired_accounting.py` to remain byte-identical
to those reviewed implementations before and after calculation. Only the one
720-hour plain candidate may execute as a strategy; no runner-up, confirmed,
short-only, alternative horizon, sizing or leverage variant may be added.
This adds **zero unique strategies**; cumulative programme count stays 32/40,
four mechanism families. Control directions and paired costs are not new alpha.

Use the [original regime contract](exp_cost_aware_regime_20261113.md) unchanged:
daily completed-hour Binance USD-M BTCUSDT features; 720-hour compensated
normalized momentum; absolute admission 1 / holding 0.5 hysteresis; no slow
confirmation; volatility-sized entry min(0.5,0.10/sigma720); fixed held quantity;
00:01 native minute entry/close and 00:02 opposite re-entry. No bracket,
same-direction rebalancing, leverage fitting or mandatory daily trade.
Initial margin is immutable absolute quantity × entry mark / 2. Report actual
20% net-margin hits diagnostically, never as account returns or a daily promise.

## Period and source contract

September 1, 2026 00:00 UTC through November 13, 2026 00:00 UTC: **73 complete
calendar days**, ending with November 12. The dates were fixed from the prior
study end/current completed-day boundary before acquisition or financial labels.
August 2026 hourly trade closes provide full 720-hour warm-up.

**This recent history is exposed and diagnostic, not untouched OOS.** Existing
September captures and November market context were already observed. A newly
downloaded file cannot become a fresh holdout merely because this evaluator
has not used it. This does not reserve or start #249's future validation period.

Qualified source inventory SHA-256:
`470a23eebf9555f93670ae73aded702131c89cceb37b30bc05c6db4ab7cf32d3`.
It binds official monthly September/October and daily November 1–12 trade-hour,
trade-minute and mark-minute archives plus their filename-bound official
checksums, original request/response identities, and August warm-up. All native
OHLC/time grids qualify before scoring: 1,752 primary hourly closes and 105,120
each of minute trades/marks, with no gaps and exact hourly/minute close agreement.
There are 44 primary ZIP sources, plus the reused verified warm-up ZIP. No
missing candles may be forward-filled or retrospectively quarantined by PnL.
A changed/missing/integrity-invalid source fails the attempt, never a green skip.

Funding is explicitly a **new** input contract, not an override of the earlier
all-period CSV requirement. A bounded native Binance funding REST response
provides the complete 219 primary settlements, exact milliseconds/rates and
positive actual funding-charge settlement marks. September/October's 183 native
CSV witnesses must match REST time/rate exactly using Decimal. November's 36
primary records use a complete fixed eight-hour native REST census; the twelve
daily funding archives and twelve sidecars return 404, and every original error
response is retained. Do not invent CSV witnesses or use a candle close as a
settlement mark. Verify all primary slots, the actual terminal slot and a later
native boundary pad; preserve response order, identity and no-truncation proof.

The actual November 13 terminal funding event occurs **exactly at END**. The
terminal frame therefore includes that rate/settlement mark: the unchanged
ledger charges the units held before the charged terminal close. Do not round
away this event or move it after the close. A nonzero-millisecond event after
END would instead be outside held exposure; source tests cover both cases.

All data remain Binance price/funding proxies. Hyperliquid hourly oracle-based
funding, executable spread/depth, precision, queueing and actual fills are not
certified by these inputs. Neither archive minute fills nor coarse boundary
closes certify real-world millisecond ordering around funding.

## Replay, controls and financial reporting

One complete source segment; no calendar-year/monthly resets or gap repairs.
Each strategy/control cost case starts a **separate 10,000-quote simulated
account**. Do not stitch it to the primary period's profits or claim continuous
portfolio performance across that earlier experiment's terminal close.
Every requested entry/exit uses a native trade-minute reference; marks value
equity and actual settlement marks determine Binance funding notional.

Regular hourly valuation plus daily 00:01/00:02 and exact funding frames.
Every held minute's mark low/high and funding receives the same conservative
isolated 2x / current 1.25% maintenance screen. Preserve all counterfactual
collateral failures. Hourly drawdown and after-execution sampled gross exposure
are not tick maxima or continuously enforced caps.

Base fees/friction per side: 4.5/2 bps. Stress: 9/4 bps, identical frozen signal,
clock and risk-weight schedule, independently simulated capital and quantities.
Controls: cash; persistent long/short at first volatility-qualified entry; and
matched long/short sharing the candidate's exact entry/exit/risk-weight schedule.
No touched-limit maker fills, invented queue priority, free reversal or reduced
costs after labels. A counterfactual control cannot certify alpha.

Reuse the corrected ledger and existing account aggregation with an explicit
73-day calendar/fold context. Default original-study aggregation remains
unchanged and tested. There is only one `recent_diagnostic` marked fold: daily
UTC equity snapshots, all 73 returns, fees/friction/native funding, pre/post-fill
sampled drawdown, after-fill exposure, long/short contributions, actual margin
ROI and target counts, hold times and flat/active days. Never apply the earlier
974-day denominator or select a new development winner here.

Report 2,000 deterministic circular-block resamples, seed 20261113, 7-day blocks
and a fixed 30-day sensitivity, with shared indices for paired daily differences
against matched controls. These exposed dependent-history intervals are
descriptive; do not choose a favorable block length, claim independent trades
or infer post-search significance from a short trending interval.

Diagnostic disposition is failed if calculation/source/execution proof fails;
counterfactual if either strategy cost case breaches collateral; insufficient
if no complete strategy trade; positive only if both complete qualified cost
returns exceed zero; otherwise not positive under both registered costs.
This disposition never admits a strategy or overrides the primary result.
All twelve cost cases, including invalid/failed controls, remain visible.

Legacy whole-segment exceptions do not serialize partial state. Preserve the
full request/source identities and exception, mark failed-period exposure
unknown, keep final equity/return null and block any reset/resume. Do not
substitute initial capital for equity at failure or count unprocessed days as
flat. Never select a different strategy when this candidate fails.

## Execution and acceptance

Before financial scoring: commit/review/merge the final plan, input adapter,
CLI and the minimal period-context extension, with deterministic source,
accounting, terminal-funding, no-reselection and PAPER-refusal tests, scoped
lint/types, independent nonauthor code/security/quant review and existing CI.
No new CI job, dependency, database, broker or production strategy framework.

Bind actual clean Git HEAD, all imported source/plan/input/lock identities and
the old frozen alpha/ledger bytes before and after the bounded local VM run.
Create private outputs only once; preserve every ledger, request, failure and
completion record. Independently reconstruct actual financial results before
publication. Keep original NAS inputs, archives and collector unchanged.
No PAPER strategy admission, TESTNET, SHADOW, LIVE, credentials, real capital,
live risk-limit change or deployment follows from this diagnostic.
