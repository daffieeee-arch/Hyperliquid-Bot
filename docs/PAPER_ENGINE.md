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

The risk boundary binds a signed position to an exact PAPER/account/venue/
instrument/strategy key and current version. The isolated logical account is
`native-paper:<run_id>`. New submissions wait for outstanding orders to settle
or cancel and reconcile. Each accepted IOC retains its original key and is
checked again against the current signed position immediately before filling;
an oversize or wrong-direction reduction is rejected rather than clipped.
The same proof runs before the emergency-exit exception for non-positive equity.
Unknown position truth blocks that exception. This is local PAPER truth, not
venue reconciliation; the create-only engine still has no resume API.

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
| `touch_refill_ns` | 1 s | How long size PAPER took at a price stays missing from that price before the level counts as refilled. Must be positive. |

A buy fills the ask and a sell fills the bid, worsened by the configured
slippage fraction, then rounded to that grid. Quantity is capped by the
displayed size (or the trade size when the book is not complete). The
unfilled remainder is cancelled (IOC). Latency waits for a later event
before the order may fill. An eligible order that meets a one-sided or
crossed BBO completes `CANCELED` with `unfilled_reason: no_touch`; with no
complete fresh book, a fresh trade print fills it. Every order is an IOC limit
around the touch at decision time: entries use `entry_price_band_fraction`, exits
`exit_price_band_fraction`, and the limit is rounded so it never widens the
band. A fill beyond the limit does not happen; the order completes as
`CANCELED` with `unfilled_reason: price_band` (other reasons: `no_touch`,
`touch_size`, `touch_consumed`, `stale_quote`, `risk_rejected`).

PAPER fills do not move the real book, so the feed keeps showing size PAPER
already took. For `touch_refill_ns` after a fill, the size PAPER took at a
price on one side is held back from the displayed size at that price; more
fills there add to it and restart the timer. A level that leaves the touch
and comes back in that time is still short. After it, the level counts as
refilled by other makers. A price PAPER has not taken from offers its full
displayed size, and a displayed size of zero is a missing touch, not a
used-up one. A trade print stays used up until the next print. An IOC that
meets a used-up level completes as `CANCELED` with
`unfilled_reason: touch_consumed`; a strategy order is checked against the
quote it would fill on, after its latency, not the one it was decided on.
Only with zero latency, and only when the decision event is itself the
touch (a complete BBO, or a print while the book is not complete), is the
decision quote also the fill quote; then such an order is rejected with
`touch_consumed` instead, recorded once while the block lasts. A stop exit or
kill flatten on a used-up level waits for new size, a new price, or the
refill, cancels a strategy order meanwhile, and `health.json` shows
`exit_waiting_for_quote: true`. The BBO feed only pushes changes, so
`on_clock` retries such a waiting exit, and after a fill applies the loss
limits at once in the last event's window: loss windows roll on venue event
time only, never on the caller's clock. A clock also reevaluates stops and
missing-price guards when a source expires: an expired book/mark may reveal
a still-fresh trade fallback. A pending exit against an unchanged missing or
band-blocked quote is not retried merely because time passed.

While an order waits, the same target from the strategy keeps it working,
even when the order was rounded or clipped to the risk size; only a changed
target cancels and replaces it. A kill flatten and a stop exit are
zero-latency: they replace a matching strategy order that is still waiting
out its latency and fill on the same event. A missing side, a crossed book,
or a missing mark does not become a mid. New risk is rejected. Unrealized
PnL stays null until a fresh venue mark or a fresh complete two-sided book exists.
Each cached BBO, mark, trade and bar expires independently after
`stale_after_ns` without a new receipt from that source. Source age uses the
greatest received timestamp or explicit clock observed by the engine, so a
late event cannot rewind freshness. Unrelated fresh messages cannot preserve
an old mark or execution quote. Expired strategy prices become unavailable;
neither valuation nor execution extrapolates them.

## Risk

- Per-trade size is `equity * risk_per_trade / stop_distance`, rounded down
  to the lot, at the risk price `touch * (1 + entry_price_band_fraction)`.
  Max notional, the `paper_risk` hard limits and the risk-based size are all
  checked at that price before submission. A BUY limit caps the fill; a SELL
  limit only floors it. Every entry therefore checks max position, max
  notional and the `paper_risk` gates again using the actual fill price and
  actual available quantity before applying a fill. A short waiting through
  a bid rise cannot exceed those limits. A partial fill within the limits
  may proceed; a breach cancels the IOC as `risk_rejected`, without clipping.
  Reduce-only exits remain available when entry limits are breached.
