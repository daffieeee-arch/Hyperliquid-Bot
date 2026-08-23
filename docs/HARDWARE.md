# Hardware & TrueNAS Plan

## Target host

The initial platform runs on the existing TrueNAS SCALE system with:

- AMD Ryzen 7 PRO 8845HS;
- 64 GB ECC RAM;
- SSD-backed fast datasets;
- HDD RAIDZ1 bulk/archive storage;
- existing ClickHouse/Grafana/Hermes-related workloads.

The design must respect that this is a powerful homelab/server platform, not an unlimited institutional cluster.

## Resource philosophy

Prioritize the live trading/data path over research workloads.

Suggested initial guardrails:

```text
TrueNAS/ZFS ARC            leave substantial headroom
ClickHouse                 cap roughly 8-12 GB initially
Trading services           roughly 4-6 GB combined target
PostgreSQL                 roughly 1-2 GB
Redis                      <= 1 GB initially
Grafana                    about 1 GB class workload
Prometheus/Alloy            small bounded footprint
Frontend/API               roughly 1-2 GB
Collectors                 roughly 2-4 GB depending on feeds
Research worker            explicitly capped, low priority
```

These are operational limits to tune with measurement, not permanent reservations.

## CPU

The 8845HS is sufficient for the intended first strategies, collectors, ClickHouse analytics and frontend/backend services. Research workers should be constrained so CPU spikes cannot starve realtime services.

A heavier experiment may receive, for example:

```text
CPU: up to ~6 cores
RAM: up to ~12 GB
priority: lower than collector/trader/risk services
```

## Storage placement

Recommended logical placement:

```text
fastdisk/
  quant/clickhouse/
  quant/postgres/
  quant/redis/
  quant/live/
  quant/features/
  quant/models/

tank/
  quant/archive/
  quant/raw-market-data/
  quant/backtests/
  quant/backups/
```

Recent/active analytical data stays on SSD. Large cold raw archives move to HDD according to retention rules.

## What not to run initially

Avoid unnecessary heavy infrastructure such as:
- Kafka;
- Elasticsearch/OpenSearch;
- Kubernetes;
- Spark;
- a full Hyperliquid non-validator node on this host.

Add complexity only when a measured bottleneck justifies it.

## Reliability

Trading services should use restart policies and persistent state where appropriate, but restart recovery must not assume that local state is authoritative. On startup, reconcile against Hyperliquid before enabling new risk.

## Backups

Back up at minimum:
- PostgreSQL control/config database;
- strategy/model metadata;
- Grafana provisioning/dashboards;
- critical ClickHouse metadata or selected tables;
- infrastructure configuration;
- repository state through GitHub.

Do not back up private keys into general NAS snapshots without a deliberate secret-management design.