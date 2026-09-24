# Capture reliability triage — 2026-09-24

Phase 1 only. No production code in this change. Review baseline and `origin/main` are the same commit: `b8d8aaf766f369450794f8157691cc647aa7d20f`. Nothing on `main` after that tip fixes findings 1–6.

PAPER inspection only. Live tmux sessions were not stopped, restarted, or sent keys. Capture files and collector env were not modified. Repros wrote under `/tmp/phase1-repro-out-ad8a` and a temp directory, using fakes, against the Python sources that match this tip for the files under test.

Green tests, including any phase-2 suite that goes green, do not prove 72h capture reliability. A passing unit test does not exercise a live socket, disk stall, or a multi-hour partial outage.

## Checkout versus what the live collectors are running

Development checkout (`/home/chupa/Hyperliquid Project/Hyperliquid-Bot`):

- `git rev-parse HEAD` = `7b59ced9370bc7300be65054b4b9be0c5e834a3b`
- branch `cursor/capture-reliability-hardening-356d`, diverged from `b8d8aaf`
- the branch has four local commits (webhook/Ping-timeout cherry-pick plus test formatting). `main` has `#102`, `#103`, and the merged `#104` that the cherry-pick duplicates
- the Python files for findings 1–4 match `b8d8aaf` on this branch. `display.ts` on the branch is the pre-`#102` clock formatting. `data1aCaptureHealthPresentation` / `venueCaptureChipStatus` still mark fresh Parquet with no health file as `RUNNING` on both tips

`origin/main` = review tip `b8d8aaf`.

No live process embeds a git SHA in its cmdline, `capture-claim.json`, or capture log. Start SHAs below are inferred. What is missing everywhere: a recorded commit id at process start.

Python collectors import modules at start and were not restarted after later checkouts of the same directory. The in-memory code is the start SHA. The directory HEAD today is not that SHA for the Hyperliquid / Bitvavo Pro / Kraken processes.

| Venue | UP? | pid | run_id | started SHA | checkout HEAD now |
| --- | --- | --- | --- | --- | --- |
| Hyperliquid | yes | 1768050 | `20260923t130254z-phase-a-72h` | `11b2940afb5cb826071a28712d71b6db2d0779d8` | dev tree `7b59ced` (moved after start) |
| Bitvavo Pro | yes | 1768146 | `20260923t130254z-phase-a-72h` | `11b2940afb5cb826071a28712d71b6db2d0779d8` | dev tree `7b59ced` (moved after start) |
| Kraken | yes | 1768215 | `20260923t130254z-phase-a-72h` | `11b2940afb5cb826071a28712d71b6db2d0779d8` | dev tree `7b59ced` (moved after start) |
| Binance | yes | 1934323 | `20260923t182625z-phase-a-72h-bn-continue` | `d93f1d5b2c9cc81882aa6b3b2031b3225cd518e8` | retain worktree still `d93f1d5` |
| Bitvavo Standard | yes | 1934375 | `20260923t182625z-phase-a-72h-bvstd` | `d93f1d5b2c9cc81882aa6b3b2031b3225cd518e8` | retain worktree still `d93f1d5` |
| Cockpit (`next dev`) | yes | 216504 | reads the runs above | `f840fb3ce1ac2affe526935248683d854fc59ee7` at process start | cwd tree now `7b59ced`; `next dev` recompiles from disk, no compile SHA is recorded |

Start-SHA evidence:

- HL / Bitvavo Pro / Kraken started 2026-09-23 15:02:54 +0200 with cwd `Hyperliquid-Bot`. Reflog holds `11b2940` from 14:41:34 until 19:10:56. `11b2940` is `#99`, an ancestor of the tip. `hyperliquid_raw_research.py` and `parquet_research.py` are unchanged from `11b2940` to `b8d8aaf`. `bitvavo_mdpro_research.py` is not: `#104` (`b6c9d51`) added Ping-timeout reconnect after this process started. The live Bitvavo Pro process does not have that reconnect in memory.
- Binance and Bitvavo Standard started 2026-09-23 20:26:25 +0200 with cwd `Hyperliquid-Bot-retain`. That worktree’s only reflog entry is `d93f1d5` at 20:25:30, and HEAD is still `d93f1d5` (`#101`). `binance_public_research.py` is unchanged from `d93f1d5` to `b8d8aaf`, so the live Binance process matches the tip for finding 1.
- Cockpit started 2026-09-19 16:32:46 +0200. Reflog is `f840fb3` from 16:32:03 until 2026-09-20 01:47:06. The process was not restarted. A read of `http://127.0.0.1:3000/api/venue-capture-health` on 2026-09-24 returned `RUNNING` / tone `ok` / `live: true` for all five venues, with `status_detail` `RUNNING (health JSON pending until stop)` and `error` `capture-health.json is not written yet`.

