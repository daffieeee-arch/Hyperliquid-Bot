# Linux VPS reference profile — PAPER capture and PAPER ops

Status: committed **migration-target** profile for a future always-on Ubuntu LTS
VPS. This is **not** an order to provision, cut over, or stop TerraPC today.

PAPER / research capture only. No LIVE trading, no signing, no capital, no
exchange order credentials. Browser code and Grafana never sign orders.

The current production capture path remains **TerraPC Windows 11 + WSL2**.
Operator start/status/stop commands stay in the existing WSL runbooks until an
explicit CoS cutover:

- [data1a-wsl-pc-retained-capture.md](data1a-wsl-pc-retained-capture.md)
- [data1f-wsl-pc-retained-capture.md](data1f-wsl-pc-retained-capture.md)
- [data1e-wsl-pc-retained-capture.md](data1e-wsl-pc-retained-capture.md)
- [data1b-wsl-pc-retained-capture.md](data1b-wsl-pc-retained-capture.md)

Per-venue VPS command counterparts (same path contracts, later host) are
[data1a-vps-retained-capture.md](data1a-vps-retained-capture.md),
[data1f-vps-retained-capture.md](data1f-vps-retained-capture.md),
[data1e-vps-retained-capture.md](data1e-vps-retained-capture.md), and
[data1b-vps-retained-capture.md](data1b-vps-retained-capture.md).

This document is **not** the definitive runtime ADR. Provisioning, factual
migration, and that ADR remain later work after the local vertical slice
([DEPLOYMENT.md](../DEPLOYMENT.md), [ROADMAP.md](../ROADMAP.md)).

Vendor product facts below were checked against official pages on
**2026-09-06**. SKU availability and list prices change; confirm on the vendor
page before ordering.

## Why a VPS

TerraPC WSL is acceptable for the current assigned 72-hour retain. It is not
the long-term 24/7 target:

- Windows sleep, hibernate, update reboot, lid-close, and `wsl --shutdown`
  kill the collector. Detached tmux survives a closed terminal; it does not
  survive a WSL VM stop. See the DATA-1A WSL host-posture section.
- A VPS does not sleep. It keeps a predictable public IP and egress path for
  continuous WebSocket capture and parquet writers.
- Four concurrent collectors (HL / BN / BV / KR) need a host that stays up
  across multi-day windows (writer max **604800 s / 7 days**; not always-on
  service, not D22-B).

Cursor Cloud Agents and Cursor Pro VMs are **unsuitable** for always-on
multi-day WebSocket capture. They are ephemeral: they can freeze or be
reclaimed mid-run. The committed DATA-1A live-evidence sample
`20260904t001700z-live-retained` already records a cloud-agent freeze after
~49 minutes of process time. Cloud Agents must not SSH to TerraPC or stop
`hl-capture` / `bn-capture` / `bv-capture` / `kr-capture`.

## Out of scope

| Item | Status |
| --- | --- |
| TrueNAS SCALE as capture host or PAPER runtime | **Retired.** Share or optional Ubuntu VM on that hardware only; not the capture host. |
| Windows as the capture OS | Out of scope. WSL2 on the home PC is the current TerraPC retain path only. |
| Cursor Cloud Agent / Cursor Pro as the capture host | Unsuitable (ephemeral). |
| LIVE trading, signing, withdrawal/transfer keys | Forbidden on this profile. |
| Always-on collector with no duration cap | Not implemented. Writer still exits at the requested duration or SIGINT/SIGTERM. |
| Definitive runtime ADR / migrate-today order | Separate later work. |
| ClickHouse init/overwrite on existing TrueNAS data | Not authorized by this document. |

## Reference profile

Target: one modest Linux/amd64 Ubuntu LTS VPS that can run **four concurrent**
PAPER collectors plus parquet writers (Hyperliquid DATA-1A, Binance DATA-1F,
Bitvavo DATA-1E, Kraken DATA-1B).

### Operating system

