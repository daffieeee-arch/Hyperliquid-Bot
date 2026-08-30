# Architecture Decision Register

This register records current high-level decisions and the conditions under which they should be revisited.

## COURSE-1B runtime transition status

Host-neutral Linux/amd64 OCI is now the platform boundary, with a supported Ubuntu LTS VPS as the
intended primary deployment profile. TrueNAS is no longer mandatory or primary; its existing
environment remains an optional protected profile. This narrows the host-specific scope of
ADR-003, ADR-012, ADR-016, ADR-017, ADR-019 and ADR-021 without authorizing a migration. The
definitive runtime ADR and factual VPS migration follow only after the local BTC-PERP vertical
slice passes.

## ADR-001 — Python for the quantitative/trading core

**Decision:** Use Python for market-data adapters, research, backtesting, strategies, portfolio/risk and initial execution.

**Why:** Strong quantitative ecosystem, rapid iteration, official Hyperliquid Python SDK, and sufficient performance for the initial seconds-to-days strategy horizons.

**Alternative considered:** Rust/C++/Go/TypeScript for the full core.

**Revisit when:** profiling shows a specific Python path is an economically meaningful latency bottleneck. Prefer replacing that isolated path with Rust rather than rewriting the platform.

---

## ADR-002 — TypeScript + React + Next.js for the trading cockpit

**Decision:** Use TypeScript, React and Next.js for the professional operator UI.

**Why:** Best fit for a dense realtime application with synchronized panels, keyboard workflows, multi-monitor layouts, advanced tables/charts and long-term maintainability.

**Alternatives considered:** Streamlit/Dash for the production UI.

**Revisit when:** only if product requirements materially change. Python UI frameworks remain suitable for temporary research tools but not the primary terminal.

---

## ADR-003 — ClickHouse for analytical/time-series data

**Decision:** ClickHouse is the primary analytical store for market, feature, signal, fill, portfolio and backtest time series.

**Why:** Columnar analytical performance, compression and suitability for large append-heavy datasets; integrates well with Grafana.

**Alternative considered:** PostgreSQL as the only database.

**Implementation note:** use a disposable local ClickHouse container in DEV and, when needed, a managed persistent instance on the approved runtime. Existing TrueNAS/ClickHouse state remains protected while that profile is retained or until migration is explicitly approved.

---

## ADR-004 — PostgreSQL for durable control/configuration state, introduced when needed

**Decision:** PostgreSQL stores strategy registry, model metadata, risk policies, operator/configuration state, experiment metadata and approvals when the durable control plane is introduced.

**Why:** These are relational/transactional concerns distinct from analytical market data.

**Constraint:** PostgreSQL is not a blocker for the first local vertical slice. Begin with explicit versioned contracts/configuration and introduce PostgreSQL before durable multi-service control state requires it.

---

## ADR-005 — Redis for bounded shared realtime state/events, introduced when needed

**Decision:** Redis provides fast current-state caches and lightweight event/stream distribution once multiple processes need shared low-latency state.

**Why:** Low-latency UI/service communication and decoupling.

**Constraint:** The first local slice may use bounded in-process queues. Redis is not the authoritative market-data or execution ledger and must use explicit memory limits.

---

## ADR-006 — Hyperliquid as primary derivatives venue

**Decision:** Hyperliquid is the initial primary venue for perpetuals and basis/carry. Bitvavo is the preferred candidate for the first very-small-capital spot deployment. The data architecture remains multi-venue.

**Why:** Hyperliquid offers strong automation support, public market data, perpetual products and a suitable API/agent-wallet model. Bitvavo provides an existing verified account, EUR funding, spot markets and useful free L2 data.

**Constraint:** Strategy interfaces must not become vendor-locked; execution and data are abstracted. Neither venue receives live capital before its own promotion gates are met.

---

## ADR-007 — Free market data first

**Decision:** Use Hyperliquid, Bitvavo, Kraken, Binance and other useful free sources plus our own collectors before purchasing institutional data.

**Why:** The first candidate edges can be researched without expensive subscriptions. Paid data does not guarantee alpha.

**Revisit when:** a specific missing dataset limits a promising hypothesis or fill model. Any paid feed should show measurable incremental out-of-sample value or substantial research-time savings.

---

## ADR-008 — Same strategy/risk path from paper to live

**Decision:** BACKTEST/PAPER/SHADOW/TESTNET/LIVE differ through execution adapters and configuration, not separate strategy implementations.

**Why:** Prevents paper/live behavioral drift and reduces the chance of introducing untested logic at promotion time.

---

## ADR-009 — Research plane separated from trading plane

**Decision:** Resource-intensive research jobs cannot share failure fate or unrestricted resources with realtime collectors/risk/execution.

**Why:** A runaway backtest must never impair continuous risk management or data ingestion.

---

## ADR-010 — AI assists research; it does not bypass governance

**Decision:** Hermes/LLMs may generate hypotheses, code, experiments, anomaly analysis and reports. They may not directly self-promote new strategies into live capital or override hard risk limits.

**Why:** Avoid uncontrolled online overfitting and preserve reproducibility/accountability.

---

## ADR-011 — Secrets isolated from frontend and master wallet

**Decision:** The Hyperliquid master-wallet seed/private key never resides on the development workstation or trading host. Dedicated Hyperliquid agent/API wallets are used for automation. Centralized-exchange credentials use minimum permissions with withdrawals disabled. Browser code never receives signing keys.

**Why:** Minimize blast radius and make the security boundary explicit from day one.

---

## ADR-012 — Keep runtime infrastructure lean

**Decision:** Start with ClickHouse + Python services + Next.js + Grafana/Alloy, adding PostgreSQL and Redis when their responsibilities become necessary. Do not add Kafka, Kubernetes, Elasticsearch/OpenSearch or Spark without a demonstrated bottleneck.

**Why:** Preserve operational simplicity and reliability across the host-neutral Linux/OCI boundary, including constrained VPS and optional shared TrueNAS profiles.

---

## ADR-013 — Observe many venues; trade on few venues

**Decision:** Public market data may be collected from multiple venues, but live execution venues are introduced one at a time through separate approval gates.

**Initial role split:**

- Hyperliquid: perpetuals, basis/carry and public data;
- Bitvavo: EUR on-ramp, spot and candidate first small-live spot venue;
- Kraken: public data, official paper/MCP research and optional future hedge/backup venue;
- Binance: public reference data initially.

**Why:** Multi-venue data improves price discovery and strategy research, while each live venue adds nonlinear operational, reconciliation, security and counterparty complexity.

**Promotion requirement:** A new live venue must demonstrate measurable benefit such as lower all-in costs, better liquidity, a required hedge, profitable cross-venue functionality or meaningful operational redundancy.

---

## ADR-014 — EUR/USDC conversion belongs to treasury routing

**Decision:** EUR-to-USDC conversion may be automated through a venue adapter, including Bitvavo's `USDC-EUR` market, but conversion is controlled by a dedicated treasury/quote-asset policy rather than individual strategy code.

**Why:** The cheapest route can vary among direct EUR trading, conversion to USDC, and use of existing USDC inventory. Repeated conversion can create unnecessary fees, spread, FX exposure and stablecoin risk.

**Required controls:**

- dynamically discovered markets and fee tiers;
- all-in route-cost comparison;
- target/min/max EUR and USDC balances;
- per-order and daily conversion limits;
- stale-data and slippage guards;
- optional human approval for large conversions;
- explicit EUR/USD and USDC risk reporting;
- complete conversion audit trail.

---

## ADR-015 — Windows 11 + WSL2 is the primary development environment

**Decision:** Develop the project on the Windows workstation using WSL2 Ubuntu, with Codex configured to run in WSL. Keep the repository inside the WSL Linux filesystem.

**Why:** Provides Linux parity, fast local iteration, strong Codex/desktop/mobile Remote workflows, better frontend/debugging ergonomics and access to the workstation's CPU/GPU without destabilizing the continuous runtime.

**Alternatives considered:** direct source development on a runtime host; native Windows/PowerShell as the primary runtime; source on an SMB share.

**Constraints:** no normal live trading credentials on the workstation; use workspace-scoped Codex permissions; store source under the Linux home filesystem rather than `/mnt/c` or SMB.

---

## ADR-016 — The deployment/runtime host is not the interactive development host

**Decision:** Any 24/7 PAPER/SHADOW/LIVE host runs CI-built Linux/OCI images. Do not run production-like services from a mutable repository checkout and do not hot-edit running containers.

**Why:** Isolates experimentation from persistent services, reduces failure blast radius, enables deterministic rollback and keeps the Windows PC optional during continuous operation.

**Constraint:** Authorized runtime tooling may operate deployments and project resources, but source changes return through Git and CI. The Ubuntu LTS VPS profile, optional TrueNAS profile and later migration remain subject to their own scoped controls.

---

## ADR-017 — Build once in CI and deploy by immutable image digest

**Decision:** GitHub Actions builds release images, publishes them to private GHCR and records source commit, tag and digest. The approved runtime deploys a reviewed digest, never a floating `latest` tag.

**Why:** Reproducibility, supply-chain traceability, reliable rollback and confidence that PAPER/SHADOW/LIVE can use the same artifact.

**Constraint:** ordinary merge does not automatically deploy. PAPER deployment initially requires explicit approval; LIVE always remains protected.

---

## ADR-018 — Separate software promotion from strategy promotion

**Decision:** A strategy and a software artifact each pass their own gates. Live eligibility requires an approved strategy/version, approved image digest, approved configuration and approved runtime environment.

**Why:** A profitable model can run on broken software, while perfect software can execute an unprofitable model. Combining the approvals obscures risk.

---

## ADR-019 — Local development uses disposable data; the runtime owns continuous history

**Decision:** DEV uses synthetic fixtures, deterministic replay and bounded data exports in disposable local services. The selected durable runtime store holds the authoritative self-collected 24/7 dataset.

**Why:** Keeps development fast/reproducible and prevents accidental mutation or copying of large production-like datasets.

**Constraint:** Windows may query approved runtime data read-only or receive bounded exports; it does not directly mutate the runtime database. Existing TrueNAS/ClickHouse data receives the same protection while that optional profile is retained.

---

## ADR-020 — Maximum useful MCP capability with scoped standing privilege

**Decision:** Do not make all MCPs globally read-only. Give Hermes the capabilities required to operate the project, while separating routine permissions from destructive/admin permissions.

**Examples:**

- Grafana MCP may edit Hyperliquid dashboards/alerts, while its ClickHouse datasource stays read-only;
- TrueNAS MCP may inspect and operate project apps/datasets, while destructive pool/dataset/update/reboot actions require explicit approval;
- ClickHouse research is read-only, while migrations use a separate controlled identity;
- paper trading tools may be available, while withdrawal/transfer tools are never exposed.

**Why:** Enables meaningful autonomy without giving every agent permanent, unrelated, high-blast-radius authority.

---

## ADR-021 — The optional early-release TrueNAS profile is PAPER-only by default

**Decision:** If retained, TrueNAS 26 BETA.3 may host research and PAPER services with backups and monitoring. It is not the primary deployment profile. Material live capital requires a supported stable runtime release or explicit documented risk acceptance and repeated soak/recovery testing.

**Why:** Operating-system maturity is part of execution risk, not merely infrastructure preference.

---

## ADR-022 — One provenance-complete market-event envelope, introduced atomically

**Status (2026-08-29): Accepted design, implementation deferred by COURSE-1.** Existing contracts,
tests and identities remain intact and dormant. Phase 1A-3B1C-2, 3B1C-3, 3B1D and the
provenance-complete form of 3B2 are not the active delivery path while the project proves one
BTC-PERP replay-to-PAPER consumer. Deferral changes priority, not the recorded semantics below.

**Decision:** The final Silver boundary uses one mandatory `MarketEventEnvelopeV3`. Phase
1A-3B1A defines its pure contract spine, but the contracts remain dormant. Existing Hyperliquid
and Binance producers continue to emit only `MarketEventEnvelope` schema v2 through Phase
1A-3B1C. Phase 1A-3B1D atomically migrates both normalizers and the Hyperliquid collector, removes
the outer-v2 constant and `is_gap`, and leaves no permanent wrapper, dual writer or v2 producer.

The dependency direction is one-way:

```text
contracts.py <- data_provenance.py <- instrument_metadata.py <- market_event_v3.py
```

Lower layers never import `market_event_v3.py`; none of these modules imports a venue DTO,
normalizer or collector. Constructors validate caller-supplied values, deterministic derivations
and explicitly supplied finite catalogues only. They do not prove capture order, sink acceptance,
runtime coverage, reconnect behavior, run completeness or server-side catalogue uniqueness.

### Canonical encoding and identities

Computed IDs use compact ASCII JSON arrays, never mappings, unordered collections, object
representations, Python hashes or `native_symbol` where canonical instrument identity is required.
Every ID wrapper validates its exact tag, component types, nested shape and canonical re-encoding.
Caller-supplied opaque `CollectorRunId`, `NormalizationRunId`, `MetadataAuthorityId`,
`SourceEventId`, `SourceTransactionId` and `CorrelationId` values remain distinct validated value
objects and are never silently regenerated. Existing Hyperliquid and Binance source-event strings
remain byte-for-byte unchanged.

The exact computed preimages are:

