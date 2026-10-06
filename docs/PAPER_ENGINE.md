# PAPER strategy and risk engine

PAPER-only path that runs a deterministic strategy on normalized public
market events. A later research signal can implement the same
`PaperStrategy` surface and be replayed, or pushed event-by-event from a
live public feed. This module does not submit venue orders, does not read
keys, and does not promote a strategy to SHADOW, TESTNET, or LIVE.

The shipped strategy is `NonProductionReferenceStrategy`. `flat` (the
default) stays flat. `toy` buys one fixed size and then flattens. Neither
mode has a researched edge. `production_eligible` is always false.

It reuses `hyperliquid_bot.paper_risk` for stop-based sizing and the
portfolio gates. It does not replace the frozen D01 smoke-risk function and
it does not change the Nautilus sandbox route.

## Run directory

Each `run_id` is create-only. If the directory exists, opening it raises
`RunAlreadyExistsError` and nothing is appended. There is no resume API.

```text
<store>/<run_id>/run-claim.json
<store>/<run_id>/ledger.jsonl
<store>/<run_id>/state.json
<store>/<run_id>/health.json
```

`ledger.jsonl` is the complete, append-only audit trail. Lines produced while
one event is processed are written together before `state.json` and
`health.json` are rewritten. With `durable_ledger=True` (the default) that
write, the run claim, and the new directory entries are fsynced, so every
completed event survives a crash or power loss. An offline replay that can
simply be re-run may set `durable_ledger=False` to skip the fsync cost. The
store root should already exist; when the engine creates it, the root's own
entry in its parent directory is not fsynced.

If anything raises while an event or clock tick is applied, the run fails
closed. Fills and orders already applied are written to the ledger,
`health.json` reports status `FAILED`, and the run refuses further events;
`close()` keeps that status. Start a new `run_id`. After a failed ledger
write the store refuses later appends rather than risk writing a partly
written batch twice, and `health.json` sets `ledger_write_failed: true`. The
flag means the last batch may be missing from the ledger or may not be
durable; reconcile the projections against the ledger before trusting either.

A strategy that keeps asking for the same blocked order is re-checked on
every event, but that is one rejection: a `risk_rejected` line is written
when the block starts, with its `received_utc_ns`. A new one is written only
when the order, reason, detail, or kill switch changes, or after an accepted
order or a flat target cleared the block. `risk_rejection_count` counts these
records.

`state.json` (`paper-engine-state-v2`) and `health.json` are projections
rewritten on every event. They are replaced atomically but not fsynced. To
keep that rewrite constant-size on a long run, `state.json` holds only the
most recent `recent_record_limit` (100) orders, fills, and rejections, plus
the full `order_count`, `fill_count`, and `risk_rejection_count`. Rebuild the
full history from the ledger.

`health.json` (`paper-engine-health-v1`) is the cockpit status file. Machine
timestamps are UTC. `observed_at_local` and `last_event_at_local` use
Europe/Amsterdam with a `CEST` or `CET` label. `venue_orders_submitted` is
false.

## Fill model

Hyperliquid BTC-PERP is a linear USDC-margined contract. Simulated PnL is
`signed_quantity * (fill_price - average_entry)` minus taker fees. Funding
is not settled.

Defaults, checked against official docs on 2026-10-06:

| Input | Value | Source |
| --- | --- | --- |
| Base-tier perp taker fee | 0.045% (`0.00045`) | [Fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) |
| Base-tier perp maker fee | 0.015% (`0.00015`), not used by the IOC taker model | same |
| BTC `szDecimals` | 5 (`0.00001` lot, size rounded down) | [Perpetuals meta](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals) |
| Price grid | at most 5 significant figures, at most `6 - szDecimals` decimal places; integers always allowed. Fills round adversely. | [Tick and lot size](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size) |
| Minimum order notional | $10, except a reduce-only close | [Error responses](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/error-responses) |
| Market orders | an IOC limit at mid +/- slippage (SDK default 5%) | [Python SDK `market_open`](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/exchange.py) |
| TP/SL trigger and slippage | triggered by the mark price; market TP/SL have a 10% slippage tolerance | [TP/SL orders](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl) |

PAPER defaults that are assumptions, not venue facts:

| Input | Default | Why |
| --- | --- | --- |
| `latency_ns` | 250 ms | Placeholder for the decision-to-venue delay. Measure the VPS round trip and override. `0` requires `allow_zero_latency=True`. |
| `entry_price_band_fraction` | 1% | Tight IOC limit for new risk, so hard limits hold at the worst admissible price. |
| `exit_price_band_fraction` | 10% | The venue's TP/SL slippage tolerance; exits still close in a fast market. |

