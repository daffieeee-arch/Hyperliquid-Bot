# Bounded public collector runtime

Issue #191 adds Linux/amd64 OCI delivery and an operator procedure. A merge or
green CI does not start collection, certify the VM migration or approve trading.
The first deployment requires independent reviews, current CI, a CI-published
immutable image and recorded host/storage gates. ADR-025 remains the development
migration record; original `vps-chupa` data is untouched.

## Scope and limitations

One nonroot container supervises existing finite native collectors. The supplied
Compose file selects Hyperliquid BTC perpetual and Binance BTC spot + USD-M.
The supervisor also supports explicit Kraken BTC/USD public L2/trades and
Bitvavo Standard BTC/EUR lanes. No key, account token, signer, execution adapter,
service port, source checkout or Docker socket is mounted. PAPER is enforced
before adapter imports; execution configuration and credential environment
names are refused, and children receive a public allowlist.

The initial pilot is **one 600-second chunk**, retaining local duplicates.
After reviewing measured growth/integrity, an operator may explicitly select
7,200–21,600-second chunks and a campaign of at most 72 hours. Do not reconnect
every ten minutes indefinitely: reconnect exclusion windows can consume much
of such short samples. Chunks have distinct native run/session identities,
publication pauses and real gaps. This is not seamless coverage, restart
recovery, queue-position evidence or research readiness. There is no automatic
restart, retry, resume, new campaign or permission for LIVE/SHADOW/TESTNET.

Hyperliquid public L2 snapshots are not order-level data. New Binance open-interest
captures sample immediately, then wait at least 60 seconds after each completed
sample; old one-sample archives remain unchanged and there is no backfill. Liquidation messages
are venue-specific observations, not every liquidation. Bitvavo Standard is
neither MD Pro nor L3; Kraken L3 is excluded. ETH/SOL and actual funding-history
expansion require separate source decisions. Existing UTC receipt/monotonic
clocks remain intact; clock telemetry cannot remove latency/batching or prove
identical exchange timestamp semantics.

## Storage, closure and budgets

All approved roots are owned by UID/GID1000 and mode0700:

| Role | Host and container path |
|---|---|
| Local spool | `/home/chupa/hyperliquid-spool` |
| NAS SSD work capture | `/mnt/hyperliquid-work/market-data/capture` |
| NAS HDD closed archive | `/mnt/hyperliquid-archive/market-data` |
| Host-written controls | `/home/chupa/hyperliquid-runtime` → read-only `/control` |

Runtime verifies the **effective descriptor mount ID**: spool must be local
ext4/XFS/Btrfs, both destinations NFS/NFS4, with at least 2 GiB available in each
at admission. Operators also verify exact export identities/options, free NAS
space and their own create/readback/fsync probe. Bind creation is disabled.
No share, pool, ACL or original archive modifications are implied.

Spool enforcement is an **8 GiB sampled stop with 2 GiB reserve below a 10 GiB
budget**, not a filesystem quota. The monitor sums allocated blocks across all
lanes/existing contents every second. Atomic partial/health renames may cross
one sample. Container limits: 8 GiB memory, no additional swap, four CPU cores,
128 PIDs, 512 MiB tmpfs, read-only root. Stop sends SIGINT to all native children,
then kills stragglers after a shared 30-second grace. Failure retains incomplete
data and stops the campaign. A host crash can lose the native in-memory segment;
no power-loss durability or hard 10 GiB quota is claimed.

Only exit-zero native `COMPLETED`, credentialless chunks with exact run ID and
duration publish into new `<campaign>/<lane>/<run-id>` reservations. Original
claims, health/logs and raw bytes are preserved. **Claims retain their original
spool paths and bytes.** A separate `publication-manifest.json` binds source
identities, original/destination roots, byte counts/SHA256 and image/source.
This relocation layout does not claim universal compatibility with legacy
reconstructable-root validators. Local DuckDB is omitted. The SSD catalog is
regenerated through the reviewed native API against identical host/container
absolute paths; HDD stores closed raw/provenance without a catalog. The offline
fixture queries the regenerated native catalog and verifies destination paths.

