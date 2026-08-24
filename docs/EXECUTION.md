# Execution

## Objective

Convert validated signals into reliable orders with minimal avoidable alpha loss, while preserving account state, safety and auditability.

## Development versus runtime

Execution code is developed and tested on Windows/WSL2, but authenticated execution runs only in an explicitly authorized TrueNAS environment.

Local DEV:

- defaults and fails closed to `PAPER`;
- uses mocks, fixtures and deterministic replay;
- has no live exchange credentials;
- does not need a wallet;
- may test order-state transitions without submitting real orders.

TrueNAS PAPER/SHADOW/LIVE:

- runs CI-built, digest-pinned images;
- owns persistent ledgers and reconciliation state;
- receives environment-specific secrets;
- remains operational when the Windows development workstation is offline;
- does not execute from a mutable source checkout.

## Venue

Hyperliquid is the initial primary execution venue. Market information may come from multiple exchanges, but the first authenticated broker implementation targets Hyperliquid. Bitvavo spot and Kraken are introduced only through their own venue promotion gates.

## Wallet model

Use an EVM-compatible master wallet for account ownership and authorize dedicated Hyperliquid API/agent wallets for automation.

The master private key/seed must never be stored on Windows, WSL2, TrueNAS, Docker, logs, browser code or GitHub. The runtime execution service receives only the dedicated agent key required for its assigned environment/process/account scope.

Prefer separate agent wallets per independent trading process to reduce nonce/state conflicts and isolate operational blast radius.

No authenticated wallet or trade key is required during the initial local and TrueNAS PAPER stages.

## Execution interface

All brokers implement a common interface so strategy/risk code is unchanged between modes:

```text
BacktestBroker
PaperBroker
ShadowBroker
TestnetBroker
HyperliquidLiveBroker
BitvavoSpotBroker        # later
KrakenBroker             # optional later
```

Broker selection is server-side configuration validated at startup. Local builds and tests prove that unapproved non-PAPER modes fail closed.

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

Every state transition is persisted/audited with:

- venue;
- environment;
- strategy/configuration version;
- client/correlation ID;
- code commit;
- container image digest.

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
- global halt/flatten controls;
- separate live secrets and persistent state;
- tested deployment rollback;
- no runtime dependency on Codex/Hermes/Windows;
- stable runtime platform or explicit risk acceptance.

## Maker versus taker

Do not assume maker execution is automatically superior. The engine compares expected spread capture against fill probability, urgency and adverse selection.

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
- fee and funding impact;
- venue and route alternatives;
- commit/image/configuration identity.

This allows measurement of alpha decay from signal -> order -> fill -> exit and distinguishes strategy decay from software/deployment regression.

## Reconciliation

The exchange is authoritative for real positions/orders. Periodically compare:

- local positions;
- local open orders;
- local cash/equity;
- order/fill history;

against venue state. Material mismatches block new risk and trigger an alert until resolved.

PAPER uses an append-only internal ledger and deterministic reconciliation rules so its behavior can be compared with SHADOW/LIVE later.

## Multi-leg trades

Basis/relative-value strategies require explicit leg-risk handling. If only one leg fills, the engine must either complete the hedge within strict bounds or neutralize the unexpected exposure.

Cross-venue multi-leg execution is introduced only after each venue adapter independently passes state-machine, recovery and reconciliation tests.

## Deployment safety

The execution service is promoted as a versioned image:

```text
CI image -> TrueNAS PAPER -> SHADOW -> SMALL LIVE
```

Use the same digest through stages where practical. Deployments record the active image and configuration. A failed deployment rolls back to a known-good digest; it is never repaired by editing files inside a running container.

## No UI-side or LLM-side signing

The Next.js cockpit sends authenticated operator intents to the backend. Browser JavaScript never receives an agent-wallet private key and never signs exchange orders directly.

Hermes/LLMs may analyze, propose, test and operate PAPER workflows, but unrestricted live order/withdrawal tools are not exposed directly. Live actions pass through deterministic risk and execution services.
