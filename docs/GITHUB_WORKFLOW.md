# GitHub Free development workflow

This project uses GitHub Free with a public, user-owned repository. Copilot code
review, paid runners and an OpenAI API key are not prerequisites. Codex work uses
the operator's existing Codex access and usage allowance.

## Roles and review evidence

The coordinator owns scope, acceptance criteria and integration. Implementation
agents work on bounded tasks in separate worktrees. An independent reviewer must
not have authored the code being reviewed. Give it the original request, full
final diff, relevant contracts and actual test results.

A separate merge agent checks the final evidence and performs routine merges.
The operator has authorized this role; he is not expected to read code or approve
each ordinary project merge. The merge agent must not have authored the change.

Every feature PR records:

- reviewer task/identity and reviewed head/base commit IDs;
- findings with file references and evidence;
- fixes or explicit reasons for declining each finding;
- the final review result after follow-up changes.

Execution, risk, secrets, CI and runtime changes receive an additional security
review. Research changes receive a point-in-time, costs/funding and holdout check.
The specialist may be scheduled after other work finishes; nine simultaneous
agents are not required. Claude Code is not a required reviewer or merger.

Subagents have separate contexts but share the host and configured capabilities.
They are not separate GitHub users or isolated authorization boundaries. Their
review report is evidence for the merge agent, not a native GitHub approval.

## Delivery sequence

1. Open or select an issue with scope, acceptance criteria and dependencies.
2. Confirm clean Git state, fetch `origin/main`, create a feature branch/worktree.
3. Implement narrowly; add deterministic tests when behavior changes.
4. Push and open a draft PR targeting `main`, linked to its issue.
5. Run local checks, address findings, and mark the PR ready with a user or
   connected app identity that triggers the `ready_for_review` workflows.
6. Verify the required CI checks really ran for this ready PR and current head.
   Unaffected-area skips are valid; skipping required tests because the PR was a
   draft is not ready-to-merge evidence.
7. Complete independent review and any specialist security/quant review. New
   changes invalidate the review of the changed code; revalidate the base after
   another PR merges.
8. Hand off to a separate Codex merge agent. It verifies the actual final
   head/base, independent reviews, resolved blocking findings, a non-draft
   mergeable PR and all required checks from their expected publishers. It then
   squash-merges with an explicit expected head SHA, without administrator
   bypass or another routine human confirmation.
9. Delete merged branches/worktrees only after checking for leftover work.

Merge never starts collectors, deploys an image or promotes trading modes. LIVE,
SHADOW, TESTNET, real credentials and live risk-limit changes retain their explicit
human-approval requirements.

## GitHub controls

Keep the existing required Python, TypeScript, D01, cockpit, dependency-review,
workflow-lint and secret-scan checks. Preserve the area selection and docs-only
behavior in [CI.md](CI.md), and the draft cost controls. The new CI gate must
reject failed classification, draft-era skips, stale PR identity, and missing,
failed or cancelled required jobs. The separate Cockpit check stays required.

Main protection requires a PR, an up-to-date branch and resolved conversations,
and rejects force pushes and branch deletion. Apply checks to administrators too.
Use squash merging. Do not add a required check before a successful run has
established its real name/source; do not remove old checks before replacement
coverage is demonstrated.

GitHub CodeQL default setup analyzes Python, JavaScript/TypeScript and Actions on
GitHub-hosted runners. Dependabot version/security updates, dependency review,
GitHub secret scanning/push protection, Gitleaks and SHA-pinned actions remain in
place. PR workflows do not run on the runtime VM, read durable datasets, or receive
exchange credentials. Do not execute untrusted PR code in a privileged publisher
workflow. Keep scan findings and checks visible on the PR.

## Review enforcement limitation

The free baseline does not claim a technically independent AI approval gate.
The author cannot approve their own PR through a second subagent using the same
GitHub account. An author-controlled label, comment or success status cannot
establish that independence. Keep GitHub's unattended auto-merge disabled. The
separate merge agent is authorized to check the actual reviews and CI, then
explicitly squash-merge; this is a role separation within the agent workflow,
not a separately enforced GitHub identity boundary. It operates while a session
runs and does not claim continuous background monitoring.

An independently controlled GitHub App/check publisher could enforce a separate
review result later. Its credentials and trusted publishing code must be outside
the implementer's reach, and its evidence must identify the exact reviewed head
and base. That integration is not created or assumed by these documents. Do not
set an impossible native required-approval count that deadlocks a solo maintainer
without first provisioning an eligible reviewer identity.

Codex's GitHub integration can provide another review through `@codex review`
when the repository is connected and review enabled in Codex settings. It uses
Codex entitlements, not a paid GitHub Copilot subscription. Availability and
settings must be verified; a review comment alone is not a required approval.
Do not add an API-key-powered review Action as a supposedly free substitute.

## Planning and follow-up work

Use issue and PR templates for acceptance criteria, validation and review evidence.
An optional Project board uses Backlog, Ready, In progress, Review and Done. If the
connected identity lacks Projects scopes, issues remain the source of truth;
do not silently change authentication or create a second tracker.

The setup session could not access Projects with its current permissions. The
tracked issues use `status:in-progress`, `status:blocked`, `status:ready` and
priority labels instead; no board or broader token permission is assumed.

The immediate order after the VM move is:

1. Verify the durable data location and migration before collecting or downloading
   replacement data. See [migration status](runbooks/ubuntu-vm-migration-status.md).
2. Continue existing S4 draft PR #146 with its own metadata/shape-only helper.
   Do not duplicate its studies, score strategies before registration, or silently
   change its source/data pins. Its holdout market is already known.
3. Design and test a bounded projection from the native PAPER engine to the
   cockpit. Existing COURSE-1 files and the native engine's ledger/state files
   are different contracts; multiasset funding settlement is not implemented.

Tracked delivery items:

| Item | Issue |
|---|---|
| GitHub Free, CI and independent-review baseline | [#148](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/148) |
| Durable-data migration verification | [#149](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/149) |
| Existing S4 draft readiness, blocked on data | [#150](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/150), [PR #146](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/146) |
| Bounded native PAPER projection | [#151](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/151) |

Container releases, GHCR, SBOMs and verified artifact attestations follow a real
deployable artifact. They are not prerequisites for this source/CI work. Standard
public-repository runner time is free; artifact/package storage and nonstandard
runners have separate billing rules. Keep artifacts small and retention bounded.

## Official references

- [GitHub Free and public repository features](https://docs.github.com/en/get-started/learning-about-github/githubs-plans)
- [Rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- [Code scanning](https://docs.github.com/en/code-security/concepts/code-scanning/code-scanning)
- [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Codex GitHub reviews](https://learn.chatgpt.com/docs/third-party/github)
