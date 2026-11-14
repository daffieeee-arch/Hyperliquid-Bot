# CLAIRVOYANT_NONTRADABLE: bounded numeric representation amendment

Pre-label engineering amendment, 2026-11-13, issue #268. Original protocol
commit `d09ba332e2c158568997d6aede9355118838bfa6` and SHA-256
`53565942d6a0940f9fafb2930e7e0482d04cc024c9d236643d8e9ab07847c6a8`
remain unchanged. No actual financial labels preceded this amendment. It
changes representation, not sources, cost/sizing/grid rules or financial choice.

Summing many different exact rational denominators grows without practical
bounds: a synthetic sum of 1/(400000000+i), i=0..1439, already exceeds Python's
standard decimal-integer serialization guard. Do not disable that guard or
build an enormous exact 974-day rational mean.

Native decimal prices, each interval's quantities/fills/fees/friction/PnL,
long-versus-short-versus-flat comparisons and interval digest rationals remain
EXACT. Day aggregates use deterministic Decimal precision50, ROUND_HALF_EVEN;
their printed strings are bounded rounded aggregates, not exact rational sums.
For NET also accumulate separate lower/upper bounds: convert every exact
interval Fraction using ROUND_FLOOR/ROUND_CEILING at precision50, then accumulate
with that SAME directed rounding. This brackets conversion AND addition error.

Compute unit-weight daily sums once. Derive weight0.5 by EXACT dyadic scaling
of those Decimal sums/bounds, with sufficient precision (at most51significant
digits; implementation may use60 and assert no inexact flag). Do not separately
round half-weight back to precision50. All directions/counts remain shared.
Fees, gross, friction and turnover follow the same exact half scaling.

Inclusive 20% reference threshold: PASS only if lower>=0.20; BELOW only if
upper<0.20; otherwise NUMERIC_THRESHOLD_UNCERTAIN with threshold_hit=null, never
quietly false or true. Keep every uncertain day/case and report target-hit count
range [PASS,PASS+UNCERTAIN]. Passing is only the price-only hypothetical hurdle.

Cross-day point means/medians/nearest-rank quantiles use bounded Decimal50 and
are explicitly rounded descriptive statistics. Directed mean bounds accumulate
daily lower/upper bounds and divide outward. Ordered lower/upper envelopes bound
median/quantiles/min/max independently of rounded point ranks. No exact rational
mean over974days, significance claim or choice of a favourable rounded case.

Require a full1440-interval synthetic heterogeneous-native-price day before
labels, with wall/RSS/output-width evidence, exact half-scale invariants and
near20% threshold tests. Independent audit uses higher precision/exact small
synthetics and verifies bounds, choices, cost ownership and all-day counts.
Actual market calculation still requires source/code/security+QUANT review,
current ready CI and separate merge; this amendment authorizes no financial run.

Pre-label source-only admission reconstructed all974days and1,402,561 distinct
minute boundaries, including December's opening carry and both mark-gap days.
Minute-clock SHA-256:
`dfc85ee599519e0ad236d6339e2d6859916cef75bbb539ac42ae8b56032b450b`.
Native-close-text chain SHA-256:
`0710f1a06a26e67e1db25c7e18551d4e262e2dd1b27a83924ee40ef6940e2255`.
These are source identities, not financial labels. Reference N/E is a sizing
budget, not proof of an instantaneous or drift-enforced fill exposure cap.
