# OKX public research fixtures

These fixtures are sanitized, synthetic messages shaped after the official OKX
WebSocket v5 public- and business-channel examples. They contain no account or
credential material and are safe for deterministic offline tests.

The `books` fixtures intentionally use the post-23 June 2026 JSON contract:
`checksum` remains present but is fixed to `0`, so continuity is tested only
with `prevSeqId` and `seqId`. The decimal values are deliberately represented as
JSON strings and include more precision than binary floating point can retain.

Sources consulted on 2026-08-31:

- <https://my.okx.com/docs-v5/en/#overview-production-trading-services>
- <https://my.okx.com/docs-v5/en/#order-book-trading-market-data-ws-all-trades-channel>
- <https://my.okx.com/docs-v5/en/#order-book-trading-market-data-ws-order-book-channel>
- <https://my.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel>
- <https://my.okx.com/docs-v5/en/#public-data-websocket-open-interest-channel>
- <https://my.okx.com/docs-v5/en/#public-data-websocket-mark-price-channel>
- <https://my.okx.com/docs-v5/en/#order-book-trading-market-data-ws-index-tickers-channel>
- <https://www.okx.com/docs-v5/log_en/#2026-06-23>

The values are not a captured market sample and must not be treated as evidence
of live feed coverage.
