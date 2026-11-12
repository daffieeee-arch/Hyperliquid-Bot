# Continuous native public-data collection

This host supervisor rolls **fresh bounded campaigns**, keeping the same
`hyperliquid-collector` TrueNAS Custom App and read-only portal. Each campaign
uses the unchanged finite runtime contract: six four-hour chunks by default,
fresh session/run IDs and a new private spool/control pair. Publication,
copy verification and reconnects create real gaps. Continuous service intent
does not mean gapless sockets, lossless capture, research readiness or trading.

The user approved continuous collection with **no automatic market archive
deletion**. Work SSD and HDD raw/publication corpora remain permanently retained.
The only optional data cleanup is the existing finite engine's proven removal
of its own **new** closed spool duplicate after two fully verified copies.
Adopted historical campaigns retain their original cleanup selection. Nothing
purges old failed/partial namespaces or previous operator records.

## Delivery and admission

Merge does not deploy. Obtain independent correctness/security review, actual
ready PR CI, a separate merger and a new trusted-main immutable image. Freeze a
root-owned release tree containing `scripts/`, `deploy/` and `src/` outside the
development checkout, under the project runtime dataset. The host only needs
its existing Python standard library, Docker/Compose, midclt, chronyc, nsenter,
ip and tc; analytical dependencies stay inside the image. Never install pip
packages into the TrueNAS base OS or expose its Docker socket to the UI.

Reserve `/mnt/<runtime-pool>/hyperliquid-runtime/continuous-control` as root0700.
Its settings and state are root-owned and cannot be written by UID1000. The
market/UI controls remain read-only binds of private UID1000 directories.
The supervisor writes a small status projection only into **new** controls;
adoption never modifies the running campaign's controls or existing finite
clock/pressure guard. The old image cannot display the new projection. Its
existing portal remains truthful about its finite campaign until normal closure
and the first new-image campaign. Inspect host `state.json` during adoption.

Prepare a private `settings.json` with schema
`collector-continuous-settings-v1` and exactly the `Settings` fields in
`collector_continuous.py`: absolute release/state/runtime/work/archive roots,
private NAS LAN bind IP, full **new supervisor image** source commit/digest,
host-specific Docker engine image ID, explicit observed ZFS version/ARC min/max,
and the reviewed `adoption` generation. Do not copy a registry configuration ID
onto a containerd engine that exposes the manifest as its image ID.

Also supply `storage_sources`, `storage_guids` and `storage_inodes`, each keyed
by `runtime`, `capture` and `archive`. Sources/GUIDs identify the three actual
project datasets; inode pins identify the existing runtime parent and the exact
work/archive root directories. Per-boot device numbers are read afresh and
jointly bound by the existing network/storage gate. Dataset substitution,
wrong filesystems or changed directories stop admission. Read-only checks recur
during new captures. These are instance settings, never committed NAS values.

The adoption record binds the **existing producer**, independently of the new
supervisor image: its original finite RuntimePlan, four actual host roots,
collector/status CIDs, engine image ID, canonical App config SHA256 and host boot
ID. Preserve its source/image, lanes, counts, duration and prune flag verbatim.
For a running adoption also provide its existing `capacity_guard` with exact
`unit`, project-scoped operator `records` path and `policy_sha256`. Admission
requires active unit/PID, fresh same-boot state and the exact producer CID.
Use the actual stored `app.get_instance` config with `retrieve_config`, not a
local render or assumed template. On the supported TrueNAS27 getter, `config`
is the direct Compose object (`name`, `networks`, `services`, `x-notes`,
`x-portals`). Hash that complete raw object without wrapping or rewriting it.
The `custom_compose_config` wrapper belongs only to the `app.update` input;
wrapped, mixed or unknown getter shapes fail closed. Reserve new generations
create-only; never
reuse a failed or closed campaign ID. No instance addresses, dataset identities,
credentials or historical container IDs belong in Git.

Before changing runtime, run the new host module with `--inspect-only` using
the frozen release's `PYTHONPATH=.../src` and explicit `TRADING_MODE=PAPER`.
It checks source/image/platform, native roots, exact managed App/container
identities and source isolation and returns metadata without writes, locks,
App updates, TC changes or capture. Record this actual readback separately.

Then use the reviewed bootstrap only after the assigned runtime operator GO:

```bash
python3 -B /mnt/<pool>/hyperliquid-runtime/<release>/scripts/truenas_collector_bootstrap.py \
  --settings /mnt/<pool>/hyperliquid-runtime/continuous-control/settings.json \
  --register-postinit
```

Registration uses TrueNAS's supported `initshutdownscript.create` API with a
project-owned **COMMAND / POSTINIT** entry; it starts nothing immediately. It
refuses duplicate/different entries instead of silently replacing one. Inspect
the actual entry in System → Advanced → Init/Shutdown Scripts. Start the same
bootstrap without `--register-postinit` for the first explicitly assigned
runtime start. The transient systemd watchdog unit holds a persistent flock;
there is no package installation, `/etc` unit persistence or unrelated service
change. No automatic restart loop overrides a paused integrity/storage/clock
boundary. Persisted `paused` state requires explicit operator revalidation.

