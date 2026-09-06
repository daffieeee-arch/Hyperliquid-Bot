"""Tests for the PAPER-only FastAPI control-service baseline."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hyperliquid_bot.control_service.app import (
    NOT_READY_DETAIL,
    app,
    create_control_service,
)
from hyperliquid_bot.local_mode import UnsafeTradingModeError

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "control-service-local.md"

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


def _documented_import_env(*, trading_mode: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("TRADING_MODE", None)
    env["PYTHONPATH"] = "src"
    if trading_mode is not None:
        env["TRADING_MODE"] = trading_mode
    return env


def test_runbook_documents_pythonpath_and_paper_only_scope() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "PAPER only" in text
    assert "PYTHONPATH=src" in text
    assert "uv run uvicorn hyperliquid_bot.control_service.app:app" in text
    assert "LIVE, SHADOW, TESTNET" in text


def test_documented_pythonpath_import_constructs_paper_app() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from hyperliquid_bot.control_service.app import app"],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=_documented_import_env(trading_mode="PAPER"),
    )

    assert result.returncode == 0, result.stderr


def test_documented_pythonpath_import_fails_closed_for_live() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from hyperliquid_bot.control_service.app import app"],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=_documented_import_env(trading_mode="LIVE"),
    )

    assert result.returncode != 0
    assert "UnsafeTradingModeError" in result.stderr
    assert "LIVE" not in result.stderr
