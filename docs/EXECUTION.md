# Execution

## Objective

Convert validated signals into reliable orders with minimal avoidable alpha loss, while preserving account state, safety and auditability.

## Venue

Hyperliquid is the initial primary execution venue. Market information may come from multiple exchanges, but the first live broker implementation targets Hyperliquid.

## Wallet model

Use an EVM-compatible master wallet for account ownership and authorize dedicated Hyperliquid API/agent wallets for automation.

The master private key/seed must never be stored on the TrueNAS host. The trading service receives only the dedicated agent key required for its assigned process/account scope.

Prefer separate agent wallets per independent trading process to reduce nonce/state conflicts and isolate operational blast radius.

## Execution interface

All brokers implement a common interface so strategy/risk code is unchanged between modes:

```text
BacktestBroker
PaperBroker
ShadowBroker
TestnetBroker
HyperliquidLiveBroker
```

## Order lifecycle

A robust order state machine must handle:

```text
INTENT
  -> RISK_APPROVED
  -> SUBMITTED
  -> ACKNOWLEDGED
  -> PARTIALLY_FILLED
  -> FILLED
  -> CANCEL_PENDING
  -> CANCELED / REJECTED / EXPIRED
```

Every state transition is persisted/audited.

## Requirements before live

- deterministic client order IDs;
- idempotent submission logic;
- partial-fill handling;
- reduce-only exits where appropriate;
- trigger/stop validation;
- maximum spread guard;
- maximum slippage guard;
- stale market-data guard;
- order-age limits;
- cancel/replace safety;
- exchange/account reconciliation;
- restart recovery;
- dead-man/cancel-all protection;
- global halt/flatten controls.

## Maker versus taker

Do not assume maker execution is automatically superior. The engine should compare expected spread capture against fill probability, urgency and adverse selection.

Possible policy for slower strategies:

1. maker-first where edge is large enough and urgency is low;
2. bounded reprice attempt;
3. skip rather than chase when expected edge has disappeared;
4. taker only when expected edge after all costs still justifies urgency.

## Transaction-cost analysis

Record per order/trade:

- signal/reference price;
- decision timestamp;
- submission timestamp;
- acknowledgement timestamp;
- fill timestamps/prices;
- maker/taker status;
- expected versus realized slippage;
- spread at decision and fill;
- opportunity cost from unfilled orders;
- fee and funding impact.

This allows measurement of alpha decay from signal -> order -> fill -> exit.

## Reconciliation

The exchange is authoritative for real positions/orders. Periodically compare:

- local positions;
- local open orders;
- local cash/equity;

against Hyperliquid state. Material mismatches block new risk and trigger an alert until resolved.

## Multi-leg trades

Basis/relative-value strategies require explicit leg-risk handling. If only one leg fills, the engine must either complete the hedge within strict bounds or neutralize the unexpected exposure.

## No UI-side signing

The Next.js cockpit sends authenticated operator intents to the backend. Browser JavaScript never receives the API-wallet private key and never signs exchange orders directly.