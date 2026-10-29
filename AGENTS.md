# AGENTS.md

## Project

This repository builds a professional multi-strategy crypto quantitative research and trading platform.

Primary development environment:

- Ubuntu virtual machine on TrueNAS SCALE (Ubuntu 26.04.1 LTS observed);
- Cursor IDE Remote SSH, Cursor CLI (`agent`) and Codex CLI;
- repository at `$HOME/coding-projects/Hyperliquid-Bot`;
- TerraPC/WSL2 only as a secondary operator environment.

24/7 runtime boundary:

- host-neutral Linux/amd64 OCI runtime;
- **ADR-025** records the development move; durable data and continuous PAPER
  runtime migration require separate verification;
- **ADR-024** remains the historical Netcup reference, not evidence of VM deployment;
- reviewed CI-built Linux container images are the deployment target;
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
   redundant, or unnecessarily heavy checks. Prefer the change-set areas and
   docs-only skip already defined in `docs/CI.md`.
7. Commit and push the branch.
8. Open a pull request targeting `main`. Open it as a draft while review
   rounds still change it: a draft skips the heavy CI jobs, and marking it
   ready for review runs them on the current head (`docs/CI.md`).
9. Let all required CI checks complete successfully.
10. **A separate Codex reviewer is standard** for feature PRs. The coordinator
    may delegate implementation, but the reviewer must not have authored the
    changes it reviews. Give it the original acceptance criteria, full final
    diff, relevant contracts and test evidence, not just the implementer's
    summary. Record the reviewed head/base commit IDs, findings and their
    disposition in the PR. Fix verified bugs on the same branch and review
    changed code again after follow-up commits. Claude Code is not a required
    reviewer or merger. Trivial docs/chore PRs may use a proportionate review.
11. For material or high-risk changes (execution, risk, secrets, LIVE gates,
    infra/runtime, non-trivial strategy/data contracts), also obtain any
    additional independent review the user requests before merge.
12. Resolve blocking review findings on the same branch; re-run the relevant
    tests and CI.
13. Cursor Cloud Agents and Codex stop at Ready: they open PRs and fix CI and
    review findings only, and do **not** merge feature PRs.
14. The default merge method is squash. Merge only through an authorized
    maintainer after current-head CI, independent review and resolved blocking
    findings. Consecutive PRs must be revalidated against the new `main`.
    Chupa does not read code: deterministic checks and review evidence must
    be sufficient for the decision. Execution, risk, secrets, CI and runtime
    changes also receive a separate security review. A subagent sharing the
    author's GitHub identity is not a native approving reviewer. Never fake
    approval with an author-controlled label, comment or green status. GitHub
    Free is the baseline; paid Copilot review or an OpenAI API key is not a
    prerequisite. See `docs/GITHUB_WORKFLOW.md` for enforcement limitations.
    A change enabling LIVE, SHADOW or TESTNET trading, moving real capital,
    or changing real credentials or live risk limits requires Chupa's explicit
    approval. A merge never deploys. Do not enable auto-merge without a
    separately authorized merge policy and a working review gate.
15. After a merge, delete the merged feature branch and any associated
    worktree only after confirming they hold no uncommitted or unmerged work.
    Runtime operations stay with the operator (Chupa) or an explicitly
    assigned runtime agent. Synchronize only the intended host checkout when
    deployment is needed, after verifying its data mounts and runtime role.
    Older documents that say "CoS assigns" or "CoS runs" mean the operator.

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

Local development uses fixtures, replay and bounded exports. Continuous data belongs on a separately verified durable runtime store outside Git.

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

- DEV runs on the Ubuntu VM on TrueNAS SCALE; disposable Docker services and
  WSL2 may be used for isolated local work when available.
- CI tests source; immutable image build/release remains a separate delivery step.
- The approved Linux/OCI runtime host pulls image digests and owns persistent runtime volumes.
- **ADR-025** records the development move. **ADR-024** retains the historical
  Ubuntu LTS VPS + Linux/amd64 OCI reference; it does not certify VM runtime readiness.
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

## Review guidelines

- Verify PAPER fail-closed startup, no venue calls from strategy code, and no
  signing, credential or durable-data changes hidden in tooling work.
- For research/data changes, check point-in-time availability, units,
  instrument identity, funding/cost treatment and holdout boundaries. Respect
  existing experiment code pins and do not silently re-register a failed study.
- For CI changes, check that required tests really ran for the current ready
  PR, failures cannot become successful skips, and untrusted PR code never
  executes with publisher credentials or access to the runtime VM.
- Report correctness and security findings with a reproducible example or
  precise code path. Mechanical format/lint checks belong in CI.
