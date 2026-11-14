# Roadmap

## Active priorities — checkpoint 2026-11-14

The active operator objectives are two separate research tracks: **20% net on
initial POSITION MARGIN per large winning trade**, and **1–2% net initial position
margin per frequent small winning trade**. These are objectives, not promised
winners, daily minimums or 20% account/day. Prioritize causal, cost-covering
strategy discovery on existing data, then fresh validation and separately admitted
PAPER. The positive but slow h720 result is preserved and operator-deprioritized;
it is not the current promotion priority. Profitable native Hyperliquid operation
has not been established. The phase history below records the broader platform; it is not
the current task order. The [Program & Readiness board](https://github.com/users/daffieeee-arch/projects/2)
tracks actual delivery evidence and remaining gates.

The bounded memory-policy fix passed independent review and runtime acceptance
([#235](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/235#issuecomment-6850450863)).
Broader collector continuity remains open separately. It does not block the
operator-approved [discovery pilot #237](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/237).
The [first real comparison](research/discovery-batch-two-result.md) is complete:
twelve residual-dispersion/OI-recovery configurations on existing BTC/ETH/SOL
history, with native funding, costs, paired stress and an independent full-ledger
audit. Ten configurations are rejected; two lack development trades and remain
insufficient. No candidate is admitted. The [two-model follow-up](research/two-model-momentum-micro-result.md)
has now run: all eight daily variants have negative total net returns (four
brackets reject, four time exits are collateral-invalid); all eight micro
variants retain execution failures and open exposure. Neither selects a finalist.
The [persistent-regime continuation](research/cost-aware-regime-result.md) has now
actually run and passed two independent financial/statistical audits. The frozen
DEV rule selects `regime-h720-plain`: +16.4718% base / +15.7015% stressed account
return over 974 exposed days, with 20 trades. Its stressed 2025 return is only
+0.003035%; uncertainty includes zero and matched-short collateral is invalid.
This is a positive retrospective candidate, not statistical alpha, fresh OOS or
native Hyperliquid trading admission. Its native qualification [#249](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/249)
is lower priority while its fit with the operator objectives is reassessed.
The same locked candidate's [recent exposed diagnostic](research/locked-regime-recent-result.md)
has also run on 73 completed September–November days: +2.3317% base / +2.2199%
stress, three trades and zero 20%-margin hits. Its November short contributes
most profit, but persistent short performs better; intervals still span zero.
The [frozen micro entry-information diagnostic](research/micro-response-cost-result.md)
has also run and passed full independent native reconstruction. All 200
conditional charged-price cell means are negative; primary gross response is
only +0.45–0.65 bps at base against an approximately 11-bps cost hurdle plus
spread/rounding. These are exposed price observations, not realized trades or
net margin returns. No endpoint or policy is selected; prioritize a different
economic mechanism rather than more pressure-threshold tuning.
The [broader OI recovery study](research/oi-recovery-broader-result.md) has now
run all four existing hypotheses on 974 calendar days and passed independent
full native/accounting reconstruction. One line earns +11.3783% at base but
-3.1890% at stress and loses in 2025; matched long performs better. The other
three lines are negative under both costs. No robust finalist is established;
all outcomes and its original 32/40 configuration budget remain preserved.
The new [hourly persistent-trend study](research/hourly-persistent-trend-result.md)
has now run all four registered 24/72-hour policies, with full independent
native/accounting reconstruction. Only 72h-confirmed is positive at base
(+6.5332%); it loses at stress (-1.6552%), all four lose in 2025 and every
paired interval spans zero. The old daily h720 financial baseline reproduces.
Programme usage is now 36/40. Stop this clock/horizon family as registered;
do not automatically spend the remaining four on retrospective retuning.

The newly registered [compression-breakout study](experiments/exp_compression_breakout_20261114.md)
([#270](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/270)) tests ONE
distinct compression mechanism in the existing price-continuation family,
bringing registered use to **37/40**. Source/tests/protocol are in development;
no new market outcome or strategy admission is claimed. The superseded account-day
oracle #268/#269 remains preserved without financial execution. Native #249 and
residual-intake #267 local WIPs are unreviewed, undeployed and lower priority.

Opt-in FAST source [#246](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/246)
and first-create bug [#258](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/258)
are closed after actual independent native acceptance. The separate FAST app
in [#252](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/252#issuecomment-6866235548)
completed a 600s pilot with all 7,497 raw records and both copies verified.
Its current-boot continuous loop started at 22:13 UTC on November 13 with
fresh data and real Parquet growth. First complete 7200s publication/rollover,
supported restart and separate POSTINIT/reboot gates remain open; continuous
start is not proof of all those gates. The standard four-venue collector
remains unchanged. Nautilus/testnet preparation supports this work. TESTNET
orders remain unapproved.

| Order | Work | Current evidence and next gate |
| --- | --- | --- |
| 1 | Test one causal large-move hypothesis on existing history | [Compression breakout](experiments/exp_compression_breakout_20261114.md) / [#270](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/270): one registered configuration, 37/40, targeting 20% net initial position margin per large winner. Source/tests/protocol first; independent CODE/security + QUANT, CI and separate merge precede financial labels. Preserve all old results and the hourly-family stop. The separate frequent 1–2% margin track remains research; no strategy is certified. Existing collection/FAST gates remain unchanged. |
| 2 | Maintain collection after the first verified full campaign | Reviewed source `df0bf41` completed one real 7200-second campaign: all four lanes reported COMPLETED with zero integrity events, and an independent full-file audit accepted all eight WORK/HDD publications. Automatic handoff produced a fresh campaign with all four feeds fresh and growing. Publication/replacement caused several minutes of downtime; Kraken recorded one transport gap and `research_ready` remains false. See the [bounded runtime acceptance](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/223#issuecomment-6842833281). Broader continuity and untested physical reboot recovery remain [#205](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/205). |
| 3 | Preserve the original BTC study's insufficient-input disposition | Independent full-file/hash and metadata admission found only three of twelve six-hour chunks per venue on both stores. The original Nov2–5 input attempt is closed as **INSUFFICIENT_DATA / NOT_TESTED**, with no OOS publication or financial scoring. A local partial remains retained and unqualified. Nov6 funding separately passes timing metadata admission for 120 native records / 74 padded slots; this cannot repair missing market data. See the [input disposition](research/btc-spot-flow-input-disposition.md) and [#192](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/192#issuecomment-6839899958). |
| 4 | Use the corrected accounting as bounded research evidence | [PR225](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/225) delivered one fixed retrospective January 2025 reconstruction. Independent native-data/Decimal verification accepted +0.1898547% at base assumed costs and +0.1056243% at doubled costs, from **six trades** on 39 surviving symbols excluding ICP. Both NAS result packages passed independent copy admission. This does not restore the original primary rule or establish a Hyperliquid edge. See the [result and limits](research/january-volume-repair-result.md) and [#222](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/222). |
| 5 | Admit only evidence-supported candidates through their own gates | No current strategy is admitted. H720 remains a preserved positive retrospective result with fragile stress and low frequency, operator-deprioritized. The new compression hypothesis must publish all costs, conditional controls, margin outcomes, actual account growth/frequency and risk-invalid curves before any fresh/native validation or PAPER admission. No oracle, recorder, research result or merge enables trading. |
| 6 | Expand data where it supports a defined experiment | Coinbase's 600s public pilot and both stored copies passed independent acceptance; continuous Coinbase is still open. Bitvavo MD Pro access/entitlement remains unverified; Pro is L2, not L3. These additions do not block the existing HL/Binance hypothesis. |
| 7 | Complete execution and platform gates as needed | PAPER strategy integration, venue-authoritative recovery/reconciliation [#178](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/178), trading observability and backup/recovery proof remain separate work. TESTNET/SHADOW/LIVE are not enabled by data collection or a research result. |

The development-dependency security item
[#187](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/187) was addressed
with the scoped minimatch override recorded in [Security](SECURITY.md).
The separate S4 multi-asset study remains blocked on its frozen funding-source
contract ([#190](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/190),
[#150](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/150)); it does not
block other admitted research. ICP remains excluded from the operator's selected universe.

The earlier `e3990b9` campaign stopped after about 66 minutes at a measured host
PSI boundary. Its last observed cgroup peak was about 251 MiB of 2 GiB with no
recorded OOM; the last full-stall interval was 1.164% against the configured 1% bound,
while direct host available memory was about 3.22 GiB. Binance's missing
terminal document made automatic recovery pause. That incomplete campaign did
not earn publication/handoff proof. Reviewed explicit revalidation preserved
its incident, hard counters and partials before the new replacement started.

The replacement runs immutable image
`sha256:55b3aff180e0726bc786e9421e2e1d8d280f127972860ab715cabeb7cbbbd14c`.
Its first producer started Nov13 01:11:59 UTC. The full eight-copy audit checked
10,512 file hashes / 3,101,457,312 bytes, complete inventories, linked seals,
source/native provenance and regenerated WORK catalog row counts. No validator
was weakened and no financial/OOS data were decoded. The audit receipt SHA-256
is `f7f3a97eee8d4bcd03d52328bf430e34dfd9286edc35b08119702fc548d4356e`.

The completed 7200-second producer exited normally at 03:16:19 UTC. The automatic
replacement's four feeds were fresh at 03:16:43, with independent growth readbacks at
03:18:10–03:19:34 confirming the same source/image, unit, storage, clock and
network admission. No OOM, restart or quarantine mutation was observed. The
short final handoff journal interval does not measure the full wire-data gap;
publication and replacement interrupted capture for several minutes. The completed
campaign's highest observed kernel cgroup peak was 407.43 MiB of 2 GiB; all
observed memory/PID events were zero, with no recorded OOM or container restart.

The closed publications contain 1,551,274,120 unique regular-file bytes on WORK
and 1,550,175,824 on HDD, counting linked seal names once. Physical ZFS allocation
was not measured. The 1,098,296-byte difference comprises four regenerated WORK
catalogs totaling 1,097,728 bytes plus 568 bytes of additional manifest entries.
Extrapolating this one two-hour window gives about 17.34 GiB/day on WORK and
17.32 GiB/day on HDD, 34.7 GiB/day combined, above the earlier 8 GiB/copy/day budget. This is a
single-window estimate rather than a long-term traffic forecast; review storage
headroom as more windows accumulate, without automatic market-archive deletion.

Native gap counts were HL 0 / Binance 0 / Kraken 1 / Bitvavo 0. Kraken logged a
transport disconnect at 02:14:11 UTC and recovered. A separate 02:38:58 poll
reported Kraken UNKNOWN; its precise cause remains unproven. Full-copy integrity
and one successful handoff do not admit the data for research or establish
lossless coverage or physical reboot recovery. No memory threshold, global ARC
setting, archive retention rule or trading mode changed.

The measured CI audit produced [PR227](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/227):
the unchanged BTC integration fixture runs locally in 2.79s instead of 33.35s,
with all 21,600 rows and the complete Parquet file identical. Required checks
remain in place. [PR228](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/228)
is merged as `7f781e1` after independent correctness/security reviews and 4,107
Python tests. Its complete 5,000-record synthetic write measured 6.88s before
and 0.0947s after, with byte-identical output. Four concurrent writers peaked at
489 MiB inside a 2 GiB DEV cgroup with no memory/PID events. The writer was
subsequently published and deployed within source `df0bf41` together with
[PR233](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/233)'s reviewed
pressure revalidation. The first full campaign and handoff are now accepted
within the bounds above. [PR232](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/232)
also removes five empty heavy runners from each verified draft event, while
ready/main behavior and mandatory checks remain verified; see [CI](CI.md).

The existing archives remain available for qualification and exploratory work;
their volume alone does not establish usable clocks, complete coverage or a fresh
holdout. Do not weaken the fixed BTC input requirements to obtain an earlier result.
Market archives have no automatic deletion policy. The
[continuous collector runbook](runbooks/continuous-native-collector.md) describes
storage guards and verified temporary-spool cleanup separately.

This checkpoint is not a deployment instruction or a promise that future timers,
rollovers or research gates have passed. Consult current evidence on the board.

## Delivery model

Development and runtime responsibilities remain isolated:

- **Ubuntu VM on TrueNAS SCALE (ADR-025):** current source development;
  observed Ubuntu 26.04.1 LTS / KVM, repository under `$HOME/coding-projects/`;
- **TerraPC/WSL2:** secondary operator environment;
- **GitHub Actions:** independent CI and explicit trusted-main collector image
  publication; other service releases remain planned;
- **Host-neutral Linux/amd64 OCI runtime:** target for approved continuous
  PAPER operation. **ADR-024** is the historical Netcup profile. Accepted NAS
  public-data operations do not certify continuous PAPER trading or every
  historical dataset's research suitability.

Fases overlap by workstream. A strategy may be in PAPER while another remains in RESEARCH, and production hardening can continue while live-market paper evidence accumulates.

## Migration and research sequence — retained historical checkpoint

This sequence records the earlier VM migration prerequisites. Use the active
priorities above for the next work; migration and source CI alone never establish
strategy performance or trading-runtime readiness.

1. Restore and verify the pinned development toolchain and existing test baseline.
2. Locate and verify durable captures and historical archives without mutating
   runtime data. Missing local roots are not proof of source-data loss.
3. Apply the independent-review workflow in [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md);
   no paid GitHub/Copilot reviewer is required.
4. Once data is verified, pre-register one new bounded study. The multi-asset
   panel/funding infrastructure is a candidate, not an approved strategy.
5. Connect a validated research candidate to supported PAPER instruments and
   economics before claiming strategy PAPER evidence.

See [migration status](runbooks/ubuntu-vm-migration-status.md). Existing VPS/WSL
capture runbooks below are legacy references, not authorization to start a
capture on the new host.

## COURSE-1 — bounded engineering evidence

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
   for a multi-day retain. DATA-1F Binance retained-capture operator docs are in
   `docs/runbooks/data1f-wsl-pc-retained-capture.md` and
   `docs/runbooks/data1f-vps-retained-capture.md`; do not start that capture until
   the assigned DATA-1A 72h series finishes and CoS assigns the window.
   DATA-1E Bitvavo MD Pro and DATA-1B Kraken retained-capture operator docs are in
   `docs/runbooks/data1e-wsl-pc-retained-capture.md` /
   `docs/runbooks/data1e-vps-retained-capture.md` and
   `docs/runbooks/data1b-wsl-pc-retained-capture.md` /
   `docs/runbooks/data1b-vps-retained-capture.md`; those captures are prepare-only
   and must not start until CoS assigns the window. Cloud Agents are unsuitable
   for a multi-day retain.
6. **Complete (PAPER path):** `hyperliquid_bot.paper_risk` applies documented
   risk-based sizing and fail-closed portfolio/exposure limits on the COURSE-1
   soak and D22-A pre-submit path. The frozen D01 publication smoke-risk
   function is extended, not replaced. A PAPER-only FastAPI `/health` and
   `/ready` baseline exists in `hyperliquid_bot.control_service` (not wired to
   cockpit, collectors, or LIVE).
7. **Complete (historical decision only):** ADR-024 records the Netcup
   Ubuntu LTS VPS/OCI profile. ADR-025 subsequently moves development to the
   Ubuntu VM without approving runtime/data cutover. Deployment and capture
   starts still require their explicit operator gates.

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
boundary. **ADR-024** records the historical Netcup PAPER profile; **ADR-025**
records the current VM development move. Continuous capture/runtime migration
requires fresh evidence and approval.

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

- Codex can modify a feature branch, run tests and open a pull request without
  using live trading credentials.

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
- FastAPI health/read endpoints (PAPER-only `/health` and `/ready` baseline
  now exists; not wired to cockpit, collectors or LIVE);
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
- Kraken public/MCP paper integration when it serves a concrete experiment;
- offline historical warehouse for Vision BTCUSDT + Kraken OHLCVT research SQL;
  its earlier Netcup path was `~/Hyperliquid Project/hist-archives/`, and its
  VM location/transfer is unverified — separate from live retain, not a substitute ([hist-archives-research-warehouse.md](runbooks/hist-archives-research-warehouse.md)).

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

Development occurs primarily on the Ubuntu VM. Authenticated runtime testing occurs
only in the appropriate isolated approved runtime environment.

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

- Ubuntu VM/Cursor/Codex;
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
- first PAPER Operator Cockpit workstation preview (`apps/cockpit`): Tailwind
  v4 + official free shadcn + Lucide + TanStack Table + recharts KPI
  sparklines + Lightweight Charts (Apache-2.0 NOTICE); default **C
  Fail-Closed Amber** with optional Desk Dark; IA Health → Markets →
  Research → PAPER → Risk; Trading DESK must-haves plus Quant P0 (registry,
  capture health, sufficiency, identity, overlap clock; UNAVAILABLE if
  missing). Collectors untouched. CoS Pass before merge;
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

The goal is not to spend months before seeing results. The local vertical slice
came first; **ADR-024** records the historical Ubuntu LTS VPS/OCI PAPER
profile. **ADR-025** records the Ubuntu VM development move, with data/runtime
migration still unverified. Production hardening and out-of-sample evidence
can accumulate after those prerequisites are met.

Time ranges are engineering/research estimates, not guarantees of strategy profitability.
