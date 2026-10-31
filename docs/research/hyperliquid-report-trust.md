# Hyperliquid offline report observations

Decision: **HOLD**. This fit-gate exercise distinguishes an observed bounded
response from failure. It grants no account, history, reconciliation, flat-position
or new-risk authority. It does not enable TESTNET, SHADOW or LIVE.

## Executed boundary

The optional [runner](../../fit_gates/hyperliquid_testnet_readiness/report_fragments.py)
invokes the actual, unbound Python methods from exactly NautilusTrader **1.231.0**:

- `generate_order_status_report`;
- `generate_order_status_reports`;
- `generate_fill_reports`;
- `generate_position_status_reports`.

Actual upstream `Generate*` commands and Python/PyO3 order, fill and position
reports exercise the upstream conversions. A small protocol self supplies recorded
transport, lookup and structured observation hooks. No execution client, factory,
signer or event loop is constructed. The inline coroutine driver rejects any
suspension. Credentials are rejected by environment-variable presence before
optional imports; their values are neither read nor printed.

The recorded transport starts **after** native HTTP, retries and wire parsing.
Its successful completion means only that one explicitly injected response was
returned. The nominal account, order, trade and instrument IDs are synthetic;
there is no authenticated client account configuration. Python audit hooks reject
socket/subprocess audit events but are not an OS sandbox for native extensions.
Native I/O, parsing, pagination, event-bus delivery and venue reconciliation remain
unexecuted. Installed source hashes match the existing exact-version inventory;
binding those files to the published wheel is still unverified.

## Why a list is insufficient

The three bulk methods catch errors, time-outs, cancellations and conversion
failures and return `[]`. A successful empty response also returns `[]`. The
single-report method similarly returns `None` on errors, no match, missing lookup
IDs or suppression of an old-leg cancellation during an in-flight modify.
Bulk order reports can suppress that cancellation and return `[]` without error.
A raw `[]`, `None`, absence of exception or reassuring log text therefore cannot
establish a successful report observation.

The [pure observation contract](../../fit_gates/hyperliquid_testnet_readiness/report_trust.py)
requires one matching invocation/query, explicit bounded transport completion,
adapter completion, no structured error, matching raw/returned counts, and one
matching bulk receipt. Returned report family, account and instrument must match.
A single report must also match every explicitly queried venue/client order ID;
the comparison uses the returned client ID after upstream cloid resolution. No
bool, float or absent count is normalized into completion evidence. Missing,
contradictory, malformed or incomplete evidence yields `UNKNOWN`.

The hooks observe `_log_report_error` exception types and `_log_report_receipt`
counts directly; they never interpret log strings. They are injected exercise
observations, not production instrumentation or an authenticated completion API.

| Classification | What it establishes |
|---|---|
| `BOUNDED_EMPTY` | The injected bulk response and converted return were consistently empty |
| `BOUNDED_NOT_FOUND` | The injected single-query response was consistently `None` |
| `BOUNDED_NONEMPTY` | The injected response yielded matching scoped domain reports |
| `UNKNOWN` | The required bounded observation was not established |

Every result keeps `account_truth=UNKNOWN`, `history_coverage=UNKNOWN`,
`authoritative_flat=false`, `new_risk_allowed=false` and `decision=HOLD`.
Even `BOUNDED_EMPTY` and `BOUNDED_NOT_FOUND` cannot prove an account flat or
authorize an entry. The contract lives under fit-gates and has no production
risk/reconciliation consumer.

## Evidence and limitations

The [46-case artifact](../../fit_gates/hyperliquid_testnet_readiness/report-evidence.json)
covers empty/nonempty, time-out, error, cancellation, wrong-family conversion,
incomplete completion, wrong account and wrong instrument for all four methods.
It additionally covers unavailable bulk responses, partial conversion followed
by failure, silently suppressed cancellation, missing single lookup and a returned
venue order different from the single query. Expected totals are 4 bounded
nonempty, 3 bounded empty, 1 bounded not-found and 38 unknown. The runner verifies
the complete matrix and expected outcomes before emitting evidence; a missing
case, unexpected outcome or error fails the command.

The actual methods forward only `instrument_id` for bulk requests, and only
venue/client order IDs for the single request. They do not forward bulk time
ranges, `open_only`, the fill command's venue order filter, or the single
command's instrument filter. The exercise uses unset ranges/default filters and
does not claim those filters were implemented. The single-query actual-method
cases use a venue order ID; client-ID-only matching is covered by pure contract
tests, not an additional authenticated/native query. No fixture proves complete
account positions, fill history, restart reconciliation or current venue truth.

## Reproduction and CI

From the repository root, using a separately prepared, exact 1.231.0 environment
with no protected credential variables present:

```bash
/path/to/isolated/python -m fit_gates.hyperliquid_testnet_readiness.report_fragments > /tmp/report-evidence.json
```

Run this only in an isolated, disposable development environment. The command
does not install packages or write runtime data. It emits deterministic JSON
bound to the runner, pure contract, reused lifecycle/probe helpers and installed
adapter source hashes. Repeated runs on the same environment must match.

Standard Python CI runs
`tests/python/test_hyperliquid_report_trust.py`: strict proof validation,
frozen evidence/source binding, recomputation of every assessment, matrix failure
cases, credential guard ordering and no-loop driver behavior. Optional adapter
execution requires the separate pinned environment; ordinary CI does not gain
a signer or venue connection. Existing Python test discovery covers these tests;
no duplicate CI job or root dependency is required for this change.

The historical [five-case lifecycle evidence](../../fit_gates/hyperliquid_testnet_readiness/lifecycle-evidence.json)
and its runner are unchanged. This supplements the
[fragment assessment](hyperliquid-adapter-fragment-fit.md) and
[readiness inventory](hyperliquid-testnet-readiness.md); it does not promote the
candidate into a production execution core.
