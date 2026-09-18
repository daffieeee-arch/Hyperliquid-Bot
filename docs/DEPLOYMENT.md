# Deployment & Environment Promotion

## Principle

Source code is developed primarily on the Netcup Ubuntu 24.04 LTS VPS
(hostname `chupa`), tested in CI and packaged into immutable Linux/amd64 OCI
images for continuous services on that host.

Production-like services must not run directly from the mutable development
checkout. They consume versioned images and persistent volumes even though
development and runtime share the VPS.

The architecture boundary is host-neutral Linux/OCI. **ADR-024** records a supported Ubuntu LTS
VPS as the definitive primary development, capture and PAPER runtime profile.
The selected host is the Netcup Ubuntu 24.04 LTS VPS.

## Environment matrix

| Environment | Host | Purpose | Credentials | Persistence |
|---|---|---|---|---|
| DEV | Netcup Ubuntu 24.04 LTS VPS; TerraPC/WSL2 secondary | coding, unit tests, local integration tests, small research | no live exchange credentials | disposable/local |
| CI | GitHub Actions | independent tests, security checks and image builds | repository-scoped workflow credentials | ephemeral |
| PAPER | Netcup Ubuntu 24.04 LTS VPS (`chupa`) | 24/7 public data, paper execution, Grafana and cockpit | public data; no live trading key | durable |
| SHADOW | approved isolated Linux/OCI runtime | live signals/account observation without submitting orders | read-only/account-scoped where needed | durable and isolated |
| SMALL LIVE | approved supported isolated runtime | tightly capped real execution | dedicated trade-only credentials | durable and isolated |
| PRODUCTION | approved supported isolated runtime | controlled scaled operation | least-privilege per service | durable, backed up and monitored |

PAPER, SHADOW and LIVE are distinct deployments with separate configuration, secrets, data namespaces and risk limits. A strategy or image approved in PAPER is not implicitly approved for LIVE.

## Software supply chain

```text
Cursor / Codex on the Netcup VPS
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
approved runtime PAPER deployment
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

- secret scanning (never skipped on PRs that touch code);
- dependency review on pull requests;
- actionlint on workflow YAML;
- a focused PAPER API/browser regress suite (fixtures only);
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

Each deployment profile receives a dedicated registry credential with package-read access only. That credential is not used for Git repository writes and cannot publish packages.

## Primary Ubuntu LTS VPS profile

**ADR-024** selects Linux/amd64 OCI images and Compose-compatible declarative configuration on a
supported Ubuntu LTS VPS (Ubuntu 24.04 LTS) as the definitive PAPER runtime
profile. Deployments
pin image digests; `PAPER` remains fail-closed; rollback is redeploy of a previous digest.

A PAPER-only reference size for multi-day market-data capture is
[linux-vps-reference-profile.md](runbooks/linux-vps-reference-profile.md).

## Initial deployment policy

During early development:

- merge does not automatically deploy to any runtime;
- release image build is separate from ordinary pull-request CI;
- PAPER deployment requires explicit operator approval;
- the approved runtime pulls a specified digest;
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

## Runtime change authority

Hermes and MCP integrations may manage the environment, but standing privileges remain scoped:

- destructive storage/dataset, update or reboot actions require explicit approval;
- Grafana MCP may edit the Hyperliquid folder and alerts;
- datasource credentials remain read-only where possible;
- ClickHouse schema migrations use a separate controlled identity from research queries;
- exchange withdrawals are never exposed to the bot or deployment automation.
