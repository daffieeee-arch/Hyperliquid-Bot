# D41: NautilusTrader BTC-PERP replay-to-PAPER fit gate

Status: **WRAP** from replacement run `runs/20260830T011412Z`. The retained
2026-08-30T00:11:21Z run remains explicitly **NOT PASSED**; its original bytes
and hashes are preserved in `artifacts/rejected-run-20260830T001121Z.json`.
This is not production code, strategy validation, deployment, or profitability
evidence.

## Decision

The retained run proves useful Nautilus mechanics but not the complete D41 input
and evidence boundary. The replacement run repairs those boundaries and supports
`WRAP`, not `ADOPT`: Nautilus is suitable for the bounded replay+sandbox core
behind project-owned risk, economics, persistence, restart, reconciliation,
correlation-mapping, and canonical-evidence wrappers. The bounded decision is
recorded in
[ADR-023](../../docs/DECISIONS/README.md#adr-023--wrap-nautilustrader-for-the-course-1-btc-perp-slice);
it does not authorize broader production-core adoption.

## Exact dependency and support status

- Runtime pin: `nautilus-trader==1.231.0`.
- Resolved environment: CPython 3.13.15, Linux x86_64, glibc 2.43, uv 0.12.5.
- `requirements.lock` records every resolved Python package at an exact version.
  It is platform-specific and version-reproducible, not a hash-locked or
  bit-for-bit environment claim.
- License: LGPL-3.0-or-later.
- Upstream calls the package Beta. The v1.231.0 release is the final legacy-v1
  line while new development moves to v2; its release notice promises critical
  v1 security backports for only about three months after cutover.
- Hyperliquid status is internally inconsistent: the README adapter table says
  stable, while the shipped Hyperliquid data example warns that the integration
  is under construction / unstable beta.

Primary sources:

- https://pypi.org/pypi/nautilus_trader/1.231.0/json
- https://github.com/nautechsystems/nautilus_trader/releases/tag/v1.231.0
- https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/pyproject.toml
- https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/LICENSE
- https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/README.md
- https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/SECURITY.md
- https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/examples/live/hyperliquid/hyperliquid_data_tester.py

## Safety boundary

The gate fails closed unless `D41_EXECUTION_MODE=PAPER` is exact. It refuses to
start when known Hyperliquid private-key, vault, or account-address environment
names are populated. The existing raw collector is the only network data route;
the TradingNode registers only:

- `SandboxLiveExecClientFactory` for local in-memory execution.

No Hyperliquid venue execution factory, wallet, signer, execution credential,
TESTNET order, SHADOW order, or LIVE order is constructed or sent. Accepted
`MarketEventEnvelope-v2` records are injected into Nautilus's `DataEngine`; the
verifier reconstructs the configured sandbox-only runtime and requires one
instrument, two filled IOC orders, a flat primary position report, and no
nonterminal orders.

## Shared transparent strategy

`D41SmokeStrategy` is used unchanged by replay and PAPER. It is deliberately not
an alpha claim:

1. after three observed trades, compare only the previous and current prices;
2. choose BUY on an uptick or SELL on a downtick;
3. record the signal on that observation;
4. submit a minimum-notional `0.00013 BTC` IOC market intent only on the next
   observed trade;
5. after five further observed trades from the entry fill, decide an exit and
   submit the reduce-only IOC exit on the next trade.

The artifacts record decision, submission, and fill ordinals. The gate requires
`submission_ordinal > decision_ordinal`, so a decision can never fill against
the event that created it.

## Public capture and thin boundary

Replay and PAPER now share one unchanged existing capture path:

```text
HyperliquidTradesCollector
  -> MarketEventEnvelope v2
  -> fit-gate-only envelope_to_trade_tick
  -> Nautilus TradeTick
```

The boundary rejects gap-tainted, stale, future-dated, unknown-instrument, and
invalid-precision input. It never rounds an off-grid value; an explicit order
normalization policy belongs in a later project wrapper. It preserves capture
order and does not reinterpret a Hyperliquid trade ID as a sequence. It applies
only the public BTC precision observed from venue metadata: price increment
`0.1`, size increment `0.00001`.

Freshness uses two deliberately separate clocks. `received_time` is the
collector's UTC wall-clock timestamp at raw-frame ingress and is compared with
the exchange event timestamp; the limit remains exactly five seconds.
`received_monotonic_ns` is used only for local receive ordering and bounded
capture duration. A monotonic timestamp is never subtracted from an exchange
wall-clock timestamp. Persisted `ts_init` derives from `received_time`; a one
nanosecond tie-break preserves capture order for events sharing one receipt.

Replacement accepted capture evidence:

- run directory: `runs/20260830T011412Z`;
- one 30-second capture after the first batch; no retry;
- 53 v2 events received: 26 stale/future events rejected, zero precision
  rejects, and 27 accepted events;
- maximum accepted age: `4.995580000` seconds against the unchanged
  five-second limit;
- 22 raw frames and 22 normalization outcomes acknowledged by volatile sinks;
- `sticky_gap=false`; collector state after cancellation: `stopped`;
- primary-event digest:
  `3ac0f4c0a59d7c1a79e017354b7c19c941c4ea97b2035b123722a71df2583088`.

Retained rejected capture evidence:

- capture window after first batch: 20 seconds;
- 68 v2 events received, of which 27 initial backlog/future-stale events were
  rejected by the five-second fail-closed boundary;
- 41 fresh public BTC perpetual events retained;
- 19 raw frames and 19 normalization outcomes acknowledged by volatile sinks;
- `sticky_gap=false`; collector state after cancellation: `stopped`;
- canonical event digest:
  `b7a7b60d0afb0dc563e582777d2d0d475ee986265e5e58a816deed431005ef2d`.

The volatile sinks make no Bronze/Silver durability claim. The data is evidence
of local capture integrity through the collector/v2 boundary, not cryptographic
proof that the exchange originated each event. Existing collector, contract,
provenance/v3, 3B1C-2, and 1H code are unchanged.

## Replacement run: WRAP

Both replay artifacts were independently reloaded and their selected business
payloads recomputed to digest
`51bdd7bd883fe0228d5d286a8fd7b9a766340aafba1cb60bd476739788f28229`.
PAPER consumed all 27 accepted events and no rejected event:

- entry decision 3, submission 4, fill execution input 4 at `78047.0`;
- exit decision 10, submission 11, fill execution input 11 at `78047.0`;
- two primary `FILLED` IOC order reports, two matching fill reports, zero
  rejections, no nonterminal order, and primary position `FLAT` at `0.00000`;
- PAPER input digest:
  `fda54bf36d775d5e86d0b997a2ea67800d296873842144390aac8a59cb5f80b1`;
- exact assumed-cost net P&L: `-0.0121753320 USDC`;
- sandbox processing elapsed `2.020243` seconds under a 30-second limit;
- all twelve independently recomputed hard gates passed; final verdict `WRAP`.

The full local run-integrity record binds all six verifier inputs and outputs.
The smaller
`runs/20260830T011412Z/publication-integrity.json` manifest carries those local
diagnostic hashes forward while binding the committed decision inputs, including
the bounded dataset and compact gate summary. The larger replay and PAPER
diagnostics remain local. The dataset file SHA-256 is
`d58ed2c701bff47d57503e554439df97c7a97e165e12c0842bc065792de372a3`
and the final gate-summary file SHA-256 is
`da36dbabfe953372282a7494837775aa07921ef3526ecce7e549fad6a8973b14`.

## Retained run: NOT PASSED

The original dataset and five artifacts remain byte-for-byte present. The
rejection record binds their file sizes and SHA-256 hashes before the harness was
changed. Its embedded `WRAP` verdict is explicitly superseded by `NOT PASSED`
because PAPER let 29 of 47 inputs older than five seconds reach the strategy,
silently rounded off-grid precision, exposed `dataset-from-paper`, and used a
non-binding verifier. Only the compact rejection manifest belongs in the
publication candidate; the superseded dataset and diagnostic replay/PAPER JSON
remain local because they cannot support the accepted decision.

## Explicit economics assumptions

| Component | D41 assumption | Native sandbox behavior |
| --- | --- | --- |
| Taker fee | 4.5 bps per fill | Hyperliquid metadata arrives with zero maker/taker fees |
| Half-spread | 0.5 bps per fill | Not injected |
| Extra slippage | 1.0 bps per fill | Zero |
| Latency | Native value reported as zero | Hardcoded zero |
| Funding | Zero for the bounded run; no hourly settlement crossed | No funding settlement; capture is trades-only |
| Currency | 1 USD = 1 USDC for overlay only | USD accounting proxy |

The 4.5 bps fee is Hyperliquid's published base taker tier, not evidence of an
account-specific tier. References:

- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications

## Restart, persistence, and reconciliation

- The public data side supports reconnect/resubscribe, while the existing
  collector remains authoritative for capture ownership and gap state.
- Sandbox cash, orders, and positions are in-memory. Artifact JSON files persist
  evidence only; they cannot recover engine state.
- Sandbox reconciliation report methods return empty results. No venue truth is
  available because no venue execution connection exists.
- An interrupted run must be invalidated. A valid run starts with a fresh engine,
  finishes flat, and has only terminal orders.
- Funding settlement, durable state checkpoints, idempotent restart, order/fill
  reconciliation, and recovery after an ambiguous shutdown remain project-owned
  wrapper responsibilities.

Relevant upstream implementation:

- https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/adapters/sandbox/execution.py
- https://nautilustrader.io/docs/latest/integrations/hyperliquid/

## Recommended integration boundary

```text
existing raw collector + venue-neutral v2 contract
  -> thin precision/staleness/gap adapter
  -> shared Nautilus strategy/order lifecycle
  -> replay engine OR the same accepted records + local sandbox execution
  -> project-owned economics + persistence + reconciliation wrapper
  -> canonical evidence artifacts
```

The replacement run justifies **WRAP**, never `ADOPT`: the verifier independently
reloads both replays plus primary events, orders, fills, positions, and economics
and all recomputed relations pass. ADR-023 records only this bounded fit decision;
D41 does not prove native economics, restart, reconciliation, or production
fitness, so it does not authorize production-core adoption.

## Reproduction

### Recompute the published decision without network

From the isolated worktree root, use the already provisioned exact environment,
the published bounded dataset, and a new output directory:

```bash
export D41_EXECUTION_MODE=PAPER
export PYTHONPATH=src:.
export D41_DATASET=fit_gates/d41_nautilus/runs/20260830T011412Z/dataset.json
export D41_REPRO_DIR=fit_gates/d41_nautilus/runs/REPRO_NEW_UNIQUE_ID
test ! -e "$D41_REPRO_DIR"

.venv/bin/python -m fit_gates.d41_nautilus.run_fit_gate replay-twice \
  --dataset "$D41_DATASET" \
  --artifact-dir "$D41_REPRO_DIR"

.venv/bin/python -m fit_gates.d41_nautilus.run_fit_gate paper \
  --seconds 30 \
  --dataset "$D41_DATASET" \
  --artifact-dir "$D41_REPRO_DIR"

.venv/bin/python -m fit_gates.d41_nautilus.run_fit_gate verify \
  --dataset "$D41_DATASET" \
  --artifact-dir "$D41_REPRO_DIR"

.venv/bin/python -m unittest -v fit_gates.d41_nautilus.test_fit_gate
```

This reruns the decision relations, not the original wall-clock metadata; the
deterministic business digest and all independently recomputed gates must match.
Every artifact write is create-only, so existing evidence is never reused or
overwritten.

### Separate future capture

A fresh public capture is a new, non-identical fit-gate attempt, not reproduction
of the published run. It must start in another nonexistent directory:

```bash
export D41_CAPTURE_DIR=fit_gates/d41_nautilus/runs/CAPTURE_NEW_UNIQUE_ID
test ! -e "$D41_CAPTURE_DIR"
.venv/bin/python -m fit_gates.d41_nautilus.capture_public_dataset \
  --seconds 30 \
  --output-dir "$D41_CAPTURE_DIR"
export D41_DATASET="$D41_CAPTURE_DIR/dataset.json"
export D41_REPRO_DIR="$D41_CAPTURE_DIR/artifacts"
test ! -e "$D41_REPRO_DIR"
```

To evaluate that capture, repeat the three replay/PAPER/verify commands above
with those variables. The safety checks intentionally reject populated known
credential variables; do not work around that guard. A failed or insufficient
capture is retained as an incomplete attempt and is not selectively retried.
