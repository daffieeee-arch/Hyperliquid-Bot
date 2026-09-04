# Roadmap

## Delivery model

Development and runtime are deliberately separated:

- **Windows 11 + WSL2 + Codex:** source development, local tests and small research;
- **GitHub Actions:** independent CI and image construction;
- **Host-neutral Linux/amd64 OCI runtime:** 24/7 data collection and PAPER/SHADOW/LIVE; a supported Ubuntu LTS VPS is the intended primary deployment profile and TrueNAS is optional.

Fases overlap by workstream. A strategy may be in PAPER while another remains in RESEARCH, and production hardening can continue while live-market paper evidence accumulates.

## COURSE-1 — current active priority

Status: **core fit, local D01, bounded D22-A, and bounded live-public PAPER soak complete; full D22 incomplete; D22-B not implemented; provenance expansion deferred**

The current implementation order is corrected before Phase 1A continues. Existing provenance and
v3 work is preserved, but Phase 1A-3B1C-2, 3B1C-3, 3B1D and the provenance-complete form of 3B2
are frozen in the backlog. They are not complete, rejected or scheduled as the next work merely
because prerequisite contract code already exists. The detailed Phase 1A history below remains an
accurate record, not the active priority order.

The active sequence is:

1. **Complete:** ADR-023 records `WRAP` for exactly pinned NautilusTrader `1.231.0` behind the
   project-owned boundary; it is not a production-core or root-dependency adoption;
2. **Complete:** D01 provides one local Hyperliquid BTC-PERP deterministic replay -> strategy ->
   risk -> credentialless sandbox-PAPER route;
3. **Complete:** prove a bounded project-owned local PAPER ledger/restart seam without claiming
   engine-state or venue-authoritative recovery; full D22 remains incomplete;
4. if separately authorized, evaluate **D22-B — venue-authoritative crash-window
   reconciliation**; it is not implemented by D22-A or by the live-public PAPER soak;
5. **Complete (bounded):** the same D01 strategy/risk/sandbox-PAPER path can be fed by a short
   credentialless Hyperliquid public BTC-PERP trade/BBO stream and now writes create-only
   Cockpit projections (run claim, paper position, assumed PnL, orders, fills, capture-health).
   This remains a duration-bounded soak, not 24/7 collection, funding settlement, venue
   reconciliation, or promotion evidence. DATA-1A public BTC-PERP capture may now retain up to
   7 days (1–604800s) at a reconstructable Parquet/DuckDB path; that is still not a 24/7
   service. Operator retain/stop rules are in `docs/runbooks/data1a-vps-retained-capture.md`
   and `docs/runbooks/data1a-wsl-pc-retained-capture.md`. Cloud Agents are unsuitable
   for a multi-day retain.
6. after the local route passes, record the definitive runtime ADR and only then perform the VPS
   migration.

The fit decision is not a benchmark exercise. It checks only the blocking product and safety
questions: supportable pinned version, licensing, Python/runtime compatibility, Hyperliquid
instrument and precision handling, deterministic replay, fill-model transparency, credentialless
PAPER composition, fail-closed environment selection, order/fill/position semantics, persistence,
restart and reconciliation boundaries. The official Hyperliquid SDK is a conformance reference,
not a competing platform core.

### Vertical-slice scope

- Hyperliquid BTC perpetual only;
- one bounded, identified replay dataset and one deterministic run configuration;
- one explicitly non-promotable smoke strategy;
- risk-based sizing plus a hard exposure limit and stale-data rejection;
- explicit fee, funding, spread, slippage and fill assumptions;
- reproducible signals, risk decisions, orders, fills, final position and PnL;
- reconstructable run artifacts with strategy/configuration/source/correlation identity;
- exact `PAPER` startup with no exchange execution client, private key or venue order;
- no v3 activation, extra venue, ClickHouse, Redis, PostgreSQL, API, cockpit, Grafana or deployment
  dependency.

Exit gate:

- two runs over the same replay and configuration produce the same business outcomes;
- invalid or stale input and rejected risk cannot create an order;
- the run artifacts reconstruct the final PAPER position and cash/PnL state;
- the same strategy and risk path survives a short live-public-data PAPER soak;
- the core-engine ADR states accepted gaps and project-owned safety boundaries;
- no result is presented as profitability, strategy promotion, TESTNET or LIVE readiness.

Runtime-host work follows this exit gate. Host-neutral Linux/amd64 OCI/Compose is the architecture
boundary and a supported Ubuntu LTS VPS is the intended primary profile. The definitive runtime
ADR, factual VPS migration and disposition of existing TrueNAS/ClickHouse/Grafana state remain a
separate, later scope.