Use a current **Ubuntu LTS** server image (amd64). Canonical documents LTS
releases every two years with five years of standard security maintenance
([Ubuntu release cycle](https://ubuntu.com/about/release-cycle)).

Prefer **Ubuntu 24.04 LTS** (`Noble`) so the capture host matches GitHub
Actions `ubuntu-24.04` and the existing WSL/CI toolchain. Ubuntu 26.04 LTS
exists (released April 2026 on the same page); do not jump the capture host
ahead of CI/images without a later decision.

Official images: [Ubuntu 24.04 releases](https://releases.ubuntu.com/24.04/),
DigitalOcean Droplet image list
(`ubuntu-24-04-x64`;
[Linux images](https://docs.digitalocean.com/products/droplets/details/images/)),
Hetzner Cloud OS list
([Servers overview](https://docs.hetzner.com/cloud/servers/overview/)).

### Compute (modest)

[HARDWARE.md](../HARDWARE.md) already caps **collectors combined** at about
**1.5 vCPU / 1.25 GiB**. That is a ceiling to tune with evidence, not a
reservation. Four concurrent writers plus OS and parquet buffers therefore
fit a small VPS:

| Resource | Starting point | Basis |
| --- | --- | --- |
| vCPU | 2–4 | Above the 1.5 vCPU collector ceiling; leave headroom for parquet flush. |
| RAM | 4–8 GiB | Above the 1.25 GiB collector ceiling plus OS. |
| Architecture | Linux/amd64 | Matches CI images. No GPU. |

Do not size this host for ClickHouse, cockpit, Grafana, or research workers.
Those remain later PAPER-ops additions on the same Ubuntu LTS profile, not
part of the first capture cutover.

### Disk (order of magnitude; stated assumptions)

Keep `${ARTIFACT_ROOT}` on a durable volume **outside git**. Do not invent a
measured 72-hour four-venue rate; none is published. Use the existing
per-venue **operator budgets** and watch growth:

| Venue | Documented disk/bandwidth note | Source |
| --- | --- | --- |
| DATA-1A Hyperliquid | Tiny feed; tens of KB/s. Bandwidth is not the limiter on TerraPC. | WSL DATA-1A runbook |
| DATA-1F Binance | 60 s smoke ≈ 8 MB payload / 1.6 MB Parquet; **budget tens of GB for 72 h**. Spot `depth@100ms` is the heavy feed. | [DATA.md](../DATA.md) |
| DATA-1B Kraken | Public 72 h L2+trades at depth 100: **low-single-digit to low-tens of GB**, KB/s–tens-of-KB/s. More if optional L3. Operator budget, not a measured rate. | [DATA.md](../DATA.md) |
| DATA-1E Bitvavo | No published 72 h disk budget. Depth-1000 book + trades; treat as **at least Kraken-class**, not Binance-class, until measured. | DATA-1E runbooks |

Assumptions for sizing (not measurements):

- One overlapping four-venue window lasts 72 h to 7 d (writer max 604800 s).
- Binance dominates local disk. Scaling the documented 72 h “tens of GB”
  budget linearly to 7 d is still **tens of GB**, and can approach
  **low-hundreds of GB** if the 72 h figure sits at the high end of “tens”
  and several `run_id` directories are retained. Do not treat that upper
  end as a measured rate.
- Published `raw/part-*.parquet` plus `research.duckdb` (rebuilt at stop)
  stay on the volume. A hard crash can lose only the in-memory segment.
- OS, logs, and one extra unused `run_id` need spare headroom.

Starting disk:

- Do **not** use a 40 GB root disk as the four-venue store.
- Prefer **≥160 GB** local SSD, **or** ≥80 GB root plus an attached volume
  mounted at the artifact root.
- Watch `df` during the first overlapping window and grow before the volume
  fills. Create-only runs cannot resume into the same `run_id`.

### Region

Prefer **EU / Amsterdam or nearby** for operator SSH latency (operator in
EU). That is an operator-comfort choice, **not** an exchange-colocation
claim.

**Exchange geography is not the VPS location.** Binance public WebSocket
endpoints and any later Polymarket feed are independent of whether the VPS
sits in Amsterdam, Frankfurt, or Helsinki. Do not claim fill-quality or
alpha from VPS region.

Official nearby-EU locations checked 2026-09-06:

- DigitalOcean **AMS3** (Amsterdam, the Netherlands), also **FRA1**
  (Frankfurt) and **LON1** (London):
  [Regional availability](https://docs.digitalocean.com/platform/regional-availability/).
- Hetzner Cloud **eu-central**: Falkenstein `fsn1`, Nuremberg `nbg1`,
  Helsinki `hel1`. Hetzner Cloud has **no Amsterdam location**:
  [Locations](https://docs.hetzner.com/cloud/general/locations/).

## Provider shortlist (not a vendor lock)

Pick one EU provider that meets the profile. Re-check the official page at
order time. Criteria, in order:

1. **Price / performance** for 2–4 vCPU and 4–8 GiB, billed hourly with a
   monthly cap.
2. **Disk** ≥160 GB local or cheap attachable SSD volume.
3. **Snapshots / backups** that can capture the OS disk (artifact volumes
   are usually **not** included — confirm).
4. **Included egress** large enough that a multi-day inbound WebSocket
   retain plus occasional artifact copy-out is not a surprise bill.
   Inbound capture traffic is the main flow; egress matters when copying
   parquet off-box.
5. EU location (Amsterdam or nearby), IPv4, SSH keys, and a firewall.

### 1. DigitalOcean Droplets — Amsterdam (`ams3`)

Official pages:

- Product / list prices: [Droplet pricing](https://www.digitalocean.com/pricing/droplets)
- Billing notes: [Droplet pricing docs](https://docs.digitalocean.com/products/droplets/details/pricing/)
- Regions: [Regional availability](https://docs.digitalocean.com/platform/regional-availability/)
- Ubuntu images: [Linux images](https://docs.digitalocean.com/products/droplets/details/images/)
- First-boot hardening: [Recommended Droplet setup](https://docs.digitalocean.com/products/droplets/getting-started/recommended-droplet-setup/)

Basic Droplet rows from the official pricing page (2026-09-06):

| Memory | vCPU | Transfer (outbound) | SSD | List price |
| --- | --- | --- | --- | --- |
| 4 GiB | 2 | 4,000 GiB | 80 GiB | $24 / mo |
| 8 GiB | 4 | 5,000 GiB | 160 GiB | $48 / mo |

Prefer the **8 GiB / 160 GiB** Basic size for four-venue parquet headroom, or
the 4 GiB / 80 GiB size **plus** a Volume if Binance growth requires it.
Inbound transfer is free; extra outbound is $0.01 / GiB
([bandwidth billing](https://docs.digitalocean.com/products/droplets/details/pricing/#bandwidth-billing)).
Droplet Snapshots are listed at $0.06 / GB / month on the same pricing page.
AMS3 shows Basic Droplet availability on the regional-availability table.

### 2. Hetzner Cloud — Germany / Finland (`fsn1` / `nbg1` / `hel1`)

Official pages:

- Product: [Hetzner Cloud](https://www.hetzner.com/cloud/)
- Cost-Optimized SKUs: [Cost-Optimized](https://www.hetzner.com/cloud/cost-optimized/)
- Locations: [Locations](https://docs.hetzner.com/cloud/general/locations/)
- Snapshots / backups: [Backups and snapshots](https://docs.hetzner.com/cloud/servers/backups-snapshots/overview/)

Cost-Optimized x86 rows from the official page (2026-09-06). EU-central
includes **at least 20 TB** traffic. Prices are shown on that page (hourly
and monthly cap; confirm current figures — they are not copied here because
the marketing page does not always render a stable number in text extracts).

| SKU | vCPU | RAM | NVMe | Traffic (EU) |
| --- | --- | --- | --- | --- |
| CX23 | 2 | 4 GB | 40 GB | 20 TB |
| CX33 | 4 | 8 GB | 80 GB | 20 TB |
| CX43 | 8 | 16 GB | 160 GB | 20 TB |

CX23 local disk is **too small** for four-venue multi-day parquet. Prefer
**CX33 plus a Volume**, or **CX43** (160 GB) when that SKU is orderable.
The Cost-Optimized page marked several SKUs “currently unavailable” at
check time; treat names as the official catalog, not a guarantee the SKU
is in stock.

Hetzner snapshots are manual copies of the **server disk only**; attached
Volumes are not included
([backup/snapshot overview](https://docs.hetzner.com/cloud/servers/backups-snapshots/overview/)).
Daily backups are a separate product (seven slots, oldest rotated).

Hetzner is usually the stronger price/perf and included-bandwidth option
in nearby EU. DigitalOcean is the option that is actually **in Amsterdam**.
Neither is locked.

## Artifact root and path contracts

VPS reconstructable root used by the existing VPS runbooks:

```text
/var/lib/hyperliquid-bot/reconstructable
```

TerraPC WSL root (current retain; do not invent a second tree):

```text
~/hyperliquid-artifacts/reconstructable
```

Same create-only layout on either host:

```text
<artifact-root>/
  data-1a/hyperliquid/BTC-PERP/<run_id>/
  data-1f/binance/BTCUSDT/<run_id>/
  data-1e/bitvavo/BTC-EUR/<run_id>/
  data-1b/kraken/BTC-USD/<run_id>/
  logs/capture-<run_id>.log          # optional tmux stdout copy
```

Each run directory:

```text
  capture-claim.json
  capture-health.json                # written at stop; missing while running is expected
  capture-<run_id>.log
  raw/part-*.parquet
  research.duckdb                    # rebuilt at collector stop
```

Helpers: `data1a_run_paths`, `data1f_run_paths`, `data1e_run_paths`,
`data1b_run_paths`. `run_id` is 1–64 lowercase ASCII letters, digits, dot,
dash, or underscore. Never resume or overwrite an existing run directory.

Keep the artifact root **outside git**. Do not commit `raw/part-*.parquet`
or `research.duckdb`.

## Ops checklist (when CoS later provisions)

Do not apply this to a live TerraPC window. Do not start VPS collectors
until CoS assigns the window.

1. **SSH keys only.** Disable password authentication. Restrict the
   provider firewall to operator IPs where practical.
2. **fail2ban** (or equivalent) on sshd. No password logins.
3. **Clock sync** (`systemd-timesyncd` or `chrony`). Cockpit freshness is
   `now - last_part_mtime <= COCKPIT_CAPTURE_FRESH_MAX_S` (default 180 s).
   A wrong host clock produces false STALE/RUNNING.
4. **tmux** sessions, same names as TerraPC: `hl-capture`, `bn-capture`,
   `bv-capture`, `kr-capture`. Detached tmux is the default retain supervisor.
5. **systemd** only if CoS wants a unit: `Type=simple`, `KillSignal=SIGINT`,
   `Restart=no`. Never `Restart=always` and never `systemctl stop` during an
   assigned evidence window.
6. **unattended-upgrades** for security patches, carefully: allow automatic
   package updates; **disable automatic reboot** for the duration of an
   assigned evidence window. Reboot only after `capture-health.json` exists
   or CoS orders a stop.
7. **No `OPERATOR_STOP` during an evidence window.** Do not send SIGINT,
   SIGTERM, `tmux kill-session`, or Cloud-Agent SSH. After stop, read
   `elapsed_seconds` separately from requested `duration_seconds`.
   `OPERATOR_STOP` with a shorter elapsed time is an interrupt, not a
   completed tape.
8. **Env files mode `600`**, owned by the capture user, outside git.
   Never commit keys. Values are never printed or logged.
9. Fail closed if protected trade/signing names are present
   (`HYPERLIQUID_PK`, generic `BITVAVO_API_*`, generic `KRAKEN_API_*`,
   and the lists in the WSL runbooks). DATA-1E View-only
   `BITVAVO_MDPRO_API_KEY` / `BITVAVO_MDPRO_API_SECRET` and optional
   Kraken WS names are the only later data-plane secrets.
10. `unset TRADING_MODE` and `unset D41_EXECUTION_MODE` before starting a
    collector. PAPER is irrelevant to these public/view-only writers;
    they never sign.

Start/status/stop command text until cutover: **WSL runbooks**, not this
file. After cutover, use the matching `data1*-vps-retained-capture.md`
with `ARTIFACT_ROOT=/var/lib/hyperliquid-bot/reconstructable`.

## What stays on TerraPC now vs what moves later

| Now on TerraPC WSL | Later on this VPS (after CoS cutover) |
| --- | --- |
| Current assigned 72 h retain (`hl-capture` / `bn-capture` / `bv-capture` / `kr-capture` as assigned) | Multi-day overlapping four-venue PAPER capture |
| Operator Cockpit `next dev` against the WSL artifact root | Optional later read-only cockpit pointing at the VPS root |
| Codex / WSL development, tests, PRs | Nothing. The VPS is not a development host. |
| Command source of truth (WSL runbooks) | Same commands, VPS artifact root, after cutover |
| Sleep-risk accepted for the current 72 h | No host sleep; durable volume |

Do **not** copy a live DuckDB catalog or append to published parquet on
cutover. Start a **new** `run_id` on the VPS.

## Secrets

- Never add, print, log, or commit real private keys or exchange
  credentials.
- The Hyperliquid master-wallet key must not reside on Windows/WSL2, any
  runtime host or container (including a TrueNAS share or Ubuntu VM), or
  GitHub.
- No withdrawal or transfer permissions on any bot credential.
- Gitignored env files: `chmod 600`. Copy names from `.env.example` /
  `apps/cockpit/.env.example` only.
- This profile does not authorize LIVE, SHADOW, or TESTNET keys.

## Explicitly not proven / not authorized

- Provisioning or paying for a VPS in this document
- 24/7 always-on collection
- Lossless WAL / crash-safe in-memory segment recovery
- Cloud Agent or Cursor Pro multi-day capture
- D22-B venue-authoritative reconciliation
- Funding settlement, signing, TESTNET, SHADOW, LIVE
- Profitability or strategy promotion
- TrueNAS / ClickHouse as the capture or PAPER runtime
- Exchange-colocation benefits from an Amsterdam (or nearby EU) VPS
