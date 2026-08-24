# Security

## Principle

Trading security is designed from day one, even while the system only uses paper money.

The platform separates four trust zones:

1. Windows/WSL2 development;
2. GitHub/CI and container registry;
3. TrueNAS PAPER/SHADOW runtime;
4. TrueNAS LIVE runtime and exchange credentials.

Moving code between zones happens through reviewed source and immutable container artifacts—not through shared mutable directories.

## Development workstation boundary

Windows/WSL2 is trusted for source development, but it is not the live signing host.

Normal development must not contain:

- the Hyperliquid master-wallet seed/private key;
- a production Hyperliquid agent key;
- CEX withdrawal credentials;
- production database administrator credentials;
- TrueNAS root/admin credentials in repository files;
- secrets embedded in test fixtures or prompts.

Codex should use workspace-scoped permissions and normal approval prompts. `danger-full-access`, Administrator execution and unrestricted access to personal/NAS data are not defaults.

Mobile Remote can supervise Codex but does not change this boundary: commands still execute with the host's configured permissions.

## Wallet separation

- Master wallet remains outside both development and trading servers.
- Prefer hardware-backed custody for meaningful capital.
- Hyperliquid agent/API wallets are authorized for automated trading.
- Use separate agent wallets per trading process/account scope where practical.
- Never expose the master seed/private key to Windows, WSL2, TrueNAS, Docker, logs, browser code or GitHub.

## Centralized-exchange credentials

Bitvavo/Kraken credentials use minimum permissions:

- view/read where sufficient;
- trade only when that venue is individually promoted;
- withdrawals/transfers always disabled for bot credentials;
- separate credential per environment/service where practical;
- IP restrictions when supported and operationally appropriate.

## Secret handling

Secrets must not appear in:

- repository files;
- committed `.env` files;
- Docker images or build arguments that persist in layers;
- application logs;
- Grafana dashboards;
- browser local storage;
- frontend bundles;
- Telegram or Codex prompts;
- command-line arguments visible through process inspection.

Use protected runtime secret injection. Rotate credentials after suspected exposure and keep a credential inventory with owner, scope and expiry/rotation status.

## GitHub and CI supply-chain security

GitHub Actions should include:

- minimal workflow permissions;
- branch/PR review before merge;
- secret scanning;
- dependency scanning/review where available;
- Python lint/type/test checks;
- TypeScript lint/type/build checks;
- container image build and vulnerability checks;
- third-party actions pinned to reviewed commit SHAs where practical;
- protected release/deployment environments;
- no direct live deployment from an unreviewed pull request.

Release images are published to private GHCR. TrueNAS receives a dedicated read-only package credential. Production-like deployments pin an image digest and record the source commit.

No successful CI run automatically promotes a strategy into live capital.

## Image security

Release images should:

- use minimal pinned base images;
- run as non-root where compatible;
- contain no Git credentials or secrets;
- expose only required ports;
- provide health/readiness endpoints;
- include source commit and build metadata;
- be scanned before promotion;
- avoid mutable `latest` deployment tags;
- support rollback to a known-good digest.

Do not build production images directly on TrueNAS. Do not hot-edit running containers.

## Browser boundary

The Next.js UI contains no exchange signing capability. It sends authenticated operator requests to the FastAPI control gateway. Signing occurs only inside the isolated execution service.

## Network design

Prefer:

- private/local bindings where external exposure is unnecessary;
- Tailscale or equivalent private access for remote administration;
- firewall restrictions between services;
- TLS for browser/API traffic;
- least-privilege database users;
- separate paper/shadow/live networks or namespaces;
- explicit outbound access requirements.

Grafana, Codex, research workers and the frontend do not receive exchange private keys.

## MCP capability model

The project favors **maximum useful capability with scoped standing privilege**, not blanket read-only access.

### TrueNAS MCP

Hermes may inspect and operate project resources, including creating/updating project datasets and Custom Apps when authorized.

Always require explicit approval for high-blast-radius actions such as:

- deleting datasets or snapshots;
- pool/topology operations;
- system updates;
- reboot/shutdown;
- destructive ACL/share changes;
- modifying unrelated applications.

Credentials must not appear in process arguments or plain repository configuration. Use certificate verification and a project-scoped service identity where supported.

### Grafana MCP

Hermes may create/edit dashboards, folders, panels, annotations and alerts in the Hyperliquid project scope. It does not require global Grafana administration.

The Grafana service-account role and the ClickHouse datasource credential are separate:

- Grafana MCP: editor capability scoped to the Hyperliquid folder/resources;
- ClickHouse datasource: read-only analytical credentials;
- org/user/plugin/datasource administration: separately gated.

### ClickHouse MCP and migrations

Research queries use read-only credentials. Schema creation/migrations use a separate controlled identity and reviewed migration files. Research agents do not receive standing DROP/TRUNCATE/database-admin rights.

### Trading tools

Paper tools may be exposed to Hermes. Live exchange order tools are not directly exposed to an unconstrained LLM; they remain behind deterministic risk/execution services. Withdrawal/transfer tools are never exposed.

## Mode safety

`PAPER` is the default. Entering `LIVE` requires:

- explicit server-side configuration;
- deployment/environment approval;
- approved strategy/version;
- approved image digest;
- approved runtime credentials;
- risk/reconciliation readiness.

A frontend toggle, LLM prompt or branch name alone must never enable real-money execution.

Local DEV fails closed to PAPER unless a distinct authorized integration environment is deliberately configured.

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
- restart recovery;
- deployment rollback;
- stable runtime operating system or explicit risk acceptance.

## Auditability

Persist:

- operator actions;
- strategy promotions;
- configuration changes;
- model/version changes;
- risk-limit changes;
- order intents and risk decisions;
- execution state transitions;
- MCP operational actions affecting the project;
- Git commit, image tag and image digest;
- deployment and rollback history.

Each live trade must be attributable to a specific code commit, image digest, strategy version and configuration state.
