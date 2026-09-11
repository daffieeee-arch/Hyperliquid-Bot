# Continuous integration

PAPER-only GitHub Actions. This repository extends the existing
`.github/workflows/ci.yml` and `.github/workflows/cockpit.yml` workflows. There
is no parallel CI stack, no paid runner, and no LIVE/wallet/signing job.

## Phase A controls

Both workflows set:

- `concurrency` per workflow + PR/ref with `cancel-in-progress: true`;
- `permissions: contents: read`;
- SHA-pinned third-party actions, matching the existing checkout / setup-uv /
  pnpm style.

`ci.yml` also:

- caches the uv download/install (`enable-cache: true`);
- shares Node 24.18.1 + pnpm 11.23.0 setup through
  `.github/actions/setup-node-pnpm` (pnpm store cache on);
- runs `dependency-review` on `pull_request` (public repo, GitHub-native);
- runs `actionlint` on every workflow (cheap, SHA-checked binary);
- classifies the change set so **docs-only** PRs skip heavy jobs while the job
  names stay green (required-check safe). Full CI always runs when
  `.github/**`, `src/**`, `apps/**`, `tests/**`, `vertical_slices/**`,
  `fit_gates/**`, `scripts/**`, lockfiles, or Python/TypeScript manifests
  change.

`secret-scan` always runs, including on docs-only PRs. It is never skipped on
PRs that touch code.

## Cockpit lint

`pnpm run lint` (CI job `typescript-foundation`) now includes `apps/cockpit/src`.
`cockpit.yml` job `first-paper-screen` also runs `pnpm --filter @hyperliquid-bot/cockpit run lint`
(`pnpm run lint:cockpit`). The job name is unchanged.

## PAPER regress suite

Job **`regress`** in `ci.yml` is the focused API/browser suite (< ~8 min extra):

- FastAPI `/health` + `/ready` PAPER smoke and LIVE fail-closed;
- official Binance USD-M `bookTicker` `/public` vs `/market` contract, including
  mutated fixtures that must raise;
- operator-stop isolation (no hardcoded `tmux send-keys` to live session names);
- Playwright Chromium against `next start` + committed COURSE-1 fixtures.
  Public HL routes are stubbed; no live exchange calls.

## Required check names for CoS / branch protection

Existing names (unchanged):

| Check | Workflow / job |
|---|---|
| `python-foundation` | CI |
| `d01-publication` | CI |
| `typescript-foundation` | CI |
| `secret-scan` | CI |
| `first-paper-screen` | Cockpit |

New names (add if CoS requires them):

| Check | When it runs | Notes |
|---|---|---|
| `changes` | every CI / Cockpit run | cheap path classifier; not a quality gate |
| `dependency-review` | `pull_request` only | skipped on `push` to `main` |
| `actionlint` | every CI run | workflow YAML |
| `regress` | every CI run except docs-only skip-success | API + browser regress |

Docs-only PRs still report `python-foundation`, `d01-publication`,
`typescript-foundation`, `regress`, and `first-paper-screen` as **success**
(early skip step). Do not require `dependency-review` on `push` to `main`.

## D01 publication hash

`.github/workflows/ci.yml` is part of the D01 publication integrity set.
Changing that file requires updating
`vertical_slices/d01_btc_perp/publication-integrity-d01-publication-proof-20260830.json`.
COURSE-1 exit-gate hashes the current bytes; it does not freeze them.

## Regression mapping (how the suite fails if a past bug returns)

See the PR description for the same table. Summary:

1. **#52 Binance USD-M `bookTicker` on `/market`** —
   `require_usdm_combined_stream_split` raises `UsdmStreamContractError`.
   Mutated fixtures in `tests/python/test_ci_regression_contracts.py` expect
   that failure. Import of `binance_public_research` also runs the check on
   the committed URL constants.
2. **Live mid as research truth** —
   `parsePanelSummary` throws on `live_mid` / `research_truth` /
   `public_mid_is_research`. Playwright asserts the rendered
   `public mid ≠ research truth` copy.
3. **DESK / #65 / `risk_based_size`** —
   Vitest + Playwright require a visible `REJECT` row labeled
   `#65/paper_risk · risk_based_size` on the soak fixture tape.
4. **`assumed_pnl` operator precision / not venue PnL** —
   Display stays `-0.0127 USDC` with exact `-0.0126583080 USDC` and
   `not venue-reconciled`. Relabeling as venue PnL fails the view contract.
5. **OPERATOR_STOP / live tmux names** —
   Stop scripts and pytest sources must not contain
   `tmux send-keys -t hl-capture` (or `bn-` / `bv-` / `kr-capture`). A mutated
   helper fixture expects `OperatorStopIsolationError`. Tests/CI must use
   isolated fake session names and become a no-op when a live name is
   targeted (`TEST_ISOLATION_NOOP`). No `pkill` / `killall` /
   `tmux kill-server`.
