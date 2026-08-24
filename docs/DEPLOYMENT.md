# Deployment & Environment Promotion

## Principle

Source code is developed on Windows/WSL2, tested in CI, packaged into immutable Linux container images and then deployed to TrueNAS.

TrueNAS must not run directly from a mutable development checkout. Production-like services consume versioned images and persistent volumes.

## Environment matrix

| Environment | Host | Purpose | Credentials | Persistence |
|---|---|---|---|---|
| DEV | Windows 11 + WSL2 | coding, unit tests, local integration tests, small research | no live exchange credentials | disposable/local |
| CI | GitHub Actions | independent tests, security checks and image builds | repository-scoped workflow credentials | ephemeral |
| PAPER | TrueNAS SCALE | 24/7 public data, paper execution, Grafana and cockpit | public data; no live trading key | durable |
| SHADOW | TrueNAS SCALE | live signals/account observation without submitting orders | read-only/account-scoped where needed | durable and isolated |
| SMALL LIVE | TrueNAS SCALE or later stable dedicated host | tightly capped real execution | dedicated trade-only credentials | durable and isolated |
| PRODUCTION | stable runtime host | controlled scaled operation | least-privilege per service | durable, backed up and monitored |

PAPER, SHADOW and LIVE are distinct deployments with separate configuration, secrets, data namespaces and risk limits. A strategy or image approved in PAPER is not implicitly approved for LIVE.

## Software supply chain

```text
Codex in WSL2
      ↓
feature branch / pull request
      ↓
GitHub Actions CI
      ↓
merge to main
      ↓
release workflow
      ↓
private GHCR image(s)
      ↓
manual approval
      ↓
TrueNAS PAPER deployment
      ↓
health/soak validation
      ↓
SHADOW / SMALL LIVE promotion
```

## CI stages

The initial CI pipeline should include:

### Python

- formatting check;
- Ruff linting;
- static typing;
- unit tests;
- deterministic replay tests;
- coverage reporting for safety-critical packages.

### TypeScript / frontend

- formatting/linting;
- TypeScript strict typecheck;
- component/unit tests;
- production Next.js build.

### Cross-cutting

- secret scanning;
- dependency review where available;
- container build smoke test;
- schema/contract compatibility checks;
- configuration validation;
- tests proving non-PAPER modes fail closed until explicitly implemented;
- eventual vulnerability scan of release images.

GitHub Actions should use minimal workflow permissions, and third-party actions should be pinned to reviewed immutable commit SHAs where practical.

## Container images

Initial target architecture:

```text
linux/amd64
```

Planned service images may include:

- collector;
- API/control gateway;
- paper/trading service;
- research worker;
- cockpit.

A shared Python base layer may be used, but runtime services remain separately addressable where failure isolation matters.

Image requirements:

- multi-stage builds;
- minimal runtime dependencies;
- non-root user where compatible;
- explicit health checks;
- no source-control credentials;
- no exchange or database secrets baked into layers;
- reproducible lockfiles;
- OCI labels containing repository, commit and build time;
- version tag plus commit SHA;
- deployment pinned to image digest rather than a floating tag.

Do not deploy `latest` to PAPER, SHADOW or LIVE.

## Private GitHub Container Registry

Images are published to private GHCR packages after CI and release gates pass. Naming is expected to follow a clear pattern such as:

```text
ghcr.io/daffieeee-arch/hyperliquid-bot-collector
ghcr.io/daffieeee-arch/hyperliquid-bot-api
ghcr.io/daffieeee-arch/hyperliquid-bot-trader
ghcr.io/daffieeee-arch/hyperliquid-bot-cockpit
```

TrueNAS receives a dedicated registry credential with package-read access only. That credential is not used for Git repository writes and cannot publish packages.

## TrueNAS deployment

Deploy through TrueNAS Custom Apps / Compose YAML using images pulled from GHCR.

Runtime configuration lives under version control without secret values. TrueNAS supplies:

- image digest;
- resource limits;
- service network configuration;
- persistent dataset mounts;
- runtime secrets;
- health/restart policy;
- paper/shadow/live mode selection.

Application data belongs in explicit host datasets rather than image layers or anonymous state that cannot be backed up.

Example logical namespaces:

```text
fastdisk/quant/hyperliquid-paper/
fastdisk/quant/hyperliquid-shadow/
fastdisk/quant/hyperliquid-live/
tank/quant/hyperliquid-archive/
tank/quant/hyperliquid-backups/
```

Exact dataset names are finalized after inspecting the existing TrueNAS and ClickHouse layout.

## Initial deployment policy

During early development:

- merge does not automatically deploy to TrueNAS;
- release image build is separate from ordinary pull-request CI;
- PAPER deployment requires explicit operator approval;
- TrueNAS pulls a specified digest;
- post-deploy health and data-quality checks run before the deployment is accepted;
- failed checks trigger rollback to the previous known-good digest.

Automatic deployment may be introduced later, but LIVE always retains an explicit protected approval gate.

## Health gates

A PAPER deployment is accepted only when:

- all required containers report healthy;
- public market feeds are fresh;
- sequence/gap checks are within policy;
- ClickHouse ingestion is working;
- the paper broker reconciles its own ledger;
- FastAPI health/readiness endpoints pass;
- cockpit and Grafana show the same strategy/position state;
- resource use remains inside defined limits;
- no unexpected outbound authenticated exchange traffic occurs.

## Rollback

Every deployment records:

- Git commit SHA;
- image tag;
- image digest;
- schema/migration version;
- configuration version;
- deployment timestamp;
- operator/automation identity.

Rollback means restoring the previous image digest and compatible configuration. Database migrations must be backward-compatible or have a tested rollback/restore plan before deployment.

Do not debug by editing files inside a running container. Fix the repository, rebuild, retest and redeploy.

## Paper and live artifact promotion

Where technically possible, promote the exact same image digest from PAPER to SHADOW and SMALL LIVE. Change only approved configuration, credentials and resource/risk limits.

Rebuilding from the same source commit creates a new artifact and requires revalidation. Passing paper validation belongs to an artifact/configuration pair, not merely to a Git branch name.

## TrueNAS release maturity

TrueNAS 26 BETA.3 is acceptable for the current research and PAPER stage, with backups and monitoring. Material live capital should not depend on an early-release NAS operating system without explicit risk acceptance. Before SMALL LIVE, prefer a stable TrueNAS release or another stable isolated runtime and repeat soak/recovery testing after any operating-system upgrade.

## Runtime change authority

Hermes and MCP integrations may manage the environment, but standing privileges remain scoped:

- TrueNAS operational actions are allowed for project datasets/apps within approved scope;
- destructive pool/dataset, update or reboot actions require explicit approval;
- Grafana MCP may edit the Hyperliquid folder and alerts;
- datasource credentials remain read-only where possible;
- ClickHouse schema migrations use a separate controlled identity from research queries;
- exchange withdrawals are never exposed to the bot or deployment automation.
