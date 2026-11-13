# Locked BTC regime: positive recent diagnostic, still unadmitted

Checkpoint 2026-11-13; delivery [#251](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/251),
programme [#237](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/237).

The unchanged `regime-h720-plain` candidate returns **+2.3317% net account
equity at base costs and +2.2199% at doubled costs** over 73 recent completed
days. It closes three simulated trades, including a November short. This is
positive exposed-history evidence on Binance USD-M inputs, not untouched OOS,
established statistical alpha, executable Hyperliquid profit or trading
admission. The earlier nearly flat stressed 2025 result remains unchanged.

## Fixed question, inputs and execution

The [registered protocol](../experiments/exp_regime_recent_diagnostic_20261113.md)
was committed, independently reviewed and merged before financial scoring.
Only the previously development-selected 720-hour plain candidate was tested;
no horizon, threshold, direction, cost, sizing or leverage was retuned. The
alpha and accounting source remain byte-identical to the
[primary study](cost-aware-regime-result.md). This adds zero unique strategies;
the programme remains at **32/40 configurations across four mechanism families**.

Period: September 1, 2026 00:00 UTC through November 13, 2026 00:00 UTC,
73 complete days ending November 12, with August hourly warm-up. All 105,120
minute trade candles and 105,120 minute mark candles qualify without gaps.
Native hourly/minute trade closes agree. Qualified official archives/checksums
and original response identities bind every input. September/October funding
has 183 exact native CSV/REST witnesses; 36 November primary events use the
explicitly registered complete REST-only contract. The daily funding ZIP and
checksum 404 responses remain retained; no witnesses or candles were invented.

Completed-hour signals decide at 00:00 UTC; entries/closes use the next native
00:01 trade-minute reference, with opposite entry at 00:02. The rule admits
absolute normalized momentum at least 1, holds while the sign agrees and its
absolute score stays at least 0.5, otherwise closes or reverses. Entry exposure
is min(0.5, 0.10 / lagged annualized volatility); quantity stays fixed until close.

Each strategy/control cost case starts its own 10,000-quote account. Results
are not stitched to the primary study's equity or open positions. Fees/friction
are 4.5/2 bps per side at base and 9/4 bps at stress, with independently carried
capital. Exact Binance funding uses its native settlement mark. The actual
funding event exactly at END is charged on held quantity before the mandatory
terminal close. That terminal liquidation is an experiment boundary, not a
new daily profit-taking rule or a signal exit.

## Candidate and every control

Returns and drawdowns below are percentages of marked **account equity**.
All twelve cost cases complete with zero calculation failures and no
counterfactual collateral failures. Controls share the registered clocks and
risk conventions; persistent controls and matched controls serve different
comparisons and are not optimized alternatives.

| Account | Closed trades | Net base % | Net stress % |
| --- | ---: | ---: | ---: |
| Locked `regime-h720-plain` | 3 | +2.3317 | +2.2199 |
| Cash | 0 | 0.0000 | 0.0000 |
| Persistent long | 1 | -2.7505 | -2.7836 |
| Persistent short | 1 | +2.6843 | +2.6513 |
| Matched long | 3 | -2.1880 | -2.2966 |
| Matched short | 3 | +1.9494 | +1.8379 |

Base final equity is 10,233.1676; stress final equity is 10,221.9892. Base gross
price PnL is +249.5819 quote units. Subtract 7.6486 fees and 3.3994 friction,
then add signed funding -5.3662 to obtain net +233.1676. At stress the respective
values are +249.4473, 15.2916, 6.7963 and -5.3702, giving net +221.9892.
Long episodes contribute +11.1031 base / +4.0010 stress; the short contributes
+222.0645 / +217.9882. Most recent profit therefore comes from one short.

The model has exposure on 44 calendar days and is flat on 29. Its sampled
maximum account drawdown is 2.4965% base / 2.5691% stress. Every held native
minute mark extreme and funding event passes the conservative isolated 2x
collateral screen. This is not venue liquidation proof, continuous exposure
enforcement or tick-level drawdown. After-execution sampled gross-exposure
peaks are approximately 0.333313 / 0.333382 of marked equity.

## The three trades and the requested margin target

Times below are UTC. Net margin ROI includes actual entry/exit costs and
funding, divided by immutable initial margin: absolute quantity times entry
mark divided by two. It is not account return or a fill-notional leverage proxy.

| Side | Entry | Exit | Net margin base % | Net margin stress % |
| --- | --- | --- | ---: | ---: |
| Long | Sep 1 00:01 | Sep 21 00:01 | +5.7754 | +5.5110 |
| Long | Sep 22 00:01 | Oct 4 00:01 | -4.7016 | -4.9589 |
| Short | Nov 3 00:01 | Nov 13 00:00, terminal close | +14.2071 | +13.9562 |

Two episodes win and one loses. **Zero of three achieves 20% net initial-margin
ROI** under either cost case. This multi-day strategy does not produce a daily
20% winner or hundreds of 1–2% margin trades. The separate micro investigation
retains its [prior failed-exit and cost evidence](two-model-momentum-micro-result.md);
faster capture is not itself an improved trading signal.

## Evidence strength and next decision

The candidate beats the matched-long and matched-short controls in this
interval, but the persistent short earns more: +2.6843% base / +2.6513% stress.
This does not establish predictive advantage over simply being short in this
particular market. Every candidate and paired matched-control 7/30-day
descriptive block interval includes zero. There are only three strategy trades.

At base, the 7-day interval for annualized arithmetic mean daily returns is
approximately -13.95% to +41.41%; the 30-day sensitivity is -5.74% to +33.55%.
At stress they are -14.66% to +40.70% and -6.38% to +32.78%. These are fixed
2,000-resample dependent-history descriptions, not compounded return forecasts,
independent-trade confidence statements or post-search significance tests.

This history was already exposed through prior captures and market context.
Fresh downloads do not make it an untouched holdout. Native Hyperliquid spread,
depth, delay, precision, fills and hourly oracle-based funding remain separate
qualification work in [#249](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/249).
No future window is reserved by this report. The short result is encouraging
enough to retain the locked candidate for that work; it does not erase the
fragile stressed 2025 result or justify increasing leverage. No PAPER strategy
admission, TESTNET, SHADOW, LIVE, real-capital action or deployment follows.

## Reproducible identities and actual run

Study `btc-locked-regime-recent-diagnostic-20261113-v1` ran on clean merged
source `723f5e61e8a3b31640ed518369d96de7d9148625`, after 4,653 local and current
ready-head CI tests, independent nonauthor code/security and quant reviews,
and the [separate merge gate](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/254#issuecomment-6863917424).
The bounded offline VM run took 3.6733 seconds with 208,392 KiB observed peak RSS,
two assigned CPUs and a 6 GiB address-space bound. It did not change the NAS
collector or original archives.

- Frozen candidate lock SHA-256: `9d3f752b7124a50497d6f2954409b802fdb08b32f997f68cb1884d571c71d9e9`.
- Protocol SHA-256: `8587fe1b5aff6564484fa5a0d5f8fa0aa43bfaf696abd6aadf3730d9f8803664`.
- Input inventory SHA-256: `470a23eebf9555f93670ae73aded702131c89cceb37b30bc05c6db4ab7cf32d3`.
- Actual `result.json` SHA-256: `28f88cf55a3a9310699dd1a7867749be09def56162ad25c639886bd47041eb24`.
- Complete 29-file, 819,134-byte result bundle manifest SHA-256: `f51019527a7f254efe4f4bb59e37c60127c59f99870370d7dd6dbe89e895efa0`.

The private create-only bundle retains all twelve requests and full ledgers,
the selected schedule, source qualification, claim, completion and summary.
An independent nonauthor audit reconstructs all twelve native ledgers, 24,132
equity points, 44 fills, 22 strategy/control episodes, frozen daily decisions,
held-minute collateral screens and 7/30-day statistics without importing
production strategy or accounting helpers. It passes 1,574,316 native/financial
assertions plus 230 post-audit hash/size checks, with zero discrepancies within
recorded arithmetic tolerances. Its documented arithmetic helpers are attributed
to the earlier independent auditor; this is a new audit of this actual bundle,
not a claim to repeat the earlier study's audit work.

Independent acceptance SHA-256:
`bd34aa593bcfa8d59c35f5c94d7eb3f456df6688c169fc35d5539557cd30835c`.
The audit script's initial pre-execution syntax error is retained with its log;
it was corrected before the first executed financial audit. No strategy result,
source contract or selection was changed or rerun to obtain this acceptance.
