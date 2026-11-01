# Results: BTC month-turn calendar proxy v1

The single [registered hypothesis](exp_btc_month_turn_proxy.md) completed once
on 2026-11-01 at 19:21:53 UTC. Outcome: **`historical_no_edge`**, promotion
`forbidden`. This fixed calendar family stops here; do not retune its window,
sign, asset or cost assumptions on these results. No candidate advances to
forward testing or PAPER admission.

## Actual result

There were 44 matched validation pairs, 11 in each of the four frozen folds
covering 2021 through Dec 30, 2024. Each primary position was long for 96 hours across
the UTC month boundary, compared with that target month's 15th-to-19th-day long.
Both used one unit of their own entry notional and included native funding.
Means below are arithmetic returns per modeled trade, not annualized returns,
compounded portfolio returns or actual account PnL. Stress changes both flat
costs and the preregistered adverse funding treatment.

| Cost/funding stress | Pairs | Primary net mean | Midmonth control net mean | Paired excess mean | Primary p | Paired excess p |
| --- | --- | --- | --- | --- | --- | --- |
| 1.0x | 44 | -0.470407% | -1.068240% | +0.597833% | 0.722096 | 0.351039 |
| 1.5x | 44 | -0.620626% | -1.211910% | +0.591284% | 0.784960 | 0.352687 |
| 2.0x | 44 | -0.770162% | -1.354620% | +0.584458% | 0.839783 | 0.354414 |

The primary mean is negative at every stress; at 1x, profit factor is 0.791809
and win rate is 38.636364%. The paired excess is positive but fails its fixed
p<=0.025 threshold at every stress. The primary and paired validation gates
therefore both fail. Neither an alternative window nor a favorable stress was
selected afterward.

Cash has return 0. The descriptive continuous validation buy-and-hold benchmark
has net unit-entry-notional return 1.223220820323332 at 1x, 0.7309960443236586 at 1.5x
and 0.24454903619017515 at 2x over its entire 35,038-hour holding interval. Those
whole-interval values are not per-trade means and are not selection inputs.

## Historical-test boundary

Validation was persisted before any possible test request. Its gate failed,
so the Jan 2025-Aug 2026 historical test's financial columns and buy-and-hold
benchmark were not decoded by the runner or the independent result reviewer.
The timestamp census and prior source qualification cover the complete input
range; this does not make that already exposed history untouched OOS. No future
OOS period was assigned. The separate Nov 2-5 Spot-flow study remains unchanged.

## Reproducible evidence

- Source merge: `d495887a586c65d6b70adfaf40aa7936de94cfab` ([runner PR215](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/215));
  tree equivalent to independently reviewed head `aa55b715fc1e8cb7a67cc548eab0b043838bf16f`.
- Registration [PR213](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/213),
  canonical spec SHA256 `bb9ee17c4daf86fdcd682ad3e72ef71df0c605e2ece0c5fdf1da80bf53e1dd56`.
- Complete Jan 2020-Aug 2026 source census: 80 hourly parts / 58,440 bars and 80
  native funding parts / 7,305 events. Original CSV/Parquet values and official
  archive checksums were independently accepted before execution.
- Exact input-manifest SHA256: `bd0c4771e5454e02ec94401ecb7f371433181d58baa1ce87cbb1b6ba3c01670c`.
- Explicit operator-GO SHA256: `b484961c04f6fa3b7fca47ee7bbcee5fbaee30d44bc436afa6b764cdd0e9c758`.
- [Original result bytes](exp_btc_month_turn_proxy.result.json), SHA256
  `9c74edb5fdebdc978b8dbe88b49616cd0dca9b84bf8231362f7329b048b960b5`.
- [Original input-lock bytes](exp_btc_month_turn_proxy.lock.json), SHA256
  `b1526e1fd676cd1db60dec328ec9ce8e4acf14702f278d128b5be518743be7c8`.
- Persisted validation SHA256: `6f941a2e8ab4af6b2a68f2a4960a938ffc5955f6babcf8d672d899b82f8613f2`.
- Independent actual-result acceptance SHA256:
  `2a3cd3a8a9b2e0706c133fb840359eabdbdc6bb77f17b4de34ae4f6caf9002ff`.
  The nonauthor generated the 44 calendar pairs directly, decoded only 120
  pre2025 monthly parts, and independently recomputed all 17 metrics for each
  primary/control/excess series at all 3 stresses. Maximum absolute numerical
  discrepancy was 2.665e-15. No producer rerun or test-period financial decode.
- Actual bounded process exited 0 in 3.56 seconds without stderr or timeout.
  Full spec/input/GO/lock/validation/result artifacts and external acceptance
  records are retained outside Git. Publication evidence is tracked in
  [issue214](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/214).

## Limits and runtime implications

This is Binance BTCUSDT USD-M history in USDT, not Hyperliquid economics or a
USDC conversion. Fills use hourly traded closes after one-hour latency, not
executable historical quotes or queue simulation. Flat costs are 13/19.5/26 bps
roundtrip on entry notional rather than actual exit-notional fees. Native
funding times are preserved, but cashflows use the frozen conditional hourly
trade-close valuation rather than native settlement oracle/mark notional.
Forty-four pairs and their dependence limit inference. This failed fixed rule
does not establish that every different BTC mechanism lacks an edge.

All top-level and nested promotions remain forbidden. Source environment is
DEV, image digest is null and mode is PAPER. No trading service, collector
image, account, credentials, live limits, existing archive or prospective timer
was changed by this research run.