| Tag | Ordered components after the tag |
| --- | --- |
| `feed-product-v1` | venue, source environment, source network, opaque product code, access requirement, entitlement class, transport, wire encoding |
| `feed-capability-set-v1` | feed-product ID, sorted unique bounded capability-code array |
| `feed-capabilities-observation-v1` | capability-set ID, effective-from UTC, boundary basis, optional source-declared ending, observed-at UTC |
| `connection-session-v1` | collector-run ID, non-negative connection ordinal |
| `adapter-feed-binding-v1` | adapter code, feed-product ID, venue, event-activation requirement |
| `subscription-spec-content-v1` | feed-product ID, exact allowed wire method, exact allowed subscription type, Hyperliquid `["coin",coin]` or Binance sorted `["id",request ID]` and `["params",[sorted unique streams]]` rows |
| `subscription-spec-v1` | feed-product ID, exact allowed wire method, exact allowed subscription type, parameter count, SHA-256 of exact `subscription-spec-content-v1` text |
| `subscription-plan-content-v1` | feed-product ID, adapter-binding ID, sorted `[spec ID,full spec content]` rows, sorted `[canonical instrument ID,native symbol,["public-source-selector-v1",selector kind,selector value],spec ID]` rows, sorted `[spec ID,adapter profile,event family,family version,payload type]` rows, sorted `[connection-option kind,public endpoint-profile value]` rows |
| `subscription-plan-v1` | feed-product ID, adapter-binding ID, SHA-256 of exact `subscription-plan-content-v1` text |
| `subscription-attempt-v1` | connection-session ID, subscription-spec ID, non-negative attempt ordinal |
| `raw-record-v1` | feed-product ID, collector-run ID, connection-session ID, ingress ordinal, frame kind, payload SHA-256 |
| `raw-record-content-v1` | raw schema version, raw-record ID, subscription-plan ID, sorted attempt-snapshot rows, receive UTC, receive monotonic ns, collector version, collector commit, payload length, payload SHA-256 |
| `instrument-specification-content-v1` | canonical instrument ID, quantity unit, contract form, canonical multiplier, multiplier asset, settlement asset, instrument expiry, last-trading UTC, settlement UTC, sorted `[price-reference role,reference ID]` rows |
| `instrument-specification-v1` | SHA-256 of canonical instrument ID, exact canonical-content length, SHA-256 of exact `instrument-specification-content-v1` text |
| `instrument-metadata-observation-v1` | canonical instrument ID, specification ID, metadata-authority ID, effective-from UTC, boundary basis, optional source-declared ending, observed-at UTC, optional raw-record ID |
| `coverage-scope-content-v1` | feed-product ID, sorted spec IDs, sorted canonical instrument IDs, event family, family schema version, payload type |
| `coverage-scope-v1` | coverage domain, feed-product ID, event family, family schema version, payload type, spec count, instrument count, Merkle root of sorted spec IDs, SHA-256 of sorted instrument IDs |
| `subscription-spec-membership-proof-v1` | coverage-scope ID, subscription-spec ID, member index, member count, ordered Merkle sibling SHA-256 values |
| `coverage-epoch-v1` | coverage-scope ID, collector-run ID, non-negative epoch ordinal, activation UTC, activation monotonic ns |
| `coverage-evidence-v1` | scope ID, epoch ID, collector-run ID, evidence kind, exact typed source reference, observed UTC, observed monotonic ns |
| `normalization-failure-evidence-v1` | raw-record ID, normalization-run ID, raw-event index or null, source-event ID when established or null otherwise (always null before indexing), bounded failure category, exact coverage-scope ID |
| `normalization-outcome-v1` | parser-only legacy outer identity with the historical frame-status set: normalization-run ID, raw-record ID, normalizer version, normalizer commit, frame status, optional decoded count, normalization-outcome-content SHA-256 |
| `normalization-outcome-v2` | current writer identity with the same ordered components and content layouts, plus the closed `mixed_indexed_failure` status; the same lower-layer strong ID is re-exported by `market_event_v3` |
| `normalization-outcome-sink-failure-coverage-binding-v1` | normalization-outcome ID, coverage-mutation-batch ID, raw-coverage-fanout-binding ID, coverage-evidence kind |
| `coverage-transition-v1` | scope ID, epoch ID, positive transition ordinal, previous status, next status, reason, coverage-evidence ID |
| `coverage-initialization-v1` | collector-run ID, coverage-scope ID, coverage-epoch ID, initial status, initial reason, activation UTC, activation monotonic ns, evidence ID, evidence UTC, evidence monotonic ns |
| `coverage-state-reference-v1` | `initialization`, coverage-initialization ID; or `transition`, coverage-initialization ID, exact predecessor coverage-state-reference ID, latest coverage-transition ID; this is a candidate token until commit acceptance |
| `coverage-target-catalog-content-v1` | subscription-plan ID, sorted unique plan-derived leaf coverage-scope IDs |
| `coverage-target-catalog-v1` | subscription-plan ID, target count, SHA-256 of exact `coverage-target-catalog-content-v1` text |
| `coverage-fanout-proof-content-v1` | fan-out kind, subscription-plan ID, coverage-target-catalog ID, connection-session ID, exact kind-specific source row, sorted unique selected coverage-scope IDs |
| `coverage-fanout-proof-v1` | fan-out kind, subscription-plan ID, coverage-target-catalog ID, target count, SHA-256 of exact `coverage-fanout-proof-content-v1` text |
| `coverage-fanout-proof-content-v2` | exact identified-rejection fan-out kind, subscription-plan ID, coverage-target-catalog ID, connection-session ID, exact `exact-identified-rejections-v1` source row, sorted unique selected Silver-normalization scope IDs |
| `coverage-fanout-proof-v2` | exact identified-rejection fan-out kind, subscription-plan ID, coverage-target-catalog ID, target count, SHA-256 of exact `coverage-fanout-proof-content-v2` text |
| `coverage-fanout-proof-content-v3` | fan-out kind, subscription-plan ID, coverage-target-catalog ID, connection-session ID, exact closed kind-specific source row, sorted unique selected coverage-scope IDs |
| `coverage-fanout-proof-v3` | fan-out kind, subscription-plan ID, coverage-target-catalog ID, target count, SHA-256 of exact `coverage-fanout-proof-content-v3` text |
| `coverage-mutation-no-op-v1` | coverage-scope ID, current coverage-state-reference ID, requested status, initial reason, transition reason, coverage-evidence ID, `already-at-or-beyond-requested-severity` |
| `coverage-mutation-batch-content-v1` | legacy content preimage with no active writer: coverage-fanout-proof ID, exact raw-coverage-fanout-binding ID or null, sorted complete expected pre-state rows, sorted initialization IDs, sorted transition IDs, sorted no-op rows, sorted complete resulting coverage-state-reference IDs |
| `coverage-mutation-batch-v1` | legacy parser-only identity: coverage-fanout-proof ID, target count, SHA-256 of exact v1 content |
| `coverage-mutation-target-decision-content-v1` | target ordinal, coverage-scope ID, expected pre-state ID or null, mutation disposition, initialization ID or null, transition ID or null, no-op row or null, resulting coverage-state-reference ID |
| `coverage-mutation-batch-content-v2` | coverage-fanout-proof ID, exact raw-coverage-fanout-binding ID or null, target count, ordered SHA-256 commitments to every exact target-decision row |
| `coverage-mutation-batch-v2` | coverage-fanout-proof ID, target count, SHA-256 of exact `coverage-mutation-batch-content-v2` text |
| `coverage-commit-acceptance-content-v1` | legacy content preimage with no active writer: coverage-mutation-batch ID and complete resulting coverage-state-reference IDs |
| `coverage-commit-acceptance-v1` | legacy parser-only identity: coverage-mutation-batch ID, resulting-state count and content SHA-256 |
| `coverage-commit-resulting-state-content-v1` | result ordinal and exact resulting coverage-state-reference ID |
| `coverage-commit-resulting-states-content-v1` | result count and ordered SHA-256 commitments to every result row |
| `coverage-commit-acceptance-content-v2` | coverage-mutation-batch ID, result count, SHA-256 of exact ordered result-set content |
| `coverage-commit-acceptance-v2` | coverage-mutation-batch ID, result count, SHA-256 of exact `coverage-commit-acceptance-content-v2` text |
| `committed-coverage-state-v1` | exact candidate coverage-state-reference ID, matching coverage-commit-acceptance ID whose result set contains that state |
| `delivery-batch-content-v1` | ordered materialization-key texts; retained lower locator content |
| `delivery-batch-v1` | item count, SHA-256 of exact `delivery-batch-content-v1` text; retained lower locator ID |
| `delivery-attempt-v1` | destination ID, lower delivery-batch ID, non-negative attempt ordinal; retained lower locator ID |
| `logical-source-key-v1` | feed-product ID, source-event ID |
| `observation-key-v1` | raw-record ID, non-negative raw-event index |
| `materialization-key-v1` | normalization-run ID, observation-key ID, event family, family version, payload type |
| `raw-event-normalization-scope-binding-v1` | raw-record ID, full-record integrity SHA-256, subscription-plan ID, raw-event index, subscription-spec ID, subscription-attempt ID, attempt status, canonical-instrument-ID SHA-256, complete `public-source-selector-v1` row, coverage-scope ID |
| `raw-frame-normalization-scope-binding-v1` | raw-record ID, full-record integrity SHA-256, subscription-plan ID, coverage-scope ID |
| `raw-event-normalization-outcome-v1` | observation-key ID, complete `raw-event-normalization-scope-binding-v1` row, disposition, optional logical-source-key ID, optional materialization-key ID, optional bounded evidence |
| `normalization-outcome-content-v1` | ordered index-outcome IDs, ordered committed-materialization IDs, sorted evidence, sorted coverage-transition IDs, optional pre-index `raw-frame-normalization-scope-binding-v1` row |
| `raw-coverage-fanout-snapshot-content-v1` | complete sorted `[subscription-spec ID,subscription-attempt ID,attempt status]` rows from the exact raw record |
| `raw-coverage-fanout-binding-content-v1` | raw-record ID, full-record integrity SHA-256, subscription-plan ID, connection-session ID, coverage-fanout-proof ID, SHA-256 of exact `raw-coverage-fanout-snapshot-content-v1` text |
| `raw-coverage-fanout-binding-v1` | raw-record ID, coverage-fanout-proof ID, snapshot-content SHA-256, SHA-256 of exact `raw-coverage-fanout-binding-content-v1` text |
| `frame-atomic-abort-primary-cause-content-v1` | sorted unique primary coverage-evidence IDs |
| `frame-atomic-abort-evidence-v1` | raw-record ID, normalization-run ID, target raw-event index, target source-event ID, target Silver-normalization scope ID, primary-cause count, SHA-256 of exact `frame-atomic-abort-primary-cause-content-v1` text |
| `normalization-source-conflict-binding-v1` | source-conflict coverage-evidence ID, normalization-run ID |
| `normalization-coverage-lineage-content-v2` | legacy content preimage with no active writer, containing complete primary, abort, conflict, transition, no-op and resulting-state lists |
| `normalization-coverage-lineage-v2` | legacy parser-only identity paired only with a v1 coverage-mutation batch |
| `normalization-coverage-lineage-item-content-v1` | lineage role, item ordinal and exact typed ID or no-op row |
| `normalization-coverage-lineage-role-content-v1` | lineage role, item count and ordered SHA-256 commitments to every exact role item |
| `normalization-coverage-lineage-content-v3` | v2 coverage-mutation-batch ID, exact raw-coverage-fanout-binding ID and, for each ordered role, its item count and ordered role commitment |
| `normalization-coverage-lineage-v3` | prepared v2 coverage-mutation-batch ID, all six role counts and SHA-256 of exact compact v3 content |
| `normalization-outcome-content-v2` | ordered index-outcome IDs, ordered committed-materialization IDs, sorted evidence, exact prepared normalization-coverage-lineage ID, optional pre-index `raw-frame-normalization-scope-binding-v1` row |
| `delivery-item-commitment-v1` | materialization-key ID, lowercase SHA-256 of exact serialized event content |
| `delivery-item-commitments-v1` | complete ordered `[materialization-key ID,event-content SHA-256]` rows; its SHA-256 is the aggregate item-content digest |
| `normalization-delivery-batch-content-v1` | normalization-outcome ID, complete ordered item-commitment rows, item count, aggregate SHA-256 of exact `delivery-item-commitments-v1` text |
| `normalization-delivery-batch-commitment-v1` | retained lower `delivery-batch-v1` ID, normalization-outcome ID, item count, SHA-256 of exact `normalization-delivery-batch-content-v1` text |
| `normalization-delivery-attempt-binding-v1` | retained lower `delivery-attempt-v1` ID, exact normalization-delivery-batch-commitment ID |
| `normalization-delivery-outcome-v1` | normalization-delivery-attempt-binding ID, knowledge status, bounded reason, observed UTC, observed monotonic ns |
| `delivery-commit-acceptance-v1` | accepted normalization-delivery-outcome ID, exact normalization-delivery-batch-commitment ID |
| `delivery-commit-failure-v1` | definitely-not-accepted or acceptance-uncertain normalization-delivery-outcome ID |

Nested typed identifiers are encoded in these arrays as their canonical JSON text string, not as
nested identity arrays. Genuine nested component collections use JSON arrays. Public wire
identity uses closed typed semantics rather than arbitrary names or mappings: the current
Hyperliquid product admits only `subscribe`/`trades` plus exact `coin`, while the Binance Spot
product admits only `SUBSCRIBE`/`@trade` plus exact public `id` and sorted stream names. The only
current connection option is a closed non-secret public endpoint-profile catalogue value.

The spec-member root in `coverage-scope-v1` is reproducible rather than an opaque set hash. Sort
unique spec IDs by canonical text; SHA-256 hash each leaf as
`["coverage-scope-spec-member-leaf-v1",spec ID]`; pad to the next power of two with position-bound
leaves `["coverage-scope-spec-padding-leaf-v1",position]`, where `position` is the zero-based
padded-leaf index; and SHA-256 hash each ordered pair as
`["coverage-scope-spec-member-node-v1",left digest,right digest]` until one root remains. Padding
leaves are SHA-256 hashes too. The
instrument-members digest is SHA-256 over
`["coverage-scope-instrument-members-v1",sorted canonical instrument IDs]`. Every JSON array in
this paragraph uses the same compact ASCII canonical encoder.

Coverage evidence embeds exactly one closed typed source row:

| Source tag | Ordered components after the tag |
| --- | --- |
| `initial-activation-evidence-v1` | connection-session ID, acknowledged-attempt count, spec-member Merkle root, SHA-256 of `["initial-activation-attempts-v1",sorted [attempt ID,"acknowledged"] rows]` |
| `transport-ambiguity-evidence-v1` | feed-product ID, connection-session ID, optional subscription-attempt ID, optional complete `subscription-spec-membership-proof-v1` row |
| `raw-record-evidence-v1` | raw-record ID, identified coverage-scope ID |
| `upstream-coverage-transition-evidence-v1` | committed upstream coverage-state ID whose state contains a transition |
| `upstream-coverage-state-evidence-v1` | committed upstream coverage-state ID |
| `normalization-failure-evidence-reference-v1` | exact `normalization-failure-evidence-v1` ID |
| `normalization-outcome-evidence-v1` | exact parser-valid normalization-outcome v1 or v2 ID, identified Silver-normalization coverage-scope ID |
| `source-sequence-break-evidence-v1` | feed-product ID, sequence role, namespace/domain, non-negative first and last values, identified coverage-scope ID |
| `source-event-conflict-evidence-v1` | feed-product ID, source-event ID, raw-record ID, raw-event index, identified coverage-scope ID |
| `acknowledgement-evidence-v1` | subscription-attempt ID, `acknowledged`, complete `subscription-spec-membership-proof-v1` row |
| `reconnect-evidence-v1` | feed-product ID, connection-session ID |
| `authoritative-state-snapshot-evidence-v1` | raw-record ID |
| `raw-rejection-normalization-unknown-source-v1` | fixed `unknown` membership, fixed `before-parsing` boundary, upstream mutation-batch ID, positional committed-state ID, result ordinal, exact raw-rejection evidence ID, raw fan-out-binding ID, raw-record ID, full-record integrity SHA-256, exact downstream Silver scope ID |

The outer `coverage-evidence-v1` row additionally binds scope ID, epoch ID, collector-run ID,
evidence kind and injected UTC/monotonic observation boundaries. Thus none of these rows is a free
label, and membership-bearing evidence can be checked against the exact high-cardinality scope.
The final source row belongs only to the opt-in 3B1C-1G evidence-v3 route described below; it is not
accepted by evidence v1 or v2.

Coverage fan-out uses one of these closed kind-specific source rows. New writers bind every kind
inside `coverage-fanout-proof-content-v3`; v1 remains parser-only for historical non-rejection
kinds and v2 remains parser-only for historical identified-rejection kinds:

| Source tag | Ordered components after the tag |
| --- | --- |
| `handshake-before-send-v1` | connection-session ID; selects no scope |
| `possibly-delivered-specs-v1` | complete sorted `[subscription-attempt ID,attempt status]` snapshot rows, sorted selected `SEND_STARTED`/`SENT` attempt IDs |
| `one-possibly-delivered-spec-v1` | complete nested `possibly-delivered-specs-v1` row for a one-spec plan |
| `acknowledged-active-v1` | coverage domain, complete sorted attempt/status snapshot rows, sorted explicitly selected `ACKNOWLEDGED` attempt IDs |
| `exact-routed-events-v1` | sorted unique `[attempt ID,spec ID,canonical instrument ID,event family,family version,payload type]` rows |
| `exact-identified-rejections-v1` | sorted non-empty `exact-identified-rejection-target-v1` rows, sorted optional `acknowledged-routed-target-v1` rows |
| `exact-identified-rejection-target-v1` | feed-product ID, connection-session ID, subscription-spec ID, subscription-attempt ID, captured `pending`/`send-started`/`sent` status, complete public-source-selector row, canonical instrument ID, adapter profile, event family, family version, payload type, Silver-normalization coverage-scope ID |
| `acknowledged-routed-target-v1` | feed-product ID, connection-session ID, subscription-spec ID, subscription-attempt ID, captured `acknowledged` status, complete public-source-selector row, canonical instrument ID, adapter profile, event family, family version, payload type, Silver-normalization coverage-scope ID |
| `all-possibly-active-v1` | coverage domain, event family or null, family version or null, payload type or null, complete sorted attempt/status snapshot rows |

The active compact preimages are exactly:

```text
["coverage-fanout-proof-content-v3",fanout_kind,subscription_plan_id,
 coverage_target_catalog_id,connection_session_id,kind_specific_source_row,
 sorted_target_scope_ids]
["coverage-fanout-proof-v3",fanout_kind,subscription_plan_id,
 coverage_target_catalog_id,target_count,content_sha256]
```

The proof retains the exact typed `CoverageTargetCatalog`; the catalog is the sole retained parent
for its complete typed `SubscriptionPlanIdentity`. The full plan/catalog values are therefore
available for stored rederivation without being duplicated in either compact preimage.

The target catalogue represents configured plan leaves. The selected rows then distinguish exact
possibly delivered, acknowledged, routed and complete relevant possibly-active populations. An
acknowledgement proof retains the complete current snapshot but selects only the explicitly named
non-empty ACK subset; all leaves of those specs and no leaves from earlier unselected ACKs become
targets. For
Silver pre-index failures, family, family version and payload are mandatory; unrelated families
cannot enter the selected target slice. The complete possibly-active attempt population remains in
the proof even when that family filter excludes some of its specs; it is aggregate pre-index
uncertainty, not per-event routing evidence. A no-attempt handshake ambiguity remains lifecycle evidence but
cannot create ordinal-zero coverage.

Canonical content including `raw-record-content-v1`, `instrument-specification-content-v1`,
`subscription-spec-content-v1`, `subscription-plan-content-v1`, `coverage-scope-content-v1`,
`coverage-mutation-batch-content-v2`, `raw-coverage-fanout-binding-content-v1`,
`normalization-coverage-lineage-content-v3`, `delivery-batch-content-v1`,
`normalization-delivery-batch-content-v1` and both normalization-outcome content versions remains
independently validated beside its bounded digest identity where applicable. These SHA-256 values
are integrity/content commitments, not a substitute for retaining the full canonical content. UTC
input is an exact built-in,
timezone-aware `datetime` with zero offset and serializes with a four-digit year and exactly six
fractional digits. Decimal text uses `Decimal.as_tuple()` only: finite values, at most 128
coefficient digits, exponent in `[-128,128]`, signed zero as `0`, fixed-point output of at most 256
characters, and no rounding, `normalize()`, `quantize()` or active-context dependence. Canonical
instrument IDs are instead structurally reconstructed against the existing `Instrument` contract
and do not inherit the generic 256-character metadata-text bound.

Every copied or canonicalized value has a finite resource ceiling. Opaque caller IDs are at most
4,096 characters; canonical ID wrappers at most 8,388,608 serialized characters; canonical
`Instrument` IDs at most 1,048,576; instrument-specification content at most 4,194,304;
subscription-spec content at most 2,097,152; and plan/scope high-cardinality content at most
16,777,216. Raw application messages are at most 1,048,576 bytes. Canonical arrays allow at most
65,536 items per array, depth 32 and 100,000 canonical value nodes. More specific repeated-field
ceilings are:

| Repeated value | Maximum items |
| --- | ---: |
| feed capabilities | 7 |
| Binance Spot streams | 1,024 |
| public wire parameters | 2 |
| subscription specs, instrument bindings and normalization bindings | 1,024 each |
| public connection options | 1 |
| raw attempt snapshots | 4,096 |
| supplied attempt or raw-record validation sequences | 65,536 each |
| coverage spec members or instrument members | 1,024 each |
| subscription membership-proof siblings | 10 |
| acknowledged activation attempts | 1,024 |
| delivery batch members | 4,096 |
| instrument price references | 32 |
| supplied metadata specifications or observations | 65,536 each |
| source-time facts or source-sequence ranges | 32 each |
| decoded event bindings, normalization index outcomes or committed materialization keys | 4,096 each |
| normalization evidence values | 8 |
| normalization coverage-transition references | 1,024 |
| normalization lineage additional-primary, frame-abort or source-conflict tuples | 4,096 each |
| normalization lineage complete primary-evidence union | 8,192 |
| coverage target-catalogue, fan-out and mutation targets | 4,096 each |

Literal boundary and boundary-plus-one tests bind every numeric ceiling through the shared
validator. Owner-level tests also prove that each owning constructor or finite-sequence validator
applies its N/N+1 boundary, directly at manageable literal ceilings or with a lower test-local
module ceiling for high-cardinality fields. Closed semantic catalogues may impose a smaller
maximum than the generic storage ceiling.

### Feed, subscription and raw-observation boundaries

Feed identity records a product's access requirement, not the caller's credentials or authority.
Capabilities are sorted, bounded, append-only point-in-time catalogue observations and never grant
authorization or contain account or credential state. Initial exact catalogue products are
Hyperliquid production/mainnet public WebSocket market data and Binance production/mainnet Spot
JSON market streams; Bitvavo Standard, Bitvavo Market Data Pro and Binance USDⓈ-M require distinct
future product identities.

`SubscriptionPlanIdentity` validates the complete desired configuration as independently
testable canonical content, then exposes a bounded versioned SHA-256 content-addressed plan ID.
Any semantic content change changes that ID, while realistic multi-instrument plans do not inflate
the strongly typed ID. One exact outbound wire request is a `SubscriptionSpecIdentity`; family and
payload semantics are deliberately outside its ID. Adapter profile bindings must match the plan's
structured adapter/feed binding. API keys, tokens, signatures, account or credential state,
authorization state, secret endpoint text, timeouts and retry controls have no representable
public-semantic field and fail before canonical content or an ID can be produced. Secret-bearing
values are also excluded from representations and errors.

Each instrument binding includes the exact existing `Instrument`, canonical instrument ID,
adapter-native symbol, closed public source selector and exact wire-spec ID. Hyperliquid `coin`
must equal the native symbol; Binance Spot selectors are exact lowercase
`<native-symbol>@trade` streams present once in that spec. A selector binds exactly one canonical
market, each spec selector must be bound, and Binance request IDs are unique within one plan so
acknowledgements cannot create ambiguous lineage.

A `SubscriptionAttemptIdentity` binds one session, spec and zero-based attempt ordinal. The
ACK-race-safe transitions are `PENDING -> SEND_STARTED`, `SEND_STARTED -> SENT`,
`SEND_STARTED -> ACKNOWLEDGED`, `SENT -> ACKNOWLEDGED` and idempotent
`ACKNOWLEDGED -> ACKNOWLEDGED`; regressions fail closed. A raw ACK observation keeps the pre-parse
snapshot (normally `SENT`, possibly `SEND_STARTED`); a later transition is a separate immutable
record and never mutates Bronze. The collector retains the reducer-produced transition objects in
a bounded append-only current-session journal with at most three state-changing transitions per
wire spec; duplicate acknowledgements do not grow it. A new session starts its own bounded journal.

