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
- classifies the change set into the **areas** it can affect, so a PR runs
  only the heavy jobs of those areas while every job name stays green
  (required-check safe). See [Change-set areas](#change-set-areas).

`secret-scan`, `actionlint` and `dependency-review` always run, on every PR
event including drafts and docs-only change sets.

## Change-set areas

`src/hyperliquid_bot/ci_scope.py` (the `changes` job of both workflows) emits
two flags:

| Flag | Heavy jobs it gates |
|---|---|
| `python` | `python-foundation`, `d01-publication`, the Python step of `regress` |
| `typescript` | `typescript-foundation`, the browser steps of `regress`, Cockpit `first-paper-screen` |

A change set sets a flag when any changed path can change a job of that area.
The tables in `ci_scope.py` come from what each job actually reads:

- **Python**: `src/**`, `tests/python/**`, `vertical_slices/**`, `fit_gates/**`,
  `config/**`, `scripts/**`, `uv.lock`, `.python-version`; any `*.py`, `*.pyi`,
  `*.ipynb` or Python tool config (`pyproject.toml`, `conftest.py`,
  `ruff.toml`, ...) anywhere; any Markdown outside `docs/` (`ruff format`
  checks the Python code blocks in it); and the docs Python tests read:
  `docs/DATA.md`, `docs/runbooks/**`, `docs/research/examples/**`,
  `docs/experiments/**`.
- **TypeScript**: `apps/cockpit/**`, `tests/typescript/**`, `.prettierignore`,
  `.node-version`, and any `*.ts`/`*.js`-family file or TypeScript tool config
  (`package.json`, `pnpm-lock.yaml`, `tsconfig*.json`, `eslint.config.*`,
  `vitest.config.*`, ...) anywhere.
- **Both**: `.github/**` (prettier checks the workflows; the D01 manifest
  hashes `ci.yml`), `tests/fixtures/**` (shared by Python tests and the
  cockpit), `ci_scope.py` itself, `.editorconfig`, and `.gitignore`,
  `.ignore`, `.gitattributes` at any depth.
- **Neither**: every other file under `docs/`. A change set of only those
  skips every heavy job (docs-only).
- **Unknown** paths set both.

`tests/python/test_ci_scope.py` fails if a Python test starts reading a doc
the tables treat as neutral, and if a workflow reads an output the classifier
does not write. The diff is taken with `--no-renames`, so moving a file out of
an area counts for that area too.

**Draft pull requests** set neither flag: push work in progress to a draft
PR without paying for heavy CI. Both workflows also trigger on
`ready_for_review`, so marking the PR ready runs the full selection on the
same head. Wait for that run before merging; a draft's skipped jobs report
success.

**Fail closed**: every gated job has `if: ${{ !cancelled() }}` and its steps
run unless the flag is exactly `false`. If the `changes` job fails, its
outputs are empty and every heavy step runs, instead of the jobs reporting a
skipped success. A failed diff inside `ci_scope.py` also runs everything.
Pushes to `main` always run everything.

## Python tests in parallel

`python-foundation` runs the suite with pytest-xdist over the runner's cores:

```bash
uv run --frozen pytest -q -n auto --dist worksteal -m "not timing"
uv run --frozen pytest -q -m timing
```

The `timing` marker (registered in `pyproject.toml`) names tests that assert
a real-clock deadline with only tens of milliseconds of slack. They run
alone, after the parallel pass, so CPU contention between workers cannot
flake them. Mark a new test `timing` when it asserts a wall-clock budget
under about half a second. Tests share no fixed paths, environment or ports
(each worker is a process; use `tmp_path` and `monkeypatch`), so any other
test may run in any worker in any order.

Test fixtures that build DuckDB tables use `tests/python/duckdb_rows.py`
(`insert_rows`): it loads rows through one CSV `COPY`, the same table as
`executemany` in milliseconds instead of seconds.

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

## Required check names for merge / branch protection

Existing names (unchanged):

| Check | Workflow / job |
|---|---|
| `python-foundation` | CI |
| `d01-publication` | CI |
| `typescript-foundation` | CI |
| `secret-scan` | CI |
| `first-paper-screen` | Cockpit |

New names (add if branch protection requires them):

| Check | When it runs | Notes |
|---|---|---|
| `changes` | every CI / Cockpit run | cheap path classifier; not a quality gate |
| `dependency-review` | `pull_request` only | skipped on `push` to `main` |
| `actionlint` | every CI run | workflow YAML |
| `regress` | every CI run except docs-only skip-success | API + browser regress |

A job whose area is not affected (docs-only, the other area only, or a
draft PR) still reports `python-foundation`, `d01-publication`,
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
