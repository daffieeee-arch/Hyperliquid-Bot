# Capture-failure webhook

PAPER only. This wakes an external automation when a retain fails, dies, or
stops writing. It does not place orders, read trading keys, or restart a
collector.

The Chief of Staff runs the end-to-end check on the VPS. Do not run `--send`
from a cloud agent, and do not print `capture-alert.env`.

## What sends an alert

| State | When | Who sends it |
|---|---|---|
| `FAILED` | The collector stops fail-closed (exhausted reconnects, integrity error, `BinanceTransportError`, health file `FAILED`) | The collector, then the checker if that POST never got a 2xx |
| `DEAD` | No terminal health file, and the process is gone or the tmux session is gone while the process is not still alive | `scripts/capture_alert.sh watch` only. A killed process cannot POST |
| `STALE` | The process is still up (or liveness was not checked) but `capture-live.json` is older than 45s, Parquet is older than 180s, or the run produced no market data after the 180s startup grace | Checker |
| `TEST` | Operator `test --send` | Checker CLI only |

`COMPLETED` and `OPERATOR_STOP` do not alert. Short reconnects do not alert.
Binance server ping/pong and `serverShutdown` still reconnect; they become
`FAILED` only when the reconnect budget is exhausted or a required profile
ends. Spot: the server sends a ping every 20 seconds and disconnects if no
pong arrives within a minute
([web-socket-streams.md](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)).
USD-M: the server pings about every 3 minutes and closes the socket if no pong
arrives within 10 minutes
([USD-M websocket market streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams)).
Bitvavo Market Data Pro does not define an application ping; a late protocol
pong is a reconnect, not an alert, until the run fail-closes
([MD Pro introduction](https://docs.bitvavo.com/docs/ws-market-data-pro-api/introduction/)).

## Payload

JSON object, UTF-8, no secrets:

- `venue`, `run_id`, `state` (same value as `status`)
- `reason`, `error_class`, `error_message`
- `last_write_utc`, `last_write_amsterdam` (`Europe/Amsterdam`, or `unknown`)
- `host`, `code_version`, `ts_utc`
- `idempotency_key` (also the `Idempotency-Key` header)
- `alert_kind`: `failure` or `test`

The same failure reuses one idempotency key. A successful 2xx is recorded
under `~/.local/state/hyperliquid-bot/capture-alert-receipts/` so a later
check does not POST again. The receipt stores the key, venue, run id, state,
and HTTP status. It does not store the URL or the authorization value.

Retries: 3 attempts, 5 seconds each, backoff 0.5s then 1.5s. HTTP 401/403 are
not retried. Timeouts and HTTP 408/429/5xx are retried. The URL and
authorization value are never written to the capture log.

## One-time VPS file

Create this on the VPS as the operator user. Mode `0600`. Do not commit it
and do not paste it into chat.

```bash
install -d -m 700 "$HOME/.config/hyperliquid-bot"
install -m 600 /dev/null "$HOME/.config/hyperliquid-bot/capture-alert.env"
```

Contents (names only here; use the real URL and header value on the host):

```text
CAPTURE_ALERT_WEBHOOK_URL=https://example.invalid/capture-fail
CAPTURE_ALERT_WEBHOOK_AUTHORIZATION=Bearer <token>
```

The collector reads this file when those names are not already in the process
environment. Existing tmux sessions keep the environment they started with
until the next process start. The checker reads the file on every run, so it
does not require a capture restart.

## Dry-run, then one labelled TEST

From the pinned repo checkout. This does not touch `hl-capture`, `bn-capture`,
`kr-capture`, `bv-capture`, `bv-std-capture`, or the cockpit session.

```bash
cd "$HOME/Hyperliquid Project/Hyperliquid-Bot"
./scripts/capture_alert.sh test --venue binance --run-id capture-alert-test
```

Expect `delivery=dry_run`, `webhook_configured=yes`, `authorization_configured=yes`,
and a JSON payload with `"state":"TEST"`. The URL and the bearer token must
not appear.

Then one real POST:

```bash
./scripts/capture_alert.sh test --send --venue binance --run-id capture-alert-test
```

Expect `delivery=sent` and an automation wake whose payload is obviously a
test (`state=TEST`, `reason=operator_webhook_test`). If the automation is
quiet, stop. Do not retry in a loop.

## External checker

Read-only against tmux (`has-session` only) and `/proc`. It does not send
keys and does not restart anything.

```bash
ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
./scripts/capture_alert.sh watch --artifact-root "$ARTIFACT_ROOT"
```

`delivery=none` means nothing in the last 24 hours needs an alert.
`delivery=dry_run` prints the payload that `--send` would POST.
`delivery=already_delivered` means a 2xx receipt already exists.

To POST for real (timer or a confirmed failure):

```bash
./scripts/capture_alert.sh watch --artifact-root "$ARTIFACT_ROOT" --send
```

Useful filters: `--venue binance`, `--run-id <run_id>`, `--tmux-session
bn-capture` (only together with `--venue` or `--run-id`). Default sessions
are `hl-capture`, `kr-capture`, `bv-std-capture`, `bv-capture`, `bn-capture`.
A custom `TMUX_SESSION` must be passed or a live custom session can look dead.

Example timer the operator may install. Do not install it from a cloud agent
and do not point it at a checkout that is mid-edit.

```ini
# ~/.config/systemd/user/capture-alert-watch.service
[Service]
Type=oneshot
ExecStart=/bin/bash -lc 'cd "$HOME/Hyperliquid Project/Hyperliquid-Bot" && exec ./scripts/capture_alert.sh watch --artifact-root "$HOME/Hyperliquid Project/data-capture" --send'
```

```ini
# ~/.config/systemd/user/capture-alert-watch.timer
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=15s

[Install]
WantedBy=timers.target
```

Runs older than 24 hours are ignored unless `--run-id` is set, so the first
timer tick does not page on ancient `FAILED` directories. A live multi-day
retain stays in scope because its Parquet or `capture-live.json` is fresh.

## What this does not prove

- The automation stored the POST after it returned 2xx.
- A capture started before this code was on the host will load the env file
  only after that process is replaced. The checker does not need that restart.
- In-memory Parquet that was never published can stay invisible until the live
  file or the next part goes stale (180s).
- `COMPLETED` written by a collector that exited 0 after a real failure is a
  collector bug. Binance now keeps a required-profile failure as `FAILED`
  even when the planned duration elapses afterward.
