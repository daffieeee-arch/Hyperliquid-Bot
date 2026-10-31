# Native TrueNAS public collector App

This is a separate explicitly selected runtime profile for the credentialless
collector, not an automatic conversion of the Ubuntu VM runtime. Issue #198
tracks the source, independent review, CI image and actual native pilot evidence.
The existing [VM/NFS profile](public-collector-runtime.md) and its source-pinned
captures retain their existing four-CPU/eight-GiB admission requirements.

## Boundary and storage

Install the rendered `deploy/truenas-collector.compose.yml` as a TrueNAS Custom
App named `hyperliquid-collector`. Use the supported TrueNAS Apps mechanism;
plain Docker Compose on the VM will not create an entry in TrueNAS Apps.
Source edits, Git credentials, signing keys, the Docker socket and host-network
mode are absent from the collector and status containers. PAPER remains the
only accepted trading mode and there is no authenticated exchange execution.

Select `--storage-profile truenas-zfs` explicitly. The default remains `vm-nfs`;
it still rejects direct ZFS roots. All native roots must be private mode0700,
UID/GID 1000 directories without symlink ancestors. An existing named NAS account
for UID 1000 is not a prerequisite for a numeric container UID; verify actual
write access in an isolated operator probe before starting a capture. Do not
change existing dataset ownership or recursively edit ACLs.

The scoped host roots are below `/mnt/<verified-pool>/`:

| Role | Native host suffix | Canonical container destination |
|---|---|---|
| Bounded spool | `hyperliquid-runtime/spool` | `/home/chupa/hyperliquid-spool` |
| Host controls | `hyperliquid-runtime/control` | `/control` (read-only) |
| Work capture | `hyperliquid-work/market-data/capture` | `/mnt/hyperliquid-work/market-data/capture` |
| Closed archive | `hyperliquid-archive/market-data` | `/mnt/hyperliquid-archive/market-data` |

Keep the spool and controls on a new bounded project dataset outside managed
`.ix-apps`; do not migrate the global Apps pool. Work and archive remain on the
verified work NVMe and HDD datasets. The ten-GiB spool budget remains a sampled
eight-GiB stop plus reserve, not a hard quota; a separately recorded new-dataset
quota may additionally bound the project runtime namespace. Neither quota nor
container limit reserves capacity shared with other Apps, the VM or ZFS ARC.

Canonical destinations deliberately match the VM's NAS mount aliases. Native
claims keep their original container spool paths; publication manifests bind
their relocation. The work catalog is regenerated inside the reviewed image.
Before acceptance, actually query its published metadata through both the
native container bind layout and the VM's existing NAS aliases. A successful
copy or matching path spelling alone does not prove catalog portability.

## Independent native admission

The new collector profile is two CPU cores, two GiB RAM without additional swap,
128 PIDs, a 512-MiB tmpfs and a read-only root, with all capabilities dropped.
It is a proposed bounded pilot budget, not capacity certification. Check fresh
host memory/pressure and shared workloads; measure capture and publication peaks
before admitting a longer campaign. Do not alter unrelated Solana/media/AI Apps.

The trusted host operator runs the reviewed clock exporter from a frozen
`src/` release tree; it only queries `chronyc -n tracking`. The sample freshness
and offset/uncertainty bounds remain unchanged. Budget exporter wall time for
startup, publication, wind-down and headroom, up to 96 hours; capture stays at
most 72 hours. No boot watcher, automatic retry or unattended campaign creation is
installed by the Compose template. A TrueNAS App restart does not independently
admit a new market capture: stale controls, namespace reuse or missing evidence
fail closed.

Run the reviewed `scripts/collector_network_gate.py` as the assigned host
operator with explicit `--profile truenas-zfs`, `--container` equal to the actual
`ix-hyperliquid-collector-collector-1` managed collector, and all four
`--spool-host`, `--capture-host`, `--archive-host`, `--control-host` arguments.
Retain scripts and their sibling `src/` modules in the same frozen release.
The gate checks managed project/service labels, exact source/image, resources,
isolation, four bind sources/permissions and actual network namespace. It then
binds each approved host ZFS source/device/inode to the effective container
descriptor mount. Both sides must refer to the same dataset and directory.

Only that container's veth and eth0 receive the existing shared 100 Mbit/s download
and 5 Mbit/s upload policy. The host NIC, bridge, other Apps and NFS shares are
untouched. The collector creates no feed client until it verifies the resulting
root-produced campaign/source/image/netns marker plus complete native storage
bindings and fresh clock evidence. Missing evidence times out after 120 seconds.
Readonly control mounts keep market/UI processes from minting their own proof.

## Pilot and operator evidence

The template starts exactly one 600-second HL+Binance pilot, no spool pruning or
restart. Close and independently accept the existing VM campaign before this
new pilot; retain all originals and controls. Record the new campaign identity,
image/source, rendered App configuration, dataset identities/free space, probes,
host clock and resource/network gates before starting.

Check actual feed timestamps, written parts and gaps independently of process
health. On success verify both destination COMPLETE seals, complete file hashes,
native terminal health, copied prelaunch plans, regenerated work catalog and
resource/clock/TC measurements. No pilot asserts seamless 72-hour coverage,
untouched OOS, funding settlement qualification or profitability. Longer native
campaigns require separate measured admission and fresh create-only identities;
do not silently change a preregistered research period or reuse stopped runs.

Stop only this App and its explicitly identified clock unit if necessary.
Rollback retains incomplete spool and already published datasets; it does not
delete Apps volumes, datasets, snapshots, original research data or another App.

References: [TrueNAS Custom Apps](https://apps.truenas.com/managing-apps/installing-custom-apps/)
and [TrueNAS27 app.create](https://api.truenas.com/v27.0/api_methods_app.create.html).
