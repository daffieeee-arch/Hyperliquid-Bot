# D22-A: bounded local PAPER ledger recovery

Status: local proof only. ADR-023 remains `WRAP`, not `ADOPT`.

## Independent architecture verdict

One concrete stdlib `sqlite3` ledger is the smallest sound choice for this
subphase. JSON/JSONL would require project-owned locking, atomic multi-fact
writes, tail recovery, uniqueness and conflict handling. PostgreSQL, an ORM, a
repository interface or a general event framework would add unused scope.

The ledger uses SQLite's rollback journal, `synchronous=FULL` and explicit
`BEGIN IMMEDIATE` transactions. It is a single-writer local WSL proof, not a
production durability or concurrency claim.

## Exact route

```text
committed D01 MarketEventEnvelope-v2 dataset
  -> unchanged D01 adapter and inherited tick-decision code
  -> unchanged D01 precision/stale/gap and smoke-risk rules
  -> PRE_SUBMIT SQLite commit
       intent + risk decision + sandbox order command + stable project IDs
  -> credentialless Nautilus 1.231.0 sandbox-PAPER order
  -> FILL_SETTLEMENT SQLite commit
       fill + position + assumed USDC cash/equity/PnL
```

There are exactly two immutable record kinds, not a generic execution model:

- `PRE_SUBMIT` atomically registers an approved intent and local sandbox order
  command before order construction/submission;
- `FILL_SETTLEMENT` atomically registers one full IOC fill and its derived
  position, cash, equity and PnL.

Updates and deletes are rejected by SQLite triggers. Re-appending an exact
stable record is a no-op; the same ID with different bytes fails and rolls back
the complete transaction. The verifier reloads every row and recomputes the
intent/risk/order relation, stable IDs, fill relation and D01 economics.

The timing-independent project intent ID binds the logical run, instrument,
intent ordinal/reason, side, exact quantity and reduce-only flag. Its derived
client order ID is passed into Nautilus's `OrderFactory`. This is only a local
PAPER ID policy; Hyperliquid `cloid` compatibility and venue idempotency remain
unproven.

## Recovery proof and honest duplicate boundary

The focused process test exits with code `86` immediately after the entry fill
settlement commits. Reopen verifies one durable intent/order/fill and an open
`0.00013 BTC` position. A second process creates a new disposable sandbox,
replays the exact D01 dataset from the start, verifies the replayed entry as an
idempotent no-op, and appends only the exit records. The final durable ledger has
exactly two intents, two orders and two fills, is flat, and recomputes net PnL
`-0.0121753320 USDC` under the unchanged D01 assumptions. A third start is
`NOOP_ALREADY_COMPLETE` without engine construction.

Across the two disposable sandbox processes, the entry is intentionally
simulated twice; there are three sandbox executions in total. D22-A therefore
proves no duplicate **durable project-ledger** order/fill, not no duplicate
external execution.

## Offline validation

Build the same isolated environment as the existing D01 publication gate. This
uses the committed exact D41 lock; no root dependency or lockfile changes are
required and no environment from another worktree is assumed:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:.
export TRADING_MODE=PAPER
export D22A_VENV=/tmp/d22a-publication-venv
test ! -e "$D22A_VENV"

uv venv --no-project --python 3.13.15 "$D22A_VENV"
uv pip sync --no-cache --strict \
  --python "$D22A_VENV/bin/python" \
  fit_gates/d41_nautilus/requirements.lock
uv pip check --python "$D22A_VENV/bin/python"

export D22A_PYTHON="$D22A_VENV/bin/python"

"$D22A_PYTHON" -m unittest -v \
  vertical_slices.d22a_paper_ledger.test_slice

"$D22A_PYTHON" -m unittest -v \
  vertical_slices.d01_btc_perp.test_slice
```

Manual bounded crash/recovery uses a new ledger path:

```bash
export D22A_LEDGER=/tmp/d22a-manual-new/paper-ledger.sqlite3
test ! -e "$D22A_LEDGER"

"$D22A_PYTHON" -m vertical_slices.d22a_paper_ledger.run_slice run \
  --run-id d22a-manual \
  --ledger "$D22A_LEDGER" \
  --crash-process-after-entry-fill

"$D22A_PYTHON" -m vertical_slices.d22a_paper_ledger.run_slice verify \
  --run-id d22a-manual --ledger "$D22A_LEDGER"

"$D22A_PYTHON" -m vertical_slices.d22a_paper_ledger.run_slice run \
  --run-id d22a-manual --ledger "$D22A_LEDGER"
```

The crash flag is test-only and deliberately terminates its process. It must not
be used in a shared service process.

## Publication artifact choice

No generated SQLite database is required in Git. The focused subprocess test
creates a fresh temporary ledger and independently exercises the crash and
recovery path. Local successful and rejected database files are diagnostics,
not additional portable evidence.

## Deferred D22 boundaries

D22-A does not prove Nautilus engine-state restoration, concurrent writers,
partial fills, cancel/reject recovery, crash atomicity with an external broker,
exactly-once venue submission, venue/account reconciliation, real USDC or
funding settlement, power-loss behavior of a future runtime volume, backup,
deployment, signing, credentials, TESTNET, SHADOW, LIVE or profitability.

Full D22 — paperbroker, ledger and reconciliation — therefore remains
incomplete. A possible next gate is **D22-B — venue-authoritative crash-window
reconciliation**; it is intentionally not implemented here.

The wrapper must be rejected or redesigned if it starts owning strategy
scheduling, risk policy, fill simulation, routing/signing, retry/cancel state
machines, venue reconciliation or generic storage adapters. Those would turn it
into a second trading framework instead of the intended project-owned seam.