Activity at inspection (directory metadata only, no Parquet body reads): every run’s newest part mtime was 2026-09-24 02:05–02:06 +0200. `capture-health.json` was absent on all five runs. Claim `state` is `STARTED_FAIL_CLOSED`.

## Finding 1 — Binance errors can vanish from final run status

**Status: CONFIRMED** (with a boundary the brief’s wording does not draw)

**Priority: P0** error propagation / honest status.

**Code:** `BinancePublicResearchCollector.capture_for`, `_capture_open_interest`, `_receive_session` exit, `run_reconstructable_capture`.

`_capture_open_interest` records the quality marker, sets the shared stop, and raises `BinanceDataIntegrityError` on truncation or validation failure (`binance_public_research.py` around 1359–1391). `capture_for` then discards that task:

- the wait loop treats a finished OI task as best-effort and does not read its exception (around 548–550)
- `asyncio.gather(oi_task, return_exceptions=True)` drops the exception again (around 560)
- `run_reconstructable_capture` sets `status = "COMPLETED"` whenever `run_bounded_capture` returns (around 2369–2372)
- `elapsed_seconds` is stamped in `finally` after wind-down (around 2379)

Two different outcomes, both reproduced with fakes under `/tmp`:

1. OI integrity error before any required stream is observed. Streams exit into the existing unobserved-stream check and raise `BinanceDataIntegrityError: Binance required public streams were not observed.` The run status is `FAILED`. The OI error text is not the terminal error. This path does **not** report `COMPLETED`. The brief’s “WS tasks stop cleanly” claim is wrong for a start-of-run OI failure.
2. OI validation error after the three profiles have already observed their required streams (OI fetcher delayed 0.5s, fixture frames first). `capture_for` returned normally in 0.503s of a requested 30s. `run_reconstructable_capture` wrote `status=COMPLETED` with `elapsed_seconds=0.758`. The OI exception is gone. `COMPLETED` here means “`capture_for` returned”, and `elapsed_seconds` is the short stop plus wind-down.

One required profile dying while the others keep going is also real, and the code comments say it is intentional (`capture_for` around 579–590): a single profile error stays local unless every websocket profile dies, unless the error is `BinanceDataIntegrityError` or `BinanceSinkError`. Repro: spot `recv` raises `ConnectionError`, `max_reconnects=0`, USD-M profiles receive their required fixtures and then idle, duration 1s. `capture_for` returned normally after 1.003s. The log line was `binance profile_task_failed error_class=BinanceTransportError`. The runner maps that normal return to `COMPLETED`.

**Reproduced by a failing test?** The behaviors were reproduced by an isolated `/tmp` script (not committed). The suite has no test that an OI error after feeds are up must not be `COMPLETED`, and no test that one required profile’s transport death must not be `COMPLETED`. `tests/python/test_binance_public_research.py` covers OI normalization and a direct `_capture_open_interest` oversize call, not this gather.

**Runtime evidence:** the live Binance process matches this code (`d93f1d5` == tip for this file) and is still writing. This inspection did not inject an OI failure into it. The live log is not evidence of finding 1 firing. Health JSON is still absent, which is normal until stop, so a vanished error would be invisible on the strip anyway (finding 5).

**Smallest fix:** after `gather`, read the OI result and every stream result. If OI set the stop, or any required profile ended with an error before the requested window, the terminal status has to name that cause. Keep today’s fail-closed unobserved-stream path. Keep reconnectable transport errors on one profile from killing the others mid-window, and still refuse `COMPLETED` when a required profile is dead at the end. Compare the stop reason to the requested duration; do not treat `elapsed_seconds` as proof the window finished.