For a successfully returned `websockets` 17 application message, Bronze bytes are exactly
`message.encode("utf-8")` for `str` TEXT and the unchanged value for `bytes` BINARY. They are
post-extension, reassembled application-message bytes. They do not represent invalid UTF-8
rejected before `recv()` returns, control/close frames, fragment boundaries, compression wire
bytes, TCP segments or TLS records. Internal protocol rejection without a returned application
message produces typed failure/coverage evidence without inventing a raw record. `recv(decode=False)`
is not used as a substitute because it removes the TEXT/BINARY distinction required by Bronze.

`RawMarketDataRecord` permits empty exact bytes, strictly decodes TEXT as UTF-8, hides payload
bytes from `repr`, and derives payload length, payload digest, raw ID and full digest. Attempt
snapshots use exact rows `[subscription_spec_id,subscription_attempt_id,attempt_status]`, sorted by
the first two canonical texts and unique on that pair. Changing only attempt status leaves the raw
ID unchanged but changes the full digest. Stored verification recomputes every derived field.
Individual construction proves only `ingress_ordinal >= 0`; a pure finite-sequence validator can
prove zero-based contiguous supplied records, but only a sealed run manifest in 3B2 can detect a
missing tail. Later annotations never mutate Bronze.

Phase 1A-3B1B requires an injected asynchronous `RawRecordSink` with the exact operation
`await sink.accept(record: RawMarketDataRecord) -> RawRecordAcceptance`. Acceptance echoes the
exact raw-record ID, the exact full-record integrity SHA-256 and a bounded non-secret destination
ID. The full digest is required because the locator ID deliberately does not bind changing attempt
status snapshots. Acceptance means ownership at that sink boundary, not durable persistence. A
separate bounded `NormalizationOutcomeSink` accepts active
frame outcomes through
`await outcome_sink.accept(outcome: NormalizationOutcome) -> NormalizationOutcomeAcceptance`;
that typed result likewise echoes the exact outcome ID and bounded destination ID. Neither sink
may silently substitute a null implementation, and the two sink objects must be distinct. The
collector owns finite positive accept timeouts and owns each injected sink for exactly one
collector run. In its outermost `finally`, it first attempts the outcome sink's bounded `aclose()`
and then the raw sink's bounded `aclose()`, each at most once. Each close uses that sink's acceptance
timeout. Both are attempted even if the first fails. Cancellation remains cancellation; a
sanitized bounded close failure cannot mask the primary failure. Sink implementations must be
cancellation-cooperative. The collector's deadline race makes its fail-stop decision without
waiting for child-cancellation completion, invalidates every late return and privately consumes any
eventual result or exception. Python cannot forcibly terminate a hostile in-process coroutine that
suppresses cancellation; such a violation may require process teardown, and sinks needing an
absolute kill boundary must later run in an isolated worker or process. Raw and outcome sinks
remain separate, and failures expose only bounded categories—never payloads, exception objects,
credentials or destination secrets. Deterministic test sinks expose accepted immutable values to
their caller.

For every application message successfully returned by ordinary `recv()`, the collector captures
the receive clocks once, reserves one run-wide ingress ordinal and snapshots the complete sorted
session attempt table. Raw acceptance occurs before routing or parsing. Pure processing then
constructs exactly one frame-level `NormalizationOutcome`; its separate acceptance is the commit
boundary before ACK/pong state, deduplication state, ordinary message counters or the existing v2
output queue may change. Two acceptance counters deliberately advance earlier and independently:
verified raw acceptance increments the raw counter immediately, and verified outcome acceptance
increments the outcome counter immediately, with `0 <= outcome <= raw`. Exact replay messages
remain distinct Bronze records even when their v2 events are suppressed. Timeout or arbitrary sink
failure is acceptance-ambiguous, terminal and never retried; explicit typed rejection is terminal
and known not to be accepted. No runtime coverage transition or `DeliveryOutcome` is constructed
in 3B1B. Consequently an accepted normalization outcome may precede a later v2 output-queue
timeout; operational atomic delivery auditability follows in 3B1C-3.

Internal validation exceptions are private implementation details, not observable contract
values. Their traceback frames may retain rejected constructor inputs in frame locals even when
bounded exception arguments, `__cause__` and `__context__` contain none. Phase 1A-3B1B must catch
them inside its private validation boundary and classify them to the closed
`ValidationFailureCategory`. Standalone module-level producer and consumer coroutine boundaries
discard the private coroutine, original exception and traceback before creating a fresh public
category-only error. No library traceback frame exported from those operations may retain a
collector, connection, sink, queue item, frame local, input value or destination value. The runtime
must never log, store, export or attach the original exception, traceback, free-form text or
`exc_info`. Intentional cancellation remains cancellation and is not reclassified as validation
failure.

With `H` heartbeat interval, `P` pong timeout, `R` raw acceptance timeout, `O` outcome acceptance
timeout, `Q` normalized-output publish timeout and `S` send timeout, validation must require:

```text
max(H, P + 2 * (R + O) + Q) + (R + O + Q) + S < 60 seconds
```

Defaults `H=45`, `P=10`, `R=1`, `O=1`, `Q=5` and `S=4` produce exactly 56 seconds. Equality at
60 seconds is rejected. This is the collector scheduling/fail-stop bound for conforming
cancellation-cooperative sinks; it is not a claim that arbitrary hostile in-process Python can be
forcibly killed. Raw sink and outcome sink coordination are active in 3B1B, but the v3 envelope,
operational coverage and delivery boundaries remain dormant.

The future 3B1C-2 coverage runtime additionally reserves a synchronous CPU budget `C` and must
validate the complete bound:

```text
max(H, P + 2 * (R + O + C) + Q) + (R + O + C + Q) + S < 60 seconds
```

With `C=1` and the same defaults, the result is exactly 57 seconds. Runtime acceptance requires a
warm setup-excluded prepare-plus-commit median no greater than 1.0 second, the maximum of five
1,024-target runs no greater than 1.5 seconds, maximum event-loop stall no greater than 1.0 second
(preferably 0.5), `T(1024) / T(512) <= 2.5`, and no missed heartbeat or pong deadline. These are
future runtime gates, not claims made by the pure 3B1C-1D contracts.

### Metadata, provenance, coverage and outcomes

`InstrumentSpecification` retains full independently verified canonical content and uses a
bounded digest ID that also commits to the canonical-instrument digest and content length; stored
reconstruction recomputes both before returning a value. Expiry comes only from `Instrument`.
Dated contracts add explicit last-trading and settlement UTC timestamps rather than parsing
symbols, and settlement may equal or follow—but never precede—the last-trading time. Stored
verification rechecks the same rule. Observations are append-only and
conflicts are scoped by
`(canonical_instrument_id, metadata_authority_id, effective_from)`. Identical re-observations of
specification, basis and ending are allowed; differing assertions at that authority/boundary fail.
`FIRST_OBSERVED` requires `effective_from == observed_at`; `SOURCE_DECLARED` may predate
observation. Selection first restricts the supplied observations to the requested instrument and
authority with `observed_at <= raw.received_time`; only that visible slice is validated and used
to construct the effective half-open interval. The separate full-catalogue validator remains
available when whole-catalogue proof is required. The selector is the sole supported constructor
of `ResolvedInstrumentMetadata`; future or unrelated-authority observations cannot invalidate a
historical selection, and current metadata may never be applied retroactively through look-ahead.

`SourceProvenance` contains only source-event ID, optional transaction ID, source-time facts and
source-sequence ranges. `ObservationProvenance` contains feed, run, session, plan, spec, attempt,
raw-record/index, receive clocks, collector build, normalization run and normalizer build.
`NormalizationContext.from_raw_record(...)` derives every capture-side value and validates feed,
adapter, instrument, metadata, collector-run-consistent coverage epochs and event-specific
subscription lineage. Mixed Hyperliquid frames
therefore retain the correct coin-specific spec and attempt at every index. The current trade-v2
binding requires exactly one `TRADE_EXECUTION_TIME` source fact; its derived UTC instant must equal
both the selected metadata event time and outer-v3 event time. An `EXCHANGE_EVENT_TIME` may coexist
without replacing it. The current Hyperliquid public-trades binding materializes events only from
an `ACKNOWLEDGED` attempt snapshot; pre-ACK raw control observations remain valid Bronze facts but
cannot authorize a trade event.

The logical source key is `(feed_product_id, source_event_id)`; the observation key is
`(raw_record_id, raw_event_index)`; the materialization key is `(normalization_run_id,
observation_key,event_family,event_family_version,payload_type)`. Run identity is stable across
sessions; session and attempt identities change at their explicit ordinals; plan/spec identities
stay stable while desired/wire configuration stays unchanged. The same source event seen through
two feed products is two logical source keys.

Coverage is separate for Bronze ingress, Silver normalization and Silver delivery. An event embeds
exactly ingress and normalization references known at construction. Delivery knowledge is a
separate immutable `OutcomeDeliveryResult`; `DeliveryCommitAcceptance` and
`DeliveryCommitFailure` provide the closed success/failure results. Silver delivery means bounded
collector-output-queue acceptance, not consumer processing or persistence. The pure reducer
enforces scope/epoch continuity, the next
ordinal, bounded transitions and recovery evidence. ACK or reconnect alone never improves degraded
coverage. Epoch identity binds scope, collector run, ordinal and an explicit UTC/monotonic
activation boundary. Initial `COMPLETE` requires typed initial-activation evidence at exactly that
boundary. Later evidence carries injected UTC and monotonic observation boundaries and cannot move
backward in process-local monotonic time. A definitively rejected successfully received raw record
makes Bronze ingress `CONFIRMED_INCOMPLETE`; ambiguous raw-sink acceptance remains `UNCERTAIN`.
Only positively identified in-scope market data can prove confirmed incompleteness; unclassified
terminal ambiguity yields uncertainty. ACK, reconnect and authoritative-state snapshots cannot
repair degraded historical trade coverage. Because no interval-bound sequence/backfill proof
exists in 3B1A, every improvement transition is rejected. Source-event conflict remains distinct
integrity evidence. A Silver-normalization source-event conflict establishes
`CONFIRMED_INCOMPLETE`, initially or through the reducer; it is never an `UNCERTAIN` conflict
state, and ACK, reconnect or unrelated evidence cannot clear it.

`NormalizationOutcome` binds the normalization build and is frame-atomic. Control and valid-empty
frames have no index or materialization outcomes. Successful indexes are unique and increasing;
new and exact-duplicate dispositions may coexist. Pre-index rejection invents no indexes. On an
indexed rejection or conflict, prior candidates become `NOT_MATERIALIZED_FRAME_ABORTED` and no
materialization key is committed. For every indexed frame, frame-level evidence must equal the
sorted unique canonical union of all per-index evidence; missing, extra, contradictory, partial,
differently ordered or duplicate evidence fails closed. The contract proves consistency only for
explicitly supplied decoded context, not that an index existed in raw bytes. Attached
normalization-failure and source-conflict coverage transitions are the only permitted transition
kinds and must use Silver-normalization scope, the same feed, collector run, raw record and
normalization run. Each indexed outcome carries a factory-derived raw/spec/attempt/public-selector/
canonical-instrument/scope binding; an attached transition must match that exact scope, event
index and nullable source-event identity: the evidence and logical source key must either contain
the same established source-event ID or both omit it. A generic rejected index cannot carry
source-conflict evidence, and every conflict index requires a non-null exact source-event ID plus
the dedicated conflict disposition and frame status. Pre-index failure uses an exact
factory-derived raw-frame scope containing every plan spec with the matching
family/version/payload binding and every instrument bound to those specs. Without typed route
evidence it cannot choose a strict subset or fabricate an event index. Pre-index evidence uses a
positive closed allowlist: protocol rejection, decoder rejection, unknown instrument, metadata
unavailable, provenance mismatch or local contract failure. Source-event conflict and
frame-atomic abort are indexed-only, and every future evidence enum member remains pre-index
invalid until explicitly classified. A frame containing both an indexed rejection and an indexed
source-event conflict must use `MIXED_INDEXED_FAILURE`. That status requires at least one of each,
permits only `REJECTED`, `SOURCE_EVENT_CONFLICT`, `NOT_MATERIALIZED_FRAME_ABORTED` and
`EXACT_DUPLICATE_SUPPRESSED`, covers the complete decoded index range, and commits no
materialization. The older rejected and conflict statuses continue to reject the other primary
disposition, so the mixed status is not a catch-all. New outer outcome identities use
`normalization-outcome-v2`; v1 remains parser-only and rejects the mixed status. Both existing
outcome-content layouts and every raw-event outcome identity remain unchanged.
Transport, reconnect, raw-sink, delivery, foreign-scope or unrelated pre-existing transitions
cannot be attached. Delivery state never appears in `NormalizationOutcome`.

### Activation sequence

- **3B1A — dormant pure contract spine:** define and test these value objects, selectors, reducers,
  envelope and outcomes; no producer constructs or emits `MarketEventEnvelopeV3`.
- **3B1B — Bronze raw capture:** integrate mandatory bounded `RawRecordSink` acceptance before
  parsing and separate bounded `NormalizationOutcomeSink` acceptance after each frame's
  processing/materialization decision; v2 remains the only Silver envelope.
- **3B1C-1 — pure contract closure:** define coverage initialization/state references,
  plan-derived fan-out, prepared compare-and-swap mutation/no-op lineage, frame-atomic abort
  evidence and outcome-bound delivery knowledge. These contracts are dormant and perform no
  async, sink, queue, collector or health work.
- **3B1C-1A — runtime-binding contract correction:** keep the contracts pure and dormant while
  requiring exact selected-ACK initialization fan-out and typed post-outcome sink-failure evidence.
- **3B1C-1B — pre-ACK rejection binding:** represent one exactly indexed non-ACK provenance
  rejection without weakening the acknowledged route or widening to plan-level uncertainty.
- **3B1C-1C — compact atomic commitments:** replace newly written high-cardinality mutation,
  acceptance and normalization-lineage content with ordered domain-separated commitments while
  retaining every full typed value and one atomic compare-and-swap operation.
- **3B1C-1D — mixed outcome and bulk derivation closure:** represent simultaneous indexed rejection
  and conflict without precedence loss, and verify one complete committed fan-out once before O(1)
  per-leaf committed-state/upstream-source access.
- **3B1C-1G — typed lossy raw-rejection projection:** preserve exact Bronze loss while expressing
  pre-parse unknown Silver membership as uncertainty through one opt-in typed relation; the exact-
  status route and `EventCoverage` remain strict.
- **3B1C-2 — coverage runtime:** operationalize immutable Bronze-ingress and Silver-normalization
  coverage and the temporary lossy v2 `is_gap` projection. Existing 3B1B outcomes remain
  transition-empty until this step.
- **3B1C-3 — atomic delivery:** expose one composite queue item containing the delivered v2 batch
  and its accepted delivery outcome. Output-queue acceptance is not dequeue, consumer processing
  or persistence.
- **3B1D — atomic cutover:** migrate both normalizers and the collector together to the one mandatory
  v3 envelope, preserve existing source-event bytes, and remove outer v2 and `is_gap`.
- **3B2 — deterministic replay:** verify digests and sealed run manifests, preserve ingress order,
  never silently sort/repair/deduplicate raw history, and retain one-to-many Bronze-to-Silver
  lineage.

**Current status:** The v3 envelope contracts are defined and tested but dormant. No runtime
producer constructs or emits `MarketEventEnvelopeV3`; v2 remains the only active Silver envelope.
Phase 1A-3B1B activates
mandatory storage-neutral raw-record and normalization-outcome acceptance in the Hyperliquid
collector, without claiming persistence. Pure delivery-knowledge contracts are dormant; no
operational coverage tracker, delivery linearization/composite queue item, deterministic replay or
deployment exists. The atomic producer and collector cutover occurs only in 3B1D. SHADOW and LIVE
remain disabled.

The direct 3B1C runtime was blocked by five contract gaps: no ordinal-zero commit identity, no
cause-derived complete fan-out/CAS batch, no no-op lineage for repeated degradation, no typed
frame-atomic abort cause binding, and no outcome-complete delivery knowledge contract. 3B1C-1
closes only those representational gaps. A prepared mutation, initialization or transition is not
committed coverage. Only an exact `CoverageCommitAcceptance` for the complete resulting
state-reference set is commit proof. Repeated evidence at an already-degraded state creates no
fake transition or ordinal, but its no-op still binds the exact compatible reason and evidence.
Fan-out evidence is cross-checked against the exact session and selected attempt. Any raw-backed
mutation additionally commits to `RawCoverageFanoutBinding`: the raw record's full-record digest
and complete attempt-status snapshot must reproduce the same fan-out exactly, so matching only a
raw-record session cannot substitute a different snapshot. Typed outcome lineage additionally
requires every indexed or pre-index normalization-scope binding to carry that same full-record
integrity SHA-256; the lower raw-record locator alone is insufficient. An event's Bronze/Silver
pair must share one collector run and preserve exact committed upstream lineage. Delivery success
and failure use separate closed result values; process-local delivery evidence is not durable
persistence. The current v2 output-queue binding permits only delivery-attempt ordinal zero and no
retry; another attempt policy requires a new explicit binding.

Runtime binding exposed two remaining pure gaps, closed in 3B1C-1A. `acknowledged-active-v1`
retains the complete plan snapshot but requires an explicit sorted unique non-empty selection;
each selected row must currently be `ACKNOWLEDGED`, and only its complete leaf union is targeted.
Outcome-sink evidence binds one exact `NormalizationOutcomeId` and exact Silver scope. Explicit
typed rejection maps to definite non-acceptance and `CONFIRMED_INCOMPLETE`; timeout, arbitrary
exception and invalid acceptance echo map to acceptance ambiguity and `UNCERTAIN`. Cancellation
creates no evidence. The binding's evidence-kind component is closed to
`normalization-outcome-rejection` and
`normalization-outcome-sink-acceptance-ambiguity`; their matching initial/transition reason codes
are respectively `normalization-outcome-definite-rejection` and
`normalization-outcome-acceptance-uncertain`. Indexed statuses use exact-routed fan-out, while only
`rejected_before_indexing` uses all-possibly-active fan-out; control/no-event and valid-empty
statuses cannot create this mutation. Every permitted case requires mandatory raw fan-out binding
and one outcome/failure kind across all targets. A lower evidence row or prepared batch is not the
concrete outcome-to-scope proof. Only the dormant
`NormalizationOutcomeSinkFailureCoverageBinding` validates the complete outcome against its exact
indexed scope/attempt union or pre-index aggregate and every target decision across
initializations, transitions and no-ops. Phase 3B1C-2 must accept this aggregate, never a loose
lower-layer batch, for this cause.
Because this evidence exists only after the outcome, it is forbidden from that same outcome's
prepared lineage. These are pure representation rules only; the active collector remains
unchanged until 3B1C-2.

