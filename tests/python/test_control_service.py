"""Tests for the PAPER-only FastAPI control-service baseline."""

import pytest
from fastapi.testclient import TestClient

from hyperliquid_bot.control_service.app import (
    NOT_READY_DETAIL,
    app,
    create_control_service,
)
from hyperliquid_bot.local_mode import UnsafeTradingModeError

PAPER_HEALTH = {"status": "ok", "mode": "PAPER", "stance": "PAPER-only"}
PAPER_READY = {"status": "ready", "mode": "PAPER", "stance": "PAPER-only"}


def test_module_level_app_serves_liveness() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == PAPER_HEALTH


def test_health_and_ready_succeed_when_trading_mode_is_unset() -> None:
    client = TestClient(create_control_service(environment={}))

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == PAPER_HEALTH
    assert ready.status_code == 200
    assert ready.json() == PAPER_READY


def test_health_and_ready_succeed_when_trading_mode_is_exact_paper() -> None:
    client = TestClient(create_control_service(environment={"TRADING_MODE": "PAPER"}))

    assert client.get("/health").json() == PAPER_HEALTH
    assert client.get("/ready").json() == PAPER_READY


@pytest.mark.parametrize(
    "unsafe_mode",
    [
        pytest.param("", id="empty"),
        pytest.param("paper", id="lowercase"),
        pytest.param(" PAPER ", id="surrounding-whitespace"),
        pytest.param("SHADOW", id="shadow"),
        pytest.param("TESTNET", id="testnet"),
        pytest.param("LIVE", id="live"),
        pytest.param("UNKNOWN", id="unknown"),
    ],
)
def test_factory_fails_closed_before_app_construction(unsafe_mode: str) -> None:
    with pytest.raises(UnsafeTradingModeError):
        create_control_service(environment={"TRADING_MODE": unsafe_mode})


def test_ready_fails_closed_when_mode_becomes_unsafe_after_start() -> None:
    environment = {"TRADING_MODE": "PAPER"}
    client = TestClient(create_control_service(environment=environment))
    environment["TRADING_MODE"] = "LIVE"

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == PAPER_HEALTH
    assert ready.status_code == 503
    assert ready.json() == {"detail": NOT_READY_DETAIL}
    assert "LIVE" not in ready.text


def test_ready_error_does_not_include_rejected_raw_mode() -> None:
    environment = {"TRADING_MODE": "PAPER"}
    client = TestClient(create_control_service(environment=environment))
    rejected_mode = "DO_NOT_INCLUDE_REJECTED_MODE"
    environment["TRADING_MODE"] = rejected_mode

    response = client.get("/ready")

    assert response.status_code == 503
    assert rejected_mode not in response.text