## Phase 0A — Blueprint and project decisions

Status: **substantially complete**

Deliverables:

- product vision;
- architecture and venue model;
- strategy/research framework;
- risk/security principles;
- frontend/Grafana vision;
- architecture decision register;
- roadmap and promotion methodology.

## Phase 0B — Windows/WSL2 development foundation

Status: **complete**

Completed through PR #2 and merge commit
`9ab3c0eb795e0ede365da7c5cabe2fe900d74518`. Python, TypeScript and secret-scanning
checks passed. Nothing was deployed and LIVE was not enabled.

Target: first development days.

Deliverables:

- current WSL2 Ubuntu environment;
- ChatGPT/Codex agent configured to run in WSL2;
- mobile Remote connectivity verified;
- repository cloned inside the WSL Linux filesystem;
- `AGENTS.md` project instructions;
- Python/Node/package versions pinned;
- Docker Desktop WSL integration verified;
- branch/PR workflow established;
- initial GitHub Actions CI skeleton;
- secret-scanning and paper-only safety baseline.

Exit gate:

- Codex can modify a feature branch, run tests and open a pull request without touching TrueNAS or using live trading credentials.

## Phase 1A — Local foundation vertical slice

Status: **started, not complete; reordered by COURSE-1**

Phase 1A-2A's pure Hyperliquid public-trade decoder, schema-v2 normalization boundary and
deterministic synthetic fixtures were merged through PR #4 in merge commit
`89339aab3099457dfce290803f2d8b5b9be8ea72`; its Python, TypeScript and secret-scan checks passed.
Phase 1A-2B's unauthenticated public-trades WebSocket lifecycle is implemented and validated in
PR #5. It remains undeployed and does not enable LIVE.
Phase 1A-3A defines and locally validates a pure offline Binance Spot raw `@trade` decoder and
schema-v2 normalization boundary with a deterministic synthetic fixture. It is not a WebSocket
collector, authenticated integration, storage path, deployment or LIVE capability.
Phase 1A-3B1A defines and tests the additive pure feed-provenance, raw-record, instrument-metadata,
coverage, event-family, v3-envelope and normalization-outcome contracts. No runtime producer
constructs or emits `MarketEventEnvelopeV3`, and v2 remains the only active Silver envelope.
The outcome contracts bind conflict/failure decisions to exact raw and event scope; Phase 1A-3B1B
activates their bounded publication and exposes only category-only sanitized validation failures,
never internal exceptions or tracebacks.
Phase 1A-3B1B integrates the storage-neutral Hyperliquid Bronze boundary: a mandatory bounded raw
sink accepts each successful application-message observation before parsing, and a separate
mandatory bounded outcome sink accepts exactly one normalization decision before control,
deduplication, counters or schema-v2 queue state becomes visible. The v3 envelope remains dormant,
v2 remains the only active Silver output, and no concrete persistence, operational coverage or
delivery outcome is added.
Phase 1A-3B1C-1 closes the pure representation gaps for coverage initialization, cause-specific
fan-out, prepared atomic mutation and no-op lineage, frame-atomic abort evidence, and exact delivery
knowledge. These contracts are dormant: 3B1B still publishes transition-empty outcomes, and no
coverage state, queue linearization, sink, consumer or health behavior changes in this slice.
Phase 1A-3B1C-1A adds the remaining pure runtime-binding rules: an ACK initialization explicitly
selects currently acknowledged attempts whose leaf scopes are to be initialized, while
normalization-outcome sink rejection versus acceptance ambiguity has exact typed Silver-coverage
evidence plus one aggregate proof binding the concrete outcome to its exact scope/attempt fan-out.
Runtime coverage is still not active; v2 and `is_gap` remain operational and v3 remains dormant.
Phase 1A-3B1C-1B adds the dormant exact pre-ACK indexed-rejection binding. It proves one known
public route from the raw capture-time attempt snapshot, permits only typed provenance-mismatch
rejection and exact Silver-normalization loss, and closes the mixed ACK/non-ACK outcome-sink scope
union without weakening acknowledged routes. No collector behavior changes in this correction.
Phase 1A-3B1C-1C closes the confirmed full-plan commitment-size blocker before runtime work
continues. It replaces new flattened mutation/acceptance content with compact ordered commitments
and moves normalization lineage to compact v3, while retaining every full typed value and one
atomic CAS. Legacy identities remain parser-only. The contracts stay dormant; 3B1C-2 resumes only
after this correction is merged.
Phase 1A-3B1C-1D closes the remaining mixed-frame and O(N²) derivation blockers. It adds one strict
nonmaterializing status for frames containing both indexed rejection and indexed source conflict,
moves new outer outcome IDs to v2 while retaining parser-only v1, and provides a sealed one-pass
batch/acceptance verification boundary with O(1) per-leaf access to the existing committed-state and
upstream-source values. No runtime integration is activated; schema v2 and `is_gap` stay active and
the v3 envelope stays dormant until later phases.
Phase 1A-3B1C-1E closes the remaining performance gate inside that same pure bulk boundary without
changing its API, canonical bytes, identities, versions or semantics. One call-local verification
transcript eliminates repeated nested parsing while retaining complete verification of every
retained field. The pinned 1,024-target medians for initialization, first degradation and full
no-op are respectively
0.283136466, 0.569355146 and 0.707791138 seconds, all below the reserved 0.8-second pure-contract
budget; maxima remain below 1.0 second and 512-to-1,024 ratios remain below 2.5. These are
host/fixture-bound `from_commit` measurements. The remaining 0.2 seconds is only a future runtime
allocation: the current serial 1,024-leaf ordinary upstream-evidence route measured 18.257364
seconds diagnostically and remains a hard 3B1C-2 blocker outside 1E's code scope. Phase 3B1C-2 must
optimize or separately close that route and still prove the entire synchronous runtime path within
its 1.0-second median gate. This phase activates no runtime or delivery behavior.

