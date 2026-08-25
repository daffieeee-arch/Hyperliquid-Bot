# Binance Spot raw-trade fixture

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
