# BTC OI deleveraging recovery on broader exposed history

Register before financial labels, 2026-11-13.
Identity `btc-oi-recovery-broader-20261113-v1`, delivery #261, programme #237.
This is a new source-admission and broader-history continuation, not a rewrite
of discovery batches one/two, their failed source attempt or January outcomes.
No selector, retuning, new alpha or trading admission. Programme remains 32/40
unique configurations across four families; retain all four lines and controls.

## Hypothesis and frozen rules

Does recovery after observable deleveraging and a large completed price-hour
remain useful after costs/funding across broader BTC regimes? Reuse exactly
the FOUR original `discovery.configs()` OI variants: price-hour shock 0.5%/1%
in either direction; lagged base-BTC `sum_open_interest` contraction at least
1%; contrarian direction; initial weight 0.5 of marked equity; hold 4/12 hours.
Decide at completed hour T, enter at T+1h, exit at entry+hold, fixed quantity
while held. One episode at a time; a decision at the last exit enters an hour
later. No bracket, averaging down, rebalancing, threshold or leverage search.
Old feature, schedule and accounting modules remain byte-identical to their
reviewed source; no strategy module calls a venue API.

Universe is selected-survivor Binance USD-M BTCUSDT, not native Hyperliquid.
Primary decisions satisfy January 1, 2024 00:00 <= T < September 1, 2026 00:00
UTC: 974 calendar days. Development is 2024; 2025 and January–August 2026 are
separate diagnostic groups. ALL are exposed retrospective history. No new
untouched OOS, selection or finalist claim. Positive, negative, no-signal,
failed and counterfactual results are all reported, without choosing a winner.

## Explicit new native OI contract

All 974 official daily ZIPs and filename-bound checksum sidecars were available
and qualified before this study. Inventory SHA-256:
`fabf9fad203c0190d766f082ec9ab026b651f22544870ccd0f396cb1055c093a`.
Prior complete native day-quality census SHA-256:
`aa8162fbfa4d748ef725bde0e0b5b80371e39e9125744534a0b52156d075ef62`.
The new decoder must reconstruct that entire census, not just permit aggregate
counts. Preserve each native decimal text, physical CSV row and the complete
280,512-slot five-minute availability mask, including missing and zero values;
bind and retain its hash in the actual result. Old archives and old strict
`discovery_io.oi_values` are never edited, repaired or silently re-admitted.
Each mask row is `[timestamp_ms, available_decimal_text, native_decimal_text,
physical_csv_row]`. Zero has an unavailable second field but retains its native
text and row; an absent slot has all three remaining fields null. The first
data row is physical CSV row 2. The hash covers the complete ordered row array.
Its source-only qualification SHA-256 is
`920e1bc1ac89bea41653c1745e18db5df261ddc4db1fa7c09b3bfac595792844`;
this binds no financial outcomes.

The sole new admission semantics index native rows by their literal timestamp,
independent of physical order. There are 107 complete permuted days and three
days missing 130 rows; 109 days have physical-order mismatches, including two
incomplete days. Preserve those anomalies and all 330 literal zero-OI rows.
Zero and absent slots are unavailable (`None`), never a 100% deleveraging event,
forward fill, interpolated value or erased loss. Do not use quote-value OI.

Retain exact eight-column header, BTC identity, one bounded unencrypted CSV,
CRC, exact official filename/size/hash/sidecar, one-megabyte decoded CSV bound,
288-row limit, finite nonnegative base quantity and literal UTC 5m grid. Reject
duplicates even with identical values, wrong-day/off-grid/malformed timestamps,
negative/nonfinite OI or any changed source. Indexing is not a CSV rewrite or
license to relabel clocks, deduplicate or invent an unavailable observation.

At each T use only exact T-5m and T-65m OI through the unchanged original
hourly feature generator. No December OI is downloaded: pre-START OI is absent,
so the first TWO decision hours are explicitly unavailable. Price history has
the existing qualified December warm-up. END never creates a decision; its
price remains a valid terminal execution reference. Missing OI after an entry
does not close, reset or resize an already held position.

Native textual archive times are modeled as UTC. The five-minute feature lag
and publication/content timing remain assumptions, not point-in-time receipt
evidence. Archives appear later and can be revised; byte checks establish this
vintage, not historical availability. No reported potential five-minute vendor
shift is applied without evidence. Mark features availability assumed.

## Price execution, gaps and chronological boundaries