Phase 1A-3B1C-1F closes the retained fan-out trust boundary and introduces one fused pure
Bronze-to-Silver preparation route. New fan-out writers emit v3 proofs that retain the complete
typed target catalog (and therefore its plan), while v1/v2 remain parser-only. Compact v2
state-reference, committed-state and upstream-evidence identities break recursive JSON-in-JSON
growth without dropping any typed parent or stored verification. The fused factory supports only
exact matching `ALL_POSSIBLY_ACTIVE` Bronze/Silver slices, verifies shared parents and the upstream
commit once, pairs every leaf linearly and returns one complete Silver mutation batch without
fabricating acceptance. Series 1, 2 and the post-review Series 4 remain failure evidence; Series 6
and Series 9 also remain failure evidence because their fused full-no-op medians were respectively
0.810096071 and 0.803406503 seconds. The final green Series 11 slowest 1,024-target median was
0.793836825 seconds, its maximum sample was 0.831679216 seconds, its worst scale ratio was
2.070689029 and its highest isolated-process RSS was 449,732,608 bytes. Series 10 removes only
duplicate construction-time verification; stored-load verification remains intact. Series 11
follows the public fan-out writer-tag metadata correction without changing parser support or
canonical bytes. The joint
compact-graph boundary accepts the worst-case escaped 1,024-leaf run
width 550 at 67,065,684 bytes and rejects width 551 without a partial result. The pure contract
gates pass, but 3B1C-2 must still integrate the contracts and prove the complete runtime path within
its unchanged 1.0-second median budget. 1F remains pure and dormant.

Phase 1A-3B1C-1G closes the pre-parse raw-rejection knowledge gap as a separate dormant contract.
A definitive raw-sink rejection makes Bronze `CONFIRMED_INCOMPLETE`; when Silver projection
membership is still `UNKNOWN`, exact typed Bronze lineage permits only Silver `UNCERTAIN`.
Proven `INCLUDED` still maps to Silver `CONFIRMED_INCOMPLETE`, while proven `EXCLUDED` causes no
Silver degradation. The existing exact-status upstream route and `EventCoverage` remain strict.
The exact-status factories refuse the pre-parse raw-rejection cause unless a separate typed
`INCLUDED` proof exists, so factory selection cannot bypass the closed table.
One opt-in evidence-v3 relation retains and rederives the complete batch, acceptance, plan,
catalog, session, attempt, raw-record, rejection and positional-commit lineage. Ordinary positional
derivation and fused complete preparation remain byte-equivalent and are both linearly verified
through 1,024 targets; standalone fully reverifying factories remain the non-plan-scale semantic
oracle, while fused preparation is the integrated atomic convenience boundary. Existing versions
and golden IDs are unchanged. The collector does not import this path; v2 and `is_gap` remain
active, market-event v3 remains dormant, and neither 3B1C-2 nor 3B1C-3 is implemented here.

