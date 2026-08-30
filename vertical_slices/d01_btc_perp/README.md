# D01: local BTC-PERP replay-to-sandbox-PAPER slice

Status: bounded local product proof. This is **WRAP**, not **ADOPT**. It is not
production code, alpha evidence, deployment evidence, or approval for signing,
TESTNET, SHADOW, or LIVE.

## Exact claim boundary

D01 proves only one local deterministic replay-to-credentialless-sandbox-PAPER
route for the committed, bounded BTC-PERP dataset. It does not prove a
live-public-data soak, durable persistence, mid-run recovery, exactly-once venue
submission, venue reconciliation, deployment readiness, or profitability.

## Working route

Exactly one committed 27-event BTC perpetual dataset follows this route:

```text
MarketEventEnvelope-v2 primary records
  -> exact BTC-PERP price/quantity, receipt-age and gap adapter
  -> one D01SmokeStrategy class
  -> project-owned pre-submit smoke-risk decision
  -> deterministic Nautilus replay (twice)
  -> the same strategy/risk class in credentialless Nautilus sandbox PAPER
  -> orders, fills, position, assumed USDC cash/equity/PnL
  -> create-only completed-flat checkpoint and read-only restart probe
```

NautilusTrader remains exactly `1.231.0` in the existing isolated
`fit_gates/d41_nautilus/requirements.lock`. Root `pyproject.toml` and `uv.lock`
remain unchanged. D01 reuses the frozen D41 strategy lifecycle, exact adapter,
instrument construction, and economics overlay, then adds only the project-owned
risk, run identity, evidence, and bounded restart composition required by this
slice.

## Fail-closed boundaries

- The existing local policy defaults to `PAPER` and rejects every other spelling
  or mode before engine construction.
- A conflicting internal D41 mode or any populated protected Hyperliquid key,
  vault, or account-address environment name prevents startup. Values are never
  read into artifacts or printed.
- The only execution factory is `SandboxLiveExecClientFactory`. There is no
  Hyperliquid venue execution factory, signer, wallet, authenticated client, or
  network data client.
- `received_time - event_time` must be between zero and exactly five seconds.
  `received_monotonic_ns` proves only local receive order; it is never compared
  with an exchange wall clock.
- Sticky or per-event gaps, duplicate source events, non-contiguous capture
  ordinals, other instruments, and off-grid values are rejected before a
  strategy sees an event. Price increment is `0.1`; quantity increment is
  `0.00001`; no value is silently rounded.
- An entry is created only after the risk decision approves the exact intent.
  The exit must be reduce-only and flatten the exact open quantity.
- Every run directory is create-only. Existing or partial runs are never reused,
  overwritten, or resumed.

The dataset's embedded harness hashes remain truthful capture-time D41
provenance. The later Ruff-only D41 commit changed the current `fit_gate.py`
bytes, so D01 does not mislabel that historical hash as its runtime identity.
Instead, it verifies the committed dataset file SHA-256
`d58ed2c701bff47d57503e554439df97c7a97e165e12c0842bc065792de372a3`,
recomputes the primary-event SHA-256
`3ac0f4c0a59d7c1a79e017354b7c19c941c4ea97b2035b123722a71df2583088`,
re-enters every record through the current v2 adapter, and records a separate
D01 source identity. No D41 evidence is edited.

## Fixed smoke risk and economics

The fixed order is `0.00013 BTC`. At the dataset maximum `78047.0`, its assumed
notional is `10.146110 USDC`, below the hard `15 USDC` exposure cap. A declared
2% stop-distance proxy plus round-trip modeled costs is `0.215097532 USDC`,
below the `0.25 USDC` smoke-loss budget. These values prove routing and rejection,
not a production sizing model.

| Assumption | Bounded D01 value |
| --- | ---: |
| Starting cash | 100,000 USDC |
| Taker fee | 4.5 bps per fill |
| Half-spread | 0.5 bps per fill |
| Extra slippage | 1.0 bps per fill |
| Funding | 0 USDC; no settlement boundary modeled or crossed |
| Fill probability | deterministic market-fill mechanics on trade ticks |
| Native latency/extra slippage | zero; explicit limitation |
| Currency | native USD account is a documented 1:1 USDC proxy |

The project-owned overlay reconstructs cash, final BTC position, equity, fee,
spread, slippage, funding, and net PnL from primary fill records. It does not
pretend that the sandbox natively settles Hyperliquid fees or funding.

## Offline reproduction

Create a new project-local environment from the isolated exact-version lock.
This does not change root project metadata or adopt NautilusTrader as a root
dependency:

```bash
test ! -e .venv-d01
uv venv --no-project --python 3.13.15 .venv-d01
uv pip sync --no-cache --strict --python .venv-d01/bin/python \
  fit_gates/d41_nautilus/requirements.lock
uv pip check --python .venv-d01/bin/python

export PYTHONPATH=src:.
export TRADING_MODE=PAPER
export D01_PYTHON=.venv-d01/bin/python
export D01_RUN_DIR=vertical_slices/d01_btc_perp/runs/local-proof-new
test ! -e "$D01_RUN_DIR"

"$D01_PYTHON" -m vertical_slices.d01_btc_perp.run_slice run \
  --run-id local-proof-new \
  --artifact-dir "$D01_RUN_DIR"

"$D01_PYTHON" -m vertical_slices.d01_btc_perp.run_slice restart-probe \
  --artifact-dir "$D01_RUN_DIR"
```

The lock contains sixteen exact `==` version pins, including
`nautilus-trader==1.231.0`. It does not contain wheel hashes, so this is an exact
version-closure claim, not a bit-for-bit package-byte or supply-chain attestation.
Provisioning the fresh environment may require the configured package index; the
bounded run itself performs no capture and needs no network.

Verify the committed publication without creating a new run or engine:

```bash
"$D01_PYTHON" -m vertical_slices.d01_btc_perp.publication verify \
  --check-installed-dependencies
```

The run writes five compact files: a start claim, two replay records, one PAPER
record, and a completion checkpoint. A failed run keeps whatever was written and
requires a new run ID and new directory.

Focused validation:

```bash
"$D01_PYTHON" -m unittest -v \
  vertical_slices.d01_btc_perp.test_slice
```

The dedicated CI job independently asserts that exactly 13 tests are collected,
exactly 13 are executed, and none are failed, errored, skipped, expected-failed,
or unexpectedly successful.

## Honest restart boundary and D22

D01 proves only that a fully verified, terminal, flat local PAPER run is a
read-only `NOOP_ALREADY_COMPLETE` on a restart probe and creates zero new intent
keys. Missing, corrupt, incomplete, or ambiguous evidence fails closed without
starting an engine. It does not prove mid-run recovery or exactly-once venue
submission.

D22 should own the durable append-only intent/order/fill ledger, stable
venue-client-ID policy, transactional checkpointing, concurrency control,
crash windows around submission and acknowledgement, partial-fill/cancel/reject
recovery, and reconciliation of open orders, fills, position, cash, and balances
against venue truth. Funding settlement and removal of the USD proxy also remain
outside this local slice.
