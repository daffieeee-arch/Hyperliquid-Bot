# COURSE-1 runbook — live-public PAPER soak → Cockpit JSON

Status: operator runbook for the existing soak CLI from PR #30 / #32.
This is **not** D22-B, not LIVE trading, and not a 24/7 PAPER service.

`live` here means live **public market data**. Execution stays fail-closed
`PAPER` sandbox. No keys, no signing, no Binance, no extra venues.

## Duration contract

The COURSE-1 soak accepts **1–600 seconds**. That soak bound is **not** the
DATA-1A retained-capture contract (1–604800 seconds). Do not raise the soak
cap in this runbook.

## Path contract

Cockpit should later read:

```text
<artifact-root>/course1/live-public-paper/<run_id>/
  run-claim.json
  paper-position.json
  paper-pnl.json
  orders.json
  fills.json
  capture-health.json
```

The soak also writes `paper.json`, `public-stream.json`, and
`completed-run.json` for verify. Helper: `course1_cockpit_paths(artifact_root,
run_id)`.

`--artifact-dir` still points at one create-only directory. Construct that
directory from the helper / path contract above.

## Isolated WRAP environment

Root `pyproject.toml` / `uv.lock` stay unchanged. ADR-023 remains `WRAP`.

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:.
export TRADING_MODE=PAPER
export SOAK_VENV=/tmp/course1-live-public-paper-venv
test ! -e "$SOAK_VENV"

uv venv --no-project --python 3.13.15 "$SOAK_VENV"
uv pip sync --no-cache --strict \
  --python "$SOAK_VENV/bin/python" \
  fit_gates/d41_nautilus/requirements.lock
uv pip check --python "$SOAK_VENV/bin/python"
export SOAK_PYTHON="$SOAK_VENV/bin/python"
```

## Live-public PAPER soak

```bash
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-paper"
export SOAK_RUN_DIR="${ARTIFACT_ROOT}/course1/live-public-paper/${RUN_ID}"
test ! -e "$SOAK_RUN_DIR"

"$SOAK_PYTHON" -m vertical_slices.course1_live_public_paper.run_slice run \
  --run-id "${RUN_ID}" \
  --artifact-dir "${SOAK_RUN_DIR}" \
  --seconds 180

"$SOAK_PYTHON" -m vertical_slices.course1_live_public_paper.run_slice verify \
  --artifact-dir "${SOAK_RUN_DIR}"
```

Fail-closed startup:

- `TRADING_MODE` must be unset or exactly `PAPER`
- any populated protected Hyperliquid key / vault / account-address name refuses start
- `TESTNET` / `SHADOW` / `LIVE` refuse start
- existing `--artifact-dir` refuses reuse
- D01 smoke-risk plus `paper_risk` sizing/portfolio hard limits can `RISK_REJECTED`
  an oversize or over-limit intent; no venue order exists

`paper-pnl.json` copies the assumed D01 overlay. Funding remains `0`. Empty
orders/fills are honest. Do not invent PnL.

## Cloud-agent evidence used by this PR

| Field | Value |
| --- | --- |
| `artifact-root` | `/workspace/var/reconstructable` |
| `run_id` | `20260904t001800z-live-paper` |
| soak seconds | `180` requested; smoke lifecycle flattened first (`COMPLETED_FLAT`) |
| start UTC | `2026-09-04T00:20:12Z` |
| artifact-dir | `/workspace/var/reconstructable/course1/live-public-paper/20260904t001800z-live-paper` |
| verify | `VERIFIED` |

Committed Cockpit JSON lives in `tests/fixtures/course1_cockpit/live-public-soak/`.
Do not commit a second raw public capture from the soak socket.

## Explicitly not proven

- 24/7 PAPER or always-on collection
- D22-B venue-authoritative crash-window reconciliation
- funding settlement
- signing, wallets, TESTNET, SHADOW, LIVE
- profitability or strategy promotion
