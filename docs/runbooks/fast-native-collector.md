# Separate native FAST-5 collector

Issue [#252](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/252)
adds a source/profile/bootstrap path. Merge does not deploy, publish an image,
create a dataset, start a pilot or admit continuous operation. The existing
four-feed App and its running source/image remain untouched. This is public
research recording, with `research_ready:false`; cadence, executable liquidity,
financial fills and profitability require separate evidence. Earlier failed
studies retain their original identities and dispositions.

## Fixed identities and preservation

| Contract | Existing default | Explicit FAST instance |
| --- | --- | --- |
| Settings/CLI `instance` | omitted or `standard` | `hyperliquid-fast` |
| TrueNAS App | `hyperliquid-collector` | `hyperliquid-fast-collector` |
| Compose project | `ix-hyperliquid-collector` | `ix-hyperliquid-fast-collector` |
| Template | `truenas-collector.compose.yml` | `truenas-fast-collector.compose.yml` |
| Campaign prefix | `hl-cont-` | `hl-fast-` |
| Lanes | existing four lanes | Hyperliquid BTC only |
| Native source | existing request/claim unchanged | `--l2-fast`, FAST-5, per-session ACK |
| Portal | 18790 | literal integer 18791 |
| Host unit | `hyperliquid-continuous-supervisor.service` | `hyperliquid-fast-continuous-supervisor.service` |

FAST fixes the new runtime parent to `/mnt/fastdisk/hyperliquid-runtime` and
LAN bind address to `192.168.1.234`. Its fresh `spool-hl-fast-...` and
`control-hl-fast-...` are matching direct siblings. Its root-owned
`continuous-control` state/settings/lock, frozen release, POSTINIT identity and
admission evidence live under that separate runtime parent. The proposed new
SSD dataset and separately reviewed quota must exist before runtime admission;
this source does not create either. Reusing the old runtime dataset would let
two ten-GiB spool budgets compete for its twenty-GiB quota and is refused.

Reuse the independently verified WORK SSD and HDD archive roots with globally
fresh create-only `hl-fast-.../hyperliquid/<run-id>` namespaces. Preserve original
market archives, incomplete publications, controls and failed experiments.
Never copy main's state, CIDs, App configuration, gate receipt, network namespace,
source claim or publication as FAST evidence. Every producer receives a new
container-only TC marker with its own instance/campaign/source/image/netns and
four actual storage identities. Existing storage/clock/pressure/OOM checks remain
mandatory. Unknown profiles and cross-instance adoption/cleanup are refused.

## First creation and later qualification

The assigned runtime operator performs these phases only after independent code,
security and quant reviews, current ready-PR CI, and a separate expected-head
merge. No command below authorizes runtime operations in this source task.

1. Publish an immutable reviewed trusted-main Linux/amd64 image separately. Pin
   source SHA, registry manifest digest and actual host engine ID. Freeze the
   reviewed release outside mutable development trees. Recheck App/port absence,
   existing main App config/CIDs/start times/feed freshness, store GUIDs/inodes,
   quota/free space, clock and three fresh unchanged capacity samples. Nominal
   NAS RAM or the Ubuntu VM's observed 24-GiB/12-core allocation is not admission.
2. Prepare private root-owned settings using the existing `Settings` schema,
   exact observed storage/capacity pins, `instance:"hyperliquid-fast"`,
   `first_create:true`, `adoption:{}`, `chunk_seconds:600`, `chunks:1` and the
   separate roots above. Missing/wrong release, storage, clock or network proof
   fails closed. Retain the two-GiB collector / 256-MiB status limits, CPU/PID/
   tmpfs/no-swap/isolation contracts and unchanged bounded clean-ARC policy.
3. Run the frozen supervisor's existing `--inspect-only` mode first. For fresh
   creation it checks release and FAST App/project absence without adopting any
   producer, creating a lock, writing state, changing TC or launching capture.
   This absence check is not capacity or pilot acceptance. Start the FAST
   bootstrap only when separately assigned. Before a privileged create, the
   supervisor rechecks exact absence, creates fresh paths, records clock and
   capacity admission and persists intent. It calls only FAST `app.create` with
   `custom_app:true` and a structured fixed-template config, never main's
   `app.update`. A name collision refuses creation; it never falls back to update.
4. The waiting new container starts no feed before its fresh clock/storage/TC
   proof. Accept the actual 600-second pilot independently: source/config hash,
   FAST subscription and ACK in every session, <=5 ordered book levels, exact
   raw byte/hash/clock preservation, gaps/reconnects/integrity, real cadence and
   event-age distribution, byte/part rates, CPU/throttling, memory peaks including
   publication, disk/network and simultaneous main-feed freshness. Require native
   COMPLETED health and both hashed COMPLETE publications with working catalog
   queries through native binds and VM aliases. Preserve evidence on failure.
5. First-create settings stop at a persistent `paused` state after pilot closure;
   the pilot cannot promote itself. After independent acceptance, separately
   authorize replacement settings with `first_create:false`, explicit reviewed
   7200-second chunks and a fresh campaign. Retain the existing owned FAST state;
   do not re-adopt main or rewrite paused state ad hoc. The existing explicit
   `--accept-fast-pilot --expected-state-sha256 <actual-state-hash>` operation
   rechecks the stopped owned producer, completed FAST controller and both
   publication audits. Its durable v2 receipt pins the persisted owned pilot's
   plan, roots, source/image, original CIDs/App config and complete two-store
   manifest evidence. Successor generations retain that receipt's hash. Changing
   `first_create` does not grant admission: ordinary startup, launch and recovery
   require the validated persisted receipt, and FAST adoption is refused.
   A process/host death during acceptance stays paused even if the audit wrote
   `auditing` or the receipt reached disk before the final state save. Retry the
   same explicit acceptance with the newly observed state hash after checking the
   owned stopped pilot. A valid orphan receipt is recovered without rewriting it;
   missing receipt work is re-audited. Repeated explicit acceptance is idempotent.
   Acceptance moves to `between-campaigns` and starts nothing; invoke the
   bootstrap separately after acceptance. Qualify a full
   two-hour publication and rollover, then startup/stop/restart ownership and
   aggregate pressure before continuous adoption. Register only the FAST
   POSTINIT identity after those gates; the old unit stays unchanged.

The create API contract follows
[TrueNAS27 app.create](https://api.truenas.com/v27.0/api_methods_app.create.html):
`app_name`, `custom_app:true`, `custom_compose_config`. Host acceptance must still
verify the real middleware job and stored rendered configuration. A failed create
or interrupted admission pauses, retains its namespace and may stop only a fully
verified newly owned FAST CID. A restart never resumes the interrupted raw run.
Rollback stops only FAST's owned collector/unit, retains its status service and
all partial/published data, and never deletes datasets or modifies main's App.

## Memory and observability contract

FAST transport is explicitly 1 MiB per application message, frame queue high-water
16, compression disabled; its distinct transport version and values are included
in the native claim/config identity. Legacy transport/claim defaults remain
8 MiB/1024. Compression is disabled only for FAST to avoid compressed expansion
multiplying a receive callback. Oversize messages close visibly (1009); transport
interruptions record gaps, and no queued payload is silently dropped by policy.

`max_queue` is a frame high-water mark with backpressure, **not a hard sixteen-MiB
allocation limit**. One already delivered callback can overshoot it, and fragmented
frames, Python objects, TCP buffers and parser/string copies add overhead. Tests
exercise a seventeen-MiB callback and exact drainage, message-bound fragmented
input, a legitimate 120-KiB trade burst and slow-writer backpressure. The existing
writer admits two segments including the in-flight segment and blocks further
append. Each rotated payload segment is less than seventeen MiB including
one-record overshoot, so admitted segment payload is below thirty-four MiB; an
awaiting caller can retain another one-MiB data record. Object/native overhead
is additional. FAST's maximum application data record is one MiB. CSV hex staging may
expand a record by approximately four times on disk and sets a four-line DuckDB
reader buffer. Publication streams one-MiB blocks but retains bounded file/
manifest inventories (50,000 source files), and catalog/Parquet native allocations
also contribute. None of these nominal sums is a proof of worst-case RSS.

The offline synthetic forty-message maximum-message capture/writer/catalog/report
measured a 297,086,976-byte peak (283.32 MiB); a fresh publication process copying
both destinations with catalog regeneration measured 74,186,752 bytes (70.75 MiB).
These are local synthetic process RSS observations, not NAS/cgroup or adversarial
maximum proofs. They preserve large observed safety headroom
under the retained two-GiB cgroup hard cap. Real shared-host cgroup peaks, OOM/
pressure/eviction counters and main-app freshness remain pilot/rollover gates;
these source fixtures do not certify aggregate runtime capacity. The hard cap
and unchanged stop guards bound the failure; a failed gate retains evidence.
No one-GiB profile or global ARC/VM tuning is introduced.

Read-only HTTP projects only bounded metadata: requested FAST-5 identity, current
session ACK, feed activity, writer pending/parts, native gap/reconnect/integrity
and byte/message counters, supervisor retry/gap state, sampled collector current/
peak/limit memory and both publication seals. Stale/missing evidence is unknown
or degraded. Cadence percentiles and exact event age are explicitly unknown until
measured independently. Seal presence is not a full-file audit. HTTP never scans
raw Parquet, runs Docker/TC, signs, deletes data or changes control state.

Existing Python CI covers these modules and deterministic fixtures. No additional
workflow, database, broker, trading mode, key, capital or retention change is
required. Release, pilot and continuous acceptance evidence belong to their
separately assigned runtime operations.

## Paused before first App creation (#258)

The first actual bootstrap on source `20de621` stopped before `app.create`:
`launch()` asked the stopped-producer baseline to verify an App that did not
exist, and middleware returned ENOENT. No FAST producer, TC gate, pilot or
publication existed. The old four-feed App remained unchanged. This failure is
not classified as memory pressure and must not enter pressure recovery.

First creation now measures three fresh host-only capacity samples while the
exact FAST App/project remains absent. It keeps the same memory/ARC/PSI/OOM
rules and hard-counter comparison with prior samples. Existing producers still
require their actual stopped, owned, non-OOM container proof before a successor;
continuous admission still requires durable pilot acceptance. No absent producer
is invented as evidence. The finite source/image and TC/storage/clock gates
remain mandatory.

A later separately assigned operator may revalidate only this retained paused,
zero-generation, no-current FAST failure. Ordinary restart continues to refuse
paused state. Use a **new reviewed frozen release and trusted CI image**, with
new explicit source, registry digest, host engine ID and settings pins. Keep the
old `20de621` release, image, original settings, journals and preparatory
spool/control directories intact. Never patch either the old release or a
container. Revalidation does not upgrade files or reset systemd itself.

Before any operation, record the actual root-owned failed state and calculate
its **canonical object digest** using the reviewed `collector_continuous.digest`.
A raw `sha256sum state.json` is a different pin. Preserve both digests. Prepare a
new root-owned settings file pointing to the new frozen release/image; pin that
file's canonical object digest too. Verify the exact original failed campaign
from its retained operator evidence, rather than guessing a directory name.

Invoke the new frozen module explicitly with PAPER and its frozen `PYTHONPATH`:

```text
python3 -B -m hyperliquid_bot.collector_continuous \
  --settings <new-root-owned-settings.json> \
  --revalidate-fast-precreate \
  --expected-state-sha256 <canonical-failed-state-digest> \
  --expected-settings-sha256 <canonical-new-settings-digest> \
  --failed-campaign <exact-retained-failed-fast-campaign>
```

This root operation requires the exact FAST profile, first-create pilot settings,
zero generations/current, the known precreate failure class, absent App/project,
verified frozen source/image, all store GUID/inode pins, private orphan spool
(empty) and control (`clock.json` only), storage reserves, a new synchronized
host-clock measurement and three fresh capacity samples. The orphan clock is
preserved evidence; it does not supply current clock admission. Stale pins,
foreign state, any existing App/producer/project or unexpected orphan data refuse.

It writes a create-only root-owned receipt containing the complete failed state,
its original JSON bytes/raw hash, pinned new settings/source/image, retained
orphan identities/clock hash and measured admission. Only after receipt fsync does
it permit a later fresh pilot; it starts no App, capture, TC gate or boot task.
Receipt/state-write interruption leaves the original failure paused, and an
explicit repeat with the same original pin preserves the existing receipt.
A later start verifies the receipt binding and allocates a new namespace. It
never resumes, deletes or overwrites the failed roots.

The original **`hyperliquid-fast-continuous-supervisor.service`** transient unit
also needs separate operator revalidation. Preserve its journal and exact
ExecStart/source/settings, verify its MainPID is zero and its state/result match
the retained failed attempt, and independently reconfirm the FAST App/project is
absent. Only a concrete newly assigned runtime plan may clear that exact dead
failed unit with `systemctl reset-failed hyperliquid-fast-continuous-supervisor.service`
and confirm `LoadState=not-found` before the new bootstrap. If it stays loaded,
stop and review that exact transient unit; do not restart its old ExecStart or
use a global reset/daemon change. The bootstrap continues to refuse loaded units.
Never reset, restart or modify `hyperliquid-continuous-supervisor.service`.

The new bootstrap and fresh 600s pilot require another explicit runtime GO after
review, current CI, separate merge, immutable image publication and actual
admission. This source task performs none of those operations. #252/#244/#246
remain open for actual source/cadence/ACK, two-store publication and continuity
qualification. Market archives and failed-study identities remain preserved.