A buy fills the ask and a sell fills the bid, worsened by the configured
slippage fraction, then rounded to that grid. Quantity is capped by the
displayed size (or the trade size when the book is not complete). The
unfilled remainder is cancelled (IOC). Latency waits for a later event
before that touch is eligible. Every order is an IOC limit around the touch
at decision time: entries use `entry_price_band_fraction`, exits
`exit_price_band_fraction`, and the limit is rounded so it never widens the
band. A fill beyond the limit does not happen; the order completes as
`CANCELED` with `unfilled_reason: price_band` (other reasons: `no_touch`,
`touch_size`, `touch_consumed`). Displayed size is used up per quote: fills
on the same BBO side or trade print never add up to more than its size. A
BBO side is reset only when its price or size changes, and a trade print by
the next trade, because PAPER fills never move the real book. While an order waits, the same target from
the strategy keeps it working, even when the order was rounded or clipped
to the risk size; only a changed target cancels and replaces it. A kill
flatten and a stop exit are zero-latency: they replace a matching strategy
order that is still waiting out its latency and fill on the same event. A missing side, a crossed book, or a missing
mark does not become a mid. New risk is rejected. Unrealized PnL stays null
until a venue mark or a complete two-sided book exists.

## Risk

- Per-trade size is `equity * risk_per_trade / stop_distance`, rounded down
  to the lot, at the risk price `touch * (1 + entry_price_band_fraction)`.
  Max notional, the `paper_risk` hard limits and the risk-based size are all
  checked at that price, so they hold wherever the IOC fills inside its band.
  For a BUY the limit caps the fill; a SELL limit only floors it, so a short
  gets the same cushion above the bid, but a bid rise beyond the band while
  the order waits is not bounded. Hard max position and max notional reject
  instead of clipping.
- Every open position carries a stop at the effective stop distance
  (`stop_distance_fraction * volatility_multiple`) from its average entry,
  the same distance the size assumed. A `stop_set` line records it. When
  the engine mark (the venue mark when fresher, else the BBO mid; the venue
  triggers TP/SL on its mark price) crosses the stop, a `stop_triggered`
  line is written and a zero-latency reduce-only IOC (`stop-exit`) closes the
  position at the touch, retried on later quotes until flat. With no mark at
  all (one-sided book, no venue mark) a trade printed since the stop was set
  triggers it; that is an exit trigger only, and equity still treats the
  price as missing. The config refuses a stop distance not wider than
  `slippage_fraction`, which would stop out every fill at once. A gap fills at
  the touch, beyond the stop: the loss is then larger than the risk budget.
- After a stop-out the strategy cannot re-open the same direction
  (`stop_lockout`, recorded once) until its target goes flat or reverses
  (`stop_lockout_cleared`). A stop-out does not halt the engine.
- Daily and weekly loss limits halt new entries and still allow a reduce-only
  flatten. An entry order that is still waiting out its latency when any kill
  switch is set is cancelled (`halted`) instead of filled. A daily-loss halt
  lifts at the next UTC day and a weekly-loss halt at the next ISO week
  (`kill_switch` state `NONE`, reason `daily_loss_window_reset` /
  `weekly_loss_window_reset`); the guard re-checks against the new baseline
  at once. Windows start at the first event (a replay is created after its
  tape), and a late event stamped in an earlier window never rolls one back. Drawdown, stale-data and missing-price halts do not lift by
  themselves.
- Drawdown at or beyond `drawdown_kill_fraction` flattens and halts.
- A gap longer than `stale_after_ns` flattens and halts. The caller can also
  pass an explicit clock (`on_clock`) so a quiet live feed trips the same
  guard. The engine does not read the wall clock, so replay stays
  deterministic.

## Replay

`load_hyperliquid_parquet_tape` reads completed DATA-1A raw Parquet parts
with the same JSON paths as the `trades`, `bbo`, and `activeAssetCtx`
research views. `PaperEngine.run_parquet` and `PaperEngine.on_event` are the
same strategy, risk, and fill path.

## Residual limits

- No queue position, no partial-book walk beyond the touch, no funding
  settlement, no venue reconciliation.
- The stop is simulated by the engine, not resting on the venue: PAPER
  triggers on its own mark and only when an event arrives.
- Depletion is tracked per displayed quote only; the book behind the touch
  is not modelled.
- A create-only run does not recover an open position after a process restart.
- Paper fills are not evidence of edge, capacity, or LIVE readiness.
