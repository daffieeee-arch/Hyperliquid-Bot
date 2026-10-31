# Hardware & Host Plan

## Development and runtime profiles

The project isolates development and continuous runtime responsibilities:

- **Development profile:** Ubuntu VM on TrueNAS SCALE (**ADR-025**), as
  reported by the operator; Ubuntu 26.04.1 LTS / KVM observed locally.
- **Runtime/capture profile:** VM migration is unverified. **ADR-024** remains
  the historical Netcup Ubuntu 24.04 LTS VPS / Linux/amd64 OCI reference.
  Hypervisor placement alone is not a runtime approval.
- **Secondary profile:** TerraPC/WSL2 for optional fallback operator work and
  GPU research.

Continuous services must not depend on TerraPC or an interactive SSH/CLI
session remaining connected. VM CPU/RAM allocation, persistent data mounts,
backups and service health require verification before any continuous run.
See [migration status](runbooks/ubuntu-vm-migration-status.md).

## Secondary TerraPC workstation

Current known system:

- Windows 11, fully updated;
- AMD Ryzen 7 7800X3D;
- 32 GB RAM;
- NVIDIA RTX 4070 SUPER 12 GB;
- WSL2 Ubuntu;
- Docker Desktop WSL2 backend;
- ChatGPT/Codex desktop with mobile Remote supervision.

### Secondary role

Use for:

- Python and TypeScript implementation;
- Codex feature branches/worktrees;
- unit and integration tests;
- Next.js hot reload;
- local disposable ClickHouse/Grafana/Redis/PostgreSQL as needed;
- small and medium backtests;
- bounded data exports;
- later GPU-assisted ML experiments.

Do not use it as the primary 24/7 PAPER host and do not store the Hyperliquid
master wallet or normal production trading secrets there.

A bounded DATA-1A public BTC-PERP retained capture may run under WSL2 while AC
sleep is disabled. That is operator-supervised research capture, not a 24/7
trading runtime and not LIVE. Cloud Agents are unsuitable for that multi-day
window. See [DATA-1A operator PC/WSL runbook](runbooks/data1a-wsl-pc-retained-capture.md).

A later overlapping DATA-1F public Binance BTCUSDT retain uses a **separate**
tmux session (`bn-capture`) and must not start until the assigned DATA-1A
`hl-capture` window finishes and CoS assigns Binance. See
[DATA-1F operator PC/WSL runbook](runbooks/data1f-wsl-pc-retained-capture.md).

Prepare-only operator paths also exist for DATA-1E Bitvavo MD Pro BTC-EUR
book+trades (`bv-capture`) and DATA-1B Kraken public BTC/USD L2+trades
(`kr-capture`). Those helpers refuse
`hl-capture` and `bn-capture` and must not be started until CoS assigns them.
See [DATA-1E operator PC/WSL runbook](runbooks/data1e-wsl-pc-retained-capture.md)
and [DATA-1B operator PC/WSL runbook](runbooks/data1b-wsl-pc-retained-capture.md).
Cloud Agents are unsuitable for those multi-day retains.

### WSL2 resource starting point

A reasonable initial cap for the 32 GB workstation is:

```text
WSL2 memory:       approximately 20 GB
WSL2 processors:   approximately 12 logical CPUs
WSL2 swap:         approximately 8 GB
```

Tune after measurement. Keep sufficient RAM and CPU headroom for Windows, the ChatGPT desktop app and other foreground work.

### Workstation storage

The current development checkout is:

```bash
export REPO_ROOT="$HOME/coding-projects/Hyperliquid-Bot"
```

Set `ARTIFACT_ROOT` and `HIST_ARCHIVES_ROOT` only after locating and verifying
the durable data. The old `$HOME/Hyperliquid Project/` tree was not present
in the initial VM audit.

If the secondary WSL environment is used, keep its checkout in the Linux
filesystem rather than `/mnt/c` or a network share. Disposable development
volumes remain separate from durable capture data.

### GPU

The RTX 4070 SUPER is optional capacity for later:

