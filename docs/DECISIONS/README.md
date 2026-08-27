# Architecture Decision Register

This register records current high-level decisions and the conditions under which they should be revisited.

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

**Implementation note:** use a disposable local ClickHouse container in DEV and a managed persistent instance/database on TrueNAS for 24/7 PAPER/SHADOW/LIVE data.

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

## ADR-012 — Keep runtime infrastructure lean on TrueNAS

**Decision:** Start with ClickHouse + Python services + Next.js + Grafana/Alloy, adding PostgreSQL and Redis when their responsibilities become necessary. Do not add Kafka, Kubernetes, Elasticsearch/OpenSearch or Spark without a demonstrated bottleneck.

**Why:** Preserve RAM, operational simplicity and reliability on the shared 64 GB TrueNAS host.

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

**Why:** Provides Linux parity, fast local iteration, strong Codex/desktop/mobile Remote workflows, better frontend/debugging ergonomics and access to the workstation's CPU/GPU without destabilizing TrueNAS.

**Alternatives considered:** direct source development on TrueNAS; native Windows/PowerShell as the primary runtime; source on an SMB share.

**Constraints:** no normal live trading credentials on the workstation; use workspace-scoped Codex permissions; store source under the Linux home filesystem rather than `/mnt/c` or SMB.

---

## ADR-016 — TrueNAS is a deployment/runtime host, not the interactive development host

**Decision:** TrueNAS runs CI-built container images for 24/7 PAPER/SHADOW/LIVE services. Do not run production-like services from a mutable repository checkout and do not hot-edit running containers.

**Why:** Isolates experimentation from persistent services, reduces failure blast radius, enables deterministic rollback and keeps the Windows PC optional during continuous operation.

**Constraint:** Hermes/TrueNAS MCP may operate deployments and project resources, but source changes return through Git and CI.

---

## ADR-017 — Build once in CI and deploy by immutable image digest

**Decision:** GitHub Actions builds release images, publishes them to private GHCR and records source commit, tag and digest. TrueNAS deploys a reviewed digest, never a floating `latest` tag.

**Why:** Reproducibility, supply-chain traceability, reliable rollback and confidence that PAPER/SHADOW/LIVE can use the same artifact.

**Constraint:** ordinary merge does not automatically deploy. PAPER deployment initially requires explicit approval; LIVE always remains protected.

---

## ADR-018 — Separate software promotion from strategy promotion

**Decision:** A strategy and a software artifact each pass their own gates. Live eligibility requires an approved strategy/version, approved image digest, approved configuration and approved runtime environment.

**Why:** A profitable model can run on broken software, while perfect software can execute an unprofitable model. Combining the approvals obscures risk.

---

## ADR-019 — Local development uses disposable data; TrueNAS owns continuous history

**Decision:** DEV uses synthetic fixtures, deterministic replay and bounded data exports in disposable local services. TrueNAS holds the authoritative self-collected 24/7 dataset.

**Why:** Keeps development fast/reproducible and prevents accidental mutation or copying of large production-like datasets.

**Constraint:** Windows may query approved TrueNAS data read-only or receive bounded exports; it does not directly mutate the runtime database.

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

## ADR-021 — Early-release TrueNAS is PAPER-only by default

**Decision:** TrueNAS 26 BETA.3 may host research and PAPER services with backups and monitoring. Material live capital should use a stable runtime release or require explicit documented risk acceptance and repeated soak/recovery testing.

**Why:** Operating-system maturity is part of execution risk, not merely infrastructure preference.

---

## ADR-022 — One provenance-complete market-event envelope, introduced atomically

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

The outer `coverage-evidence-v1` row additionally binds scope ID, epoch ID, collector-run ID,
evidence kind and injected UTC/monotonic observation boundaries. Thus none of these rows is a free
label, and membership-bearing evidence can be checked against the exact high-cardinality scope.

Coverage fan-out uses one of these closed kind-specific source rows inside
`coverage-fanout-proof-content-v1`, except the identified-rejection row, which is bound by
`coverage-fanout-proof-content-v2`:

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

**Why:** No deployed dataset or ClickHouse schema depends on v2, so one atomic migration provides a
clean long-term boundary without permanent compatibility complexity while preserving reviewable,
bounded implementation slices.
