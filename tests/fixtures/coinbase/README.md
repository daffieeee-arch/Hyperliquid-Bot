# Coinbase Exchange public protocol fixtures

Unless specified below, these are synthetic, credential-free BTC-USD messages adapted from the official
[Exchange WebSocket channel schemas](https://docs.cdp.coinbase.com/exchange/websocket-feed/channels).
They are not captured venue observations, latency evidence or research samples.

`last_match` is a historical anchor. `match` uses IDs above 2^53 and exact decimal
strings to catch accidental float conversion; its `side` is the maker side.
`l2update` uses absolute sizes and includes a zero-size deletion. Timestamps retain
nine fractional digits. The channel subset intentionally has non-contiguous
exchange sequence numbers: those jumps must not become fabricated trade gaps.

`subscriptions_level2_50.json` is the exact 233-byte public ACK retained from the
Nov1 2026 12:27 UTC bounded pilot B. It contains only BTC-USD channel names and
null account scopes. Payload SHA-256:
`6399849e8ba86a9d28c3311ba5eeeb8640892ed5a59239aeffb59fb661ff2be1`.
The official channel documentation confirms that `level2_50` was renamed
`level2_batch`; both names identify the public 50-ms batch feed. This fixture is
protocol regression evidence, not market data, latency or strategy evidence.
