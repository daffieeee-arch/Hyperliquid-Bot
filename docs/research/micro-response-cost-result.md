# Frozen micro signals: price response does not cover modeled costs

Actual result, 2026-11-13, delivery [#256](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/256).
The [registered protocol](../experiments/exp_micro_response_cost_20261113.md)
tested the four existing entry-feature policies on all eleven preselected
Hyperliquid BTC perpetual captures. **All 200 conditional cell means are
negative after modeled spread, taker fees and adverse friction.** No signal,
horizon, target or profitable strategy is selected. This is a price-response
diagnostic, not realized trades, account returns or net margin ROI.

The separate [large-move candidate](locked-regime-recent-result.md) retains its
positive but unproven retrospective result. This micro diagnostic neither
repairs nor replaces the original eight micro strategies' failed execution
results in the [two-model study](two-model-momentum-micro-result.md).

## Population, fixed clocks and economics

All 9,323 sealed native parts are included, covering 3,159 original conditional
source windows and 156.4942 source-qualified hours. Those hours include short
windows, warm-up and purged endpoints; they are not 156 hours of executable
signals or continuous trading. The population is exposed September and
November 2026 history. The original development, validation, diagnostic and
recent-diagnostic groups remain visible, together with an explicitly exposed
aggregate. None is fresh out-of-sample evidence.

The original pressure features are unchanged: trailing 30/120-second history,
strength 0.2/0.4, best-size imbalance, signed trade-notional imbalance and
matching order-flow sign. The two old 1/2%-margin targets share their entry
features and are collapsed for this diagnostic. No new alpha is introduced;
the programme remains at **32/40 unique configurations**. There is no portfolio
position gate, target, stop, quantity allocation or leverage multiplication.

Decisions use the frozen 10-second grid and entry calendar. Native-book age
(-1,000 through +2,000 milliseconds), one-second decision receipt freshness and
one-second entry-observation wait are separate guards. Entry requires a NEW
qualified book after 500ms base / 1,000ms stress. The primary label is the first
NEW qualified book at or after 300 seconds from the actual observed entry,
with at most 15 seconds additional wait and no crossing a source window.
The fixed 1/5/30/120-second endpoints describe decay only. No interpolation,
cached pre-delay fill or post-result endpoint selection is used.

Base assumes 4.5 bps taker fee and 1 bp adverse friction **per side**; stress
uses 9/2 bps and the longer delay. The idealized unit-price expression uses
the observed ask/bid, adverse grid rounding and fees on entry and exit prices.
The approximate round-trip hurdle is therefore 11 bps base / 22 bps stress
plus observed spread and rounding. This diagnostic certifies neither available
quantity nor actual venue fills, latency, impact or funding cash. One basis
point is 0.01% of price, not a percentage of margin.

## Primary 300-second result

These are event-weighted means on each cost-specific observed cohort. Different
entry delays can produce different observed quotes and denominators.

| Frozen entry policy | Base labels | Base gross bps | Base charged price bps | Stress labels | Stress charged price bps |
| --- | ---: | ---: | ---: | ---: | ---: |
| 120s, strength 0.2 | 6,746 | +0.4498 | -10.8244 | 6,757 | -21.8160 |
| 120s, strength 0.4 | 4,633 | +0.5812 | -10.6932 | 4,645 | -21.6811 |
| 30s, strength 0.2 | 7,845 | +0.5489 | -10.7254 | 7,841 | -21.7165 |
| 30s, strength 0.4 | 5,902 | +0.6541 | -10.6218 | 5,903 | -21.6067 |

The roughly +0.0045–0.0065% gross directional price response at base is much
smaller than modeled trading costs. These numbers cannot be converted into
hundreds of profitable 1–2% net-margin trades by applying leverage: costs are
leveraged alongside price movement, and overlapping observations are not
independent executable trades.

Every primary aggregate has nine contributing UTC days. Fixed 2,000-resample
UTC-day-cluster intervals (seed 20261113) describe **equal-observed-day means**,
not the event-weighted means above. All eight primary aggregate charged-price
intervals are strictly negative. All primary aggregate paired differences
against constant short have intervals spanning zero. Constant long and the
original completed-history 30-second price-momentum comparison are also
retained on each policy's own eligible cohort; unavailable momentum stays
unavailable. These are dependent, exposed-history descriptions, not a
post-search significance claim.

## Groups, missing observations and shorter endpoints

The [complete 200-cell CSV](micro-response-cost-cells.csv) publishes every
policy, group, cost, horizon, denominator, contributing day and metric, including
both event-weighted and equal-day means and available/unavailable intervals.
At 300 seconds the four original groups contribute respectively 3, 2, 4 and
1 observed UTC days. They overlap calendar days, so their day counts must not
be added to infer independent samples. Their intervals are unavailable under
the registered minimum of five contributing days. All their charged-price
cell means are negative as well.

Every scheduled clock is retained: 56,348 decision records, 32,332 entry-outcome
records (31,486 with an observed entry) and 157,430 label records, 246,110 records
total. For example,
the base 120s/0.2 policy has 8,590 signals: 20 entry boundary purges, 165
unavailable entry quotes and 8,405 observed entries. At 300 seconds those
entries yield 1,659 label boundary purges and 6,746 observed labels. Short
windows and unavailable observations are not silently removed from coverage.
The CSV's shared scheduled-grid counts are denominators for each cell, not
additional disjoint observations to sum across policies, costs or horizons.

At 1/5/30/120 seconds all aggregate base charged-price means remain negative,
approximately -11.20 to -10.62 bps across the fixed endpoints and policies.
These are decay descriptions; none becomes a new selected winner. Negative
300-second mean response does **not** mathematically reject every asymmetric
exit rule or transient opportunity. It does show that these frozen entry
features offer little average gross response relative to this cost hurdle.

Changes-only quote eligibility and missing future labels may be missing not
at random. Conditional windows, overlapping forecasts, a small number of
observed days and previously exposed captures limit generalization. Source
liveness, quote-receipt freshness and native timestamp age are distinct;
none proves that an unchanged old quote is executable now.

All padded entry-to-label intervals must fit wholly inside one individually
qualified funding request range; the disjoint September/November requests do
not certify their unqueried gap. No observed price pair intersects a padded
funding settlement, but funding cash remains **NULL**, not certified zero:
there is no owned position, quantity or oracle-based cash settlement here.
Capacity, queueing, partial exits, recovery and native trade economics retain
their separate qualification gates.

## Actual run, independent reconstruction and identities

Merged source `bd6bee0b00eafdeefd2b78e86cd85908abab5ade` ran after independent
code/security and quant reviews, current ready-head CI (4,764 Python tests) and
the [separate source merge gate](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/257#issuecomment-6865260472).
The actual offline VM run took 656.1919 seconds, with 267,652 KiB observed peak
RSS, two assigned CPUs and a 6 GiB address-space limit. It produced 113 bundle
files totaling 51,620,713 bytes. Original archives and running collectors were
unchanged. The source and input hashes passed before/after checks.

An independent nonauthor auditor reconstructed all 9,387,932 physical native
rows, all 3,159 source windows, every output record and all 200 cells, including
shared bootstrap indices and paired controls, without importing production
model/statistics functions. It passed 206,706,282 core checks plus 632 manifest
binding checks, with maximum numeric difference 0.0, and 34 synthetic checks.
That audit took 313.3391 seconds and 248,440 KiB peak RSS. Its initial
manifest-relative-path assumption is retained and explicitly corrected by
binding the `actual-result/` prefix; the underlying bytes and hashes matched.
No financial result or source was changed to obtain acceptance.

- Study: `micro-response-cost-20261113-v1`.
- Protocol SHA-256: `437a28873b33b5ba9d918c58d5c245c3d729cfca82885146ee0ce4e83ea696ec`.
- Input SHA-256: `ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`.
- Conditional-window vector SHA-256: `d7e6dd8b3abb9990a12471b713697a66cf4af1caa3341532fcd507907ff4a320`.
- Actual result SHA-256: `f6024053df12ad8870b146695386dc3c5e7f193a4b06e1b491a460c1dbb374de`.
- Exact 113-file bundle manifest SHA-256: `4ba6f26c86a4ce4b241b73cd5a92527a09269ca45fc839648c0d45e3d93c398f`.
- Independent bound acceptance SHA-256: `5bf2663bc4876ee852d77bb8ec973fdd6e097db28a68d9b59ad167d2bfdf726d`.

The private create-only bundle retains the full decisions, entries, labels,
per-day sufficient statistics, source windows, claim and completion. The CSV
is a derived public summary, not a replacement for that audit bundle. No
PAPER strategy admission, TESTNET, SHADOW, LIVE, capital action or deployment
follows. The research decision is to preserve this negative entry-information
result and prioritize a different economic mechanism over tuning more pressure
thresholds on these exposed captures. Native large-move validation and the
separate fast recorder continue under their own contracts.
