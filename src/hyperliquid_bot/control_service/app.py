"""PAPER-only FastAPI control-service health and readiness baseline.

Liveness (`GET /health`) answers whether this process can serve HTTP.
Readiness (`GET /ready`) answers whether required configuration is safe to
accept traffic. Kubernetes HTTP probes treat 200-399 as success and any other
status as failure:
https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/

This process is constructed only after the existing fail-closed PAPER policy
succeeds. `/ready` re-evaluates `TRADING_MODE` on every call so a later unsafe
value fails closed without implying LIVE, SHADOW, or TESTNET support.
"""

import os
from collections.abc import Mapping
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from hyperliquid_bot.local_mode import UnsafeTradingModeError, require_local_paper_mode
from hyperliquid_bot.startup import TRADING_MODE_ENV_VAR, start_local_application

NOT_READY_DETAIL = "Control service is not ready: trading mode is not PAPER."


class HealthResponse(BaseModel):
    """Liveness body. No dependency or environment re-check."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    mode: Literal["PAPER"]
    stance: Literal["PAPER-only"]


class ReadyResponse(BaseModel):
    """Readiness body after fail-closed PAPER selection."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"]
    mode: Literal["PAPER"]
    stance: Literal["PAPER-only"]


def _identity_paper_mode(mode: Literal["PAPER"]) -> Literal["PAPER"]:
    return mode


def _build_fastapi_app(*, mode: Literal["PAPER"], environment: Mapping[str, str]) -> FastAPI:
    application = FastAPI(
        title="Hyperliquid Bot control service",
        summary="PAPER-only health and readiness baseline.",
        description=(
            "PAPER-only control-service baseline. GET /health is liveness. "
            "GET /ready is readiness and fail-closes unless TRADING_MODE is unset "
            "or exactly PAPER. This service does not sign orders, call venues, or "
            "talk to collectors, ClickHouse, or Grafana."
        ),
        version="0.0.0",
    )

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", mode=mode, stance="PAPER-only")

    @application.get("/ready", response_model=ReadyResponse)
    async def ready() -> ReadyResponse:
        try:
            resolved = require_local_paper_mode(environment.get(TRADING_MODE_ENV_VAR))
        except UnsafeTradingModeError:
            raise HTTPException(status_code=503, detail=NOT_READY_DETAIL) from None
        return ReadyResponse(status="ready", mode=resolved, stance="PAPER-only")

    return application


def create_control_service(*, environment: Mapping[str, str] | None = None) -> FastAPI:
    """Build the PAPER-only app after fail-closed local mode selection."""

    source: Mapping[str, str] = os.environ if environment is None else environment
    return start_local_application(
        _identity_paper_mode,
        lambda mode: _build_fastapi_app(mode=mode, environment=source),
        environment=environment,
    )


app = create_control_service()
