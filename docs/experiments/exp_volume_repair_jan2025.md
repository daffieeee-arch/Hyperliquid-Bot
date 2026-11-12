# January 2025 volume accounting repair

Frozen specification, 2026-11-12; issue #222. This is **one retrospective
accounting reconstruction**, not a new untouched test or a strategy promotion.
The previously reported November results remain superseded. Recovered scripts
are evidence only and are never imported or executed.

## Question and identity

Does the already-declared secondary one-hour-delayed continuation rule retain
its result when positions are fixed quantities, funding uses actual settlement
notional, costs change cash and risk includes open positions? The original
primary spike-close entry is a different execution model. This calculation
cannot certify that primary result or establish a profitable Hyperliquid strategy.

Use the recovered 40-symbol universe minus **ICPUSDT**, excluded by the operator.
The 39-symbol selection is a new input identity, with the original survivorship
bias retained explicitly. Source input acquisition was frozen before download:
plan SHA-256 `ee60c7a415a7f80fd0cc6cb6f6c8f6e071f533a3c5122cd837608ad494516a67`.
The new bundle manifest SHA-256 is
`7f523c1a1e68fbd894cdd409c9d69dcbb70f3205b1125231c9eb30b63e90ae9b`.

## Periods and point-in-time information

- Warm-up: December 2024 hourly USD-M klines; exactly 720 contiguous preceding
  hours enter each volume median, excluding the current bar.
- Exposed calculation: **2025-01-01 00:00 through 2025-01-31 23:00 UTC**.
  January is already-exposed validation history. No training, parameter selection
  or untouched OOS is performed here. The Nov2–5 prospective holdout is excluded.
- A native candle closing at `close_time` becomes available at `close_time + 1`
  millisecond. A spike-hour decision is available at its canonical hour end;
  entry is exactly one hour later, using that later hour's trade-close proxy.
- Decisions require the spike bar's open within the calculation period. Both
  entry and the full four-hour holding window must fit inside the closed end.
  Boundary exclusions are counted without using later returns.

## Frozen rule and portfolio

`V = quote_volume / median(previous 720 hourly quote_volume)` and
`I = 2 * taker_buy_quote_volume / quote_volume - 1`. Select `V >= 5` and
`abs(I) >= 0.2`; buy for positive imbalance and sell for negative imbalance.
No threshold, horizon, direction or leverage sweep is allowed.
Inclusive thresholds are compared algebraically before division, so an exact
60% taker-buy fraction is not lost to a rounded `0.199999...` imbalance.

Hold each admitted position for four hours. Keep the original per-symbol busy
rule: a new signal's decision must be strictly after the previous candidate's
exit. This prefilter runs before the shared portfolio's capacity filter, even
when a candidate was subsequently refused by that filter. Sort candidates by
`(entry, exit, symbol)`; process exits before entries; retain at most ten open
symbols. Every new entry has notional 10% of the same timestamp's pretrade marked
equity. Simultaneous entries share that pretrade sizing point. Exits and other
held quantities are not rebalanced. Initial research equity is **10,000 USDT**;
fixed scale is one. No borrowing or margin/liquidation safety is implied.

## Price, funding and cash accounting

The existing corrected ledger is reused. Trade-close execution reference prices
and perpetual mark valuations are separate. Each side pays five basis points
taker fee on actual adverse fill notional and two basis points adverse slippage
from the execution reference. A flat-price round trip therefore costs about
14 basis points of traded notional. These assumed costs include a spread proxy;
there is no historical order-book fill/depth or partial-fill proof.

Value equity at every canonical minute close and every exact native funding
event, including the final charged close. Between settlements, explicit zero
funding coverage is certified only after validating the complete native January
census against the independently fetched funding response. Preserve funding
event milliseconds; never floor the observed 0–15 ms offsets. The REST response's
positive finite `markPrice` is the settlement mark, not a kline trade close.
Funding is applied to quantities held immediately before executions at the same
timestamp. Entry exactly at a settlement excludes that payment; exit includes it.
A settlement a few milliseconds after entry is a different later event and pays.

Other symbols at an asynchronous settlement use their most recent canonical
minute mark, no more than 60 seconds old, and never a future minute's close.
The flat initial frame may lack preceding marks; held or traded positions may
not. Missing hours, minutes, settlement events, prices, invalid numbers, source
identity changes or ambiguous duplicates refuse the entire run. Read and hash
all bounded source bytes before and after; native archives must match official
checksum files. No source is modified.

## Output, checks and limits

Record source/code identities, the complete fixed-quantity marked ledger,
executions, all signal/capacity/boundary counts, net return, sampled marked
drawdown, fee/slippage/funding cash contributions and daily returns from marked
equity endpoints. The cash benchmark is zero return. Run a separately reported
two-times fee/slippage stress on the identical frozen entries, without selecting
between results. Do not annualize one month into a promised return.

Tests use synthetic cash counterexamples, native-time funding at entry/exit,
separate reference/mark prices, intratrade losses, exact source/census checks and
holding-window boundaries. The bounded offline runner has no venue/network API,
credentials, order submission or promotion path. Its output always says
`promotion_decision: forbidden` and `economic_assessment: accounting_only`.

The universe has survivorship bias; month and rule were previously exposed;
minute closes miss intraminute risk; close fills are proxies; fee tiers, spread,
queue/depth, liquidation, collateral and operational loss remain unvalidated.
This repair is useful evidence about accounting, not proof of an executable edge.

## Reviewed execution

Run from the reviewed commit with the project-pinned Python environment. The
following paths are examples; choose new directories under an existing private
local output parent. Input storage may be read-only NFS; output creation is
restricted to supported local filesystems. Copy accepted outputs to the archive
afterward, independently of the collector's runtime directories.

```bash
PYTHONPATH=src .venv/bin/python -m research.volume_repair prepare-index \
  --archive /mnt/hyperliquid-archive/vps-chupa/hist-archives \
  --acquisition /mnt/hyperliquid-work/research/volume-repair-jan2025-20261112 \
  --output /absolute/private/new-volume-source-index
sha256sum /absolute/private/new-volume-source-index/input.json
```

Preparation reads hashes and fixed metadata, without decoding financial values.
Record the printed SHA-256 before scoring; retain the exact reviewed source SHA.
Then execute the single frozen replay:

```bash
PYTHONPATH=src .venv/bin/python -m research.volume_repair run \
  --input /absolute/private/new-volume-source-index/input.json \
  --expected-input-sha256 RECORDED_INPUT_SHA256 \
  --source-commit REVIEWED_SOURCE_COMMIT \
  --output /absolute/private/new-volume-result
```

The runner verifies all 351 bound source files before use and after calculation.
It retains the input index, ledger/result and completion marker, written last.
No original archive or recovered script is changed. Existing CI's Python area
covers this runner and its regression tests; no parallel research CI is needed.
Native field and checksum references are the
[Binance public data documentation](https://github.com/binance/binance-public-data)
and [funding history API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#get-funding-rate-history).