Each lane also gets a create-only `collector-plan.json` **before** its native
process is spawned. It binds campaign/chunk/run/lane, venue/product, reviewed
source/image and requested duration to a UTC/monotonic/UTC clock bracket (at
most one second) and fresh host-clock evidence. Adding the duration to the
prelaunch clocks gives a lower bound on an uninterrupted native timer's end,
under the recorded clock model; it is not an actual stop or uptime guarantee.
Early failures and unexpected gaps remain failures.

At successful closure the original planning bytes are checked against their
prelaunch SHA256 and copied exactly into the native run root. Both publications
include those bytes and their hashes. Their per-run provenance uses schema
`public-collector-run-provenance-v1` and binds `window_plan_sha256`, run and lane;
the campaign-level record retains its existing schema. Native claims remain
unchanged. Old captures are never retrofitted. Research consumers need separate
part/run/claim/planning acceptance before using a planned-end entry filter;
terminal health or first/last market events cannot supply a missing prelaunch
time. No strategy or trading behavior is introduced by this record.

All destination bytes are rehashed after copying/catalog creation. Manifest and
private publishing marker are written/fsynced before an exclusive final hard
link publishes `COMPLETE.json`. Failed reservations are retained, never resumed
or overwritten. NAS files are never deleted. Optional later `--prune-spool`
removes only runtime-owned closed **local duplicates**, after both destination
seals, expected file inventories, full hashes and source identities verify.
The pilot leaves cleanup disabled. No active native part is removed.

Health reports phase, source/image, sampled allocation and clock evidence.
Every 30 seconds and at closure, bounded `collector-metrics.ndjson` retains
UTC/monotonic clocks, chrony evidence, combined container byte counters and spool
allocation; these bytes accompany each published lane. Clock stop bounds:
sample age ≤120 seconds, Normal leap status, absolute system offset ≤50 ms,
root dispersion ≤250 ms, root delay 0–500 ms. These engineering bounds do not
claim submillisecond alignment. The exporter only reads `chronyc -n tracking`;
it never adjusts the clock.

Interface counters and the container-veth TC policy cover public socket
traffic in this container. Bind-mounted NFS publication uses the host's NFS
client: its bytes and bandwidth are outside these counters and this TC policy.
Measure NAS publication separately when budgeting host/NAS network capacity.

## Image delivery

`Dockerfile.collector` pins Python3.13.15/uv0.12.5 base digests and frozen
production dependencies. Whitelisted context excludes Git, environments, data
and reports. Existing PR CI builds and runs a network-disabled source/PAPER
import smoke, including LIVE refusal, without publisher credentials. This is
build validation, not feed coverage or deployment.

Manual trusted-main `collector-image.yml` verifies the exact current main SHA
and successful expected-publisher CI evidence, builds/smokes, rechecks main and
publishes a unique source/run/attempt tag to
`ghcr.io/daffieeee-arch/hyperliquid-bot/collector`. It never deploys or publishes
`latest`. Record the resulting **repository@sha256 digest** and OCI revision.
Public repo visibility does not prove a public package: verify an anonymous pull
using a fresh empty Docker configuration. Stop if authentication is required;
never copy existing GitHub credentials to the runtime.

## Explicit first-pilot procedure

Use the clean merged reviewed checkout. Record source, image digest, main CI,
review links, mount identities and storage probes in a new private operator
record before start. Templates below contain only non-secret identities:

```bash
export COLLECTOR_SOURCE_COMMIT='<full reviewed current main SHA>'
export COLLECTOR_IMAGE_DIGEST='sha256:<CI-published digest>'
export COLLECTOR_IMAGE="ghcr.io/daffieeee-arch/hyperliquid-bot/collector@${COLLECTOR_IMAGE_DIGEST}"
export COLLECTOR_CAMPAIGN='<new lowercase campaign>'
docker compose -f deploy/public-collector.compose.yml config --quiet
```

Independently inspect the pulled Linux/amd64 image revision/entrypoint; verify
approved mounts/permissions, available reserve and no concurrent collector.
Preserve previous controls under a new private operator record rather than
silently reusing/removing an old marker. Do not touch another service or data.