The protected runtime audit then exposed one exact pre-ACK indexed-rejection gap, closed purely in
3B1C-1B. `RoutedCoverageTarget` and `exact-routed-events-v1` remain ACK-only and byte-identical. The
separate `ExactIdentifiedRejectionTarget` accepts only the raw-captured `PENDING`, `SEND_STARTED` or
`SENT` status and resolves the exact selector/instrument/spec/attempt/family binding to a unique
Silver leaf. Its direct matrix is closed to `REJECTED_AFTER_INDEXING`, `REJECTED`, frame evidence
`PROVENANCE_MISMATCH`, failure category `PROVENANCE_MISMATCH`, and
`CONFIRMED_INCOMPLETE` / `IN_SCOPE_NORMALIZATION_FAILURE`; Bronze, activation, materialization,
duplicates, conflict, recovery and delivery are forbidden. Scope fan-out stays unique, while each
same-route wire index retains a distinct raw-backed evidence row. For a later outcome-sink failure,
the v2 proof can include a separate ACK-only route partition, and the aggregate binding must equal
the complete concrete outcome scope/attempt union. Raw rederivation uses the capture-time snapshot,
so later ACK state cannot rewrite lineage. This contract remains dormant; 3B1C-2 is still the first
runtime consumer.

The 3B1C-2 implementation probe exposed a separate boundedness blocker in the dormant contracts.
`coverage-mutation-batch-content-v1` repeats several complete nested canonical-ID collections: a
957-target initialization is 16,762,723 characters and still fits the 16,777,216-character
content ceiling, while the otherwise valid 958-target operation exceeds it. Historical state makes
the problem grow sooner; a 300-target `COMPLETE -> UNCERTAIN` batch already occupies 11,824,581
characters. Raising the ceiling only moves the failure, and splitting one fan-out would violate the
required all-target CAS.

Phase 3B1C-1C therefore keeps every immutable typed pre-state, initialization, transition, no-op
and resulting-state tuple, but writes `coverage-mutation-batch-content-v2` as an ordered list of
domain-separated per-target SHA-256 commitments. Target ordinal and count are committed explicitly;
each target commitment binds its scope, exact expected pre-state or null, closed disposition,
exact selected operation and resulting state. `coverage-commit-acceptance-content-v2` likewise
binds the exact ordered result count and result-set commitment. `normalization-coverage-lineage-v3`
commits its six complete ordered roles independently, including no-op rows, without flattening
their full values into its top-level content. Constructors and stored verification recompute all
item, role, aggregate, content and ID digests from the retained typed values. V1 mutation and
acceptance IDs and v2 lineage IDs remain byte-exact parser-only legacy values; new factories emit
only the paired v2/v2/v3 identities, and cross-version role substitution is rejected. No partial
batch, partial acceptance, chunking or recovery semantics are introduced. These contracts remain
dormant; 3B1C-2 resumes only after this correction merges.

The next runtime probe exposed two independent remaining blockers. First, status precedence could
retain either an indexed rejection or a source-event conflict but not both. Phase 3B1C-1D adds the
strict mixed matrix described above and moves new outer outcome IDs to v2 without changing the v1
raw-event or v1/v2 outcome-content layouts. Existing v1 outer IDs remain byte-exact parser fixtures;
there is no legacy or dual writer.

Second, a 1,000-spec run measured 0.841 seconds for plan construction, 0.070 seconds for attempts,
0.783 seconds for runtime initialization, 3.683 seconds for ambiguity preparation and 47.808 seconds
for commit, approximately 53.189 seconds total. Profiling attributed about 335.7 million calls and
2.06 million canonical JSON parses to repeated verification of the same shared acceptance and
lineage per Silver leaf. Runtime-only dictionaries cannot remove that nested validation.
`BatchVerifiedCoverageDerivation.from_commit` therefore accepts one exact batch-v2, matching
acceptance-v2 and complete ordered result tuple. It fully verifies their IDs, compact commitments,
membership, scopes, run, epochs, statuses and ordinals once, then derives the existing ordinary
committed-state and upstream-source objects once and exposes O(1) indexed access. It is sealed,
factory-only and stateless: there is no caller-supplied verification flag, alternate identity,
mutable/global cache or second commit proof. Deterministic tests bind one shared verification and
one derivation per leaf and cap canonical-parse growth from 512 to 1,024 targets at 2.5x. This phase
adds no runtime import, coverage mutation, delivery linearization or deployment; schema v2 and
`is_gap` remain active and the v3 envelope remains dormant. Phase 3B1C-2 resumes only after merge.

**3B1C-1E bulk-derivation performance closure.** The sealed bulk factory remained semantically
correct and O(N), but its historical warm, setup-excluded 1,024-target median was 1.148715 seconds.
That mandatory inner call could not fit inside the future complete-runtime median gate of 1.0
second. Profiling showed repeated nested canonical parsing and serialization during the one shared
verification, not superlinear leaf lookup. The correction therefore keeps every existing public
API, preimage, byte, digest, ID and version while building one private call-local transcript of
fully rederived typed facts. The transcript is discarded with the call; it is not a verification
token, alternate proof, mutable/global cache or cross-call cache. Batch decisions, acceptance item
and aggregate commitments and every ordered state result are still recomputed in full. Retained
raw-fan-out values are format-checked and rebound into their content, digest and outer ID, with
feed/run/session lineage cross-checked; the retained-only verifier cannot reconstruct the
factory-computed full-record or complete-snapshot digest preimages because neither source value is
retained. Exactly one batch verification, one acceptance verification, N leaf derivations and N-1
result/operation-order comparisons are deterministic regression gates. Independent fan-out
snapshot, scope, selection and route order checks are separate bounded O(N) adjacent passes.

The binding pure-contract `from_commit` budget is 0.800000 seconds at 1,024 targets, with a maximum
of 1.000000 seconds and a 1,024/512 median ratio no greater than 2.5. At least 0.2 seconds is
consequently allocated inside the later full-path `C=1` budget. The pinned TerraPC measurement built
every plan, batch, acceptance and result tuple before timing, measured each complete
`BatchVerifiedCoverageDerivation.from_commit(...)` call with `perf_counter_ns` and left garbage
collection enabled.

One preliminary launcher invocation exited before fixture construction, warm-up or timing because
the repository `src` path was missing from that launcher's import path. It executed zero warm-ups
and produced zero samples, so it was not a benchmark measurement series. After correcting the
launcher path, exactly one valid measurement series was executed: three unreported warm-ups followed
by nine reported complete `from_commit(...)` runs for every scenario/cardinality combination. No
reported sample was discarded, replaced or selectively rerun, and no second valid measurement
series was executed.

All binding gates passed in that one valid measurement series. The ordered ledger is:

- initialization, 512: `samples_s=[0.148724019,0.148351854,0.148233575,0.161710064,0.149504862,0.148696052,0.147115030,0.156337790,0.148410392]`; `median_s=0.148696052`; `max_s=0.161710064`;
- initialization, 1,024: `samples_s=[0.279684863,0.283136466,0.276422147,0.278448813,0.286420407,0.287097495,0.302709014,0.281350437,0.288575808]`; `median_s=0.283136466`; `max_s=0.302709014`; `median_ratio_1024_512=1.904129008`;
- `COMPLETE -> UNCERTAIN`, 512: `samples_s=[0.297152155,0.302009816,0.296064591,0.287185938,0.304965925,0.286829596,0.310995542,0.291249474,0.304835876]`; `median_s=0.297152155`; `max_s=0.310995542`;
- `COMPLETE -> UNCERTAIN`, 1,024: `samples_s=[0.587829245,0.566387276,0.582801299,0.562257234,0.570218779,0.567087433,0.569355146,0.566632970,0.574538141]`; `median_s=0.569355146`; `max_s=0.587829245`; `median_ratio_1024_512=1.916039095`;
- full no-op, 512: `samples_s=[0.378645320,0.375446032,0.379661460,0.366737291,0.378510346,0.358678720,0.366148833,0.360407344,0.377142482]`; `median_s=0.375446032`; `max_s=0.379661460`;
- full no-op, 1,024: `samples_s=[0.714588626,0.693932575,0.721643903,0.702718279,0.723139806,0.707491559,0.707791138,0.708760046,0.703391107]`; `median_s=0.707791138`; `max_s=0.723139806`; `median_ratio_1024_512=1.885200742`.

These results are host- and fixture-bound and do not claim an absolute worst case. They cover only
`from_commit`. A separate diagnostic serial
construction of 1,024 ordinary upstream evidence values took 18.257364 seconds and about 739
canonical parses per leaf. Therefore the remaining 0.2 seconds is a binding 3B1C-2 allocation, not
proof of a working Bronze-to-Silver runtime path. 3B1C-2 must optimize that path or request a
separate contract closure, then prove its complete warm prepare/commit/propagation/snapshot path
has a median no greater than 1.0 second. Its unchanged heartbeat budget is:

```text
max(45, 10 + 2 * (1 + 1 + 1) + 5) + (1 + 1 + 1 + 5) + 4
= 57 seconds
< 60 seconds
```

This correction remains pure contract code. It activates neither 3B1C-2 runtime coverage nor
3B1C-3 delivery; schema v2 and `is_gap` remain active and v3 remains dormant.

**3B1C-1F initial fan-out and fused design.** New fan-out writers emit v3 for every closed kind.
Each proof retains the exact typed `CoverageTargetCatalog`; the catalog retains the exact typed
plan, so no second plan field is stored. `verify_stored` rederives plan content, digest and ID;
catalog scopes, content, digest and ID; session; complete attempt snapshot; kind-specific
membership; target ordering/count; proof content, digest and ID. V1 non-rejection and v2
identified-rejection proofs remain byte-exact parser-only forms. Mutation-batch v2, acceptance v2,
raw-binding v1 and lineage v3 layouts are unchanged. This paragraph describes the pre-correction
evidence/state writer used by the two failure series below; the compact writer matrix follows those
series.

`BatchVerifiedUpstreamCoveragePreparation.from_committed_upstream(...)` accepts only a degraded
Bronze `ALL_POSSIBLY_ACTIVE` batch/acceptance/result tuple and the corresponding Silver
`ALL_POSSIBLY_ACTIVE` v3 proof over the same feed, run, plan, catalog, session, complete attempt
snapshot and selected attempts. Optional raw bindings are symmetric and must retain the same raw
record, full-record digest and snapshot digest. One call-local verification context verifies the
upstream batch and acceptance once, reuses one plan/catalog verification across the two fan-outs,
indexes Bronze/Silver semantic scope keys, performs exactly N pairings and returns exactly N
ordinary upstream evidence-v2 values, N requested mutations and one complete batch-v2. Initial propagation
uses the upstream state boundary; a Silver status change requires the exact upstream transition;
equal or worse state is a no-op. No partial tuple or commit acceptance is produced on failure.
Tests observe one batch verification, one acceptance verification, two proof verifications with
shared retained-parent verification, exactly `2N` scope-key visits and `N-1` operation-order
comparisons. Top-level batch, acceptance, plan, catalog and each proof ID are parsed at most once in
the call-local transcript; 512-to-1,024 parse growth remains linear.

The performance protocol built fixtures outside the interval, kept garbage collection enabled,
ran three unreported warm-ups and then exactly nine reported sequential calls for every operation,
scenario and cardinality. Development profiles before this protocol were not measurement series.
Series 1 and Series 2 are retained below as immutable failure evidence. Series 1 exposed that a
direct target-decision serializer was slower for deeply nested no-op IDs. No sample was removed or
selected. The implementation was changed back to the existing byte-identical generic canonical
serializer, all equivalence tests passed, and Series 2 measured that still-recursive code. The
compact-identity correction below was then code-frozen and measured as Series 3. Independent
post-Series-3 review added bounded retained-cycle guards; the first formal post-review Series 4
then exposed redundant public-constructor verification in fused initialization and transition
paths. That hotspot was closed without changing canonical output, correctness and resource gates
were rerun, and Series 5 established the first green post-correction evidence. Later trust-boundary
review added fresh public-parent verification and a normative aggregate compact-graph bound.
Series 6 preserved the resulting narrow full-no-op timing failure; cached verification dispatch was
then made single-lookup without changing any verified fact, and Series 7 was green. Final
atomicity review then found that the ordinary batch writer did not yet rederive every retained
fan-out and optional raw-binding field before return. That gap was closed with one shared
call-local verification context and no canonical change; Series 8 was green. The subsequent full
suite exposed one family-filtered Silver `ALL_POSSIBLY_ACTIVE` retained-proof rederivation bug:
the verifier compared a family-specific target slice with all possibly active specs. The verifier
now derives the exact spec set from the fully reconstructed family-filtered scope tuple. After its
regression test and the complete correctness/resource selection passed, Series 9 retained one
narrow failure: fused full no-op at 1,024 targets had a 0.803406503-second median. Series 9 is kept
unchanged below. Profiling found construction-time duplicate fan-out scans, no-op retained-load
verification and generic end-of-transcript validation over facts already fully verified in the
same lexical call. Those duplicate passes were removed only from the closed fused construction
path; independent retained-load verification remains unchanged. Series 10 was green, but final
trust-boundary review then found one low-severity public metadata
inconsistency: `CoverageFanoutProofId.VERSION_TAG` still advertised parser-only v1 although all
current writers emit v3. The metadata now names v3 while the explicit accepted parser set remains
exactly v1/v2/v3; canonical bytes and validation semantics do not change. The affected parser,
writer and retained-proof selection passed before Series 11 was announced. Series 11 is the final
evidence.

Series 1 ordered ledger (`from_commit` first, then fused preparation):

- `from_commit` initial uncertain, 512: `samples_s=[0.193113370,0.194118878,0.205996496,0.219868281,0.201592694,0.215889878,0.205350926,0.209896523,0.220235010]`; `median_s=0.205996496`; `max_s=0.220235010`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.379169509,0.390090482,0.404189812,0.396348258,0.398616134,0.407987591,0.393863854,0.417213166,0.394967631]`; `median_s=0.396348258`; `max_s=0.417213166`; `ratio=1.924053398`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.304534889,0.310382901,0.311563599,0.301062624,0.320051903,0.326100735,0.302827741,0.309325128,0.304412992]`; `median_s=0.309325128`; `max_s=0.326100735`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.619968099,0.651494896,0.605443526,0.645165254,0.608362076,0.622315464,0.621860677,0.616146597,0.613970240]`; `median_s=0.619968099`; `max_s=0.651494896`; `ratio=2.004260381`;
- `from_commit` full no-op, 512: `samples_s=[1.192488108,1.198321593,1.173241083,1.207104869,1.181193629,1.208301812,1.202905165,1.188767702,1.211119482]`; `median_s=1.198321593`; `max_s=1.211119482`;
- `from_commit` full no-op, 1,024: `samples_s=[2.438071954,2.402724166,2.377272134,2.401628689,2.403724144,2.432823428,2.452038801,2.441711342,2.441537328]`; `median_s=2.432823428`; `max_s=2.452038801`; `ratio=2.030192431`;
- fused initial uncertain, 512: `samples_s=[0.645200359,0.656939671,0.677264194,0.644793645,0.660471495,0.653041425,0.640407415,0.662490288,0.643340911]`; `median_s=0.653041425`; `max_s=0.677264194`;
- fused initial uncertain, 1,024: `samples_s=[1.292699909,1.346441217,1.410014355,1.405090160,1.336354511,1.354890798,1.328043397,1.385376659,1.329101016]`; `median_s=1.346441217`; `max_s=1.410014355`; `ratio=2.061800623`;
- fused initial incomplete, 512: `samples_s=[0.565454383,0.538301947,0.567414474,0.554738187,0.547532827,0.545913767,0.550028499,0.545549881,0.543914184]`; `median_s=0.547532827`; `max_s=0.567414474`;
- fused initial incomplete, 1,024: `samples_s=[1.102641982,1.057701852,1.130069380,1.111971727,1.094892405,1.127697717,1.102308460,1.077740208,1.081655625]`; `median_s=1.102308460`; `max_s=1.130069380`; `ratio=2.013228076`;
- fused complete to uncertain, 512: `samples_s=[1.221623157,1.187102767,1.198352147,1.200879218,1.185464137,1.218282243,1.190501789,1.204976394,1.197958177]`; `median_s=1.198352147`; `max_s=1.221623157`;
- fused complete to uncertain, 1,024: `samples_s=[2.625046250,2.796448464,2.673521066,2.643517451,2.706863516,2.713209643,2.682325830,2.629312784,2.585401926]`; `median_s=2.673521066`; `max_s=2.796448464`; `ratio=2.230997852`;
- fused uncertain to incomplete, 512: `samples_s=[7.164445482,7.249923658,7.040639247,7.527854790,7.225452625,8.096781101,8.332573586,7.498436539,7.090475737]`; `median_s=7.249923658`; `max_s=8.332573586`;
- fused uncertain to incomplete, 1,024: `samples_s=[15.899772919,15.396018164,15.515741096,15.669622170,15.606039473,17.049321640,16.642122434,17.683727526,16.826970080]`; `median_s=15.899772919`; `max_s=17.683727526`; `ratio=2.193095220`;
- fused mixed transition/no-op, 512: `samples_s=[1.721228153,1.660545301,1.645525874,1.649892586,1.672614046,1.684003645,1.664887231,1.712809139,1.727039203]`; `median_s=1.672614046`; `max_s=1.727039203`;
- fused mixed transition/no-op, 1,024: `samples_s=[3.294925160,3.412930978,3.328537554,3.216654992,3.244130868,3.211215436,3.230929291,3.206443481,3.175170057]`; `median_s=3.230929291`; `max_s=3.412930978`; `ratio=1.931664569`;
- fused full no-op, 512: `samples_s=[23.690293996,23.298218730,23.230792849,23.115139089,23.259124222,23.102364107,23.141746039,23.060506439,22.910823037]`; `median_s=23.141746039`; `max_s=23.690293996`;
- fused full no-op, 1,024: `samples_s=[47.311028967,45.956334541,46.881562814,46.818369505,46.839633005,46.664820541,47.640378412,46.961184813,46.176905655]`; `median_s=46.839633005`; `max_s=47.640378412`; `ratio=2.024031935`.

Series 2 final-code ordered ledger:

- `from_commit` initial uncertain, 512: `samples_s=[0.227622963,0.251923766,0.238849237,0.217673525,0.239549612,0.215649802,0.219981059,0.213529506,0.217817671]`; `median_s=0.219981059`; `max_s=0.251923766`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.435323572,0.448174007,0.448541028,0.423157854,0.442136689,0.438534805,0.488215394,0.454407765,0.445590674]`; `median_s=0.445590674`; `max_s=0.488215394`; `ratio=2.025586548`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.358841559,0.345305311,0.371121864,0.346791701,0.346843922,0.359007163,0.357384356,0.350417657,0.360178183]`; `median_s=0.357384356`; `max_s=0.371121864`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.696958473,0.712905735,0.691735267,0.729935295,0.727216846,0.730128321,0.697853600,0.727576439,0.700464715]`; `median_s=0.712905735`; `max_s=0.730128321`; `ratio=1.994787189`;
- `from_commit` full no-op, 512: `samples_s=[1.390140526,1.331822411,1.301951839,1.323447560,1.316864507,1.287840955,1.324885377,1.343628071,1.318754610]`; `median_s=1.323447560`; `max_s=1.390140526`;
- `from_commit` full no-op, 1,024: `samples_s=[2.807772603,2.945551495,2.867108751,2.684832962,2.768648040,2.763869419,2.698423424,2.607674239,2.639830862]`; `median_s=2.763869419`; `max_s=2.945551495`; `ratio=2.088386048`;
- fused initial uncertain, 512: `samples_s=[0.813684258,0.744677741,0.760133201,0.755627169,0.765493157,0.753753573,0.751055276,0.762100433,0.743672490]`; `median_s=0.755627169`; `max_s=0.813684258`;
- fused initial uncertain, 1,024: `samples_s=[1.495965397,1.548083138,1.550381150,1.514006826,1.569185696,1.574837776,1.569053990,1.517482330,1.589057655]`; `median_s=1.550381150`; `max_s=1.589057655`; `ratio=2.051780579`;
- fused initial incomplete, 512: `samples_s=[0.640273712,0.688440121,0.665962456,0.658821727,0.663174433,0.638878393,0.638954507,0.649226842,0.672157923]`; `median_s=0.658821727`; `max_s=0.688440121`;
- fused initial incomplete, 1,024: `samples_s=[1.301624138,1.262241121,1.259782348,1.284885974,1.266075061,1.269979040,1.277234969,1.269012386,1.268555328]`; `median_s=1.269012386`; `max_s=1.301624138`; `ratio=1.926184784`;
- fused complete to uncertain, 512: `samples_s=[1.535681394,1.423990536,1.423750300,1.416263541,1.411918228,1.397071109,1.396879981,1.435069149,1.370728348]`; `median_s=1.416263541`; `max_s=1.535681394`;
- fused complete to uncertain, 1,024: `samples_s=[2.809159180,2.787855932,2.770458467,2.762430354,2.829751578,2.807876204,2.815377122,2.773685688,2.784676353]`; `median_s=2.787855932`; `max_s=2.829751578`; `ratio=1.968458448`;
- fused uncertain to incomplete, 512: `samples_s=[5.520170958,5.566538546,5.562425810,5.654053322,5.573338776,5.527827966,5.473317644,5.470836236,5.489778715]`; `median_s=5.527827966`; `max_s=5.654053322`;
- fused uncertain to incomplete, 1,024: `samples_s=[13.547391817,13.509388881,13.495212192,13.466305655,13.483475885,13.488128593,13.582483464,13.605750352,14.058029326]`; `median_s=13.509388881`; `max_s=14.058029326`; `ratio=2.443887358`;
- fused mixed transition/no-op, 512: `samples_s=[2.008589939,1.994736640,1.971207381,2.008058436,2.078855219,1.940760058,2.014170003,1.963489674,1.989914241]`; `median_s=1.994736640`; `max_s=2.078855219`;
- fused mixed transition/no-op, 1,024: `samples_s=[4.021662101,4.009270483,4.046603072,3.962445439,4.038697576,4.038982635,4.078966155,4.039021442,4.008531661]`; `median_s=4.038697576`; `max_s=4.078966155`; `ratio=2.024677090`;
- fused full no-op, 512: `samples_s=[26.830041898,26.909540312,26.629819266,26.620347090,26.813807127,26.886553282,26.549572140,26.651845167,26.919392355]`; `median_s=26.813807127`; `max_s=26.919392355`;
- fused full no-op, 1,024: `samples_s=[45.313278972,45.392613817,45.806412354,45.861638293,45.437754810,45.385292755,45.415680428,45.203344666,45.349740570]`; `median_s=45.392613817`; `max_s=45.861638293`; `ratio=1.692882089`.

