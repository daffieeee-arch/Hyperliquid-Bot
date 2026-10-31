# Development Workflow

## Decision

Primary software development happens on the **Ubuntu VM on TrueNAS SCALE**
under **ADR-025**. The operator reported the TrueNAS placement; local
inspection observed Ubuntu **26.04.1 LTS** and KVM. This development move does
not approve a migrated capture or PAPER runtime. **ADR-024** and VPS-named
runbooks retain the earlier Netcup profile as historical operational reference.

Interactive source work stays separate from durable datasets and running
services. Release images and runtime promotion remain separate planned work.
See [migration status](runbooks/ubuntu-vm-migration-status.md).

TerraPC/WSL2 is a secondary operator environment, not the primary development
or capture host.

## Primary development station

Primary host:

- Ubuntu VM hosted on TrueNAS SCALE, as reported by the operator;
- observed Ubuntu 26.04.1 LTS / KVM;
- source under `$HOME/coding-projects/Hyperliquid-Bot`;
- Codex with feature branches and isolated worktrees;
- pinned project Python and Node.js tooling;
- Cursor/Remote SSH and disposable Docker services when needed and installed.

The VM OS and CI runner OS are separate choices. Use the repository-pinned
Python/Node versions rather than the VM system Python. Docker compatibility
and runtime prerequisites must be verified before introducing service
containers on this host.

## Interactive tools

Use Cursor Remote SSH when an editor is preferred. Use `agent` or `codex`
directly in the VM shell for terminal-first work. Run all tools from the
repository root unless a worktree is intentionally selected.

Recommended permission posture:

- use the workspace-scoped permission profile for ordinary implementation;
- retain approval prompts for commands outside the workspace or for network/system changes;
- do not use unrestricted/danger-full-access as the default;
- do not run routine development as `root`;
- use separate feature branches or Codex-managed worktrees for parallel tasks.

The primary paths are:

```bash
export REPO_ROOT="$HOME/coding-projects/Hyperliquid-Bot"
cd "$REPO_ROOT"
# Set ARTIFACT_ROOT and HIST_ARCHIVES_ROOT only to verified durable data roots.
```

Keep capture data outside Git. The previous `$HOME/Hyperliquid Project/`
layout was absent during the initial VM audit. Do not recreate an empty
archive root and treat it as a completed data migration. Pass verified roots
explicitly to existing helpers; legacy defaults still describe Netcup paths.

## Secondary TerraPC/WSL2 notes

TerraPC/WSL2 may be used for isolated fallback development, GPU research, or
the explicitly WSL-named operator runbooks. Keep any WSL checkout in its Linux
filesystem, not under `/mnt/c` or a network share. WSL examples are secondary
overrides; capture helper defaults still describe the legacy VPS layout.

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

Before installing Docker Engine, verify support for the actual VM release
using Docker's official Ubuntu installation guide. For secondary WSL use, Docker Desktop integration may be
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

Current repository controls:

- Python 3.13.15 pinned in `.python-version` (system Python 3.14 is outside the supported range);
- `uv` 0.12.5 pinned in `pyproject.toml`;
- Python dependencies managed with `uv` and `uv.lock`;
- Node.js 24.18.1 pinned in `.node-version`;
- `pnpm` 11.23.0 pinned through `packageManager` and lockfile;
- planned release-container bases must be pinned to reviewed versions/digests;
- no production dependency uses an unreviewed floating `latest` tag.

Pinned tools were restored user-locally after the initial migration audit.
Use `export PATH="$HOME/.local/bin:$PATH"` where needed. Locked dependencies
were installed in the workflow feature worktree, not the main checkout.
A passing local baseline still requires actual check results; see the
[migration status](runbooks/ubuntu-vm-migration-status.md) for before/after evidence.

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
separate Codex review agent checks final diff and test evidence
        ↓
extra security/quant review for high-risk changes
        ↓
resolve blocking findings; re-run affected tests, CI and review
        ↓
separate Codex merge agent verifies final head/base, CI and independent reviews
        ↓
explicit squash merge without branch-protection bypass
        ↓
delete merged branch / worktree only if no leftover work
        ↓
separately approved deployment; never implied by merge
```

Rules:

- do not develop directly on `main`;
- do not force-push `main`;
- keep each branch narrowly scoped;
- architecture changes must update the relevant documentation/ADR;
- generated code must pass deterministic tests rather than rely on an LLM review alone;
- default merge method is Squash and Merge;
- implementation agents hand off to a separate Codex merge agent after CI and
  independent review. Chupa authorizes routine merges through that agent without
  asking him to read code or confirm each merge. The merge agent must not have
  authored the change and uses an expected head SHA, without administrator bypass;
- **A separate Codex review agent reviews feature PRs.** It checks the full
  final diff, acceptance criteria and test evidence, and records head/base
  commit identities with its findings. It does not implement that same change;
- high-risk changes (execution, risk, secrets, CI, infra/runtime and non-trivial
  strategy/data contracts) also receive an appropriate security/quant review;
- Claude Code has no mandatory reviewer or merger role. LIVE/SHADOW/TESTNET, real capital,
  real credentials or live risk-limit changes still need explicit operator approval;
- follow [GitHub workflow](GITHUB_WORKFLOW.md) for review evidence and actual
  merge gates. GitHub Free is sufficient for the current public repository;
  no paid GitHub/Copilot reviewer is required;
- extend CI only when existing workflows do not already cover the change;
  prefer the change-set areas and docs-only skip behavior in [CI](CI.md);
- open a PR as a draft while review rounds still change it: a draft runs only
  the cheap checks, and marking it ready for review runs the full CI on its
  head, which must be green before merge;
- independent review applies to feature changes; keep review proportionate for
  trivial documentation/chore changes and honor an explicit review request;
- no deployment occurs merely because a pull request was merged.
- GitHub's unattended auto-merge stays disabled; the separate merge agent makes
  the explicit gated squash merge during an active session.

## Local data policy

Development uses:

- synthetic fixtures;
- captured and sanitized public-market samples;
- deterministic replay files;
- small local ClickHouse datasets;
- read-only exports from an approved runtime store where explicitly needed.

The full retained dataset belongs on a verified durable runtime store. Its
location and transfer to the VM remain unverified. Interactive
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
source inside running containers. Sharing a physical host does not merge the
source checkout, disposable development services and durable runtime volumes.

GitHub Actions Phase A, cockpit lint, and the PAPER API/browser regress
suite are documented in [CI](CI.md). Local checks stay the same: `uv run
pytest` (or, faster, `uv run pytest -m timing` followed by
`uv run pytest -n auto -m "not timing"`, as CI does), `pnpm run lint`,
`pnpm --filter @hyperliquid-bot/cockpit run test`.

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

## Ubuntu VM development bootstrap milestone

The development environment is ready when all of the following are true:

- the VM identity and source path match ADR-025;
- the selected editor/Codex can open the repository;
- Git/GitHub authentication works from the VM;
- pinned Python, uv, Node.js and pnpm versions are available;
- locked dependencies restore and relevant local checks pass;
- runtime data roots are treated as unverified until separately inspected;
- Docker prerequisites are checked when containers are actually needed;
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
