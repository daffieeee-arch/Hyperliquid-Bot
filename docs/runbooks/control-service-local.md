# Runbook — PAPER-only control-service baseline

Status: local FastAPI health and readiness process only. PAPER only. Not
LIVE, SHADOW, TESTNET, signing, collectors, ClickHouse, Grafana, or cockpit
wiring.

Architecture names this service the FastAPI control gateway (`apps/api`).
The implementation lives in `src/hyperliquid_bot/control_service` so it shares
the existing Python package, `TRADING_MODE` fail-closed policy, and CI.

Cockpit stays on its Next.js API routes for capture / DESK / MARKETS. Do not
point the browser at this service yet.

## Endpoints

| Method | Path | Role | Success | Failure |
|---|---|---|---|---|
| `GET` | `/health` | Liveness: process can serve HTTP | `200` `{"status":"ok","mode":"PAPER","stance":"PAPER-only"}` | process down |
| `GET` | `/ready` | Readiness: fail-closed PAPER config | `200` `{"status":"ready","mode":"PAPER","stance":"PAPER-only"}` | `503` if `TRADING_MODE` is set and not exactly `PAPER` |

`TRADING_MODE` unset still selects `PAPER`, matching
`require_local_paper_mode`. Unsafe values (`LIVE`, `SHADOW`, `TESTNET`,
case variants, whitespace) fail closed. The factory refuses to construct the
app in that case; `/ready` also re-checks the environment after start.

Kubernetes HTTP probes treat 200-399 as success and any other status as
failure. `/health` does not re-check environment so a later unsafe mode can
take the process out of rotation via `/ready` without claiming the process is
dead.

This baseline has no venue, ledger, database, or collector dependency.

## Run locally (WSL2 / Ubuntu)

From the repository root, with the pinned `uv` toolchain:

```bash
export TRADING_MODE=PAPER
uv sync --frozen --all-groups
uv run uvicorn hyperliquid_bot.control_service.app:app --host 127.0.0.1 --port 8000
```

This repo pins `uvicorn` and records the official FastAPI entrypoint
`hyperliquid_bot.control_service.app:app` in `pyproject.toml`.

Check the probes:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

Interactive docs (local only): `http://127.0.0.1:8000/docs`.

Fail-closed construction:

```bash
TRADING_MODE=LIVE uv run uvicorn hyperliquid_bot.control_service.app:app
```

That must exit before serving. Do not use this as a way to enable LIVE.

## Tests

```bash
export TRADING_MODE=PAPER
uv run --frozen pytest -q tests/python/test_control_service.py
```

CI `python-foundation` already runs the full pytest suite after ruff and mypy.

## Out of scope

- Cockpit proxy or Next.js rewrite
- SHADOW / TESTNET / LIVE capital or signing
- ClickHouse, Redis, PostgreSQL, Grafana
- Collectors, capture scripts, tmux sessions, retained-artifact runs
- Image build or runtime deploy