The original Phase 1A target and deliverables below remain historical planning context. They are
not acceptance criteria for the active COURSE-1 slice; the narrower scope and exit gate above
control current work.

Target: useful local output within approximately the first 1-2 weeks of active development.

Deliverables:

- canonical venue/instrument/market-event contracts;
- strict PAPER-only startup gate;
- Hyperliquid public realtime collector;
- one external public reference adapter, initially Binance;
- bounded queues and reconnect/backpressure behavior;
- deterministic fixture capture/replay;
- disposable local ClickHouse;
- paper broker interface and append-only ledger baseline;
- risk-based position-sizing baseline;
- FastAPI health/read endpoints;
- Grafana provisioning as code;
- basic Next.js cockpit shell;
- Python/TypeScript tests and container build smoke tests.

Redis and PostgreSQL are not mandatory for this thin slice. They are introduced when multi-process shared state and durable control-plane requirements justify them.

The multi-venue data-contract spine proceeds in bounded steps:

1. **Phase 1A-3B1A — dormant pure contract spine:** definitions and deterministic tests only;
2. **Phase 1A-3B1B — Bronze raw capture:** separate mandatory bounded acceptance for raw records
   before parsing and normalization outcomes after processing; acceptance is not yet durable
   persistence;
3. **Phase 1A-3B1C-1 — contract closure:** pure coverage initialization/mutation and delivery
   knowledge contracts only;
4. **Phase 1A-3B1C-1A — runtime-binding correction:** pure selected-ACK fan-out and typed
   normalization-outcome sink-failure evidence only;
5. **Phase 1A-3B1C-1B — pre-ACK rejection binding:** pure exact indexed non-ACK route, lineage and
   mixed outcome-sink fan-out contracts only;
6. **Phase 1A-3B1C-1C — compact atomic commitments:** pure bounded mutation, acceptance and
   normalization-lineage commitments for complete 1,024-spec fan-out;
7. **Phase 1A-3B1C-1D — mixed outcome and bulk derivation closure:** pure mixed-indexed-failure
   versioning and one-pass verified committed-state/upstream-evidence derivation;
8. **Phase 1A-3B1C-1E — bulk derivation performance closure:** pure call-local shared verification
   optimization with byte-identical canonical contracts and a reserved 0.8-second contract budget;
9. **Phase 1A-3B1C-1F — self-contained fan-out and fused upstream preparation:** retained typed
   catalog/plan trust boundary, compact recursive coverage identities and one bounded linear
   `ALL_POSSIBLY_ACTIVE` Bronze-to-Silver factory;
10. **Phase 1A-3B1C-1G — typed lossy raw-rejection projection:** pure opt-in pre-parse `UNKNOWN`
   membership relation from definitive Bronze loss to Silver uncertainty;
11. **Phase 1A-3B1C-2 — coverage runtime:** operational immutable coverage state and temporary v2
   `is_gap` projection;
12. **Phase 1A-3B1C-3 — atomic delivery:** one audited composite output-queue item per delivered
   non-empty normalization outcome;
13. **Phase 1A-3B1D — atomic cutover:** both normalizers and the collector move together to the one
   mandatory outer-v3 envelope; outer v2 and `is_gap` are removed without dual writing;
14. **Phase 1A-3B2 — deterministic replay:** digest verification, sealed run manifests and exact
   Bronze-to-Silver lineage.

Phase 1A-3B1B implements no concrete persistence, operational coverage tracker, delivery outcome
or deterministic replay. Later work includes simultaneous Bitvavo Standard/Market Data Pro
comparison, approved advanced feeds, ClickHouse persistence, Grafana/Alloy/OpenTelemetry
forensics and the Bloomberg/EMS-inspired cockpit. None is deployed by this slice; SHADOW/LIVE
remain disabled. Phase 1A remains started, not complete.

## Phase 1B — Host-neutral 24/7 PAPER deployment

Target: only after the COURSE-1 local vertical-slice exit gate.

Deliverables:

- private GHCR image publication;
- digest-pinned Linux/amd64 OCI/Compose deployment on a supported Ubuntu LTS VPS;
- an explicit retain-or-migrate plan for the optional existing TrueNAS profile;
- separate paper datasets and configuration;
- managed ClickHouse Hyperliquid database/users;
- 24/7 public-data collection;
- paper strategy execution;
- Grafana market/system dashboards;
- cockpit access;
- resource limits, health checks and restart policies;
- deployment annotations containing commit and image digest;
- rollback to a previous known-good image;
- soak, reconnect and data-gap tests.

Exit gate:

