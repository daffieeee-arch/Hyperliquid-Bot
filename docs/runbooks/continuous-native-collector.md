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

The adoption record binds the **existing producer**, independently of the new
supervisor image: its original finite RuntimePlan, four actual host roots,
collector/status CIDs, engine image ID, canonical App config SHA256 and host boot
ID. Preserve its source/image, lanes, counts, duration and prune flag verbatim.
Use the actual stored `app.get_instance` config with `retrieve_config`, not a
local render or assumed template. Reserve new generations create-only; never
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
