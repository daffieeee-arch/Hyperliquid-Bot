# Prospective public decision-input recorder: initial local source contract

Registered before any real API call, 2026-11-13. Identity
`decision-input-recorder-local-rest-v1`; native-source work #249.
This source-only engineering slice changes zero alpha configurations or prior
experiments. No own-wallet data, signing, credentials, trading or profit labels.

## Initial scope and deployment boundary

One bounded local PAPER recorder preserves exact HTTP response bodies and
receipt metadata. Outputs use existing create-only PrivateOutput with explicit
file/directory fsync. This initial local product is NOT a running TrueNAS App,
NAS-replicated archive, complete WS stream, oracle source or trading admission.
Independent source/code/security review, CI and merge precede any real public
network pilot. A separate future runtime plan must admit App/image, verified
NAS namespaces, replication, persistent clock export, resources and portal.
Neither standard four-feed nor FAST apps/controls are modified or restarted.
No host clock publisher or guessed rotating collector path is installed.

## Fixed sources, cadence and immutable envelope

Only credentialless HTTPS endpoints `https://fapi.binance.com/fapi/v1/klines`
and `https://api.hyperliquid.xyz/info` are permitted. URLs, methods, symbols,
reference addresses and request types are built internally; no supplied URLs,
headers, authentication, proxy credentials, redirects or endpoint fallback.

1. `binance_usdm_h1_rest_closed_observations_v1`: for each closed UTC hour
   boundary T, GET BTCUSDT, interval1h, latest three expected completed bars
   `[T-3h,T-1ms]`, limit3 at fixed T+1s/+4s/+7s. Bound each request by remaining
   T+10s deadline and at most three seconds. Keep every attempt including errors.
2. `hlp_userfunding_rest_timed_observations_v1`: at T+15s/+60s/+300s preserve
   both fixed A/B responses, every returned coin row. POST userFunding,
   inclusive startTime T-1000ms, endTime min(actual request UTC,T+300000ms).
   A is `0x010461c14e146ac35fe42271bdc1134ee31c703a`; B is
   `0x31ca8395cf837de08b24da3f660e77761dfb974b`. Public protocol references,
   not own accounts. Fixed collection does not depend on rate sign or success.
3. `hl_btc_fundinghistory_exact_census_v1`: same three rounds, BTC rate history
   over prior/current hour, inclusive original milliseconds, plus one fixed
   closed-day reconciliation. Closed prior slots get a census; current slot
   is provisional. Never round source event timestamps for joins. No daily
   all-coin cash request, which could hit the 500-row cap.

Actual sequential starts may follow the shared planned round time; both planned
and actual clocks remain visible. No missed scheduled job disappears. Cash/rate
last-round deadline is T+310s; serial jobs share that round budget. Live limits:
bar64KiB, rate64KiB, cash256KiB, optional seed512KiB; max32 requests/hour,
512/day, 128MiB response body/day, one HTTP request in flight, maximum2starts/s.
Obey bounded 429 Retry-After backoff. Late/timeouts/caps/errors stay evidence,
not zero values. Oversize capture retains a labelled prefix, never a full-body
hash claim. Each body/decoded schema is bounded before parsing.

Preserve product/schema/run/session/request IDs, source/config/plan identities,
exact request bytes, endpoint/method, planned times/deadline, actual request
UTC/monotonic, full-body receipt UTC/monotonic BEFORE parsing, HTTP status,
allowlisted public headers, byte count/hash/body-complete flag, original body,
native times/decimal strings and nSamples presence/value. Body is the HTTP
application payload, not TLS wire capture. REST has no native dispatch E or
WS final flag: these stay unavailable, never fabricated.

Write exact body plus immutable receipt metadata, fsync both files and parent,
then record content-durable clocks. Fsync an immutable commit binding both
hashes, then sample ready clocks after that commit. Persist a create-only ready
receipt; only a fully successful durable write enters recorder state. Crash
tails without a complete ready receipt are UNKNOWN/uncommitted. Readiness
clocks are source-content availability evidence, not proof that a strategy
actually evaluated those inputs: a future consumer must record its own actual
evaluation time/input IDs and cannot backdate a decision to T.

