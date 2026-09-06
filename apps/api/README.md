# Control service (`apps/api`)

Planned FastAPI control-gateway home from [Architecture](../../docs/ARCHITECTURE.md).

This baseline is PAPER-only health and readiness. Implementation lives in
`src/hyperliquid_bot/control_service` so it uses the existing Python package,
fail-closed `TRADING_MODE` policy, and CI.

- `GET /health` — liveness
- `GET /ready` — readiness; fail-closed unless `TRADING_MODE` is unset or exactly `PAPER`

No LIVE, SHADOW, TESTNET, signing, collectors, ClickHouse, or Grafana.
Operator Cockpit is not wired here; it keeps its Next.js API routes.

Local run (WSL2 / Ubuntu), from the repository root:

```bash
export TRADING_MODE=PAPER
export PYTHONPATH=src
uv run uvicorn hyperliquid_bot.control_service.app:app --host 127.0.0.1 --port 8000
```

See [control-service-local.md](../../docs/runbooks/control-service-local.md).