**Same class elsewhere:** OKX `capture_for` and Kraken `capture_for` already raise the first socket exception (`FIRST_EXCEPTION`). Bitvavo Pro `await stream` propagates. The drop is Binance-specific. Every runner that sets `COMPLETED` on a normal return shares the status rule, so a partial outage that does not raise becomes `COMPLETED` there too.

## Finding 2 — Receive path coupled to Parquet storage

**Status: PARTIAL**

**Priority: P2** recv/storage coupling.

**Code:** `ParquetResearchWriter.append` holds `self._lock` and, when rotation trips, `await self._flush_locked()`. `_flush_locked` `await`s `asyncio.to_thread(self._write_segment)` before releasing the lock (`parquet_research.py` around 129–174). `to_thread` keeps the event loop runnable. The lock does not. The caller of `append` cannot recv again until DuckDB plus `fsync` finishes. Binance profiles also share one `BinancePublicResearchCollector._append_lock` around `sink.append` (around 1432 and 1490), so one profile’s flush stalls the other two.

Repro: rotation at 2 records, `_write_segment` slept 0.4s then wrote. A third `append` blocked 0.376s. That is the coupling. It is not a websocket pong-timeout reproduction.

**Reproduced by a failing test?** The blocking wait was reproduced in `/tmp`. The suite does not assert that recv can continue during flush. A phase-2 test should fail on today’s lock scope and pass once `append` returns after a bounded handoff.

**Runtime evidence:** live Binance log (`d93f1d5` process) has 13 spot `Pong timeout` closes (code 1008) and 1 `usdm_public` `Pong timeout`, plus other disconnects. That matches the failure mode the collector comment already names (blocked processing versus a shallow read queue). It does not identify Parquet flush as the cause of those 14 closes. No flush-duration measurement was taken on the live run.

**Docs check (2026-09-24):**

- Spot JSON streams, `binance/binance-spot-api-docs` `web-socket-streams.md`: server ping every 20s; disconnect if no pong within a minute; pong payload must copy the ping. The collector disables client pings (`ping_interval` / `ping_timeout` `None`) and relies on the library to answer server pings. That matches this rule.
- USD-M “ping every 3 minutes / pong within 10 minutes” is what the collector comment says. The official USD-M page did not load here (JS gate), so that interval was not re-verified from the primary page.
- `websockets` asyncio client docs: `max_queue` is the receive-buffer high-water mark (default 16); `None` disables flow control. This collector sets 1024. A consumer that does not `recv` still lets that buffer fill, after which the socket is not read and a server ping can miss the Spot one-minute pong deadline.

**Smallest fix:** under the writer lock, swap out the segment, release the lock, then write. `append` returns after the handoff. Stamp `received_*` at recv, before any queue wait. Bound the queue, expose its depth, and propagate writer errors on shutdown. Unbounded queues and silent drops are out of scope. A separate writer task is optional.

**Same class:** every collector that `await`s `ParquetResearchWriter.append` on its receive task. Binance is the sharp case because three profiles share one append lock and Spot’s pong deadline is one minute.

## Finding 3 — Hyperliquid stale market data is marked and not recovered

**Status: CONFIRMED**

**Priority: P1** outage detect / recover.

**Code:** `HyperliquidRawResearchCollector._receive_session` (`hyperliquid_raw_research.py`). `_MARKET_DATA_CHANNELS` is `trades`, `bbo`, `l2Book`, `activeAssetCtx`. `pong` and `subscriptionResponse` are control channels. One `last_market_data` clock covers every market channel and every coin. After `_MARKET_DATA_STALE_SECONDS` (90) with heartbeats still flowing, it writes `market_data_stale_despite_heartbeat` and sets `market_data_stale_marked`. Nothing closes or reconnects. The latch clears when any market channel arrives, so one live channel hides a dead one. `last_inbound` still updates on pong, so the receive timeout does not fire.

`tests/python/test_hyperliquid_raw_research.py::test_heartbeat_does_not_count_as_market_data_validity` asserts the marker and then stops the run from outside. The scripted factory has one connection. A reconnect would need a second connection. The test passes today because there is no recovery.

**Reproduced by a failing test?** No new failing test in this run. The existing test encodes the gap by passing.

**Runtime evidence:** no. The live HL log shows three `ConnectionClosedOK` code 1000 reconnects. Those are transport closes, which `_capture_until` already reconnects. Stale markers live in Parquet `data_quality` rows. Those bodies were not scanned.

