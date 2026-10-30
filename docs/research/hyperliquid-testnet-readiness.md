# Hyperliquid execution adapter: offline readiness assessment

Decision on 2026-10-30: **conditionally reuse an upstream execution adapter;
HOLD authenticated execution adoption.** NautilusTrader `1.231.0` is the first
candidate for the next isolated conformance exercise. The official SDK
`0.24.0` remains the protocol reference and fallback transport/signing
component if the framework fails the bounded fit gate. Do not build a second
OMS, signer or reconciliation framework merely to bypass untested upstream
behavior. Neither candidate is approved for orders or deployment.

This completes the offline comparison scope of [#159](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/159),
with execution-safety prerequisites from [#161](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/161).
It does not supersede [ADR-023](../DECISIONS/README.md#adr-023--wrap-nautilustrader-for-the-course-1-btc-perp-slice),
change the D41 pin, or enable TESTNET. Existing local startup, risk and cockpit
remain PAPER-only. Research/source pins and holdouts are untouched.

## Evidence and its limits

- [Package inspection](hyperliquid-testnet-packages.json) records exact PyPI
  metadata/wheel hashes. Only the small SDK wheel was downloaded to temporary
  inspection storage; it was not installed/imported and no upstream source is
  vendored here. Wheel acquisition is package-source access, not venue access.
- [Actual optional probe](hyperliquid-testnet-probe.json) records the installed
  isolated Nautilus package's configuration objects, two pure cloid conversions,
  method inventory and Python-source hashes on CPython 3.13.15/Linux x86_64,
  glibc 2.43. It does **not** establish that this installed package came from
  the listed wheel bytes or that its native binary is unmodified.
- The probe constructs configuration values and `ClientOrderId` values only.
  There are no execution/data/HTTP/WebSocket clients, factory `create` calls,
  keys, signatures, orders, account calls or captured market data. Its Python
  audit hook rejects sockets/subprocesses; that hook is not an OS network
  sandbox for native extensions. The only native function called is the pure
  ID-to-cloid conversion.
- Standard CI checks the frozen evidence and probe guards without installing
  either optional upstream dependency. It does **not** run the actual package
  probe or claim lifecycle/reconciliation conformance. The conformance cases
  below are a plan, not passing test results or a toy replacement OMS.

## Exact-version comparison

| Concern | NautilusTrader 1.231.0 legacy wheel | Official Python SDK 0.24.0 wheel |
|---|---|---|
| Runtime/license | Python >=3.12,<3.15; assessed cp313 manylinux_2_35 x86_64 wheel; LGPL-3.0-or-later | Python >=3.9,<4.0; py3-none-any; MIT; package and transitive dependencies still need review |
| Existing project use | Isolated D41 replay/sandbox WRAP; not a root dependency or approved venue execution route | Not a root dependency; source inspected only |
| Release API | `HyperliquidExecClientConfig`, `HyperliquidLiveExecClientFactory`; report methods present in installed source | `Exchange` and `Info`, order/cancel/status/fill/account methods; transport primitives rather than an OMS |
| Environment default | Config `environment=None`; factory helper resolves this to MAINNET. Explicit TESTNET config construction succeeds without a key | `Exchange(base_url=None)` feeds `Info`; require an explicit testnet base URL, never the SDK default |
| Constructor effects | Factories construct native HTTP/execution clients and may source environment credentials; not exercised | `Exchange.__init__` creates `Info`; absent metadata triggers REST loading even with `skip_ws=True`. Never instantiate during offline readiness |
| Identity | Pure native conversion maps project IDs to 128-bit hex cloids; mapping rebuilt from cached orders in `_sync_cloid_cache` | `Cloid` checks prefix/length but not hex characters; project boundary must validate the full 128-bit hex contract |
| Unknown submit | Installed `_submit_order` source logs a transport failure and returns awaiting reconciliation; no Python retry there. Native transport and complete recovery remain untested | Calls expose signed request responses; project still needs an outcome/reconciliation policy, not blind HTTP retries |
| Account/reports | Order/fill/position report methods exist; their network results, truncation, failure semantics and durability are not validated here | `Info` has account/open orders/fills/by-time/status queries; no durable process recovery is proved |
| Capabilities to isolate | Client also exposes unrelated products/actions; constrain to standard BTC perpetual, approved order/cancel surface | SDK also exposes transfers/withdrawals and logs signed action payloads in `_post_action` at DEBUG; never expose these methods or payload logging to the bot |
| Adoption cost | Reuse lifecycle/reporting machinery if conformance/support gates pass; keep project policy and evidence boundary small | Smaller protocol library, but more lifecycle/recovery ownership. Reject a fallback that quietly becomes another trading framework |

Source anchors are relative to the assessed installed package/wheel, whose
hashes are recorded above: Nautilus `config.py:96-154`, `factories.py:40-44`,
`execution.py:229-280,322-463,641-731`; SDK `exchange.py:83-110`,
`info.py:18-70,613-617`, `utils/types.py:194-203`.

The [release notice](https://github.com/nautechsystems/nautilus_trader/releases/tag/v1.231.0)
places 1.231.0 on the final legacy-v1 line, with limited critical-security
backports following v2 cutover. Confirm an actually supported release/branch
and maintenance window before adoption; a stable PyPI version number alone
does not settle that question. The [latest integration docs](https://nautilustrader.io/docs/latest/integrations/hyperliquid/)
use v2 class/factory names that differ from this wheel. Never combine those
examples with the legacy pin or silently upgrade D41. Assess any new version
in another isolated environment, with licensing/packaging and migration review.

## Proposed future profile: specification only

Before **any** client construction, require a separately approved execution
composition root to validate one immutable profile: environment TESTNET,
venue Hyperliquid, standard BTC perpetual, account namespace, explicit
`https://api.hyperliquid-testnet.xyz` REST and
`wss://api.hyperliquid-testnet.xyz/ws` endpoints, metadata source, signing
domain and separate durable storage namespace. Missing, empty, mixed,
mainnet, alternate-host/proxy or mismatched account configuration fails
closed. No default factory/environment resolution or mainnet public-collector
fallback is allowed. Revalidate after restart before enabling new exposure.

Initial scope excludes vaults, subaccounts, spot, HIP products, transfers,
withdrawals, leverage-changing calls, builder approval scripts and arbitrary
SDK/client methods. Set builder attribution false; never require a master-key
operation to make this test route work. Retrieve BTC asset identity, quantity
precision and contract settlement from the approved testnet metadata; do not
copy a mainnet universe index or assume a USD symbol means USD collateral.

Only a dedicated testnet agent/API signing key may later enter the approved
isolated execution process through protected secret injection. Keep the
master-wallet seed/key off every development/runtime host, container, GitHub,
browser, log and AI prompt. Operator authorization happens externally. Account
queries use the approved account address, not the agent address. Separate
signers per process/account scope and handle nonce state explicitly; never
reuse a revoked agent identity. Suppress raw signed payloads and secret-bearing
exception/configuration dumps. Root development and CI receive no signing key.

See the primary [wallet/nonce contract](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets),
[order/cloid contract](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint)
and [network endpoints](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket).

## Durable identity and evidence boundary

Persist approved project intent **before** sending, binding environment,
account, venue, instrument, strategy/config version, source commit, image
digest, correlation ID and exact normalized quantity/price/risk decision.
Store project ID -> validated `0x` + 32 hex cloid -> venue order ID, with unique
constraints and conflict detection. The native PAPER/D22-A ID strings cannot
be passed as raw cloids. The upstream conversion is reusable; the two recorded
examples demonstrate format, repeatability and distinct sample outputs, not
collision freedom, venue idempotency or durable recovery. Preserve mappings
across restarts and upgrades; never regenerate them under a changed algorithm.

Write timeout/disconnect means **unknown outcome**, not rejection or safe
resubmission. Reconcile cloid/order status, fills and account before deciding
anything further. A fresh nonce does not prove that an earlier order failed.
Do not re-run a strategy or replay its entry into an external venue as D22-A
does with disposable local sandboxes.

TESTNET audit/artifact schemas must remain separate from PAPER simulation and
its `native-paper-cockpit-v1` reader. Venue fees, funding, account balances and
PnL carry their own reconciliation/coverage state; never relabel simulated
cash as venue margin. The current browser cannot sign or control test trading.

## Next deterministic conformance work: not executed

Run against the actual pinned adapter with injected recorded protocol
responses or transport seams; assertions inspect adapter events, commands,
durable project records and independent expected arithmetic. Do not implement
a fake broker/state machine whose own outputs become its passing evidence.
If the version offers no practical seam, record that fit failure before
expanding a wrapper. Signing-key cases require a separately approved phase;
this assessment constructs no signer, including a throwaway one.

| Case | Required observable result |
|---|---|
| Missing/mixed environment, wrong endpoint/account/product | Refuse before factories, sockets, metadata reads or secret lookup; current PAPER guard still rejects TESTNET |
| Precision/minimum notional and native cloid | Correct standard BTC contract, finite exact units, protective bounds; stable ID mapping/conflicts rejected |
| Submit ACK, reject, partial IOC and resting cancel | Correct lifecycle and quantities; fill price/fee/source attribution from the adapter; no invented fill on ACK |
| Duplicate/out-of-order fill; cancel races with fill | Each venue fill applied once; late fills reconcile; filled exposure never disappears because cancellation arrived |
| Submit/cancel timeout or dropped ACK | Unknown outcome blocks new risk; status/fills/account resolved first; zero blind resend with a fresh nonce |
| Startup/reconnect reports, empty/failed/truncated history | Distinguish authoritative flat/empty from unavailable or incomplete; no risk while coverage or account identity is uncertain |
| Crash before send, after send/before ACK, after fill/before local commit | Durable approved intent and stable mapping survive; reconcile venue truth before further commands; no replayed duplicate entry |
| Stale BBO, mark or user feed; market/metadata gap | Block new exposure and stale fills independently; approved fresh fallback explicit; do not let unrelated fresh messages heal old quotes |
| Storage failure/reconciliation mismatch/loss or exposure breach | Fail closed; halt/cancel/approved reduce-only exit behavior recorded; cancel-all/dead-man behavior verified independently |
| Funding boundary and fees; restart across a boundary | Venue-account economics reconcile with fill/funding coverage and decimals; missing information is unavailable, not zero |
| Source/config/image mismatch or environment mixing | Refuse reuse; TESTNET evidence never enters PAPER or research holdout results |

Additional blocking project contracts from #161: prove `reduce_only` actually
reduces the signed position of the **same asset/account**, never opens from
flat or crosses zero; a caller boolean is insufficient. Existing PAPER risk
snapshots aggregate exposure and are not a ready authenticated per-asset book.
Prove fill-time hard notional/exposure limits under adverse short-price moves,
partial fills and gaps; decide cancel/clip/halt behavior explicitly before
execution adoption. Completing the PAPER input repair does not itself prove
these broader execution contracts.

Exit from HOLD requires supported exact release + compatible runtime/license
decision, the conformance evidence above, reviewed durable recovery and
credential procedure, and separate explicit operator approval for a bounded
network TESTNET phase. A later network test validates connectivity/lifecycle,
not profitability or real-liquidity execution quality. No automatic promotion,
watcher, runtime cutover, deployment or real capital follows this document.

## Reproducing the narrow optional probe

Use an already installed, isolated CPython 3.13.15 / Nautilus 1.231.0 environment
without account/key variables. Example for the existing assessment environment:

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/hl-ci-d01-venv/bin/python \
  fit_gates/hyperliquid_testnet_readiness/probe.py
```

It prints JSON to stdout and does not install packages, fetch sources or
write artifacts. An interpreter without the candidate exits 2; do not add it
to root dependencies to make this optional probe pass. The captured artifact
is historical evidence; compare runtime, source hashes and observations when
repeating, and review any drift instead of overwriting the previous result.