- the Windows PC can be switched off while the approved PAPER runtime continues collecting, paper trading and monitoring correctly.

## Phase 2 — First alpha engines

Target: begin parallel research as soon as stable contracts and replay exist.

Deliverables:

- spot momentum baseline;
- perpetual momentum baseline;
- basis/carry baseline;
- realistic fee/funding model;
- basic slippage model;
- event-driven backtest framework;
- experiment registry;
- Bitvavo public-data adapter for spot research;
- Kraken public/MCP paper integration when it serves a concrete experiment.

Possible parallel status:

| Strategy | Example status |
|---|---|
| Spot momentum | PAPER |
| Perp momentum | WALK-FORWARD |
| Basis/carry | BACKTEST |
| Relative strength | RESEARCH |
| Order flow | IDEA |

## Phase 3 — Robust validation and live-market paper trading

Approximate active-development window: weeks 3-6 and beyond as evidence accumulates.

Deliverables:

- walk-forward validation;
- untouched OOS evaluation;
- cost stress testing;
- Monte Carlo/bootstrap analysis;
- point-in-time universe handling;
- PAPER mode fed by live market data;
- strategy leaderboard;
- paper-vs-backtest comparison;
- initial portfolio/risk engine;
- why-this-trade records;
- model/strategy health and quarantine logic;
- simulator-versus-observed paper fill analysis.

## Phase 4 — Production-grade execution

Approximate active-development window: weeks 6-12, overlapping with paper validation.

Deliverables:

- Hyperliquid testnet/live execution adapter;
- agent-wallet integration isolated on the runtime host;
- full order state machine;
- partial-fill handling;
- reconciliation;
- restart recovery;
- dead-man protection;
- kill switches;
- transaction-cost analytics;
- stronger observability/tracing;
- separate SHADOW and LIVE deployments;
- protected software/deployment promotion gates;
- disaster-recovery and rollback drills.

Development still occurs on Windows/WSL2. Authenticated runtime testing occurs only in the appropriate isolated approved runtime environment.

## Phase 5 — Advanced alpha and portfolio layer

Only after initial strategies and the data path are stable.

Candidates:

- relative-strength hedging;
- cross-sectional portfolios;
- mean reversion;
- order-flow/microstructure features;
- lead/lag research;
- quarter-hour effects;
- regime classification;
- adaptive strategy allocation;
- optional GPU-assisted model training on the Windows workstation.

## Phase 6 — Shadow and very small live

Calendar validation cannot be compressed merely by coding faster.

Deliverables:

- shadow trading with live feeds;
- testnet where useful;
- simulator-vs-observed fill comparison;
- very-small-capital live account;
- strict risk limits;
- same tested image digest promoted from paper where practical;
- separate runtime secrets and data namespaces;
- staged allocation increases only when evidence remains consistent.

Before material live capital, use a supported stable isolated runtime and repeat soak/recovery tests after material operating-system changes.

## Parallel workstreams

These workstreams can progress simultaneously after shared contracts are agreed:

### A. Development platform and CI

- WSL2/Codex;
- repository bootstrap;
- tests;
- image builds;
- GitHub workflows.

### B. Market data

- adapters;
- schemas;
- normalization;
- data quality;
- ClickHouse ingestion.

### C. Research and strategies

- backtester;
- costs;
- momentum;
- basis/carry;
- validation.

### D. Cockpit and Grafana

- UI shell;
- dashboard provisioning;
- venue/strategy/risk views;
- observability.

### E. Risk and execution

- paper broker;
- sizing;
- limits;
- order state machine;
- later authenticated venue adapters.

### F. Runtime and operations

- Linux/OCI runtime images and durable datasets;
- health/restart behavior;
- backups;
- deployment/rollback;
- MCP-assisted operations.

Cross-cutting architecture changes are coordinated centrally; workstreams may not independently replace shared contracts or core infrastructure.

## Longer-term direction

Potential additions only when justified:

- paid L2/tick history;
- options/on-chain context;
- Rust fast paths for measured latency bottlenecks;
- more advanced portfolio optimization;
- automated research hypothesis generation;
- richer multi-monitor terminal workflows;
- automated but approval-gated runtime deployments.

## Planning interpretation

The goal is not to spend months before seeing results. The local vertical slice comes first, followed by the definitive runtime ADR and an Ubuntu LTS VPS PAPER deployment built from the same repository and CI pipeline. Production hardening and out-of-sample evidence then accumulate in parallel; TrueNAS remains optional.

Time ranges are engineering/research estimates, not guarantees of strategy profitability.