Both historical series remain linear by the `<=2.5` ratio gate, but their absolute gates fail.
Series 2 exceeds 0.8 seconds for every fused 1,024 case and exceeds 1.0 second maximum for every
fused 1,024 case; retained v3 verification also makes `from_commit` full no-op exceed both 1E
bounds. The cause is recursive JSON-in-JSON identity expansion: an upstream evidence ID embeds a
full committed-state v1 ID, that embeds a full state-reference v1 ID, transitions retain the
predecessor and earlier evidence, and a no-op quotes the full current state again. The final 1,024
full-no-op fixture used about 4.3 GiB resident. A faster serializer cannot remove that byte volume.

**3B1C-1F compact recursive coverage identities.** The pre-commit correction breaks the recursive
identity at three exact boundaries while retaining every typed parent:

```text
["upstream-coverage-state-evidence-v2", committed_coverage_state_id_v2]
["upstream-coverage-transition-evidence-v2", committed_coverage_state_id_v2,
 upstream_coverage_transition_id]

["coverage-evidence-content-v2", coverage_scope_id, coverage_epoch_id, collector_run_id,
 evidence_kind, typed_upstream_source_row_v2, canonical_observed_at, observed_monotonic_ns]
["coverage-evidence-v2", coverage_scope_id, coverage_epoch_id, collector_run_id,
 evidence_kind, typed_upstream_source_row_v2, canonical_observed_at, observed_monotonic_ns,
 content_sha256]

["coverage-state-reference-content-v2", "initialization"|"transition",
 coverage_initialization_id, previous_coverage_state_reference_id_or_null,
 latest_coverage_transition_id_or_null, coverage_scope_id, coverage_epoch_id,
 collector_run_id, coverage_status, transition_ordinal, canonical_observed_at,
 observed_monotonic_ns]
["coverage-state-reference-v2", coverage_scope_id, coverage_epoch_id, collector_run_id,
 coverage_status, transition_ordinal, canonical_observed_at, observed_monotonic_ns,
 content_sha256]

["committed-coverage-state-content-v2", coverage_state_reference_id,
 coverage_commit_acceptance_id, result_ordinal]
["committed-coverage-state-v2", coverage_scope_id, coverage_epoch_id, collector_run_id,
 coverage_status, transition_ordinal, canonical_observed_at, observed_monotonic_ns,
 result_ordinal, content_sha256]
```

The state source retains the complete typed `CommittedCoverageState`; the transition source also
retains the exact typed `CoverageTransition`; a state retains its initialization, predecessor and
latest transition; and a committed state retains its state, acceptance and exact result ordinal.
Stored verification rederives all retained parents, canonical content, digest, ID, time boundary
and positional membership. Hashes prove integrity only: they do not prove availability,
reconstructability, completeness, venue authenticity or semantic validity. Missing typed parents
therefore fail closed. No validation token, cross-call cache or hash-only load path exists.

New committed-state writers emit v2 and committed-state v1 is parser-only. Upstream evidence
writers emit evidence v2 while non-upstream evidence remains writer-active v1. Transitioned and
upstream-derived state references emit v2; an ordinary non-upstream ordinal-zero state may still
emit v1, and a first v2 transition may retain a fully verified v1 predecessor. Initialization v1,
transition v1, mutation-no-op v1, mutation-batch v2, acceptance v2, fan-out v3, raw binding v1 and
normalization-lineage v3 remain unchanged. A no-op returns the identical state object and ID and
does not create a transition or increment the ordinal. `CommittedCoverageState.from_commit_at`
binds membership by exact built-in `result_ordinal`; it never performs a per-leaf tuple search.

The current-writer outer state and committed-state ID bounds are structurally derived as 156,856
and 156,877 characters; committed-state content is bounded at 483,694 characters. Existing
evidence/state content ceilings were not raised. The exact maximum retained transition ID that can
fit both evidence-v2 preimages is 813,255 characters and larger input fails closed. Tests account
for every reachable compact evidence/state/committed ID once. The normative joint cross-component
composability limit is `(64 MiB) - 1`: both the ordinary batch writer and fused writer charge every
unique verified v2 evidence/state/committed ID by role and fail closed before returning any value
when the total would exceed 67,108,863 bytes. This means independently valid widest scalar values
need not compose with every maximum-cardinality graph. At 1,024 leaves the worst-case escaped
collector-run probe accepts 550 emoji codepoints at exactly 67,065,684 bytes; 551 is the first
rejected width and returns no partial prefix. The representative 512-to-1,024 byte growth must
remain at most 2.5. The absolute RSS limit was fixed at 1.5 GiB before any compact-series launch
and was not relaxed.

The deterministic retained-graph ledger measured 16,080,346 compact ID-bytes at 512 leaves and
32,161,362 at 1,024 leaves (`ratio=2.000041666`). Maximum observed current-writer IDs at 1,024 were
9,292 bytes for evidence, 1,215 for state references and 1,208 for committed states. The
instrumented 512/1,024 transcript totals were respectively 60,824/96,214 parse-input bytes,
25,708,147/51,398,703 canonical re-encode bytes, 90,132,118/180,258,991 quote-input bytes,
126,855,716/253,698,109 quote-output bytes, 123,558,080/247,111,824 encoded-array-output bytes and
92,841,336/185,675,276 SHA-256-input bytes. Unique/repeated charged bytes were
209,444,080/418,825,036 and 249,712,141/499,414,081; the maximum individual encoded preimage was
1,505,462 bytes. Every byte-work ratio is at most 2.0 apart from the deliberately deduplicated parse
counter (`1.581842694`), and all are below 2.5.

Series 3 ran after code freeze in one isolated process per operation/scenario/cardinality. Fixture
construction was outside timing; garbage collection stayed enabled; every process performed three
unreported warm-ups followed by exactly nine reported complete calls. The append-only ledger
completed all 18 children. No child was restarted and no sample was removed, replaced or selected.

Series 3 ordered ledger (`from_commit` first, then fused preparation; RSS includes fixture,
warm-ups and samples in that isolated process):

- `from_commit` initial uncertain, 512: `samples_s=[0.116505803,0.112288268,0.112916640,0.112440639,0.113288423,0.113497367,0.113955223,0.115040251,0.113838635]`; `median_s=0.113497367`; `max_s=0.116505803`; `peak_rss_bytes=73273344`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.230128853,0.224055748,0.224059028,0.222006845,0.222785886,0.223998384,0.226391570,0.230641047,0.223282698]`; `median_s=0.224055748`; `max_s=0.230641047`; `ratio=1.974105250`; `peak_rss_bytes=110186496`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.129712740,0.130093131,0.129781680,0.130538237,0.133230053,0.137334295,0.132973735,0.130871626,0.131893043]`; `median_s=0.130871626`; `max_s=0.137334295`; `peak_rss_bytes=156233728`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.264288278,0.265323916,0.263816232,0.271990994,0.278438386,0.278180864,0.283953954,0.265118122,0.267047330]`; `median_s=0.267047330`; `max_s=0.283953954`; `ratio=2.040528861`; `peak_rss_bytes=278462464`;
- `from_commit` full no-op, 512: `samples_s=[0.159108860,0.157762931,0.158213497,0.157118675,0.157707069,0.160706744,0.157687722,0.157820010,0.157137591]`; `median_s=0.157762931`; `max_s=0.160706744`; `peak_rss_bytes=224022528`;
- `from_commit` full no-op, 1,024: `samples_s=[0.329597202,0.322912240,0.327485066,0.320543752,0.319918848,0.320634233,0.321553864,0.319937098,0.326467674]`; `median_s=0.321553864`; `max_s=0.329597202`; `ratio=2.038209242`; `peak_rss_bytes=412954624`;
- fused initial uncertain, 512: `samples_s=[0.145552713,0.144600723,0.145183520,0.146210062,0.145761212,0.144510725,0.145987752,0.145777138,0.148328554]`; `median_s=0.145761212`; `max_s=0.148328554`; `peak_rss_bytes=92200960`;
- fused initial uncertain, 1,024: `samples_s=[0.297871027,0.295390510,0.293497315,0.293052182,0.292449868,0.293148857,0.301997556,0.296235176,0.292930054]`; `median_s=0.293497315`; `max_s=0.301997556`; `ratio=2.013548810`; `peak_rss_bytes=149139456`;
- fused initial incomplete, 512: `samples_s=[0.134223905,0.142588349,0.135781367,0.134912691,0.141197767,0.134916711,0.134896878,0.150277328,0.135219100]`; `median_s=0.135219100`; `max_s=0.150277328`; `peak_rss_bytes=84910080`;
- fused initial incomplete, 1,024: `samples_s=[0.272503513,0.270261744,0.271327582,0.273676304,0.270680740,0.274887452,0.272028410,0.270605220,0.270216984]`; `median_s=0.271327582`; `max_s=0.274887452`; `ratio=2.006577340`; `peak_rss_bytes=135151616`;
- fused complete to uncertain, 512: `samples_s=[0.250294711,0.246846608,0.244317151,0.243710439,0.245133671,0.245038528,0.244334490,0.244538443,0.249968667]`; `median_s=0.245038528`; `max_s=0.250294711`; `peak_rss_bytes=156405760`;
- fused complete to uncertain, 1,024: `samples_s=[0.488611065,0.487799747,0.486734017,0.492999318,0.486724726,0.484914879,0.486195882,0.486838503,0.486570337]`; `median_s=0.486734017`; `max_s=0.492999318`; `ratio=1.986357088`; `peak_rss_bytes=278364160`;
- fused uncertain to incomplete, 512: `samples_s=[0.358105388,0.357483362,0.361363599,0.357315181,0.356061839,0.359803770,0.359869794,0.358696047,0.361779379]`; `median_s=0.358696047`; `max_s=0.361779379`; `peak_rss_bytes=222044160`;
- fused uncertain to incomplete, 1,024: `samples_s=[0.740927550,0.767520597,0.738438730,0.759504481,0.735133711,0.724390548,0.741394845,0.725661596,0.726846883]`; `median_s=0.738438730`; `max_s=0.767520597`; `ratio=2.058675405`; `peak_rss_bytes=409042944`;
- fused mixed transition/no-op, 512: `samples_s=[0.260415866,0.259602274,0.260155324,0.260250285,0.265923666,0.262590718,0.260894934,0.261796163,0.266757185]`; `median_s=0.260894934`; `max_s=0.266757185`; `peak_rss_bytes=165740544`;
- fused mixed transition/no-op, 1,024: `samples_s=[0.536272768,0.538328044,0.536103592,0.532946293,0.533098902,0.531632448,0.528111229,0.538706191,0.553360045]`; `median_s=0.536103592`; `max_s=0.553360045`; `ratio=2.054863940`; `peak_rss_bytes=295878656`;
- fused full no-op, 512: `samples_s=[0.373569561,0.370947721,0.372290388,0.373252785,0.371216795,0.371820367,0.371228595,0.384488611,0.378726209]`; `median_s=0.372290388`; `max_s=0.384488611`; `peak_rss_bytes=242233344`;
- fused full no-op, 1,024: `samples_s=[0.763051499,0.764016390,0.753211982,0.762434562,0.757798140,0.757098337,0.757337654,0.769037574,0.804969960]`; `median_s=0.762434562`; `max_s=0.804969960`; `ratio=2.047956613`; `peak_rss_bytes=448778240`.

Every 1,024 median is at most 0.800000 seconds, every reported maximum is at most 1.000000
second, every scale ratio is at most 2.5 and every isolated-process peak RSS is below 1.5 GiB.
The slowest median is 0.762434562 seconds, the slowest sample is 0.804969960 seconds, the worst
ratio is 2.058675405 and the highest peak RSS is 448,778,240 bytes. These results are TerraPC- and
fixture-bound, not absolute worst-case guarantees. They restore the pure contract/resource gates;
3B1C-2 must still integrate the fused factory and separately prove its complete warm synchronous
runtime path at a median no greater than 1.0 second. This phase remains pure and dormant, schema v2
plus `is_gap` remain active, and market-event v3 remains dormant.

Independent review after Series 3 required bounded cycle detection for retained predecessor,
evidence and committed-state graphs. Correctness, adversarial, complexity and byte-volume tests
were green before the first explicitly announced post-review measurement. Series 4 used the same
isolated-process protocol and completed all 18 children without restart or sample selection. It is
retained as failure evidence because fused initialization and transition still invoked public
constructors that opened a fresh stored-verification transcript for every leaf.

Series 4 ordered ledger (`from_commit` first, then fused preparation; RSS includes fixture,
warm-ups and samples in that isolated process):

- `from_commit` initial uncertain, 512: `samples_s=[0.118091179,0.118610703,0.117148244,0.117984080,0.117266583,0.117195376,0.117322951,0.124062866,0.122649797]`; `median_s=0.117984080`; `max_s=0.124062866`; `peak_rss_bytes=72982528`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.230715607,0.232644315,0.231751239,0.232355129,0.231279976,0.238137183,0.233128764,0.241717324,0.233633367]`; `median_s=0.232644315`; `max_s=0.241717324`; `ratio=1.971828021`; `peak_rss_bytes=110084096`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.132223183,0.130830297,0.130746802,0.131115325,0.131348130,0.131786206,0.131647358,0.131350209,0.133198180]`; `median_s=0.131350209`; `max_s=0.133198180`; `peak_rss_bytes=158728192`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.266888799,0.278844379,0.269642148,0.289180709,0.268633298,0.285088306,0.266455468,0.267670479,0.267685101]`; `median_s=0.268633298`; `max_s=0.289180709`; `ratio=2.045168409`; `peak_rss_bytes=281620480`;
- `from_commit` full no-op, 512: `samples_s=[0.156056828,0.158540928,0.157238464,0.155791341,0.157029845,0.157147204,0.158650260,0.157809109,0.158021708]`; `median_s=0.157238464`; `max_s=0.158650260`; `peak_rss_bytes=225456128`;
- `from_commit` full no-op, 1,024: `samples_s=[0.322149260,0.333292484,0.320201527,0.318904146,0.321148554,0.325247977,0.318174162,0.322897834,0.325797291]`; `median_s=0.322149260`; `max_s=0.333292484`; `ratio=2.048794244`; `peak_rss_bytes=415236096`;
- fused initial uncertain, 512: `samples_s=[1.371763318,1.364502265,1.391478865,1.367644329,1.362222192,1.372054957,1.366510045,1.369567754,1.378099545]`; `median_s=1.369567754`; `max_s=1.391478865`; `peak_rss_bytes=93827072`;
- fused initial uncertain, 1,024: `samples_s=[2.746192242,2.765380745,2.769448649,2.781596756,2.748749596,2.756286278,2.773878090,2.744775634,2.783113076]`; `median_s=2.765380745`; `max_s=2.783113076`; `ratio=2.019163153`; `peak_rss_bytes=152043520`;
- fused initial incomplete, 512: `samples_s=[1.172061355,1.169863247,1.166810062,1.168052175,1.186212254,1.168059584,1.173794589,1.199038815,1.183341595]`; `median_s=1.172061355`; `max_s=1.199038815`; `peak_rss_bytes=86630400`;
- fused initial incomplete, 1,024: `samples_s=[2.367212572,2.370063760,2.361937117,2.392799562,2.369684726,2.364048864,2.373042016,2.376070495,2.358459602]`; `median_s=2.369684726`; `max_s=2.392799562`; `ratio=2.021809452`; `peak_rss_bytes=137928704`;
- fused complete to uncertain, 512: `samples_s=[3.168948183,3.172375536,3.223609387,3.167894510,3.175502306,3.196447018,3.194320744,3.187571410,3.182764927]`; `median_s=3.182764927`; `max_s=3.223609387`; `peak_rss_bytes=158445568`;
- fused complete to uncertain, 1,024: `samples_s=[6.363456468,6.364278310,6.365653429,6.406412011,6.380784433,6.377180277,6.343121117,6.371369293,6.404486377]`; `median_s=6.371369293`; `max_s=6.406412011`; `ratio=2.001834706`; `peak_rss_bytes=281501696`;
- fused uncertain to incomplete, 512: `samples_s=[2.562549894,2.580385454,2.557948629,2.591588054,2.556850436,2.553080016,2.552376298,2.581511294,2.559570873]`; `median_s=2.559570873`; `max_s=2.591588054`; `peak_rss_bytes=223215616`;
- fused uncertain to incomplete, 1,024: `samples_s=[5.153780742,5.180796497,5.188147294,5.175958085,5.193545464,5.160587808,5.163231957,5.210895776,5.199095328]`; `median_s=5.180796497`; `max_s=5.210895776`; `ratio=2.024087925`; `peak_rss_bytes=411557888`;
- fused mixed transition/no-op, 512: `samples_s=[1.738360953,1.739807150,1.731768682,1.732598465,1.757716648,1.744408514,1.749067591,1.739787508,1.757212845]`; `median_s=1.739807150`; `max_s=1.757716648`; `peak_rss_bytes=167477248`;
- fused mixed transition/no-op, 1,024: `samples_s=[3.535674669,3.549886918,3.538701400,3.528022117,3.544633595,3.506776318,3.524149536,3.520801398,3.525174428]`; `median_s=3.528022117`; `max_s=3.549886918`; `ratio=2.027823668`; `peak_rss_bytes=299118592`;
- fused full no-op, 512: `samples_s=[0.381100374,0.382180910,0.382835844,0.381487430,0.381459103,0.392813437,0.390425151,0.383140479,0.382504392]`; `median_s=0.382504392`; `max_s=0.392813437`; `peak_rss_bytes=243077120`;
- fused full no-op, 1,024: `samples_s=[0.788470874,0.790533305,0.781940561,0.782170434,0.795506434,0.803757995,0.787616262,0.785480342,0.794342482]`; `median_s=0.788470874`; `max_s=0.803757995`; `ratio=2.061338093`; `peak_rss_bytes=449966080`.