## H1 interpretation and clock qualification

T is native bar label, not arrival. Require at least TWO semantically matching,
valid closed-range snapshots whose actual full-body receipt AND durable-ready
clock upper bounds are <=T+10s. Native open times must be the three expected
consecutive hours; native closeTime=openTime+1h-1ms, strictly before receipt's
conservative lower bound. Require twelve fields, finite coherent positive
OHLC, nonnegative volumes/counts and complete native strings/fields.

REST has no `x=true`: range-closed plus repeated agreement remains an explicit
FINALITY_ASSUMED condition; later revision is possible. Every other valid
on-time variant conflicts rather than choosing a majority or latest result.
Keep native bytes even when invalid. A possible future selected-final WS
product must be separately named and preserve actual x/E/t/T. It cannot claim
all WS raw when intentionally omitting intrahour x=false messages.

An optional 721-CLOSED-bar seed is historical content, not on-time proof for
721 past decisions. Without a qualified seed report WARMUP_UNAVAILABLE. Seed
and late/backfill products are distinct; restart repair never retroactively
repairs an on-time receipt. All future consumers must bind actual seed arrival.

A clock evidence path is optional and explicit; there is no default path to
FAST/standard controls. Require a fresh host-chrony snapshot, synchronized,
conservative offset/dispersion/delay uncertainty<=100ms, age<=60s and consistent
UTC/monotonic progression. Missing/invalid/stale clock means TIME_UNQUALIFIED;
raw bodies may still be retained. Monotonic clocks only compare within a boot.
No clock reset, adjustment or chrony command socket is exposed.

## Funding reference limits

Preserve ALL original coin rows. Join BTC userFunding only to unique exact
native fundingHistory time AND numerical rate, for both fixed references.
Missing/empty API, capped response>=500 rows, duplicate conflict, nonfinite
values, zero/too-small signed size and aggregation are explicit conditions.
Do not map an empty array to cash0 or a rounded midnight to an hourly event.
Different native strings remain available even when Decimal values compare equal.

nSamples missing, explicit null and numeric value are distinct. Any unexplained
numeric aggregation, especially24, is unqualified. Null is only a conditional
gate, never proof of unaggregated cash. Zero native hash does not certify event
uniqueness or inclusion. Negative rates and signed sizes remain valid data;
zero-rate, zero-cash and unavailable cases have separate statuses. No selection
of positive hours/replacement accounts. All fixed scheduled denominators stay.

At most a MODELED_REFERENCE_CASH_INTERVAL_CONDITIONAL can be described later:
predeclared epsilon1e-6USDC and1e-4 sensitivity, abs(reported size)>=1BTC,
exact event/rate and A/B agreement. These are assumptions, not vendor cash/size
precision guarantees. No division by rate, exact oracle reconstruction,
own-position cash, funding PnL or trading certification is implemented here.
Cash received after settlement is not predictive information available then.

## Duplicates, state, restart, status and completion

Raw attempts never overwrite. Bar identity is native open time + product;
cash identity includes address/coin/exact time; rate identity is BTC/exact time.
Retain all raw identities for semantic duplicates and all conflicting variants.
Do not silently sort-repair, shift, select latest or rewrite sealed observations.
Late conflicts stay late: retain original as-of-deadline knowledge separately.

Each bounded run gets a unique new local directory/session. A bounded optional
previous checkpoint is read-only and hash-bound, not resumed in place. Startup
records a new generation and explicit gap/backfill context. Incomplete request
and crash-tail manifests cannot become successful input. No whole archive scan,
resume from an untrusted partial, deletion or arbitrary directory discovery.

Status is a small read-only metadata surface. It reads bounded atomic JSON,
never raw/Parquet files or recursive datasets. Controller liveness, HTTP health,
on-time bar status, seed coverage, conditional A/B/census availability, clock,
errors/backoff/caps and durable publication are separate. No broker endpoints
or runtime controls. Bind loopback by default; a future LAN/TrueNAS portal is a
separate deployment gate. Local finite run/storage bounds are explicit; this
slice installs no watcher or background service. There is no both-copy NAS
promise, new database, dependency, CI stack or automatic archive removal.
