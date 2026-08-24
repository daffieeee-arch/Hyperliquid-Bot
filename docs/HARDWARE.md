# Hardware & Host Plan

## Two-host model

The project deliberately uses different machines for different jobs:

- **Windows workstation:** interactive development, Codex, frontend work, local tests, disposable containers and optional GPU research.
- **TrueNAS SCALE host:** 24/7 market-data collection, persistent databases, paper/shadow/live services, Grafana and operational monitoring.

The trading runtime must not depend on the Windows workstation remaining powered on.

## Development workstation

Current known system:

- Windows 11, fully updated;
- AMD Ryzen 7 7800X3D;
- 32 GB RAM;
- NVIDIA RTX 4070 SUPER 12 GB;
- WSL2 Ubuntu;
- Docker Desktop WSL2 backend;
- ChatGPT/Codex desktop with mobile Remote supervision.

### Development role

Use for:

- Python and TypeScript implementation;
- Codex feature branches/worktrees;
- unit and integration tests;
- Next.js hot reload;
- local disposable ClickHouse/Grafana/Redis/PostgreSQL as needed;
- small and medium backtests;
- bounded data exports;
- later GPU-assisted ML experiments.

Do not use it as the 24/7 paper/live host and do not store the Hyperliquid master wallet or normal production trading secrets there.

### WSL2 resource starting point

A reasonable initial cap for the 32 GB workstation is:

```text
WSL2 memory:       approximately 20 GB
WSL2 processors:   approximately 12 logical CPUs
WSL2 swap:         approximately 8 GB
```

Tune after measurement. Keep sufficient RAM and CPU headroom for Windows, the ChatGPT desktop app and other foreground work.

### Workstation storage

The repository and Linux development files should live inside the WSL Linux filesystem, for example:

```text
~/code/Hyperliquid-Bot
```

Do not develop from an SMB-mounted TrueNAS directory or `/mnt/c` when the toolchain runs in WSL. Local Docker volumes are disposable; full runtime history remains on TrueNAS.

### GPU

The RTX 4070 SUPER is optional capacity for later:

- model training;
- feature experiments;
- local inference benchmarking;
- order-book or regime-classification research.

Phase 1 must remain CPU-capable. No core collector, risk or execution process may require the GPU.

## Runtime host

The 24/7 platform runs on the existing TrueNAS SCALE system with:

- TrueNAS SCALE 26.0.0-BETA.3 at the current planning point;
- AMD Ryzen 7 PRO 8845HS, 8 cores / 16 threads;
- approximately 64 GB RAM;
- SSD-backed `fastdisk`;
- HDD RAIDZ1 `tank` for bulk/archive storage;
- existing ClickHouse, Grafana and Hermes-related workloads.

ECC was expected from the hardware context but was not confirmed by the most recent software audit. Treat ECC status as **unverified** until hardware/firmware reporting confirms it.

The host is powerful enough for the intended first strategies but remains a shared homelab/NAS, not an unlimited institutional cluster.

## Runtime resource philosophy

Prioritize, in order:

1. TrueNAS/ZFS and storage integrity;
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

Recommended logical placement:

```text
fastdisk/
  quant/hyperliquid-paper/clickhouse/
  quant/hyperliquid-paper/control/
  quant/hyperliquid-paper/runtime/
  quant/hyperliquid-paper/features/
  quant/hyperliquid-shadow/
  quant/hyperliquid-live/

tank/
  quant/hyperliquid/archive/
  quant/hyperliquid/raw-market-data/
  quant/hyperliquid/backtests/
  quant/hyperliquid/backups/
```

Exact paths are finalized after reviewing the existing 90+ GB ClickHouse dataset and avoiding disruption to Solana/Hermes data.

Recent/active analytical data stays on SSD. Large cold raw archives move to HDD according to retention rules.

## What not to run initially

Avoid unnecessary heavy infrastructure such as:

- Kafka;
- Elasticsearch/OpenSearch;
- Kubernetes;
- Spark;
- a full Hyperliquid non-validator node on this host;
- duplicate ClickHouse instances without a demonstrated isolation need;
- production container builds on TrueNAS.

Add complexity only when a measured bottleneck justifies it.

## Reliability

TrueNAS runs versioned images, not a mutable source checkout. Services use:

- explicit resource limits;
- health/readiness checks;
- restart policies;
- persistent datasets;
- image digest recording;
- startup reconciliation;
- known-good rollback images.

Restart recovery must never assume that local trading state is authoritative. For authenticated execution, reconcile against the venue before enabling new risk.

## Operating-system maturity

TrueNAS 26 BETA.3 is suitable for current architecture work, public data and PAPER testing with backups and monitoring. It is an early-release platform and should not carry material live trading risk without explicit acceptance.

Before SMALL LIVE:

- prefer a stable TrueNAS release or another stable isolated runtime;
- re-run deployment, soak, recovery and rollback tests after an OS upgrade;
- confirm Custom Apps, private registry pulls, networking and persistent mounts;
- verify temperature and memory behavior under sustained load.

## Backups

Back up at minimum:

- PostgreSQL/control state when introduced;
- strategy/model metadata;
- Grafana provisioning and dashboard definitions;
- critical ClickHouse metadata and selected data partitions;
- TrueNAS deployment configuration;
- runtime configuration without secret values;
- release/image digest history;
- repository state through GitHub.

Do not place master-wallet keys into general NAS snapshots. Runtime agent keys require a deliberate encrypted backup/rotation design.