The Series 4 scale and RSS gates passed, but every fused initialization/transition absolute gate
failed; the slowest median was 6.371369293 seconds. The constructor hotspot was then removed by
deriving initialization-v1, transition-v1 and compact state-v2 candidates inside the already
verified lexical transcript and immediately rederiving every candidate through that same context.
No private context crosses the public factory boundary, and retained loads still invoke a fresh,
complete stored verifier. Ordinary factory output and every canonical byte remain equal.

After that correction the complete correctness, adversarial, complexity and byte-volume gates were
rerun. Series 5 was announced before launch and is the first formal post-hotspot series. It used the
same protocol, completed all 18 children, and did not restart, discard, replace or select a sample.

Series 5 ordered ledger (`from_commit` first, then fused preparation; RSS includes fixture,
warm-ups and samples in that isolated process):

- `from_commit` initial uncertain, 512: `samples_s=[0.120376257,0.119120791,0.119749856,0.119988156,0.120310628,0.118951150,0.121655130,0.120621268,0.121426192]`; `median_s=0.120310628`; `max_s=0.121655130`; `peak_rss_bytes=72871936`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.239294197,0.237216887,0.233956606,0.238740896,0.246124784,0.252279932,0.245805863,0.248135584,0.254801338]`; `median_s=0.245805863`; `max_s=0.254801338`; `ratio=2.043093508`; `peak_rss_bytes=109953024`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.133309830,0.132497308,0.134231649,0.130868313,0.133081510,0.134750472,0.135621629,0.141070929,0.135297848]`; `median_s=0.134231649`; `max_s=0.141070929`; `peak_rss_bytes=156688384`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.269889350,0.268986671,0.268148478,0.269039922,0.268815058,0.277275031,0.275110379,0.268287913,0.268118356]`; `median_s=0.268986671`; `max_s=0.277275031`; `ratio=2.003899028`; `peak_rss_bytes=278470656`;
- `from_commit` full no-op, 512: `samples_s=[0.155379588,0.156176719,0.155509276,0.155741562,0.165868302,0.163486976,0.155377587,0.164478641,0.161801826]`; `median_s=0.156176719`; `max_s=0.165868302`; `peak_rss_bytes=224526336`;
- `from_commit` full no-op, 1,024: `samples_s=[0.317455181,0.323040992,0.323106988,0.324436858,0.323482912,0.316035539,0.320206287,0.314550979,0.314797172]`; `median_s=0.320206287`; `max_s=0.324436858`; `ratio=2.050281816`; `peak_rss_bytes=413704192`;
- fused initial uncertain, 512: `samples_s=[0.210804487,0.207688358,0.207318273,0.208261916,0.213950909,0.206804550,0.208073198,0.205955864,0.207647274]`; `median_s=0.207688358`; `max_s=0.213950909`; `peak_rss_bytes=92504064`;
- fused initial uncertain, 1,024: `samples_s=[0.417878880,0.417580949,0.430444627,0.421434377,0.423229101,0.422403084,0.421157270,0.437336491,0.430788141]`; `median_s=0.422403084`; `max_s=0.437336491`; `ratio=2.033831304`; `peak_rss_bytes=150622208`;
- fused initial incomplete, 512: `samples_s=[0.196227270,0.200624759,0.196163022,0.196880556,0.197779630,0.198708151,0.201872785,0.199303239,0.196244930]`; `median_s=0.197779630`; `max_s=0.201872785`; `peak_rss_bytes=85540864`;
- fused initial incomplete, 1,024: `samples_s=[0.393592398,0.390939721,0.395784612,0.393020252,0.394252959,0.394200003,0.407551031,0.393058565,0.399896847]`; `median_s=0.394200003`; `max_s=0.407551031`; `ratio=1.993127417`; `peak_rss_bytes=136323072`;
- fused complete to uncertain, 512: `samples_s=[0.270424903,0.268221811,0.272512523,0.270653409,0.270724998,0.268989598,0.272225423,0.270864297,0.269817583]`; `median_s=0.270653409`; `max_s=0.272512523`; `peak_rss_bytes=156639232`;
- fused complete to uncertain, 1,024: `samples_s=[0.548722941,0.539314219,0.549166379,0.539932345,0.543635533,0.538483768,0.541890250,0.535003455,0.534640457]`; `median_s=0.539932345`; `max_s=0.549166379`; `ratio=1.994921649`; `peak_rss_bytes=278519808`;
- fused uncertain to incomplete, 512: `samples_s=[0.390635796,0.382810578,0.386604542,0.382538661,0.383002916,0.388855444,0.382207899,0.391148364,0.392660239]`; `median_s=0.386604542`; `max_s=0.392660239`; `peak_rss_bytes=222437376`;
- fused uncertain to incomplete, 1,024: `samples_s=[0.699229766,0.699628367,0.724275605,0.711041684,0.698385059,0.727471217,0.713823367,0.708467279,0.710175441]`; `median_s=0.710175441`; `max_s=0.727471217`; `ratio=1.836955762`; `peak_rss_bytes=410185728`;
- fused mixed transition/no-op, 512: `samples_s=[0.281733059,0.278343552,0.278098881,0.286883246,0.277851844,0.277267536,0.279813670,0.283840251,0.284678995]`; `median_s=0.279813670`; `max_s=0.286883246`; `peak_rss_bytes=166051840`;
- fused mixed transition/no-op, 1,024: `samples_s=[0.567950974,0.573928433,0.574488471,0.573861347,0.565447238,0.574198396,0.562814355,0.565602493,0.577075942]`; `median_s=0.573861347`; `max_s=0.577075942`; `ratio=2.050869591`; `peak_rss_bytes=296128512`;
- fused full no-op, 512: `samples_s=[0.385121940,0.384508219,0.389014914,0.389619424,0.396401249,0.387674470,0.387136915,0.391103920,0.400163383]`; `median_s=0.389014914`; `max_s=0.400163383`; `peak_rss_bytes=242212864`;
- fused full no-op, 1,024: `samples_s=[0.799519001,0.792557347,0.795413196,0.791147212,0.807741558,0.790658338,0.784166435,0.798012575,0.787129437]`; `median_s=0.792557347`; `max_s=0.807741558`; `ratio=2.037344375`; `peak_rss_bytes=448614400`.

Every Series 5 1,024 median is at most 0.800000 seconds, every maximum is at most 1.000000
second, every 1,024/512 median ratio is at most 2.5 and every isolated-process peak RSS is below
1.5 GiB. The slowest median is 0.792557347 seconds, the slowest sample is 0.807741558 seconds, the
worst ratio is 2.050869591 and the highest peak RSS is 448,614,400 bytes. These measurements are
TerraPC- and fixture-bound rather than absolute worst-case guarantees. The full 3B1C-2 runtime path
still has to integrate these dormant contracts and prove its separate warm median at or below 1.0
second.

Independent review then required fresh public-parent verification, one-shot call-local contexts and
the joint compact-graph resource boundary described above. After all correctness, adversarial,
complexity and byte gates were green, Series 6 was announced and run once against binary diff
`7b3fdc69f0dd8afd5d85bd2e25203773dbb62d28cc76e368113f07eaf9cec82f` and code/test diff
`a455b976ae1c5137e4d93873af5250f34e6c08c8dfdc50144b93f7a1378c9ab1`. It completed all 18
isolated children with the unchanged protocol and no discarded, replaced or selected sample.

Series 6 ordered ledger (`from_commit` first, then fused preparation; RSS includes fixture,
warm-ups and samples in that isolated process):

- `from_commit` initial uncertain, 512: `samples_s=[0.116250290,0.116874028,0.115634942,0.117500657,0.116574740,0.118566198,0.120372959,0.119055761,0.117896340]`; `median_s=0.117500657`; `max_s=0.120372959`; `peak_rss_bytes=73281536`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.230921609,0.232036345,0.231039440,0.232675223,0.251297918,0.233447636,0.231985363,0.230583522,0.230462784]`; `median_s=0.231985363`; `max_s=0.251297918`; `ratio=1.974332475`; `peak_rss_bytes=109654016`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.133039020,0.139189248,0.132179158,0.130549466,0.132299660,0.132268542,0.131950930,0.131474577,0.132580047]`; `median_s=0.132268542`; `max_s=0.139189248`; `peak_rss_bytes=157368320`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.267470824,0.279309156,0.267730368,0.274640662,0.271116867,0.268010714,0.277213603,0.276944764,0.278403835]`; `median_s=0.274640662`; `max_s=0.279309156`; `ratio=2.076386855`; `peak_rss_bytes=278560768`;
- `from_commit` full no-op, 512: `samples_s=[0.159228622,0.161020257,0.158111250,0.159754890,0.162875996,0.160931499,0.159722887,0.158331891,0.159009602]`; `median_s=0.159722887`; `max_s=0.162875996`; `peak_rss_bytes=224669696`;
- `from_commit` full no-op, 1,024: `samples_s=[0.320799887,0.320578027,0.318550572,0.320239827,0.319437283,0.318376225,0.319693332,0.322290306,0.324160243]`; `median_s=0.320239827`; `max_s=0.324160243`; `ratio=2.004971442`; `peak_rss_bytes=413818880`;
- fused initial uncertain, 512: `samples_s=[0.208742067,0.212132625,0.209326406,0.214133014,0.213429937,0.217108644,0.215571349,0.210481621,0.213228259]`; `median_s=0.213228259`; `max_s=0.217108644`; `peak_rss_bytes=92520448`;
- fused initial uncertain, 1,024: `samples_s=[0.420926170,0.421999912,0.428057406,0.421344280,0.420517282,0.425364271,0.431735363,0.438477838,0.416897055]`; `median_s=0.421999912`; `max_s=0.438477838`; `ratio=1.979099365`; `peak_rss_bytes=150765568`;
- fused initial incomplete, 512: `samples_s=[0.201843775,0.197116539,0.197780912,0.198030855,0.198228470,0.198773881,0.206740413,0.197990252,0.198614426]`; `median_s=0.198228470`; `max_s=0.206740413`; `peak_rss_bytes=85745664`;
- fused initial incomplete, 1,024: `samples_s=[0.400280151,0.397388679,0.400093425,0.397561286,0.396623232,0.396875215,0.402318616,0.398291984,0.398623205]`; `median_s=0.398291984`; `max_s=0.402318616`; `ratio=2.009257217`; `peak_rss_bytes=136540160`;
- fused complete to uncertain, 512: `samples_s=[0.287763734,0.288737675,0.283540443,0.282731412,0.285261637,0.285470377,0.284503545,0.284924075,0.282127024]`; `median_s=0.284924075`; `max_s=0.288737675`; `peak_rss_bytes=157175808`;
- fused complete to uncertain, 1,024: `samples_s=[0.532087662,0.538276643,0.533096086,0.554093824,0.535951039,0.553876678,0.535331678,0.545987511,0.534758817]`; `median_s=0.535951039`; `max_s=0.554093824`; `ratio=1.881031075`; `peak_rss_bytes=278507520`;
- fused uncertain to incomplete, 512: `samples_s=[0.381097969,0.383356758,0.394625023,0.382633726,0.391362357,0.384199002,0.379112546,0.379734898,0.387006837]`; `median_s=0.383356758`; `max_s=0.394625023`; `peak_rss_bytes=223211520`;
- fused uncertain to incomplete, 1,024: `samples_s=[0.715600712,0.712853614,0.738180793,0.729756647,0.722246807,0.728669732,0.718680253,0.727231425,0.717226277]`; `median_s=0.722246807`; `max_s=0.738180793`; `ratio=1.884006978`; `peak_rss_bytes=410619904`;
- fused mixed transition/no-op, 512: `samples_s=[0.282025723,0.283740217,0.280338231,0.281163458,0.287290493,0.284268122,0.281211072,0.283988641,0.287175345]`; `median_s=0.283740217`; `max_s=0.287290493`; `peak_rss_bytes=165974016`;
- fused mixed transition/no-op, 1,024: `samples_s=[0.573267560,0.575454816,0.589745465,0.597563440,0.590146391,0.578958688,0.575122661,0.573790260,0.583273741]`; `median_s=0.578958688`; `max_s=0.597563440`; `ratio=2.040453391`; `peak_rss_bytes=296153088`;
- fused full no-op, 512: `samples_s=[0.387906928,0.389659939,0.389280525,0.391481966,0.391419390,0.400189307,0.384958877,0.385291018,0.385588002]`; `median_s=0.389280525`; `max_s=0.400189307`; `peak_rss_bytes=242192384`;
- fused full no-op, 1,024: `samples_s=[0.801821428,0.803626881,0.802064711,0.810096071,0.824570169,0.831693416,0.821573706,0.805516257,0.822554404]`; `median_s=0.810096071`; `max_s=0.831693416`; `ratio=2.081008473`; `peak_rss_bytes=449802240`.

Series 6 passed every scale, maximum-sample and RSS gate, but its fused 1,024 full-no-op median
was 0.810096071 seconds and therefore failed the non-negotiable 0.800000-second median gate. That
failure is retained. A diagnostic profile then identified redundant wrapper dispatch and duplicate
dictionary lookup on already verified call-local values. The cache-hit path was reduced to one
lookup while preserving the same object/value collision check; no verification fact, canonical
byte, ID, public API or cache lifetime changed. After the complete correctness/resource selection
passed again, Series 7 was announced and run once against binary diff
`e95dac35d6d390d265e72d6d7d9b6920f5506cac1c6b6caa7f06a035743225dd` and code/test diff
`7d1095e901cecef384740a32e2bfa638a508bd22cb1f2f5b3e55d163cea3d734`. It likewise completed
all 18 isolated children without restarting or changing any sample.

Series 7 ordered ledger (`from_commit` first, then fused preparation; RSS includes fixture,
warm-ups and samples in that isolated process):

