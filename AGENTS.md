# AGENTS.md

## Project

This repository builds a professional multi-strategy crypto quantitative research and trading platform.

Primary development environment:

- Windows 11 workstation;
- Codex/ChatGPT desktop;
- Codex agent running in WSL2 Ubuntu;
- repository stored inside the WSL Linux filesystem.

24/7 runtime boundary:

- host-neutral Linux/amd64 OCI runtime;
- a supported Ubuntu LTS VPS is the intended primary deployment profile after the local vertical slice;
- the existing TrueNAS SCALE environment remains an optional profile;
- CI-built Linux container images;
- PAPER first, later SHADOW and explicitly approved LIVE.

Read `README.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT.md`, `docs/DEPLOYMENT.md`, `docs/ROADMAP.md`, `docs/SECURITY.md` and the relevant domain document before significant changes.

## Non-negotiable rules

- `PAPER` is the default and initial permitted trading mode.
- Local development must fail closed if asked to use an unapproved live mode.
- Never add, request, print, log or commit real private keys or exchange credentials.
- The Hyperliquid master-wallet key must never reside on Windows/WSL2, any runtime host or container (including TrueNAS), or GitHub.
- Never expose withdrawal or transfer permissions to the bot.
- Strategy code must not call venue-specific APIs directly.
- LLMs may propose strategies and code; deterministic tests/data decide correctness and performance.
- No martingale or uncontrolled averaging down.
- Leverage follows risk-based sizing and hard limits.
- No strategy or agent self-promotes to LIVE.
- Do not edit source inside running runtime containers.
- Do not deploy floating `latest` tags.
- Do not mutate TrueNAS runtime data from ordinary Windows development tasks.

## Development workflow

For each implementation task:

1. inspect the relevant documentation and current code;
2. state assumptions and scope;
3. work on a feature branch or worktree, never directly on `main`;
4. keep changes narrowly scoped;
5. add/update deterministic tests;
6. run relevant format, lint, typecheck and test commands;
7. review the diff for secrets and architecture violations;
8. report what changed, tests run, remaining risks and readiness stage;
9. open a pull request only when requested or when the task explicitly includes it;
10. never deploy merely because CI passes.

Do not make unrelated refactors during a focused task.

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

Local development uses fixtures, replay and bounded exports. Continuous data belongs on the selected durable runtime store. Existing TrueNAS/ClickHouse data remains protected until an approved migration exists.

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

- DEV runs in WSL2 and disposable local Docker services.
- CI builds/tests images.
- The approved Linux/OCI runtime host pulls image digests and owns persistent runtime volumes.
- VPS provisioning, migration and the definitive runtime ADR follow only after the local vertical slice.
- Redis and PostgreSQL are introduced only when their defined responsibilities are required.
- Reuse the existing TrueNAS ClickHouse safely; never initialize or overwrite it without explicit migration/backup approval.
- Grafana dashboards and alerts should be version-controlled even when edited through MCP.

## MCP and administrative actions

Useful project-scoped operations are allowed when explicitly part of the task. High-blast-radius actions require explicit human approval, including:

- deleting datasets/snapshots/databases;
- DROP/TRUNCATE/destructive migrations;
- TrueNAS pool/topology changes;
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
