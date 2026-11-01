# Coinbase Exchange public protocol fixtures

These are synthetic, credential-free BTC-USD messages adapted from the official
[Exchange WebSocket channel schemas](https://docs.cdp.coinbase.com/exchange/websocket-feed/channels).
They are not captured venue observations, latency evidence or research samples.

`last_match` is a historical anchor. `match` uses IDs above 2^53 and exact decimal
strings to catch accidental float conversion; its `side` is the maker side.
`l2update` uses absolute sizes and includes a zero-size deletion. Timestamps retain
nine fractional digits. The channel subset intentionally has non-contiguous
exchange sequence numbers: those jumps must not become fabricated trade gaps.
