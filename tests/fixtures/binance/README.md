# Binance public research fixtures

`spot_trade_event.json` is a small deterministic synthetic fixture shaped after Binance Spot's
individual JSON raw Trade Stream (`<lowercase-symbol>@trade`). The schema basis was reviewed on
2026-08-25 against Binance's official Spot WebSocket Streams, market-data-only and SBE market-data
documentation. The JSON fixture itself represents only the documented JSON `@trade` event; it is
not an SBE payload.

The fixture was authored for tests. It was not captured from production or mainnet, so no capture
window exists and no account, credential, user, wallet or private data is present. Its values are
deliberately synthetic.

Raw `@trade` is a single exchange trade event and is distinct from Binance's aggregated
`@aggTrade` stream. `E` is exchange event time, while `T` is trade execution time. JSON
timestamps use milliseconds by default and may use microseconds only when the transport explicitly
selects that unit; the offline decoder therefore requires the caller to provide `MILLISECONDS` or
`MICROSECONDS` and never infers a unit from magnitude.

The `m` flag states whether the buyer was maker: `true` means the seller aggressed (`sell`), and
`false` means the buyer aggressed (`buy`). The documented `M` ignore flag is retained in the source
DTO without assigning normalized meaning to it. Normalization continues to use market-event schema
version 2; this fixture does not change shared contracts and contains no authenticated data.

Official schema basis:

- https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
- https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md
- https://github.com/binance/binance-spot-api-docs/blob/master/sbe-market-data-streams.md

The additional `public_*` fixtures are deterministic synthetic frames for DATA-1F. The Spot
WebSocket frames use the documented combined-stream envelope and microsecond timestamp option.
The depth snapshot is shaped after public REST `GET /api/v3/depth`; diff-depth quantities are
absolute price-level quantities and `"0"` means deletion. The USDⓈ-M frames use the current
routed `/market` combined JSON stream contract, including `bookTicker`. USDⓈ-M `aggTrade` is a 100-ms server
aggregate, not an individual trade; `nq` excludes RPI quantity while `q` can include it. Public
USDⓈ-M `bookTicker` excludes RPI liquidity. `forceOrder` exposes at most one exchange-selected
liquidation snapshot for a symbol in each 1,000-ms interval; current generated documentation and
the effective changelog differ on whether that means latest or largest. Silence cannot mean zero
liquidations.

All additional values are synthetic. No frame was captured from Binance, and no account, API key,
credential, order, wallet, or private data is present.

Additional official schema basis, reviewed 2026-08-31:

- https://developers.binance.com/en/docs/products/spot/faqs/market_data_only
- https://developers.binance.com/en/docs/products/spot/market-data/web-socket-streams
- https://developers.binance.com/en/docs/products/spot/market-data/rest-api/Order-Book
- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect
- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public
- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market
- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