## Host-pressure recovery

The supervisor records every measured capacity stop with its preceding/current
kernel samples, material-pressure streaks, policy identity and host boot. Only a
typed stop whose sole reason is host PSI `memory-pressure` may enter
`pressure-wait`. Automatic recovery requires the exact producer still running,
its controller still `capturing`, and zero cgroup OOM counters. The receipt
retains that pre-stop controller evidence before stopping the owned producer.
Its process/configuration/storage identities must match, and the old partial
namespace remains retained. Already stopped, failed or publishing controllers
cannot be reclassified as transient pressure.

The pressure retry counter is independent of the transport counter. The existing
bounded `max_retries` setting limits each counter, using the same 30/60/120/240/
480-second backoff. The pressure counter resets only after a fully completed and
accepted campaign. Repeated pressure during unfinished campaigns eventually
requires operator revalidation.

While waiting, the supervisor samples fresh capacity every second. All three
startup headroom/PSI checks must pass continuously for at least 60 seconds and
the backoff must have elapsed. A process restart or host boot resets this stable
interval. OOM, low available memory, ZFS throttle/eviction faults, malformed
telemetry, storage, clock, source or identity failures enter the persistent
`paused` state. Missing or unsafe native terminal evidence also pauses automatic
recovery. The receipt sets a fixed 30-minute stable-admission deadline; a restart
cannot extend it. Source/image/storage/clock guards are checked periodically
without renewing the interrupted producer's controls. Automatic terminal proof
must retain the same interrupted chunk and include a native `OPERATOR_STOP`;
completed writers or unknown failures cannot acquire pressure permission.

Before a new capture, the supervisor accepts every already sealed WORK/HDD copy,
records `classified-host-pressure-interruption`, and performs the normal fresh
source/image/storage/clock/network admission. A new campaign receives new raw
and control roots. Recovery never continues an interrupted raw namespace. The
portal's `pressure-wait` state and pressure retry count describe recovery in
progress while the producer is stopped.

Host commands retain bounded watchdog heartbeats for their explicit command
timeout. Cleanup may therefore finish its existing 90-second stop grace. This
changes process liveness only. Active capture still refuses a capacity sample
gap longer than five seconds. The baseline is reset only after the old producer
has been independently verified stopped; OOM/throttle/eviction counter changes
are compared with the immutable incident sample across stopped setup and
same-boot supervisor restarts. Explicit legacy recovery records a fresh
pre-stop counter baseline. Only a fully verified actual host boot change may
persist a new boot-scoped counter epoch; restarting the supervisor cannot
rebase counters. Slow stopped audit/setup therefore does not
pretend to be a continuous capture interval. The final fresh admission runs
after setup, and App creation is polled with ongoing capacity checks. Bootstrap journal allowance is
100 messages/minute so a Python traceback's final cause remains visible.

### One-time migration of an old pressure pause

Legacy releases saved only a reason string. They cannot automatically create a
measured pressure receipt. The assigned runtime operator first preserves the old
root-owned settings/state and inspects the stopped App, original source/image,
containers, native partial metadata and both publication stores. Compute the
expected state pin with `collector_continuous.digest` on the parsed original
state. This is the canonical object digest, distinct from a raw-file checksum.

After independent review, the following first-start flags explicitly revalidate
that exact legacy state under the new frozen release.

```bash
python3 -B /mnt/<pool>/hyperliquid-runtime/<release>/scripts/truenas_collector_bootstrap.py \
  --settings /mnt/<pool>/hyperliquid-runtime/continuous-control/settings.json \
  --recover-pressure --expected-state-sha256 <canonical-state-sha256>
```

The operation requires the old phase/reason, an exact pinned state, full App
configuration and immutable producer contract. New CIDs may be rebound only
after an actual host boot change and those checks. Same-boot CIDs remain pinned.
The receipt retains the original state
and records explicit operator authority. The new supervisor image/source remains
independent of the old interrupted producer.

If the watchdog interrupted an unsealed writer before its terminal health was
written, the operator may additionally use `--abandon-unsealed-pressure`.
This is a separate explicit abandonment decision. It retains the old namespace
as unqualified, records each lane's claim/live/terminal metadata identity and
document hash, and records missing terminals. Known native failure or integrity
evidence still refuses migration. Every already sealed copy must pass the
existing complete inventory/hash audit. Changed quarantine metadata fails the
recovery. The old partial data earns no research-ready or completeness claim.

Before applying the one-time operation, run this read-only preflight with the
same frozen settings and exact original state pin. It verifies identities on an
in-memory copy, without saving state, taking the supervisor lock, stopping
containers or updating the App. Add `--abandon-unsealed-pressure` here as well
when reviewing that explicit quarantine decision. Stable capacity and sealed
copy acceptance remain mandatory during the subsequent actual recovery.

```bash
env TRADING_MODE=PAPER PYTHONPATH=/mnt/<pool>/hyperliquid-runtime/<release>/src \
  python3 -B -m hyperliquid_bot.collector_continuous \
  --settings /mnt/<pool>/hyperliquid-runtime/continuous-control/settings.json \
  --inspect-pressure-recovery --expected-state-sha256 <canonical-state-sha256>
```

