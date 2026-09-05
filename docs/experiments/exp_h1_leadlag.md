# Experiment H1 — Binance impulse / Hyperliquid lag (WP-Q2)

PAPER / Quant research only. This document **pre-registers** the H1 lead-lag
scaffold. It is not an edge claim, not a promotion memo, and not a capture or
execution runbook.

Runner: `python -m hyperliquid_bot.exp_h1_leadlag`

The runner consumes a WP-Q1 `panel_hl_binance` panel (`panel.parquet` +
`panel-summary.json`) or, as a convenience, builds that panel first from
retained DATA-1A / DATA-1F reconstructable run ids. It still fails closed when
the panel is missing, the panel summary is `not_enough_data`, usable overlap
after the gap mask is too small, or any predeclared horizon has too few trades.

Allowed verdicts are **`noise`** or **`not_enough_data` only**. The runner
must refuse or raise if anything tries to assign `edge`. A future rerun that
wants to be cited must write a registry record from the committed template;
this scaffold never auto-promotes.

H2 basis, H4 imbalance, EUR venues, lookback fitting, and Δ search are out of
scope.

## Mechanism

Hypothesis under test (descriptive only): a Binance BTCUSDT impulse observed
on the WP-Q1 receipt-clock panel is followed by a Hyperliquid BTC-PERP
response at a predeclared lag Δ.

The join clock is the panel bucket clock (`bucket_utc_ns` =
`received_utc_ns // bucket_ns`). Venue event times are not used. Default
WP-Q1 `--bucket-ms` is **1000** (1 second).

## Predeclared Δ set (exactly 3)

Δ is counted in **panel bucket units**, not fitted, and not selected after
seeing results. The committed set is:

| Δ (buckets) | Duration at default 1s panel | Role |
| --- | --- | --- |
| 1 | 1 second | Near-term microstructure lag |
| 5 | 5 seconds | Short continuation after the impulse |
| 30 | 30 seconds | Slightly longer response still inside a one-minute window |

Why these three, and why not more:

- Three is the documented maximum for this work package. Adding a fourth Δ
  after seeing the panel would be a search, not a pre-registration.
- 1s matches the WP-Q1 default bucket, so Δ=1 is “next bucket”.
- 5s is a short multiple that is still microstructure-scale on BTC-PERP.
- 30s is the longest predeclared horizon that still fits inside a one-minute
  economic window without turning this into a minute-bar momentum study.
- If a caller builds a panel with `--bucket-ms` other than 1000, the same
  bucket counts still apply. The runner reports `delta_ms = Δ * bucket_ms`
  and does **not** retune the set.

The runner cannot override this tuple.

## Primary signal definition

One primary definition (boring on purpose):

1. Require the signal bucket `t`, the prior bucket `t − 1`, and the horizon
   bucket `t + Δ` to exist on the calendar bucket grid (not merely as the
   next surviving row after a gap).
2. All three buckets must pass the WP-Q1 usable mask:
   `overlap_ok` and not `hl_incomplete` / `bn_incomplete` /
   `hl_gap_detected` / `bn_gap_detected`.
3. Binance impulse price at a bucket, first available of:
   `bn_spot_last_price`, `bn_spot_bbo_mid_proxy`, `bn_usdm_last_agg_price`.
4. Hyperliquid response price at a bucket, first available of:
   `hl_last_mid_price`, `hl_bbo_mid_proxy`, `hl_last_trade_price`.
5. BN return over the prior bucket:
   `(bn_t − bn_{t−1}) / bn_{t−1}`.
6. Signal = `+1` if that return is positive, `−1` if negative. Zero BN
   return is **not** a trade (no impulse).
7. HL gross return over Δ: `(hl_{t+Δ} − hl_t) / hl_t`.
8. Signed HL return: `signal * hl_gross_return` (long HL if BN printed up,
   short HL if BN printed down).

No secondary definition is scored in this work package. Do not swap in
signed BN trade size, USDM mark, or multi-bucket BN lookbacks without a new
pre-registration.

## Cost model

Every signed HL return is reduced by a round-trip Hyperliquid cost, then
stressed:

- **Taker fee:** `0.00045` (4.5 bps) per fill — Hyperliquid published base
  taker tier, two fills (enter and exit). This is a schedule assumption, not
  an account-tier measurement.
