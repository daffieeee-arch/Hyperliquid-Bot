"""Focused CI regression contracts for previously found PAPER incidents.

Each test documents how it fails if the named bug is reintroduced. No live
exchange sockets, no TerraPC collectors, no LIVE credentials.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hyperliquid_bot.binance_public_research import (
    BINANCE_USDM_MARKET_WEBSOCKET_URL,
    BINANCE_USDM_PUBLIC_WEBSOCKET_URL,
)
from hyperliquid_bot.binance_usdm_stream_contract import (
    UsdmStreamContractError,
    require_usdm_combined_stream_split,
)
from hyperliquid_bot.ci_scope import classify_paths
from hyperliquid_bot.control_service.app import (
    NOT_READY_DETAIL,
    create_control_service,
)
from hyperliquid_bot.operator_stop_isolation import (
    PROTECTED_LIVE_SESSIONS,
    OperatorStopIsolationError,
    require_sources_do_not_hardcode_live_stop_targets,
    require_stop_script_does_not_hardcode_live_targets,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STOP_SCRIPTS = tuple(sorted((REPO_ROOT / "scripts").glob("data1*_stop.sh")))

PAPER_HEALTH = {"status": "ok", "mode": "PAPER", "stance": "PAPER-only"}
PAPER_READY = {"status": "ready", "mode": "PAPER", "stance": "PAPER-only"}


def test_control_service_health_and_ready_smoke_on_paper() -> None:
    client = TestClient(create_control_service(environment={"TRADING_MODE": "PAPER"}))

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == PAPER_HEALTH
    assert ready.status_code == 200
    assert ready.json() == PAPER_READY


def test_control_service_ready_fails_closed_when_mode_flips_to_live() -> None:
    """Reintroducing LIVE readiness would make this 200 instead of 503."""

    environment = {"TRADING_MODE": "PAPER"}
    client = TestClient(create_control_service(environment=environment))
    environment["TRADING_MODE"] = "LIVE"

    ready = client.get("/ready")

    assert ready.status_code == 503
    assert ready.json() == {"detail": NOT_READY_DETAIL}
    assert "LIVE" not in ready.text


def test_committed_usdm_urls_follow_official_public_vs_market_split() -> None:
    require_usdm_combined_stream_split(
        public_url=BINANCE_USDM_PUBLIC_WEBSOCKET_URL,
        market_url=BINANCE_USDM_MARKET_WEBSOCKET_URL,
    )
    assert "btcusdt@bookTicker" in BINANCE_USDM_PUBLIC_WEBSOCKET_URL
    assert "/public/stream?" in BINANCE_USDM_PUBLIC_WEBSOCKET_URL
    assert "bookTicker" not in BINANCE_USDM_MARKET_WEBSOCKET_URL


@pytest.mark.parametrize(
    ("public_url", "market_url", "match"),
    [
        pytest.param(
            "wss://fstream.binance.com/market/stream?streams=btcusdt@bookTicker",
            "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade",
            r"/public",
            id="bookticker-on-market-as-public-url",
        ),
        pytest.param(
            "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker",
            "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker",
            r"bookTicker",
            id="bookticker-mixed-into-market-combined-stream",
        ),
        pytest.param(
            "wss://fstream.binance.com/public/stream?streams=btcusdt@aggTrade",
            "wss://fstream.binance.com/market/stream?streams=btcusdt@markPrice@1s",
            r"high-frequency|regular-market|mix",
            id="aggtrade-on-public",
        ),
    ],
)
def test_mutated_usdm_bookticker_on_market_fails_closed(
    public_url: str,
    market_url: str,
    match: str,
) -> None:
    """#52 fixture: bookTicker on /market (or mixed categories) must raise."""

    with pytest.raises(UsdmStreamContractError, match=match):
        require_usdm_combined_stream_split(public_url=public_url, market_url=market_url)


def test_committed_stop_scripts_do_not_hardcode_live_send_keys() -> None:
    assert STOP_SCRIPTS
    require_sources_do_not_hardcode_live_stop_targets(
        (str(path.relative_to(REPO_ROOT)), path.read_text(encoding="utf-8"))
        for path in STOP_SCRIPTS
    )


def test_mutated_stop_script_hardcoding_hl_capture_fails_closed() -> None:
    """OPERATOR_STOP fixture: a helper that C-c's hl-capture by name must fail."""

    # Built at runtime so the source scanner does not contain a live send-keys line.
    mutated = "#!/usr/bin/env bash\n" + "tmux send-keys -t " + "hl-capture" + " C-c\n"
    with pytest.raises(OperatorStopIsolationError, match="hl-capture"):
        require_stop_script_does_not_hardcode_live_targets(mutated)
    for session in PROTECTED_LIVE_SESSIONS:
        with pytest.raises(OperatorStopIsolationError, match=session):
            require_stop_script_does_not_hardcode_live_targets(
                "tmux send-keys -t " + session + " C-c\n"
            )


def test_docs_only_paths_skip_heavy_jobs_but_code_paths_do_not() -> None:
    assert classify_paths(["docs/ROADMAP.md", "README.md"]) == (False, False)
    assert classify_paths([".github/workflows/ci.yml"]) == (True, True)
    assert classify_paths(["src/hyperliquid_bot/local_mode.py"]) == (True, True)
    assert classify_paths(["apps/cockpit/src/app/page.tsx"]) == (True, True)
    assert classify_paths(["tests/python/test_control_service.py"]) == (True, True)
    assert classify_paths(["uv.lock"]) == (True, True)
    assert classify_paths(["pnpm-lock.yaml"]) == (True, True)
    assert classify_paths(["pyproject.toml"]) == (True, True)
    assert classify_paths(["package.json"]) == (True, True)
    assert classify_paths(["docs/ROADMAP.md", "src/hyperliquid_bot/local_mode.py"]) == (
        True,
        True,
    )
