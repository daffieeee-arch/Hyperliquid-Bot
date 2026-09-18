# Development Workflow

## Decision

Primary software development happens on the Netcup **Ubuntu 24.04 LTS** VPS
(hostname `chupa`) through Cursor IDE Remote SSH, Cursor CLI (`agent`) or Codex
CLI. The same VPS is the primary capture and PAPER host under **ADR-024**.
Interactive source work remains isolated from running service containers and
durable runtime volumes; long-running services consume reviewed,
digest-pinned images rather than the mutable checkout.

TerraPC/WSL2 is a secondary operator environment, not the primary development
or capture host.

## Primary development station

Primary host:

- Netcup VPS;
- Ubuntu 24.04 LTS;
- hostname `chupa`;
- Cursor IDE with the Anysphere Remote SSH extension;
- Cursor CLI (`agent`);
- Codex CLI;
- native Linux Python, Node.js and Docker tooling.

Netcup lists Ubuntu 24.04 among its KVM vServer images, Ubuntu classifies
24.04 as an LTS release, and Docker supports Ubuntu Noble 24.04 for Docker
Engine. Cursor documents Linux support for `agent`; OpenAI documents Linux
support for Codex CLI. See the official references below.

## Interactive tools

Use Cursor Remote SSH when an editor is preferred. Use `agent` or `codex`
directly in the VPS shell for terminal-first work. Run all tools from the
repository root unless a worktree is intentionally selected.

Recommended permission posture:

- use the workspace-scoped permission profile for ordinary implementation;
- retain approval prompts for commands outside the workspace or for network/system changes;
- do not use unrestricted/danger-full-access as the default;
- do not run routine development as `root`;
- use separate feature branches or Codex-managed worktrees for parallel tasks.

The primary paths are:

```bash
export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
cd "$REPO_ROOT"
```

Keep capture data outside Git. The source checkout may coexist on the VPS with
runtime services, but it is not mounted into production-like containers and
is not the source of deployed service code.

## Secondary TerraPC/WSL2 notes

TerraPC/WSL2 may be used for isolated fallback development, GPU research, or
the explicitly WSL-named operator runbooks. Keep any WSL checkout in its Linux
filesystem, not under `/mnt/c` or a network share. WSL examples are secondary
overrides; capture helper defaults always resolve to the VPS layout above.

### WSL2 resource guardrails

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

Use Docker Engine on Ubuntu 24.04 according to Docker's official Ubuntu
installation guide. For secondary WSL use, Docker Desktop integration may be
used; do not run a competing Docker Engine daemon in the same distribution.

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

Standard ship workflow for every development task (also encoded in
`AGENTS.md`):

```text
confirm clean, synced Git state
        ↓
fetch latest origin/main
        ↓
feature branch / worktree (never commit on main)
        ↓
narrow implementation (no out-of-scope refactors)
        ↓
add/update tests when behavior changes
        ↓
local lint, typecheck and relevant tests
        ↓
judge existing CI coverage; extend CI only if needed
        ↓
commit and push
        ↓
open pull request → main
        ↓
required CI green
        ↓
independent agent/model review when material or high-risk
        ↓
resolve blocking findings on the same branch; re-run CI
        ↓
squash-and-merge when CI green, no conflicts, blockers cleared
        ↓
delete merged branch / worktree only if no leftover work
        ↓
sync local main to origin/main
```

Rules:

- do not develop directly on `main`;
- do not force-push `main`;
- keep each branch narrowly scoped;
- architecture changes must update the relevant documentation/ADR;
- generated code must pass deterministic tests rather than rely on an LLM review alone;
- default merge method is Squash and Merge;
- extend CI only when existing workflows do not already cover the change;
  prefer the path-based / docs-only skip behavior in [CI](CI.md);
- independent review is required for material or high-risk changes (execution,
  risk, secrets, LIVE gates, infra/runtime, non-trivial strategy/data
  contracts); trivial docs/chore PRs may skip it unless requested;
- no deployment occurs merely because a pull request was merged.

## Local data policy

Development uses:

- synthetic fixtures;
- captured and sanitized public-market samples;
- deterministic replay files;
- small local ClickHouse datasets;
- read-only exports from an approved runtime store where explicitly needed.

The full 24/7 dataset belongs on the VPS durable runtime store. Interactive
development should not mutate the runtime ClickHouse instance. Heavy research
can run as a controlled runtime research job or against a bounded exported
dataset.

Small fixtures suitable for reproducible tests may be committed. Large raw captures, secrets and personal trading/account data must not be committed.

## Secret policy in development

Development environments must not contain:

- a Hyperliquid master-wallet key;
- a live Hyperliquid agent key during normal development;
- Bitvavo/Kraken withdrawal credentials;
- host root/admin credentials in repository files;
- production database passwords in source or images.

Initial development requires no authenticated exchange credentials. Public market data and deterministic mocks are sufficient.

Where a later integration test genuinely needs a non-production secret, inject it through local protected secret storage or environment configuration excluded from Git. Never paste it into a Codex prompt, commit, frontend bundle or test fixture.

## Development versus runtime parity

Parity is achieved through containers, contracts and tests—not by editing
source inside running containers. Sharing a physical VPS does not merge the
source checkout, disposable development services and durable runtime volumes.

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

## Primary VPS bootstrap milestone

The development environment is ready when all of the following are true:

- Ubuntu 24.04 LTS is current and healthy;
- hostname is `chupa`;
- Cursor Remote SSH can open the repository;
- `agent --version` and `codex --version` succeed;
- repository and artifact roots match the primary paths above;
- Git/GitHub authentication works from the VPS;
- `docker run` and `docker compose` work on the VPS;
- repository version managers/package tools are available;
- no real trading credentials are present;
- a feature branch can be pushed and a pull request created.

## Official operational references

- Ubuntu release lifecycle:
  https://ubuntu.com/about/release-cycle
- Ubuntu Server documentation:
  https://ubuntu.com/server/docs/
- Netcup vServer images:
  https://www.netcup.com/en/server/vserver-images
- Docker Engine on Ubuntu:
  https://docs.docker.com/engine/install/ubuntu/
- Cursor CLI installation and `agent` command:
  https://cursor.com/docs/cli/installation.md
- Cursor-maintained Remote SSH extension announcement:
  https://forum.cursor.com/t/remote-ssh-unstable-while-vscode-is-stable/87621/1
- OpenAI Codex CLI quickstart:
  https://developers.openai.com/codex/quickstart