- model training;
- feature experiments;
- local inference benchmarking;
- order-book or regime-classification research.

Phase 1 must remain CPU-capable. No core collector, risk or execution process may require the GPU.

## Runtime profile and migration boundary

The architecture target remains host-neutral Linux/amd64 OCI. **ADR-024**
records the earlier Netcup Ubuntu LTS reference profile. **ADR-025** moves
source development to the Ubuntu VM but does not certify runtime/data
migration or revive direct NAS-appliance deployment. The companion VPS
runbook is historical sizing guidance, not a statement of the VM allocation.

The legacy PAPER capture/ops reference size for four concurrent collectors is
recorded in
[linux-vps-reference-profile.md](runbooks/linux-vps-reference-profile.md).

## Runtime resource philosophy

Prioritize, in order:

1. host operating-system and storage integrity;
2. market-data collectors and data-quality guards;
3. paper/live risk and execution processes;
4. databases and control API;
5. observability and cockpit;
6. research workers and Codex/Hermes bursts.

Research and development workloads must not starve continuous services.

Conservative starting hard caps for the first paper slice:

```text
Collectors combined         ~1.5 vCPU / 1.25 GiB
ClickHouse                  ~4 vCPU / 6-8 GiB
FastAPI                     ~0.5 vCPU / 512 MiB
Paper trader/risk           ~0.5-1 vCPU / 512 MiB-1 GiB
Next.js cockpit             ~0.5 vCPU / 512 MiB
Grafana + Alloy             ~0.75 vCPU / 768 MiB-1.5 GiB
Redis if introduced         ~0.25 vCPU / 256-512 MiB
PostgreSQL if introduced    ~0.75 vCPU / 1 GiB
Research worker             ~2-6 vCPU / 2-12 GiB, batch only
Hermes                      bounded separately
```

These are ceilings to tune with evidence, not reserved memory.

Do not start heavy research or build workloads when memory headroom is low. Grafana should alert on CPU temperature, available RAM, disk I/O pressure and ClickHouse query pressure.

## Storage placement

Legacy Netcup storage example (not a verified VM mount or migration target):

```text
$HOME/Hyperliquid Project/data-capture/
  data-1a/
  data-1b/
  data-1e/
  data-1f/

/var/lib/hyperliquid-bot/
  clickhouse/
  control/
  runtime/
  backups/
```

Capture helper defaults still use the legacy home-directory path. Configure
explicit verified roots on the VM. Container services may use explicit
`/var/lib` volumes when introduced. Actual durable placement, ownership,
capacity and backups must be documented before deployment.

## What not to run initially

Avoid unnecessary heavy infrastructure such as:

- Kafka;
- Elasticsearch/OpenSearch;
- Kubernetes;
- Spark;
- a full Hyperliquid non-validator node on this host;
- duplicate ClickHouse instances without a demonstrated isolation need;
- production container builds on the runtime host.

Add complexity only when a measured bottleneck justifies it.

## Reliability

Every runtime profile runs versioned images, not a mutable source checkout. Services use:

- explicit resource limits;
- health/readiness checks;
- restart policies;
- persistent datasets;
- image digest recording;
- startup reconciliation;
- known-good rollback images.

Restart recovery must never assume that local trading state is authoritative. For authenticated execution, reconcile against the venue before enabling new risk.

## Before SMALL LIVE

- use a supported stable isolated runtime;
- re-run deployment, soak, recovery and rollback tests after an OS upgrade;
- confirm private registry pulls, networking and persistent mounts;
- verify temperature and memory behavior under sustained load.

## Backups

Back up at minimum:

- PostgreSQL/control state when introduced;
- strategy/model metadata;
- Grafana provisioning and dashboard definitions;
- critical ClickHouse metadata and selected data partitions;
- runtime configuration without secret values;
- release/image digest history;
- repository state through GitHub.

Do not place master-wallet keys into general NAS snapshots. Runtime agent keys require a deliberate encrypted backup/rotation design.
