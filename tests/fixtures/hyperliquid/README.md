# Hyperliquid public WebSocket fixtures

`trades_frame.json`, `hip3_trades_frame.json`, `bbo_frame.json`, `l2_book_frame.json`,
`active_asset_ctx_frame.json` and `subscription_response_frame.json` are deterministic, sanitized
fixtures constructed from the official Hyperliquid
[WebSocket subscription and data-format schema](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions).

- Schema basis: the official documentation as reviewed on 2026-08-30; the source schema does not
  publish a separate version number. Existing trade-normalization assertions target market-event
  schema version 2; DATA-1A exact-raw storage rows use their separate research schema version 1.
- No fixture was **captured from mainnet**; none has a capture window.
- Coins, prices, sizes, timestamps, transaction hashes, trade IDs and user addresses are
  synthetic test values; no captured values were transformed or retained.
- The research fixtures preserve the documented `trades`, `bbo`, `l2Book`, `activeAssetCtx` and
  `subscriptionResponse` wire shapes. Decimal values remain strings. The BBO and L2 fixtures use
  `WsLevel` objects with `px`, `sz` and order count `n`; the context fixture covers funding, open
  interest, oracle, mark, mid, previous-day price and day notional volume.
- Each frame contains exactly one coin, matching coin-specific trades subscriptions and the
  [official Python SDK routing behavior](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/websocket_manager.py).
- `trades_frame.json` contains two BTC trades and covers both aggressor sides, a multi-trade array
  and the maximum valid 50-bit trade ID.
- `hip3_trades_frame.json` contains one `xyz:XYZ100` trade and exercises the documented HIP-3
  `{dex}:{coin}` namespace without normalization.
- The four DATA-1A market-data fixtures use BTC and deliberately retain trailing decimal zeros so
  Parquet and DuckDB tests can detect accidental float conversion.
- No account data, credential, private key or authenticated payload is present.
- These frames are schema fixtures only. They cannot satisfy the multi-day
  `hypothesis_research` candidate thresholds and must fail closed as
  `not_enough_data`. They are not a retained market series.