A wrong expected state pin fails before any state/control write or owned cleanup.

These flags apply only to the first start. They are excluded from ExecStopPost
cleanup and from the registered POSTINIT command. Subsequent restarts need a
valid pending receipt and fresh stable capacity evidence. Ordinary paused
states continue to require explicit operator review.

Normal SIGTERM/shutdown stores `stopped` while retaining continuous intent.
It refuses an unattended same-boot restart, but the next actual host boot audits
the interrupted producer and admits a fresh namespace. To pause permanently,
stop the unit and invoke the host module with `--pause`; this explicit pause
survives reboot, as do integrity/storage/clock pauses.

## Lifecycle and limits

1. Adopt the exact existing managed campaign; do not update/restart its App
   while it runs. Its current finite pressure/clock guards remain responsible.
2. At normal exit require exit0/native COMPLETED and audit **every** chunk/lane
   in both destinations using the new pinned image with no network and readonly
   binds. Verify linked publication seals, inventories, every file byte/hash,
   original prelaunch-plan hash and producer provenance. Resolve the destination
   native catalog with metadata-only row counting; do not inspect prices/OOS.
3. Journal the publication/reconnect gap. Revalidate storage, immutable image,
   clock and the actual host memory policy. New roots are exclusive private
   siblings. Render the frozen Compose template, normalize only the known omitted
   `bind.create_host_path:false` case and integer portal, and use `app.update`.
4. The new market container waits for fresh root-produced clock/storage/TC
   admission before opening any venue client. The existing network helper affects
   only that exact container's veth/eth0, never the host NIC or another App.
5. Continue with new identities after verified normal completion. Native venue
   collectors already reconnect internally. Outer retries require explicit known
   transport exception metadata, zero integrity events, successful acceptance of
   all already published chunks, and bounded backoff (30/60/120/240/480 seconds
   by default). Unknown/writer/integrity failures remain paused, not retried.

After an actual host boot change, rebind only the same canonical App config and
fully checked managed source/image/mount identities. Stop the old interrupted
session if TrueNAS recreated it, accept every already sealed copy, retain all
partial data, journal the interruption and admit a fresh namespace. No old run
is resumed. A changed App, OOM, incomplete publication or unknown identity stays
paused for review. POSTINIT queues a nonblocking unit requiring the existing
Docker/middlewared services. A bounded180-second read-only readiness wait handles
cold service startup within a300-second startup budget. Incorrect source/image
proof is not a transient failure. Actual reboot acceptance is separate.
Known terminal integrity/writer/controller failures observed before reboot
remain paused; a reboot cannot relabel them as a harmless connection gap.

The market container keeps its native2CPU/2GiB/no-swap/UID1000/ROroot limits;
the UI remains0.25CPU/256MiB/ROroot. The offline audit uses1CPU/1GiB only after
capture closes. ARC admission uses three fresh samples: direct available≥2GiB,
effective≥5GiB, credit≤3GiB from one quarter of non-ghost evictable DATA above
the explicitly pinned ARC floor. Startup PSI is strictly below full1%/some5%,
with only the kernel's one-microsecond cumulative-export rounding allowance.
Runtime memory pressure, OOM/throttle, telemetry gaps or changed module/bounds
stop the owned capture. This is an engineering policy, not reserved memory.

Admission needs4GiB free per backing store; at2GiB the supervisor stops and
records a pause. Runtime quota is also checked through actual available bytes.
The existing finite8GiB sampled spool stop remains unchanged. No archive space
is reclaimed automatically. Growth depends on market activity and added lanes;
previous four-lane measurements are capacity estimates, not guaranteed bounds.
The readonly dashboard exposes actual store availability and the supervisor's
phase/heartbeat/retry count/last gap separately from feed freshness. Missing or
stale supervisor evidence never becomes a green continuous claim.

`state.json` is bounded and atomic; one intent/closure/gap metadata receipt per
new campaign is retained. Child output uses bounded own scratch files; journal
errors are rate limited. Market archives and historical logs are not log-rotated.
Stop only `hyperliquid-continuous-supervisor.service`; its reviewed cleanup
verifies exact owned CIDs and stops its new collector, retaining data/UI. It
never stops the adopted original campaign merely because this daemon exits.

Offline lifecycle/fault tests are source evidence, not an actual reboot or
automatic first-handoff claim. Record actual supported POSTINIT registration,
live lease/watchdog state, unchanged adopted CIDs, first new campaign admission,
two-copy acceptance and a later handoff before declaring deployment proven.
Existing preregistered VM timers, holdouts and research code pins stay untouched.

Official references: [TrueNAS27 initshutdownscript.create](https://api.truenas.com/v27.0/api_methods_initshutdownscript.create.html),
[TrueNAS27 Init/Shutdown scripts](https://www.truenas.com/docs/scale/27/systemsettings/advanced/manageinitshutdown/),
[TrueNAS27 app.update](https://api.truenas.com/v27.0/api_methods_app.update.html).
