# Hyperliquid adapter fragments: bounded offline fit result

Decision: **the legacy Python adapter exposes a useful signer-free test seam;
HOLD execution adoption.** This is the bounded engineering result for
[#168](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/168), following
[the readiness assessment](hyperliquid-testnet-readiness.md). It is not a
TESTNET connection, a complete adapter conformance suite or production reuse
approval. No environment, root dependency, D41 pin, study registration or
runtime has changed.

## Candidate maintenance, checked against primary sources

The [dated maintenance observation](../../fit_gates/hyperliquid_testnet_readiness/maintenance-observation.json)
records PyPI's default `1.231.0`, the published `2.0.0rc8` candidate (first
PyPI upload 2026-10-29), and no stable `2.0.0` release at observation time.
The [official rc8 release](https://github.com/nautechsystems/nautilus_trader/releases/tag/v2.0.0rc8)
labels it a prerelease with release-note date 2026-10-28. These are dated
observations, not a promise about future latest versions or support.

The [1.231.0 release notice](https://github.com/nautechsystems/nautilus_trader/releases/tag/v1.231.0)
marks the final intended legacy-v1 release. The maintainer's
[RFC and follow-up](https://github.com/nautechsystems/nautilus_trader/issues/4042)
state that new features target v2 and critical v1 security backports are
accepted for approximately three months after the **stable v2 release**,
after which the legacy branch freezes. Do not count that window from the
v1 release date or an RC date. A new v2 fit requires a separate exact-version
environment and migration/license review; this exercise does not upgrade D41
or turn the legacy pin into a supported production adoption decision.

## What actually ran

The [optional runner](../../fit_gates/hyperliquid_testnet_readiness/lifecycle.py)
executes unbound methods from the **installed NautilusTrader 1.231.0**
`HyperliquidExecutionClient` class against a small protocol self. It never
constructs that client or any execution/data/HTTP/WebSocket client. The source
inventory must match the four hashes from the earlier installed-package
probe before optional imports; the imported adapter path must match the
inspected installed source. Output records those hashes, the runner hash,
exact package version and Python/machine/libc runtime. This does not bind the
native binary or installed package to independently authenticated wheel bytes;
the earlier published-wheel metadata is an inventory, not that proof.

The injected seam consists only of:

- a recorded-submit callable returning synthetic PyO3 report objects, `None`,
  or a `TimeoutError`, plus an in-memory cloid mapping;
- lookups returning one real upstream order and its quote currency;
- a fixed clock/logger and an event delivery function.

It invokes actual `_submit_order`, immediate status-report processing,
cloid resolution and partial-fill handling. Inputs use real upstream
`LimitOrder`, `SubmitOrder`, `OrderStatusReport` and `FillReport` domain types,
including Python/PyO3 conversions. The event delivery seam instantiates the
real upstream `OrderSubmitted`, `OrderAccepted`, `OrderRejected` and
`OrderFilled` objects from generator arguments and applies them directly to
that upstream order. Quantity arithmetic and status transitions therefore
come from the real core order model. This bypasses the base execution
client's event generators, event bus and cache mutation machinery; it does
not verify their delivery or persistence semantics. There is no local
replacement broker, order state machine or signing implementation.

Report IDs and the nominal `HYPERLIQUID-OFFLINE` account ID are deterministic
synthetic **domain identifiers**, not an account address or configured
execution account. `BTC-USD-PERP.HYPERLIQUID`, quantity and price are nominal
fixtures, not metadata fetched from or verified against a venue. Reports
carry the generated cloid, so the actual upstream resolver maps them back to
the original project client ID before producing events. These observations
do not prove collision freedom, venue idempotency or durable ID storage.

The async submit fragment is driven inline without creating an event loop.
The recorded transport has no I/O or suspension; unexpected suspension is a
fit failure. This avoids even an event loop's local socket pair while Python
socket/process audit guards are installed. Credential/account variable
presence is refused before optional imports without reading those values.
The guard is not an OS network sandbox for Rust/native extensions. No signer
was constructed, including a throwaway one. The official SDK is not imported
or installed, and no factory-create, account, signing or venue method runs.

## Observations and limits

[Executed evidence](../../fit_gates/hyperliquid_testnet_readiness/lifecycle-evidence.json)
contains five hand-authored synthetic response cases. They are recorded
**adapter-domain report fixtures**, not captured venue traffic. The injected
return value sits after the native response parser; the real wire parser,
native transport and any native retry policy are unexercised.

| Fragment | Actual upstream order result | Narrow conclusion |
|---|---|---|
| ACCEPTED report | SUBMITTED → ACCEPTED; 0 filled, 0.10000 remaining | An ACK does not create a fill |
| REJECTED report | SUBMITTED → REJECTED; 0 filled | A rejection report remains terminal without invented exposure |
| Accepted + separate partial fill | PARTIALLY_FILLED; 0.04000 filled, 0.06000 remaining; fill 100000, commission 1.80000000 USDC | Real report conversion/event/core arithmetic preserve the fragment's quantities, fee and identifiers |
| Submit timeout | One submit call; remains SUBMITTED with no reject/fill | This Python fragment does not blindly resend after an uncertain result |
| Missing ACK/report (`None`) | One submit call; remains SUBMITTED | No fabricated acceptance or fill; reconciliation is still needed |

The partial IOC case intentionally stops before the remainder's terminal
cancel; it does not claim the venue would leave that remainder resting.
The uncertain cases do not implement or prove a project-wide new-exposure
halt, native retry prevention, status/fill/account reconciliation, startup
recovery or durable crash behavior. No second strategy invocation is made;
caller resubmission policy remains unproved.

Cancel/fill races, duplicates/out-of-order delivery, account/funding/balance
coverage, precision/minimum-notional validation, signing/nonce domains,
endpoint fallback, disconnect/reconnect, crashes and storage failures remain
HOLD gates from #159. The signed same-asset reduce-only contract (#167) is a
separate prerequisite and is not established by these fixtures. PAPER input
or risk fixes do not themselves prove venue execution safety.

## Reproducing and CI coverage

From the repository root, in the existing isolated exact-version environment
and without account/key variables:

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/hl-ci-d01-venv/bin/python \
  -m fit_gates.hyperliquid_testnet_readiness.lifecycle > /tmp/hyperliquid-fragments-repeat.json
```

Compare the complete output with the frozen artifact, including source and
runner hashes, runtime and observations. Successful process exit means that
the **five bounded fragments** matched explicit expected outcomes; it is not
an execution-readiness verdict. The result remains HOLD. An absent package,
wrong version/source, credential presence, unsafe mode, unexpected suspension
or upstream exception exits 2 with a generic message. No package installation
is performed to make the probe pass.

Normal CI does **not** install Nautilus or execute these actual adapter
methods. It checks frozen evidence arithmetic/limits/source linkage and the
runner's pre-import and coroutine guards. Changes under `fit_gates/` and the new tests select the existing Python
jobs. Documentation-only changes retain the normal docs-only skip, and the
docs-read property guard remains in the checks. No new heavy CI job or parallel workflow is
needed. Repeating the actual optional runner belongs to a separately reported
isolated check. No merge, future probe or green CI authorizes network testing,
keys, deployment, automatic promotion or real capital.