Run the bounded host clock exporter independently of the chat, for example
through an explicitly created transient user systemd unit with a unique name,
using reviewed source, UID1000 and `PYTHONPATH=src`:

```bash
uv run --frozen python -B -m hyperliquid_bot.collector_clock \
  --output /home/chupa/hyperliquid-runtime/clock.json --duration-seconds 1200
```

The exporter needs an explicit **wall-time** budget covering startup, the total
requested capture duration, publication pauses between chunks, final publication,
shutdown grace and operator headroom. Twelve six-hour chunks request 72 hours of
collection but take longer in wall time. Choose an exporter duration up to
**96 hours** (345,600 seconds); the capture admission limit stays 72 hours.
Align the transient unit's `RuntimeMaxSec` with that whole exporter budget plus
startup/drain headroom, rather than ending the unit at the capture duration.
For example, a deliberately chosen 96-hour exporter budget needs a unit ceiling
such as `RuntimeMaxSec=96h5m`, not `72h`.

The ceiling does not guarantee completion within 96 hours: publication or other
delays may exhaust the admitted wall-time budget. The operator must admit a
campaign with adequate measured headroom; clock evidence still expires after
120 seconds and the collector fails closed if export stops or becomes stale.
No running exporter or unit is extended by this source change. This PR installs no
boot-enabled service or watcher. Detached Docker collection is independent of
the chat and restart is off.

```bash
sudo -n env COLLECTOR_SOURCE_COMMIT="${COLLECTOR_SOURCE_COMMIT}" \
  COLLECTOR_IMAGE_DIGEST="${COLLECTOR_IMAGE_DIGEST}" \
  COLLECTOR_IMAGE="${COLLECTOR_IMAGE}" COLLECTOR_CAMPAIGN="${COLLECTOR_CAMPAIGN}" \
  docker compose -f deploy/public-collector.compose.yml up -d --pull never
sudo -n python3 scripts/collector_network_gate.py \
  --source "${COLLECTOR_SOURCE_COMMIT}" --image "${COLLECTOR_IMAGE}" \
  --campaign "${COLLECTOR_CAMPAIGN}"
```

The waiting container creates no feed client until the marker appears
(120-second startup timeout). Root-only gate verifies exact image/source,
nonroot, read-only root, dropped caps, no host networking/ports, exact mounts
and resource/restart limits. It discovers this container's veth, verifies peer
identity and uses `tc qdisc add` (never replace) **only** on that host veth and
eth0 inside this container's netns. Host-veth egress limits combined download to
100,000,000 bit/s; container eth0 egress limits combined upload to5,000,000 bit/s,
with bounded TBF bursts262,144/65,536 bytes. This is one shared policy for all
lanes; it never changes the VM/NAS NIC or grants NET_ADMIN inside the container.
Both kernel policies are read back and PID/netns rechecked before atomic
publication of the campaign/source/image/netns-bound marker. Unexpected prior
qdiscs fail closed. Record actual resource/TC counters and private health.

Only this service is stopped:

```bash
sudo -n env COLLECTOR_SOURCE_COMMIT="${COLLECTOR_SOURCE_COMMIT}" \
  COLLECTOR_IMAGE_DIGEST="${COLLECTOR_IMAGE_DIGEST}" \
  COLLECTOR_IMAGE="${COLLECTOR_IMAGE}" COLLECTOR_CAMPAIGN="${COLLECTOR_CAMPAIGN}" \
  docker compose -f deploy/public-collector.compose.yml stop collector
```

Health success alone is not publication acceptance: inspect native health and
both COMPLETE/manifest inventories. Measure each lane's physical bytes per
observed second, peak growth, allocation, publication duration, clock uncertainty
and feed/session gaps. Derive NAS growth from **measured rates** and reserves,
not invented GB/day estimates. Then review an exact longer-chunk command or
Compose override, lane selection, cleanup option and bounded duration. New
campaigns reverify current source/image/storage/network controls. Stop on the
first integrity/publication/resource boundary. Untouched holdout designation
and gap-safe cross-chunk analysis remain separate research gates.
