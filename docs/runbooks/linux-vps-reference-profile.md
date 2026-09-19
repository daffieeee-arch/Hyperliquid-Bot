# Linux VPS reference profile — PAPER capture and PAPER ops

Status: active profile for the Netcup Ubuntu 24.04 LTS VPS (hostname `chupa`),
the primary development, capture and PAPER host.

PAPER / research capture only. No LIVE trading, no signing, no capital, no
exchange order credentials. Browser code and Grafana never sign orders.

The VPS operator runbooks are the primary start/status/stop guides:

- [data1a-vps-retained-capture.md](data1a-vps-retained-capture.md)
- [data1f-vps-retained-capture.md](data1f-vps-retained-capture.md)
- [data1d-vps-retained-capture.md](data1d-vps-retained-capture.md)
- [data1e-vps-retained-capture.md](data1e-vps-retained-capture.md)
- [data1b-vps-retained-capture.md](data1b-vps-retained-capture.md)

The WSL-named runbooks remain secondary TerraPC operator notes.

This document is the sizing and operating companion to **ADR-024**
([DEPLOYMENT.md](../DEPLOYMENT.md), [ROADMAP.md](../ROADMAP.md),
[DECISIONS/README.md](../DECISIONS/README.md)).

Operational platform facts were rechecked against official pages on
**2026-09-18**. Ubuntu documents 24.04 as LTS, Netcup lists Ubuntu 24.04
vServer images, Docker supports Ubuntu Noble 24.04, Cursor documents the
Linux `agent` CLI, and OpenAI documents Codex CLI on Linux. Links appear
below and in [DEVELOPMENT.md](../DEVELOPMENT.md).

## Why a VPS

The Netcup VPS is the selected host because it remains available independently
of TerraPC:

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
| TerraPC/WSL2 as primary capture host | Retired; secondary operator notes only. |
| Cursor Cloud Agent / Cursor Pro as the capture host | Unsuitable (ephemeral). |
| LIVE trading, signing, withdrawal/transfer keys | Forbidden on this profile. |
| Always-on collector with no duration cap | Not implemented. Writer still exits at the requested duration or SIGINT/SIGTERM. |
| Definitive runtime ADR | **ADR-024**. |

## Reference profile

Target: one modest Linux/amd64 Ubuntu LTS VPS that can run **four concurrent**
PAPER collectors plus parquet writers (Hyperliquid DATA-1A, Binance DATA-1F,
Bitvavo DATA-1E, Kraken DATA-1B).

### Operating system

Use a current **Ubuntu LTS** server image (amd64). Canonical documents LTS
releases every two years with five years of standard security maintenance
([Ubuntu release cycle](https://ubuntu.com/about/release-cycle)).

Use **Ubuntu 24.04 LTS** (`Noble`) so the capture host matches GitHub Actions
`ubuntu-24.04`. Official references:

- Ubuntu release lifecycle: https://ubuntu.com/about/release-cycle
- Ubuntu Server documentation: https://ubuntu.com/server/docs/
- Netcup vServer images: https://www.netcup.com/en/server/vserver-images
- Docker Engine on Ubuntu: https://docs.docker.com/engine/install/ubuntu/

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
| DATA-1D Bitvavo Standard | Operator budget **~3-5 GB / 72 h** for trades/ticker/book (+ optional candles). Distinct from Pro (`bv-std-capture` / `data-1d/`). | DATA-1D runbooks |

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

## Selected provider

Netcup is selected. Its official vServer image catalog lists Ubuntu 24.04,
Ubuntu cloud image 24.04 and Ubuntu 24.04 Live Server for KVM-based vServers:
https://www.netcup.com/en/server/vserver-images

Do not infer unrecorded CPU, memory, disk, traffic, region or availability
from this architecture decision. Verify the actual `chupa` VPS allocation in
the Netcup control plane before changing collector concurrency or retention.

## Repository and artifact path contracts

Primary VPS defaults used by the capture helpers and VPS runbooks:

```bash
export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
```

Create-only layout:

```text
<artifact-root>/
  data-1a/hyperliquid/BTC-PERP/<run_id>/
  data-1f/binance/BTCUSDT/<run_id>/
  data-1d/bitvavo/BTC-EUR/<run_id>/
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

Helpers: `data1a_run_paths`, `data1f_run_paths`, `data1d_run_paths`, `data1e_run_paths`,
`data1b_run_paths`. `run_id` is 1–64 lowercase ASCII letters, digits, dot,
dash, or underscore. Never resume or overwrite an existing run directory.

Keep the artifact root **outside git**. Do not commit `raw/part-*.parquet`
or `research.duckdb`.

## Ops checklist

Do not start collectors until CoS assigns the window.

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

Use the matching `data1*-vps-retained-capture.md` with the primary
`ARTIFACT_ROOT` above.

## Primary and secondary host roles

| Netcup Ubuntu VPS (primary) | TerraPC/WSL2 (secondary) |
| --- | --- |
| Assigned multi-day PAPER captures | Optional fallback captures only when explicitly assigned |
| Cursor Remote SSH / `agent` / Codex CLI development | Optional WSL/GPU development |
| Primary cockpit and artifact root | Optional read-only cockpit |
| VPS runbooks | WSL-named fallback runbooks |

Do not copy a live DuckDB catalog or append to published parquet when moving a
capture between hosts. Start a new `run_id`.

## Secrets

- Never add, print, log, or commit real private keys or exchange
  credentials.
- The Hyperliquid master-wallet key must not reside on any development or
  runtime host, container, or GitHub.
- No withdrawal or transfer permissions on any bot credential.
- Gitignored env files: `chmod 600`. Copy names from `.env.example` /
  `apps/cockpit/.env.example` only.
- This profile does not authorize LIVE, SHADOW, or TESTNET keys.

## Explicitly not proven / not authorized

- 24/7 always-on collection
- Lossless WAL / crash-safe in-memory segment recovery
- Cloud Agent or Cursor Pro multi-day capture
- D22-B venue-authoritative reconciliation
- Funding settlement, signing, TESTNET, SHADOW, LIVE
- Profitability or strategy promotion
- Exchange-colocation benefits from an Amsterdam (or nearby EU) VPS