- **Half-spread proxy:** when the signal or horizon bucket has HL BBO bid and
  ask, `(ask − bid) / (2 * mid)` using `hl_bbo_mid_proxy` or
  `(bid + ask) / 2`. If BBO is missing or invalid, fall back to `0.00005`
  (0.5 bps) per fill.
- **Round-trip base cost:** `2 * taker_fee + half_spread_entry + half_spread_exit`.
- **Stress multipliers (predeclared):** `1.0`, `1.5`, `2.0` applied to the
  whole base cost.
- **After-cost return:** `signed_hl_return − base_cost * multiplier`.

Funding, slippage beyond the half-spread proxy, queue position, and
partial fills are **not** modeled here. After-cost figures are descriptive
units, not account PnL.

## OOS holdout

The caller **must** pass an explicit UTC-nanosecond range:

- `--oos-start-utc-ns`
- `--oos-end-utc-ns`

A fractional split with a seed is not offered. The OOS slice is the only
slice that can decide `noise` versus `not_enough_data`.

A trade is OOS when its signal bucket `t` and its horizon bucket `t + Δ`
both lie inside `[oos_start, oos_end]`. In-sample trades are those whose
signal and horizon both lie strictly before `oos_start`. In-sample metrics
may be printed as descriptive diagnostics. They must not be used as
promotion language.

On a real retained panel the holdout should be a later receipt-clock window
that was not used to choose the signal. This scaffold does not search
parameters, but it still requires the caller to name the holdout so the
range is recorded.

## Metrics (descriptive only)

For each predeclared Δ and each cost multiplier, on OOS and separately on
in-sample:

- `trade_count`
- `mean_after_cost_hl_return` (Decimal text)
- `hit_rate` (fraction of trades with after-cost return `> 0`)
- `sum_pnl_units` (sum of after-cost returns; unitless)

No Sharpe, no t-stat, no multiple-testing adjustment, no “expectancy proves
edge” sentence.

## Falsification / verdict rules

| Condition | Verdict |
| --- | --- |
| Panel parquet or summary missing / unreadable | `not_enough_data` |
| WP-Q1 summary verdict is not `panel_ready` (including `not_enough_data`) | `not_enough_data` |
| Usable buckets after the gap/incomplete/`overlap_ok` mask below `MIN_USABLE_BUCKETS` (16) | `not_enough_data` |
| OOS range does not overlap usable buckets | `not_enough_data` |
| Any predeclared Δ has OOS `trade_count` below `MIN_TRADES_PER_HORIZON` (8) | `not_enough_data` |
| Otherwise (this work package) | **`noise`** |

`noise` here means “the scaffold ran and is not allowed to claim a result”.
It is **not** a statistical acceptance of a white-noise null, and it is not
issued because the mean after-cost return was negative. Positive-looking
synthetic or retained metrics still receive `noise`.

`edge` is not a legal H1 verdict. The enum does not include it. Assignment
raises.

## Registry

Any rerun that should be citable must copy
[`exp_h1_leadlag.registry.template.json`](exp_h1_leadlag.registry.template.json)
and fill the [Research Method](../RESEARCH_METHOD.md) registry fields
(experiment id, commit SHA, panel paths/version, Δ set, costs, OOS range,
metrics, verdict, promotion decision). The template ships with **null**
metrics. Do not invent winning numbers.

The runner writes the same field set into its JSON summary. `promotion_decision`
is always `forbidden`.

## Invocation

Existing WP-Q1 panel:

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.exp_h1_leadlag \
  --panel-parquet /path/to/panel-out/panel.parquet \
  --panel-summary /path/to/panel-out/panel-summary.json \
  --oos-start-utc-ns 1788105600000000000 \
  --oos-end-utc-ns 1788192000000000000 \
  --output-dir /path/to/h1-out
```

Build the panel first from reconstructable retained runs (TerraPC roots live
outside git):

```bash
PYTHONPATH=src uv run --frozen python -m hyperliquid_bot.exp_h1_leadlag \
  --artifact-root /path/to/reconstructable \
  --hl-run-id <data-1a-run-id> \
  --bn-run-id <data-1f-run-id> \
  --oos-start-utc-ns 1788105600000000000 \
  --oos-end-utc-ns 1788192000000000000 \
  --output-dir /path/to/h1-out
```

Do not commit live captures or filled registry rows with fabricated metrics.
Unit tests use synthetic panels only.
