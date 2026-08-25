# Hyperliquid public-trade fixtures

`trades_frame.json` and `hip3_trades_frame.json` are deterministic, sanitized fixtures constructed
from the official Hyperliquid
[`trades` WebSocket and `WsTrade` schema](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions).

- Schema basis: the official documentation as reviewed on 2026-08-25; the source schema does not
  publish a separate version number. Normalized assertions target market-event schema version 2.
- Neither fixture was **captured from mainnet**; neither has a capture window.
- Coins, prices, sizes, timestamps, transaction hashes, trade IDs and user addresses are
  synthetic test values; no captured values were transformed or retained.
- Both fixtures preserve the documented wire shape: `channel` is `trades`, `data` is an array,
  decimal values are strings, and `users` is the ordered `[buyer, seller]` pair.
- Each frame contains exactly one coin, matching coin-specific trades subscriptions and the
  [official Python SDK routing behavior](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/websocket_manager.py).
- `trades_frame.json` contains two BTC trades and covers both aggressor sides, a multi-trade array
  and the maximum valid 50-bit trade ID.
- `hip3_trades_frame.json` contains one `xyz:XYZ100` trade and exercises the documented HIP-3
  `{dex}:{coin}` namespace without normalization.
- No account data, credential, private key or authenticated payload is present.