**Docs check:** Hyperliquid’s websocket doc (`hyperliquid.gitbook.io` websocket page) says clients must handle unannounced server disconnects and reconnect, and that missed data shows up on the snapshot after reconnect. Client `{"method":"ping"}` / server `{"channel":"pong"}` is the keepalive (also described on the public websocket guides). Pong is not a market channel. `l2Book` is a book snapshot stream, not a heartbeat. Silence on one coin is not automatically a fault; BTC `trades` / `l2Book` / `bbo` / `activeAssetCtx` going quiet while pong continues is the fault this marker already names.

**Smallest fix:** per required channel, for the required coins, a silence clock. On breach, write the marker and reconnect that session the same way a receive timeout already does. Clear the latch when that channel is fresh again. Leave pong on the transport clock. Pick thresholds per channel so a quiet addon coin does not flap the BTC session.

**Same class:** Bitvavo Pro and Bitvavo Standard use one `last_market_monotonic_ns` for every market frame (finding 4). Binance already force-reconnects a profile on required-stream starvation. HL is the collector that records staleness and stays up.

## Finding 4 — Bitvavo Pro can wait without a bound during bootstrap

**Status: CONFIRMED**

**Priority: P1** outage detect / recover.

**Code:** `BitvavoMdProResearchCollector._receive_session`, `_require_authentication_ack`, `_require_subscription_ack`, `_receive_or_stop`.

`_receive_or_stop` uses `asyncio.wait(..., timeout=timeout_seconds)`. Callers pass no timeout for the auth ack (around 819) and the subscription ack (around 904). In `_receive_session`, `timeout_seconds` stays `None` until `session_healthy` (around 646–665). `session_healthy` flips only after a valid book snapshot (around 746–748). Until then a socket that stays open and sends nothing waits until the outer duration stop. Auth reject, missing snapshot, and transport close already have different exceptions once a frame arrives. The hang is the no-frame case.

After the snapshot, every accepted market frame, including trades and ticker, updates the same `last_market_monotonic_ns` (around 745). Trades keep the idle clock fresh while the book is silent, and the reverse.

`#104` (`b6c9d51`, on the tip) reconnects code 1000 reason `Ping timeout`, including during authenticate, and turns client `ping_timeout` off. That path is separate. A phase-2 patch has to keep it.

**Reproduced by a failing test?** No. The unbounded `timeout=None` call is visible in code. A credentialed ack-timeout test was not added in this run.

**Runtime evidence:** the live Bitvavo Pro log, from the `11b2940` process, records repeated `ConnectionClosedOK` code 1000 reason `Ping timeout` between 15:54 and 18:06. That is the pre-`#104` keepalive behavior, and this process does not contain `#104`. Parts were still rotating at inspection, so those closes did not end the run. There is no log evidence of a bootstrap hang on this process.

**Docs check:** Bitvavo WS Market Data Pro introduction requires authenticate, then subscribe, on `wss://ws-mdpro.bitvavo.com/v2/`. The book subscription doc describes subscribe / book events and does not define an application-level ping. `python-bitvavo-api` issue 58 shows the server closing with websocket code 1000 reason `Ping timeout` after it stops sending protocol pings (about every 50s). That supports keeping the `#104` reconnect and not treating protocol ping as market data.

**Smallest fix:** give auth ack, subscription ack, and the first snapshot their own deadlines inside `_receive_or_stop`. Map timeout to the existing auth, subscription, and `snapshot_missing` errors. After the book is live, track book silence and trade silence separately so one feed cannot mask the other. Keep the `venue_ping_timeout` reconnect.

**Same class:** `bitvavo_standard_research.py` gates its idle timeout on `session_healthy` the same way, and uses one market clock. Kraken and OKX already fail the slice when a required task raises; they are not this bootstrap wait.

## Finding 5 — Dashboard treats recent files as healthy feeds

**Status: CONFIRMED**

**Priority: P0** honest status. Operators are looking at this during the 72h window.

**Code:** `data1aCaptureHealthPresentation` and `venueCaptureChipStatus` in `apps/cockpit/src/lib/display.ts`. When `capture-health.json` is missing, a raw dir with at least one part and a fresh `last_part_mtime_utc` becomes `tileLabel` `RUNNING`, tone `ok`, `live: true` (around 122–138). `venueCaptureChipStatus` maps `presentation.live` to `RUNNING` (around 272–276). `loadVenueCaptureChip` copies that through. Gaps and reconnects stay unset while health is missing.

