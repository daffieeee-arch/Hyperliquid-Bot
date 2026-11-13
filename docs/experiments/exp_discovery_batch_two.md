# Discovery batch two: explicit native OI unavailability

Frozen before financial scoring, 2026-11-13; issue #237.
Identity: `discovery-real-history-20261113-b2`.

This is an explicit data-admission continuation of
[batch one](exp_discovery_batch_one.md), not a retuned hypothesis. The first
registered attempt, source `682b12c0eeb668e9bf1e37f4055db1c1c49ec7dc`, failed
before evaluating any financial trial because native BTC open interest
contained zero records. Its claim, failure and resource receipt are retained;
batch one remains failed, not superseded by an invented successful result.

## Sole contract amendment

Independent full native qualification verified all 31 official ZIP/checksum
identities, the exact header, and 8,928 contiguous five-minute BTC observations.
There are 8,923 positive finite OI values and five literal `0E-16` records:

- 2025-01-08 17:15, 17:20, 17:40 and 18:10 UTC;
- 2025-01-28 12:10 UTC.

Treat numeric zero OI as **unavailable**, represented by `None`; never as a
100% deleveraging event, zero inventory observation or an imputed/forward-filled
value. Negative, nonfinite or malformed OI still refuses admission. All source
hash, official checksum, schema, instrument, row-count and time-grid checks
remain strict. Record all zero timestamps, total observations, this policy and
the number of zero slots selected by the hourly feature clock in the output.

None of these five records is at minute 55, so none enters the registered
T−5-minute hourly feature. This fact was established from source qualification,
before any strategy PnL or returns were calculated. A selected unavailable OI
slot would make that hourly OI comparison unavailable; the price-only residual
mechanism remains independent of OI feature availability.

## Inherited frozen experiment

All other batch-one rules are inherited unchanged: same 89 source identities,
BTC/ETH/SOL survivor universe, exposed January development/diagnostic periods,
December warm-up, twelve configurations and paired base/doubled-cost cases,
strict execution delay, fixed exposure/holding, native funding/cash accounting,
benchmarks, metrics, development-only ranking, limitations and forbidden trading
promotion. The cumulative **unique numerical configuration budget remains 12
of 40**; the failed source-admission attempt added zero scored configurations.
Do not overwrite the original plan or source pins. Bind both specification files
and this new identity to the reviewed source. Review and merge this amendment
before scoring; retain both attempts. No hypothesis, threshold, direction,
selection rule or cost assumption may change after inspecting outcomes.
