# January 2025 delayed-volume accounting result

Checkpoint: **2026-11-13**. Disposition: **accepted accounting only**.
Six trades in exposed retrospective history do not establish profitability or
admit a Hyperliquid trading strategy.

## Fixed calculation

The [frozen reconstruction protocol](../experiments/exp_volume_repair_jan2025.md)
defines one previously declared secondary continuation rule: volume at least
five times the preceding 720-hour median, absolute taker imbalance at least
0.2, entry delayed one hour and a complete four-hour hold. Threshold comparisons
retain exact native rational values. Each entry is 10% of current marked equity,
with at most ten positions, fixed quantities between entry/exit and scale one.

The calculation covers **Jan1 00:00–Jan31 23:00 UTC, 2025**, with December warm-up,
on 39 surviving Binance USD-M symbols excluding ICP as the operator requested.
January was already exposed; no training, parameter selection or untouched OOS
occurs here. This input identity and delayed execution rule differ from the
original 40-symbol spike-close primary calculation.

Base assumptions charge five basis points taker fee on adverse fill notional
and two basis points slippage per side. Hourly trade-close fill references and
minute perpetual mark valuations are separate. Funding preserves each native
event's exact milliseconds and settlement mark. The predeclared stress doubles
fee/slippage on the same windows; the cash benchmark returns zero.

## Result

The scorer ran once from independently reviewed, CI-verified merged source
`668a9d0f01d59865aa9203a54e6874fab63b74a9` in
[PR225](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/225).

| Measure | Base assumed costs | Predeclared double-cost stress |
| --- | ---: | ---: |
| Initial simulated equity | 10,000 USDT | 10,000 USDT |
| Final simulated equity | 10,018.985467 USDT | 10,010.562429 USDT |
| Net return | +0.1898546731% | +0.1056242932% |
| Sampled marked maximum drawdown | 0.4040797198% | 0.4181421754% |
| Complete trade windows | 6 | Same 6 |
| Executions | 12 | 12 |

No additional parameter, direction or period was selected from these results.
The original November claims and full-period/54-variant family remain recorded
in the [recovery/correction record](november-session-repair.md).

## Independent acceptance and preservation

A separate verifier imported no scorer/project module and did not rerun the
scorer. Its native-data/Decimal oracle checked:

- all 351 source bindings before and after, including archive checksums;
- 58,032 hourly rows, 1,740,960 minute marks and 3,720 exact native funding events;
- the same six windows, all 12 fills and fixed quantities;
- all 44,615 cash/equity/position/drawdown points and 31 daily endpoints;
- both cost cases, all charged entry/exit executions and final empty positions.

No discrepancy was found. Result, input index, independent verifier/report and
receipts are preserved under logical namespace
`volume-repair-jan2025-result-20261112` in both existing research/archive stores.
An independent reader accepted each complete nine-file, 8,731,719-byte package,
its private ownership/modes, all hashes and the finalization marker.

| Artifact | SHA-256 |
| --- | --- |
| Input index | `fcc687f5272ab355579f4f9dfe990a4f7553101427b87f32ebf4608e49ce7172` |
| Result | `355485ef5ca7b404c22f4b49611a2e5c19a9b4315e987df6d1ca26ccd573d0c7` |
| Preserved package manifest | `5bb7f1f8e78b9c347fd68f444b51d5a80aeff2fadf72a98990220310a9ac3d2c` |

The detailed acceptance is recorded on
[#222](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/222#issuecomment-6839481021).

## Limits and next gate

Six exposed trades and a survivor universe are insufficient evidence for a
profitable strategy. Minute marks miss intraminute risk; trade closes are fill
proxies, and historical depth, partial fills, collateral and liquidation remain
unvalidated. These Binance assumptions do not establish executable Hyperliquid
economics. The month is not annualized into a return target.

The November prospective holdout was neither opened nor scored. This result
does not restore the original primary, clear S4 funding/economic gates or
authorize PAPER/SHADOW/TESTNET/LIVE promotion. Any confirmatory experiment needs
its own fixed design, qualified venue economics, sufficient samples and fresh
OOS.