- Every open position carries a stop at the effective stop distance
  (`stop_distance_fraction * volatility_multiple`) from its average entry,
  the same distance the size assumed. A `stop_set` line records it. When
  the engine mark (the venue mark when fresher, else the BBO mid; the venue
  triggers TP/SL on its mark price) crosses the stop, a `stop_triggered`
  line is written and a zero-latency reduce-only IOC (`stop-exit`) closes the
  position at the touch, retried on later quotes until flat. With no mark at
  all (one-sided or expired book, no fresh venue mark) a fresh trade processed
  after the stop was set triggers it. Venue times are not compared on purpose:
  any venue-time filter either lets a re-delivered print through or lets one skewed or mis-stamped
  print hide a real stop. A print with a skewed venue timestamp may exit early
  while its receipt remains fresh; an expired receipt cannot trigger or fill
  an exit. The WS client and the Parquet replay drop
  a re-sent print by trade id before it reaches the engine. That is an exit
  trigger only, and equity still treats the price as missing. The config
  refuses a stop distance not wider than `slippage_fraction`, which would
  stop out every fill at once. Choose it wider than half the spread plus
  slippage as well; the spread cannot be checked up front, and a narrower
  stop fires on the first mark after a fill. A gap fills at the touch,
  beyond the stop: the loss is then larger than the risk budget.
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
  tape), and a late event stamped in an earlier window never rolls one
  back. Drawdown, stale-data and missing-price halts do not lift by
  themselves.
- Drawdown at or beyond `drawdown_kill_fraction` flattens and halts.
- A gap longer than `stale_after_ns` flattens and halts. The caller can also
  pass an explicit clock (`on_clock`) so a quiet live feed trips the same
  guard. The engine does not read the wall clock, so replay stays
  deterministic. A flatten with no fresh execution source remains pending
  with `flatten_blocked_missing_price`; it waits for a fresh quote instead
  of filling at an expired last touch.

## Replay

`load_hyperliquid_parquet_tape` reads completed DATA-1A raw Parquet parts
with the same JSON paths as the `trades`, `bbo`, and `activeAssetCtx`
research views. `PaperEngine.run_parquet` and `PaperEngine.on_event` are the
same strategy, risk, and fill path. Each selected trade, BBO and asset-context
payload must identify `coin: BTC`, matching the outer `BTC-PERP` product.
A wrong, absent or empty payload coin raises `PaperTapeError`, including a
context without a mark; it is never silently filtered or relabelled.
A trade print re-sent after a reconnect
is kept once, like the live trades collector's dedup: same source identity
(time, coin, trade id) within a 10,000-id LRU window. This approximates the
live path rather than copying it: the live cache is per collector process
(empty after a restart) and shared by all its instruments, while the replay
keeps one window per tape and product. The same identity with a different
print raises `PaperTapeError`; a corrupt tape stops the replay.

## Optional read-only cockpit snapshot

Set the server-only `COCKPIT_NATIVE_PAPER_RUN_DIR` to one existing native
PAPER run directory to select the native view at `/paper`. This reads
`run-claim.json`, `state.json`, `health.json` and `ledger.jsonl`; it does not
create or resume a run. Unset the variable to retain the existing COURSE-1
view. An explicitly empty binding selects an UNBOUND native view; missing or
invalid artifacts never fall back to COURSE-1 data. Requests cannot choose a
filesystem path.

SSR and `/api/native-paper-run` use the same `native-paper-cockpit-v1`
projection. The existing cockpit refresh clock rereads the snapshot with
no-store caching. Only PAPER, Hyperliquid and BTC-PERP are supported. No
collector, account request, signing, deployment or durable-data write occurs.
On the native `/paper` view, shell branding and refresh controls describe
snapshot reads; the shell does not poll or display unrelated capture-origin
badges. Other workspaces retain their capture context. The binding is resolved
at server runtime, and only its presence is passed to the browser shell.

Repository fixtures are DEMO; a bound non-fixture directory is a HISTORICAL
SNAPSHOT with liveness unverified. Even `RUNNING` is only the recorded engine
status. `observed_at_utc` is engine event/replay time, not a wall-clock
heartbeat. The UI separates source observation, snapshot read time, run
closure and read state; reading old artifacts does not make them fresh.

Each JSON artifact is limited to 256 KiB; the complete ledger must fit within
2 MiB and 5,000 records. Over-limit, malformed, failed or inconsistent
artifacts fail closed with no economic snapshot. Partial writes are pending,
missing files are missing, and source age above 180 seconds or a stale-data
halt is stale. A failed browser refresh marks previously displayed values as
not current. State arrays are bounded recent records; complete history and
counts come from the validated ledger, with the last eight rows per category
shown. Completed/canceled orders exclude accepted orders still open.

The adapter checks source identity, overlapping projections, ledger counts
and recent suffixes. There is no cross-file transaction or event commit
watermark, so these checks do not certify atomic event completion. PnL
remains assumed and not venue-reconciled; funding is not settled. Decimal
strings and unavailable marks/equity remain exact or null. Realized PnL is
gross of separately reported fees; cash is a simulated ledger balance, not
free margin. No start-capital return or venue PnL is inferred. The reader
does not expose potentially rounded JSON nanosecond timestamps as exact times.

## Residual limits

- No queue position, no partial-book walk beyond the touch, no funding
  settlement, no venue reconciliation.
- The stop is simulated by the engine, not resting on the venue: PAPER
  triggers on its own mark and only when an event arrives.
- Depletion is a fixed refill time at the touch only. The book behind the
  touch is not modelled: an exit on a used-up level waits instead of walking
  to the next level, and a refill is assumed, not observed.
- The engine itself does not deduplicate trade prints. The WS client and
  the Parquet replay drop a print re-sent after a reconnect by trade id;
  any other feed must do the same.
- A create-only run does not recover an open position after a process restart.
- Paper fills are not evidence of edge, capacity, or LIVE readiness.
