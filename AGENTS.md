# AGENTS.md

## Project

This repository builds a professional multi-strategy crypto quantitative research and trading platform.

Primary development environment:

- Netcup Ubuntu 24.04 LTS VPS (hostname `chupa`);
- Cursor IDE Remote SSH, Cursor CLI (`agent`) and Codex CLI;
- repository at `$HOME/Hyperliquid Project/Hyperliquid-Bot`;
- TerraPC/WSL2 only as a secondary operator environment.

24/7 runtime boundary:

- host-neutral Linux/amd64 OCI runtime;
- the Netcup Ubuntu 24.04 LTS VPS is the definitive primary PAPER, capture and
  development profile (**ADR-024**);
- CI-built Linux container images;
- PAPER first, later SHADOW and explicitly approved LIVE.

Read `README.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT.md`, `docs/DEPLOYMENT.md`, `docs/ROADMAP.md`, `docs/SECURITY.md` and the relevant domain document before significant changes.

## Non-negotiable rules

- `PAPER` is the default and initial permitted trading mode.
- Local development must fail closed if asked to use an unapproved live mode.
- Never add, request, print, log or commit real private keys or exchange credentials.
- The Hyperliquid master-wallet key must never reside on any development or runtime host, container, or GitHub.
- Never expose withdrawal or transfer permissions to the bot.
- Strategy code must not call venue-specific APIs directly.
- LLMs may propose strategies and code; deterministic tests/data decide correctness and performance.
- No martingale or uncontrolled averaging down.
- Leverage follows risk-based sizing and hard limits.
- No strategy or agent self-promotes to LIVE.
- Do not edit source inside running runtime containers.
- Do not deploy floating `latest` tags.
- Do not mutate durable runtime data from ordinary development tasks.

## Development workflow

Apply this ship workflow automatically to every development task unless the
user explicitly narrows the scope (for example docs-only advice with no
repository change):

1. Never work directly on `main`.
2. Start each new task on a separate feature branch from the latest
   `origin/main` (fetch first; use a worktree when parallel tasks require it).
3. Before starting, confirm Git status is clean and synchronized so no
   existing work can be lost (clean tree, no unexpected uncommitted files,
   local/remote refs understood).
4. Execute the task narrowly: no unrelated refactors, architecture changes, or
   out-of-scope expansions.
5. Add or update deterministic tests when behavior changes, and run the
   relevant local format/lint/typecheck/test commands.
6. For every change, judge whether existing CI already covers the new or
   changed behavior. Extend CI only when truly needed; avoid duplicate,
   redundant, or unnecessarily heavy checks. Prefer the docs-only / path-based
   skip paths already defined in `docs/CI.md`.
7. Commit and push the branch.
8. Open a pull request targeting `main`.
9. Let all required CI checks complete successfully.
10. For material or high-risk changes (execution, risk, secrets, LIVE gates,
    infra/runtime, non-trivial strategy/data contracts), obtain an independent
    review from a separate agent/model before merge. Skip independent review
    for trivial docs/chore PRs unless the user asks.
11. Resolve blocking review findings on the same branch; re-run the relevant
    tests and CI.
12. Merge only when CI is green, the PR has no merge conflicts, and all
    blocking issues are resolved.
13. Use Squash and Merge by default.
14. After merge, delete the merged feature branch and any associated worktree,
    but only after confirming they hold no uncommitted or unmerged work.
15. Synchronize local `main` with `origin/main` again.

Keep the workflow practical: add extra steps, tests, or CI only when the
nature of the change actually requires them.

Also for each task: inspect relevant documentation and current code; state
assumptions and scope; review the diff for secrets and architecture
violations; report what changed, tests run, remaining risks and readiness
stage. Never deploy merely because CI passes or a PR merged.

## Language and style

- Python for data, research, strategies, portfolio, risk and execution.
- TypeScript/React/Next.js for the cockpit.
- Prefer typed domain models and explicit interfaces.
- Prefer simple, testable, deterministic implementations over clever abstractions.
- Use clear names and short functions with explicit error handling.
- Comments explain intent, invariants or non-obvious market/execution behavior—not restate code.
- Documentation and code identifiers are English.

## Data and research

Every quantitative experiment must define:

- hypothesis;
- universe;
- point-in-time data requirements;
- train/validation/untouched OOS periods;
- fees, funding, spread and slippage assumptions;
- fill model;
- benchmarks;
- robustness/stress tests;
- result limitations.

Never claim profitability from in-sample results alone. Avoid look-ahead, survivorship, selection and execution bias.

Local development uses fixtures, replay and bounded exports. Continuous data belongs on the VPS durable runtime store.

## Execution and risk

All broker modes implement the same strategy/risk contracts. The execution path must eventually support idempotency, partial fills, reconciliation, restart recovery, stale-data guards, kill switches and complete auditability.

Every order/trade record must be attributable to:

- environment;
- venue;
- strategy/configuration version;
- source commit;
- image digest;
- correlation/client order ID.

Browser code and Grafana never sign orders.

## Infrastructure boundaries

- DEV runs on the Netcup Ubuntu 24.04 LTS VPS; disposable Docker services and
  WSL2 remain available for isolated local work.
- CI builds/tests images.
- The approved Linux/OCI runtime host pulls image digests and owns persistent runtime volumes.
- **ADR-024** records the definitive PAPER runtime profile (Ubuntu LTS VPS + Linux/amd64 OCI).
- Redis and PostgreSQL are introduced only when their defined responsibilities are required.
- Grafana dashboards and alerts should be version-controlled even when edited through MCP.

## MCP and administrative actions

Useful project-scoped operations are allowed when explicitly part of the task. High-blast-radius actions require explicit human approval, including:

- deleting datasets/snapshots/databases;
- DROP/TRUNCATE/destructive migrations;
- storage pool/topology changes;
- reboot/shutdown/update;
- destructive ACL/share changes;
- deleting unrelated Grafana assets;
- force-pushing or rewriting `main`;
- enabling LIVE or changing live credentials/risk limits.

Never reveal MCP credentials or secret values.

## Definition of done

A task is not done merely because code was generated. It is done when:

- acceptance criteria are met;
- relevant tests pass;
- typing/linting passes;
- no secret or live-mode regression is introduced;
- documentation/contracts are consistent;
- deployment/runtime implications are stated;
- unresolved risks are explicit.
