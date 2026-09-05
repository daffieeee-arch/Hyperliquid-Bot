# Bitvavo Market Data Pro fixtures

These synthetic, credential-free BTC-EUR messages follow the official 2026
Market Data Pro `book`, `getBook`, `trades`, and `ticker` field shapes. They
were not captured from an account or live socket. Authentication and
subscription controls are generated in tests so no key, secret, signature,
account field, or entitlement claim can enter a fixture.

`mdpro_*` source channels and `market_data_pro` feed identity stay distinct
from DATA-1D Standard (`trades` / `ticker` / `book`).
