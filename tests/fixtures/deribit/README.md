# Synthetic Deribit research fixtures

These JSON messages are sanitized, deterministic examples shaped from the public
Deribit WebSocket API v2 contracts. They contain no account data or credentials,
were not captured from a live endpoint, and deliberately retain long numeric
lexemes for exact-byte and decimal-string regression tests.

The sample scope is BTC-PERPETUAL, BTC-25SEP26, and paired call/put options across
two expiries. It is a bounded research sample, not the complete option universe.
