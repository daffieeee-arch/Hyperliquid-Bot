# COURSE-1: bounded live-public PAPER soak

Status: bounded public-data engineering proof. ADR-023 remains `WRAP`, not
`ADOPT`. This is not D22-B, not 24/7 collection, and not approval for signing,
TESTNET, SHADOW, or LIVE.

## Exact claim boundary

This slice proves that the existing D01 smoke strategy and smoke-risk path can
be fed by a short, credentialless Hyperliquid public BTC-PERP trade/BBO stream
and still write reconstructable sandbox-PAPER artifacts.

It does **not** prove:

- 24/7 collection or an always-on PAPER service;
- funding settlement (funding remains `0` under the D01 overlay);
- D22-B venue-authoritative crash-window reconciliation;
- durable venue orders, signing, wallets, TESTNET, SHADOW, or LIVE;
- mid-run recovery or exactly-once venue submission;
- profitability, edge, or strategy promotion.

## Working route

```text
Hyperliquid public MAINNET trades + BBO (BTC only)
  -> existing v2 trade decoder/adapter and a fail-closed BBO parse
  -> unchanged D01SmokeStrategy
  -> unchanged D01 evaluate_order_risk
  -> credentialless Nautilus 1.231.0 sandbox PAPER
  -> orders, fills, position, assumed USDC cash/equity/PnL
```

The committed D01 27-event fixture is not the soak input. Offline tests inject
public wire-format frames through the same decoder and PAPER driver. A live
`run` command may open `wss://api.hyperliquid.xyz/ws` with no credentials.

## Fail-closed boundaries

- `TRADING_MODE` must be unset or exactly `PAPER`.
- A conflicting internal D41 mode or any populated protected Hyperliquid key,
  vault, or account-address environment name prevents startup. Values are never
  read into artifacts or printed.
- Duration is an integer between 1 and 600 seconds. This is a soak bound, not a
  service lifetime.
- The only execution factory is `SandboxLiveExecClientFactory`. There is no
  Hyperliquid venue execution factory, signer, wallet, or authenticated client.
- Trade ticks re-enter the existing D01/D41 stale, future, gap, and precision
  adapter. Per-event stale, future, or off-grid public ticks are skipped; a soak
  with zero accepted trades fails closed. BBO prices must sit on the `0.1`
  increment and must not cross.
- A channel's data may arrive before the other channel is acknowledged; those
  frames are buffered and only enter the D01 path after both public ACKs.
  Data for a channel before its own ACK is rejected. The historical
  Hyperliquid greeting is accepted if present and is not required.
- Unexpected channels, HIP-3 coins, duplicate trade source IDs, and testnet
  sockets are out of scope and fail closed.
- Every run directory is create-only.
- After `paper.json` is written, the soak also writes create-only Cockpit projections:
  `paper-position.json`, `paper-pnl.json`, `orders.json`, `fills.json`, and
  `capture-health.json`. Those files copy assumed overlay economics and observed stream
  health. They do not invent PnL or claim 24/7 service.

## Outcomes

| Status | Meaning |
| --- | --- |
| `COMPLETED_FLAT` | The unchanged smoke lifecycle entered and flattened on public ticks |
| `RISK_REJECTED` | The unchanged D01 smoke-risk rule blocked an intent; no venue order exists |
| `BOUNDED_TIMEOUT` | The duration ended before a complete flat lifecycle |

Live BTC prices can exceed the fixed D01 smoke-loss budget (`0.25 USDC` at
`0.00013 BTC` and a 2% stop-distance proxy). That is a documented fail-closed
outcome, not a reason to loosen risk.

## Offline validation

Build the same isolated environment as the D01 publication gate. Root
`pyproject.toml` and `uv.lock` stay unchanged:

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

"$SOAK_PYTHON" -m unittest -v \
  vertical_slices.course1_live_public_paper.test_slice
```

A live public soak is optional and create-only:

```bash
export SOAK_RUN_DIR=/tmp/course1-live-public-paper-manual
test ! -e "$SOAK_RUN_DIR"

"$SOAK_PYTHON" -m vertical_slices.course1_live_public_paper.run_slice run \
  --run-id live-public-manual \
  --artifact-dir "$SOAK_RUN_DIR" \
  --seconds 45

"$SOAK_PYTHON" -m vertical_slices.course1_live_public_paper.run_slice verify \
  --artifact-dir "$SOAK_RUN_DIR"
```

Do not commit live capture artifacts.

## Cockpit path contract

Cockpit should later read create-only files from:

```text
<artifact-root>/course1/live-public-paper/<run_id>/
  run-claim.json
  paper-position.json
  paper-pnl.json
  orders.json
  fills.json
  capture-health.json
```

`paper.json`, `public-stream.json` and `completed-run.json` remain the soak verification
bundle. `paper-pnl.json` is assumed overlay economics (`funding_payment_usdc` is `0`),
not venue PnL. A synthetic empty-state sample lives at
`tests/fixtures/course1_cockpit/sample-run/`.

The helper `course1_cockpit_paths(artifact_root, run_id)` is the executable contract.
`--artifact-dir` may still point at any create-only directory; the helper is how Cockpit
should construct the recommended path.

## Honest remaining COURSE-1 work

D22-B remains blocked and unimplemented. The definitive runtime ADR and any
later 24/7 PAPER host work remain later, separately authorized scope. Schema v3
and Phase 1A-3B1C-2+ stay dormant. No additional DATA-1 venue is added here.
