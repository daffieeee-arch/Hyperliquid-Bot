# Development Workflow

## Decision

Primary software development happens on the Windows 11 workstation through **WSL2 Ubuntu** with Codex/ChatGPT desktop. The independent Linux/OCI runtime is not an interactive source-development machine; it receives tested container images for continuous paper, shadow and later live operation. A supported Ubuntu LTS VPS is the intended primary deployment profile (PAPER capture target: [linux-vps-reference-profile.md](runbooks/linux-vps-reference-profile.md)). TrueNAS SCALE is out of runtime/capture scope.

This separation provides faster iteration, better debugging, safer experimentation and a cleaner 24/7 runtime.

## Development workstation

Current known workstation:

- Windows 11, fully updated;
- AMD Ryzen 7 7800X3D;
- 32 GB RAM;
- NVIDIA RTX 4070 SUPER 12 GB;
- WSL2 Ubuntu;
- ChatGPT/Codex desktop with ChatGPT Pro;
- Docker Desktop using the WSL2 backend.

The GPU is not required for Phase 1. It may later support local ML experiments or model training, while production inference should remain lightweight enough for the approved runtime profile unless evidence justifies otherwise.

## Codex environment

Use the ChatGPT desktop application on Windows, but configure the **Codex agent environment as WSL2** for this repository. The integrated terminal should also normally use WSL.

Recommended permission posture:

- use the workspace-scoped permission profile for ordinary implementation;
- retain approval prompts for commands outside the workspace or for network/system changes;
- do not use unrestricted/danger-full-access as the default;
- never run the desktop app as Administrator for routine repository work;
- use separate feature branches or Codex-managed worktrees for parallel tasks.

Codex may be supervised from the ChatGPT mobile app through Remote. Remote operation still depends on the Windows host remaining powered on, online, connected and available. Files, credentials and commands stay on the host environment.

## WSL2 filesystem policy

Clone and develop the repository inside the Linux filesystem, for example:

```text
~/code/Hyperliquid-Bot
```

Do not use `/mnt/c/...`, an SMB share, or a TrueNAS-mounted source directory as the primary working tree. Linux-native filesystem placement avoids cross-filesystem I/O, symlink and permission problems.

Windows can access the files through:

```text
\\wsl$\Ubuntu\home\<user>\code\Hyperliquid-Bot
```

but Linux tooling remains authoritative for builds and tests.

## WSL2 resource guardrails

The workstation has 32 GB RAM and 16 logical CPU threads. A conservative starting point is to cap the WSL2 utility VM so Windows remains responsive while Codex, Docker and tests run.

Example `%UserProfile%\.wslconfig` starting point:

```ini
[wsl2]
memory=20GB
processors=12
swap=8GB
```

These values are starting limits, not permanent settings. Adjust them after observing actual Docker, compiler and test usage. Restart WSL after changes with `wsl --shutdown` from PowerShell.

## Docker development model

Use Docker Desktop with:

- WSL2 engine enabled;
- WSL integration enabled for the chosen Ubuntu distribution;
- Linux containers mode;
- current Docker Desktop and WSL versions;
- resource usage bounded through WSL settings.

Do not install a second Docker Engine daemon inside the same WSL distribution when Docker Desktop integration is used. Duplicate daemons create confusing sockets, images, networks and permissions.

The local Compose stack is disposable and may include:

- ClickHouse development instance;
- optional Redis development instance;
- optional PostgreSQL development instance;
- FastAPI service;
- collector;
- paper broker;
- Next.js cockpit;
- local Grafana.

Local containers contain test/sample data only. They are not production state.

## Toolchain and version pinning

Planned repository controls:

- Python version pinned in repository metadata;
- Python dependencies managed with `uv` and `uv.lock`;
- Node.js LTS pinned through a repository version file;
- `pnpm` version pinned through `packageManager` and lockfile;
- base container images pinned to known versions and later to digests where practical;
- no production dependency uses an unreviewed floating `latest` tag.

The exact versions are selected during repository bootstrap after compatibility checks with the official Hyperliquid SDK, FastAPI, Next.js and ClickHouse clients.

## Git and Codex workflow

Normal task flow:

```text
GitHub issue or explicit task
        ↓
feature branch / worktree
        ↓
Codex implementation
        ↓
local lint, typecheck and tests
        ↓
review the diff
        ↓
push branch
        ↓
GitHub pull request
        ↓
CI
        ↓
review and merge
```

Rules:

- do not develop directly on `main`;
- do not force-push `main`;
- keep each branch narrowly scoped;
- architecture changes must update the relevant documentation/ADR;
- generated code must pass deterministic tests rather than rely on an LLM review alone;
- no deployment occurs merely because a pull request was merged.

## Local data policy

Development uses:

- synthetic fixtures;
- captured and sanitized public-market samples;
- deterministic replay files;
- small local ClickHouse datasets;
- read-only exports from an approved runtime store where explicitly needed.

The full 24/7 dataset belongs on the selected durable runtime store. Windows development should not mutate a runtime ClickHouse instance. Heavy research can run as a controlled runtime research job or against a bounded exported dataset. Existing TrueNAS/ClickHouse state remains protected pending an explicit retain-or-migrate decision.

Small fixtures suitable for reproducible tests may be committed. Large raw captures, secrets and personal trading/account data must not be committed.

## Secret policy in development

The Windows/WSL development environment must not contain:

- a Hyperliquid master-wallet key;
- a live Hyperliquid agent key during normal development;
- Bitvavo/Kraken withdrawal credentials;
- TrueNAS administrative credentials in repository files;
- production database passwords in source or images.

Initial development requires no authenticated exchange credentials. Public market data and deterministic mocks are sufficient.

Where a later integration test genuinely needs a non-production secret, inject it through local protected secret storage or environment configuration excluded from Git. Never paste it into a Codex prompt, commit, frontend bundle or test fixture.

## Local versus runtime parity

Parity is achieved through containers, contracts and tests—not by editing source directly on a runtime host.

GitHub Actions Phase A, cockpit lint, and the PAPER API/browser regress
suite are documented in [CI](CI.md). Local checks stay the same: `uv run
pytest`, `pnpm run lint`, `pnpm --filter @hyperliquid-bot/cockpit run test`.

Keep consistent across local, CI and every runtime profile:

- Linux/amd64 runtime target;
- service entrypoints;
- environment-variable names;
- schemas and migrations;
- health endpoints;
- image build definitions;
- strategy/risk code;
- broker interfaces.

Differences are limited to configuration, credentials, resource limits and persistent volumes.

## First workstation bootstrap milestone

The development environment is ready when all of the following are true:

- WSL2 Ubuntu is current and healthy;
- Codex agent environment is WSL2;
- mobile Remote can connect to a harmless Codex thread;
- repository is cloned under the WSL Linux home filesystem;
- Git/GitHub authentication works from WSL;
- Docker Desktop WSL integration works;
- `docker run` and `docker compose` work from WSL;
- repository version managers/package tools are available;
- no real trading credentials are present;
- a feature branch can be pushed and a pull request created.

Only after this milestone should Codex bootstrap the application source tree.