`apps/cockpit/src/lib/venue-capture.test.ts` expects this (`copies a live-looking Binance run as RUNNING`, and the DATA-1D soft-load case). Those tests pass because the UI is specified that way today.

**Reproduced by a failing test?** No new failing test. The live API is the reproduction: all five chips were `RUNNING` / `ok` / `live: true` while each `error` was `capture-health.json is not written yet`. A phase-2 test should expect a non-healthy state when health is absent, which will fail until `display.ts` changes. The current tests will need to change with it.

**Runtime evidence:** yes. Health files absent, parts fresh, cockpit GET as above. The strip cannot see a dead required Binance profile or a stale HL channel, because those facts are not in the health file and the UI does not read them.

**Smallest fix:** split the chip into storage activity versus feed health. Fresh mtime with no health file is “writing, validity unknown”, tone not `ok`. Missing health stays unknown. Keep the mtime check; do not scan Parquet bodies for the strip. When health exists, keep copying it. A later, optional, cheap heartbeat file can publish per-profile liveness without a rescan. That file is only needed if phase 2 wants the strip to show a dead profile before process stop.

**Same class:** one function serves HL, Binance, both Bitvavo feeds, and Kraken.

## Finding 6 — Tests cover helpers more than the whole error path

**Status: CONFIRMED**

**Priority:** lands with the fix it guards. P0 cases first.

**What exists:** unit tests for OI normalization, a direct OI oversize call, the HL stale marker without reconnect, and cockpit tests that require `RUNNING` when health is pending.

**What does not exist in the suite:**

- OI integrity after Binance required streams are already flowing, asserting terminal status is not `COMPLETED` and the OI reason is visible
- one Binance profile hard-failing while the others run to the duration, asserting the run is not `COMPLETED`
- regression guard: OI integrity before any frame still `FAILED` (already true)
- pongs and no required HL market channel, asserting a reconnect and a cleared latch once data returns
- Bitvavo auth ack and first snapshot that never arrive, asserting a bounded, classified failure
- book silent while trades flow, asserting the book clock still fires
- slow Parquet flush under a second append, asserting the receive side is not stuck for the flush, with `received_*` taken at recv
- cockpit chip with fresh parts and no health file, asserting not `RUNNING` / not `ok`

**Reproduced by a failing test?** The Binance status cases and the Parquet lock wait were reproduced outside the suite (`/tmp/phase1-repro-ad8a.py`, outputs under `/tmp` only). They are not part of this PR. The immediate-OI `FAILED` path also called `emit_capture_operator_alert` (inherited environment, HTTP 401, authorization unset). No live capture path was written.

## Phase 2 order

1. **P0 — Finding 1.** Fold Binance task results into the terminal status. Keep unobserved-stream fail-closed. A required profile that is dead at the end, and an OI stop after feeds are up, must not be `COMPLETED`.
2. **P0 — Finding 5.** Cockpit: fresh files with no health file are unknown, not healthy. This is what the live strip shows today.
3. **P1 — Finding 3.** HL per-channel silence and the existing reconnect path. Clear the latch when that channel is back.
4. **P1 — Finding 4.** Bitvavo Pro bootstrap deadlines and separate book/trade clocks. Keep the `#104` Ping-timeout reconnect. The live Pro process is still on `11b2940` and does not have `#104` until it is restarted, which is outside this triage.
5. **P2 — Finding 2.** Release the Parquet lock across the DuckDB write. Bounded handoff, timestamps at recv, errors still propagate.
6. **Finding 6** ships inside each of those changes, as a test that fails on the tip and passes after the fix.

Stay inside these collectors. OKX and Kraken already raise on the first required-task failure; do not copy Binance’s “local unless every profile died” rule onto them. Bitvavo Standard shares the bootstrap clock with finding 4 and can take the same small timeout change if it is the same function shape. No new exchanges.

## What this does not prove

A green phase-2 suite, a green merge, and this document do not prove the 72h captures are healthy. The live runs are still inside their requested windows, health files do not exist yet, and the cockpit currently paints that as `RUNNING`. Proving reliability still takes the finished health files, the per-channel gaps those files do not yet carry, and a window that actually elapsed.
