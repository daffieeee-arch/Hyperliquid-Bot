# Hyperliquid lifecycle edges: bounded offline observations

Decision: **HOLD execution adoption; no new risk permitted.** The seven
single-order delivery sequences for [#175](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/175)
extend the [original five fragments](hyperliquid-adapter-fragment-fit.md).
They do not enable TESTNET, change dependencies/pins, replace the order model,
or prove a deployed runtime. The original runner and evidence remain unchanged.

## Actual exercise and fixture boundaries

The [new optional runner](../../fit_gates/hyperliquid_testnet_readiness/lifecycle_edges.py)
reuses the original exact `nautilus-trader==1.231.0` installed-source/version
checks, inline coroutine driver, order/report builders and real event delivery
helper. The environment-presence guard runs before optional imports or audit
installation. Python socket/process audit guards are installed before candidate
loading; these are not an OS sandbox for native extensions. No client, factory,
signer, event loop, socket or venue method is constructed/called.

Actual unbound Python adapter methods handle the synthetic reports, including
cloid resolution, acceptance, fill buffering/deduplication and terminal mapping
cleanup. Their events are applied directly to the actual upstream `LimitOrder`.
The native `FifoCache` types provide accepted/terminal/trade-ID caches. Quantity,
trade IDs, commissions, leaves and closed/status observations come from that
real core order and its applied events. There is no local replacement broker,
OMS, state machine or repair of an upstream result.

Only explicit seams are supplied: one recorded submit returning no immediate
ACK; exact client/venue/instrument cache lookups; a clock/logger; direct event
application and capture of externally forwarded domain reports. The buffered
case delays the order lookup while keeping its strategy index visible, then
restores visibility. Venue-ID lookup works only after the core order has a
binding. These are stated lookup fixtures, not evidence of real cache/event-bus
ordering, cache mutation/persistence or reconciliation behavior. External reports
are recorded, not fed to a fabricated reconciliation engine.

All domain inputs are hand-authored, never captured venue traffic. Nominal
BTC quantity `0.10000`, fill `700001` of `0.04000`/fee `1.80000000 USDC`, and
fill `700002` of `0.03000`/fee `1.35000000 USDC` use price `100000` and venue
order ID `900001`. Initial ACCEPTED reports carry zero cumulative fill;
`cancel0`, `cancel4`, `cancel7` explicitly carry cumulative `0`, `0.04`, `0.07`
respectively, independently of the actual order result. All timestamps share
the same nominal synthetic instant. Only arrival ordering is tested; venue
event-time/clock ordering and actual IOC wire policy are unproved. A terminal
CANCELED remainder is an injected status requirement, never a prediction of
a venue response. Instrument precision/limits are not checked against metadata.

## Results and conformance limits

[Recorded actual-run evidence](../../fit_gates/hyperliquid_testnet_readiness/lifecycle-edges-evidence.json)
contains all seven cases, individual checks and explicit `bounded_verdict`:

| Sequence                                                        | Real core observation                                                                                                             | Bounded verdict                       |
| --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| ACCEPTED, partial fill, same trade again                        | One unique fill and fee; `0.04000` filled, `0.06000` leaves                                                                       | PASS                                  |
| Fill before ACCEPTED, repeated ACCEPTED                         | Fill handler emits acceptance first; later ACKs add no fill/fee                                                                   | PASS                                  |
| Order lookup hidden, duplicate fills, lookup restored, ACCEPTED | Both reports buffer; acceptance drains to one unique fill/fee                                                                     | PASS                                  |
| ACCEPTED, partial fill, CANCELED remainder                      | CANCELED/closed; partial quantity and fee retained                                                                                | PASS                                  |
| Partial fill, CANCELED, second partial, repeated CANCELED       | `0.07000` and `3.15000000 USDC` retained; order reopens PARTIALLY_FILLED and repeat cancel is suppressed                          | HOLD: terminal remainder unproved     |
| ACCEPTED, CANCELED, late partial, repeated CANCELED             | `0.04000` and fee retained; again PARTIALLY_FILLED/open                                                                           | HOLD: terminal remainder unproved     |
| CANCELED before ACCEPTED, then late fill and ACCEPTED           | Core remains CANCELED with zero fill and no venue-ID binding; actual fill/status reports forwarded externally after cloid cleanup | HOLD: reconciliation handoff unproved |

The two reopened-order cases preserve observed fill quantities and fees; they
fail the required terminal IOC disposition. The third HOLD forwards the actual
external fill with its original cloid, venue/trade IDs, `0.04000` quantity and
`1.80000000 USDC` commission. Its local order does not incorporate that exposure.
This is **not evidence of globally lost exposure**: the base event bus and
reconciler that receive external reports were deliberately not exercised.
Their authoritative attribution/recovery is a remaining gate. No local state
logic, retained cloid oracle or vendor patch was added to make these cases pass.

Native transport/retries, wire parsing, cancel-request transport, a real event
bus, account/balance/funding truth, durable recovery and venue reconciliation
remain unproved. Cache eviction, multi-order trade-ID scope, metadata, signing,
nonce policy and real clock/venue ordering are outside these seven fixtures.
The original readiness and explicit mode/credential gates remain in force.

## Repeatability and CI

With the existing isolated candidate environment and no account/key variables:

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/hl-ci-d01-venv/bin/python \
  -m fit_gates.hyperliquid_testnet_readiness.lifecycle_edges \
  > /tmp/hyperliquid-lifecycle-edges-repeat.json
cmp fit_gates/hyperliquid_testnet_readiness/lifecycle-edges-evidence.json \
  /tmp/hyperliquid-lifecycle-edges-repeat.json
```

Two executions produced byte-identical output. Evidence binds the installed
Python-source hashes, unchanged shared harness hash, new runner hash and runtime;
it does not independently authenticate installed native binaries/wheel bytes.
Exit 0 means **all seven expected positive and negative observations reproduced**,
not all safety cases passed. The runner checks case completeness, exact failed
check sets, final status/closed state, external handoff content and classification.
Unexpected outcomes, missing cases, source drift, mode/credential violations or
unavailable required seams fail with exit 2 and a generic diagnostic. No case is
skipped or xfailed; known HOLD cases remain counted. Overall `decision: HOLD`,
`all_safety_cases_pass: false` and `new_risk_permitted: false` are explicit.

Existing normal Python CI checks frozen observations, arithmetic, hash linkage
and fail-closed guards without installing or executing the optional adapter.
The actual runner was executed separately in the already installed isolated
environment; the frozen checks alone are not evidence of a new execution.
A separate bounded CI integration is tracked in #176. No new CI job, root
package, source/study pin, NAS scan, data capture or deployment is included here.
