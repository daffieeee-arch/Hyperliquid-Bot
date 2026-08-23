# Security

## Principle

Trading security is designed from day one, even while the system only uses paper money.

## Wallet separation

- Master wallet remains outside the trading server.
- Prefer hardware-backed custody for meaningful capital.
- Hyperliquid agent/API wallets are authorized for automated trading.
- Use separate agent wallets per trading process/account scope where practical.
- Never expose the master seed/private key to TrueNAS, Docker, logs, browser code or GitHub.

## Secret handling

Secrets must not appear in:
- repository files;
- `.env` committed to Git;
- Docker images;
- application logs;
- Grafana dashboards;
- browser local storage;
- frontend bundles.

Use TrueNAS/Docker secret mechanisms or an equivalent protected runtime secret path. Rotate credentials after any suspected leak.

## Browser boundary

The Next.js UI contains no exchange signing capability. It sends authenticated operator requests to the FastAPI control gateway. Signing occurs only inside the isolated execution service.

## Network design

Prefer:
- private/local bindings where external exposure is unnecessary;
- Tailscale or equivalent private access for remote administration;
- firewall restrictions between services;
- TLS for browser/API traffic;
- least-privilege database users.

Grafana and research workers do not receive exchange private keys.

## Mode safety

`PAPER` is the default. Entering `LIVE` must require explicit configuration and operator approval. A frontend toggle alone must never be sufficient to enable real-money execution.

## Execution safeguards

Before material live capital:
- dead-man/cancel-all protection;
- global halt control;
- flatten-and-halt control;
- stale-data halt;
- reconciliation halt;
- maximum order notional;
- maximum exposure;
- daily/weekly loss limits;
- rate-limit aware order handling;
- restart recovery.

## Auditability

Persist:
- operator actions;
- strategy promotions;
- configuration changes;
- model/version changes;
- risk-limit changes;
- order intents and risk decisions;
- execution state transitions.

Each live trade must be attributable to a specific code commit, strategy version and configuration state.

## CI/CD security

GitHub Actions should eventually include:
- dependency scanning;
- secret scanning;
- Python lint/type/test checks;
- TypeScript lint/type/build checks;
- container image scanning;
- protected live deployment environment.

No successful CI run automatically promotes a strategy into live capital.