Reuse the accepted price/mark/native funding input, SHA-256
`ebc112967d3a6a75ad5e50f147d500fba899821ec6db31bcd96fd06183b0a518`,
and `regime_io.load` source qualification. Build an OWN OI replay specification:
fees/friction are the OLD OI **5/2 bps PER SIDE**, stressed **10/4**. Do not
inherit regime's 4.5/2 fees or its daily signals/volatility sizing. Costs replay
the same frozen schedules, carrying base/stress capital independently.

Each hourly execution explicitly uses the native closed TRADE-H1 close from
the qualified hourly series, matching the original OI batch. Do not silently
inherit regime's minute-trade reference or substitute the mark. Preserve the
native minute marks, exact funding frames/settlement marks and all held-minute
extremes. Extra 00:01/00:02 valuation frames do not create daily orders or
independent statistical returns. Daily statistics use fixed UTC snapshots.

The two prequalified missing-mark days, August 12, 2024 and June 29, 2026,
define the SAME three qualified price segments for every line/control. Exclude
any decision-to-exit interval that crosses a price-segment endpoint or the
fixed Jan1-2025 / Jan1-2026 diagnostic boundaries, before reading outcomes.
An exit exactly at a boundary is allowed; crossing it is not. Keep exclusion,
unavailable and busy counts. This conditional known-gap policy does not prove
that a live strategy anticipates an unplanned gap or exits safely through it.

Every cost/line begins a separate 10,000-USDT account. Carry actual capital
across segment and year boundaries; no monthly/year reset or fresh account
after a failure. Only the known price gaps require flatness. Targets exactly
at segment END are represented by the existing paid terminal close, not an
unsupported new target at END. Funding on the units held immediately before
same-time executions is charged BEFORE that close. Nonzero-ms funding after
the close is outside exposure. Preserve all original timestamp ownership.

## Accounting, controls, risk and interpretation

Reuse the unchanged corrected fixed-quantity simulator, regime financial/risk
summary and period-aware `regime_cli.Account`; no new cash framework. Each case
also receives the previously reviewed 2x ENTRY-MARK isolated-margin / current
1.25% maintenance proxy screen on every held native minute extreme and exact
funding event. This is explicitly ADDITIONAL diagnostic risk qualification,
not a change to 0.5 sizing or a restoration of old collateral certification.
Retain numeric accounting even if counterfactual; do not discard a losing trade.
Inherited 2x margin/20%-margin diagnostic fields are not directly comparable to
old unqualified collateral conventions and do not change the trade targets.

Fifteen accounts: four strategies; cash; fixed 0.5 persistent long/short entering
at the first delayed hour of each valid price segment; matched long/short for
EACH of the four strategies with its exact trade clocks and weights. Two cost
cases over three segments give 90 expected segment/cost cases. Persistent
controls may cross year splits; their group returns use continuous marked
equity, not separate flat-start year trades. Matched controls remain on the
strategy's prospectively excluded calendar. A failed/risk-invalid control
cannot certify comparative alpha; preserve its evidence and mark comparison
unavailable. No account replaces a failed selected line: there is no selector.

Report all ledgers, requests, schedules/episodes, native masks, source exclusion
counts, gross price PnL, fees/friction, signed funding, long/short attribution,
marked drawdown, turnover, per-episode results/holding and full calendar daily
returns. Descriptive uncertainty reuses fixed 2,000 circular-block resamples,
seed20261113, seven-day blocks and 30-day sensitivity; paired comparisons share
indices. Dependence, exposed search history and small samples prevent claims
of post-search significance, expected daily profit or trading admission.

If a legacy simulator exception omits partial state, preserve its full request
and error; failed-segment exposure is UNKNOWN. Final equity/return remain null,
only completed-segment equity is labeled as such, and later segments stay
blocked. Never label unprocessed days flat or restart at initial capital.

## Delivery boundary

PAPER refusal precedes source/output reads. Bind actual clean reviewed HEAD,
complete imported source, original/new protocols and both inventory identities
before/after. Rehash all OI ZIPs/sidecars and qualified price/funding files after
calculation. Create-only bounded private VM artifacts; no NAS writes, downloads,
runtime change, dependency, CI framework, key, venue order or broker admission.

Source qualification and synthetic tests may run before registration review;
financial labels may run ONLY after independent code/security and quantitative
review, current CI and the separate expected-head merge. Then independently
reconstruct actual results and publish all outcomes. No PAPER strategy, TESTNET,
SHADOW, LIVE, capital action or model self-promotion follows from this study.