- `from_commit` initial uncertain, 512: `samples_s=[0.117599669,0.116539786,0.117786999,0.117024608,0.117010297,0.116743124,0.121175387,0.117865105,0.120685715]`; `median_s=0.117599669`; `max_s=0.121175387`; `peak_rss_bytes=73228288`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.233007686,0.232464370,0.231536369,0.235033942,0.231964618,0.232510396,0.231661289,0.232033043,0.236198612]`; `median_s=0.232464370`; `max_s=0.236198612`; `ratio=1.976743404`; `peak_rss_bytes=109506560`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.130523515,0.134019379,0.130561279,0.132491884,0.132021947,0.132670160,0.136891223,0.130902983,0.131680492]`; `median_s=0.132021947`; `max_s=0.136891223`; `peak_rss_bytes=157184000`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.266953379,0.273601557,0.291059616,0.272414276,0.271650310,0.276987896,0.266152637,0.274739243,0.274582590]`; `median_s=0.273601557`; `max_s=0.291059616`; `ratio=2.072394501`; `peak_rss_bytes=278499328`;
- `from_commit` full no-op, 512: `samples_s=[0.158678585,0.156034515,0.158454315,0.158038658,0.159064085,0.157048504,0.167614829,0.159513096,0.157723021]`; `median_s=0.158454315`; `max_s=0.167614829`; `peak_rss_bytes=224305152`;
- `from_commit` full no-op, 1,024: `samples_s=[0.327380010,0.327837307,0.322668739,0.321024925,0.325270080,0.331836205,0.327591007,0.322700148,0.321038450]`; `median_s=0.325270080`; `max_s=0.331836205`; `ratio=2.052768838`; `peak_rss_bytes=414273536`;
- fused initial uncertain, 512: `samples_s=[0.209193122,0.212762205,0.212846102,0.216365270,0.210031277,0.209747223,0.215456609,0.212847787,0.208562653]`; `median_s=0.212762205`; `max_s=0.216365270`; `peak_rss_bytes=92729344`;
- fused initial uncertain, 1,024: `samples_s=[0.421690580,0.419074892,0.426517230,0.420016004,0.419700458,0.421244120,0.417153884,0.427846324,0.418295156]`; `median_s=0.420016004`; `max_s=0.427846324`; `ratio=1.974110035`; `peak_rss_bytes=150732800`;
- fused initial incomplete, 512: `samples_s=[0.201772221,0.195565426,0.196810233,0.200289893,0.199951344,0.197546116,0.199600793,0.197633634,0.198135377]`; `median_s=0.198135377`; `max_s=0.201772221`; `peak_rss_bytes=85753856`;
- fused initial incomplete, 1,024: `samples_s=[0.398581163,0.396708722,0.399521360,0.416294875,0.398626321,0.406924623,0.408640783,0.396702013,0.396403562]`; `median_s=0.398626321`; `max_s=0.416294875`; `ratio=2.011888674`; `peak_rss_bytes=136802304`;
- fused complete to uncertain, 512: `samples_s=[0.288664762,0.285470180,0.281423583,0.284562970,0.283182949,0.282611240,0.281595042,0.281052381,0.282302233]`; `median_s=0.282611240`; `max_s=0.288664762`; `peak_rss_bytes=157212672`;
- fused complete to uncertain, 1,024: `samples_s=[0.530058874,0.527730041,0.526491397,0.526985133,0.532376370,0.529756267,0.534375922,0.542429494,0.531867178]`; `median_s=0.530058874`; `max_s=0.542429494`; `ratio=1.875576053`; `peak_rss_bytes=278528000`;
- fused uncertain to incomplete, 512: `samples_s=[0.383761259,0.384521264,0.386089933,0.378053986,0.378335285,0.397455533,0.399255239,0.393759976,0.402292859]`; `median_s=0.386089933`; `max_s=0.402292859`; `peak_rss_bytes=222601216`;
- fused uncertain to incomplete, 1,024: `samples_s=[0.702852595,0.709719009,0.705981688,0.707983818,0.706451947,0.716605821,0.703092339,0.711356683,0.716696498]`; `median_s=0.707983818`; `max_s=0.716696498`; `ratio=1.833727734`; `peak_rss_bytes=410918912`;
- fused mixed transition/no-op, 512: `samples_s=[0.285406841,0.284534084,0.285689358,0.280865126,0.280408366,0.298238079,0.287386143,0.286563744,0.286609359]`; `median_s=0.285689358`; `max_s=0.298238079`; `peak_rss_bytes=166121472`;
- fused mixed transition/no-op, 1,024: `samples_s=[0.575893974,0.583865114,0.576634808,0.573451072,0.575022280,0.569553311,0.576841284,0.573461504,0.571890537]`; `median_s=0.575022280`; `max_s=0.583865114`; `ratio=2.012753587`; `peak_rss_bytes=296189952`;
- fused full no-op, 512: `samples_s=[0.387462378,0.416996195,0.398451269,0.397831778,0.390439452,0.395107575,0.389439295,0.390111029,0.403526231]`; `median_s=0.395107575`; `max_s=0.416996195`; `peak_rss_bytes=242098176`;
- fused full no-op, 1,024: `samples_s=[0.800556591,0.788896064,0.788405089,0.786853790,0.790887412,0.794700568,0.800262130,0.799500722,0.799622003]`; `median_s=0.794700568`; `max_s=0.800556591`; `ratio=2.011352397`; `peak_rss_bytes=449769472`.

Every Series 7 1,024 median is at most 0.800000 seconds, every maximum is at most 1.000000
second, every 1,024/512 median ratio is at most 2.5 and every isolated-process peak RSS is below
1.5 GiB. The slowest median is 0.794700568 seconds, the slowest sample is 0.800556591 seconds, the
worst ratio is 2.072394501 and the highest peak RSS is 449,769,472 bytes. These results are
TerraPC- and fixture-bound rather than absolute worst-case guarantees. The full 3B1C-2 runtime
path still must integrate these dormant contracts and prove its separate warm median at or below
1.0 second.

Final atomicity review then identified a retained-parent verification gap in the ordinary
`prepare_coverage_mutation_batch(...)` writer. The writer now constructs one call-local
verification context, fully rederives the v3 fan-out proof and optional raw binding before target
decisions, and reuses that same verified context for every decision and aggregate charge. Six
tamper cases cover fan-out content, digest and catalog plus raw-binding content, digest and ID. No
canonical byte, version, identity or benchmarked fused/from-commit path changed. The complete
correctness/resource selection passed before Series 8 was announced. Series 8 ran once against
binary diff `dfa81467fad882942f4cb1c9d67fdb97149fab48613baa86f4cb703101c5283f` and code/test diff
`122714cf5f64d97eb038a2a7a72947a519e6a58855f5e51dc5f37ca55006de35`. It completed all 18
isolated children without restart, replacement or sample selection; its append-only ledger SHA-256
is `921d406b616ba249d5a00f06950b893228c47a3906c3a7b91e044c917b1bc8d0`.

Series 8 ordered ledger (`from_commit` first, then fused preparation; RSS includes fixture,
warm-ups and samples in that isolated process):

- `from_commit` initial uncertain, 512: `samples_s=[0.118578796,0.116236352,0.117208144,0.117725042,0.116700597,0.116565646,0.117878603,0.117056103,0.115708604]`; `median_s=0.117056103`; `max_s=0.118578796`; `peak_rss_bytes=73101312`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.233698489,0.230312837,0.231967761,0.237470047,0.234036955,0.231481414,0.232480509,0.233559518,0.241352344]`; `median_s=0.233559518`; `max_s=0.241352344`; `ratio=1.995278435`; `peak_rss_bytes=110170112`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.130599944,0.131201838,0.130986944,0.131749445,0.132478350,0.130510746,0.131428032,0.131858704,0.130319586]`; `median_s=0.131201838`; `max_s=0.132478350`; `peak_rss_bytes=156786688`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.270495775,0.270418415,0.267278577,0.267220699,0.269190046,0.268451631,0.267717677,0.265336147,0.269259242]`; `median_s=0.268451631`; `max_s=0.270495775`; `ratio=2.046096572`; `peak_rss_bytes=278347776`;
- `from_commit` full no-op, 512: `samples_s=[0.156423677,0.156654865,0.158117897,0.156206779,0.157245591,0.156355340,0.156642313,0.158523389,0.156673081]`; `median_s=0.156654865`; `max_s=0.158523389`; `peak_rss_bytes=224280576`;
- `from_commit` full no-op, 1,024: `samples_s=[0.316343779,0.326224600,0.329236108,0.319304906,0.320860257,0.330467287,0.324375538,0.321385223,0.319455466]`; `median_s=0.321385223`; `max_s=0.330467287`; `ratio=2.051549583`; `peak_rss_bytes=414175232`;
- fused initial uncertain, 512: `samples_s=[0.207760271,0.208714801,0.213074025,0.210771342,0.215365939,0.208827375,0.209772327,0.223522974,0.219393752]`; `median_s=0.210771342`; `max_s=0.223522974`; `peak_rss_bytes=92721152`;
- fused initial uncertain, 1,024: `samples_s=[0.420666671,0.415746013,0.420398620,0.419059302,0.421280366,0.431913068,0.421523673,0.418325234,0.419764618]`; `median_s=0.420398620`; `max_s=0.431913068`; `ratio=1.994572013`; `peak_rss_bytes=150757376`;
- fused initial incomplete, 512: `samples_s=[0.197608879,0.197694465,0.196508825,0.196094068,0.196341292,0.199038803,0.196869797,0.196872858,0.197643231]`; `median_s=0.196872858`; `max_s=0.199038803`; `peak_rss_bytes=85856256`;
- fused initial incomplete, 1,024: `samples_s=[0.400621387,0.397822624,0.406008938,0.394384606,0.396461826,0.400892016,0.398248995,0.400997658,0.394424555]`; `median_s=0.398248995`; `max_s=0.406008938`; `ratio=2.022874047`; `peak_rss_bytes=136609792`;
- fused complete to uncertain, 512: `samples_s=[0.293332834,0.292631602,0.294800551,0.309480744,0.293768503,0.285481158,0.283944745,0.286740295,0.284919265]`; `median_s=0.292631602`; `max_s=0.309480744`; `peak_rss_bytes=156966912`;
- fused complete to uncertain, 1,024: `samples_s=[0.525819390,0.529813260,0.526144946,0.527613059,0.529883531,0.527912067,0.527680645,0.526131399,0.527284740]`; `median_s=0.527613059`; `max_s=0.529883531`; `ratio=1.802994124`; `peak_rss_bytes=278581248`;
- fused uncertain to incomplete, 512: `samples_s=[0.380135282,0.390657189,0.398140121,0.386362914,0.382988509,0.385123572,0.381199223,0.380610132,0.384845900]`; `median_s=0.384845900`; `max_s=0.398140121`; `peak_rss_bytes=222130176`;
- fused uncertain to incomplete, 1,024: `samples_s=[0.709538608,0.703283215,0.704292669,0.706051740,0.707359955,0.701581090,0.703382666,0.708713493,0.710063573]`; `median_s=0.706051740`; `max_s=0.710063573`; `ratio=1.834634954`; `peak_rss_bytes=410906624`;
- fused mixed transition/no-op, 512: `samples_s=[0.282833441,0.293512104,0.295030402,0.288429652,0.284967327,0.280988623,0.282593968,0.285233673,0.289484285]`; `median_s=0.285233673`; `max_s=0.295030402`; `peak_rss_bytes=165453824`;
- fused mixed transition/no-op, 1,024: `samples_s=[0.570097437,0.582962185,0.568286639,0.570467257,0.575779412,0.568692427,0.568281309,0.564743882,0.570624111]`; `median_s=0.570097437`; `max_s=0.582962185`; `ratio=1.998703137`; `peak_rss_bytes=296345600`;
- fused full no-op, 512: `samples_s=[0.389526852,0.384233114,0.386237412,0.385889652,0.400000483,0.384540781,0.384034595,0.397076212,0.391865356]`; `median_s=0.386237412`; `max_s=0.400000483`; `peak_rss_bytes=241999872`;
- fused full no-op, 1,024: `samples_s=[0.790591347,0.802912768,0.790723904,0.802440504,0.792857738,0.790821342,0.792540991,0.790710708,0.790911471]`; `median_s=0.790911471`; `max_s=0.802912768`; `ratio=2.047733975`; `peak_rss_bytes=449912832`.

Every Series 8 1,024 median is at most 0.800000 seconds, every maximum is at most 1.000000
second, every 1,024/512 median ratio is at most 2.5 and every isolated-process peak RSS is below
1.5 GiB. The slowest median is 0.790911471 seconds, the slowest sample is 0.802912768 seconds, the
worst ratio is 2.051549583 and the highest peak RSS is 449,912,832 bytes. These results are
TerraPC- and fixture-bound rather than absolute worst-case guarantees. The full 3B1C-2 runtime
path still must integrate these dormant contracts and prove its separate warm median at or below
1.0 second.

The family-filtered retained-proof correction described above passed the full correctness and
resource selection before Series 9 was announced. Series 9 ran once against binary diff
`44ed44d7ee2678900367d7ab4cf58293f7d63452728129b996b2a80d11ffec8a` and code/test diff
`c7d05a8e58f9aff1020eb83ef8b731df3bd606e28d4186c5d38f8f3a0f3e70b9`. It completed all 18
isolated children without restart, replacement or sample selection; its append-only ledger SHA-256
is `0ad83dc38c01943d08386acd68cdc750a3c71b38e333cdec7b9c7d622d68c749`.

Series 9 ordered ledger (`from_commit` first, then fused preparation; RSS includes fixture,
warm-ups and samples in that isolated process):

- `from_commit` initial uncertain, 512: `samples_s=[0.119505185,0.120124779,0.117513416,0.117849650,0.118169502,0.117608054,0.116335876,0.118357975,0.118401197]`; `median_s=0.118169502`; `max_s=0.120124779`; `peak_rss_bytes=73146368`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.232240649,0.243832061,0.237125650,0.237121671,0.234242629,0.232520289,0.231179876,0.231796758,0.233129202]`; `median_s=0.233129202`; `max_s=0.243832061`; `ratio=1.972837306`; `peak_rss_bytes=110129152`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.134109327,0.135933013,0.131925244,0.132339664,0.130273771,0.134270453,0.138134331,0.135891321,0.131806198]`; `median_s=0.134109327`; `max_s=0.138134331`; `peak_rss_bytes=156827648`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.266005146,0.270137823,0.265772901,0.271194047,0.268751548,0.269096299,0.268707255,0.271772220,0.268808854]`; `median_s=0.268808854`; `max_s=0.271772220`; `ratio=2.004400887`; `peak_rss_bytes=278564864`;
- `from_commit` full no-op, 512: `samples_s=[0.158174707,0.156838095,0.158490848,0.159541340,0.158107273,0.159135593,0.158161534,0.158669427,0.169147388]`; `median_s=0.158490848`; `max_s=0.169147388`; `peak_rss_bytes=224509952`;
- `from_commit` full no-op, 1,024: `samples_s=[0.331822480,0.325432996,0.327449127,0.326147878,0.325947764,0.326974856,0.328760037,0.340404288,0.324486542]`; `median_s=0.326974856`; `max_s=0.340404288`; `ratio=2.063051969`; `peak_rss_bytes=414351360`;
- fused initial uncertain, 512: `samples_s=[0.210780388,0.210588112,0.212020673,0.211258400,0.212116460,0.212432451,0.210933727,0.215385318,0.210565712]`; `median_s=0.211258400`; `max_s=0.215385318`; `peak_rss_bytes=92504064`;
- fused initial uncertain, 1,024: `samples_s=[0.424814327,0.422887581,0.436305163,0.431033006,0.425966631,0.422874529,0.424428423,0.434763671,0.428858053]`; `median_s=0.425966631`; `max_s=0.436305163`; `ratio=2.016329912`; `peak_rss_bytes=150597632`;
- fused initial incomplete, 512: `samples_s=[0.197497902,0.198793031,0.201435714,0.198913521,0.199514852,0.204438382,0.200086490,0.208364726,0.199797562]`; `median_s=0.199797562`; `max_s=0.208364726`; `peak_rss_bytes=85618688`;
- fused initial incomplete, 1,024: `samples_s=[0.421165817,0.396290292,0.402200378,0.395423976,0.405252139,0.397586719,0.398808735,0.392713310,0.407480991]`; `median_s=0.398808735`; `max_s=0.421165817`; `ratio=1.996064071`; `peak_rss_bytes=136593408`;
- fused complete to uncertain, 512: `samples_s=[0.284265202,0.287877693,0.289168118,0.281597660,0.283492028,0.285689886,0.283187914,0.283886715,0.288800652]`; `median_s=0.284265202`; `max_s=0.289168118`; `peak_rss_bytes=156946432`;
- fused complete to uncertain, 1,024: `samples_s=[0.538295521,0.535983379,0.533736881,0.548323611,0.534438120,0.531837278,0.531833250,0.533519367,0.544135478]`; `median_s=0.534438120`; `max_s=0.548323611`; `ratio=1.880068739`; `peak_rss_bytes=278618112`;
- fused uncertain to incomplete, 512: `samples_s=[0.386472852,0.387674339,0.384675515,0.383707316,0.389017713,0.384645793,0.383384052,0.394092216,0.388069526]`; `median_s=0.386472852`; `max_s=0.394092216`; `peak_rss_bytes=222416896`;
- fused uncertain to incomplete, 1,024: `samples_s=[0.731337074,0.719148273,0.720798438,0.711251202,0.712505819,0.714768067,0.710776608,0.712058359,0.736431319]`; `median_s=0.714768067`; `max_s=0.736431319`; `ratio=1.849465139`; `peak_rss_bytes=410853376`;
- fused mixed transition/no-op, 512: `samples_s=[0.284853901,0.285785148,0.283857113,0.284277251,0.280986021,0.283847573,0.285425810,0.285065877,0.282383369]`; `median_s=0.284277251`; `max_s=0.285785148`; `peak_rss_bytes=166023168`;
- fused mixed transition/no-op, 1,024: `samples_s=[0.576637520,0.582373043,0.574816111,0.576850945,0.592181112,0.579875983,0.581319925,0.574097801,0.587403540]`; `median_s=0.579875983`; `max_s=0.592181112`; `ratio=2.039825491`; `peak_rss_bytes=296255488`;
- fused full no-op, 512: `samples_s=[0.396864494,0.390084388,0.393656640,0.382634301,0.390445804,0.390589511,0.384876138,0.391727563,0.389089617]`; `median_s=0.390445804`; `max_s=0.396864494`; `peak_rss_bytes=241954816`;
- fused full no-op, 1,024: `samples_s=[0.804315111,0.811338975,0.800016109,0.813619827,0.790596244,0.795454624,0.795781957,0.803406503,0.822745783]`; `median_s=0.803406503`; `max_s=0.822745783`; `ratio=2.057664584`; `peak_rss_bytes=450019328`.

Series 9 passed every maximum-sample, scale and RSS gate, but its fused 1,024 full-no-op median
was 0.803406503 seconds and therefore failed the strict 0.800000-second median gate. That failure
is retained. A profile found no missing compact identity; it found duplicate construction-time
validation only. For `ALL_POSSIBLY_ACTIVE`, exact equality with the family-filtered reconstructed
scope tuple already proves target spec, domain and family, so redundant set/domain scans were
removed. The fused writer already fully verifies current state, request, evidence boundary and
severity before sealing a no-op, and fully verifies fan-out v3 and optional raw symmetry before its
leaf loop. It now derives that exact no-op row once and omits only the duplicate generic
construction-time passes. Independent retained-load verifiers remain unchanged. The complete
correctness/resource selection passed before Series 10 was announced.

Series 10 ran once against binary diff
`1d57ec517ed8bfccc6c5b3491ba20cc5260931c771d9b620b0ccc8dfbeb44580` and code/test diff
`a92a39905b0559c238ffb7a48c3bf42fe9a4196aaa7369aae742bcb53f2426a2`. It completed all 18
isolated children without restart, replacement or sample selection; its append-only ledger SHA-256
is `0e479d4f2797eab9376ebb357c0fcaa43dcf5b772834c4715a620db68efb28cd`.

Series 10 ordered ledger (`from_commit` first, then fused preparation; RSS includes fixture,
warm-ups and samples in that isolated process):

- `from_commit` initial uncertain, 512: `samples_s=[0.119851760,0.119875341,0.118879098,0.116932266,0.120903017,0.122975407,0.119261446,0.116958747,0.119174459]`; `median_s=0.119261446`; `max_s=0.122975407`; `peak_rss_bytes=72916992`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.233041132,0.235866972,0.233882194,0.231838394,0.233132358,0.235273097,0.232424315,0.232412246,0.235087703]`; `median_s=0.233132358`; `max_s=0.235866972`; `ratio=1.954800699`; `peak_rss_bytes=110403584`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.131901946,0.141121080,0.130705757,0.131174712,0.131590883,0.131719673,0.130904731,0.131834802,0.135750731]`; `median_s=0.131719673`; `max_s=0.141121080`; `peak_rss_bytes=157044736`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.264748127,0.264187635,0.267063192,0.276353357,0.275863640,0.273099017,0.270322027,0.266263011,0.267719479]`; `median_s=0.267719479`; `max_s=0.276353357`; `ratio=2.032494258`; `peak_rss_bytes=278298624`;
- `from_commit` full no-op, 512: `samples_s=[0.157146738,0.155189434,0.156264182,0.155453422,0.157878373,0.154410233,0.156311586,0.155927097,0.158938123]`; `median_s=0.156264182`; `max_s=0.158938123`; `peak_rss_bytes=224362496`;
- `from_commit` full no-op, 1,024: `samples_s=[0.323133463,0.320518391,0.320848615,0.321582850,0.329750286,0.318949163,0.328355333,0.338856780,0.322141592]`; `median_s=0.322141592`; `max_s=0.338856780`; `ratio=2.061519075`; `peak_rss_bytes=414195712`;
- fused initial uncertain, 512: `samples_s=[0.211595638,0.210812021,0.217054015,0.212408659,0.213879677,0.209938971,0.209330492,0.210215219,0.211833616]`; `median_s=0.211595638`; `max_s=0.217054015`; `peak_rss_bytes=92532736`;
- fused initial uncertain, 1,024: `samples_s=[0.422772747,0.423861811,0.424028148,0.418643938,0.431599267,0.426888390,0.427880344,0.426092523,0.422562690]`; `median_s=0.424028148`; `max_s=0.431599267`; `ratio=2.003955053`; `peak_rss_bytes=150720512`;
- fused initial incomplete, 512: `samples_s=[0.198088453,0.197830313,0.202344080,0.202280694,0.196762775,0.199842273,0.197781099,0.202912584,0.196157479]`; `median_s=0.198088453`; `max_s=0.202912584`; `peak_rss_bytes=85843968`;
- fused initial incomplete, 1,024: `samples_s=[0.408283284,0.414438543,0.418038818,0.410291854,0.398971898,0.399050503,0.400656153,0.397280112,0.402610083]`; `median_s=0.402610083`; `max_s=0.418038818`; `ratio=2.032476285`; `peak_rss_bytes=136466432`;
- fused complete to uncertain, 512: `samples_s=[0.281033397,0.280033781,0.286868598,0.281965096,0.280536218,0.282523847,0.285001448,0.283995432,0.283353866]`; `median_s=0.282523847`; `max_s=0.286868598`; `peak_rss_bytes=156946432`;
- fused complete to uncertain, 1,024: `samples_s=[0.547637559,0.542453707,0.532871144,0.530765959,0.540197528,0.531748062,0.528796033,0.540751868,0.535167767]`; `median_s=0.535167767`; `max_s=0.547637559`; `ratio=1.894239275`; `peak_rss_bytes=278597632`;
- fused uncertain to incomplete, 512: `samples_s=[0.392083939,0.382365186,0.380609325,0.383983669,0.383368842,0.381348975,0.383851711,0.386884279,0.382835800]`; `median_s=0.383368842`; `max_s=0.392083939`; `peak_rss_bytes=223051776`;
- fused uncertain to incomplete, 1,024: `samples_s=[0.733733231,0.712685166,0.717375017,0.711054294,0.725807503,0.718900989,0.723283585,0.705836470,0.712686301]`; `median_s=0.717375017`; `max_s=0.733733231`; `ratio=1.871239752`; `peak_rss_bytes=410607616`;
- fused mixed transition/no-op, 512: `samples_s=[0.280908631,0.280245321,0.280987307,0.280633679,0.304913994,0.281316428,0.283033236,0.286782170,0.295700918]`; `median_s=0.281316428`; `max_s=0.304913994`; `peak_rss_bytes=165683200`;
- fused mixed transition/no-op, 1,024: `samples_s=[0.574533832,0.577521993,0.570896253,0.569247990,0.572083917,0.583148887,0.582494760,0.573982893,0.570210154]`; `median_s=0.573982893`; `max_s=0.583148887`; `ratio=2.040346158`; `peak_rss_bytes=296169472`;
- fused full no-op, 512: `samples_s=[0.391114622,0.394196033,0.389431268,0.383806975,0.383053729,0.386732803,0.388280121,0.381731898,0.382968733]`; `median_s=0.386732803`; `max_s=0.394196033`; `peak_rss_bytes=242167808`;
- fused full no-op, 1,024: `samples_s=[0.790247370,0.800836814,0.809815022,0.792892892,0.795126322,0.786511766,0.811541931,0.790585457,0.788481514]`; `median_s=0.792892892`; `max_s=0.811541931`; `ratio=2.050234389`; `peak_rss_bytes=449294336`.

