# Bitvavo Standard fixtures

These credential-free BTC-EUR messages are sanitized synthetic adaptations of the public
Bitvavo WebSocket examples and schemas. They retain the documented field shapes, integer
timestamps, decimal strings, and wrapped `getBook` response without containing captured market
data, account data, credentials, or Market Data Pro payloads.

Sources checked on 2026-08-31 (candles subscription re-checked 2026-09-19):

- https://docs.bitvavo.com/docs/websocket-api/trades-subscription/
- https://docs.bitvavo.com/docs/websocket-api/ticker-subscription/
- https://docs.bitvavo.com/docs/websocket-api/book-subscription/
- https://docs.bitvavo.com/docs/websocket-api/candles-subscription/
- https://docs.bitvavo.com/docs/websocket-api/get-order-book/
- https://docs.bitvavo.com/docs/manage-order-book/
- https://docs.bitvavo.com/api-specs/exchange-websocket-api.yaml
