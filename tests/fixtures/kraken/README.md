# Kraken DATA-1B fixtures

These are synthetic BTC/EUR WebSocket v2 market-data frames for deterministic offline tests.
They contain no API key, API secret, WebSocket token, account data or outbound authenticated
subscription. Decimal values are intentionally JSON number tokens with trailing zeros and more
precision than IEEE-754 binary floats can preserve.

`official_book_checksum_vector.json` and `official_level3_checksum_vector.json` reproduce the
price, quantity, order and checksum fields from Kraken's published WebSocket v2 checksum examples.
Only the market symbol is changed from `BTC/USD` to this slice's `BTC/EUR`, and the documented L2
example is given a message timestamp because neither field participates in the checksum.
