# Optional Hyperliquid fast L2 source capture

Source feature for [#246](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/246),
supporting the execution-proof work in #244. This does not deploy an image,
change the continuous TrueNAS app, admit a strategy or send orders.

## Why this is a separate source

The failed `micro-flow-20261113-v1` study required a new qualified quote within
one second of its delayed execution request. The source-only investigation found
sampled slow twenty-level L2 intervals of approximately 5–12 seconds. A deeper
snapshot observed several seconds earlier cannot prove current liquidity.
All original failed accounts and archives retain their original identities.

The [official subscription documentation](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
defines `l2Book`'s optional `fast: true` as five levels rather than twenty slow
levels. A separate bounded probe confirmed that the VM received this mode;
neither that probe nor this source feature proves continuous cadence, exchange
fills, queue priority, complete depth or profitability. BBO remains changes-only.

## Explicit opt-in and provenance

The native public capture CLI accepts `--l2-fast`. Omission preserves the exact
legacy subscription bytes and feed/config fingerprint. The flag affects only
the L2 subscriptions of explicitly started BTC/optional ETH/SOL instruments;
trades, BBO, context and optional candles keep their existing responsibilities.
Exactly one L2 mode is requested per coin/session. Never mix same-channel fast
and slow subscriptions in one session or concatenate their raw parts without
their capture metadata.

Fast runs have the feed suffix `-fast-l2-5`, and their create-only start claim
and config fingerprint bind:

- `l2_mode: fast-5` and at most five levels per side;
- the exact outbound L2 subscription payloads containing `fast: true`;
- the requirement for a matching acknowledgement in each new session.

Original raw outbound requests, inbound acknowledgements/payloads, timestamps,
ordinals and hashes remain intact. The ordinary publication manifest binds the
claim and raw files verbatim; no publication schema or historic claim is rewritten.
Absence of these fast claim fields denotes the legacy slow **request**, not a
promise of twenty populated levels or a measured delivery interval.

For an explicitly assigned operator using a verified bounded research output
root, the native source invocation is:

```bash
uv run --frozen python -m hyperliquid_bot.hyperliquid_raw_research \
  --artifact-root /path/to/verified/research-output \
  --run-id hl-fast-btc-new-campaign \
  --duration-seconds 600 \
  --l2-fast
```

The run directory must be new; capture refuses reuse or overwrite. A native CLI
run is bounded, not a replacement for the reviewed continuous app. This feature
does not add a `RuntimePlan` option or silently change the app's default command.
Continuous adoption needs its own configuration/provenance integration, reviewed
immutable image, fresh campaign, storage/traffic pilot and runtime acceptance.

## Fail-closed source behavior

For L2 acknowledgements, the requested fast boolean and unaggregated precision
must match. An absent `fast` is accepted only for the legacy slow request;
an explicit `false` also means slow. Wrong booleans, numeric/string substitutes,
changed `nSigFigs`/`mantissa`, malformed fields or ambiguous duplicate JSON keys
cannot establish the fast mode. Official nullable precision defaults are allowed.

Fast books are validated from original bytes separately from the legacy general
classifier, which preserves numeric tokens as strings. Native timestamps and
order counts must be integers; prices/sizes must be bounded finite positive
decimal strings. Sides must contain zero through five properly ordered levels
with uncrossed best prices. Empty/sparse books stay observable; they do not
certify an executable price. Fast-mode JSON/ack validation is bounded to 64 KiB,
and price/size strings to 128 characters; the existing transport byte, queue,
writer rotation, duration and stop guards remain in force.

A fast book cannot refresh L2 liveness until its coin's requested mode is
acknowledged in that session. Invalid raw messages are retained before an
existing `subscription_error` or `schema_error` integrity marker references
their ordinal. The connection then follows the existing disconnect/gap/reconnect
path. Reconnect starts a new acknowledgement set; a previous session's proof
cannot make its first unacknowledged book fresh. Recovery does not erase integrity
counts or convert the affected capture into clean research evidence.

The unchanged collector's feed silence limits monitor transport/source activity;
they are not a promise of subsecond delivery or a trading freshness threshold.
A later research decoder must independently qualify native clock/receipt age,
gaps, source mode, depth consumption, partial fills and residual exposure.
In particular, five levels are not an unlimited liquidity or guaranteed exit model.

## Validation and operational boundary

The deterministic source tests cover default request/fingerprint compatibility,
exact boolean opt-in, add-on/candle coexistence, distinct fast claim identity,
native numeric types, sparse books, wrong depth/order/precision, duplicate keys,
pre-ACK liveness refusal, reconnect acknowledgement reset, raw-byte preservation
and integrity counts surviving a recovered connection. Existing Python CI covers
these changes; no additional workflow or paid tool is introduced.

No credentials, signing, account endpoints, broker, trading mode, NAS files,
running containers, retention rule or continuous runtime configuration changes.
Measure fast-source storage and cadence in a new accepted pilot before adopting
it continuously; never overwrite slow archives or automatically delete market data.