Every Series 10 1,024 median is at most 0.800000 seconds, every maximum is at most 1.000000
second, every 1,024/512 median ratio is at most 2.5 and every isolated-process peak RSS is below
1.5 GiB. The slowest median is 0.792892892 seconds, the slowest sample is 0.811541931 seconds, the
worst ratio is 2.061519075 and the highest peak RSS is 449,294,336 bytes. These results are
TerraPC- and fixture-bound rather than absolute worst-case guarantees. The full 3B1C-2 runtime
path still must integrate these dormant contracts and prove its separate warm median at or below
1.0 second.

After the low-severity writer-tag metadata correction, the affected parser/writer and retained
fan-out selection passed before Series 11 was announced. The accepted parser set remained exactly
v1/v2/v3 and no canonical byte or validation rule changed. Series 11 ran once against binary diff
`6f5dc267fbc7dbc5861e6d68f1efa04f9219cd7ae7ed5ede5619bd905e09d86a` and final code/test diff
`798718080f248fc4d8ba2a0aa1e0206e0b1712c69740eebbba66680ccc13c2f0`. It reused the exact
Series 10 isolated child harness with SHA-256
`f91d98ddb92311629d7d0b322130e12b2de73dc031ade269cc99ca27d00d09b7`, completed all 18
children without restart, replacement or sample selection, and produced append-only ledger SHA-256
`7bf0f53c232f564a776db6eafd397b16661e8b2779fd342d29ce778c7a7901fe`.

Series 11 ordered ledger (`from_commit` first, then fused preparation; RSS includes fixture,
warm-ups and samples in that isolated process):

- `from_commit` initial uncertain, 512: `samples_s=[0.117819328,0.117300276,0.122065600,0.117053756,0.117288354,0.117032164,0.115857492,0.115988489,0.117147374]`; `median_s=0.117147374`; `max_s=0.122065600`; `peak_rss_bytes=73207808`;
- `from_commit` initial uncertain, 1,024: `samples_s=[0.229879606,0.243932635,0.233438509,0.229936805,0.239304307,0.235816415,0.231520014,0.243218074,0.238896973]`; `median_s=0.235816415`; `max_s=0.243932635`; `ratio=2.012989339`; `peak_rss_bytes=110014464`;
- `from_commit` complete to uncertain, 512: `samples_s=[0.131734142,0.132379371,0.131544187,0.133809159,0.134589571,0.137017241,0.130768864,0.132012213,0.140530371]`; `median_s=0.132379371`; `max_s=0.140530371`; `peak_rss_bytes=157147136`;
- `from_commit` complete to uncertain, 1,024: `samples_s=[0.263316796,0.264735629,0.265949776,0.271850580,0.265076046,0.263249170,0.262297744,0.262467168,0.263840408]`; `median_s=0.263840408`; `max_s=0.271850580`; `ratio=1.993062862`; `peak_rss_bytes=278425600`;
- `from_commit` full no-op, 512: `samples_s=[0.157722542,0.155353140,0.157665930,0.164405640,0.157762664,0.156842062,0.157835062,0.159565021,0.156945391]`; `median_s=0.157722542`; `max_s=0.164405640`; `peak_rss_bytes=224264192`;
- `from_commit` full no-op, 1,024: `samples_s=[0.337944642,0.320113685,0.319288229,0.321124686,0.319489454,0.318771236,0.317981732,0.324646073,0.318034326]`; `median_s=0.319489454`; `max_s=0.337944642`; `ratio=2.025642308`; `peak_rss_bytes=414511104`;
- fused initial uncertain, 512: `samples_s=[0.208453075,0.206901473,0.207144381,0.210759318,0.206741072,0.206267254,0.206735401,0.207365648,0.206707360]`; `median_s=0.206901473`; `max_s=0.210759318`; `peak_rss_bytes=92778496`;
- fused initial uncertain, 1,024: `samples_s=[0.428360671,0.430362513,0.420763693,0.433511241,0.417641370,0.417392347,0.421354412,0.416242306,0.424600356]`; `median_s=0.421354412`; `max_s=0.433511241`; `ratio=2.036497884`; `peak_rss_bytes=150691840`;
- fused initial incomplete, 512: `samples_s=[0.196689990,0.196657547,0.197695391,0.197088242,0.197312220,0.197234934,0.197368613,0.197879957,0.197217483]`; `median_s=0.197234934`; `max_s=0.197879957`; `peak_rss_bytes=85741568`;
- fused initial incomplete, 1,024: `samples_s=[0.403316825,0.405035188,0.403983028,0.398909206,0.417142808,0.421551397,0.396011510,0.395254138,0.398308747]`; `median_s=0.403316825`; `max_s=0.421551397`; `ratio=2.044854919`; `peak_rss_bytes=136581120`;
- fused complete to uncertain, 512: `samples_s=[0.288719031,0.289052261,0.289001510,0.285412090,0.292001317,0.284473817,0.282297504,0.281718026,0.279782688]`; `median_s=0.285412090`; `max_s=0.292001317`; `peak_rss_bytes=156753920`;
- fused complete to uncertain, 1,024: `samples_s=[0.542291076,0.528278544,0.524853864,0.528584324,0.532372932,0.536602044,0.527838451,0.528307742,0.533269615]`; `median_s=0.528584324`; `max_s=0.542291076`; `ratio=1.852003971`; `peak_rss_bytes=278339584`;
- fused uncertain to incomplete, 512: `samples_s=[0.379686731,0.433701408,0.386940108,0.389189980,0.410163802,0.390158654,0.388235658,0.382959363,0.380709819]`; `median_s=0.388235658`; `max_s=0.433701408`; `peak_rss_bytes=222375936`;
- fused uncertain to incomplete, 1,024: `samples_s=[0.707194638,0.715219586,0.713386277,0.732738462,0.702051523,0.702290981,0.724652551,0.711495216,0.706927028]`; `median_s=0.711495216`; `max_s=0.732738462`; `ratio=1.832637475`; `peak_rss_bytes=410689536`;
- fused mixed transition/no-op, 512: `samples_s=[0.285087799,0.297886106,0.281754825,0.286847966,0.278815443,0.280675050,0.279834551,0.282499123,0.278484538]`; `median_s=0.281754825`; `max_s=0.297886106`; `peak_rss_bytes=165965824`;
- fused mixed transition/no-op, 1,024: `samples_s=[0.569415788,0.577737689,0.567442110,0.565040886,0.561737791,0.571339993,0.566642105,0.566628324,0.563932017]`; `median_s=0.566642105`; `max_s=0.577737689`; `ratio=2.011117662`; `peak_rss_bytes=295976960`;
- fused full no-op, 512: `samples_s=[0.383302284,0.382743552,0.383319506,0.381137951,0.384047935,0.391247234,0.383368441,0.387220381,0.390368054]`; `median_s=0.383368441`; `max_s=0.391247234`; `peak_rss_bytes=242085888`;
- fused full no-op, 1,024: `samples_s=[0.793814509,0.796460020,0.793836825,0.785515012,0.789060659,0.831679216,0.801635667,0.794327686,0.787743893]`; `median_s=0.793836825`; `max_s=0.831679216`; `ratio=2.070689029`; `peak_rss_bytes=449732608`.

Every Series 11 1,024 median is at most 0.800000 seconds, every maximum is at most 1.000000
second, every 1,024/512 median ratio is at most 2.5 and every isolated-process peak RSS is below
1.5 GiB. The slowest median is 0.793836825 seconds, the slowest sample is 0.831679216 seconds, the
worst ratio is 2.070689029 and the highest peak RSS is 449,732,608 bytes. These results are
TerraPC- and fixture-bound rather than absolute worst-case guarantees. The full 3B1C-2 runtime
path still must integrate these dormant contracts and prove its separate warm median at or below
1.0 second.

**3B1C-1G typed lossy raw-rejection projection.** The existing ordinary upstream route deliberately
requires exact status equality, and `EventCoverage` rejects a Silver state that is less severe than
its exact committed Bronze parent. That remains correct for a parsed or otherwise classified
item. It cannot, however, express a definitive raw-sink rejection before parsing without making an
unsupported claim: Bronze is `CONFIRMED_INCOMPLETE` because the expected raw record was rejected,
while Silver membership is unknown and therefore Silver can only be `UNCERTAIN`. Treating Silver as
`CONFIRMED_INCOMPLETE` would claim that a Silver-relevant item was proven lost.

The closed knowledge table is normative:

| Projection membership at the rejection boundary | Silver effect |
| --- | --- |
| proven `INCLUDED` | `CONFIRMED_INCOMPLETE` |
| proven `EXCLUDED` | no Silver degradation from this rejection |
| `UNKNOWN` because rejection occurred before parsing/projecting | `UNCERTAIN` |

Only the final row is introduced by 3B1C-1G. It uses fixed
`RawRejectionProjectionMembership.UNKNOWN` and
`RawRejectionProjectionBoundary.BEFORE_PARSING`; neither is a caller-selected free label. A
generic `CONFIRMED_INCOMPLETE -> UNCERTAIN` conversion remains forbidden, and the existing
`BatchVerifiedUpstreamCoveragePreparation.from_committed_upstream(...)` exact-status path and
general `EventCoverage` invariant remain strict. The exact-status factories reject this pre-parse
raw-rejection cause because no typed `INCLUDED` proof exists; selecting the older factory cannot
turn `UNKNOWN` into `INCLUDED`.

`RawRejectionNormalizationUnknownEvidenceSource` retains the complete upstream batch, exact
positional `CommittedCoverageState`, exact typed raw-record rejection evidence, raw fan-out binding
and exact matching downstream Silver scope. Its canonical source row is:

```text
["raw-rejection-normalization-unknown-source-v1", "unknown", "before-parsing",
 upstream_coverage_mutation_batch_id, committed_coverage_state_id, result_ordinal,
 raw_rejection_evidence_id, raw_coverage_fanout_binding_id, raw_record_id,
 full_record_integrity_sha256, downstream_coverage_scope_id]
```

Only evidence kind `raw-rejection-normalization-unknown` writes the new evidence-v3 identity:

```text
["coverage-evidence-content-v3", coverage_scope_id, coverage_epoch_id, collector_run_id,
 "raw-rejection-normalization-unknown", typed_raw_rejection_projection_source_row_v1,
 canonical_observed_at, observed_monotonic_ns]

["coverage-evidence-v3", coverage_scope_id, coverage_epoch_id, collector_run_id,
 "raw-rejection-normalization-unknown", typed_raw_rejection_projection_source_row_v1,
 canonical_observed_at, observed_monotonic_ns, content_sha256]
```

Stored verification fully rederives the accepted upstream mutation/acceptance/result transcript,
exact `result_ordinal`, committed state, raw rejection cause, raw record and full-record digest,
fan-out v3 plan/catalog/session/attempt membership and Bronze-to-Silver scope pairing before
rederiving the source row, evidence content, digest and ID. A hash or ID without every retained
typed parent is insufficient. Missing, foreign, reordered, duplicated, cross-run, cross-epoch,
cross-scope, wrong-version or tampered input fails closed without a partial tuple.

The ordinary positional derivation API and the opt-in
`BatchVerifiedUpstreamCoveragePreparation.from_committed_raw_rejection(...)` fused API create the
same evidence, requested mutations, resulting state references and batch identities. The ordinary
standalone source/evidence factories are the fully reverifying semantic oracle and are not a
plan-scale composition path. The verified positional ordinary API and fused API each keep one
call-local transcript across complete preparation and are verified linear for 1, 4, 50 and 1,024
targets; the fused API remains the integrated atomic convenience boundary. Evidence v1 remains
writer-active for its existing
non-upstream kinds, evidence v2 remains writer-active for exact upstream state/transition
propagation, and v1/v2 golden identities remain byte-exact.
Evidence v3 is writer-active only for this lossy raw-rejection relation. Initialization v1,
transition v1, no-op v1, mutation-batch v2, acceptance v2, committed-state v2, state-reference v2,
fan-out v3, raw binding v1 and normalization-lineage v3 remain unchanged.

This phase adds pure dormant contracts only. The active collector does not import the new path, a
rejected raw record produces no fabricated normalization outcome or market event, schema v2 and
`is_gap` remain operational, and market-event v3 remains dormant. Runtime integration remains
3B1C-2 work; atomic delivery remains 3B1C-3 work. Nothing is deployed or promoted.

**Why:** No deployed dataset or ClickHouse schema depends on v2, so one atomic migration provides a
clean long-term boundary without permanent compatibility complexity while preserving reviewable,
bounded implementation slices.

### COURSE-1 reactivation gate

ADR-022 may return to active implementation only after the BTC-PERP vertical slice identifies a
direct consumer for its guarantees and the selected core-engine ADR states how the project/engine
boundary preserves them. Reactivation must compare the remaining work with the concrete product
risk it removes. Prior implementation effort, plan-scale completeness and dormant contract
coverage are not sufficient reasons by themselves.

---

## ADR-023 — Wrap NautilusTrader for the COURSE-1 BTC-PERP slice

**Decision:** Use exactly `nautilus-trader==1.231.0` for the bounded COURSE-1 BTC-PERP replay and
sandbox-PAPER slice, behind a project-owned boundary. The decision is `WRAP`, not `ADOPT`:
NautilusTrader is not the production core, is not a root dependency and receives no authority to
sign or send venue orders.

**Evidence:** The bounded D41 run `20260830T011412Z` uses one locally captured public
`MarketEventEnvelope-v2` dataset for two deterministic replays and credentialless sandbox PAPER.
The verifier independently recomputes the primary event, order, fill, position and economics
relations. Its compact gate summary and publication integrity manifest are retained with the D41
fit-gate. The result is engineering-fit evidence only, not alpha, profitability or promotion
evidence.

**Project-owned boundary:** The project, not NautilusTrader, remains authoritative for:

- exact BTC-PERP precision plus fail-closed stale, future and gap handling;
- USDC-denominated economics and explicit fee, funding, spread and slippage assumptions;
- risk and mode policy outside this bounded fit gate;
- durable persistence, idempotent restart, reconciliation and ambiguous-shutdown recovery;
- reconstructable evidence and the mapping to project correlation and source identity.

**Constraints:** No signing client, wallet, private key, venue execution client, TESTNET order,
SHADOW order or LIVE order is approved. PAPER remains the only permitted D41 execution mode and
uses local sandbox execution. Direct production adoption requires a separate decision and evidence.

**Version and license risk:** `1.231.0` declares LGPL-3.0-or-later, remains upstream Beta and is the
last legacy-v1 line while upstream development moves to v2. The pin stays isolated in the D41 lock;
root `pyproject.toml` and `uv.lock` remain unchanged. No upstream source is vendored or modified.
Any later distributable image that includes NautilusTrader requires a packaging-time LGPL
compliance review, including applicable license, notice, source and replacement/relinking duties.

**Reopen when:** the project-owned wrapper becomes a second trading framework in size or lifecycle
ownership; Nautilus order/fill/position, funding, precision or reconciliation semantics cannot
preserve project invariants; v2 migration breaks the bounded mapping; the pinned line becomes
unsupported or unsafe; licensing or distribution changes; or production-core adoption is proposed.
An unsuitable result reopens the thin-native fallback rather than silently expanding the wrapper.

---

## Pending ADR gates — not yet decisions

One material choice still requires evidence and therefore is not recorded as an accepted ADR:

1. **Runtime deployment:** after the local replay-to-PAPER route passes, record the supported
   Ubuntu LTS VPS/OCI/Compose profile, operational and rollback requirements, and how existing
   TrueNAS, ClickHouse and Grafana assets are retained or migrated without mutation by ordinary
   development tasks.
